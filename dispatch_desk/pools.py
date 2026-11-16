r"""TRAILER POOLS — how many of our trailers are sitting at each customer right now,
against how many that customer was promised.

Why: customers complain in writing when the pool runs short ("only three
trailers on site when the pool is meant to be seven"). The board cannot answer
"how many are there" — it lists loads, not trailers — so this counts them
from live GPS:

  on site   = tracked trailers inside the customer's geofence
              (at least 0.35 mi, yards are big)
  loaded    = of those, the ones the board names on a load still to be pulled
  wanted    = customer_needs.json in the desk's state folder (numbers the
              customers stated; the office can edit them)

What this can NOT see: a trailer with no tracker, and whether an on-site
trailer the board doesn't mention is empty or loaded ("delivered" never
proved "empty"). So the count is a floor and "not on the board" is reported
as exactly that.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dispatch_desk import settings  # noqa: E402

MIN_RADIUS_MI = 0.35
_STOP = {"DC", "AZ", "CA", "NV", "THE", "YARD", "CO", "&"}


def _mi(a, b, c, d) -> float:
    return 3959 * 2 * math.asin(math.sqrt(
        math.sin(math.radians(c - a) / 2) ** 2
        + math.cos(math.radians(a)) * math.cos(math.radians(c))
        * math.sin(math.radians(d - b) / 2) ** 2))


def _tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[A-Z0-9]+", name.upper()) if t not in _STOP]


def fence_for(section: str, fences: list[dict]) -> dict | None:
    """The geofence whose name matches a board section: same first two words
    ('PALMCO VEGAS'), in either order of length ('SUNPEAK DC' ~ 'SUNPEAK DC GOODYEAR AZ')."""
    st = _tokens(section)
    if not st:
        return None
    best = None
    for f in fences:
        ft = _tokens(f.get("name") or "")
        if not ft:
            continue
        n = min(len(st), len(ft), 2)
        if st[:n] == ft[:n] and (n == 2 or len(st) == len(ft) == 1):
            if best is None or len(ft) < len(_tokens(best["name"])):
                best = f
    if best is None and len(st) == 1:           # 'SUNPEAK DC' ~ 'SUNPEAK DC GOODYEAR AZ'
        same = [f for f in fences if _tokens(f.get("name") or "")[:1] == st]
        if len(same) == 1:
            best = same[0]
    return best


def inventory() -> list[dict]:
    """Every geofence with our parked trailers inside it:
    [{'name', 'lat', 'lng', 'units': [...]}]. Empty list if GPS is down."""
    try:
        import fleethunt_api as fh
        fences = fh.fetch_pois()
        trailers = [d for d in fh.positions() if d.kind == "trailer" and not d.moving]
    except Exception:
        return []
    out = []
    for f in fences:
        try:
            la, lo = float(f["lat"]), float(f["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        r = max(MIN_RADIUS_MI, float(f.get("radius_m") or 0) / 1609.34)
        units = sorted(d.unit for d in trailers if _mi(la, lo, d.lat, d.lng) <= r)
        if units:
            out.append({"name": f["name"], "lat": la, "lng": lo, "units": units})
    return out


def count(sections: list[str], loaded_by_trailer: dict[str, str], needs: dict) -> dict:
    """{'ok': bool, 'why': str, 'sites': [...]} — one entry per section that
    has a geofence. loaded_by_trailer: trailer# -> load# for rows still to pull."""
    try:
        import fleethunt_api as fh
        fences = fh.fetch_pois()
        trailers = [d for d in fh.positions() if d.kind == "trailer"]
    except Exception as e:                      # GPS down must not sink the plan
        return {"ok": False, "why": f"GPS not reachable ({type(e).__name__})", "sites": []}
    if not trailers:
        return {"ok": False, "why": "GPS returned no trailers", "sites": []}
    sites = []
    levels = usual()
    for sec in sorted({s.strip() for s in sections if s.strip()}):
        f = fence_for(sec, fences)
        if not f:
            continue
        try:
            la, lo = float(f["lat"]), float(f["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        r = max(MIN_RADIUS_MI, float(f.get("radius_m") or 0) / 1609.34)
        here = sorted((d for d in trailers if _mi(la, lo, d.lat, d.lng) <= r and not d.moving),
                      key=lambda d: d.unit)
        loaded = [d for d in here if d.unit in loaded_by_trailer]
        need = needs.get(sec.upper(), {})
        want, note, derived = need.get("trailers_on_site"), need.get("notes", ""), False
        u = levels.get(sec) or levels.get(sec.upper())
        if want is None and u and u["median"] > 0:
            # nobody has stated a pool size: hold the customer to its own usual level
            want, derived = u["median"], True
            note = f"usual level: {u['days']} weekdays of GPS, between {u['low']} and {u['high']} there"
        short = (want - len(here)) if want is not None else None
        if derived and short < 2:               # one under "usual" is an ordinary day, not a shortage
            short = 0
        sites.append({
            "section": sec, "fence": f["name"], "on_site": len(here),
            "units": [d.unit for d in here],
            "loaded": [(d.unit, loaded_by_trailer[d.unit]) for d in loaded],
            "other": [d.unit for d in here if d.unit not in loaded_by_trailer],
            "want": want, "short": short, "derived": derived, "usual": u,
            "source": note,
            "oldest_fix_h": max((d.age_min for d in here), default=0) / 60,
        })
    return {"ok": True, "why": "", "sites": sites, "tracked": len(trailers)}


# ---------------------------------------------------------------- history
# No file anywhere states a pool size for most customers, so the desk keeps
# its own count: the most of our trailers seen at each customer on each day.
# After ten days that gives "usually N there", shown where no size is set.
HISTORY = settings.STATE_DIR / "pool_history.json"
KEEP_DAYS, MIN_DAYS = 60, 10


def remember(day, pc: dict) -> None:
    if not pc.get("ok"):
        return
    try:
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hist = {}
    today = hist.setdefault(day.isoformat(), {})
    for s in pc["sites"]:
        today[s["section"]] = max(today.get(s["section"], 0), s["on_site"])
    hist = dict(sorted(hist.items())[-KEEP_DAYS:])
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist, indent=1), encoding="utf-8")


