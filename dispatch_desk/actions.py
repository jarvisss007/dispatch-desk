r"""THE WORKLIST — the top of the day plan, as something a dispatcher can WORK:
tick an item done, pick a different driver, copy the line for the board, filter
by region. And for every problem it lists the ways out, nearest/cheapest first.

State (what was ticked, who was picked) is kept in the browser, per day, so
the 15-minute rebuild does not wipe it. It is this browser's memory only — it
does not write the board and nobody else sees it.

Solutions offered (all from the board + live GPS, nothing invented):
  no driver for an outbound load   -> who gets BACK today and may run it;
                                      the latest it can leave the yard
  backhaul with no truck           -> the uncovered outbound load that would
                                      bring it home; else a yard run, with miles
  truck coming home empty          -> trailers still at sites near his drop
                                      (not ready yet / not on the board — ask)
  customer short of trailers       -> our trailers parked at other sites nearby
                                      that no open load names; else the yard
"""
from __future__ import annotations

import html as H
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta

from dispatch_desk import assign, extras, geo, settings

DRIVE_H = {"AZ": 6.5, "NV": 4.5, "CV": 6.0}      # yard -> region, loaded, with a stop
NEAR_MI = 30
_APPT = re.compile(r"(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})(?::(\d{2}))?\s*([AP])M", re.I)
ARROW = '<span class="arrow">→</span>'


def _e(s) -> str:
    return H.escape(str(s))


def _miles(v) -> str:
    return "an unknown distance" if v is None else ("under a mile" if v < 1.5 else f"{v:.0f} miles")


def _drop(row, C) -> str:
    cell = row["cells"][C["delivery"]]
    return cell.splitlines()[0].strip()[:40] if cell else "?"


def _appt_text(row, C) -> str:
    return " · ".join(c[C["delivery"]] for c in row["cont"] if c[C["delivery"]]) or "no appointment on the board"


def leave_by(row, code, C, now: datetime) -> str:
    m = _APPT.search(_appt_text(row, C))
    if not m:
        return ""
    hour = int(m.group(3)) % 12 + (12 if m.group(5).upper() == "P" else 0)
    try:
        appt = datetime(now.year, int(m.group(1)), int(m.group(2)), hour, int(m.group(4) or 0))
    except ValueError:
        return ""
    go = appt - timedelta(hours=DRIVE_H[code])
    if go < now:
        return f"It had to leave the yard by {go:%a %I:%M %p} — already late for this appointment."
    return f"Latest it can leave the yard: {go:%a %I:%M %p}."


def _trl(l) -> str:
    return f" trl {l['trailer']}" if l.get("trailer") else ""


# ---------------------------------------------------------------- items
def _item(iid, region, do, why="", check="", tone="", copy="", picks=None, options=None) -> str:
    opts = ""
    if picks:
        opts = ('<select class="pick" title="who takes it">'
                + "".join(f"<option>{_e(p)}</option>" for p in picks) + "</select>")
    sol = ""
    if options:
        sol = '<ol class="opts">' + "".join(f"<li>{o}</li>" for o in options) + "</ol>"
    return (f'<div class="item {tone}" data-id="{_e(iid)}" data-region="{_e(region)}" data-copy="{_e(copy)}">'
            f'<div class="main"><div class="do">{do}</div>'
            + (f'<div class="why">{why}</div>' if why else "")
            + (f'<div class="chk">CHECK: {_e(check)}</div>' if check else "")
            + (f'<div class="sol"><span>Ways to fix it</span>{sol}</div>' if sol else "")
            + '</div><div class="btns">' + opts
            + '<button class="done" type="button">Done</button>'
            + ('<button class="copy" type="button">Copy</button>' if copy else "")
            + "</div></div>")


def _step(n, title, key) -> str:
    return (f'<div class="step" data-step="{key}"><span class="n">{n}</span>{_e(title)}'
            f'<span class="c"></span></div>')


