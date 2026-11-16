r"""HOME — the front page: one block per question, each from data already built.

  Do now            desk.json (built this run by plan.py)
  Customers waiting dispatch mailbox, read-only, cached hourly (mail.py)
  Money waiting     optional JSON feeds other systems publish
                    (settings.MONEY_FEEDS; hidden when none is configured)
  Fleet now         live GPS
  Pages             every page the desk builds, one click away

Every block prints the age of its input. A block whose source is missing says
so instead of showing a stale or empty number as if it were real.
"""
from __future__ import annotations

import html as H
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import page_design as gd           # noqa: E402
from dispatch_desk import mail, settings  # noqa: E402

OUT = settings.OUT_DIR / "DESK"


def _e(s) -> str:
    return H.escape(str(s))


def _age(ts) -> str:
    """'12 min old' from a datetime, an ISO string, or a file path."""
    try:
        if isinstance(ts, Path):
            ts = datetime.fromtimestamp(ts.stat().st_mtime)
        elif isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace(" ", "T")[:16])
        m = (datetime.now() - ts).total_seconds() / 60
    except Exception:
        return "age unknown"
    if m < 90:
        return f"{m:.0f} min old"
    if m < 48 * 60:
        return f"{m / 60:.0f} h old"
    return f"{m / 1440:.0f} days old"


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ago(iso: str) -> str:
    try:
        m = (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 60
    except Exception:
        return ""
    return f"{m:.0f} min ago" if m < 90 else (f"{m / 60:.0f} h ago" if m < 48 * 60 else f"{m / 1440:.0f} days ago")


def block(title: str, src: str, inner: str) -> str:
    return (f'<section class="blk"><div class="bh"><h2>{_e(title)}</h2>'
            f'<span class="src">{_e(src)}</span></div>{inner}</section>')


def stat(value, label, href="", tone="") -> str:
    a, z = (f'<a href="{_e(href)}">', "</a>") if href else ("", "")
    return f'{a}<div class="st {tone}"><b>{_e(value)}</b><span>{_e(label)}</span></div>{z}'


# ---------------------------------------------------------------- blocks
def do_now() -> str:
    d = _json(OUT / "desk.json")
    if not d:
        return block("Do now", "desk.json missing", '<p class="miss">The day plan did not build this run.</p>')
    reg = d["regions"].values()
    short = [p for p in d["pools"] if (p["short"] or 0) > 0]
    nodrv = sum(r["outbound_no_driver"] for r in reg)
    notruck = sum(r["backhauls_no_truck"] for r in reg)
    empty = sum(len(r["trucks_home_empty"]) for r in reg)
    sug = sum(len(r["driver_suggestions"]) + len(r["backhaul_suggestions"]) for r in reg)
    L = d["loads"]
    inner = '<div class="stats">' + "".join([
        stat(L["open"], f"loads open today (of {L['on_tab']})", "DAY_PLAN.html"),
        stat(L["no_name"], "open loads with no name on them", "DRIVER_PACKETS_LATEST.html", "hot" if L["no_name"] else ""),
        stat(len(short), "customers short of trailers", "DAY_PLAN.html", "hot" if short else ""),
        stat(sug, "driver and backhaul suggestions ready", "DAY_PLAN.html", "good" if sug else ""),
        stat(nodrv, "out-of-town loads with no driver", "DAY_PLAN.html", "hot" if nodrv else ""),
        stat(notruck, "backhauls with no truck", "DAY_PLAN.html", "hot" if notruck else ""),
        stat(empty, "trucks coming home empty", "DAY_PLAN.html", "hot" if empty else ""),
    ]) + "</div>"
    if short:
        inner += '<p class="line">' + " · ".join(
            f"<b>{_e(p['customer'])}</b> has {p['there']} of {p['wanted']}" for p in short) + "</p>"
    inner += '<p class="go"><a href="DAY_PLAN.html">Open the day plan and work the list →</a></p>'
    b = d["board"]
    return block("Do now", f"live board, saved {b['saved'][5:].replace('T', ' ')} · {_age(b['saved'])}", inner)


def customers_waiting() -> str:
    m = mail.load()
    if not m["rows"] and m["error"]:
        return block("Customers waiting on a reply", "dispatch mailbox not reachable",
                     f'<p class="miss">{_e(m["error"])}</p>')
    wait = [r for r in m["rows"] if r["waiting"]]
    rows = "".join(
        f'<div class="mrow"><div class="who"><b>{_e(r["org"])}</b><span>{_e(r["from"])} · {_ago(r["when"])}</span></div>'
        f'<div class="what"><b>{_e(r["subject"])}</b><span>{_e(r["line"])}</span></div></div>' for r in wait[:8])
    answered = len(m["rows"]) - len(wait)
    inner = (rows or '<p class="line">Every recent customer email about trailers or service has a reply from us after it.</p>')
    inner += (f'<p class="line dim">{answered} other thread{"s" if answered != 1 else ""} on these topics in the '
              f'last 4 days already {"have" if answered != 1 else "has"} a reply from us. '
              "\"Waiting\" means the last message in the thread is the customer's.</p>")
    src = f"{settings.MAILBOX}, read-only · {_age(m['fetched'])}" + (" · refresh failed, showing last good" if m["error"] else "")
    return block(f"Customers waiting on a reply — {len(wait)}", src, inner)


def on_site() -> str:
    d = _json(OUT / "desk.json")
    if not d:
        return ""
    stops = [s for s in d.get("on_site", []) if s["state"] != "noload"]
    hot = [s for s in stops if s["state"] in ("running", "soon")]
    over = d.get("swap_overdue", [])
    inner = '<div class="stats">' + "".join([
        stat(len([s for s in stops if s["state"] == "running"]), "trucks with detention running now", "DAY_PLAN.html",
             "hot" if any(s["state"] == "running" for s in stops) else ""),
        stat(len([s for s in stops if s["state"] == "soon"]), "detention starting within 45 min", "DAY_PLAN.html",
             "hot" if any(s["state"] == "soon" for s in stops) else ""),
        stat(len(stops), "trucks stopped at a customer", "DAY_PLAN.html"),
        stat(len(over), "trailers READY more than a day", "DAY_PLAN.html", "hot" if over else ""),
    ]) + "</div>"
    for s in hot:
        inner += (f'<p class="line"><b>{_e(s["driver"] or s["unit"])}</b> at {_e(s["site"])} · load {_e(s["ref"])} · '
                  f'{_e(s["say"])}</p>')
    if over:
        worst = sorted(over, key=lambda x: -x["days"])[:4]
        inner += '<p class="line dim">Longest waiting: ' + " · ".join(
            f"{_e(x['load'])} {_e(x['customer'])} ({x['days']}d)" for x in worst) + "</p>"
    return block("On site and overdue", f"live GPS + board · {_age(d['generated'])}", inner)


def drafts_waiting() -> str:
    m = mail.load()
    ds = m.get("drafts")
    if ds is None:
        return ""
    old = [x for x in ds if (datetime.now() - datetime.fromisoformat(x["when"])).total_seconds() > 3600]
    notes = [x for x in old if x["to"].lower().endswith("@" + settings.MAIL_DOMAIN)]     # the system's notes to itself
    old = [x for x in old if x not in notes]
    old.sort(key=lambda x: (not x["detention"], x["when"]))                   # money first, oldest first
    rows = "".join(
        f'<div class="mrow"><div class="who"><b>{_ago(x["when"])}</b><span>to {_e(x["to"])}</span></div>'
        f'<div class="what"><b>{_e(x["subject"])}</b><span>{"detention request" if x["detention"] else "reply"} '
        f'written, never sent</span></div></div>' for x in old[:8])
    det = sum(1 for x in old if x["detention"])
    inner = rows or '<p class="line">No reply has been sitting unsent for more than an hour.</p>'
    if len(old) > 8:
        inner += f'<p class="line dim">and {len(old) - 8} more.</p>'
    if notes:
        inner += (f'<p class="line dim">Not counted: {len(notes)} old drafts addressed to our own mailbox '
                  "(automatic daily notes) — safe to delete.</p>")
    inner += ('<p class="line dim">These are in the Drafts folder of the dispatch mailbox. '
              "Open the mailbox, read it, send it or delete it — the system never sends.</p>")
    return block(f"Written but not sent — {len(old)}" + (f" ({det} detention)" if det else ""),
                 f"{settings.MAILBOX} drafts folder, headers only · {_age(m['fetched'])}", inner)


def tracking() -> str:
    d = _json(OUT / "desk.json")
    t = (d or {}).get("tracking")
    if not t:
        return ""
    if not t["ok"]:
        return block("Loads on the road", "GPS not reachable", '<p class="miss">Tracking could not run this time.</p>')
    c = t["count"]
    blind = c["silent"] + c["notruck"] + c["unplaced"]
    inner = '<div class="stats">' + "".join([
        stat(c["late"], "loads that will miss their time", "TRACKING.html", "hot" if c["late"] else "good"),
        stat(c["tight"], "tight — under 45 min to spare", "TRACKING.html", "hot" if c["tight"] else ""),
        stat(c.get("passed", 0), "past their time, truck on another load — check", "TRACKING.html",
             "hot" if c.get("passed") else ""),
        stat(c["ontime"] + c["there"], "on time or already there", "TRACKING.html", "good"),
        stat(c["waiting"], "not moving yet, still in time", "TRACKING.html"),
        stat(c.get("been", 0), "done, board status not updated", "TRACKING.html"),
        stat(c.get("lastseen", 0), "GPS too old to judge — call the driver", "TRACKING.html",
             "hot" if c.get("lastseen") else ""),
        stat(blind, "the desk cannot see (no truck or tracker)", "TRACKING.html", "hot" if blind else ""),
    ]) + "</div>"
    for x in [l for l in t["loads"] if l["state"] in ("late", "tight")][:6]:
        inner += (f'<p class="line"><b>{_e(x["driver"])}</b> {_e(x["unit"])} · load {_e(x["ref"])} '
                  f'{"to pick up at" if x["leg"] == "pickup" else "to"} {_e(x["place"])} · {_e(x["appt"])} · '
                  f'<b>{_e(x["say"])}</b></p>')
    inner += ('<p class="line dim">Arrival is worked out at 50 mph from the last GPS position, no traffic or stops. '
              '<a href="TRACKING.html">Open tracking →</a></p>')
    return block("Loads on the road", f"live GPS + board · {_age(d['generated'])}", inner)


def trailers() -> str:
    d = _json(OUT / "desk.json")
    if not d or not d.get("pools"):
        return ""
    ps = sorted(d["pools"], key=lambda p: (-(p["short"] or 0), p["customer"]))
    cells = ""
    for p in ps:
        if p["wanted"] is None:
            continue
        short = (p["short"] or 0) > 0
        pct = min(100, round(100 * p["there"] / p["wanted"])) if p["wanted"] else 100
        cells += (f'<div class="pool{" hot" if short else ""}"><b>{_e(p["customer"])}</b>'
                  f'<span class="bar"><i style="width:{pct}%"></i></span>'
                  f'<span>{p["there"]} of {p["wanted"]}{" — send " + str(p["short"]) if short else ""}'
                  f'{"" if p.get("wanted_from", "").startswith("stated") else " (usual)"}</span></div>')
    inner = f'<div class="pools">{cells}</div>'
    inner += ('<p class="line dim">Plain numbers are pool sizes the customer stated. "(usual)" is that customer\'s '
              "normal level from four weeks of GPS; it is flagged only when two or more under. Trailers without a "
              "working tracker are not counted.</p>")
    return block("Trailers at customers", f"live GPS + board · {_age(d['generated'])}", inner)


def ahead() -> str:
    d = _json(OUT / "desk.json")
    if not d or not d.get("week_ahead"):
        return ""
    names = list(d["week_ahead"][0]["regions"])
    head = "<tr><th></th>" + "".join(f"<th>{_e(a['day'][5:].replace('-', '/'))}</th>" for a in d["week_ahead"]) + "</tr>"
    body = ""
    for n in names:
        body += f"<tr><th>{_e(n)}</th>"
        for a in d["week_ahead"]:
            v = a["regions"][n]
            need = max(v["booked"], v["normal"])
            body += (f'<td class="{"hot" if v["gap"] else ""}"><b>{need}</b> loads<br>'
                     f'<span>{v["can_leave"]} can leave{" · SHORT " + str(v["gap"]) if v["gap"] else ""}</span></td>')
        body += "</tr>"
    gaps = sum(1 for a in d["week_ahead"] for v in a["regions"].values() if v["gap"])
    inner = f'<table class="wk">{head}{body}</table>'
    inner += ('<p class="line dim">Loads = the larger of what is already on the board and a normal same weekday. '
              '<a href="WEEK_AHEAD.html">Open the week ahead →</a></p>')
    return block(f"Week ahead — {gaps} region-day{'s' if gaps != 1 else ''} short of drivers",
                 f"future tabs + UPCOMING LOADS + DRIVERS tab · {_age(d['generated'])}", inner)


def people() -> str:
    d = _json(OUT / "desk.json")
    if not d or "papers" not in d:
        return ""
    exp = [p for p in d["papers"] if p["days_left"] < 0]
    soon = [p for p in d["papers"] if p["days_left"] >= 0]
    sc = (d.get("score") or [None])[0]
    stats = [stat(d["roster"]["on_tab"], "drivers on the DRIVERS tab", "ROSTER.html"),
             stat(len(exp), "licence or medical dates already passed", "ROSTER.html", "hot" if exp else ""),
             stat(len(soon), "expiring within 30 days", "ROSTER.html", "hot" if soon else "")]
    for name, n in d.get("can_run", {}).items():
        stats.append(stat(n, f"drivers the desk can send to {name}", "ROSTER.html"))
    inner = '<div class="stats">' + "".join(stats) + "</div>"
    if exp:
        inner += '<p class="line">' + " · ".join(
            f"<b>{_e(p['driver'])}</b> {p['paper']} {_e(p['date'][5:].replace('-', '/'))}" for p in exp) + (
            ' <span class="dim">— dates as typed on the tab; check the folder.</span></p>')
    if sc:
        named = sc["taken"] + sc["near"] + sc["other"]
        inner += (f'<p class="line">Score {_e(sc["day"][5:].replace("-", "/"))}: {sc["suggestions"]} suggestions · '
                  f'{sc["taken"]} first pick used · {sc["near"]} second or third · {sc["other"]} someone else · '
                  f'{sc["open"]} still no name'
                  + (f' · {sc["empty_miles"]} empty backhaul miles ({sc["over_best"]} over the best pairing)'
                     if sc["backhauls_paired"] else "")
                  + f' <a href="SCORE.html">Open the score →</a></p>')
    return block("Drivers and score", f"board DRIVERS tab + desk suggestion log · {_age(d['generated'])}", inner)


def money_waiting() -> str:
    """Optional. Numbers other systems publish as small JSON files, named in
    settings.MONEY_FEEDS: {"pod_chase": path, "finance": path}. Nothing is
    recomputed here, and with no feed configured the block is left off the page."""
    feeds = settings.MONEY_FEEDS or {}
    if not feeds:
        return ""
    cells, srcs = [], []
    pc = _json(Path(feeds["pod_chase"])) if feeds.get("pod_chase") else None
    if pc:
        cells.append(stat(pc.get("open", "?"), "delivery receipts filed, not invoiced"))
        srcs.append(f"receipts feed {_age(pc.get('generated', ''))}")
    fin = _json(Path(feeds["finance"])) if feeds.get("finance") else None
    if fin and fin.get("open_ar"):
        cells.append(stat(f"${fin['open_ar']['total'] / 1000:,.0f}k", f"open receivables as of {fin.get('as_of', '?')}"))
        srcs.append(f"finance feed {_age(fin.get('generated', ''))}")
    if not cells:
        return block("Money waiting", "no source readable", '<p class="miss">None of the money feeds could be read.</p>')
    return block("Money waiting", " · ".join(srcs), '<div class="stats">' + "".join(cells) + "</div>")


def fleet_now() -> str:
    try:
        import fleethunt_api as fh
        dev = fh.positions()
    except Exception as e:
        return block("Fleet now", "GPS not reachable", f'<p class="miss">{_e(type(e).__name__)}</p>')
    tr = [d for d in dev if d.kind == "tractor"]
    tl = [d for d in dev if d.kind == "trailer"]
    moving = sum(1 for d in tr if d.moving)
    dead = [d.unit for d in tr if not d.live]
    inner = '<div class="stats">' + "".join([
        stat(moving, f"trucks moving (of {len(tr)} tracked)", "TRUCKS.html"),
        stat(len(tr) - moving - len(dead), "trucks stopped", "TRUCKS.html"),
        stat(len(dead), "truck trackers silent 6h+", "TRUCKS.html", "hot" if dead else ""),
        stat(len(tl), "trailers with a live tracker", "DAY_PLAN.html"),
    ]) + "</div>"
    if dead:
        inner += f'<p class="line dim">Silent: {_e(", ".join(sorted(dead)[:14]))}</p>'
    return block("Fleet now", "live GPS · just now", inner)


PAGES = [
    ("Dispatch Desk", [("Day plan", "DAY_PLAN.html"), ("Driver packets", "DRIVER_PACKETS_LATEST.html"),
                       ("Tracking", "TRACKING.html"), ("Tomorrow's board", "TOMORROW_DRAFT.html"),
                       ("Week ahead", "WEEK_AHEAD.html"),
                       ("Roster", "ROSTER.html"), ("Trucks (print)", "TRUCKS.html"), ("Truck VIN and plate (print)", "TRUCK_VIN_PLATE.html"), ("Score", "SCORE.html"),
                       ("Empty trips home", "EMPTY_RETURNS.html")]),
] + [(g, list(links)) for g, links in settings.EXTRA_PAGES]      # other dashboards, if any are configured


def pages() -> str:
    out = ""
    for group, links in PAGES:
        chips = "".join(f'<a class="lnk" href="{_e(h)}">{_e(n)}</a>' for n, h in links
                        if (OUT / h).resolve().exists())
        if chips:
            out += f'<div class="grp"><span>{_e(group)}</span>{chips}</div>'
    return block("All pages", "links only", out)


CSS = """
.pools{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.pool{border:1px solid #2a2f3a;border-radius:4px;padding:12px 16px;font-size:12px}
.pool b{display:block;font-size:14px;margin-bottom:8px}
.pool .bar{display:block;height:6px;background:#232833;border-radius:3px;margin-bottom:8px;overflow:hidden}
.pool .bar i{display:block;height:100%;background:#3fb950}
.pool.hot{border-color:#f5a524}.pool.hot .bar i{background:#f5a524}.pool.hot span:last-child{color:#f5a524}
.wk{width:100%;border-collapse:collapse;font-size:12px}
.wk th{text-align:left;font-weight:400;opacity:.6;padding:8px;font-family:ui-monospace,monospace;text-transform:uppercase;font-size:11px}
.wk td{border:1px solid #2a2f3a;padding:8px 12px}.wk td b{font-family:ui-monospace,monospace;font-size:18px}
.wk td span{opacity:.7}.wk td.hot{border-color:#f5a524}.wk td.hot span{color:#f5a524;opacity:1}
.blk{margin:32px 0;border:1px solid #232833;border-radius:6px;padding:20px 24px;background:#12151b}
.bh{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:12px}
.bh h2{margin:0;font-size:20px}
.src{font-family:ui-monospace,monospace;font-size:11px;opacity:.55;text-transform:uppercase;letter-spacing:.04em}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(164px,1fr));gap:12px}
.stats a{color:inherit;text-decoration:none}
.st{border:1px solid #2a2f3a;border-radius:4px;padding:12px 16px;height:100%}
.st:hover{border-color:#f5a524}
.st b{display:block;font-family:ui-monospace,monospace;font-size:28px;line-height:1.2}
.st span{font-size:12px;opacity:.72}
.st.hot b{color:#f5a524}.st.good b{color:#3fb950}
.line{font-size:14px;margin:12px 0 0}.dim{opacity:.6;font-size:12px}
.go a{color:#f5a524;font-weight:600;text-decoration:none}
.miss{color:#ff8a80;font-size:14px}
.mrow{display:grid;grid-template-columns:220px 1fr;gap:16px;padding:12px 0;border-top:1px solid #232833}
.mrow:first-of-type{border-top:0}
.mrow b{display:block;font-size:14px}.mrow span{display:block;font-size:12px;opacity:.7;margin-top:2px}
.grp{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.grp>span{width:168px;font-size:12px;opacity:.6}
.lnk{border:1px solid #2a2f3a;border-radius:4px;padding:4px 12px;font-size:13px;color:inherit;text-decoration:none}
.lnk:hover{border-color:#f5a524}
@media (max-width:700px){.mrow{grid-template-columns:1fr}.grp>span{width:100%}}
@media print{.blk{background:none;border-color:#999}}
"""


def build() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    # a stable name for "today's packets", so links never point at yesterday
    packs = sorted(OUT.glob("DRIVER_PACKETS_[0-9]*.html"), key=lambda p: p.stat().st_mtime)
    if packs:
        (OUT / "DRIVER_PACKETS_LATEST.html").write_bytes(packs[-1].read_bytes())
    body = (do_now() + tracking() + on_site() + trailers() + ahead() + customers_waiting() + drafts_waiting()
            + people() + money_waiting() + fleet_now() + pages())
    page = gd.page(title=f"{settings.COMPANY} dispatch — today", eyebrow=settings.EYEBROW,
                   sub="What needs doing, who is waiting on us, and where every truck is. "
                       "Suggestions only — nothing here writes the board or sends anything.",
                   source="dispatch desk: board workbook, live GPS, dispatch mailbox",
                   body=body, extra_css=CSS, refresh=300)
    out = OUT / "INDEX.html"
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(f"[desk] home: {build()}")
