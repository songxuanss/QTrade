"""Validate one supplied licence over stdin; save privately only on success.

Never accepts the licence on argv, prints it, or serializes transport exceptions.
One ordinary metadata call proves only basic access, not Pro/history readiness.
"""

import json
import os
import re
import runpy
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def report(value: dict) -> None:
    # Only safe status/count fields are recorded, never body bytes or URLs.
    writer = runpy.run_path(str(ROOT / "scripts/prepare-wsl-collector.py"))["private_write"]
    writer(ROOT / ".tools/windows-environment/provider-probe-result.json", json.dumps(value))
    print(json.dumps(value))


def main() -> int:
    if os.environ.get("WSL_DISTRO_NAME") != "QTrade-Ubuntu-26":
        raise SystemExit("Use the dedicated Ubuntu 26 environment")
    body = sys.stdin.buffer.read(1025)
    if len(body) > 1024:
        raise SystemExit("Private input exceeds the supported limit")
    request = json.loads(body)
    licence = request.get("licence")
    if not isinstance(licence, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{16,256}", licence):
        raise SystemExit("Unsupported private input format")
    sys.path[:0] = [
        str(path / "src")
        for group in ("apps", "packages", "adapters")
        for path in (ROOT / group).iterdir()
        if (path / "src").is_dir()
    ]
    from qtrade_data_core.ports import DataFailure
    from qtrade_data_mairui.bounded_http import BoundedHttpClient, TransportFailure
    from qtrade_data_mairui.collection import MaiRuiCollection

    transport = {"http_status": None, "received_bytes": 0, "business_code": None}

    class MetadataClient(BoundedHttpClient):
        def get(self, url, *, params=None, timeout=90):
            try:
                response = super().get(url, params=params, timeout=timeout)
            except TransportFailure as error:
                transport.update(
                    http_status=error.response_status, received_bytes=error.response_bytes
                )
                raise
            transport.update(http_status=response.status_code, received_bytes=len(response.content))
            try:
                payload = json.loads(response.content)
                code = payload.get("code") if isinstance(payload, dict) else None
                if type(code) is int:
                    transport["business_code"] = code
            except (ValueError, RecursionError):
                pass
            return response

    provider = MaiRuiCollection(
        licence,
        cache=ROOT / ".tools/windows-environment/provider-probe",
        request_timeout_seconds=90,
        client=MetadataClient(),
    )
    try:
        names = provider.names(("601288.SH",))
        if names.get("601288.SH") in {None, "", "名称未核验"}:
            raise DataFailure("unverified")
    except DataFailure as error:
        report(
            {
                "basic_metadata_access": "not_confirmed",
                "error_code": error.code,
                "credential_saved": False,
                "maximum_requests": 1,
                **transport,
            }
        )
        return 2
    writer = runpy.run_path(str(ROOT / "scripts/prepare-wsl-collector.py"))["save_env"]
    for filename in (".env", ".env.collector"):
        path = ROOT / filename
        values = {key: value for key, value in dotenv_values(path).items() if value is not None}
        values["MAIRUI_LICENCE"] = licence
        writer(path, values)
    report(
        {
            "basic_metadata_access": "confirmed",
            "credential_saved": True,
            "maximum_requests": 1,
            "pro_history_access": "not_tested",
            **transport,
        }
    )
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        # A supplier URL may contain the licence. Never expose an exception repr
        # if an unexpected configuration/transport failure crosses this boundary.
        report({"basic_metadata_access": "not_confirmed", "error_code": "local_setup"})
        result = 1
    raise SystemExit(result)
