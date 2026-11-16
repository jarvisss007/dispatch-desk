r"""ON SITE RIGHT NOW — every truck sitting at a customer, how long it has been
there, and whether detention is running, about to start, or lost.

Detention is usually audited after the truck has left. Brokers want to hear
WHILE the driver is waiting — that is when a revised rate confirmation can
still be asked for before delivery. This is the live half: it reads, it never
drafts or sends.

How (all live, nothing stored):
  truck stopped inside a customer geofence  -> GPS position + how long
                                               it has been stopped
  which load                                -> today's board row whose TRK
                                               cell names that truck
  appointment                               -> the row's second line
  free time                                 -> settings.DETENTION_TERMS_FILE
                                               (2h house rule, a few are 3h)

The house rules: the clock starts at arrival but never before the
appointment; arriving late for the appointment means no detention at all.
Stops at direct customers (settings.DIRECT_CUSTOMERS) are claimed on site
times, so they are shown as "have the driver write his times", not "ask the
broker".

Limits: only trucks with a live tracker; "arrived" is when the truck last
stopped, so a truck that pulled forward in the yard looks like a new arrival.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dispatch_desk import geo, pools, settings  # noqa: E402

_UNIT = re.compile(settings.UNIT_PATTERN, re.I)
_DUR = re.compile(r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?")
_APPT = re.compile(r"(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})(?::(\d{2}))?\s*([AP])M", re.I)
_DIRECT = re.compile(settings.DIRECT_CUSTOMERS, re.I)
LATE_GRACE_MIN = 15
SOON_MIN = 45
SKIP_FENCES = re.compile(settings.SKIP_FENCES, re.I)


def _norm_unit(s: str) -> str:
    return re.sub(r"[\s-]", "", s).upper()


def _dur_min(s: str) -> int | None:
    m = _DUR.fullmatch((s or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mi


def free_minutes(customer: str) -> tuple[int, bool]:
    """(minutes, confirmed?) — longest matching key wins; default 120, unconfirmed."""
    try:
        terms = json.loads(settings.DETENTION_TERMS_FILE.read_text(encoding="utf-8"))["free_min"]
    except (OSError, ValueError, KeyError):
        terms = {}
    up = customer.upper()
    hits = [k for k in terms if k.upper() in up]
    if hits:
        return int(terms[max(hits, key=len)]), True
    return 120, False


def parse_appt(text: str, now: datetime) -> datetime | None:
    m = _APPT.search(text or "")
    if not m:
        return None
    hour = int(m.group(3)) % 12 + (12 if m.group(5).upper() == "P" else 0)
    try:
        return datetime(now.year, int(m.group(1)), int(m.group(2)), hour, int(m.group(4) or 0))
    except ValueError:
        return None


def judge(arrived: datetime, appt: datetime | None, free_min: int, now: datetime) -> dict:
    """The house detention rules, as one pure function."""
    if appt is None:
        return {"state": "unknown", "say": "no appointment time readable on the board"}
    if arrived > appt + timedelta(minutes=LATE_GRACE_MIN):
        late = int((arrived - appt).total_seconds() // 60)
        return {"state": "late", "say": f"arrived {late // 60}h{late % 60:02d}m after the appointment — no detention"}
    start = max(arrived, appt) + timedelta(minutes=free_min)
    mins = int((now - start).total_seconds() // 60)
    if mins >= 0:
        return {"state": "running", "over_min": mins, "start": start,
                "say": f"detention running {mins // 60}h{mins % 60:02d}m (since {start:%I:%M %p})"}
    if -mins <= SOON_MIN:
        return {"state": "soon", "over_min": mins, "start": start,
                "say": f"detention starts in {-mins} min (at {start:%I:%M %p})"}
    return {"state": "free", "over_min": mins, "start": start,
            "say": f"inside free time until {start:%I:%M %p}"}


def watch(rows: list[dict], C: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    try:
        import fleethunt_api as fh
        fences = [f for f in fh.fetch_pois() if not SKIP_FENCES.search(f.get("name") or "")]
        trucks = [d for d in fh.positions() if d.kind == "tractor" and d.live and not d.moving]
    except Exception as e:
        return {"ok": False, "why": f"GPS not reachable ({type(e).__name__})", "stops": []}
    by_unit: dict[str, list[dict]] = {}
    for r in rows:
        for u in _UNIT.findall(r["cells"][C["trk"]]):
            by_unit.setdefault(_norm_unit(u), []).append(r)
    stops = []
    for d in trucks:
        here = None
        for f in fences:
            try:
                dist = geo.miles((d.lat, d.lng), (float(f["lat"]), float(f["lng"]))) / geo.ROAD_FACTOR
            except (KeyError, TypeError, ValueError):
                continue
            if dist <= max(pools.MIN_RADIUS_MI, float(f.get("radius_m") or 0) / 1609.34):
                here = f
                break
        if not here and (abs(d.lat - geo.YARD[0]) < 0.02 and abs(d.lng - geo.YARD[1]) < 0.02):
            continue                                    # parked at our own yard
        dur = _dur_min(d.state_dur)
        if dur is None or dur < 10:
            continue
        arrived = now - timedelta(minutes=dur)
        pos = (d.lat, d.lng)
        row, end = None, ""
        for r in by_unit.get(_norm_unit(d.unit), []):
            for which, key in (("receiver", "dpos"), ("shipper", "ppos")):
                p = r.get(key) or geo.locate(r["cells"][C["delivery" if which == "receiver" else "pickup"]],
                                             r["section"] if which == "shipper" else "")
                if p and geo.miles(pos, p[:2]) <= 3:
                    row, end = r, which
                    break
            if row:
                break
        if not row:
            # TRK often carries only a time on the row he is working now. Go by the
            # man: whoever the board puts in this truck anywhere today, and his
            # still-open row at this site.
            names = {n for r in by_unit.get(_norm_unit(d.unit), []) for n in (r["drv"] or r["pre"])}
            if d.driver:
                names.add(d.driver.split()[0])
            for r in rows:
                if r["stage"] == "DELIVERED" or not names & set(r["pre"] + r["drv"]):
                    continue
                for which, key, col in (("receiver", "dpos", "delivery"), ("shipper", "ppos", "pickup")):
                    p = r.get(key) or geo.locate(r["cells"][C[col]], r["section"] if which == "shipper" else "")
                    mine = r["drv"] if which == "receiver" else r["pre"]
                    if p and geo.miles(pos, p[:2]) <= 3 and names & set(mine or r["pre"] + r["drv"]):
                        row, end = r, which
                        break
                if row:
                    break
        if not here and not row:
            continue                                    # stopped somewhere that is neither a customer nor his stop
        if here:
            site = here["name"]
        else:
            cell = row["cells"][C["delivery" if end == "receiver" else "pickup"]]
            site = cell.splitlines()[0].strip()[:40] if cell else "?"
        item = {"unit": d.unit, "site": site, "stopped_min": dur,
                "arrived": arrived.isoformat(timespec="minutes"), "ref": "", "driver": "", "customer": "",
                "end": end, "appt": "", "direct": False}
        if row:
            col = C["delivery"] if end == "receiver" else C["pickup"]
            appt_text = " · ".join(c[col] for c in row["cont"] if c[col])
            free, confirmed = free_minutes(row["section"] + " " + site)
            j = judge(arrived, parse_appt(appt_text, now), free, now)
            who = (row["drv"] if end == "receiver" else row["pre"]) or row["drv"] or row["pre"]
            item.update({"ref": row["ref"], "driver": who[0] if who else "", "customer": row["section"],
                         "appt": appt_text, "free_min": free, "free_confirmed": confirmed,
                         "direct": bool(_DIRECT.search(row["section"])), **{k: v for k, v in j.items() if k != "start"}})
        else:
            item.update({"state": "noload", "say": "no load on today's board names this truck"})
        stops.append(item)
    order = {"running": 0, "soon": 1, "free": 2, "unknown": 3, "late": 4, "noload": 5}
    stops.sort(key=lambda s: (order.get(s["state"], 9), -s["stopped_min"]))
    return {"ok": True, "why": "", "stops": stops, "trucks_checked": len(trucks)}
