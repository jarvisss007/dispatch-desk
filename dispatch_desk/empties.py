"""Empty trips home: every truck that delivered out of town and pulled nothing back.

The score page compares WHICH truck pulled a backhaul. This counts the bigger
loss: the truck that pulled none. Read from the board's own daily tabs only.

How a trip is read
  trip    one driver, one region, one day: the tab on which a load delivered
          into Arizona / Las Vegas / Central Valley last shows his name in the
          DRIVER column. A load rolls from tab to tab until it is done, so it
          is counted once, by its load number, on its last tab.
  loaded  the same person is the PRELOADER (the puller) of a load that starts
          in that region, first named on the tab before, the same tab or the
          tab after. Each pull pays for one trip only.
  empty   no such pull. Empty miles home = where he unloaded to the yard.
  a trailer was there
          on the tab he delivered, a load starting in that region showed READY
          with no puller. Each such load can cover one empty truck only, ever.

It is the board's view, not GPS: a pull the office never typed into the
PRELOADER column reads as an empty trip. Today's tab is left out: its pulls
are still being typed. The long leg home uses a road factor per region
(HOME_FACTOR) because straight line x 1.25, right for a short hop between two
docks, overstates a freeway run by up to a fifth.
Suggestions only: nothing here writes anywhere but the desk's own pages.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import date, timedelta

from pathlib import Path

from dispatch_desk import geo, roster, settings

# A JSON file published by whatever tracks fuel (a fuel-card dashboard, say).
# Read-only, two figures only: {"driverScore": {"fleetMpg"}, "fuelDaily": {"netPpg"}}.
FUEL_FILE = settings.FUEL_FILE


def fuel_rates(path: Path | None = None) -> dict | None:
    """{'mpg', 'ppg', ...} from the fuel figures file, or None when it cannot be read.
    mpg = its fleet figure (card fills, odometer at fill, averaged over drivers);
    ppg = what a gallon cost over its last 14 days, card and yard fuel together."""
    try:
        d = json.loads((path or FUEL_FILE).read_text(encoding="utf-8-sig"))
        mpg = float(d["driverScore"]["fleetMpg"])
        ppg = float(d["fuelDaily"]["netPpg"])
        if not (3 <= mpg <= 12 and 1 <= ppg <= 15):
            return None                      # not a believable truck figure: show no dollars
        return {"mpg": mpg, "ppg": ppg, "mpg_built": str(d["driverScore"].get("generated", "")),
                "ppg_as_of": str(d["fuelDaily"].get("asOf", "")),
                "ppg_days": d["fuelDaily"].get("windowDays")}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def dollars(mi: float, fuel: dict | None) -> float | None:
    return mi / fuel["mpg"] * fuel["ppg"] if fuel else None

WINDOW = 1            # a pull counts for a trip when first named within this many TABS of it
REF_GAP_DAYS = 10     # the same load number this far apart is a different load
# Road miles over straight-line miles for the run back to the yard, from highway
# distances (Fontana-Tolleson ~315, Fontana-Las Vegas ~215, Fontana-Tracy ~385).
HOME_FACTOR = {"AZ": 1.05, "NV": 1.12, "CV": 1.15}
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _people(days, same):
    """One key per person: the roster's name when the DRIVERS tab knows him,
    else the commonest of the spellings that are plainly one name."""
    seen: dict[str, int] = defaultdict(int)
    for _, rows in days:
        for r in rows:
            for w in list(r["drv"][:1]) + list(r["pre"][:1]):
                seen[w] += 1
    key = {}
    loose = []
    for w in sorted(seen, key=lambda x: -seen[x]):
        s = same(w)
        if s != w:
            key[w] = s
            continue
        hit = next((k for k in loose if roster._close(k, w)), None)
        key[w] = hit or w
        if not hit:
            loose.append(w)
    return key


def _place(text: str) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    line = re.split(r"\s+\d{2,}", line.upper())[0]          # drop the street address
    line = re.sub(r"\s*,\s*", ", ", re.sub(r"\s+-\s*|\s*-\s+", " - ", line))    # 'TRACY,CA' = 'TRACY, CA': one place
    return re.sub(r"\s+", " ", line).strip(" -,")[:34] or "(not named)"


def home_miles(pos, code: str) -> float | None:
    """Road miles from a delivery back to the yard."""
    if not pos:
        return None
    return geo.miles(pos, geo.YARD) / geo.ROAD_FACTOR * HOME_FACTOR.get(code, geo.ROAD_FACTOR)


def analyze(days: list[tuple[date, list[dict]]], codes: list[str], delivery_col: int, same=None,
            not_pulled=(), today: date | None = None, window: int = WINDOW) -> dict:
    """days = [(tab date, classified rows)] oldest first. Returns
    {'trips': [...], 'from': date, 'to': date, 'tabs': n}; each trip is
    {'day','code','who','place','refs','home_mi','loaded','bh','bh_place','empty_mi','ready_there','could','could_mi'}."""
    same = same or (lambda w: w)
    today = today or date.today()
    days = sorted(days, key=lambda x: x[0])
    if not days:
        return {"trips": [], "tabs": 0, "from": None, "to": None}
    key = _people(days, same)
    idx = {d: i for i, (d, _) in enumerate(days)}

    last_in: dict[tuple, tuple] = {}        # (code, ref) -> (day, row): last tab with a driver on it
    pull: dict[tuple, dict] = {}            # (code, ref) -> the pull event
    waiting: dict[tuple, list] = defaultdict(list)      # (code, day) -> READY loads with no puller
    for d, rows in days:
        for r in rows:
            ref = r["ref"] or f"row{r['n']}@{d}"
            if r["dr"] in codes and r["pr"] != r["dr"] and r["drv"]:
                k = (r["dr"], ref)
                if k in last_in and (d - last_in[k][0]).days > REF_GAP_DAYS:
                    k = (r["dr"], f"{ref}@{d}")
                last_in[k] = (d, r)
            if r["pr"] in codes and r["dr"] != r["pr"]:
                k = (r["pr"], ref)
                if r["pre"]:
                    who = key.get(r["pre"][0], r["pre"][0])
                    e = pull.get(k)
                    if not e or e["who"] != who or (d - e["last"]).days > REF_GAP_DAYS:
                        e = pull[k] = {"who": who, "day": d, "row": r, "code": r["pr"], "used": False}
                    e["last"] = d
                elif r["ready"] and (not not_pulled or r["stage"] in not_pulled):
                    waiting[(r["pr"], d)].append(r)

    # one trip per person, region and day
    trips: dict[tuple, dict] = {}
    for (code, _), (d, r) in last_in.items():
        if d >= today:
            continue                         # today's pulls are still being typed: not judged yet
        who = key.get(r["drv"][0], r["drv"][0])
        t = trips.setdefault((who, code, d), {
            "day": d, "code": code, "who": who, "refs": [], "row": r,
            "place": _place(r["cells"][delivery_col]),
            "home_mi": home_miles(r.get("dpos"), code)})
        t["refs"].append(r["ref"])

    by_person: dict[tuple, list] = defaultdict(list)
    for e in pull.values():
        by_person[(e["who"], e["code"])].append(e)
    out = sorted(trips.values(), key=lambda t: (t["day"], t["code"], t["who"]))
    for t in out:
        i = idx[t["day"]]
        near = [e for e in by_person.get((t["who"], t["code"]), [])
                if not e["used"] and abs(idx[e["day"]] - i) <= window]
        near.sort(key=lambda e: (abs(idx[e["day"]] - i), idx[e["day"]] < i))
        e = near[0] if near else None
        t.update(loaded=bool(e), bh=e["row"]["ref"] if e else None, bh_place=e["row"]["section"] if e else None,
                 empty_mi=None, ready_there=len(waiting.get((t["code"], t["day"]), [])), could=None, could_mi=None)
        if e:
            e["used"] = True
            if t["row"].get("dpos") and e["row"].get("ppos"):
                t["empty_mi"] = geo.miles(t["row"]["dpos"], e["row"]["ppos"])
        else:
            t["empty_mi"] = t["home_mi"]

    # an empty truck against a READY trailer nobody pulled that day — each trailer used once, ever
    spent: set = set()
    for t in out:
        if t["loaded"]:
            continue
        free = [l for l in waiting.get((t["code"], t["day"]), []) if (t["code"], l["ref"] or id(l)) not in spent]
        if not free:
            continue

        def gap(l):
            return geo.miles(t["row"]["dpos"], l["ppos"]) if t["row"].get("dpos") and l.get("ppos") else 9e9
        l = min(free, key=gap)
        spent.add((t["code"], l["ref"] or id(l)))
        t["could"] = f"{l['ref']} {l['section']}".strip()
        t["could_mi"] = gap(l) if gap(l) < 9e9 else None
    for t in out:
        t.pop("row")
    judged = [d for d, _ in days if d < today] or [days[0][0]]
    return {"trips": out, "tabs": len(judged), "from": judged[0], "to": judged[-1]}


def total(trips: list[dict]) -> dict:
    """Roll a list of trips up to one line."""
    empty = [t for t in trips if not t["loaded"]]
    could = [t for t in empty if t["could"]]
    return {
        "trips": len(trips), "loaded": len(trips) - len(empty), "empty": len(empty),
        "share": len(empty) / len(trips) if trips else 0.0,
        "empty_home_mi": sum(t["home_mi"] or 0 for t in empty),
        "unplaced": sum(1 for t in empty if t["home_mi"] is None),
        "could": len(could),
        "could_home_mi": sum(t["home_mi"] or 0 for t in could),
        # what the truck would still have driven empty to reach that trailer
        "could_reach_mi": sum(t["could_mi"] or 0 for t in could),
    }


def by(trips: list[dict], fn) -> dict:
    g: dict = defaultdict(list)
    for t in trips:
        g[fn(t)].append(t)
    return {k: total(v) for k, v in g.items()}


def week_of(t: dict) -> date:
    return t["day"] - timedelta(days=t["day"].weekday())


def feed(a: dict, names: dict, fuel: dict | None = None) -> dict:
    """The published numbers (desk.json): totals, per region, per week, last 14 days."""
    tr = a["trips"]
    if not tr:
        return {}
    r1 = lambda v: {k: (round(x, 3) if k == "share" else round(x)) for k, x in v.items()}   # noqa: E731
    recent = [t for t in tr if (a["to"] - t["day"]).days < 14]
    return {
        "from": a["from"].isoformat(), "to": a["to"].isoformat(), "tabs": a["tabs"],
        "how": "board tabs only, today left out; a pull not typed in the PRELOADER column reads as empty; "
               "miles approximate",
        "all": r1(total(tr)),
        "regions": {names.get(k, k): r1(v) for k, v in by(tr, lambda t: t["code"]).items()},
        "weeks": [{"week": k.isoformat(), **r1(v)} for k, v in sorted(by(tr, week_of).items())],
        "last_14_days": r1(total(recent)),
        "fuel": ({**fuel, "source": "fuel figures file: driverScore.fleetMpg, fuelDaily.netPpg",
                  "empty_home_gal": round(total(tr)["empty_home_mi"] / fuel["mpg"]),
                  "empty_home_usd": round(dollars(total(tr)["empty_home_mi"], fuel)),
                  "could_home_usd": round(dollars(total(tr)["could_home_mi"], fuel)),
                  "note": "today's price on every week; fleet-average mpg, an empty truck burns less"}
                 if fuel else None),
    }


def page_body(a: dict, names: dict, gd, esc, wide: dict | None = None, wide_tabs: int = 3,
              fuel: dict | None = None) -> str:
    """wide = total() of the same trips judged with a looser window, shown as the low end."""
    tr = a["trips"]
    if not tr:
        return gd.callout("No finished out-of-town trips on the board yet.", "")
    tot = total(tr)
    n_weeks = max(1, len({week_of(t) for t in tr}))
    span = f"{a['from']:%m/%d} to {a['to']:%m/%d}"
    body = gd.callout(
        f"{tot['empty']} of {tot['trips']} out-of-town trips came home with no backhaul ({tot['share']:.0%}) — "
        f"{tot['empty_home_mi']:,.0f} empty miles home, about {tot['empty_home_mi'] / n_weeks:,.0f} a week.",
        f"On {tot['could']} of those trips a READY trailer with no puller was sitting in that region on the same "
        f"tab: {tot['could_home_mi']:,.0f} of the empty miles, of which about "
        f"{max(0.0, tot['could_home_mi'] - tot['could_reach_mi']):,.0f} would have been driven loaded "
        f"(the other {tot['could_reach_mi']:,.0f} is the drive to reach the trailer). "
        f"Board tabs {span} ({a['tabs']} tabs).")
    if wide and wide["empty"] != tot["empty"]:
        body += gd.callout(
            f"Counted the loose way it is {wide['empty']} trips and {wide['empty_home_mi']:,.0f} miles.",
            f"That is allowing a pull typed up to {wide_tabs} tabs away from the delivery to count for the trip, "
            f"instead of one. The true figure sits between the two; the tables below use the stricter count.")

    if fuel:
        lo = wide if wide and wide["empty"] != tot["empty"] else tot
        usd = dollars(tot["empty_home_mi"], fuel)
        usd_lo = dollars(lo["empty_home_mi"], fuel)
        rng = f"${usd_lo:,.0f} to ${usd:,.0f}" if lo is not tot else f"${usd:,.0f}"
        wk = (f"${usd_lo / n_weeks:,.0f} to ${usd / n_weeks:,.0f}" if lo is not tot else f"${usd / n_weeks:,.0f}")
        body += gd.callout(
            f"Fuel burned on those empty miles: about {rng} — {wk} a week. "
            f"${dollars(tot['could_home_mi'], fuel):,.0f} of it was on trips where a READY trailer sat there.",
            f"{tot['empty_home_mi'] / fuel['mpg']:,.0f} gallons at {fuel['mpg']:.2f} miles per gallon and "
            f"${fuel['ppg']:.2f} a gallon. Both figures are read from the fuel figures file: its fleet miles per gallon "
            f"(card fills, built {esc(fuel['mpg_built'])}) and its price per gallon for the "
            f"{fuel['ppg_days'] or 14} days to {esc(fuel['ppg_as_of'])}, card and yard fuel together. "
            "Two cautions: this puts today's price on every week counted, and the miles per gallon is "
            "the fleet average over all miles — a truck pulling an empty trailer burns less, so read these "
            "dollars as the high side. This is fuel spent earning nothing, not money that comes back: the truck "
            "still drives home, the gain is that it carries a paying load.")

    def line(label, v):
        return [label, v["trips"], v["loaded"], v["empty"], f"{v['share']:.0%}", f"{v['empty_home_mi']:,.0f}",
                v["could"], f"{v['could_home_mi']:,.0f}"] + (
                    [f"${dollars(v['empty_home_mi'], fuel):,.0f}"] if fuel else [])
    head = ["", "Trips", "Came back loaded", "Came back empty", "Empty share", "Empty miles home",
            "Empty while a READY trailer sat there", "Empty miles on those trips"] + (
                ["Fuel on the empty miles"] if fuel else [])
    al = ["l"] + ["n"] * (len(head) - 1)
    reg = by(tr, lambda t: t["code"])
    body += "<h2>By region</h2>" + gd.table(
        ["Region"] + head[1:], [line(f"<b>{esc(names.get(k, k))}</b>", reg[k]) for k in names if k in reg]
        + [line("<b>All</b>", tot)], al)
    wk = by(tr, week_of)
    body += "<h2>Week by week</h2>" + gd.table(
        ["Week of"] + head[1:], [line(f"<b>{k:%m/%d}</b>", wk[k]) for k in sorted(wk, reverse=True)], al)
    wd = by(tr, lambda t: t["day"].weekday())
    body += "<h2>By day of the week</h2>" + gd.table(
        ["Day"] + head[1:], [line(f"<b>{DAYS[k]}</b>", wd[k]) for k in sorted(wd)], al)
    pl = by(tr, lambda t: (t["code"], t["place"]))
    top = sorted((k for k in pl if pl[k]["trips"] >= 4), key=lambda k: -pl[k]["empty_home_mi"])[:20]
    body += "<h2>Where the empty trips come from — receivers with four or more trips</h2>" + gd.table(
        ["Delivered to", "Region"] + head[1:],
        [[esc(k[1]), esc(names.get(k[0], k[0]))] + line("", pl[k])[1:] for k in top], ["l", "l"] + ["n"] * (len(head) - 1))
    recent = [t for t in tr if not t["loaded"] and (a["to"] - t["day"]).days < 14]
    recent.sort(key=lambda t: (t["day"], t["code"]), reverse=True)
    body += "<h2>Every empty trip, last 14 days</h2>" + gd.table(
        ["Day", "Region", "Driver", "Delivered to", "Load", "Empty miles home", "READY trailers with no puller that day",
         "One he could have pulled", "Miles to reach it"],
        [[f"<b>{t['day']:%a %m/%d}</b>", esc(names.get(t["code"], t["code"])), esc(t["who"]), esc(t["place"]),
          f'<span class="mono">{esc(", ".join(t["refs"]))}</span>',
          "—" if t["home_mi"] is None else f"{t['home_mi']:,.0f}", t["ready_there"],
          f'<span class="sug">{esc(t["could"])}</span>' if t["could"]
          else "already counted for another truck" if t["ready_there"] else "none",
          "" if t["could_mi"] is None else f"{t['could_mi']:,.0f}"] for t in recent],
        ["l", "l", "l", "l", "l", "n", "n", "l", "n"])
    body += gd.callout(
        "How to read this",
        "A trip is one driver delivering into one region on one day, taken from the last tab that shows his name "
        "on that load. It counts as loaded when the same person is the puller (PRELOADER column) of a load that "
        "starts in that region, first named on the tab before, the same tab or the tab after. This is the board's "
        "view, not GPS: a pull never typed on the board reads as empty, and a driver who stayed out for a second "
        "delivery reads as two trips. \"A READY trailer sat there\" means a load starting in that region showed "
        "READY with no puller on that day's tab; each trailer is counted against one empty truck only. "
        "Today's tab is left out because its pulls are still being typed, and yesterday's can still change. "
        "Miles home are straight line times a road factor per region (Arizona 1.05, Las Vegas 1.12, Central "
        "Valley 1.15); miles to reach a trailer are straight line x 1.25. Both are approximate.")
    return body


def pointer(a: dict, gd) -> str:
    """Two lines for the score page, pointing at the full page."""
    if not a["trips"]:
        return ""
    t = total(a["trips"])
    return "<h2>Trucks that pulled no backhaul at all</h2>" + gd.callout(
        f"{t['empty']} of {t['trips']} out-of-town trips came home empty ({t['share']:.0%}), "
        f"{t['empty_home_mi']:,.0f} empty miles, {a['from']:%m/%d} to {a['to']:%m/%d}. "
        f"A READY trailer with no puller was in that region on {t['could']} of them.",
        '<a href="EMPTY_RETURNS.html">Open the empty trips page</a> for regions, weeks, receivers and every trip.')
