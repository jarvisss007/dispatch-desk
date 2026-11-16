r"""WHO SHOULD TAKE IT — a driver for every out-of-town load that has none.

The day plan's biggest gap is not pairing, it is loads with nobody on them:
an Arizona delivery with no driver is also an Arizona backhaul with no truck.
This fills the DRIVER column (as a suggestion) for each such load, then the
plan chains the nearest backhaul onto it.

Who is a candidate for a load into a region:
  * he is working today  — his name is on today's tab (or yesterday's, flagged)
  * he may run there     — the roster says so, or the roster does not
                           list him but the board shows him delivering there
                           3+ times in the last 45 tabs ("proven by history")
  * he is not already gone — not the DRIVER on another out-of-town load today
                             and not already pulling a backhaul out of town

Who is the BEST candidate (lowest cost, all loads solved together):
  * has delivered to this same receiver before       (knows the dock)
  * has run this region often                        (knows the lane)
  * has the fewest stops already on his paper today  (spread the work)

What it does NOT know: hours-of-service, who called in sick, a truck in the
shop. So it is a ranked suggestion with the reason shown — the dispatcher
decides. `backtest()` measures it against who the office really sent.
"""
from __future__ import annotations

from collections import Counter, defaultdict

MIN_PROVEN = 3
NO_ONE = 1e6


def _receiver(row: dict, C: dict) -> str:
    return row["cells"][C["delivery"]].split("\n")[0].strip().upper()[:28]


def lane_history(days: list[list[dict]], C: dict) -> dict:
    """What every driver has delivered, from past tabs (one count per load)."""
    reg, rec, seen = defaultdict(Counter), defaultdict(Counter), set()
    for rows in days:
        for r in rows:
            if r["dr"] == "LOCAL" or not r["drv"] or r["ref"] in seen:
                continue
            seen.add(r["ref"])
            who = r["drv"][0]
            reg[who][r["dr"]] += 1
            rec[who][_receiver(r, C)] += 1
    return {"region": reg, "receiver": rec}


def workload(rows: list[dict]) -> tuple[Counter, set, set]:
    """(stops per name today, names already out of town today, names on the tab)."""
    stops, away, on_tab = Counter(), set(), set()
    for r in rows:
        for who in set(r["pre"]) | set(r["drv"]):
            stops[who] += 1
            on_tab.add(who)
        if r["dr"] != "LOCAL" and r["drv"]:
            away.add(r["drv"][0])
        if r["pr"] != "LOCAL" and r["pre"]:
            away.add(r["pre"][0])
    return stops, away, on_tab


def cost(who: str, row: dict, code: str, hist: dict, stops: Counter, on_today: bool, C: dict) -> float:
    same_dock = min(hist["receiver"][who][_receiver(row, C)], 5)
    same_lane = min(hist["region"][who][code], 10)
    return 20 - 2 * same_dock - 0.5 * same_lane + 4 * stops[who] + (0 if on_today else 6)


def candidates(code: str, rows: list[dict], yesterday: list[dict], hist: dict,
               auth_fn, exclude: set, same=None) -> dict[str, dict]:
    """same(name) -> the person behind a board spelling. One man written two
    ways ("OWEN C" / "OWEN CARTER") is one candidate, and is busy if either
    spelling is (an early version offered him two loads at once)."""
    same = same or (lambda w: w)
    stops, away, on_tab = workload(rows)
    _, _, on_yday = workload(yesterday)
    busy = {same(w) for w in away | set(exclude)}
    total = Counter()
    for w in on_tab:
        total[same(w)] += stops[w]
    out, kept = {}, {}
    for who in sorted(on_tab | on_yday, key=lambda w: (w not in on_tab, -stops[w], w)):
        if same(who) in busy or same(who) in kept:
            continue
        a, words = auth_fn(who, code)
        runs = hist["region"][who][code]
        if a == "no":
            continue
        if "CHECK FIRST" in words:
            continue        # licence or medical date on the DRIVERS tab has passed: never OFFER him a new load
        if a == "unknown":
            if runs < MIN_PROVEN:
                continue
            words = f"not on the roster, but {runs} runs here on the board"
        kept[same(who)] = who
        out[who] = {"auth": a, "auth_text": words, "runs": runs,
                    "stops": total[same(who)], "on_today": who in on_tab}
    return out


