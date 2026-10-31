"""Real TCP evidence: a trickling body cannot keep the HTTP owner alive forever."""

import multiprocessing
import socketserver
import time
from threading import Event, Thread

import pytest

from qtrade_data_core.ports import DataFailure
from qtrade_data_mairui.bounded_http import BoundedHttpClient, TransportFailure


@pytest.fixture
def source(monkeypatch):
    # Loopback evidence must not depend on a host proxy synthesizing responses.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    accepted, closed = Event(), Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            request = self.request.recv(8192)
            accepted.set()
            try:
                if b"/preheaders " in request:
                    return
                if b"/truncated " in request or b"/headers " in request:
                    self.request.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n"
                    )
                    if b"/truncated " in request:
                        self.request.sendall(b'[{"jzrq":')
                    return
                if b"/slow " in request:
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")
                    for _ in range(600):
                        self.request.sendall(b"x" * 4096)
                        time.sleep(0.05)
                else:
                    if b"/delayed " in request:
                        time.sleep(0.05)
                    content = b"x" * 100000 if b"/large " in request else b"[]"
                    self.request.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: "
                        + str(len(content)).encode()
                        + b"\r\nConnection: close\r\n\r\n"
                        + content
                    )
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                closed.set()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}", accepted, closed
        finally:
            server.shutdown()
            thread.join()


def test_successful_tcp_request_reaps_owner(source):
    url, accepted, closed = source
    before = {p.pid for p in multiprocessing.active_children()}
    response = BoundedHttpClient().get(url + "/fast", timeout=5)
    assert response.status_code == 200 and response.content == b"[]"
    assert accepted.is_set() and closed.wait(1)
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_child_transport_timing_measures_delayed_response_without_spawn(source):
    url, _, closed = source
    started = time.monotonic()
    response = BoundedHttpClient().get(url + "/delayed", timeout=5)
    wall_ms = (time.monotonic() - started) * 1000
    measured = response.extensions["qtrade_transport_ms"]
    assert type(measured) is int and 40 <= measured < wall_ms
    assert response.content == b"[]" and closed.wait(1)


def test_trickling_tcp_response_total_deadline_closes_connection(source):
    url, accepted, closed = source
    before = {p.pid for p in multiprocessing.active_children()}
    started = time.monotonic()
    with pytest.raises(TransportFailure, match="timeout") as error:
        BoundedHttpClient().get(url + "/slow", timeout=2)
    assert error.value.response_status == 200
    assert error.value.response_body and set(error.value.response_body) == {ord("x")}
    assert error.value.response_bytes == len(error.value.response_body)
    assert error.value.args == ("timeout",) and "xxxx" not in repr(error.value)
    assert accepted.is_set()  # This exercised a socket, not startup timeout.
    assert time.monotonic() - started < 4
    assert closed.wait(2), "supplier still sees an active socket after timeout"
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_size_limit_closes_connection_without_returning_oversize_body(source):
    url, accepted, closed = source
    with pytest.raises(TransportFailure, match="unavailable") as error:
        BoundedHttpClient(maximum=65536).get(url + "/large", timeout=5)
    assert error.value.response_bytes <= 65536
    assert error.value.response_body == b"x" * 65536
    assert error.value.response_status == 200
    assert accepted.is_set() and closed.wait(1)


@pytest.mark.parametrize(
    "route,body,status",
    [("truncated", b'[{"jzrq":', 200), ("headers", b"", 200), ("preheaders", None, None)],
)
def test_disconnect_preserves_only_observed_response_evidence(source, route, body, status):
    url, accepted, closed = source
    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(TransportFailure, match="unavailable") as error:
        BoundedHttpClient().get(url + "/" + route, timeout=5)
    assert error.value.response_status == status and error.value.response_body == body
    assert error.value.response_bytes == len(body or b"")
    assert accepted.is_set() and closed.wait(1)
    assert {p.pid for p in multiprocessing.active_children()} == before


def _blocking_request(url):
    BoundedHttpClient().get(url + "/slow", timeout=20)


def test_worker_death_terminates_socket_owner(source, tmp_path, monkeypatch):
    url, accepted, closed = source
    # Abrupt parent death cannot run TemporaryDirectory cleanup; confine any
    # private response residue to this isolated test directory.
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    worker = multiprocessing.get_context("spawn").Process(target=_blocking_request, args=(url,))
    try:
        worker.start()
        assert accepted.wait(5), "HTTP owner never reached the test source"
        worker.kill()
        worker.join(5)
        assert not worker.is_alive()
        assert closed.wait(5), "HTTP socket owner survived its Worker"
    finally:
        if worker.is_alive():
            worker.kill()
        worker.join()
        worker.close()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), 301])
def test_invalid_deadline_rejected_before_outbound(timeout):
    with pytest.raises(DataFailure, match="timeout"):
        BoundedHttpClient().get("http://127.0.0.1:1/fixture", timeout=timeout)
