r"""Smaller planners that feed the worklist:

  swap_clock()      READY trailers nobody has pulled since a previous tab —
                    a mill's usual rule is a swap within 24 hours of the request.
  broker_offers()   emailed broker loads that start near a truck coming home
                    empty and end back in Southern California.
  weekly_miles()    empty miles driven vs best possible, week by week.

All read-only: the board history already in memory, and an optional sqlite
file of emailed offers (settings.OFFERS_DB, table `offers`).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dispatch_desk import geo, settings  # noqa: E402

_CITY = re.compile(r"^[A-Za-z .'-]{3,30},\s*[A-Z]{2}$")
_PICK = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")


def swap_clock(days: list[tuple[date, list[dict]]], today_rows: list[dict], day: date, not_pulled) -> list[dict]:
    """Loads READY today that were already READY on an earlier tab and are
    still at the shipper. `days` is [(tab date, rows)] oldest first."""
    first: dict[str, date] = {}
    for d, rows in days:
        for r in rows:
            if r["ready"] and r["ref"]:
                first.setdefault(r["ref"], d)
    out = []
    for r in today_rows:
        fr = first.get(r["ref"])
        if r["ready"] and r["stage"] in not_pulled and fr and fr < day:
            out.append({"row": r, "since": fr, "days": (day - fr).days})
    out.sort(key=lambda x: (-x["days"], x["row"]["section"]))
    return out


def broker_offers(max_age_h: int = 30) -> list[dict]:
    """Recent emailed offers with a place we can put on the map."""
    db = Path(settings.OFFERS_DB) if settings.OFFERS_DB else None
    if db is None or not db.exists():
        return []
    cutoff = (datetime.now() - timedelta(hours=max_age_h)).isoformat(timespec="seconds")
    out = []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        rows = con.execute("select source, load_id, origin, destination, rate, pickup, last_seen "
                           "from offers where last_seen >= ?", (cutoff,)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    today = date.today()
    for src, lid, org, dst, rate, pick, seen in rows:
        if not (_CITY.match(org or "") and _CITY.match(dst or "")):
            continue                                    # parser noise, not a lane
        o, d = geo.locate(org.upper()), geo.locate(dst.upper())
        if not o:
            continue
        when = None
        m = _PICK.search(pick or "")
        if m:
            try:
                when = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)),
                                int(m.group(4) or 0), int(m.group(5) or 0))
            except ValueError:
                when = None
            if when and not (today <= when.date() <= today + timedelta(days=1)):
                continue                                # not today or tomorrow
        out.append({"source": src, "load": lid, "origin": org, "dest": dst, "rate": rate or 0,
                    "pickup": pick or "", "opos": o[:2], "dpos": d[:2] if d else None, "seen": seen})
    return out


def offers_for(pos, offers: list[dict], limit: int = 2, within_mi: float = 60) -> list[dict]:
    """Offers that start near `pos` and end closer to the yard than `pos` is."""
    home = geo.miles(pos, geo.YARD)
    hits = []
    for o in offers:
        dead = geo.miles(pos, o["opos"])
        if dead > within_mi:
            continue
        if o["dpos"] and geo.miles(o["dpos"], geo.YARD) > home * 0.5:
            continue                                    # does not bring him home
        hits.append({**o, "dead_mi": dead})
    hits.sort(key=lambda o: (o["dead_mi"], -o["rate"]))
    return hits[:limit]


def weekly_miles(days: list[tuple[date, list[dict]]], history_fn) -> list[dict]:
    """history_fn is plan.history; grouped by the Monday of each week."""
    weeks: dict[date, list] = {}
    for d, rows in days:
        weeks.setdefault(d - timedelta(days=d.weekday()), []).append(rows)
    out = []
    for wk in sorted(weeks):
        h = history_fn(weeks[wk])
        pairs = sum(v["pairs"] for v in h.values())
        if pairs:
            a, b = sum(v["actual"] for v in h.values()), sum(v["best"] for v in h.values())
            out.append({"week": wk, "pairs": pairs, "actual": a, "best": b})
    return out


# ---------------------------------------------------------------- week ahead
# Days a round trip takes: an Arizona or Central Valley driver is gone today
# and back tomorrow, so only half of them can leave on any one day.
TURN_DAYS = {"AZ": 2, "NV": 1, "CV": 2}


def _pickup_day(row: dict, pickup_col: int, today: date) -> date | None:
    for c in row["cont"][:1]:
        m = re.search(r"(\d{1,2})/(\d{1,2})", str(c[pickup_col] or ""))
        if m:
            try:
                d = date(today.year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return None
            return d if d >= today - timedelta(days=60) else date(today.year + 1, d.month, d.day)
    return None


def week_ahead(past: list[tuple[date, list[dict]]], future: list[tuple[date, list[dict]]],
               upcoming: list[dict], can_run: dict[str, int], today: date, pickup_col: int,
               codes: list[str], days: int = 6) -> list[dict]:
    """For each of the next `days` days and each region: out-of-town loads
    already on the board (that day's tab + the UPCOMING LOADS tab), what a
    normal same weekday looked like (median of the last six), and how many
    drivers can leave that day. A load counts once, on the first tab it shows."""
    seen, per_day = set(), {}
    for d, rows in past:
        n = {c: 0 for c in codes}
        for r in rows:
            if r["dr"] in n and r["pr"] != r["dr"] and r["ref"] not in seen:
                seen.add(r["ref"])
                n[r["dr"]] += 1
        per_day[d] = n
    out = []
    for i in range(1, days + 1):
        d = today + timedelta(days=i)
        same = [per_day[x] for x in sorted(per_day) if x.weekday() == d.weekday()][-6:]
        booked, refs = {c: 0 for c in codes}, set()
        for fd, rows in future:
            if fd == d:
                for r in rows:
                    if r["dr"] in booked and r["pr"] != r["dr"] and r["ref"] not in refs and r["ref"] not in seen:
                        refs.add(r["ref"])
                        booked[r["dr"]] += 1
        for r in upcoming:
            if r["dr"] in booked and r["pr"] != r["dr"] and r["ref"] not in refs and _pickup_day(r, pickup_col, today) == d:
                refs.add(r["ref"])
                booked[r["dr"]] += 1
        regs = {}
        for c in codes:
            v = sorted(x[c] for x in same)
            normal = v[len(v) // 2] if v else 0
            cap = can_run.get(c, 0) // TURN_DAYS.get(c, 1)
            regs[c] = {"booked": booked[c], "normal": normal, "can_leave": cap,
                       "gap": max(0, max(booked[c], normal) - cap)}
        out.append({"day": d, "regions": regs})
    return out
