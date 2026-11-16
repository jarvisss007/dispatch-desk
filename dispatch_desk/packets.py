r"""DRIVER PACKETS — the paper a driver picks up at the office, one page each.

From one day's board tab: every row a driver is named on, as PRELOADER
(shipper -> yard) or DRIVER (yard -> receiver), in the order he will run them,
with full addresses, appointments, trailer and the dispatcher's notes — plus
blank ARRIVED / LEFT boxes for him to write his gate times in. Those
handwritten times are the evidence detention claims need (direct customers'
claims run on site times, not GPS).

It reads the board exactly as written. It does not assign anyone, does not
message anyone, and a row with no name on it goes on the office's
"not assigned" list, not on a packet.

    py -m dispatch_desk.packets                # today's tab
    py -m dispatch_desk.packets 2026-11-11     # a specific day's tab

Writes DRIVER_PACKETS_<m.d>.html to the output folder (dark on screen, white
on paper, one driver per printed page).
"""
from __future__ import annotations

import html as H
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import board                      # noqa: E402
import page_design as gd          # noqa: E402
from dispatch_desk import settings, source  # noqa: E402
from dispatch_desk.tomorrow import C, daily, read_tab, year_of  # noqa: E402

OUT = settings.OUT_DIR / "DESK"
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([AP])\.?M\b", re.I)
_DAYREF = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
_BH = re.compile(r"\bBH\b|BACK ?HAUL", re.I)


def norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().upper()


def split_names(cell: str, sections: set[str]) -> list[str]:
    """Names in a PRELOADER/DRIVER cell. 'MARCO/ DELROY' is two people; '?' or
    a shipper name typed in the wrong column is nobody."""
    out = []
    for part in re.split(r"\s*/\s*", norm_name(cell)):
        if part and re.search(r"[A-Z]{2}", part) and part not in sections:
            out.append(part)
    return out


def lookalikes(names: list[str]) -> list[tuple[str, str]]:
    """Pairs of names that are probably one person spelled two ways. Listed
    for the office to fix on the board — NOT merged here: DARNELL and
    DARRELL look just as alike and are two different drivers."""
    import difflib
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if difflib.SequenceMatcher(None, a, b).ratio() >= 0.85:
                out.append((a, b))
    return out


def _minutes(text: str) -> int | None:
    m = _TIME.search(text or "")
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "P" else 0)
    return h * 60 + int(m.group(2) or 0)


def _sort_key(leg: dict, day: date) -> tuple:
    """Run order: the truck's leave time if the board gives one, else the
    appointment. An appointment dated another day sorts by that day."""
    text = leg["appt"]
    off = 0
    m = _DAYREF.search(text)
    if m:
        try:
            off = (date(day.year, int(m.group(1)), int(m.group(2))) - day).days
        except ValueError:
            off = 0
    mins = _minutes(leg["trk"])
    if mins is None:
        mins, known = _minutes(text), True
    else:
        off, known = 0, True
    if mins is None:
        return (1, 0, 0)            # no time anywhere -> bottom, board order
    return (0, off, mins)


def legs_for_tab(loads: list[dict], day: date) -> tuple[dict[str, list[dict]], list[dict]]:
    by_driver: dict[str, list[dict]] = {}
    unassigned = []
    sections = {norm_name(l["section"]) for l in loads if l["section"]}
    for n, l in enumerate(loads):
        if l["stage"] == board.Stage.CANCELED:
            continue
        c = l["cells"]
        pres, drvs = split_names(c[C["preloader"]], sections), split_names(c[C["driver"]], sections)
        pu_appt = " · ".join(r[C["pickup"]] for r in l["cont"] if r[C["pickup"]])
        del_appt = " · ".join(r[C["delivery"]] for r in l["cont"] if r[C["delivery"]])
        base = {"ref": l["ref"], "section": l["section"], "pickup": c[C["pickup"]],
                "delivery": c[C["delivery"]], "dnum": c[C["delivery_no"]],
                "trk": c[C["trk"]], "status": c[C["status"]], "note": c[C["note"]],
                "stage": l["stage"], "n": n, "pu_appt": pu_appt, "del_appt": del_appt,
                "shared": ""}
        bh = bool(_BH.search(c[C["trk"]]))
        if not pres and not drvs:
            if l["stage"] != board.Stage.DELIVERED:
                unassigned.append({**base, "appt": del_appt or pu_appt})
            continue
        for who in pres:
            others = [x for x in pres if x != who]
            shared = f"board says {' / '.join(pres)} — check who takes it" if others else ""
            if who in drvs:
                by_driver.setdefault(who, []).append(
                    {**base, "do": "PICK UP & DELIVER", "appt": pu_appt or del_appt, "shared": shared})
            else:
                by_driver.setdefault(who, []).append(
                    {**base, "do": "BACKHAUL — PICK UP" if bh else "PICK UP → YARD",
                     "appt": pu_appt, "trk": "" if (bh or drvs) else base["trk"], "shared": shared})
        for who in drvs:
            if who in pres:
                continue
            others = [x for x in drvs if x != who]
            by_driver.setdefault(who, []).append(
                {**base, "do": "DELIVER", "appt": del_appt,
                 "shared": f"board says {' / '.join(drvs)} — check who takes it" if others else ""})
    for legs in by_driver.values():
        legs.sort(key=lambda g: (_sort_key(g, day), g["n"]))
    return by_driver, unassigned


