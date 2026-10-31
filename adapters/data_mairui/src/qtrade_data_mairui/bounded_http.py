"""One disposable Linux HTTP process owns the socket for each charged attempt.

HTTPX read timeouts reset on incoming chunks. A trickling response can therefore
outlive a lease. The parent enforces an overall deadline, kills and reaps the
socket owner before returning, and never runs detached transport threads.

The socket owner is forked from the caller on Linux: this keeps the socket-owner
parent equal to the charged caller (so parent-death signaling is exact) and
avoids ``spawn``/``forkserver`` re-importing the caller's ``__main__``, which
would otherwise start a nested test runner under ``python -m pytest``.
"""

import ctypes
import json
import multiprocessing
import os
import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Literal

import httpx

from qtrade_data_core.ports import DataFailure


class TransportFailure(DataFailure):
    """Safe error code with optional private, incomplete decoded response bytes.

    Exception args/repr remain the fixed code. The body is only handed to the
    archive callback; callers must never serialize this exception's attributes.
    None means no response evidence, whereas b'' can mean headers but no body.
    """

    def __init__(
        self,
        code: Literal["timeout", "unavailable"],
        response_bytes: int = 0,
        *,
        response_status: int | None = None,
        response_body: bytes | None = None,
    ):
        self.response_bytes = len(response_body) if response_body is not None else response_bytes
        self.response_status, self.response_body = response_status, response_body
        super().__init__(code)


def _save_result(root: str, result: dict[str, str | int | None]) -> None:
    # Atomic replacement leaves usable header evidence even if the parent kills
    # the socket owner while its final outcome is being written.
    pending = Path(root) / "result.pending"
    pending.write_text(json.dumps(result))
    os.chmod(pending, 0o600)
    pending.replace(Path(root) / "result")


def _failed_response(root: str, code: Literal["timeout", "unavailable"]) -> TransportFailure:
    """Read only after the socket owner is reaped: no racing file/byte claims."""
    status = None
    result_path, body_path = Path(root) / "result", Path(root) / "body"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        value = result.get("status")
        if type(value) is int and 100 <= value <= 599:
            status = value
    body = body_path.read_bytes() if body_path.exists() else None
    if status is not None and body is None:
        body = b""
    return TransportFailure(code, response_status=status, response_body=body)


def _download(
    parent_pid: int, root: str, url: str, params: dict[str, str] | None, maximum: int
) -> None:
    # A Worker shutdown must also terminate its socket owner. Recheck the parent
    # after prctl to close the race where it dies before the signal is installed.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        return
    # Third-party logging must never expose the licence-bearing URL in a child.
    with open(os.devnull, "w") as null:
        os.dup2(null.fileno(), 1)
        os.dup2(null.fileno(), 2)
    body = Path(root) / "body"
    result: dict[str, str | int | None] = {"error": "unavailable", "status": 0}
    transport_started: float | None = None
    try:
        with httpx.Client(timeout=httpx.Timeout(90, connect=5), follow_redirects=False) as client:
            transport_started = monotonic()
            with client.stream("GET", url, params=params) as response:
                result["status"] = response.status_code
                _save_result(root, result)
                # No userspace buffering: a forced deadline must retain every
                # decoded byte already written, including short/trickling chunks.
                with body.open("wb", buffering=0) as output:
                    os.chmod(body, 0o600)
                    size = 0
                    for chunk in response.iter_bytes():
                        retained = chunk[: maximum - size]
                        output.write(retained)
                        size += len(retained)
                        # Keep a bounded prefix as incomplete evidence; never
                        # return a silently truncated successful response.
                        if len(retained) != len(chunk):
                            raise DataFailure("unavailable")
                result["error"] = None
    except httpx.TimeoutException:
        result["error"] = "timeout"
    except Exception:
        # Only fixed error codes cross the process seam; exception reprs/URLs
        # and upstream bodies never enter diagnostics or process arguments.
        pass
    # Child-side timing excludes process spawn and supplier pacing. It measures
    # network wait/body reception plus local file writes, not server compute.
    result["transport_ms"] = (
        round((monotonic() - transport_started) * 1000) if transport_started is not None else None
    )
    _save_result(root, result)


class BoundedHttpClient:
    """Synchronous GET with a total deadline and bounded decoded response.

    Requires Linux parent-death signaling. Credentials are passed through spawn
    IPC, not argv/files. Private temporary files are removed after their decoded
    bytes are returned or attached privately to a failure for archive capture.
    Abrupt host/parent death may leave a private temporary folder; it cannot
    promise a durable response receipt. Each attempt trades connection pooling
    for deterministic socket-owner cleanup.
    """

    def __init__(self, maximum: int = 64_000_000):
        if sys.platform != "linux":
            raise ValueError("bounded supplier transport requires Linux")
        if type(maximum) is not int or not 1 <= maximum <= 64_000_000:
            raise ValueError("invalid decoded response limit")
        self.maximum = maximum

    def get(
        self, url: str, *, params: dict[str, str] | None = None, timeout: float = 90
    ) -> httpx.Response:
        if not 0 < timeout <= 300:
            raise DataFailure("timeout")
        with TemporaryDirectory(prefix="qtrade-http-") as root:
            process = multiprocessing.get_context("fork").Process(
                target=_download, args=(os.getpid(), root, url, params, self.maximum), daemon=True
            )
            try:
                started = monotonic()
                process.start()
                process.join(max(0, timeout - (monotonic() - started)))
                if process.is_alive():
                    process.kill()
                    process.join()
                    raise _failed_response(root, "timeout")
                result_path = Path(root) / "result"
                if process.exitcode != 0 or not result_path.exists():
                    raise _failed_response(root, "unavailable")
                result = json.loads(result_path.read_text())
                body = Path(root) / "body"
                if result["error"]:
                    raise _failed_response(
                        root, "timeout" if result["error"] == "timeout" else "unavailable"
                    )
                return httpx.Response(
                    result["status"],
                    content=body.read_bytes(),
                    extensions={"qtrade_transport_ms": result.get("transport_ms")},
                )
            except (OSError, ValueError):
                raise TransportFailure("unavailable") from None
            finally:
                # Reap before the caller finalizes its concurrency reservation.
                # A timeout never leaves a background socket owner running.
                if process.pid is not None:
                    if process.is_alive():
                        process.kill()
                    process.join()
                    process.close()
