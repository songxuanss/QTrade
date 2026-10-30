"""Compare copied source bytes without reading private runtime configuration.

Run inside WSL after bootstrap. A source snapshot is not a Git revision, and
matching bytes do not establish build, service, or business-flow readiness.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

EXCLUDED = {
    ".git",
    ".tools",
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "test-results",
}


def fingerprints(root: Path) -> dict[str, str]:
    result = {}
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(name for name in folders if name not in EXCLUDED)
        for name in sorted(files):
            if name.startswith(".env") or name.endswith((".tsbuildinfo", ".pyc", ".pyo")):
                continue
            path = Path(directory) / name
            # Stream rather than loading potentially large checked-in fixtures.
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    source, target = args.source.resolve(), args.target.resolve()
    if source == target or not all(
        (root / "pyproject.toml").is_file() for root in (source, target)
    ):
        raise SystemExit("Two distinct existing project roots are required")
    expected, actual = fingerprints(source), fingerprints(target)
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(key for key in expected.keys() & actual.keys() if expected[key] != actual[key])
    summary = {
        "files": len(expected),
        "source_sha256": hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "missing": len(missing),
        "extra": len(extra),
        "changed": len(changed),
    }
    print(json.dumps(summary, sort_keys=True))
    if missing or extra or changed:
        # Only file names are reported; private configuration is excluded above.
        print(json.dumps({"missing": missing, "extra": extra, "changed": changed}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
