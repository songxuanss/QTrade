"""Private file configuration plus environment overrides; never printable secrets."""

import os
from pathlib import Path


def configuration() -> dict[str, str]:
    values: dict[str, str] = {}
    path = os.environ.get("COLLECTOR_CONFIG_FILE")
    if path:
        for line in Path(path).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}
