"""Bounded child-process execution used only inside the isolated runner container."""

import atexit
import ctypes
import json
import os
import resource
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

MAX_CODE_BYTES = 1_000_000
MAX_INPUT_BYTES = 1_000_000
MAX_OUTPUT_BYTES = 64 * 1024
CPU_SECONDS = 2
WALL_SECONDS = 5
MEMORY_BYTES = 256 * 1024 * 1024
FILE_BYTES = 8 * 1024 * 1024
MAX_PROCESSES = 2
_JOB_ROOT = Path(tempfile.mkdtemp(prefix="qtrade-runner-jobs-"))
atexit.register(shutil.rmtree, _JOB_ROOT, ignore_errors=True)
_BOOTSTRAP = (
    "import os,sys; os.read(int(sys.argv[1]),1); "
    "os.geteuid()==0 and (os.setgroups([]), os.setgid(int(sys.argv[3])), "
    "os.setuid(int(sys.argv[2]))); "
    "(os.getuid()!=int(sys.argv[2]) or os.getgid()!=int(sys.argv[3])) and os._exit(126); "
    "os.chdir(sys.argv[5]); "
    "exec(compile(open(sys.argv[4], encoding='utf-8').read(), '<strategy>', 'exec'))"
)


@dataclass(frozen=True, slots=True)
class SandboxResult:
    status: Literal["succeeded", "failed", "timed_out", "output_limit", "resource_limit"]
    exit_code: int | None
    output: str
    elapsed_ms: int


class IsolationFailure(RuntimeError):
    """A job could not be fully contained; the isolated service must terminate."""