def render(P, day, regions: dict, rows: list[dict], pc: dict, inv: list[dict],
           table: dict, lane_hist: dict, extra: dict | None = None) -> str:
    """P is the plan module (passed in to avoid a circular import)."""
    C, S = P.C, P.S
    now = datetime.now()
    named = {r["trailer"] for r in rows if r["trailer"] and r["stage"] != S.DELIVERED}
    yard = next((f for f in inv if settings.YARD_FENCE.upper() in f["name"].upper()), None)

    def spare_near(pos, skip_name=""):
        """Our trailers parked at other sites near `pos` that no open load names."""
        out = []
        for f in inv:
            if f is yard or (skip_name and P.pools.fence_for(skip_name, [f])):
                continue
            d = geo.miles(pos, (f["lat"], f["lng"]))
            units = [u for u in f["units"] if u not in named]
            if d <= NEAR_MI and units:
                out.append((d, f["name"], units))
        return sorted(out)[:3]

    extra = extra or {}
    num = iter(range(1, 20))
    out = ""
    # ---------- trucks sitting at a customer right now
    ons = extra.get("onsite", {})
    out += _step(next(num), "Trucks waiting at a customer right now", "s0")
    shown = 0
    for st in ons.get("stops", []):
        if st["state"] == "noload":
            continue
        shown += 1
        hrs = f"{st['stopped_min'] // 60}h{st['stopped_min'] % 60:02d}m"
        tone = {"running": "bad", "soon": "", "free": "fine", "late": "fine", "unknown": ""}.get(st["state"], "")
        if st["state"] == "running":
            act = ("Have the driver write his ARRIVED time and get it signed — this customer is claimed on site times."
                   if st["direct"] else
                   "Email the broker NOW that detention has started, before the truck leaves, and ask for a revised rate con.")
        elif st["state"] == "soon":
            act = "Warn the customer or broker now that detention is about to start."
        elif st["state"] == "late":
            act = "No claim: the truck arrived after its appointment (house rule)."
        else:
            act = ""
        free = f"{st.get('free_min', 120) // 60}h free time" + (
            "" if st.get("free_confirmed") else " (house rule, not confirmed for this customer)")
        ask = ""
        if st["state"] in ("running", "soon") and not st["direct"]:
            ask = (f"Load {st['ref']}: our truck {st['unit']} arrived {st['arrived'][11:16]} for the {st['appt']} "
                   "appointment and is still on site. Detention is running. Please confirm and send a revised "
                   "rate confirmation.")
        out += _item(
            f"site|{st['unit']}|{st['ref']}", P.region_of(st["site"] + " " + st["customer"]),
            f"<b>{_e(st['driver'] or st['unit'])}</b> ({_e(st['unit'])}) at <b>{_e(st['site'])}</b>{ARROW}{_e(st['say'])}",
            f"Load {_e(st['ref'])} · {_e(st['customer'])} · {st['end']} · appointment {_e(st['appt'] or '?')} · "
            f"stopped {hrs} · {free}. {act}", tone=tone, copy=ask)
    if not ons.get("ok", True):
        out += _item("site|gps", "", "Live truck positions not available this run", _e(ons.get("why", "")), tone="bad")
    elif not shown:
        out += '<div class="none">No truck with a live tracker is stopped at a customer on one of today&#39;s loads.</div>'

    # ---------- 1 empties
    out += _step(next(num), "Send empty trailers", "s1")
    short = sorted((x for x in pc.get("sites", []) if (x["short"] or 0) > 0), key=lambda x: -x["short"])
    for x in short:
        f = P.pools.fence_for(x["section"], [{"name": i["name"], "lat": i["lat"], "lng": i["lng"]} for i in inv])
        pos = (f["lat"], f["lng"]) if f else None
        code = P.region_of(x["section"])
        options = []
        if pos:
            for d, name, units in spare_near(pos, x["section"]):
                options.append(f"<b>{_e(name)}</b> is {_miles(d)} away with {len(units)} of our trailers no open load "
                               f"names: {_e(', '.join(units))}. Ask the site which are empty and move them over.")
            ymi = geo.miles(pos, geo.YARD)
            options.append(f"From the yard: {len(yard['units']) if yard else '?'} of our trailers are parked there "
                           f"per GPS; it is about {ymi:.0f} miles one way. Send them with trucks already going "
                           f"{'to ' + P.REGION_NAME[code] if code in P.REGION_NAME else 'that way'} without a trailer to return.")
        loaded = len(x["loaded"])
        out += _item(
            f"empty|{x['section']}", code,
            f"<b>{_e(x['section'])}</b>{ARROW}send <b>{x['short']}</b> empt{'y' if x['short'] == 1 else 'ies'}",
            f"They have {x['on_site']} of our trailers, they want {x['want']}."
            + (f" {loaded} of those are loaded and waiting to be pulled." if loaded else ""),
            copy=f"{x['section']} — SEND {x['short']} EMPTY (has {x['on_site']}, pool {x['want']})",
            options=options)
    if not short:
        out += '<div class="none">No customer with a known pool size is short.</div>'

    # ---------- ready more than a day
    swaps = extra.get("swaps", [])
    out += _step(next(num), "Ready more than a day — pull these first", "s1b")
    for x in swaps:
        r0 = x["row"]
        who = (r0["pre"] or r0["drv"] or ["nobody"])[0]
        out += _item(
            f"swap|{r0['ref']}", r0["pr"],
            f"<b>{_e(r0['ref'])}</b> at <b>{_e(r0['section'])}</b>{ARROW}READY since {x['since']:%a %m/%d} "
            f"({x['days']} day{'s' if x['days'] != 1 else ''})",
            f"{'Trailer ' + _e(r0['trailer']) + ' · ' if r0['trailer'] else ''}on it: {_e(who)}. "
            "The mill asks for a swap within 24 hours of the request.",
            tone="bad" if x["days"] >= 2 or who == "nobody" else "",
            copy=f"{r0['ref']} {r0['section']}{_trl(r0)} — READY {x['days']}d, PULL FIRST ({who})")
    if not swaps:
        out += '<div class="none">Nothing has been sitting READY since an earlier day.</div>'

    # ---------- 2 drivers
    out += _step(next(num), "Put a driver on these out-of-town loads", "s2")
    used_bh = set()
    for code, r in regions.items():
        for x in r.get("driver_sug", []):
            if not x["who"]:
                continue
            row, l = x["row"], x.get("bh")
            then = ""
            if l:
                used_bh.add(l["ref"])
                then = (f"Then he brings back <b>{_e(l['ref'])}</b> from {_e(l['section'])}"
                        f"{', trailer ' + _e(l['trailer']) if l['trailer'] else ''} — {_miles(x['empty_mi'])} from his drop.")
            c = x.get("cand", {})
            check = []
            if c.get("auth") != "ok":
                check.append(f"not on the driver roster for {r['name']} — confirm he can run it")
            if not c.get("on_today", True):
                check.append("not on today's board yet — confirm he is working")
            lb = leave_by(row, code, C, now)
            out += _item(
                f"drv|{row['ref']}", code,
                f"<b>{_e(x['who'])}</b>{ARROW}load <b>{_e(row['ref'])}</b> to {_e(_drop(row, C))}",
                f"{r['name']} · {_e(_appt_text(row, C))}. {lb} {then}", "; ".join(check),
                copy=f"{row['ref']} — DRIVER: {{who}}" + (f" — BH {l['ref']} {l['section']}{_trl(l)}" if l else ""),
                picks=[x["who"]] + x["alts"])
    if '"drv|' not in out:
        out += '<div class="none">Every out-of-town load that can be covered has a driver.</div>'

    # ---------- local loads with nobody
    loc = extra.get("local", [])
    out += _step(next(num), "Put a name on these local loads", "s2b")
    for x in loc:
        r0 = x["row"]
        pk = r0["cells"][C["pickup"]].splitlines()[0][:36] if r0["cells"][C["pickup"]] else "?"
        if x["who"]:
            out += _item(f"loc|{r0['ref']}", "LOCAL",
                         f"<b>{_e(x['who'])}</b>{ARROW}load <b>{_e(r0['ref'] or '(no number)')}</b> · {_e(r0['section'])}",
                         f"{_e(pk)} to {_e(_drop(r0, C))}. {_e(x['why'])}.",
                         copy=f"{r0['ref']} {r0['section']} — {{who}}", picks=[x["who"]] + x["alts"])
        else:
            out += _item(f"loc|{r0['ref']}|{r0['section']}", "LOCAL",
                         f"Load <b>{_e(r0['ref'] or '(no number)')}</b> · {_e(r0['section'])} has nobody",
                         f"{_e(pk)} to {_e(_drop(r0, C))}. {_e(x['why'])}.", tone="bad",
                         copy=f"{r0['ref']} {r0['section']} — NEEDS A NAME")
    if not loc:
        out += '<div class="none">Every open local load has a name on it.</div>'

    # ---------- 3 backhauls
    out += _step(next(num), "Tell these drivers what to bring back", "s3")
    n3 = 0
    for code, r in regions.items():
        for m in r["matches"]:
            t, l = m["truck"], m["loads"][0]
            if m["how"] == "suggested":
                n3 += 1
                far = (m["empty_mi"] or 0) > 60
                check = []
                if t["auth"] != "ok":
                    check.append("not on the driver roster — confirm he can run it")
                if far:
                    check.append("long empty run — it is the nearest load left")
                out += _item(
                    f"bh|{t['who']}|{l['ref']}", code,
                    f"<b>{_e(t['who'])}</b>{ARROW}bring back <b>{_e(l['ref'])}</b> from {_e(l['section'])}"
                    f"{', trailer ' + _e(l['trailer']) if l['trailer'] else ''}",
                    f"{_miles(m['empty_mi']).capitalize()} from {_e(_drop(t['row'], C))}, where he unloads. "
                    + ("Trailer is READY." if l["ready"] else "Trailer is NOT marked ready yet."),
                    "; ".join(check),
                    copy=f"{l['ref']} {l['section']}{_trl(l)} — BH: {t['who']}")
            elif m.get("nearer"):
                n3 += 1
                d, nl = m["nearer"]
                out += _item(
                    f"sw|{t['who']}|{nl['ref']}", code,
                    f"<b>{_e(t['who'])}</b>{ARROW}switch to <b>{_e(nl['ref'])}</b> from {_e(nl['section'])}"
                    f"{', trailer ' + _e(nl['trailer']) if nl['trailer'] else ''}",
                    f"It is {_miles(d)} from his drop. The board has him on {_e(l['ref'])} at {_e(l['section'])}, "
                    f"{_miles(m['empty_mi'])} away — about {max(0, (m['empty_mi'] or 0) - d):.0f} empty miles saved.",
                    copy=f"{nl['ref']} {nl['section']}{_trl(nl)} — BH: {t['who']} (instead of {l['ref']})")
    if not n3:
        out += '<div class="none">Every truck out of town already has its backhaul on the board.</div>'

    # ---------- 4 problems, each with ways out
    out += _step(next(num), "Problems — with ways to fix each", "s4")
    probs = ""
    stops, away, on_tab = assign.workload(rows)
    for code, r in regions.items():
        left = list(r.get("still_no_truck", r["no_truck"]))
        # who gets back today and may run this region
        back = []
        for who in sorted(away):
            a, _ = P.authority(who, code, table)
            runs = lane_hist["region"][who][code]
            if a == "ok" or (a == "unknown" and runs >= assign.MIN_PROVEN):
                back.append((-runs, who, runs))
        back = [(w, n) for _, w, n in sorted(back)[:8]]
        for k, x in enumerate(x for x in r.get("driver_sug", []) if not x["who"]):
            row = x["row"]
            options = []
            turn = back[k % len(back):] + back[:k % len(back)] if back else []   # a different first name per load
            if turn:
                options.append(
                    f"These drivers are out of town today and know {r['name']}: <b>{_e(turn[0][0])}</b> "
                    f"({turn[0][1]} runs), then {_e(', '.join(w for w, _ in turn[1:4]))}. "
                    "One of them can take it once he is back — check his hours first.")
            near = [(P.deadhead(row, l), l) for l in left]
            near = sorted(((d, l) for d, l in near if d is not None), key=lambda t: t[0])
            if near:
                d, l = near[0]
                left.remove(l)
                options.append(f"Whoever takes it brings back <b>{_e(l['ref'])}</b> from {_e(l['section'])} "
                               f"({_miles(d)} from the drop) — one truck fixes two problems.")
            if re.search(settings.BROKER_SECTIONS, row["section"], re.I):
                options.append("Broker load: if nobody can be found, tell the broker now and ask to move the "
                               "appointment — a late call costs more than an early one.")
            else:
                options.append("Direct customer load: ask the plant for a later delivery window before it goes late.")
            lb = leave_by(row, code, C, now)
            probs += _item(
                f"nodrv|{row['ref']}", code,
                f"Load <b>{_e(row['ref'])}</b> to {_e(_drop(row, C))} has no driver",
                f"{r['name']} · {_e(row['section'])} · {_e(_appt_text(row, C))}. {lb}",
                tone="bad", copy=f"{row['ref']} — DRIVER: {{who}} ({_appt_text(row, C)})", options=options,
                picks=[w for w, _ in turn[:5]] + ["still nobody"] if turn else None)
        if left:
            by = defaultdict(list)
            for l in left:
                by[l["section"]].append(l)
            for sec, ls in by.items():
                pos = ls[0].get("ppos")
                rt = 2 * geo.miles(pos, geo.YARD) if pos else None
                site = next((s for s in pc.get("sites", []) if s["section"].upper() == sec.strip().upper()), None)
                urgent = bool(site and (site["short"] or 0) > 0) or any(l["days"] >= 2 for l in ls)
                options = [
                    f"Book or assign {len(ls)} more load{'s' if len(ls) != 1 else ''} INTO {r['name']} and give "
                    f"{'them these backhauls' if len(ls) != 1 else 'it this backhaul'} — the trip out is then paid for.",
                    (f"Send {len(ls)} truck{'s' if len(ls) != 1 else ''} from the yard with an empty: about "
                     f"{rt:.0f} miles round trip each, half of it empty." if rt else
                     "Send a truck from the yard with an empty."),
                ]
                if r["empty_home"]:
                    options.insert(0, "No truck in this region is free — the ones coming home empty are in other regions.")
                probs += _item(
                    f"notruck|{sec}|{','.join(sorted(l['ref'] for l in ls))}", code,
                    f"<b>{len(ls)}</b> load{'s' if len(ls) != 1 else ''} at <b>{_e(sec)}</b> "
                    f"{'have' if len(ls) != 1 else 'has'} no truck to bring {'them' if len(ls) != 1 else 'it'} home",
                    _e(", ".join(f"{l['ref']}{_trl(l)}" for l in ls))
                    + (" · this customer is already short of trailers or has waited 2+ days — do these first" if urgent else ""),
                    tone="bad", copy=f"{sec} — NO TRUCK: {', '.join(l['ref'] for l in ls)}", options=options)
        for t in r["empty_home"]:
            pos = t["row"].get("dpos")
            options = []
            waiting_nr = [(P.deadhead(t["row"], l), l) for l in r["waiting"] if not l["ready"] and not l["pre"]]
            waiting_nr = sorted(((d, l) for d, l in waiting_nr if d is not None), key=lambda z: z[0])[:2]
            for d, l in waiting_nr:
                options.append(f"<b>{_e(l['ref'])}</b> at {_e(l['section'])} is {_miles(d)} away but not marked READY — "
                               "call the site; if it is loaded he can take it.")
            if pos:
                for d, name, units in spare_near(pos):
                    options.append(f"<b>{_e(name)}</b> is {_miles(d)} from his drop with {len(units)} of our trailers "
                                   f"no open load names ({_e(', '.join(units[:6]))}). Ask if any is loaded.")
            found = extras.offers_for(pos[:2], extra.get("offers", [])) if pos else []
            for o in found:
                rate = f"${o['rate']:,.0f} as emailed" if o["rate"] else "rate not in the email"
                options.append(f"Broker load: <b>{_e(o['source'].upper())} {_e(o['load'])}</b>, {_e(o['origin'])} to "
                               f"{_e(o['dest'])}, {rate}, pickup {_e(o['pickup'] or 'time not given')} — "
                               f"{_miles(o['dead_mi'])} from his drop. Offer seen {_e(o['seen'][5:16].replace('T', ' '))}; "
                               "confirm it is still open before promising the truck.")
            if not found:
                options.append("No emailed broker offer starts near his drop right now.")
            probs += _item(
                f"empty_home|{t['who']}|{t['row']['ref']}", code,
                f"<b>{_e(t['who'])}</b> comes home empty from {r['name']}",
                f"He unloads at {_e(_drop(t['row'], C))} ({_e(t['row']['cells'][C['status']] or 'no status')}). "
                "Nothing READY is left there for him.",
                copy=f"{t['who']} — EMPTY HOME from {r['name']} after {t['row']['ref']}", options=options)
        for t in r["not_allowed"]:
            probs += _item(f"auth|{t['who']}|{t['row']['ref']}", code,
                           f"<b>{_e(t['who'])}</b> is on a {r['name']} load but the roster says {_e(t['auth_text'])}",
                           f"Load {_e(t['row']['ref'])}.", tone="bad",
                           options=["Swap him with a driver allowed to run it, or update the roster if it is out of date."])
    out += probs or '<div class="none">Nothing open.</div>'

    bar = ('<div class="bar noprint"><div class="chips">'
           '<button type="button" class="chip on" data-f="">All</button>'
           + "".join(f'<button type="button" class="chip" data-f="{k}">{_e(v)}</button>'
                     for k, v in P.REGION_NAME.items())
           + '<button type="button" class="chip" data-f="LOCAL">Local</button></div>'
           '<label><input type="checkbox" id="hidedone"> hide what is done</label>'
           '<span id="prog"></span>'
           '<button type="button" id="copyall">Copy my decisions</button>'
           '<button type="button" id="reset">Clear ticks</button></div>')
    return bar + f'<div id="work" data-day="{day:%Y-%m-%d}">' + out + "</div>" + f"<script>{JS}</script>"


