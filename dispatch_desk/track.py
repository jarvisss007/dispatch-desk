r"""TRACKING — will each open load make its appointment, from where the truck is now.

For every load on today's tab that is not delivered and has a name on it:

  which leg       still to be picked up -> the shipper and the pickup time;
                  otherwise             -> the receiver and the delivery time
  which truck     the unit in the row's TRK cell; else the unit the board puts
                  that driver in on any row today; else the driver name on the
                  GPS tracker
  where it is     live GPS position
  miles to go     straight line x 1.25 (geo.miles), driven at 50 mph
  verdict         there / on time / tight / LATE / not moving yet / no tracker

A window ("8AM - 4PM") is late only after it closes; a single time gets 15
minutes of grace. No routing service is called: no paid calls, live board.

It reads; it never messages a driver or a customer.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dispatch_desk import geo, settings  # noqa: E402

MPH = 50
GRACE_MIN = 15
TIGHT_MIN = 45
THERE_MI = 3
CITY_MI = 12          # a stop known only by its city: "there" means anywhere in town
LIVE_MIN = 20         # a position older than this is where the truck WAS: shown, never judged
STALE_MIN = 120       # older than this the tracker is silent: no position worth showing
_UNIT = re.compile(settings.UNIT_PATTERN, re.I)
_DAY = re.compile(r"(\d{1,2})/(\d{1,2})")
_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([AP])\.?M", re.I)
PICKUP_STAGES = ("UNKNOWN", "DISPATCHED", "TO_SHIPPER", "AT_SHIPPER")
DONE = ("DELIVERED", "CANCELED")


def _norm_unit(s: str) -> str:
    return re.sub(r"[\s-]", "", s).upper()


def window(text: str, now: datetime) -> tuple[datetime, datetime] | None:
    """(opens, closes) from a board appointment cell: '11/11 - 9AM',
    '11/11 - 8AM - 4PM', '11/11 - 9PM - 11/12 - 3AM'. None if no day and time."""
    days = list(_DAY.finditer(text or ""))
    times = list(_TIME.finditer(text or ""))
    if not days or not times:
        return None

    def at(day_m, t_m):
        hour = int(t_m.group(1)) % 12 + (12 if t_m.group(3).upper() == "P" else 0)
        d = datetime(now.year, int(day_m.group(1)), int(day_m.group(2)), hour, int(t_m.group(2) or 0))
        if d < now - timedelta(days=300):                 # a January date read in December
            d = d.replace(year=now.year + 1)
        return d

    try:
        start = at(days[0], times[0])
        if len(times) == 1:
            return start, start + timedelta(minutes=GRACE_MIN)
        end_day = days[1] if len(days) > 1 and days[1].start() < times[-1].start() else days[0]
        end = at(end_day, times[-1])
        if end <= start:
            end += timedelta(days=1)                      # '9PM - 3AM'
        return start, end
    except ValueError:
        return None


def verdict(miles: float | None, moving: bool, close: datetime, now: datetime) -> dict:
    """One pure rule. miles None = the stop could not be placed on a map."""
    if miles is None:
        return {"state": "unplaced", "say": "address not on the map — cannot work out an arrival"}
    if miles <= THERE_MI:
        return {"state": "there", "say": "truck is at the stop", "eta": now}
    eta = now + timedelta(minutes=miles / MPH * 60)
    slack = int((close - eta).total_seconds() // 60)
    if slack < 0:
        late = -slack
        return {"state": "late", "eta": eta, "late_min": late,
                "say": f"LATE by about {late // 60}h{late % 60:02d}m even leaving now" if not moving
                else f"LATE by about {late // 60}h{late % 60:02d}m"}
    if not moving and slack <= TIGHT_MIN:
        return {"state": "tight", "eta": eta, "say": f"must roll within {slack} min — truck is not moving"}
    if slack <= TIGHT_MIN:
        return {"state": "tight", "eta": eta, "say": f"only {slack} min to spare"}
    if not moving:
        go = close - timedelta(minutes=miles / MPH * 60)
        return {"state": "waiting", "eta": eta, "leave_by": go, "say": f"not moving yet — latest to leave {go:%a %I:%M %p}"}
    return {"state": "ontime", "eta": eta, "say": f"on time, about {slack // 60}h{slack % 60:02d}m to spare"}


MAX_HISTORY_CALLS = 20


def _already_there(items: list[dict], now: datetime, history=None) -> None:
    """A load that looks late because its time has passed may simply be done:
    the board's STATUS column lags the truck. For those, ask GPS whether the
    truck stopped at that address since yesterday. If it did, the load is not
    late — the board needs updating."""
    if history is None:
        try:
            import fleethunt_api as fh
            history = fh.fetch_history
        except Exception:
            history = None
    asked: dict[tuple, list] = {}                         # one call per truck per day, however many loads it has

    def stops_of(dev, day: str) -> list:
        if (dev, day) not in asked:
            if len(asked) >= MAX_HISTORY_CALLS:
                return []
            try:
                asked[(dev, day)] = (history(dev, day) or {}).get("stops", [])
            except Exception:
                asked[(dev, day)] = []
        return asked[(dev, day)]

    for x in items:
        chk = x.pop("_check", None)
        if not chk or history is None:
            continue
        dev, stop, near = chk if len(chk) == 3 else (*chk, THERE_MI)
        seen = []
        for day in (now - timedelta(days=1), now):
            for s in stops_of(dev, f"{day:%Y-%m-%d}"):
                try:
                    if geo.miles((float(s["latitude"]), float(s["longitude"])), stop) <= near:
                        seen.append((datetime.strptime(s["stop_start"], "%Y-%m-%d %H:%M:%S"),
                                     datetime.strptime(s["stop_end"], "%Y-%m-%d %H:%M:%S")))
                except (KeyError, TypeError, ValueError):
                    continue
        opens = datetime.fromisoformat(x["opens"])
        seen = [v for v in seen if v[1] >= opens - timedelta(hours=12)]
        if seen:
            a, b = min(v[0] for v in seen), max(v[1] for v in seen)
            if near > THERE_MI:
                say = (f"truck was in that town {a:%a %I:%M %p} to {b:%I:%M %p} — probably delivered; the desk has "
                       "no exact address, so confirm with the driver")
            else:
                say = f"truck was at this stop {a:%a %I:%M %p} to {b:%I:%M %p} — board status not updated"
            x.update(state="been", eta="", say=say)


MEASURED = ("late", "tight", "waiting", "ontime", "there")
FAR_MI = 50


def _in_order(items: list[dict], now: datetime) -> None:
    """One truck, several loads on the tab: it can only be driving to one of
    them. Take that truck's stops in appointment order.

      passed   the time has gone, GPS shows no stop there, and the truck is now
               at (or clearly nearer to) a later stop — it is not driving there,
               so an arrival time would be fiction. Someone must check the board.
      chained  a stop that comes after another is reached FROM that stop, not
               from where the truck is now, and not before that stop opens.
    """
    by: dict[str, list[dict]] = {}
    for x in items:
        if x.get("unit") and x.get("_stop") and x["state"] in MEASURED + ("been",):
            by.setdefault(x["unit"], []).append(x)
    for legs in by.values():
        if len(legs) < 2:
            continue
        legs.sort(key=lambda x: x["closes"])
        for i, x in enumerate(legs):
            close = datetime.fromisoformat(x["closes"])
            if x["state"] != "late" or close >= now:
                continue
            later = next((y for y in legs[i + 1:] if x["_mi"] > FAR_MI and (
                y["state"] in ("there", "been") or y["_mi"] * 2 < x["_mi"])), None)
            if later:
                ago = int((now - close).total_seconds() // 60)
                how = {"there": "at", "been": "and has since been at"}.get(later["state"], "nearer to")
                x.update(state="passed", eta="", say=(
                    f"time passed {ago // 60}h{ago % 60:02d}m ago and GPS shows no stop there by this truck; it is "
                    f"{round(x['_mi'])} mi away, {how} load {later['ref']}"))
        clock, pos, prev = now, None, None
        for x in legs:
            if x["state"] in ("passed", "been"):
                continue
            opens, close = datetime.fromisoformat(x["opens"]), datetime.fromisoformat(x["closes"])
            if x["state"] == "there":
                eta = clock
            elif prev is None:
                eta = datetime.fromisoformat(x["eta"])
            else:
                eta = clock + timedelta(minutes=geo.miles(pos, x["_stop"]) / MPH * 60)
                gone = (eta - now).total_seconds() / 3600 * MPH
                v = verdict(max(gone, THERE_MI + 1), x["_moving"], close, now)
                x.update(state=v["state"], eta=v["eta"].isoformat(timespec="minutes"),
                         say=f"{v['say']} — after load {prev['ref']} first")
            clock, pos, prev = max(eta, opens), x["_stop"], x


def _namelike(w: str) -> bool:
    """A note typed in the driver cell ('VERY STRICT AT SHIPPER. NEED ...') is not a man."""
    return (bool(w) and len(w) <= 24 and len(w.split()) <= 3 and not re.search(r"[.!:;]\s|\d", w)
            and not set(re.findall(r"[A-Z]+", w.upper())) & _NOTE_WORDS)


# words the office types in the DRIVER cell that are not a person ("2ND LOAD", "TRAILER BLOCKED")
_NOTE_WORDS = {"LOAD", "LOADS", "TRAILER", "BLOCKED", "EMPTY", "READY", "DROP", "NEED", "NEEDS", "HOLD", "CALL",
               "TBD", "OPEN", "PENDING", "CANCEL", "CANCELED", "CANCELLED", "REQUESTED", "DRIVER",
               "CALLED", "OFF", "PRIORITY", "PICKUP", "PICK", "TO", "ASAP", "WAITING", "SICK"}


def _silent_for(age_min: float) -> str:
    if age_min > 5e5:
        return "tracker has never reported — position unknown"
    if age_min > 2880:
        return f"tracker silent for {age_min / 1440:.0f} days — position unknown"
    return f"tracker silent for {age_min / 60:.0f} h — position unknown"


def freshness(age_min: float) -> str:
    """'live' (judge it), 'old' (show where it was, give no verdict) or 'silent'."""
    return "silent" if age_min > STALE_MIN else "old" if age_min > LIVE_MIN else "live"


def _last_seen(age_min: float, where: str, miles) -> str:
    far = f", {round(miles)} mi from this stop" if miles is not None and miles > THERE_MI else \
        ", at this stop" if miles is not None else ""
    return (f"last seen {where}{far}, {round(age_min)} min ago — too old to say whether it makes the time "
            "or has already delivered")


def follow(rows: list[dict], C: dict, same=None, now: datetime | None = None, devices=None, history=None) -> dict:
    now = now or datetime.now()
    same = same or (lambda w: w)
    try:
        if devices is None:
            import fleethunt_api as fh
            devices = [d for d in fh.positions() if d.kind == "tractor"]
    except Exception as e:
        return {"ok": False, "why": f"GPS not reachable ({type(e).__name__})", "loads": []}
    by_unit = {_norm_unit(d.unit): d for d in devices}
    by_gps_name = {}
    for d in devices:
        if d.driver:
            by_gps_name.setdefault(same(d.driver.split()[0].upper()), d)
    # the truck the board puts each man in today (only rows that name exactly one man and one truck)
    truck_of: dict[str, str] = {}
    for r in rows:
        units = {_norm_unit(u) for u in _UNIT.findall(r["cells"][C["trk"]])}
        people = {same(w) for w in (r["drv"] or r["pre"])}
        if len(units) == 1 and len(people) == 1:
            truck_of.setdefault(next(iter(people)), next(iter(units)))
    out = []
    for r in rows:
        if r["stage"] in DONE or not r["ref"].strip():
            continue
        pickup = r["stage"] in PICKUP_STAGES and bool(r["pre"])
        who = [w for w in (r["pre"] if pickup else (r["drv"] or r["pre"])) if _namelike(w)]
        if not who:
            continue
        col = C["pickup"] if pickup else C["delivery"]
        text = " · ".join(str(c[col]) for c in r["cont"] if c[col])
        w = window(text, now)
        if not w or w[1] < now - timedelta(hours=18) or w[0] > now + timedelta(hours=36):
            continue                                      # no time on the board, or not today's business
        # classify() only places out-of-town stops; place local ones here (gate, known address, then city)
        stop = (r.get("ppos") if pickup else r.get("dpos")) or geo.locate(r["cells"][col], r["section"] if pickup else "")
        place = (r["cells"][col] or "").splitlines()[0].strip()[:44] or r["section"]
        units = [_norm_unit(u) for u in _UNIT.findall(r["cells"][C["trk"]])]
        d = next((by_unit[u] for u in units if u in by_unit), None)
        how = "truck on this row"
        if d is None and same(who[0]) in truck_of and truck_of[same(who[0])] in by_unit:
            d, how = by_unit[truck_of[same(who[0])]], "his truck on another row today"
        if d is None and same(who[0]) in by_gps_name:
            d, how = by_gps_name[same(who[0])], "driver name on the tracker"
        item = {"ref": r["ref"], "customer": r["section"], "leg": "pickup" if pickup else "delivery",
                "place": place, "appt": text, "opens": w[0].isoformat(timespec="minutes"),
                "closes": w[1].isoformat(timespec="minutes"), "driver": who[0], "unit": d.unit if d else "",
                "found_by": how if d else "", "miles": None, "where": "", "eta": "", "fix_min": None}
        if d is None:
            item.update(state="notruck", say="no truck with a tracker found for this driver")
        elif d.age_min > STALE_MIN:
            item.update(state="silent", fix_min=round(d.age_min) if d.age_min < 5e5 else None,
                        say=_silent_for(d.age_min))
        else:
            miles = geo.miles((d.lat, d.lng), stop[:2]) if stop else None
            at_yard = abs(d.lat - geo.YARD[0]) < 0.02 and abs(d.lng - geo.YARD[1]) < 0.02
            rough = bool(stop) and len(stop) > 2 and stop[2] == "city"
            near = CITY_MI if rough else THERE_MI
            v = verdict(0 if miles is not None and miles <= near else miles, d.moving, w[1], now)
            if rough and v["state"] == "there":
                v["say"] = "truck is in that town (the desk has no exact address for this stop)"
            item.update(state=v["state"], say=v["say"], miles=round(miles) if miles is not None else None,
                        fix_min=round(d.age_min), eta=v["eta"].isoformat(timespec="minutes") if v.get("eta") else "",
                        where=("at our yard" if at_yard else "moving" if d.moving else "stopped")
                        + (f", {round(miles)} mi out" if miles is not None and miles > THERE_MI else ""))
            if freshness(d.age_min) == "old":
                # An old position proves nothing about now. Say where the truck was and stop there;
                # the stop history below can still prove it has BEEN to the stop.
                where = "at our yard" if at_yard else "moving" if d.moving else "stopped"
                item.update(state="lastseen", eta="", where=f"was {where}", say=_last_seen(d.age_min, where, miles))
                if stop and w[0] < now:
                    item["_check"] = (d.device_id, stop[:2], near)
            else:
                if stop:
                    item.update(_stop=stop[:2], _mi=miles, _moving=d.moving)
                if v["state"] == "late" and w[1] < now and stop:
                    item["_check"] = (d.device_id, stop[:2], near)
        out.append(item)
    _already_there(out, now, history)
    _in_order(out, now)
    for x in out:
        for k in ("_stop", "_mi", "_moving"):
            x.pop(k, None)
    order = {"late": 0, "tight": 1, "passed": 2, "lastseen": 3, "silent": 4, "notruck": 5, "unplaced": 6,
             "waiting": 7, "ontime": 8, "been": 9, "there": 10}
    out.sort(key=lambda x: (order[x["state"]], x["closes"]))
    return {"ok": True, "why": "", "loads": out, "count": {k: sum(1 for x in out if x["state"] == k) for k in order}}


WORD = {"late": "LATE", "tight": "Tight", "silent": "Tracker silent", "notruck": "No truck found",
        "unplaced": "Stop not on the map", "waiting": "Not moving yet", "ontime": "On time", "there": "At the stop",
        "been": "Already been there", "passed": "Time passed — check", "lastseen": "GPS too old to judge"}
FIX = {"late": "Call the customer now with the new arrival time, before they call you.",
       "tight": "Call the driver; warn the customer if he is not rolling.",
       "silent": "Call the driver for his position.",
       "lastseen": "Call the driver for his position, or wait for the tracker to report again.",
       "notruck": "Type the truck number in the TRK cell so the desk can follow it.",
       "been": "Update the STATUS cell on the board.",
       "passed": "Ask the driver: delivered already, handed to someone else, or still owed? Then fix the board."}


def page_body(t: dict, gd, esc) -> str:
    if not t["ok"]:
        return gd.callout("Tracking could not run this time.", t["why"])
    c = t["count"]
    if not t["loads"]:
        return gd.callout("No open load on today's tab has both a name and an appointment in the next day and a half.", "")
    body = gd.callout(
        f"{len(t['loads'])} loads being followed: {c['late']} late, {c['tight']} tight, "
        f"{c['ontime'] + c['there']} on time or at the stop, {c['been']} already done but not marked on the "
        f"board, {c['passed']} past their time with the truck on another load (check these), "
        f"{c['waiting']} not moving yet, "
        f"{c['lastseen']} with a GPS position too old to judge, "
        f"{c['silent'] + c['notruck'] + c['unplaced']} the desk cannot see.",
        f"A verdict is given only from a GPS position under {LIVE_MIN} minutes old. Between {LIVE_MIN} minutes and "
        f"{STALE_MIN // 60} hours the desk shows where the truck was and says nothing more; past that the tracker "
        "counts as silent. Delivered is never taken from GPS, only from the board. "
        f"Arrival = miles left (straight line x 1.25) at {MPH} mph from the truck's last GPS position. "
        "No traffic, no fuel or rest stops, so a long run will really take longer. "
        "A window is late only after it closes. A truck with several loads is taken through its stops in "
        "appointment order, each one reached from the stop before it.")
    rows = []
    for x in t["loads"]:
        hot = x["state"] in ("late", "tight", "passed", "lastseen", "silent", "notruck")
        eta = datetime.fromisoformat(x["eta"]).strftime("%a %I:%M %p") if x["eta"] else "—"
        rows.append([
            f'<span class="{"sug" if hot else "ok"}"><b>{WORD[x["state"]]}</b></span>',
            f'<span class="mono">{esc(x["ref"])}</span><small>{esc(x["customer"])}</small>',
            f'{"Pick up at" if x["leg"] == "pickup" else "Deliver to"} {esc(x["place"])}<small>{esc(x["appt"])}</small>',
            f'<b>{esc(x["driver"])}</b><small>{esc(x["unit"] or "no truck")}'
            f'{" · " + esc(x["found_by"]) if x["found_by"] and x["found_by"] != "truck on this row" else ""}</small>',
            f'{esc(x["where"]) or "—"}<small>{"GPS " + str(x["fix_min"]) + " min ago" if x["fix_min"] is not None else ""}</small>',
            eta,
            f'{esc(x["say"])}<small>{FIX.get(x["state"], "")}</small>'])
    return body + gd.table(["", "Load", "Stop and appointment", "Driver and truck", "Truck is", "Arrives about", "What it means"],
                           rows, ["l"] * 7)
