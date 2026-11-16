r"""THE SCORE — did the office use what the desk suggested, and what did it cost?

Every run, the desk's suggestions for the day are written to
log/<day>.json in the desk's state folder. A suggestion is kept the FIRST time it is made
for a load: once the office types a name on the board the suggestion vanishes
from the plan, so the first one is the only honest record of what was offered.

Scoring reads the board again and puts each suggestion in one bin:
  taken     the board now shows the driver the desk suggested
  near      the board shows the desk's second or third choice
  other     the board shows somebody else
  open      the load still has no name on it
  gone      the load is no longer on that day's tab

Nothing here writes the board. It reads tabs and its own log.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from dispatch_desk import settings

LOG = settings.STATE_DIR / "log"
KINDS = (("drivers", "Out-of-town driver", "drv"), ("backhauls", "Backhaul", "pre"), ("local", "Local load", "any"))
BINS = ("taken", "near", "other", "open", "gone")


def _path(day: date) -> Path:
    return LOG / f"{day.isoformat()}.json"


def load(day: date) -> dict:
    try:
        return json.loads(_path(day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(day: date, regions: dict, local: list[dict], region_name: dict) -> dict:
    """Add this run's suggestions to the day's log. Never overwrites one already there."""
    log = load(day) or {"day": day.isoformat(), "drivers": {}, "backhauls": {}, "local": {}}
    now = datetime.now().isoformat(timespec="minutes")
    for code, r in regions.items():
        for x in r.get("driver_sug", []):
            if x["who"]:
                log["drivers"].setdefault(x["row"]["ref"], {
                    "who": x["who"], "alts": x["alts"], "where": region_name[code],
                    "customer": x["row"]["section"], "at": now})
        for m in r["matches"]:
            if m["how"] == "suggested":
                l = m["loads"][0]
                log["backhauls"].setdefault(l["ref"], {
                    "who": m["truck"]["who"], "alts": [], "where": region_name[code], "customer": l["section"],
                    "empty_mi": round(m["empty_mi"]) if m.get("empty_mi") is not None else None, "at": now})
    for x in local:
        if x["who"]:
            log["local"].setdefault(x["row"]["ref"], {
                "who": x["who"], "alts": x["alts"], "where": "Local", "customer": x["row"]["section"], "at": now})
    LOG.mkdir(parents=True, exist_ok=True)
    _path(day).write_text(json.dumps(log, indent=1), encoding="utf-8")
    return log


SAME_MI = 10          # two pullers within this many empty miles of each other: call it even


def closer(items: list[dict]) -> dict:
    """Backhauls where the office sent someone other than the desk's pick: whose
    truck unloaded nearer to the trailer? Only loads where BOTH can be measured."""
    out = {"office": 0, "desk": 0, "even": 0, "unmeasured": 0, "office_mi": 0.0, "desk_mi": 0.0}
    for i in items:
        if i["kind"] != "Backhaul" or i["result"] != "other":
            continue
        d, b = i.get("desk_mi"), i.get("board_mi")
        if d is None or b is None:
            out["unmeasured"] += 1
            continue
        out["office_mi"] += b
        out["desk_mi"] += d
        out["even" if abs(d - b) <= SAME_MI else "office" if b < d else "desk"] += 1
    return out


def judge(log: dict, rows: list[dict], same=None, board_mi=None) -> dict:
    """{'items': [...], 'count': {bin: n}, 'by_kind': {kind: {bin: n}}} for one day.
    board_mi(load_row, rows) -> the empty miles of the puller the board shows, or None."""
    same = same or (lambda w: w)
    by_ref = {}
    for r in rows:
        by_ref.setdefault(r["ref"], r)
    items = []
    for kind, label, col in KINDS:
        for ref, s in log.get(kind, {}).items():
            r = by_ref.get(ref)
            if r is None:
                got, where = [], "gone"
            else:
                got = r[col] if col != "any" else (r["pre"] + r["drv"])
                people = {same(w) for w in got}
                where = ("open" if not got else "taken" if same(s["who"]) in people
                         else "near" if any(same(a) in people for a in s.get("alts", [])) else "other")
            items.append({"kind": label, "load": ref, "customer": s.get("customer", ""), "where": s.get("where", ""),
                          "suggested": s["who"], "board": ", ".join(got), "result": where,
                          "desk_mi": s.get("empty_mi") if kind == "backhauls" else None,
                          "board_mi": board_mi(r, rows) if kind == "backhauls" and board_mi and got else None})
    count = {b: sum(1 for i in items if i["result"] == b) for b in BINS}
    by_kind = {label: {b: sum(1 for i in items if i["kind"] == label and i["result"] == b) for b in BINS}
               for _, label, _ in KINDS}
    return {"items": items, "count": count, "by_kind": by_kind, "n": len(items), "closer": closer(items)}


def days_logged() -> list[date]:
    out = []
    for p in sorted(LOG.glob("*.json")) if LOG.exists() else []:
        try:
            out.append(date.fromisoformat(p.stem))
        except ValueError:
            pass
    return out


WORD = {"taken": "Used the desk's pick", "near": "Used its 2nd or 3rd pick", "other": "Sent someone else",
        "open": "Still no name", "gone": "Load left the tab"}


def page_body(scores: list[dict], gd, esc) -> str:
    """scores: newest first, each {'day', 'judge', 'miles': {'pairs','actual','best'}, 'short': [...]}."""
    if not scores:
        return gd.callout("No day has been scored yet.", "The desk logs its suggestions from its first run.")
    t = scores[0]
    j = t["judge"]
    decided = j["n"] - j["count"]["open"] - j["count"]["gone"]
    used = j["count"]["taken"] + j["count"]["near"]
    body = gd.callout(
        f"{t['day']:%a %m/%d}: {j['n']} suggestions made. The office has put a name on {decided} of those loads — "
        f"{j['count']['taken']} the desk's first pick, {j['count']['near']} its second or third, "
        f"{j['count']['other']} someone else. {j['count']['open']} still have no name.",
        "A suggestion is logged the first time it is made for a load. The board is re-read every run, "
        "so today's line keeps moving until the day is over."
        + (f" Used so far: {used / decided:.0%}." if decided else ""))
    rows = []
    for s in scores:
        c, m = s["judge"]["count"], s["miles"]
        rows.append([f"<b>{s['day']:%a %m/%d}</b>", s["judge"]["n"], c["taken"], c["near"], c["other"], c["open"],
                     m["pairs"] or "—", f"{m['actual']:.0f}" if m["pairs"] else "—",
                     f"{m['actual'] - m['best']:.0f}" if m["pairs"] else "—",
                     ", ".join(f"{esc(x['customer'])} ({x['there']} of {x['wanted']})" for x in s["short"]) or "none"])
    body += "<h2>Day by day</h2>" + gd.table(
        ["Day", "Suggestions", "First pick used", "2nd/3rd used", "Someone else", "No name",
         "Backhauls paired", "Empty miles driven", "Miles over the best pairing", "Customers short of trailers"],
        rows, ["l"] + ["n"] * 8 + ["l"])
    body += gd.callout(
        "Empty miles are for backhauls the board shows as actually paired that day.",
        "\"Over the best pairing\" is how many of those miles a nearest-first pairing of the same trucks and "
        "loads would have saved. Miles are approximate (straight line x 1.25). \"Short of trailers\" is the "
        "most trailers GPS saw at the customer that day against its pool size.")
    tot = {k: sum(s["judge"]["closer"][k] for s in scores) for k in scores[0]["judge"]["closer"]}
    bk = {b: sum(s["judge"]["by_kind"]["Backhaul"][b] for s in scores) for b in BINS}
    measured = tot["office"] + tot["desk"] + tot["even"]
    body += "<h2>Backhauls — when the office sent someone else, who was closer?</h2>" + gd.callout(
        f"{sum(bk.values())} backhaul suggestions over {len(scores)} days: {bk['taken']} used, {bk['other']} went to "
        f"someone else, {bk['open']} still have no puller."
        + (f" Of the {measured} that can be measured both ways: the office's truck was closer on {tot['office']}, "
           f"the desk's on {tot['desk']}, about the same on {tot['even']} "
           f"({tot['office_mi']:.0f} empty miles the office's way against {tot['desk_mi']:.0f} the desk's)."
           if measured else ""),
        f"Both are measured the same way: from where that driver unloads on that day's tab to where the trailer "
        f"sits (straight line x 1.25). Within {SAME_MI} miles counts as the same. {tot['unmeasured']} could not be "
        "measured because the office's puller has no out-of-town delivery on that tab to measure from. "
        "The desk's figure is from its first suggestion for that load.")
    body += gd.table(
        ["Day", "Suggested", "Desk's pick used", "Someone else", "Office closer", "Desk closer", "About the same",
         "Cannot measure", "No puller yet"],
        [[f"<b>{s['day']:%a %m/%d}</b>", sum(s["judge"]["by_kind"]["Backhaul"].values()),
          s["judge"]["by_kind"]["Backhaul"]["taken"], s["judge"]["by_kind"]["Backhaul"]["other"],
          s["judge"]["closer"]["office"], s["judge"]["closer"]["desk"], s["judge"]["closer"]["even"],
          s["judge"]["closer"]["unmeasured"], s["judge"]["by_kind"]["Backhaul"]["open"]] for s in scores],
        ["l"] + ["n"] * 8)
    order = {"other": 0, "open": 1, "near": 2, "taken": 3, "gone": 4}

    def mi(v):
        return "—" if v is None else f"{v:.0f} mi"

    det = sorted(j["items"], key=lambda i: (order[i["result"]], i["kind"], i["load"]))
    body += f"<h2>{t['day']:%a %m/%d} — every suggestion</h2>" + gd.table(
        ["What", "Load", "Customer", "Where", "Desk suggested", "Desk's empty miles", "Board shows",
         "Office's empty miles", "Result"],
        [[i["kind"], f'<span class="mono">{esc(i["load"])}</span>', esc(i["customer"]), esc(i["where"]),
          f"<b>{esc(i['suggested'])}</b>", mi(i.get("desk_mi")) if i["kind"] == "Backhaul" else "",
          esc(i["board"]) or "—",
          mi(i.get("board_mi")) if i["kind"] == "Backhaul" and i["board"] else "",
          f'<span class="{"sug" if i["result"] in ("other", "open") else "ok"}">{WORD[i["result"]]}</span>']
         for i in det], ["l"] * 5 + ["n", "l", "n", "l"])
    return body