CSS = """
.bar{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:12px 0;background:#0d0f13;border-bottom:1px solid #2a2f3a;font-size:13px}
.chips{display:flex;gap:4px}
.bar button,.btns button,.btns select{font:inherit;font-size:13px;color:inherit;background:#1b1f27;border:1px solid #3a4150;border-radius:4px;padding:4px 12px;cursor:pointer}
.bar button:hover,.btns button:hover{border-color:#f5a524}
.chip.on{background:#f5a524;color:#111;border-color:#f5a524}
#prog{margin-left:auto;font-family:ui-monospace,monospace}
.item{display:flex;gap:16px;align-items:flex-start}
.item .main{flex:1;min-width:0}
.item .btns{display:flex;flex-direction:column;gap:8px;flex:none;min-width:112px}
.item.isdone{opacity:.38;border-left-color:#3fb950}
.item.isdone .do{text-decoration:line-through}
.item.isdone button.done{background:#3fb950;color:#111;border-color:#3fb950}
.sol{margin-top:8px;font-size:13px}
.sol>span{font-size:11px;letter-spacing:.08em;text-transform:uppercase;opacity:.6}
.sol ol{margin:4px 0 0 20px;padding:0}
.sol li{margin:4px 0;line-height:1.45}
.hide{display:none!important}
@media (max-width:700px){.item{flex-direction:column}.item .btns{flex-direction:row}}
@media print{.noprint,.btns{display:none!important}.item.isdone{display:none}}
"""