# ---------------------------------------------------------------- page
_CSS = """
.idx{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 32px}
.idx a{border:1px solid #2a2f3a;padding:4px 12px;border-radius:4px;text-decoration:none;color:inherit;font-size:13px}
.idx a b{font-family:ui-monospace,monospace;opacity:.7;margin-left:4px}
.packet{border:1px solid #2a2f3a;border-radius:4px;padding:24px;margin:24px 0}
.packet h2{margin:0;font-size:28px;letter-spacing:.02em}
.packet .when{opacity:.7;font-size:13px;margin:4px 0 16px}
.packet table{width:100%;border-collapse:collapse}
.packet td,.packet th{vertical-align:top;padding:8px;border-bottom:1px solid #2a2f3a;font-size:13px;text-align:left}
.packet td.do{font-weight:700;white-space:nowrap}
.packet td .ref{font-family:ui-monospace,monospace;font-size:15px;font-weight:700}
.packet td small{display:block;opacity:.7}
.packet td.box{width:76px;border-left:1px solid #2a2f3a}
.packet tr.done td{opacity:.45}
.packet .sign{display:flex;gap:32px;margin-top:24px;font-size:12px;opacity:.8}
.packet .sign span{flex:1;border-top:1px solid currentColor;padding-top:4px}
.noprint{}
@media print{
  @page{size:letter landscape;margin:10mm}
  .noprint,.eyebrow,h1,.sub,footer,.tiles,.callout{display:none!important}
  .wrap{max-width:none!important;padding:0!important}
  .packet{border:0;padding:0;margin:0;page-break-after:always;break-after:page}
  .packet:last-child{page-break-after:auto}
  .packet td,.packet th{border-color:#999;font-size:11px;padding:5px}
  .packet td.box{border-left:1px solid #999;height:34px}
  .packet tr{page-break-inside:avoid}
  .packet tr.done td{opacity:.6}
}
"""


def _ml(s: str) -> str:
    """Multi-line cell: first line (the name) strong, the address under it."""
    lines = [x.strip() for x in (s or "").split("\n") if x.strip()]
    if not lines:
        return "—"
    head = H.escape(lines[0])
    rest = H.escape(", ".join(lines[1:]))
    return f"<b>{head}</b>" + (f"<small>{rest}</small>" if rest else "")


def _anchor(name: str) -> str:
    return "d-" + re.sub(r"[^A-Z0-9]+", "-", name)


