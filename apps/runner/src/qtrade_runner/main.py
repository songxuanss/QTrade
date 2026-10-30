"""Unix-socket service for resource-constrained strategy executions."""

import asyncio
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from qtrade_runner.sandbox import IsolationFailure, StrategySandbox
from qtrade_runtime.configuration import validate_configuration
from qtrade_runtime.observability import configure_logging, instrument_app

validate_configuration(os.environ, isolated=True)
SOCKET = Path("/run/qtrade/runner.sock")
SOCKET_GROUP = 10001
sandbox = StrategySandbox()
slots = asyncio.Semaphore(1)


class ExecuteInput(BaseModel):
    code: str = Field(min_length=1, max_length=1_000_000)
    input: dict[str, object] = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    yield
    try:
        SOCKET.unlink()
    except FileNotFoundError:
        pass


app = FastAPI(title="QTrade Strategy Runner", lifespan=lifespan)
telemetry = instrument_app(app, "qtrade-runner", accept_trace_parent=True)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "qtrade-runner"}


@app.get("/ready")
def ready() -> JSONResponse:
    # The isolated runner intentionally has no database, network or broker dependencies.
    available = SOCKET.is_socket() and os.access(SOCKET.parent, os.W_OK)
    telemetry.dependencies.labels("execution_socket").set(int(available))
    return JSONResponse(
        {"status": "ready" if available else "unavailable", "service": "qtrade-runner"},
        status_code=200 if available else 503,
    )


@app.post("/execute")
async def execute(body: ExecuteInput) -> dict[str, object]:
    async with slots:
        try:
            with telemetry.operation("strategy.execute"):
                result = await asyncio.to_thread(sandbox.execute, body.code, body.input)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except IsolationFailure:
            logging.getLogger(__name__).critical("runner isolation failed; terminating container")
            # PID 1 exit destroys this PID namespace, including any uncontained descendants.
            os._exit(70)
        return {
            "status": result.status,
            "exit_code": result.exit_code,
            "output": result.output,
            "elapsed_ms": result.elapsed_ms,
        }


def run() -> None:
    os.umask(0o117)
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    try:
        SOCKET.unlink()
    except FileNotFoundError:
        pass
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(SOCKET))
    os.chown(SOCKET, 0, SOCKET_GROUP)
    # The worker GID needs socket access; API has no socket volume and job GIDs differ.
    os.chmod(SOCKET, 0o660)  # nosec B103
    listener.listen(2048)
    listener.setblocking(False)
    config = uvicorn.Config(app, log_level="warning")
    configure_logging("qtrade-runner")
    uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    run()
