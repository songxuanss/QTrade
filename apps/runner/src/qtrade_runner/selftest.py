"""Exercise the live Unix-socket service and container security boundary."""

import asyncio
import time
from typing import cast

import httpx

SOCKET = "/run/qtrade/runner.sock"


async def _post(client: httpx.AsyncClient, code: str) -> dict[str, object]:
    response = await client.post("/execute", json={"code": code})
    response.raise_for_status()
    return cast(dict[str, object], response.json())


async def main() -> None:
    transport = httpx.AsyncHTTPTransport(uds=SOCKET)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://runner",
        timeout=20,
    ) as client:
        assert (await client.get("/health")).status_code == 200
        cases = (
            ("print('ok')\n", "succeeded"),
            ("import time\ntime.sleep(30)\n", "timed_out"),
            ("while True:\n    pass\n", "resource_limit"),
            ("print('x' * 1_000_000)\n", "output_limit"),
            ("payload = bytearray(512 * 1024 * 1024)\n", "failed"),
            (
                "from pathlib import Path\nPath('/etc/qtrade-runner-test').write_text('x')\n",
                "failed",
            ),
        )
        for code, expected in cases:
            print(f"runner test expects {expected}", flush=True)
            result = await _post(client, code)
            assert result["status"] == expected, result
        identity = await _post(
            client,
            "import os\nprint(os.geteuid())\nprint(os.getegid())\n",
        )
        uid, gid = (int(item) for item in str(identity["output"]).splitlines())
        assert uid != 0 and gid != 0, identity
        active = asyncio.create_task(_post(client, "import time\ntime.sleep(10)\n"))
        await asyncio.sleep(0.2)
        started = time.monotonic()
        health = await client.get("/health")
        assert health.status_code == 200 and time.monotonic() - started < 1
        assert (await active)["status"] == "timed_out"
        network = await _post(
            client,
            "import socket\nsocket.create_connection(('1.1.1.1', 443), timeout=1)\n",
        )
        assert network["status"] == "failed", network
        assert "Network is unreachable" in str(network["output"]), network
        detached = await _post(
            client,
            "import os,time\npid=os.fork()\nif pid==0:\n"
            "    os.setsid()\n    os.close(0); os.close(1); os.close(2)\n"
            "    time.sleep(120)\nelse:\n    print(pid)\n",
        )
        assert detached["status"] == "succeeded", detached
        from pathlib import Path

        assert not Path(f"/proc/{int(str(detached['output']).strip())}").exists()
        print("runner detached descendant cleanup and reaping passed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
