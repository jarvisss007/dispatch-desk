r"""House design system — one visual language for every page.

Before this module every generator hand-rolled its own CSS (~21k characters
of near-duplicate styling across twelve files), so no two pages agreed on a
colour, a font size or a border. This is the single source of truth. Import
it, hand it content, get a page that looks like all the others.

Principles, applied to whole pages:

  * EDITORIAL RESTRAINT — the highest-quality move is deletion. A page states
    one thing first; everything else is support.
  * ONE ACCENT — amber marks what needs a decision, never decoration.
  * TYPOGRAPHY BY ROLE — serif for the title, sans for names, mono for
    figures and technical strings. Numbers are the point on these pages, so
    they get mono and tabular alignment.
  * 4PX GRID — every size and gap divisible by four.
  * ALWAYS DARK on screen, always white on paper.
  * EVERY PAGE STATES ITS SOURCE AND AS-OF DATE. A page that can't say where
    its numbers came from is not shippable.

    from page_design import page, tiles, card, table, callout
    html = page(title="Day plan", eyebrow="Sample Freight · Dispatch Desk",
                sub="…", source="live board tab 11.13",
                body=tiles([...]) + table(...))
"""
from __future__ import annotations

import html as _h
from datetime import datetime

__all__ = ["page", "tiles", "card", "cards", "table", "callout", "esc", "TOKENS", "BRAND"]

BRAND = "Sample Freight"       # the default eyebrow and the footer; the desk sets it from its settings

esc = _h.escape

TOKENS = {
    "paper": "#101214", "paper2": "#1a1a19", "ink": "#ffffff",
    "muted": "#898781", "soft": "#5b6570",
    "rule": "rgba(255,255,255,.10)", "rule_solid": "#2c2c2a",
    "accent": "#fab219", "accent_tint": "rgba(250,178,25,.12)",
    "link": "#8fd4ff", "good": "#0ca30c", "bad": "#ff6b6b",
}

_FONTS = ("https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1"
          "&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap")

BASE_CSS = """
:root{--paper:#101214;--paper2:#1a1a19;--ink:#fff;--muted:#898781;--soft:#5b6570;
--rule:rgba(255,255,255,.10);--rule2:#2c2c2a;--accent:#fab219;
--accent-tint:rgba(250,178,25,.12);--link:#8fd4ff;--good:#0ca30c;--bad:#ff6b6b;
color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);padding:28px 32px 24px;
font:14px/1.5 Geist,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1240px;margin:0 auto}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}

/* ---- header: eyebrow / title / subtitle ---- */
.eyebrow{font-family:"Geist Mono",Consolas,monospace;font-size:9px;
letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
h1{font-family:"Instrument Serif",Georgia,serif;font-weight:400;font-size:28px;
margin:0 0 4px;letter-spacing:-.01em}
.sub{font-size:12px;color:var(--muted);max-width:88ch;line-height:1.55;margin-bottom:20px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);
margin:24px 0 8px;font-weight:600}

/* ---- KPI tiles: the page's headline numbers ---- */
.tiles{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.tile{background:var(--paper2);border-radius:10px;padding:10px 18px;min-width:104px;
box-shadow:0 0 0 1px var(--rule)}
.tile b{display:block;font-family:"Geist Mono",Consolas,monospace;font-size:22px;
font-weight:500;letter-spacing:-.01em}
.tile span{font-size:9px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.tile.hot{box-shadow:0 0 0 1px var(--accent)}
.tile.hot b{color:var(--accent)}
.tile.good b{color:var(--good)} .tile.bad b{color:var(--bad)}

/* ---- cards: varied widths, never equal thirds ---- */
.cards{display:grid;grid-template-columns:1.1fr 1fr .9fr;gap:16px;margin:16px 0}
.card{background:var(--paper2);border-radius:10px;padding:16px 18px;
box-shadow:0 0 0 1px var(--rule)}
.card h3{font-size:12px;font-weight:600;margin:0 0 8px}
.card p{font-size:11.5px;line-height:1.55;color:#c3c2b7;margin:0}
.card .fig{font-family:"Geist Mono",Consolas,monospace;font-size:9px;
color:var(--muted);margin-top:10px;letter-spacing:.04em}
.card .dot{width:7px;height:7px;border-radius:50%;display:inline-block;
margin-right:7px;vertical-align:1px}

/* ---- tables: figures in mono, tabular, right-aligned ---- */
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;color:var(--muted);font-size:9px;text-transform:uppercase;
letter-spacing:.07em;padding:5px 8px;border-bottom:1px solid var(--rule2);font-weight:600}
td{padding:6px 8px;border-bottom:1px solid #232321;vertical-align:top}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;
font-family:"Geist Mono",Consolas,monospace}
td.mono{font-family:"Geist Mono",Consolas,monospace}
tr.hot td{background:var(--accent-tint)}
.dim{color:var(--muted);font-size:11px}

/* ---- expandable detail rows ---- */
details.row{background:var(--paper2);border-radius:10px;padding:9px 14px;
margin-bottom:6px;box-shadow:0 0 0 1px var(--rule)}
details.row summary{cursor:pointer;list-style:none;display:flex;gap:10px;
align-items:baseline;flex-wrap:wrap}
details.row summary::-webkit-details-marker{display:none}
details.row .hint{color:var(--soft);font-size:11px;margin-left:auto}
details.row[open] .hint{display:none}
details.row .body{margin-top:10px;border-top:1px solid var(--rule2);padding-top:10px}

/* ---- editorial callout: max two per page ---- */
.callout{border-left:2px solid var(--accent);padding:2px 0 2px 14px;margin:18px 0}
.callout .say{font-family:"Instrument Serif",Georgia,serif;font-style:italic;
font-size:14px;line-height:1.45}
.callout .fine{font-family:"Geist Mono",Consolas,monospace;font-size:9px;
color:var(--muted);letter-spacing:.05em;margin-top:5px}

.empty{color:var(--good);font-weight:600;font-size:15px;padding:16px 2px}
footer{margin-top:26px;padding-top:12px;border-top:1px solid var(--rule);
font-family:"Geist Mono",Consolas,monospace;font-size:9px;color:var(--muted);
letter-spacing:.06em;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}

@media (max-width:860px){
  body{padding:18px 16px}
  .cards{grid-template-columns:1fr}
  .tile{flex:1 1 44%}
}
/* paper is white — the dark rule is for screens */
@media print{
  :root{color-scheme:light}
  body{background:#fff;color:#111;padding:10mm;font-size:11.5px}
  .tile,.card,details.row{background:#fafafa;box-shadow:0 0 0 1px #ddd}
  .card p{color:#333} .sub,.dim,.tile span,footer{color:#555}
  .tile.hot{box-shadow:0 0 0 1.5px #b5523a} .tile.hot b{color:#b5523a}
  .callout{border-left-color:#b5523a}
  tr.hot td{background:#fdf3e6;-webkit-print-color-adjust:exact}
  th{border-bottom-color:#111;color:#333} td{border-bottom-color:#ddd}
  tr{page-break-inside:avoid} thead{display:table-header-group}
  a{color:#111}
}
"""


