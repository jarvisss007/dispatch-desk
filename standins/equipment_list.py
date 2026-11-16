"""STAND-IN: the equipment list (what each tractor is).

In the original system this joined vehicle records, insurance lists and a
corrections file. Here it reads a generated sample:
<DESK_DATA_DIR>/equipment.json, shaped

    {"pulled": "YYYY-MM-DD",
     "tractors": [{"name", "group", "year", "make", "model", "vin", "plate", "state", "own", "miles"}]}
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def build_rows() -> dict:
    base = os.environ.get("DESK_DATA_DIR") or Path(__file__).resolve().parent.parent / "demo" / "sample"
    return json.loads((Path(base) / "equipment.json").read_text(encoding="utf-8"))
