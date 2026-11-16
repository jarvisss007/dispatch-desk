r"""DAY PLAN — every truck going out matched to a load coming back, and what
each customer still needs pulled.

The carrier runs hub-and-spoke: trucks leave the yard loaded for Arizona, Las Vegas
and the Central Valley, and the freight that feeds the mills sits in those
same places waiting to come home. The board marks the pairing by hand ("BH"
in the TRK column). This lays the whole day out so nothing rides home empty
and no ready trailer is left sitting:

  1. BALANCE   per region: trucks going in vs loads waiting to come out.
  2. MATCHES   each truck in a region -> the load it should bring back
               (what the board already paired is kept; the rest is suggested:
               READY first, then BH-marked, then oldest on the board).
  3. GAPS      loads with no truck to fetch them · trucks with nothing to haul home.
  4. CUSTOMERS per shipper: loads still to pull, how many are READY, trailers
               named, who is on them — against the trailer count the customer
               wants on site, once the office fills that in
               (customer_needs.json in the desk's state folder).

Recommend-only. It suggests a pairing; the dispatcher decides and writes the
board. Nobody is messaged.

    py -m dispatch_desk.plan               # today's tab
    py -m dispatch_desk.plan 2026-11-11

Writes DAY_PLAN.html (and most other pages) to the output folder.
"""
from __future__ import annotations

import html as H
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import board                      # noqa: E402
import page_design as gd          # noqa: E402
from dispatch_desk import actions, assign, empties, extras, geo, live, pools, roster, score, settings, source, track, trucks  # noqa: E402
from dispatch_desk.packets import _minutes, split_names  # noqa: E402
from dispatch_desk.tomorrow import C, daily, first_seen, read_tab, year_of  # noqa: E402

OUT = settings.OUT_DIR / "DESK"
NEEDS_PATH = settings.STATE_DIR / "customer_needs.json"
S = board.Stage

# (code, name, compiled pattern) — the towns of each region come from settings.REGIONS
REGIONS = [(code, name, re.compile(rx, re.I)) for code, name, rx in settings.REGIONS]
REGION_NAME = {k: n for k, n, _ in REGIONS}
# A shipper section name is the dispatcher's own label for where the freight is: a
# heading that names a town which also exists locally is pinned to its region.
_SECTION_HINTS = [(re.compile(rx, re.I), code) for rx, code in settings.SECTION_REGION_HINTS]


def region_of(text: str) -> str:
    for code, _, rx in REGIONS:
        if rx.search(text or ""):
            return code
    return "LOCAL"


def classify(loads: list[dict], seen: dict, day: date) -> list[dict]:
    out = []
    for n, l in enumerate(loads):
        if l["stage"] == S.CANCELED:
            continue
        c = l["cells"]
        if not re.search(r"\d", l["ref"]) and not c[C["pickup"]] and not c[C["delivery"]]:
            continue        # a sub-heading typed into a load row, not a load
        pick_text = c[C["pickup"]] + " " + l["section"]
        pr = region_of(pick_text)
        if pr == "LOCAL":
            pr = next((code for rx, code in _SECTION_HINTS if rx.search(l["section"])), pr)
        dr = region_of(c[C["delivery"]])
        cont = " ".join(x for row in l["cont"] for x in row).upper()
        fs = seen.get(l["ref"] or "")
        out.append({
            **l, "n": n, "pr": pr, "dr": dr,
            "pre": split_names(c[C["preloader"]], set()),
            "drv": split_names(c[C["driver"]], set()),
            "bh": bool(re.search(r"\bBH\b|BACK ?HAUL", c[C["trk"]], re.I)),
            "ready": "READY" in cont or "READY" in c[C["delivery_no"]].upper(),
            "trailer": (re.search(r"\b\d{4,7}\b", c[C["note"]]) or [""])[0] if c[C["note"]] else "",
            "days": (day - fs).days if fs else 0,
            "ppos": geo.locate(c[C["pickup"]], l["section"]) if pr != "LOCAL" else None,
            "dpos": geo.locate(c[C["delivery"]]) if dr != "LOCAL" else None,
        })
    return out


UNKNOWN_MI = 60.0       # a stop we cannot place: never the nearest, never ruled out
NOT_READY_MI = 1000.0   # a trailer that is not READY is only for a truck with nothing else


def deadhead(truck_row: dict, load_row: dict) -> float | None:
    """Empty miles from where the truck unloads to where the backhaul sits."""
    if truck_row.get("dpos") and load_row.get("ppos"):
        return geo.miles(truck_row["dpos"], load_row["ppos"])
    return None


def puller_miles(load_row: dict, rows: list[dict], same=None) -> float | None:
    """Empty miles for the puller the BOARD shows on a backhaul: from where he
    unloads in that region on the same tab to where the trailer sits. None when
    he has no out-of-town delivery there on this tab (nothing to measure from)."""
    same = same or (lambda w: w)
    people = {same(w) for w in load_row["pre"]}
    for r in rows:
        if r["dr"] == load_row["pr"] and r["pr"] != r["dr"] and r.get("dpos") \
                and any(same(w) in people for w in r["drv"]):
            return deadhead(r, load_row)
    return None


def nearest_pairs(trucks: list[dict], loads: list[dict]) -> list[tuple[int, int]]:
    """Fewest total empty miles across ALL trucks at once (not nearest-first:
    giving truck A its nearest load can cost truck B more than it saves).
    READY trailers go first; a not-ready one is taken only when a truck would
    otherwise ride home empty."""
    if not trucks or not loads:
        return []
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    cost = np.zeros((len(trucks), len(loads)))
    for i, t in enumerate(trucks):
        for j, l in enumerate(loads):
            d = deadhead(t["row"], l)
            cost[i, j] = (UNKNOWN_MI if d is None else d) + (0 if l["ready"] else NOT_READY_MI) \
                - min(l["days"], 10) * 0.01          # tie: the older load
    ri, ci = linear_sum_assignment(cost)
    return list(zip(ri.tolist(), ci.tolist()))


