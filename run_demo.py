r"""Run the whole Dispatch Desk against made-up sample data. No network, no keys.

    py run_demo.py              # (re)make the sample for right now, then build every page
    py run_demo.py --keep       # reuse the sample already in ./demo/sample

Pages are written to ./out/DESK/ — open ./out/DESK/INDEX.html.

The sample is rebuilt on each run because its "live" part (truck positions,
appointments an hour from now) is counted from the minute it is made. It is
deterministic all the same: same day, same loads.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the Dispatch Desk demo.")
    ap.add_argument("--keep", action="store_true", help="do not regenerate the sample data if it exists")
    ap.add_argument("--sample", default=str(REPO / "demo" / "sample"), help="where the sample data lives")
    ap.add_argument("--out", default=str(REPO / "out"), help="pages are written to <out>/DESK")
    a = ap.parse_args(argv)
    sample, out = Path(a.sample).resolve(), Path(a.out).resolve()
    for stream in (sys.stdout, sys.stderr):                 # a console that cannot print "·" must not stop the run
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    # Point the desk at the sample BEFORE it is imported: its settings read these once.
    os.environ["DESK_DATA_DIR"] = str(sample)
    os.environ["DESK_STATE_DIR"] = str(sample / "state")
    os.environ["DESK_OUT_DIR"] = str(out)
    os.environ.pop("DESK_BOARD_FILE", None)
    os.environ.pop("DESK_SETTINGS_FILE", None)
    for p in (REPO / "standins", REPO):
        sys.path.insert(0, str(p))

    if not (a.keep and (sample / "sample.json").exists()):
        sys.path.insert(0, str(REPO / "demo"))
        import make_sample
        rc = make_sample.main(["--out", str(sample), "--state", str(sample / "state")])
        if rc:
            return rc

    from dispatch_desk import __main__ as desk
    rc = desk.main()
    index = out / "DESK" / "INDEX.html"
    print(f"[demo] {'done' if rc == 0 else 'finished with errors'} — open {index}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
