"""The demo runs from nothing: sample data is generated, every page is built,
and the numbers in desk.json are not all zeros."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_demo_run_produces_desk_json(tmp_path):
    sample, out = tmp_path / "sample", tmp_path / "out"
    r = subprocess.run([sys.executable, str(ROOT / "run_demo.py"), "--sample", str(sample), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "Traceback" not in r.stdout + r.stderr
    desk = out / "DESK"
    for name in ("INDEX.html", "DAY_PLAN.html", "TRACKING.html", "SCORE.html", "EMPTY_RETURNS.html",
                 "ROSTER.html", "WEEK_AHEAD.html", "TOMORROW_DRAFT.html", "TRUCKS.html", "desk.json"):
        assert (desk / name).stat().st_size > 500, name
    assert list(desk.glob("DRIVER_PACKETS_*.html"))
    d = json.loads((desk / "desk.json").read_text(encoding="utf-8"))
    assert d["loads"]["on_tab"] > 20 and d["loads"]["open"] > 5
    assert d["tracking"]["ok"] and len(d["tracking"]["loads"]) >= 8
    assert d["empty_returns"]["all"]["empty"] > 0 and d["empty_returns"]["fuel"]["empty_home_usd"] > 0
    assert any((p["short"] or 0) > 0 for p in d["pools"])
    assert sum(len(r["driver_suggestions"]) + len(r["backhaul_suggestions"]) for r in d["regions"].values()) >= 4
    # pages are dark only: nothing switches on the reader's colour-scheme preference
    for page in desk.glob("*.html"):
        assert "prefers-color-scheme" not in page.read_text(encoding="utf-8"), page.name
