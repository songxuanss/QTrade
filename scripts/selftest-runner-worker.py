"""Confirm the isolated runner can be reached only through the worker socket mount."""

import asyncio

import httpx


async def main() -> None:
    transport = httpx.AsyncHTTPTransport(uds="/run/qtrade/runner.sock")
    async with httpx.AsyncClient(transport=transport, base_url="http://runner") as client:
        health = await client.get("/health")
        assert health.status_code == 200
        response = await client.post("/execute", json={"code": "print('worker socket ok')"})
        response.raise_for_status()
        assert response.json()["status"] == "succeeded"
        assert "worker socket ok" in response.json()["output"]


asyncio.run(main())