def packet_html(name: str, legs: list[dict], day: date, src: str) -> str:
    trs = []
    for i, g in enumerate(legs, 1):
        done = g["stage"] == board.Stage.DELIVERED and g["do"] != "PICK UP → YARD"
        note = " · ".join(x for x in (g["note"].replace("\n", " "), g["dnum"].replace("\n", " ")) if x)
        trs.append(
            f'<tr class="{"done" if done else ""}"><td>{i}</td>'
            f'<td class="do">{H.escape(g["do"])}<small>{H.escape(g["status"]) or "&nbsp;"}</small></td>'
            f'<td><span class="ref">{H.escape(g["ref"] or "—")}</span><small>{H.escape(g["section"])}</small></td>'
            f"<td>{_ml(g['pickup'])}</td><td>{_ml(g['delivery'])}</td>"
            f"<td>{H.escape(g['appt']) or '—'}</td>"
            f"<td>{H.escape(g['trk']) or '—'}</td>"
            f"<td>{H.escape(note) or '—'}</td>"
            '<td class="box"></td><td class="box"></td></tr>')
    return (
        f'<section class="packet" id="{_anchor(name)}">'
        f"<h2>{H.escape(name)}</h2>"
        f'<div class="when">{day:%A} {day.month}/{day.day}/{day.year} · {len(legs)} '
        f'stop{"s" if len(legs) != 1 else ""} · from the board as of {H.escape(src)}</div>'
        "<table><thead><tr><th>#</th><th>Do</th><th>Load</th><th>Pick up at</th><th>Deliver to</th>"
        "<th>Appointment</th><th>Leave / truck</th><th>Trailer &amp; notes</th>"
        "<th>Arrived</th><th>Left</th></tr></thead><tbody>" + "".join(trs) + "</tbody></table>"
        '<div class="sign"><span>Driver signature</span><span>Truck #</span>'
        "<span>Write your ARRIVED and LEFT times at every stop — that is how detention gets paid.</span></div>"
        "</section>")


def build_page(day: date, tab: str, by_driver: dict, unassigned: list, info: dict) -> str:
    names = sorted(by_driver)
    src = f"{info['saved']:%a %m/%d %I:%M %p}"
    stops = sum(len(v) for v in by_driver.values())
    body = ""
    if info["stale"]:
        body += gd.callout("The board has not been saved in over 12 hours.",
                           source.age_text(info) + " — these packets may be out of date.")
    body += gd.tiles([
        (len(names), "drivers with a packet"),
        (stops, "stops on paper"),
        (len(unassigned), "open loads with no name on them", "hot" if unassigned else ""),
    ])
    body += ('<div class="idx noprint">'
             + "".join(f'<a href="#{_anchor(n)}">{H.escape(n)}<b>{len(by_driver[n])}</b></a>' for n in names)
             + "</div>")
    twins = lookalikes(names)
    if twins:
        body += ('<div class="noprint">' + gd.callout(
            "Same driver spelled two ways? " + " · ".join(f"{a} / {b}" for a, b in twins),
            "Each spelling gets its own packet. Not merged automatically — some look-alike names are "
            "different people. Fix the spelling on the board and re-run.") + "</div>")
    if unassigned:
        rows = [[f'<span class="mono">{H.escape(u["ref"] or "—")}</span>', H.escape(u["section"]),
                 _ml(u["pickup"]), _ml(u["delivery"]), H.escape(u["appt"]) or "—",
                 H.escape(u["status"]) or "—", H.escape(u["note"].replace(chr(10), " ")) or "—"]
                for u in unassigned]
        body += ('<div class="noprint"><h3>Not assigned yet — these are on nobody\'s paper</h3>'
                 + gd.table(["Load", "Section", "Pick up", "Deliver", "Appointment", "Status", "Note"], rows)
                 + "</div>")
    body += "".join(packet_html(n, by_driver[n], day, src) for n in names)
    return gd.page(
        title=f"Driver packets — {day:%A} {day.month}/{day.day}",
        eyebrow=settings.EYEBROW,
        sub=("One page per driver, straight off the board. Print (Ctrl+P) gives white pages, "
             "one driver each. Names are as the board writes them."),
        source=f"live board tab {tab} · {source.age_text(info)}",
        body=body, extra_css=_CSS)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wb, info = source.open_live()
    tabs = daily(wb, year_of(info))
    day = date.fromisoformat(args[0]) if args else max(
        (d for _, d in tabs if d <= date.today()), default=tabs[-1][1])
    tab = next((n for n, d in tabs if d == day), None)
    if tab is None:
        print(f"[desk] the board has no tab for {day:%m/%d} yet — newest is {tabs[-1][0].strip()}")
        return 1
    by_driver, unassigned = legs_for_tab(read_tab(wb[tab]), day)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"DRIVER_PACKETS_{day.month}.{day.day}.html"
    out.write_text(build_page(day, tab.strip(), by_driver, unassigned, info), encoding="utf-8")
    print(f"[desk] {len(by_driver)} driver packets, {sum(map(len, by_driver.values()))} stops, "
          f"{len(unassigned)} open loads unassigned — tab {tab.strip()}, {source.age_text(info)}")
    print(f"[desk] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