class StrategySandbox:
    """Run source with OS limits; container policy supplies network and mount isolation."""

    def __init__(self) -> None:
        if os.geteuid() == 0:
            # Adopt double-forked descendants so they can be reaped rather than leak zombies.
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                raise IsolationFailure("runner descendant supervision is unavailable")
            descriptor = os.pidfd_open(os.getpid())
            os.close(descriptor)

    def execute(self, code: str, input_data: dict[str, object] | None = None) -> SandboxResult:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("strategy source is required")
        code_bytes = code.encode("utf-8")
        if len(code_bytes) > MAX_CODE_BYTES:
            raise ValueError("strategy source exceeds 1 MB")
        payload = json.dumps(input_data or {}, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_INPUT_BYTES:
            raise ValueError("strategy input exceeds 1 MB")

        uid, gid = self._job_identity()
        job = self._create_job(uid, gid, code_bytes)
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        gate_read, gate_write = os.pipe()
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    _BOOTSTRAP,
                    str(gate_read),
                    str(uid),
                    str(gid),
                    str(job / "source" / "strategy.py"),
                    str(job / "work"),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(_JOB_ROOT),
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
                start_new_session=True,
                pass_fds=(gate_read,),
            )
            os.close(gate_read)
            gate_read = -1
            _apply_child_limits(process.pid)
            assert process.stdin is not None and process.stdout is not None
            writer = threading.Thread(
                target=_write_input, args=(process.stdin, payload), daemon=True
            )
            writer.start()
            os.write(gate_write, b"1")
            os.close(gate_write)
            gate_write = -1
            collect_status, output = self._collect(process, started)
            writer.join(timeout=0.25)
            exit_code = process.poll()
            elapsed = _elapsed_ms(started)
            if collect_status == "timed_out":
                return SandboxResult("timed_out", exit_code, "执行超过 5 秒时限", elapsed)
            if collect_status == "output_limit":
                return SandboxResult("output_limit", exit_code, "输出超过 64 KiB 上限", elapsed)
            if collect_status == "cpu_limit" or exit_code in {
                -signal.SIGXCPU,
                -signal.SIGKILL,
            }:
                return SandboxResult("resource_limit", exit_code, output, elapsed)
            return SandboxResult(
                "succeeded" if exit_code == 0 else "failed", exit_code, output, elapsed
            )
        finally:
            if gate_read >= 0:
                os.close(gate_read)
            if gate_write >= 0:
                os.close(gate_write)
            if process is not None and process.poll() is None:
                _kill_process_group(process)
                process.wait(timeout=1)
            if os.geteuid() == 0:
                try:
                    _terminate_job_descendants(uid)
                except Exception:
                    raise IsolationFailure("runner descendant cleanup failed") from None
            shutil.rmtree(job, ignore_errors=True)

    @staticmethod
    def _job_identity() -> tuple[int, int]:
        if os.geteuid() != 0:
            return os.getuid(), os.getgid()
        active = {_process_uid(path) for path in Path("/proc").glob("[0-9]*")}
        for _ in range(100):
            uid = 20_000 + secrets.randbelow(40_000)
            if uid not in active:
                return uid, 20_000 + secrets.randbelow(40_000)
        raise IsolationFailure("runner job identity is unavailable")

    @staticmethod
    def _create_job(uid: int, gid: int, code: bytes) -> Path:
        _JOB_ROOT.mkdir(mode=0o711, parents=True, exist_ok=True)
        # Traversal only; randomly named jobs cannot be listed or written by other users.
        os.chmod(_JOB_ROOT, 0o711)  # nosec B103
        job = Path(tempfile.mkdtemp(prefix="run-", dir=_JOB_ROOT))
        # The child needs traversal; it cannot list or modify this root-owned directory.
        os.chmod(job, 0o711)  # nosec B103
        source = job / "source"
        source.mkdir(mode=0o700)
        os.chmod(source, 0o700)
        (source / "strategy.py").write_bytes(code)
        if os.geteuid() == 0:
            os.chown(source, 0, gid)
        # Only this isolated job's GID may traverse to read its immutable source.
        os.chmod(source, 0o710)  # nosec B103
        if os.geteuid() == 0:
            os.chown(source / "strategy.py", 0, gid)
        os.chmod(source / "strategy.py", 0o440)
        work = job / "work"
        work.mkdir(mode=0o700)
        os.chmod(work, 0o700)
        os.chown(work, uid, gid)
        return job

    @staticmethod
    def _collect(process: subprocess.Popen[bytes], started: float) -> tuple[str, str]:
        assert process.stdout is not None
        stream = process.stdout
        os.set_blocking(stream.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(stream, selectors.EVENT_READ)
        output = bytearray()
        deadline = started + WALL_SECONDS
        try:
            while selector.get_map() or process.poll() is None:
                if time.monotonic() >= deadline:
                    _kill_process_group(process)
                    process.wait(timeout=1)
                    return "timed_out", ""
                events = selector.select(min(0.05, max(0, deadline - time.monotonic())))
                for key, _ in events:
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                        _kill_process_group(process)
                        process.wait(timeout=1)
                        return "output_limit", ""
                    output.extend(chunk)
            process.wait(timeout=1)
            if process.returncode == -signal.SIGXCPU:
                return "cpu_limit", ""
            return "completed", output.decode("utf-8", errors="replace")
        finally:
            selector.close()


def _apply_child_limits(pid: int) -> None:
    limits = (
        (resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 1)),
        (resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES)),
        (resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES)),
        (resource.RLIMIT_NOFILE, (64, 64)),
        (resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES)),
        (resource.RLIMIT_CORE, (0, 0)),
    )
    for limit, value in limits:
        resource.prlimit(pid, limit, value)


def _write_input(stream: BinaryIO, payload: bytes) -> None:
    try:
        stream.write(payload)
        stream.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        stream.close()


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _process_uid(path: Path) -> int | None:
    try:
        status = path.joinpath("status").read_text()
        return int(next(line for line in status.splitlines() if line.startswith("Uid:")).split()[1])
    except (FileNotFoundError, ProcessLookupError):
        return None


def _terminate_job_descendants(uid: int) -> None:
    if not 20_000 <= uid < 60_000:
        raise IsolationFailure("invalid isolated job identity")
    deadline = time.monotonic() + 1
    while True:
        remaining = [path for path in Path("/proc").glob("[0-9]*") if _process_uid(path) == uid]
        if not remaining:
            return
        if time.monotonic() >= deadline:
            raise IsolationFailure("runner job descendants did not terminate")
        for path in remaining:
            descriptor = None
            pid = int(path.name)
            try:
                descriptor = os.pidfd_open(pid)
                # Recheck after opening: pidfds prevent signalling an unrelated reused PID.
                if _process_uid(path) == uid:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass  # Its parent may still be dying; subreaper adoption follows.
        time.sleep(0.01)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
