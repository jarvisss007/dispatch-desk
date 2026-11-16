r"""TOMORROW'S BOARD, DRAFTED — carry the open loads forward so nobody retypes them.

What the office does by hand every afternoon: copy every load that is not
delivered onto the next day's tab (about 80 rows a weekday), then add what is
new. This drafts that tab:

  1. CARRY  — every load whose most recent row in the last few tabs is neither
              DELIVERED nor CANCELED, cells kept exactly as the dispatcher
              left them, under its own shipper section.
  2. NEW    — rows on the UPCOMING LOADS tab whose first date is the target day.

Output (the desk's output folder):
  TOMORROW_DRAFT.html          the page (dark; prints white)
  TOMORROW_DRAFT_<m.d>.xlsx    sheet 1 is laid out like a board tab, ready to
                               copy into the real workbook; sheet 2 is the why.

It never writes the board. A human copies the rows over, or doesn't.

    py -m dispatch_desk.tomorrow                 # draft the day after the newest tab
    py -m dispatch_desk.tomorrow 2026-11-11      # draft a specific day
    py -m dispatch_desk.tomorrow --backtest      # how the carry rule did on past days
"""
from __future__ import annotations

import html as H
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import board                      # noqa: E402  (row rules only — read, never edited)
import page_design as gd          # noqa: E402
from dispatch_desk import settings, source  # noqa: E402

OUT = settings.OUT_DIR / "DESK"
HEADERS = ["LOAD", "PICKUP", "DELIVERY", "DELIVERY#", "PRELOADER", "DRIVER",
           "TRK", "STATUS", "NOTE", "RATE"]
C = board.COL
LOOKBACK_TABS = 4       # most tabs a window can span (Fri+Sat+Sun before a Monday)
CLOSED = (board.Stage.DELIVERED, board.Stage.CANCELED)
_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")


# ---------------------------------------------------------------- reading
def _raw(row) -> list[str]:
    out = ["" if c is None else str(c).strip() for c in row[:board.NUM_COLS]]
    return out + [""] * (board.NUM_COLS - len(out))


def read_tab(ws) -> list[dict]:
    """Loads on one tab, raw cell text kept (newlines and all)."""
    header = board._find_header_row(ws)
    loads, section, cur = [], "", None
    for row in ws.iter_rows(min_row=header + 1, max_col=board.NUM_COLS, values_only=True):
        vals = board._row_values(row)
        if not any(vals):
            continue
        if board._is_section(vals):
            section, cur = _raw(row)[0], None
            continue
        if board._is_continuation(vals) and cur is not None:
            cur["cont"].append(_raw(row))
            continue
        cur = {"section": section, "cells": _raw(row), "cont": [],
               "ref": vals[C["load"]], "stage": board.classify_status(vals[C["status"]])}
        loads.append(cur)
    return loads


def key(l: dict) -> str:
    """Identity of a row across days. Rows without a load number (yard moves)
    are keyed by their text so they carry too, but never collide."""
    return l["ref"] or "~" + "|".join(l["cells"][1:4])[:80]


def daily(wb, year: int) -> list[tuple[str, date]]:
    return sorted(board.daily_tabs(wb, year), key=lambda t: t[1])


def year_of(info: dict) -> int:
    # The book is named for the month it STARTED; its tabs run on. Tabs carry
    # no year, so use the save year (a Dec->Jan book would need a look here).
    return info["saved"].year


# ---------------------------------------------------------------- the rule
def window_for(read: list[tuple[str, date, list[dict]]]) -> list:
    """Which past tabs feed the next one: back to the most recent WEEKDAY tab.
    Tue-Fri that is just yesterday; a Monday is built from Friday plus the
    small weekend tabs. Looking further back re-carries loads the office had
    already dropped (back-test: 83% kept with a flat 4-tab window)."""
    out = []
    for item in reversed(read[-LOOKBACK_TABS:]):
        out.insert(0, item)
        if item[1].weekday() < 5:
            break
    return out