def usual() -> dict[str, dict]:
    """{customer: {median, low, high, days}} once a customer has ten days counted."""
    try:
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    from datetime import date
    per: dict[str, list[int]] = {}
    for day, counts in hist.items():
        if date.fromisoformat(day).weekday() >= 5:      # weekends run thin and would drag "usual" down
            continue
        for sec, n in counts.items():
            per.setdefault(sec, []).append(n)
    out = {}
    for sec, v in per.items():
        if len(v) >= MIN_DAYS:
            v = sorted(v)
            out[sec] = {"median": v[len(v) // 2], "low": v[0], "high": v[-1], "days": len(v)}
    return out


def backfill(days: int = 28) -> dict:
    """Rebuild the daily count for the last `days` days from the GPS vendor's stop
    history (one read-only call per trailer; needs a real GPS client, not the demo stand-in). A trailer counts for a customer
    on a day when it was parked inside that customer's geofence at 6 AM or
    3 PM; the day's number is the larger of the two. Today is left alone —
    the live run owns it."""
    import urllib.request
    from datetime import datetime, timedelta
    from urllib.parse import urlencode
    import fleethunt_api as fh
    fences = fh.fetch_pois()
    try:
        secs = list(json.loads((settings.STATE_DIR / "customer_needs.json").read_text(encoding="utf-8"))["customers"])
    except (OSError, ValueError, KeyError):
        secs = []
    sites = {}
    for sec in secs:
        f = fence_for(sec, fences)
        try:
            sites[sec] = (float(f["lat"]), float(f["lng"]), max(MIN_RADIUS_MI, float(f.get("radius_m") or 0) / 1609.34))
        except (TypeError, KeyError, ValueError):
            continue
    end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    marks = [(start + timedelta(days=i)).replace(hour=h) for i in range(days) for h in (6, 15)]
    seen = {m: {s: 0 for s in sites} for m in marks}
    trailers = [d for d in fh.positions() if d.kind == "trailer" and d.device_id]
    got = 0
    for d in trailers:
        q = urlencode({"device_id": d.device_id, "from_date": f"{start:%Y-%m-%d} 00:00:00",
                       "to_date": f"{end - timedelta(seconds=1):%Y-%m-%d %H:%M:%S}", "min_stop_duration": 60})
        req = urllib.request.Request(f"{fh.BASE}/history?{q}",
                                     headers={"Authorization": f"Bearer {fh._key()}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                stops = json.load(r).get("stops", [])
        except Exception:
            continue
        got += 1
        for s in stops:
            try:
                la, lo = float(s["latitude"]), float(s["longitude"])
                a = datetime.strptime(s["stop_start"], "%Y-%m-%d %H:%M:%S")
                b = datetime.strptime(s["stop_end"], "%Y-%m-%d %H:%M:%S")
            except (KeyError, TypeError, ValueError):
                continue
            where = next((sec for sec, (fla, flo, r) in sites.items() if _mi(fla, flo, la, lo) <= r), None)
            if where:
                for m in marks:
                    if a <= m <= b:
                        seen[m][where] += 1
    try:
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hist = {}
    for i in range(days):
        day = start + timedelta(days=i)
        hist[f"{day:%Y-%m-%d}"] = {s: max(seen[day.replace(hour=6)][s], seen[day.replace(hour=15)][s]) for s in sites}
    hist = dict(sorted(hist.items())[-KEEP_DAYS:])
    HISTORY.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    return {"trailers": len(trailers), "answered": got, "days": days, "customers": len(sites)}


if __name__ == "__main__" and "--backfill" in sys.argv:
    print("[pools] backfill", backfill())
    for sec, u in sorted(usual().items()):
        print(f"   {sec:28} usually {u['median']:2}  ({u['low']}-{u['high']}, {u['days']} weekdays)")
