"""STAND-IN: the older, hand-kept roster of which regions each driver may run.

In the original system this was parsed out of a private notes file. Here it is
a small generated sample: <DESK_DATA_DIR>/authority.json, shaped
{"BOARD NAME": "OTR - AZ, NV, LOCAL", ...}. The board's own DRIVERS tab
outranks it; it only adds which regions an out-of-town driver runs.
In the authority text: AZ = Arizona, NV = Las Vegas, N or CA = Central Valley.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def authority_table() -> dict[str, str]:
    base = os.environ.get("DESK_DATA_DIR") or Path(__file__).resolve().parent.parent / "demo" / "sample"
    try:
        raw = json.loads((Path(base) / "authority.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for name, auth in raw.items():
        name = name.strip().upper()
        out[name] = auth
        out[name.split(" ")[0]] = auth
    return out
