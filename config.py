"""The little that board.py needs to know about where the board lives.

Nothing secret is read here: no keys, no tokens. The Dispatch Desk itself
finds the board through dispatch_desk/source.py and dispatch_desk/settings.py;
this is only for board.py's own open_board() / find_load() helpers.
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
BOARD_DIR = Path(os.environ.get("DESK_DATA_DIR") or _HERE / "demo" / "sample")
_MONTHLY = re.compile(
    r"^(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|"
    r"NOVEMBER|DECEMBER)\s+\d{4}\.xlsx$", re.I)

# Extra STATUS phrases that mean "staged at a yard" (for example a partner's yard).
PARTNER_YARD_WORDS = ("AT ANNEX", "ANNEX YARD")


def board_filename_for(d: date) -> str:
    return f"{d:%B} {d.year}.xlsx".upper().replace(".XLSX", ".xlsx")


def board_path(d: date | None = None) -> Path:
    """DESK_BOARD_FILE if set; else this month's book if it exists; else the
    most recently saved monthly book (tabs keep being added to last month's
    file for a while after the month turns)."""
    explicit = os.environ.get("DESK_BOARD_FILE")
    if explicit:
        return Path(explicit)
    d = d or date.today()
    guess = BOARD_DIR / board_filename_for(d)
    if guess.exists():
        return guess
    cands = [p for p in BOARD_DIR.glob("*.xlsx") if _MONTHLY.match(p.name)] if BOARD_DIR.exists() else []
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else guess