def suggest(code: str, loads: list[dict], rows: list[dict], yesterday: list[dict],
            hist: dict, auth_fn, C: dict, exclude: set, same=None) -> list[dict]:
    """One suggestion per load (driver or None), all loads solved together so
    the same man is not offered twice."""
    if not loads:
        return []
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    cand = candidates(code, rows, yesterday, hist, auth_fn, exclude, same)
    names = sorted(cand)
    stops = Counter({n: cand[n]["stops"] for n in names})
    out = [{"row": l, "who": None, "why": "nobody free who may run this region", "alts": []} for l in loads]
    if not names:
        return out
    m = np.array([[cost(n, l, code, hist, stops, cand[n]["on_today"], C) for n in names] for l in loads])
    ri, ci = linear_sum_assignment(m)
    for i, j in zip(ri.tolist(), ci.tolist()):
        n, l = names[j], loads[i]
        c = cand[n]
        dock = hist["receiver"][n][_receiver(l, C)]
        bits = [f"{dock}x to this receiver" if dock else "new to this receiver",
                f"{c['runs']} runs in this region", f"{c['stops']} stops today",
                c["auth_text"]]
        if not c["on_today"]:
            bits.append("NOT on today's board yet — confirm he is in")
        order = sorted(range(len(names)), key=lambda k: m[i, k])
        out[i] = {"row": l, "who": n, "why": " · ".join(bits), "cand": c,
                  "alts": [names[k] for k in order if names[k] != n][:2]}
    return out


def backtest(days: list[list[dict]], auth_fn, C: dict, warmup: int = 15) -> dict:
    """For each past out-of-town delivery: hide the driver, rank everyone who
    was on that day's tab and allowed there, and see where the man the office
    really sent lands. History used is only what came BEFORE that day."""
    res = {"n": 0, "top1": 0, "top3": 0, "pool": 0}
    for i in range(warmup, len(days)):
        hist = lane_history(days[:i], C)
        rows = days[i]
        stops, _, on_tab = workload(rows)
        for r in rows:
            if r["dr"] == "LOCAL" or r["pr"] == r["dr"] or not r["drv"]:
                continue
            real = r["drv"][0]
            pool = []
            for who in on_tab:
                a, _ = auth_fn(who, r["dr"])
                if a == "no" or (a == "unknown" and hist["region"][who][r["dr"]] < MIN_PROVEN):
                    continue
                pool.append(who)
            if real not in pool or len(pool) < 4:
                continue
            adj = Counter(stops)
            adj[real] -= 1                      # he would not have this stop yet
            ranked = sorted(pool, key=lambda w: cost(w, r, r["dr"], hist, adj, True, C))
            res["n"] += 1
            res["pool"] += len(pool)
            res["top1"] += ranked[0] == real
            res["top3"] += real in ranked[:3]
    return res


# ---------------------------------------------------------------- local loads
def section_history(days: list[list[dict]]) -> dict:
    """Who works which shipper section on local moves (pickup and delivery both local)."""
    sec = defaultdict(Counter)
    for rows in days:
        for r in rows:
            if r["pr"] == "LOCAL" and r["dr"] == "LOCAL":
                for who in set(r["pre"]) | set(r["drv"]):
                    sec[who][r["section"].strip().upper()] += 1
    return sec


def local_cost(who: str, section: str, sec: dict, stops: Counter) -> float:
    return 12 - 2 * min(sec[who][section.strip().upper()], 5) + 3 * stops[who]


def suggest_local(loads: list[dict], rows: list[dict], sec: dict, exclude: set) -> list[dict]:
    """A name for each local load that has none: someone on today's board, not
    out of town, who has worked that shipper before, with the lightest day."""
    stops, away, on_tab = workload(rows)
    pool = sorted(on_tab - away - exclude)
    out = []
    extra = Counter()
    for l in loads:
        s = l["section"].strip().upper()
        known = [w for w in pool if sec[w][s] >= 2]
        if not known:
            out.append({"row": l, "who": None, "alts": [], "why": "nobody on today's board has worked this customer"})
            continue
        adj = stops + extra
        ranked = sorted(known, key=lambda w: local_cost(w, s, sec, adj))
        who = ranked[0]
        extra[who] += 1
        out.append({"row": l, "who": who, "alts": ranked[1:3],
                    "why": f"{sec[who][s]} loads for this customer before · {adj[who]} stops today"})
    return out


def backtest_local(days: list[list[dict]], warmup: int = 15) -> dict:
    res = {"n": 0, "top1": 0, "top3": 0, "pool": 0}
    for i in range(warmup, len(days)):
        sec = section_history(days[:i])
        rows = days[i]
        stops, away, on_tab = workload(rows)
        for r in rows:
            if r["pr"] != "LOCAL" or r["dr"] != "LOCAL" or not (r["pre"] or r["drv"]):
                continue
            real = (r["pre"] or r["drv"])[0]
            s = r["section"].strip().upper()
            pool = [w for w in on_tab if (w not in away or w == real) and sec[w][s] >= 2]
            if real not in pool or len(pool) < 4:
                continue
            adj = Counter(stops)
            adj[real] -= 1
            ranked = sorted(pool, key=lambda w: local_cost(w, s, sec, adj))
            res["n"] += 1
            res["pool"] += len(pool)
            res["top1"] += ranked[0] == real
            res["top3"] += real in ranked[:3]
    return res