def tiles(items) -> str:
    """items: [(value, label)] or [(value, label, tone)] — tone in
    '', 'hot', 'good', 'bad'. The page's headline figures."""
    out = []
    for it in items:
        val, lab = it[0], it[1]
        tone = it[2] if len(it) > 2 else ""
        out.append(f'<div class="tile {tone}"><b>{esc(str(val))}</b>'
                   f'<span>{esc(str(lab))}</span></div>')
    return f'<div class="tiles">{"".join(out)}</div>'


def card(title, body, fig="", dot="") -> str:
    d = f'<span class="dot" style="background:{dot}"></span>' if dot else ""
    f = f'<div class="fig">{esc(fig)}</div>' if fig else ""
    return (f'<div class="card"><h3>{d}{esc(title)}</h3>'
            f'<p>{body}</p>{f}</div>')


def cards(items) -> str:
    """items: [(title, body_html, fig, dot)] — three reads best."""
    return f'<div class="cards">{"".join(card(*i) for i in items)}</div>'


def table(headers, rows, aligns=None) -> str:
    """headers: [str]; rows: [[cell_html]]; aligns: 'l'/'n'/'m' per column
    (left, numeric-mono, mono)."""
    aligns = aligns or ["l"] * len(headers)
    cls = {"l": "", "n": " class=\"num\"", "m": " class=\"mono\""}
    th = "".join(f'<th{" class=\"num\"" if a == "n" else ""}>{esc(h)}</th>'
                 for h, a in zip(headers, aligns))
    body = []
    for r in rows:
        tds = "".join(f'<td{cls[a]}>{c}</td>' for c, a in zip(r, aligns))
        body.append(f"<tr>{tds}</tr>")
    return f'<table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def callout(say, fine="") -> str:
    f = f'<div class="fine">{esc(fine)}</div>' if fine else ""
    return f'<div class="callout"><div class="say">{esc(say)}</div>{f}</div>'


def page(title, body, eyebrow="", sub="", source="", refresh=0,
         generated=None, extra_css="") -> str:
    """The page shell. `source` is REQUIRED in spirit: every page says
    where its numbers came from and when they were made."""
    gen = generated or datetime.now().strftime("%a %m/%d %I:%M %p")
    meta_refresh = (f'<meta http-equiv="refresh" content="{refresh}">'
                    if refresh else "")
    eyebrow = eyebrow or BRAND.upper()
    src_line = f'<span>SOURCE: {esc(source).upper()}</span>' if source else "<span></span>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{meta_refresh}<title>{esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{_FONTS}" rel="stylesheet">
<style>{BASE_CSS}{extra_css}</style></head><body><div class="wrap">
<div class="eyebrow">{esc(eyebrow)} &middot; {esc(gen)}</div>
<h1>{esc(title)}</h1>
{f'<div class="sub">{sub}</div>' if sub else ''}
{body}
<footer>{src_line}<span>{esc(BRAND).upper()} · DISPATCH DESK</span></footer>
</div></body></html>"""
