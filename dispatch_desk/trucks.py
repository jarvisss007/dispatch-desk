r"""TRUCKS — every tractor with its details, on one sheet that prints.

  identity     unit, type, year, make, model, VIN, plate, owned or leased
               -> equipment_list.build_rows() (a separate module; it is only
                  CALLED here, read-only)
  who drives   the name the board most often puts with that unit in the TRK
               cell over the last 30 daily tabs
  right now    live GPS: odometer, tracker state, where it is

Dark on screen, white on paper (landscape). Nothing here is typed in by hand:
a blank cell means no source has that fact.
"""
from __future__ import annotations

import html as H
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dispatch_desk import geo, pools, settings  # noqa: E402

_UNIT = re.compile(settings.UNIT_PATTERN, re.I)

CSS = """
.tk td,.tk th{font-size:12px;padding:6px 8px;vertical-align:top}
.tk td small{display:block;opacity:.65}
.tk .mono{font-family:ui-monospace,monospace}
.bar{display:flex;gap:12px;align-items:center;margin:16px 0}
.bar button{font:inherit;padding:8px 16px;border:1px solid #f5a524;background:transparent;color:#f5a524;border-radius:4px;cursor:pointer}
.warn{color:#f5a524}
@page{size:landscape;margin:8mm}
@media print{.bar{display:none}.tk td,.tk th{font-size:9.5px;padding:3px 5px}.warn{color:#000;font-weight:700}
  h2{margin:12px 0 4px;font-size:13px} tr{break-inside:avoid}}
"""


def _norm(u: str) -> str:
    return re.sub(r"[\s-]", "", u).upper()


def usual_drivers(days: list[list[dict]], C: dict, same=None) -> dict[str, list[tuple[str, int]]]:
    """unit -> [(name, tabs it was his), ...] most frequent first. A row counts
    only when it names exactly one truck and one man."""
    same = same or (lambda w: w)
    seen: dict[str, Counter] = defaultdict(Counter)
    for rows in days:
        today = set()
        for r in rows:
            units = {_norm(u) for u in _UNIT.findall(r["cells"][C["trk"]])}
            people = {same(w) for w in (r["drv"] or r["pre"])}
            if len(units) == 1 and len(people) == 1:
                today.add((next(iter(units)), next(iter(people))))
        for u, w in today:
            seen[u][w] += 1
    return {u: c.most_common(3) for u, c in seen.items()}


def _where(d, fences) -> str:
    if abs(d.lat - geo.YARD[0]) < 0.02 and abs(d.lng - geo.YARD[1]) < 0.02:
        return "our yard"
    for f in fences:
        try:
            if pools._mi(float(f["lat"]), float(f["lng"]), d.lat, d.lng) <= max(
                    pools.MIN_RADIUS_MI, float(f.get("radius_m") or 0) / 1609.34):
                return f["name"].strip('" ').title()
        except (KeyError, TypeError, ValueError):
            continue
    best = None
    for name, pos in geo._cities().items():
        mi = geo.miles((d.lat, d.lng), pos)
        if best is None or mi < best[1]:
            city, _, st = name.partition(",")
            best = (f"{city.title()},{st.upper()}", mi)
    return f"near {best[0]}" if best and best[1] < 40 else f"{d.lat:.3f}, {d.lng:.3f}"


