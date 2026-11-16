r"""Run the whole Dispatch Desk in one go:   py -m dispatch_desk

  1. DAY_PLAN.html          trailer pools · drivers for uncovered loads · nearest backhauls
  2. DRIVER_PACKETS_*.html  the paper each driver picks up
  3. TOMORROW_DRAFT.*       tomorrow's tab, open loads carried forward
  +  INDEX.html             the home dashboard (desk + mail + fleet + every page)
  +  desk.json              the desk's numbers as data, for any other page to read

Reads the live board through its own copy. Writes only to its output folder
(settings.OUT_DIR / "DESK"; the demo uses ./out/DESK).
Sends nothing, assigns nothing, never writes the board.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dispatch_desk import packets, plan, source, tomorrow  # noqa: E402

STEPS = [
    ("Day plan", "Who is short on trailers, who takes the uncovered loads, and the nearest backhaul for every truck.",
     plan, "DAY_PLAN.html"),
    ("Driver packets", "One printable page per driver for today, with ARRIVED / LEFT boxes.",
     packets, "DRIVER_PACKETS_*.html"),
    ("Tomorrow's board", "Every open load carried forward, plus a copy-ready sheet.",
     tomorrow, "TOMORROW_DRAFT.html"),
]


def main() -> int:
    argv, results = sys.argv[:], []
    for title, what, mod, pattern in STEPS:
        sys.argv = [argv[0]]
        try:
            rc = mod.main()
            err = "" if rc == 0 else f"finished with code {rc}"
        except Exception as e:                # one page failing must not stop the others
            traceback.print_exc()
            err = f"{type(e).__name__}: {e}"
        files = sorted(plan.OUT.glob(pattern), key=lambda p: p.stat().st_mtime)
        results.append((title, what, files[-1].name if files else None, err))
    sys.argv = argv
    from dispatch_desk import home
    try:
        front = home.build()
    except Exception as e:
        traceback.print_exc()
        results.append(("Home", "", None, f"{type(e).__name__}: {e}"))
        front = plan.OUT / "INDEX.html"
    print(f"[desk] front page: {front}")
    return 1 if any(e for *_, e in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
