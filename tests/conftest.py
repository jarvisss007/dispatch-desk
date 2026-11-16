"""Put the repo root (board.py, page_design.py, config.py) and the stand-in
modules (standins/) on the import path, so the tests run from a fresh clone
with no network, no keys and no company files."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "standins", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
