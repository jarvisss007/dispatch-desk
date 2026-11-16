r"""THE ROSTER — who may go where, read from the board's own DRIVERS tab.

The office keeps a DRIVERS tab in the live board workbook: one row per driver
with a WORKLOAD (OTR / LOCAL / BALE / a site name) and the CDL and medical
dates. That tab is the roster. An older hand-kept roster file only adds which
regions an OTR driver runs; past board tabs prove the rest.

Only the name, workload, company and the two expiry dates are read. Addresses,
phones and licence numbers on the tab are never copied anywhere.

Board cells use short names ("MATEO", "THEO", "OWEN C", "FELIX NEW"), so a
board name is matched to a roster row in tiers, strictest first, and a tier
only counts when everyone it finds has the same workload — two people with
different workloads under one board name is "unknown", never a guess.
"""
from __future__ import annotations

import html as H
import re
from collections import Counter
from datetime import date, datetime

from dispatch_desk import settings

IGNORE = {"NEW", "JR", "SR", "UNCLE", "BIG"}
OUT_OF_TOWN = "OTR"


def _d(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def read(wb) -> list[dict]:
    """Rows of the DRIVERS tab. Empty list when the tab is missing or reshaped."""
    ws = next((wb[n] for n in wb.sheetnames if n.strip().upper() == "DRIVERS"), None)
    if ws is None:
        return []
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    head = [str(c or "").strip().upper() for c in rows[0]]
    col = {k: head.index(k) for k in ("DRIVER NAME", "WORKLOAD", "COMP", "CDL EXPIRY", "MEDICAL") if k in head}
    if "DRIVER NAME" not in col or "WORKLOAD" not in col:
        return []
    out = []
    for r in rows[1:]:
        raw = str(r[col["DRIVER NAME"]] or "").strip().upper()
        work = str(r[col["WORKLOAD"]] or "").strip().upper()
        if not raw or not work:
            continue
        last, _, given = raw.partition(",")
        given_w, last_w = re.findall(r"[A-Z]+", given), re.findall(r"[A-Z]+", last)
        if not given_w:                                   # "FIRST LAST" with no comma
            given_w, last_w = last_w[:1], last_w[1:]
        get = lambda k: r[col[k]] if k in col and col[k] < len(r) else None      # noqa: E731
        out.append({
            "name": " ".join(given_w[:1] + last_w).title(),
            "first": given_w[0] if given_w else "", "words": given_w + last_w,
            "workload": work, "comp": str(get("COMP") or "").strip().upper(),
            "cdl": _d(get("CDL EXPIRY")), "medical": _d(get("MEDICAL")),
        })
    return out


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _close(a: str, b: str) -> bool:
    n = min(len(a), len(b))
    return n >= 5 and _edits(a, b) <= (2 if n >= 7 else 1)


def match(who: str, people: list[dict]) -> list[dict]:
    """Roster rows a board name could mean. [] = nobody; several = the same
    board name fits more than one person (the caller decides if that matters)."""
    words = [w for w in re.findall(r"[A-Z]+", who.upper()) if w not in IGNORE]
    if not words or not people:
        return []
    lead, rest = words[0], words[1:]

    def rest_ok(p):
        return all(w in p["words"] or (len(w) == 1 and any(x.startswith(w) for x in p["words"])) for w in rest)

    tiers = (
        lambda p: p["first"] == lead,                                    # the first name, exactly
        lambda p: lead in p["words"],                                    # a middle or last name
        lambda p: len(lead) >= 4 and p["first"].startswith(lead),        # THEO -> THEODORE
        lambda p: any(_close(lead, w) for w in p["words"]),              # NATHANIEL -> NATHANEL
    )
    for fits in tiers:
        hit = [p for p in people if fits(p) and rest_ok(p)]
        if hit:
            return hit
    return []


def workload(who: str, people: list[dict]) -> tuple[str | None, list[dict]]:
    """(the one workload this board name maps to, or None; the rows it matched)."""
    hit = match(who, people)
    kinds = {p["workload"] for p in hit}
    return (kinds.pop() if len(kinds) == 1 else None), hit


def paper_note(hit: list[dict], today: date | None = None) -> str:
    """A warning to hang on a suggestion when the tab's licence or medical date
    has passed. It warns, it does not block: the tab is often behind a renewal."""
    today = today or date.today()
    if len(hit) != 1:
        return ""
    bits = [f"{what} date on the DRIVERS tab passed {when:%m/%d/%Y}"
            for what, when in (("licence", hit[0]["cdl"]), ("medical", hit[0]["medical"])) if when and when < today]
    return (" · CHECK FIRST: " + ", ".join(bits)) if bits else ""


# ---------------------------------------------------------------- the page
def _e(s) -> str:
    return H.escape(str(s))


def page_body(people: list[dict], names: Counter, proven: dict, auth_fn, region_names: dict,
              today: date, gd) -> str:
    """names: board name -> loads on recent tabs. proven: board name -> {region: trips}."""
    if not people:
        return gd.callout("The board has no DRIVERS tab this run, so the desk fell back to the older roster file.",
                          "Authority comes from that file and from trips already on the board.")
    kinds = Counter(p["workload"] for p in people)
    body = gd.callout(
        f"{len(people)} drivers on the board's DRIVERS tab: "
        + ", ".join(f"{n} {k.title() if k != 'OTR' else 'out-of-town (OTR)'}" for k, n in kinds.most_common()) + ".",
        f"The desk never suggests a {settings.SITE_WORKLOADS} driver for an out-of-town load. An OTR driver "
        "is suggested for a region once the board shows three trips there, or the older roster file names it.")

    # 1 — papers
    soon = []
    for p in people:
        for what, when in (("Licence (CDL)", p["cdl"]), ("Medical card", p["medical"])):
            if when and (when - today).days <= 30:
                on = sum(n for w, n in names.items() if p in match(w, people))
                left = (when - today).days
                soon.append((left, [f"<b>{_e(p['name'])}</b>", what, when.strftime("%m/%d/%Y"),
                                    f'<span class="sug">EXPIRED {-left} days ago</span>' if left < 0
                                    else f"{left} days left", on or "—"]))
    if soon:
        body += "<h2>Papers to check</h2>" + gd.table(
            ["Driver", "Paper", "Date on the DRIVERS tab", "", "Loads on the board, last 30 tabs"],
            [r for _, r in sorted(soon, key=lambda x: x[0])], ["l", "l", "l", "l", "n"]) + gd.callout(
            "These dates are exactly what the DRIVERS tab says.",
            "A date already past may only mean the tab was not updated after a renewal — "
            "check the driver's folder before pulling anyone off a load.")

    # 2 — every name the board uses
    rows, strangers, clash = [], [], []
    for w, n in names.most_common():
        if n < 3:
            continue
        wl, hit = workload(w, people)
        trips = proven.get(w, {})
        trip_txt = " · ".join(f"{region_names[c]} {trips[c]}" for c in region_names if trips.get(c)) or "—"
        ok = [region_names[c] for c in region_names if auth_fn(w, c)[0] == "ok"]
        if not hit:
            strangers.append([f"<b>{_e(w)}</b>", n, trip_txt, ", ".join(ok) or "local only"])
            continue
        who = " / ".join(sorted({p["name"] for p in hit}))
        if wl and wl != OUT_OF_TOWN and sum(trips.values()) >= 3:
            clash.append([f"<b>{_e(w)}</b>", _e(who), wl.title(), trip_txt])
        rows.append([f"<b>{_e(w)}</b>", _e(who), _e(wl or "more than one person"), trip_txt,
                     ", ".join(ok) or ("nobody — unclear who this is" if not wl else "local only"
                                       if wl != OUT_OF_TOWN else "nowhere yet — under 3 trips on the board"), n])
    if clash:
        body += "<h2>Tab says local, board says otherwise</h2>" + gd.table(
            ["Board name", "Driver", "Workload on the tab", "Out-of-town trips on the board"],
            clash, ["l", "l", "l", "l"]) + gd.callout(
            "These drivers are marked local on the DRIVERS tab but have delivered out of town.",
            "The desk follows the tab and will not suggest them. If they do run out of town, "
            "change WORKLOAD to OTR on the tab and the desk picks it up on the next run.")
    body += "<h2>Every name on the board</h2>" + gd.table(
        ["Board name", "Driver on the DRIVERS tab", "Workload", "Out-of-town trips on the board",
         "The desk will suggest for", "Loads, last 30 tabs"], rows, ["l", "l", "l", "l", "l", "n"])
    if strangers:
        body += "<h2>On the board, not on the DRIVERS tab</h2>" + gd.table(
            ["Board name", "Loads, last 30 tabs", "Out-of-town trips on the board", "The desk will suggest for"],
            strangers, ["l", "n", "l", "l"]) + gd.callout(
            "The desk cannot tie these names to a row on the DRIVERS tab.",
            "Nicknames, owner-operators, or drivers not added yet. They are only suggested where the board "
            "already shows three trips.")
    used = [p for w in names for p in match(w, people)]
    idle = [p for p in people if p not in used]
    if idle:
        body += "<h2>On the DRIVERS tab, not on the board lately</h2>" + gd.table(
            ["Driver", "Workload", "Company"],
            [[_e(p["name"]), _e(p["workload"].title()), _e(p["comp"])] for p in idle], ["l", "l", "l"])
    return body


class Trips(dict):
    """Out-of-town trips per board name, pooled per person: KOWALSKI and the
    misspelt KOWLASKI are one man, so a trip under either spelling counts for
    both. Names that fit nobody, or more than one person, keep their own count."""

    def __init__(self, raw: dict, people: list[dict]):
        super().__init__({w: dict(c) for w, c in raw.items()})
        self._people, self._by, self._memo = people, {}, {}
        for w, c in raw.items():
            k = self._person(w)
            if k:
                tot = self._by.setdefault(k, Counter())
                tot.update(c)

    def _person(self, who: str) -> str | None:
        if who not in self._memo:
            hit = match(who, self._people)
            self._memo[who] = hit[0]["name"] if len(hit) == 1 else None
        return self._memo[who]

    def get(self, who, default=None):
        k = self._person(who)
        if k:
            return dict(self._by.get(k, {}))
        return super().get(who, default)