def build(days: list[list[dict]], C: dict, same, out: Path, gd, source_note: str) -> dict:
    e = H.escape
    try:
        import equipment_list
        spec = equipment_list.build_rows()
        tractors, pulled = spec["tractors"], spec.get("pulled", "")
    except Exception as ex:                                  # the list must not sink the plan
        out.write_text(gd.page(title="Trucks", body=gd.callout(
            "The truck list could not be built this run.", f"{type(ex).__name__}: {str(ex)[:120]}"),
            source="equipment list", extra_css=CSS), encoding="utf-8")
        return {"ok": False, "n": 0}
    try:
        import fleethunt_api as fh
        live = {_norm(d.unit): d for d in fh.positions() if d.kind == "tractor"}
        fences = fh.fetch_pois()
    except Exception:
        live, fences = {}, []
    who = usual_drivers(days, C, same)
    groups: dict[str, list] = defaultdict(list)
    for t in tractors:
        groups[t.get("group") or "TRACTOR"].append(t)
    body = ('<div class="bar"><button onclick="window.print()">Print this list</button>'
            '<span class="dim">Prints white, landscape. '
            f'{len(tractors)} trucks.</span></div>')
    silent = 0
    for g in sorted(groups):
        rows = []
        for t in sorted(groups[g], key=lambda x: (re.sub(r"\d", "", x["name"]), int(re.sub(r"\D", "", x["name"]) or 0))):
            d = live.get(_norm(t["name"]))
            drv = who.get(_norm(t["name"]), [])
            if d is None:
                gps = '<span class="warn">no tracker</span>'
                where, odo = "—", t.get("miles") or "—"
            else:
                quiet = d.age_min > 360
                silent += quiet
                gps = ('<span class="warn">tracker never reported</span>' if d.age_min > 5e5 else
                       f'<span class="warn">silent {d.age_min / 1440:.0f} days</span>' if d.age_min > 2880 else
                       f'<span class="warn">silent {d.age_min / 60:.0f} h</span>' if quiet
                       else ("moving" if d.moving else "stopped"))
                where = e(_where(d, fences)) + (" (last known)" if quiet else "")
                odo = f"{d.odometer:,.0f}" if d.odometer else "—"
            rows.append([
                f'<b class="mono">{e(t["name"])}</b>',
                f'{e(t.get("year", ""))} {e(t.get("make", ""))} {e(t.get("model", "")).title()}',
                f'<span class="mono">{e(t.get("vin", "") or "—")}</span>',
                f'<span class="mono">{e(t.get("plate", "") or "—")}</span>'
                + (f' <small>{e(t["state"])}</small>' if t.get("state") else ""),
                e(t.get("own", "") or "—"),
                (f'<b>{e(drv[0][0].title())}</b><small>{drv[0][1]} of the last 30 days'
                 + (" · also " + ", ".join(e(n.title()) for n, _ in drv[1:]) if len(drv) > 1 else "") + "</small>")
                if drv else '<span class="dim">not on the board lately</span>',
                odo, gps, where])
        body += f"<h2>{e(g.title())}s — {len(rows)}</h2>" + '<div class="tk">' + gd.table(
            ["Unit", "Year, make, model", "VIN", "Plate", "Owned", "Usual driver (from the board)",
             "GPS miles", "Tracker", "Where now"], rows, ["l", "l", "l", "l", "l", "l", "n", "l", "l"]) + "</div>"
    body += gd.callout(
        "Where each fact comes from.",
        f"Year, make, VIN, plate and ownership: the equipment list "
        f"(vehicle records pulled {pulled[:10] or 'date unknown'}). Usual driver: the name the board's TRK cell most often "
        "puts with that truck. Odometer and position: live GPS at the time printed above; GPS miles count "
        "from the day the tracker was fitted, so they are lower than the dash odometer — read the dash for service.  A blank means no source has it.")
    short_rows = [[f'<b class="mono">{e(t["name"])}</b>', e(t.get("year", "") or "—"), e(t.get("make", "") or "—"),
                   e((t.get("model", "") or "—").title()), f'<span class="mono">{e(t.get("vin", "") or "—")}</span>',
                   f'<span class="mono">{e(t.get("plate", "") or "—")}</span>']
                  for g in sorted(groups)
                  for t in sorted(groups[g], key=lambda x: (re.sub(r"\d", "", x["name"]), int(re.sub(r"\D", "", x["name"]) or 0)))]
    (out.parent / "TRUCK_VIN_PLATE.html").write_text(gd.page(
        title="Trucks — year, make, model, VIN and plate", eyebrow=settings.EYEBROW,
        sub=f"{len(short_rows)} trucks.",
        source=f"equipment list (vehicle records pulled {pulled[:10] or 'date unknown'})",
        body='<div class="bar"><button onclick="window.print()">Print this list</button></div><div class="tk one">'
             + gd.table(["Truck", "Year", "Make", "Model", "VIN", "Plate"], short_rows, ["l"] * 6) + "</div>",
        extra_css=CSS + ".one td,.one th{font-size:13px}@media print{@page{size:portrait;margin:9mm}"
                        ".one td,.one th{font-size:10.5px;padding:2.5px 6px}h1{font-size:18px;margin:0}}",
        refresh=0), encoding="utf-8")
    out.write_text(gd.page(
        title="Trucks — every tractor and its details", eyebrow=settings.EYEBROW,
        sub="One line per truck: what it is, who usually drives it, and where it is now.",
        source=source_note, body=body, extra_css=CSS, refresh=0), encoding="utf-8")
    return {"ok": True, "n": len(tractors), "tracked": len(live), "silent": silent,
            "built": datetime.now().isoformat(timespec="minutes")}