JS = r"""
(function(){
  var work=document.getElementById('work'); if(!work) return;
  var KEY='dispatch-desk-'+work.dataset.day, state={}, filt='';
  try{state=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(state))}catch(e){}}
  function items(){return Array.prototype.slice.call(work.querySelectorAll('.item'))}
  function text(it){var p=it.querySelector('select.pick');return (it.dataset.copy||'').replace('{who}',p?p.value:'')}
  function paint(){
    var hide=document.getElementById('hidedone').checked, done=0, all=0, per={};
    items().forEach(function(it){
      var st=state[it.dataset.id]||{}, p=it.querySelector('select.pick');
      if(p&&st.pick){for(var i=0;i<p.options.length;i++){if(p.options[i].value===st.pick)p.selectedIndex=i}}
      it.classList.toggle('isdone',!!st.done);
      it.querySelector('button.done').textContent=st.done?'Undo':'Done';
      var off=(filt&&it.dataset.region!==filt)||(hide&&st.done);
      it.classList.toggle('hide',!!off);
      all++; if(st.done)done++;
      var s=it.previousElementSibling; while(s&&!s.classList.contains('step'))s=s.previousElementSibling;
      if(s){var k=s.dataset.step; per[k]=per[k]||[0,0,s]; per[k][1]++; if(st.done)per[k][0]++}
    });
    Object.keys(per).forEach(function(k){per[k][2].querySelector('.c').textContent=per[k][0]+' of '+per[k][1]+' done'});
    document.getElementById('prog').textContent=done+' of '+all+' done';
  }
  function copy(t,btn){
    function ok(){var o=btn.textContent;btn.textContent='Copied';setTimeout(function(){btn.textContent=o},1200)}
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(ok,fallback)}else fallback();
    function fallback(){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();try{document.execCommand('copy');ok()}catch(e){}document.body.removeChild(a)}
  }
  work.addEventListener('click',function(e){
    var it=e.target.closest('.item'); if(!it) return; var id=it.dataset.id;
    if(e.target.classList.contains('done')){var st=state[id]||{};st.done=!st.done;st.text=text(it);state[id]=st;save();paint()}
    if(e.target.classList.contains('copy')){copy(text(it),e.target)}
  });
  work.addEventListener('change',function(e){
    if(!e.target.classList.contains('pick'))return; var it=e.target.closest('.item'),st=state[it.dataset.id]||{};
    st.pick=e.target.value;st.text=text(it);state[it.dataset.id]=st;save();
  });
  Array.prototype.forEach.call(document.querySelectorAll('.chip'),function(c){c.addEventListener('click',function(){
    filt=c.dataset.f;Array.prototype.forEach.call(document.querySelectorAll('.chip'),function(x){x.classList.toggle('on',x===c)});paint()})});
  document.getElementById('hidedone').addEventListener('change',paint);
  document.getElementById('copyall').addEventListener('click',function(e){
    var lines=items().filter(function(it){return (state[it.dataset.id]||{}).done}).map(text).filter(Boolean);
    copy(lines.length?lines.join('\n'):'(nothing ticked yet)',e.target)});
  document.getElementById('reset').addEventListener('click',function(){if(confirm('Clear every tick and pick for today?')){state={};save();paint()}});
  paint();
})();
"""