def carry_set(tabs: list[tuple[str, date, list[dict]]]) -> list[dict]:
    """Open loads as of the last tab given: latest row per load across the
    lookback window, kept if that row is not delivered/canceled. Order and
    sections follow the newest tab first, then older stragglers."""
    latest: dict[str, dict] = {}
    for name, d, loads in tabs:                 # oldest -> newest, newest wins
        for l in loads:
            latest[key(l)] = {**l, "from_tab": name.strip(), "from_date": d}
    newest_first = sorted(latest.values(), key=lambda l: l["from_date"], reverse=True)
    return [l for l in newest_first if l["stage"] not in CLOSED]


def upcoming_for(wb, target: date) -> list[dict]:
    if "UPCOMING LOADS" not in wb.sheetnames:
        return []
    out = []
    for l in read_tab(wb["UPCOMING LOADS"]):
        if not re.search(r"\d{5,}", l["ref"]):
            continue                            # the tab also holds unrelated lists
        m = _DATE.search(" ".join(c for row in l["cont"] for c in row))
        if m and (int(m.group(1)), int(m.group(2))) == (target.month, target.day):
            out.append({**l, "from_tab": "UPCOMING", "from_date": target, "new": True})
    return out


def first_seen(all_tabs) -> dict[str, date]:
    seen: dict[str, date] = {}
    for name, d, loads in all_tabs:
        for l in loads:
            seen.setdefault(key(l), d)
    return seen


def build(wb, info: dict, target: date | None = None) -> dict:
    tabs = daily(wb, year_of(info))
    if target is None:
        target = tabs[-1][1] + timedelta(days=1)
    before = [(n, d) for n, d in tabs if d < target]
    all_read = [(n, d, read_tab(wb[n])) for n, d in before[-45:]]
    window = window_for(all_read)
    carried = carry_set(window)
    seen = first_seen(all_read)
    have = {key(l) for l in carried}
    new = [l for l in upcoming_for(wb, target) if key(l) not in have]
    rows = []
    for l in carried + new:
        fs = seen.get(key(l))
        l = {**l, "days": (target - fs).days if fs else 0, "flag": ""}
        if l.get("new"):
            l["flag"] = "NEW — from UPCOMING LOADS"
        elif l["stage"] in (board.Stage.TO_CONSIGNEE, board.Stage.AT_CONSIGNEE):
            l["flag"] = "Was out for delivery — confirm it isn't already delivered"
        elif l["from_date"] < window[-1][1]:
            l["flag"] = f"Last seen on {l['from_tab']}, not on the newest tab"
        rows.append(l)
    # group by section, sections in first-appearance order
    order: list[str] = []
    for l in rows:
        if l["section"] not in order:
            order.append(l["section"])
    rows.sort(key=lambda l: order.index(l["section"]))
    existing = next((n for n, d in tabs if d == target), None)
    return {"target": target, "rows": rows, "base_tab": window[-1][0].strip(),
            "base_date": window[-1][1], "info": info, "existing_tab": existing}


# ---------------------------------------------------------------- back-test
def backtest(wb, info: dict, days: int = 30) -> list[dict]:
    """For each past day: would the rule have predicted that day's tab?
    kept = share of what we'd carry that the dispatcher really carried;
    covered = share of the real tab we'd have pre-filled."""
    tabs = daily(wb, year_of(info))
    read = [(n, d, read_tab(wb[n])) for n, d in tabs[-(days + LOOKBACK_TABS):]]
    out = []
    for i in range(LOOKBACK_TABS, len(read)):
        name, d, actual = read[i]
        pred = {key(l) for l in carry_set(window_for(read[:i]))}
        act = {key(l) for l in actual}
        if not act:
            continue
        out.append({"tab": name.strip(), "date": d, "predicted": len(pred),
                    "actual": len(act), "hit": len(pred & act)})
    return out


