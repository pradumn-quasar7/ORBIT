"""Local secrets (Phase 19): read ``.env`` into the environment at start-up.

Only ``KEY=VALUE`` lines; comments and blanks are ignored; values already in the
environment win. The file is git-ignored and readable only by its owner.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_env(path: Path = ROOT / ".env") -> int:
    loaded = 0
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
