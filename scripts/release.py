"""Run release operations with repository module paths; private settings stay in the environment."""

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for group in ("apps", "packages", "adapters"):
    for path in (root / group).iterdir():
        if (path / "src").is_dir():
            sys.path.insert(0, str(path / "src"))

from qtrade_composition.releases import main  # noqa: E402

if __name__ == "__main__":
    main()