def backtest_summary(bt: list[dict]) -> dict:
    def agg(rows):
        p, a, h = (sum(r[k] for r in rows) for k in ("predicted", "actual", "hit"))
        return {"days": len(rows), "kept": h / p if p else 0, "covered": h / a if a else 0}
    wk = [r for r in bt if r["date"].weekday() < 5]
    we = [r for r in bt if r["date"].weekday() >= 5]
    return {"weekday": agg(wk), "weekend": agg(we)}


# ---------------------------------------------------------------- outputs
def write_xlsx(res: dict, path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    t = res["target"]
    ws.title = f"{t.month}.{t.day}"
    ws.append([f"{t:%A}".upper() + f" {t.month}/{t.day}"])
    ws.append([])
    ws.append(HEADERS)
    ws.append([])
    bold = Font(bold=True)
    sect = PatternFill("solid", fgColor="D9D9D9")
    for c in ws[3]:
        c.font = bold
    ws["A1"].font = Font(bold=True, size=14)
    section = None
    for l in res["rows"]:
        if l["section"] != section:
            section = l["section"]
            ws.append([section])
            for c in ws[ws.max_row]:
                c.font, c.fill = bold, sect
            for col in range(2, 11):
                ws.cell(ws.max_row, col).fill = sect
        ws.append(l["cells"])
        for cont in l["cont"]:
            ws.append(cont)
    for col, w in zip("ABCDEFGHIJ", (14, 30, 30, 26, 14, 14, 12, 26, 18, 14)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=5):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    why = wb.create_sheet("WHY")
    why.append([f"Drafted {datetime.now():%m/%d %I:%M %p} from {source.age_text(res['info'])}"])
    why.append(["LOAD", "SECTION", "CARRIED FROM TAB", "STATUS THERE", "DAYS ON BOARD", "CHECK"])
    for c in why[2]:
        c.font = bold
    for l in res["rows"]:
        why.append([l["ref"] or "(no load #)", l["section"], l["from_tab"],
                    l["cells"][C["status"]], l["days"], l["flag"]])
    for col, w in zip("ABCDEF", (16, 26, 18, 28, 14, 56)):
        why.column_dimensions[col].width = w
    wb.save(path)


_CSS = """
.sec td{background:#1b1f27;font-weight:600;letter-spacing:.04em;font-size:12px}
td.flag{color:var(--accent,#f5a524);font-size:12px}
td small{opacity:.65;display:block}
.stale{border:1px solid #c0392b;color:#ff8a80;padding:12px 16px;margin:16px 0;border-radius:4px}
@media print{.sec td{background:#eee}}
"""


def _one_line(s: str) -> str:
    return H.escape(re.sub(r"\s*\n\s*", " · ", s))


def write_html(res: dict, bt: dict, path: Path, xlsx_name: str) -> None:
    rows, t = res["rows"], res["target"]
    carried = [l for l in rows if not l.get("new")]
    new = [l for l in rows if l.get("new")]
    old = [l for l in carried if l["days"] >= 3]
    nodrv = [l for l in rows if not l["cells"][C["driver"]]]
    body = ""
    if res["info"]["stale"]:
        body += (f'<div class="stale">BOARD IS STALE — {H.escape(source.age_text(res["info"]))}. '
                 "This draft is only as current as that save.</div>")
    if res["existing_tab"]:
        body += gd.callout(
            f"The board already has a {res['existing_tab'].strip()} tab.",
            "This draft is built from the tabs before it — use it to check nothing open was left behind.")
    body += gd.tiles([
        (len(rows), f"loads drafted for {t:%a} {t.month}/{t.day}"),
        (len(carried), f"carried from {res['base_tab']} and before"),
        (len(new), "new from UPCOMING LOADS"),
        (len(old), "on the board 3+ days", "hot" if old else ""),
        (len(nodrv), "no delivery driver yet", "hot" if nodrv else ""),
    ])
    wk, we = bt["weekday"], bt["weekend"]
    weekend_note = (f" Weekend tabs are different: only {we['kept']:.0%} of open loads were carried "
                    f"onto them ({we['days']} days) — on a Saturday or Sunday treat this as a pick-list."
                    if we["days"] else "")
    body += gd.callout(
        f"On the last {wk['days']} weekdays this rule pre-filled {wk['covered']:.0%} of the tab "
        f"the office ended up with, and {wk['kept']:.0%} of what it carried was really carried.",
        "Checked against the board's own past tabs. The rest of each day's tab was new work that "
        "arrived after the draft would have been made." + weekend_note)
    trs, section = [], None
    for l in rows:
        if l["section"] != section:
            section = l["section"]
            n = sum(1 for x in rows if x["section"] == section)
            trs.append(f'<tr class="sec"><td colspan="8">{H.escape(section or "(no section)")} · {n}</td></tr>')
        c = l["cells"]
        appt = " · ".join(x for row in l["cont"] for x in row if x)
        trs.append(
            "<tr>"
            f'<td class="mono">{H.escape(l["ref"] or "—")}</td>'
            f'<td>{_one_line(c[C["pickup"]])[:60]}<small>→ {_one_line(c[C["delivery"]])[:60]}</small></td>'
            f"<td>{H.escape(appt)[:70]}</td>"
            f'<td>{H.escape(c[C["preloader"]])}<small>{H.escape(c[C["driver"]]) or "no driver"}</small></td>'
            f'<td>{H.escape(c[C["status"]])}</td>'
            f'<td class="mono">{_one_line(c[C["note"]])[:40]}</td>'
            f'<td class="num">{l["days"]}</td>'
            f'<td class="flag">{H.escape(l["flag"])}</td>'
            "</tr>")
    body += ("<table><thead><tr><th>Load</th><th>Pickup → Delivery</th><th>Appointment / line 2</th>"
             "<th>Preloader / Driver</th><th>Status</th><th>Note</th>"
             '<th class="num">Days</th><th>Check</th></tr></thead><tbody>'
             + "".join(trs) + "</tbody></table>")
    page = gd.page(
        title=f"Tomorrow's board — draft for {t:%A} {t.month}/{t.day}",
        eyebrow=settings.EYEBROW,
        sub=(f"Every open load carried forward, ready to copy: <b>{H.escape(xlsx_name)}</b> "
             "(same folder). Nothing here writes to the board."),
        source=f"live board · {source.age_text(res['info'])}",
        body=body, extra_css=_CSS)
    path.write_text(page, encoding="utf-8")


# ---------------------------------------------------------------- cli
def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wb, info = source.open_live()
    bt_rows = backtest(wb, info)
    bt = backtest_summary(bt_rows)
    if "--backtest" in sys.argv:
        for r in bt_rows:
            print(f"{r['tab']:6} {r['date']:%a}  would carry {r['predicted']:3}  "
                  f"tab had {r['actual']:3}  matched {r['hit']:3}")
        for k, v in bt.items():
            print(f"{k}: {v['days']} days · kept {v['kept']:.0%} · covered {v['covered']:.0%}")
        return 0
    target = date.fromisoformat(args[0]) if args else None
    res = build(wb, info, target)
    OUT.mkdir(parents=True, exist_ok=True)
    t = res["target"]
    xlsx = OUT / f"TOMORROW_DRAFT_{t.month}.{t.day}.xlsx"
    write_xlsx(res, xlsx)
    write_html(res, bt, OUT / "TOMORROW_DRAFT.html", xlsx.name)
    print(f"[desk] {len(res['rows'])} loads drafted for {t:%a %m/%d} "
          f"from {source.age_text(info)}")
    print(f"[desk] {xlsx}")
    print(f"[desk] {OUT / 'TOMORROW_DRAFT.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