def history(days: list[list[dict]]) -> dict:
    """How the office really paired backhauls: for every driver who delivered
    out of town and pulled a load from the same region on the same tab, the
    empty miles he drove — against the best possible re-pairing of the same
    trucks and the same loads that day."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    out = {code: {"pairs": 0, "actual": 0.0, "best": 0.0} for code, _, _ in REGIONS}
    for rows in days:
        for code, _, _ in REGIONS:
            delivered = {}
            for r in rows:
                if r["dr"] == code and r["pr"] != code and r["drv"] and r["dpos"]:
                    delivered.setdefault(r["drv"][0], r)
            pulls, seen = [], set()
            for r in rows:
                if r["pr"] == code and r["dr"] != code and r["pre"] and r["ppos"]:
                    w = r["pre"][0]
                    if w in delivered and w not in seen:
                        seen.add(w)
                        pulls.append((w, r))
            if not pulls:
                continue
            m = np.array([[geo.miles(delivered[w]["dpos"], l["ppos"]) for _, l in pulls]
                          for w, _ in pulls])
            ri, ci = linear_sum_assignment(m)
            out[code]["pairs"] += len(pulls)
            out[code]["actual"] += float(np.trace(m))
            out[code]["best"] += float(m[ri, ci].sum())
    return out


_AUTH_TOKEN = {"AZ": r"\bAZ\b", "NV": r"\bNV\b", "CV": r"\bN\b|\bCA\b"}


def load_authority() -> dict[str, str]:
    """The older hand-kept roster: who may run where ({} when there is none)."""
    try:
        import own_backhaul
        return own_backhaul.authority_table()
    except Exception:
        return {}


def authority(who: str, code: str, table: dict[str, str]) -> tuple[str, str]:
    """('ok' | 'no' | 'unknown', the roster's own words). A board name is
    matched to the roster by full name or as a whole word of one ('HOLLIS' ->
    'PETER JAMES HOLLIS'). Two roster people with different authority under
    one board name = unknown, never a guess."""
    found = {v for k, v in table.items() if k == who}
    if not found:
        found = {v for k, v in table.items() if " " in k and who in k.split()}
    old = next(iter(found)).upper() if len(found) == 1 else ""
    people = getattr(table, "people", None)
    trips = getattr(table, "proven", {}).get(who, {}).get(code, 0)
    if people:
        # The board's own DRIVERS tab is the live roster and outranks the older file.
        work, hit = roster.workload(who, people)
        if work and work != roster.OUT_OF_TOWN:
            return "no", f"{work} on the DRIVERS tab"
        if work:
            if old.startswith("OTR"):                    # the older roster names his regions
                return ("ok" if re.search(_AUTH_TOKEN[code], old) else "no"), old + roster.paper_note(hit)
            if trips >= assign.MIN_PROVEN:
                return "ok", (f"OTR on the DRIVERS tab · {trips} {REGION_NAME[code]} trips on the board"
                              + roster.paper_note(hit))
            return "unknown", f"OTR on the DRIVERS tab, no {REGION_NAME[code]} trips on the board yet"
        if hit:
            return "unknown", "name fits more than one driver on the DRIVERS tab"
    if not old:
        if trips >= assign.MIN_PROVEN:
            return "ok", f"not on the roster · {trips} {REGION_NAME[code]} trips on the board"
        return "unknown", "not on the authority roster" if not found else "name matches more than one driver"
    if re.search(_AUTH_TOKEN[code], old):
        return "ok", old
    return "no", old


class Roster(dict):
    """The older authority table, carrying the DRIVERS tab and the trips the
    board has already seen. A plain dict still works everywhere (tests)."""
    people: list = []
    proven: dict = {}

    def same(self, who: str) -> str:
        """The person behind a board spelling (the spelling itself if unclear)."""
        hit = roster.match(who, self.people)
        return hit[0]["name"] if len(hit) == 1 else who


# Still at the shipper: nobody has pulled it yet.
NOT_PULLED = (S.UNKNOWN, S.DISPATCHED, S.TO_SHIPPER, S.AT_SHIPPER)


def plan(rows: list[dict], table: dict[str, str] | None = None) -> dict:
    table = table or {}
    regions = {}
    for code, name, _ in REGIONS:
        # trucks: a delivery INTO the region with a name in the DRIVER column.
        # The PRELOADER only brought that load to the yard — he is not going
        # (the first version "sent" a local-only preloader to Arizona).
        trucks, unstaffed_out = [], []
        for r in rows:
            if r["dr"] != code or r["pr"] == code:
                continue
            if not r["drv"]:
                if r["stage"] != S.DELIVERED:
                    unstaffed_out.append(r)
                continue
            who = r["drv"][0]
            a, words = authority(who, code, table)
            trucks.append({"who": who, "row": r, "auth": a, "auth_text": words,
                           "leave": _minutes(r["cells"][C["trk"]])})
        # loads: everything waiting in the region that has not been pulled
        waiting = [r for r in rows if r["pr"] == code and r["dr"] != code
                   and r["stage"] in NOT_PULLED]
        taken: dict[int, str] = {}          # load n -> who, already on the board
        for r in waiting:
            if r["pre"]:
                taken[r["n"]] = r["pre"][0]
        matches, used_trucks = [], set()
        # 1) keep what the board already says
        for t in trucks:
            mine = [r for r in waiting if t["who"] in r["pre"]]
            if mine:
                used_trucks.add(id(t))
                matches.append({"truck": t, "loads": mine, "how": "on the board"})
        # 2) suggest the rest by NEAREST backhaul: least total empty miles
        free_loads = [r for r in waiting if r["n"] not in taken]
        free_trucks = sorted((t for t in trucks if id(t) not in used_trucks),
                             key=lambda t: (t["leave"] is None, t["leave"] or 0))
        seen_people = {m["truck"]["who"] for m in matches}
        empty_home, not_allowed, eligible = [], [], []
        for t in free_trucks:
            if t["who"] in seen_people:
                continue                    # second delivery by someone already paired
            seen_people.add(t["who"])
            if t["auth"] == "no":
                not_allowed.append(t)       # roster says he may not run here: never suggest
                continue
            eligible.append(t)
        pairs = nearest_pairs(eligible, free_loads)
        got = {i: j for i, j in pairs}
        for i, t in enumerate(eligible):
            if i in got:
                matches.append({"truck": t, "loads": [free_loads[got[i]]], "how": "suggested"})
            else:
                empty_home.append(t)
        free_loads = [l for j, l in enumerate(free_loads) if j not in got.values()]
        free_loads.sort(key=lambda r: (not r["ready"], -r["days"], r["n"]))
        for m in matches:                   # miles on every pairing, the board's too
            m["empty_mi"] = deadhead(m["truck"]["row"], m["loads"][0])
            m["home_mi"] = geo.miles(m["loads"][0]["ppos"], geo.YARD) if m["loads"][0].get("ppos") else None
            if m["how"] != "suggested":
                near = [(deadhead(m["truck"]["row"], l), l) for l in free_loads if l["ready"]]
                near = [(d, l) for d, l in near if d is not None]
                if near and m["empty_mi"] is not None:
                    d, l = min(near, key=lambda x: x[0])
                    if d < m["empty_mi"] - 10:
                        m["nearer"] = (d, l)
        other_puller = [r for r in waiting if r["n"] in taken
                        and not any(r in m["loads"] for m in matches)]
        regions[code] = {"name": name, "trucks": trucks, "waiting": waiting,
                         "matches": matches, "no_truck": free_loads,
                         "empty_home": empty_home, "unstaffed_out": unstaffed_out, "not_allowed": not_allowed,
                         "other_puller": other_puller}
    return regions


def fill_drivers(regions: dict, rows: list[dict], yesterday: list[dict],
                 past: list[list[dict]], table: dict) -> None:
    """Suggest a driver for every out-of-town load that has none, then give
    each of those new trucks the nearest backhaul still without a truck."""
    hist = assign.lane_history(past, C)
    auth_fn = lambda w, c: authority(w, c, table)   # noqa: E731
    used: set = set()
    for code, r in regions.items():
        sug = assign.suggest(code, r["unstaffed_out"], rows, yesterday, hist, auth_fn, C, used,
                             getattr(table, "same", None))
        used |= {x["who"] for x in sug if x["who"]}
        staffed = [x for x in sug if x["who"]]
        trucks = [{"who": x["who"], "row": x["row"]} for x in staffed]
        pairs = dict(nearest_pairs(trucks, r["no_truck"]))
        for i, x in enumerate(staffed):
            l = r["no_truck"][pairs[i]] if i in pairs else None
            x["bh"] = l
            x["empty_mi"] = deadhead(x["row"], l) if l else None
        r["driver_sug"] = sug
        r["still_no_truck"] = [l for j, l in enumerate(r["no_truck"]) if j not in pairs.values()]
        for m in r["matches"]:              # a "nearer load" tip must not reuse a load just given away
            if not m.get("nearer"):
                continue
            m.pop("nearer")
            near = [(deadhead(m["truck"]["row"], l), l) for l in r["still_no_truck"] if l["ready"]]
            near = [(d, l) for d, l in near if d is not None]
            if near and m.get("empty_mi") is not None:
                d, l = min(near, key=lambda x: x[0])
                if d < m["empty_mi"] - 10:
                    m["nearer"] = (d, l)


def customers(rows: list[dict], needs: dict) -> list[dict]:
    by = defaultdict(list)
    for r in rows:
        if r["stage"] != S.DELIVERED:
            by[r["section"]].append(r)
    out = []
    for sec, rs in by.items():
        to_pull = [r for r in rs if r["stage"] in NOT_PULLED]
        out.append({
            "section": sec, "region": rs[0]["pr"], "open": len(rs),
            "to_pull": len(to_pull),
            "ready": sum(r["ready"] for r in to_pull),
            "no_one": sum(1 for r in to_pull if not r["pre"] and not r["drv"]),
            "at_yard": sum(1 for r in rs if r["stage"] in (S.AT_YARD, S.TO_YARD)),
            "trailers": sum(1 for r in to_pull if r["trailer"]),
            "oldest": max((r["days"] for r in rs), default=0),
            "want": needs.get(sec.strip().upper(), {}).get("trailers_on_site"),
        })
    out.sort(key=lambda c: (-c["to_pull"], c["section"]))
    return out


def load_needs() -> dict:
    try:
        raw = json.loads(NEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.strip().upper(): v for k, v in raw.get("customers", {}).items()}


def seed_needs(sections: list[str]) -> None:
    """First run: write the customer list with blanks for the office to fill.
    Never overwrites numbers already entered; only adds new customers."""
    try:
        raw = json.loads(NEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {"_how_to": "For each customer, set trailers_on_site to the number of our "
                          "trailers they want sitting at their dock (null = not set). "
                          "notes is free text.", "customers": {}}
    changed = False
    for s in sorted({x.strip().upper() for x in sections if x.strip()}):
        if s not in raw["customers"]:
            raw["customers"][s] = {"trailers_on_site": None, "notes": ""}
            changed = True
    if changed:
        NEEDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        NEEDS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- page
_CSS = """
h2{margin:40px 0 8px;font-size:20px}
td small{display:block;opacity:.65}
td.sug{color:var(--accent,#f5a524)} td.ok{opacity:.75}
.pill{display:inline-block;border:1px solid currentColor;border-radius:3px;padding:0 6px;font-size:11px;margin-left:6px;opacity:.8}
.step{margin:40px 0 8px;font-size:22px;display:flex;align-items:baseline;gap:12px}
.step .n{font-family:ui-monospace,monospace;font-size:14px;border:1px solid currentColor;border-radius:50%;width:28px;height:28px;display:inline-flex;align-items:center;justify-content:center;flex:none}
.step .c{font-size:14px;opacity:.6;font-weight:400}
.item{padding:12px 16px;margin:8px 0;border-left:4px solid #f5a524;background:#161a21;border-radius:0 4px 4px 0}
.item.bad{border-left-color:#e5484d}
.item.fine{border-left-color:#3fb950}
.item .do{font-size:17px;line-height:1.4}
.item .do b{letter-spacing:.02em}
.item .do .arrow{opacity:.5;margin:0 8px}
.item .why{font-size:13px;opacity:.72;margin-top:4px}
.item .chk{font-size:12px;color:#f5a524;margin-top:4px}
.none{opacity:.6;font-size:14px;margin:8px 0}
details.all{margin-top:56px;border-top:1px solid #2a2f3a;padding-top:16px}
details.all>summary{cursor:pointer;font-size:15px;opacity:.8;padding:8px 0}
@media print{.item{background:none;border:1px solid #999;border-left-width:4px}details.all{display:none}}
"""


def _load_cell(r: dict) -> str:
    tags = "".join(f'<span class="pill">{t}</span>' for t, on in
                   (("READY", r["ready"]), ("BH", r["bh"])) if on)
    days = f" · {r['days']}d on board" if r["days"] >= 2 else ""
    trl = f" · trailer {H.escape(r['trailer'])}" if r["trailer"] else ""
    return (f'<span class="mono">{H.escape(r["ref"] or "—")}</span>{tags}'
            f"<small>{H.escape(r['section'])}{trl}{days}</small>")


def _mi(v) -> str:
    return "—" if v is None else f"{v:.0f}"


def history_html(h: dict, n_days: int) -> str:
    pairs = sum(v["pairs"] for v in h.values())
    if not pairs:
        return ""
    actual = sum(v["actual"] for v in h.values())
    best = sum(v["best"] for v in h.values())
    rows = [[REGION_NAME[k], v["pairs"], f"{v['actual']:.0f}", f"{v['best']:.0f}",
             f"{v['actual'] - v['best']:.0f}",
             f"{v['actual'] / v['pairs']:.0f}" if v["pairs"] else "—"] for k, v in h.items()]
    return ("<h2>How backhauls were really paired — last "
            f"{n_days} days of tabs</h2>"
            + gd.callout(
                f"{pairs} backhauls: {actual:,.0f} empty miles driven to reach them. Pairing each truck "
                f"with its nearest load would have been {best:,.0f} — {actual - best:,.0f} miles "
                f"({(actual - best) / actual:.0%}) saved.",
                "Same trucks and same loads as the office used, only re-paired. Miles are approximate "
                "(straight line x 1.25). Counts a pairing only when the same name delivered into a region "
                "and pulled a load out of it on one tab.")
            + gd.table(["Region", "Backhauls", "Empty miles driven", "Best possible", "Miles left on the table",
                        "Empty miles per backhaul"], rows, ["l"] + ["n"] * 5))


def week_html(ahead: list[dict], can_run: dict) -> str:
    gaps = [(a["day"], REGION_NAME[c], v) for a in ahead for c, v in a["regions"].items() if v["gap"]]
    if gaps:
        say = "Short of drivers: " + "; ".join(
            f"{d:%a %m/%d} {name} needs {max(v['booked'], v['normal'])}, {v['can_leave']} can leave"
            for d, name, v in gaps[:6]) + ("; and more below." if len(gaps) > 6 else ".")
    else:
        say = "No day in the next six needs more drivers than can leave."
    body = gd.callout(say, "\"Can leave\" is the drivers the roster lets the desk send to that region, halved for "
                           "Arizona and Central Valley because a driver who left yesterday is still coming back. "
                           "It does not know about days off.")
    for c, name, _ in REGIONS:
        rows = []
        for a in ahead:
            v = a["regions"][c]
            need = max(v["booked"], v["normal"])
            rows.append([f"<b>{a['day']:%a %m/%d}</b>", v["booked"], v["normal"], v["can_leave"],
                         f'<span class="sug">SHORT {v["gap"]} — line up {v["gap"]} more or broker it out</span>'
                         if v["gap"] else f'<span class="ok">covered ({v["can_leave"] - need} spare)</span>'])
        body += f"<h2>{name} — {can_run.get(c, 0)} drivers can run it</h2>" + gd.table(
            ["Day", "Loads already on the board", "A normal same weekday", "Drivers who can leave", "Verdict"],
            rows, ["l", "n", "n", "n", "l"])
    return body


def weekly_html(weeks: list[dict]) -> str:
    if not weeks:
        return ""
    rows = [[f"{w['week']:%m/%d}", w["pairs"], f"{w['actual']:.0f}", f"{w['best']:.0f}",
             f"{w['actual'] - w['best']:.0f}", f"{(w['actual'] - w['best']) / w['actual']:.0%}" if w["actual"] else "—"]
            for w in weeks]
    return ("<h2>Empty miles to reach backhauls, week by week</h2>"
            + gd.table(["Week of", "Backhauls", "Empty miles driven", "Best possible", "Left on the table", "Share"],
                       rows, ["l"] + ["n"] * 5))


def backtest_html(bt: dict, kind: str = "out-of-town") -> str:
    if not bt.get("n"):
        return ""
    return gd.callout(
        f"How good are the driver suggestions? On {bt['n']} past {kind} loads, the driver the office "
        f"really sent was the first pick {bt['top1'] / bt['n']:.0%} of the time and in the top three "
        f"{bt['top3'] / bt['n']:.0%}.",
        f"Picking at random from the {bt['pool'] / bt['n']:.0f} drivers available would score "
        f"{bt['n'] / bt['pool']:.0%} and {3 * bt['n'] / bt['pool']:.0%}. Treat the suggestion as a short list, "
        "not an order: it cannot see hours-of-service, sick calls or a truck in the shop.")


def _truck_cell(t: dict) -> str:
    r = t["row"]
    place = r["cells"][C["delivery"]].split("\n")[0]
    return (f"<b>{H.escape(t['who'])}</b>"
            f"<small>{H.escape(r['cells'][C['trk']] or 'no leave time')} · delivering "
            f"{H.escape(r['ref'])} to {H.escape(place)[:40]} · {H.escape(r['cells'][C['status']] or 'no status')}</small>")


def pools_html(pc: dict) -> str:
    if not pc["ok"]:
        return gd.callout("Trailer pools could not be counted this run.", pc["why"])
    rows = []
    usual = pools.usual()
    order = sorted(pc["sites"], key=lambda s: (s["short"] is None, -(s["short"] or 0), -s["on_site"]))
    for s in order:
        if s["short"] is None:
            u = usual.get(s["section"])
            verdict = ('<span class="ok">pool size not set</span>' if not u else
                       f'<span class="{"sug" if s["on_site"] <= u["median"] - 2 else "ok"}">usually {u["median"]} there '
                       f'({u["low"]}–{u["high"]} over {u["days"]} days)</span>')
        elif s["short"] > 0:
            verdict = f'<span class="sug">SHORT {s["short"]} — send {s["short"]} empt{"y" if s["short"] == 1 else "ies"}</span>'
        elif s.get("derived"):
            verdict = '<span class="ok">at its usual level</span>'
        else:
            verdict = '<span class="ok">pool is full</span>'
        loaded = ", ".join(f"{u} ({ref})" for u, ref in s["loaded"]) or "—"
        rows.append([f"<b>{H.escape(s['section'])}</b><small>{H.escape(s['source'])}</small>",
                     "not set" if s["want"] is None else s["want"], s["on_site"], verdict,
                     f'<span class="mono">{H.escape(loaded)}</span>',
                     f'<span class="mono">{H.escape(", ".join(s["other"]) or "—")}</span>'])
    return gd.table(["Customer", "Pool wanted", "Our trailers there now", "Verdict",
                     "Loaded, waiting to be pulled (load #)", "There, not on today's board"],
                    rows, ["l", "n", "n", "l", "l", "l"]) + gd.callout(
        f"Counted from live GPS: {pc['tracked']} trailers have a tracker.",
        "A trailer without a tracker is not counted, so treat each number as a floor. "
        "\"Not on today's board\" means GPS sees it there and no open load names it — it may be "
        "empty or loaded; only the site can say.")


def build_page(day: date, tab: str, regions: dict, custs: list[dict], info: dict,
               needs_set: int, pc: dict, hist_html: str = "", work_html: str = "") -> str:
    tot = lambda k: sum(len(r[k]) for r in regions.values())  # noqa: E731
    sug = sum(1 for r in regions.values() for m in r["matches"] if m["how"] == "suggested")
    body = ""
    if info["stale"]:
        body += gd.callout("The board has not been saved in over 12 hours.", source.age_text(info))
    body += gd.tiles([
        (tot("trucks"), "trucks going out of town today"),
        (tot("waiting"), "loads waiting to come back"),
        (sug, "new pairings suggested", "good" if sug else ""),
        (tot("no_truck"), "loads with no truck to fetch them", "hot" if tot("no_truck") else ""),
        (tot("empty_home"), "trucks coming home empty", "hot" if tot("empty_home") else ""),
    ])
    short = [x for x in pc.get("sites", []) if (x["short"] or 0) > 0]
    body += "<h2>Trailer pools — who is short right now</h2>"
    if short:
        body += gd.callout(
            "Short right now: " + " · ".join(
                f"{x['section']} has {x['on_site']} of {x['want']}" for x in short),
            "Each empty sent to these sites today closes a written complaint.")
    body += pools_html(pc)
    bal = [[H.escape(r["name"]), len(r["trucks"]), len(r["unstaffed_out"]), len(r["waiting"]),
            sum(x["ready"] for x in r["waiting"]),
            sum(len(m["loads"]) for m in r["matches"]) + len(r["other_puller"]),
            len(r["no_truck"]), len(r["empty_home"])] for r in regions.values()]
    body += "<h2>Balance by region</h2>" + gd.table(
        ["Region", "Trucks going in", "Outbound loads with no driver", "Loads waiting there",
         "of which READY", "Covered", "No truck", "Trucks home empty"],
        bal, ["l"] + ["n"] * 7)
    for code, r in regions.items():
        if not (r["trucks"] or r["waiting"]):
            continue
        body += f"<h2>{H.escape(r['name'])} — who brings what back</h2>"
        rows = []
        for m in sorted(r["matches"], key=lambda m: m["how"] != "suggested"):
            cls = "sug" if m["how"] == "suggested" else "ok"
            t = m["truck"]
            say = "already on the board"
            if cls == "sug":
                say = ("SUGGESTED — roster: " + t["auth_text"] if t["auth"] == "ok"
                       else "SUGGESTED — but " + t["auth_text"] + ", confirm he may run here")
            if m.get("nearer"):
                d, l = m["nearer"]
                say += f" — a READY load is nearer: {l['ref']} at {l['section']}, {d:.0f} mi"
                cls = "sug"
            rows.append([_truck_cell(t), "<br>".join(_load_cell(x) for x in m["loads"]),
                         _mi(m.get("empty_mi")), _mi(m.get("home_mi")),
                         f'<span class="{cls}">{H.escape(say)}</span>'])
        for t in r["not_allowed"]:
            rows.append([_truck_cell(t), "—", "—", "—",
                         f'<span class="sug">NOT SUGGESTED — roster says {H.escape(t["auth_text"])}. '
                         "Check why he is on this load.</span>"])
        for t in r["empty_home"]:
            rows.append([_truck_cell(t), "—", "—", "—", '<span class="sug">COMES HOME EMPTY — nothing left to pull here</span>'])
        for x in r["no_truck"]:
            rows.append(["—", _load_cell(x), "—", _mi(geo.miles(x["ppos"], geo.YARD) if x.get("ppos") else None),
                         '<span class="sug">NO TRUCK — nobody going there is free</span>'])
        for x in r["other_puller"]:
            rows.append([f"<b>{H.escape(x['pre'][0])}</b><small>named on the board, not delivering here today</small>",
                         _load_cell(x), "—", _mi(geo.miles(x["ppos"], geo.YARD) if x.get("ppos") else None),
                         '<span class="ok">already on the board</span>'])
        body += gd.table(["Truck going in", "Load to bring back", "Empty miles to the pickup",
                          "Loaded miles back to the yard", "Status of the pairing"], rows,
                         ["l", "l", "n", "n", "l"])
        if r.get("driver_sug"):
            body += f"<h2>{H.escape(r['name'])} — loads going out with no driver: who should take them</h2>"
            srows = []
            for x in r["driver_sug"]:
                row = x["row"]
                place = row["cells"][C["delivery"]].splitlines()[0] if row["cells"][C["delivery"]] else ""
                appt = " · ".join(c2[C["delivery"]] for c2 in row["cont"] if c2[C["delivery"]])
                who = (f"<b>{H.escape(x['who'])}</b><small>{H.escape(x['why'])}</small>" if x["who"]
                       else f'<span class="sug">NOBODY — {H.escape(x["why"])}</span>')
                alts = ", ".join(x["alts"]) or "—"
                bh = (_load_cell(x["bh"]) + f"<small>{_mi(x['empty_mi'])} empty miles to it</small>"
                      if x.get("bh") else "—")
                srows.append([f'<span class="mono">{H.escape(row["ref"])}</span><small>{H.escape(row["section"])}</small>',
                              f"{H.escape(place)[:44]}<small>{H.escape(appt) or 'no appointment on the board'}</small>",
                              who, H.escape(alts), bh])
            body += gd.table(["Load", "Deliver to", "Suggested driver", "Next best", "Backhaul he then brings home"], srows)
            left = r.get("still_no_truck", [])
            if left:
                body += gd.callout(
                    f"Even with those drivers, {len(left)} {r['name']} load(s) still have no truck: "
                    + ", ".join(f"{l['ref']} ({l['section']})" for l in left),
                    "These need a truck sent from the yard with an empty, or another outbound load booked into the region.")
        elif r["unstaffed_out"]:
            body += gd.callout(
                f"{len(r['unstaffed_out'])} outbound load(s) to {r['name']} have no delivery driver yet: "
                + ", ".join(x["ref"] for x in r["unstaffed_out"]),
                "Each one is another truck that could bring a load back once it is assigned.")
    body += hist_html
    body += "<h2>What each customer still needs</h2>"
    crow = [[H.escape(c["section"]), REGION_NAME.get(c["region"], "Local"), c["to_pull"], c["ready"],
             c["no_one"], c["trailers"], c["at_yard"], c["oldest"],
             "not set" if c["want"] is None else c["want"]] for c in custs]
    body += gd.table(["Customer", "Where", "Loads still to pull", "READY now", "Nobody on it",
                      "Trailers named", "At our yard to deliver", "Oldest (days)", "Trailers they want on site"],
                     crow, ["l", "l"] + ["n"] * 7)
    body += gd.callout(
        f"Trailers each customer wants on site: {needs_set} of {len(custs)} filled in.",
        "The board does not say how many trailers a customer wants at their dock, so this page cannot "
        "plan empties until the office enters the numbers in customer_needs.json "
        "(in the desk's state folder).")
    return gd.page(
        title=f"Day plan — {day:%A} {day.month}/{day.day}",
        eyebrow=settings.EYEBROW,
        sub="What to do now, in order. Suggestions only — the dispatcher decides.",
        source=f"live board tab {tab} · {source.age_text(info)}",
        body=(gd.callout("The board has not been saved in over 12 hours.", source.age_text(info))
              if info["stale"] else "")
        + work_html
        + '<details class="all"><summary>Show all the detail (every truck, every load, every customer)</summary>'
        + body + "</details>", extra_css=_CSS + actions.CSS, refresh=300)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wb, info = source.open_live()
    tabs = daily(wb, year_of(info))
    day = date.fromisoformat(args[0]) if args else max(
        (d for _, d in tabs if d <= date.today()), default=tabs[-1][1])
    tab = next((n for n, d in tabs if d == day), None)
    if tab is None:
        print(f"[desk] the board has no tab for {day:%m/%d} yet")
        return 1
    hist = [(n, d, read_tab(wb[n])) for n, d in tabs if d <= day][-45:]
    seen = first_seen(hist)
    rows = classify(hist[-1][2], seen, day)
    seed_needs([r["section"] for r in rows])
    needs = load_needs()
    past = [classify(loads, {}, d) for _, d, loads in hist[:-1]]
    table = Roster(load_authority())
    table.people = roster.read(wb)
    table.proven = roster.Trips(assign.lane_history(past, C)["region"], table.people)
    regions = plan(rows, table)
    fill_drivers(regions, rows, past[-1] if past else [], past, table)
    bt = assign.backtest(past + [rows], lambda w, c: authority(w, c, table), C)
    dated = [(d, rws) for (_, d, _), rws in zip(hist[:-1], past)]
    taken = {x["who"] for r in regions.values() for x in r.get("driver_sug", []) if x["who"]}
    local_open = [r for r in rows if r["pr"] == "LOCAL" and r["dr"] == "LOCAL" and not r["pre"]
                  and not r["drv"] and r["stage"] != S.DELIVERED]
    extra = {
        "onsite": live.watch(rows, C),
        "swaps": extras.swap_clock(dated, rows, day, NOT_PULLED),
        "local": assign.suggest_local(local_open, rows, assign.section_history(past), taken),
        "bt_local": assign.backtest_local(past + [rows]),
        "offers": extras.broker_offers(),
        "weekly": extras.weekly_miles(dated + [(day, rows)], history),
    }
    custs = customers(rows, needs)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "DAY_PLAN.html"
    loaded = {r["trailer"]: r["ref"] for r in rows if r["trailer"] and r["stage"] in NOT_PULLED}
    pc = pools.count([r["section"] for r in rows], loaded, needs)
    pools.remember(day, pc)
    pc_derived = {x["section"]: x.get("derived") for x in pc.get("sites", [])}
    names = defaultdict(int)
    for rws in past[-30:] + [rows]:
        for r in rws:
            for w in set(r["pre"]) | set(r["drv"]):
                names[w] += 1
    from collections import Counter
    (OUT / "ROSTER.html").write_text(gd.page(
        title="Roster — who can go where", eyebrow=settings.EYEBROW,
        sub="Read from the DRIVERS tab of the live board, checked against the trips the board already shows. "
            "To change what the desk does, change WORKLOAD on that tab.",
        source=f"board DRIVERS tab + last {len(past)} daily tabs · {source.age_text(info)}",
        body=roster.page_body(table.people, Counter(names), table.proven,
                              lambda w, c: authority(w, c, table), REGION_NAME, date.today(), gd),
        extra_css=_CSS, refresh=900), encoding="utf-8")
    h = history(past + [rows])
    out.write_text(build_page(day, tab.strip(), regions, custs, info,
                              sum(1 for c in custs if c["want"] is not None), pc,
                              history_html(h, len(hist)) + weekly_html(extra["weekly"])
                              + backtest_html(bt) + backtest_html(extra["bt_local"], "local"),
                              actions.render(sys.modules[__name__], day, regions, rows, pc, pools.inventory(),
                                             table, assign.lane_history(past, C), extra)), encoding="utf-8")
    if bt["n"]:
        print(f"[desk] driver suggestions back-test: {bt['n']} past loads · the driver really sent was our "
              f"first pick {bt['top1'] / bt['n']:.0%}, in our top three {bt['top3'] / bt['n']:.0%} "
              f"(chance: {bt['n'] / bt['pool']:.0%} / {3 * bt['n'] / bt['pool']:.0%})")
    for code, r in regions.items():
        for x in r.get("driver_sug", []):
            print(f"         {REGION_NAME[code]:10} {x['row']['ref']:12} -> {x['who'] or 'NOBODY':12} "
                  f"then BH {x['bh']['ref'] + ' ' + x['bh']['section'] if x.get('bh') else '-'} | {x['why'][:70]}")
    # ---- week ahead: loads already booked vs a normal weekday vs drivers who can leave
    codes = [c for c, _, _ in REGIONS]
    people_seen = {table.same(w) for w in names}
    can_run = {c: len({table.same(w) for w in names if names[w] >= 3 and authority(w, c, table)[0] == "ok"})
               for c in codes}
    future = [(d, classify(read_tab(wb[n]), {}, d)) for n, d in tabs if d > day]
    try:
        up_ws = next(wb[n] for n in wb.sheetnames if n.strip().upper() == "UPCOMING LOADS")
        upcoming = classify(read_tab(up_ws), {}, day)
    except StopIteration:
        upcoming = []
    ahead = extras.week_ahead(dated + [(day, rows)], future, upcoming, can_run, day, C["pickup"], codes)
    (OUT / "WEEK_AHEAD.html").write_text(gd.page(
        title="Week ahead — loads against drivers", eyebrow=settings.EYEBROW,
        sub="For each of the next six days: out-of-town loads already on the board, what a normal same weekday "
            "looks like, and how many drivers can leave that day.",
        source=f"future daily tabs + UPCOMING LOADS tab + last {len(past)} daily tabs + DRIVERS tab · {source.age_text(info)}",
        body=week_html(ahead, can_run), extra_css=_CSS, refresh=900), encoding="utf-8")

    # ---- tracking: every open load against its appointment, from live GPS
    tracking = track.follow(rows, C, table.same)
    (OUT / "TRACKING.html").write_text(gd.page(
        title="Tracking — will each load make its time", eyebrow=settings.EYEBROW,
        sub="Every open load on today's tab with a name on it, where its truck is now, and whether it "
            "makes the appointment.",
        source=f"live GPS + live board · {source.age_text(info)}",
        body=track.page_body(tracking, gd, H.escape), extra_css=_CSS, refresh=300), encoding="utf-8")

    trucks.build(past[-30:] + [rows], C, table.same, OUT / "TRUCKS.html", gd,
                 f"equipment list + board TRK cells, last 30 tabs + live GPS · {source.age_text(info)}")

    # ---- empty trips home: every tab on the workbook, not only the last 45
    older = [(d, classify(read_tab(wb[n]), {}, d)) for n, d in tabs if d < hist[0][1]]
    every = older + dated + [(day, rows)]
    empty = empties.analyze(every, codes, C["delivery"], table.same, NOT_PULLED, day)
    loose = empties.total(empties.analyze(every, codes, C["delivery"], table.same, NOT_PULLED, day, 3)["trips"])
    fuel = empties.fuel_rates()
    (OUT / "EMPTY_RETURNS.html").write_text(gd.page(
        title="Empty trips home", eyebrow=settings.EYEBROW,
        sub="Every truck that delivered out of town and pulled nothing back: how many, where from, and whether "
            "a trailer was sitting there.",
        source=f"every daily tab on the live board, {len(every)} tabs · {source.age_text(info)}"
               + (f" · fuel figures file, price to {fuel['ppg_as_of']}" if fuel
                  else " · no fuel figures: the fuel figures file could not be read"),
        body=empties.page_body(empty, REGION_NAME, gd, H.escape, loose, fuel=fuel), extra_css=_CSS, refresh=900),
        encoding="utf-8")

    # ---- the score: what the desk suggested against what the board now shows
    score.record(day, regions, extra["local"], REGION_NAME)
    tab_of = {d: n for n, d in tabs}
    wants = {x["section"]: x["want"] for x in pc.get("sites", []) if x["want"] is not None}
    try:
        counted = json.loads(pools.HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        counted = {}
    scores = []
    for d in sorted(score.days_logged(), reverse=True)[:14]:
        if d not in tab_of:
            continue
        d_rows = rows if d == day else classify(read_tab(wb[tab_of[d]]), {}, d)
        hh = history([d_rows])
        seen_there = counted.get(d.isoformat(), {})
        scores.append({
            "day": d, "judge": score.judge(score.load(d), d_rows, table.same,
                                           lambda r, rws: puller_miles(r, rws, table.same)),
            "miles": {k: sum(v[k] for v in hh.values()) for k in ("pairs", "actual", "best")},
            "short": [{"customer": s, "there": seen_there[s], "wanted": w} for s, w in sorted(wants.items())
                      if s in seen_there and seen_there[s] < w and not (pc_derived.get(s) and w - seen_there[s] < 2)],
        })
    (OUT / "SCORE.html").write_text(gd.page(
        title="Score — suggestions against the board", eyebrow=settings.EYEBROW,
        sub="What the desk suggested, what the office did, and what it cost in empty miles and short customers.",
        source=f"desk suggestion log + live board + GPS trailer counts · {source.age_text(info)}",
        body=score.page_body(scores, gd, H.escape) + empties.pointer(empty, gd), extra_css=_CSS, refresh=900),
        encoding="utf-8")
    papers = [{"driver": p["name"], "paper": what, "date": when.isoformat(), "days_left": (when - date.today()).days}
              for p in table.people for what, when in (("licence", p["cdl"]), ("medical", p["medical"]))
              if when and (when - date.today()).days <= 30]

    feed = {
        "generated": datetime.now().isoformat(timespec="minutes"), "day": day.isoformat(),
        "week_ahead": [{"day": a["day"].isoformat(), "regions": {REGION_NAME[c]: v for c, v in a["regions"].items()}}
                       for a in ahead],
        "can_run": {REGION_NAME[c]: n for c, n in can_run.items()},
        "score": [{"day": s["day"].isoformat(), "suggestions": s["judge"]["n"], **s["judge"]["count"],
                   "backhauls_paired": s["miles"]["pairs"], "empty_miles": round(s["miles"]["actual"]),
                   "over_best": round(s["miles"]["actual"] - s["miles"]["best"]),
                   "backhaul": {**s["judge"]["by_kind"]["Backhaul"],
                                **{k: round(v) for k, v in s["judge"]["closer"].items()}},
                   "customers_short": s["short"]} for s in scores],
        "papers": sorted(papers, key=lambda x: x["days_left"]),
        "tracking": {"ok": tracking["ok"], "count": tracking.get("count", {}), "loads": tracking["loads"]},
        "roster": {"on_tab": len(table.people), "on_board": len(people_seen)},
        "board": {"file": info["name"], "saved": info["saved"].isoformat(timespec="minutes"),
                  "age_hours": round(info["age_hours"], 2)},
        "loads": {"on_tab": len(rows),
                  "open": sum(1 for r in rows if r["stage"] != S.DELIVERED),
                  "delivered": sum(1 for r in rows if r["stage"] == S.DELIVERED),
                  "no_name": sum(1 for r in rows if r["stage"] != S.DELIVERED and not r["pre"] and not r["drv"])},
        "pools": [{"customer": x["section"], "there": x["on_site"], "wanted": x["want"], "short": x["short"],
                   "wanted_from": "usual level (GPS history)" if x.get("derived") else "stated by the customer"
                   if x["want"] is not None else None}
                  for x in pc.get("sites", [])],
        "regions": {code: {
            "name": r["name"], "trucks_in": len(r["trucks"]), "loads_waiting": len(r["waiting"]),
            "outbound_no_driver": sum(1 for x in r.get("driver_sug", []) if not x["who"]),
            "backhauls_no_truck": len(r.get("still_no_truck", r["no_truck"])),
            "trucks_home_empty": [t["who"] for t in r["empty_home"]],
            "driver_suggestions": [{"load": x["row"]["ref"], "driver": x["who"], "next": x["alts"],
                                    "backhaul": x["bh"]["ref"] if x.get("bh") else None}
                                   for x in r.get("driver_sug", []) if x["who"]],
            "backhaul_suggestions": [{"driver": m["truck"]["who"], "load": m["loads"][0]["ref"],
                                      "customer": m["loads"][0]["section"],
                                      "empty_miles": round(m["empty_mi"]) if m.get("empty_mi") is not None else None}
                                     for m in r["matches"] if m["how"] == "suggested"],
        } for code, r in regions.items()},
        "on_site": extra["onsite"].get("stops", []),
        "swap_overdue": [{"load": x["row"]["ref"], "customer": x["row"]["section"],
                          "ready_since": x["since"].isoformat(), "days": x["days"]} for x in extra["swaps"]],
        "local_suggestions": [{"load": x["row"]["ref"], "customer": x["row"]["section"], "driver": x["who"],
                               "next": x["alts"]} for x in extra["local"]],
        "weekly_miles": [{"week": w["week"].isoformat(), "backhauls": w["pairs"], "empty_miles": round(w["actual"]),
                          "best_possible": round(w["best"])} for w in extra["weekly"]],
        "empty_returns": {**empties.feed(empty, REGION_NAME, fuel),
                          "loose_count": {"empty": loose["empty"], "empty_home_mi": round(loose["empty_home_mi"])}},
        "pairing_history": {k: {x: round(y) for x, y in v.items()} for k, v in h.items()},
        "note": "Suggestions only. Published DATA: any page may read it; nothing writes the board.",
    }
    (OUT / "desk.json").write_text(json.dumps(feed, indent=1), encoding="utf-8")
    for k, v in h.items():
        if v["pairs"]:
            print(f"[desk] history {REGION_NAME[k]:14} {v['pairs']:3} backhauls · {v['actual']:6.0f} empty mi driven · "
                  f"{v['best']:6.0f} best · {v['actual'] - v['best']:5.0f} left on the table")
    for x in pc.get("sites", []):
        if x["want"] is not None or x["on_site"]:
            print(f"[desk] pool {x['section']:22} there {x['on_site']:2} · wanted {x['want'] if x['want'] is not None else '-':>2} · "
                  f"loaded {len(x['loaded'])} · other {len(x['other'])}")
    for code, r in regions.items():
        print(f"[desk] {r['name']:14} trucks in {len(r['trucks']):2} · waiting {len(r['waiting']):2} · "
              f"on board {sum(1 for m in r['matches'] if m['how'] != 'suggested'):2} · "
              f"suggested {sum(1 for m in r['matches'] if m['how'] == 'suggested'):2} · "
              f"no truck {len(r['no_truck']):2} · home empty {len(r['empty_home']):2} · "
              f"not allowed {len(r['not_allowed']):2} · outbound no driver {len(r['unstaffed_out']):2}")
        for m in r["matches"]:
            if m["how"] == "suggested":
                t = m["truck"]
                print(f"         {t['who']:14} [{t['auth']}] {t['row']['cells'][C['delivery']].splitlines()[0][:26]:26} -> "
                      f"{m['loads'][0]['ref']} {m['loads'][0]['section']:20} empty {_mi(m['empty_mi'])} mi")
            elif m.get("nearer"):
                print(f"         {m['truck']['who']:14} on board -> {m['loads'][0]['section']} {_mi(m['empty_mi'])} mi; "
                      f"nearer READY: {m['nearer'][1]['section']} {m['nearer'][0]:.0f} mi")
        for t in r["not_allowed"]:
            print(f"         {t['who']:14} NOT SUGGESTED [{t['auth_text']}] on {t['row']['ref']}")
    et = empties.total(empty["trips"])
    print(f"[desk] empty trips home {empty['from']:%m/%d}-{empty['to']:%m/%d}: {et['empty']} of {et['trips']} "
          f"({et['share']:.0%}) · {et['empty_home_mi']:,.0f} mi · ready trailer there on {et['could']} · "
          f"loose count {loose['empty']}")
    print(f"[desk] {out}  ({source.age_text(info)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
