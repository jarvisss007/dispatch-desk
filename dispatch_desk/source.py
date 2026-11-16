"""The desk's own read of the live board: copy aside, then open the copy.

Copying first means an open Excel session can't lock us out and we can never
write to the dispatchers' file by accident. The copy's age is the SOURCE
file's last save, not the copy time — a fresh copy of a frozen board is still
a frozen board (input freshness is not page freshness).

Which file: settings.BOARD_FILE (env DESK_BOARD_FILE) when set, otherwise the
most recently saved "<MONTH> <YEAR>.xlsx" in settings.BOARD_DIRS.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

import openpyxl

from dispatch_desk import settings

ASIDE = settings.STATE_DIR / "_board"
_MONTHLY = re.compile(
    r"^(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|"
    r"NOVEMBER|DECEMBER)\s+\d{4}\.xlsx$", re.I)
STALE_HOURS = 12.0


def live_board_path() -> Path:
    """The board to read. With no explicit file: the newest '<MONTH> <YEAR>.xlsx'.
    Trust the save time, never the month in the name — the office keeps adding
    daily tabs to last month's book for a while."""
    if settings.BOARD_FILE:
        p = Path(settings.BOARD_FILE)
        if not p.exists():
            raise FileNotFoundError(f"Board not found: {p} (DESK_BOARD_FILE)")
        return p
    cands = [p for root in settings.BOARD_DIRS if Path(root).exists()
             for p in Path(root).glob("*.xlsx") if _MONTHLY.match(p.name)]
    if not cands:
        raise FileNotFoundError(
            "No monthly board found in " + ", ".join(str(r) for r in settings.BOARD_DIRS)
            + " — for the demo, run: py demo/make_sample.py")
    return max(cands, key=lambda p: p.stat().st_mtime)


def open_live() -> tuple[openpyxl.Workbook, dict]:
    """(workbook opened read-only from the copy, info about the source)."""
    src = live_board_path()
    saved = datetime.fromtimestamp(src.stat().st_mtime)
    ASIDE.mkdir(parents=True, exist_ok=True)
    dst = ASIDE / src.name
    shutil.copy2(src, dst)
    age_h = (datetime.now() - saved).total_seconds() / 3600
    info = {"name": src.name, "saved": saved, "age_hours": age_h,
            "stale": age_h > STALE_HOURS}
    wb = openpyxl.load_workbook(dst, read_only=True, data_only=True)
    return wb, info


def age_text(info: dict) -> str:
    h = info["age_hours"]
    age = f"{int(h * 60)} min" if h < 1 else (f"{h:.1f} h" if h < 48 else f"{h / 24:.0f} days")
    return f"{info['name']} saved {info['saved']:%a %m/%d %I:%M %p} ({age} old)"
