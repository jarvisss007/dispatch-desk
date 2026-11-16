"""Read the live dispatch board (a monthly .xlsx) off disk and look up loads.

The board is authored by humans, so parsing is defensive. What we rely on:

  * Daily tabs are named "<month>.<day>" -> "6.29", "6.30", "7.1". Some have a
    stray trailing space ("6.13 "). Non-daily tabs (DRIVERS, UPCOMING LOADS,
    Sheet1, RESPONSIBILITIES, ...) are ignored for lookups.
  * Each daily tab has a one-row day banner on row 1 ("WEDNESDAY 7/1"), then a
    header row whose column A == "LOAD". Real data starts below the header.
  * Columns (1-based): A=LOAD B=PICKUP C=DELIVERY D=DELIVERY# E=PRELOADER
    F=DRIVER G=TRK H=STATUS I=NOTE J=RATE.
  * Customer section rows have text only in column A (e.g. "PALMCO RIVERSIDE")
    and blank everywhere else — separators, not loads.
  * Loads often have a continuation row underneath: column A blank, "READY" in
    PICKUP, and the DELIVERY APPOINTMENT time in the DELIVERY column
    ("7/1 - 3:15AM"). We attach these to the load above.
  * The broker's number may sit in LOAD (A) or DELIVERY# (D), and a cell can
    hold two numbers ("100123/2004567"). We match if the number appears
    anywhere in the load's cells (including continuation rows).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import openpyxl

import config

# Column indices (1-based) -> 0-based list positions.
COL = {
    "load": 0, "pickup": 1, "delivery": 2, "delivery_no": 3, "preloader": 4,
    "driver": 5, "trk": 6, "status": 7, "note": 8, "rate": 9,
}
NUM_COLS = 10  # A..J

_DAILY_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\s*$")
# Broker/load numbers are long digit runs; 4+ digits covers trailer#s too but we
# match the query token exactly, so short collisions don't matter.
_NUM_TOKEN_RE = re.compile(r"\d{4,}")
_APPT_RE = re.compile(r"\d{1,2}(:\d{2})?\s*[AP]M", re.IGNORECASE)
# A date like "7/1", a time window, "READY", or "NEED APPT" — anything that
# reads as scheduling info rather than a delivery location.
_APPT_HINT_RE = re.compile(
    r"(\d{1,2}(:\d{2})?\s*[AP]M|\d{1,2}/\d{1,2}|NEED\s*APPT|APPT|READY)",
    re.IGNORECASE,
)


def _looks_like_appt(text: str) -> bool:
    return bool(text) and bool(_APPT_HINT_RE.search(text))


# A real scheduled time/date — stricter than _looks_like_appt (excludes bare
# "READY"/"NEED APPT"). Used for the PICKUP-column appointment.
_APPT_TIME_RE = re.compile(r"(\d{1,2}(:\d{2})?\s*[AP]M|\d{1,2}:\d{2}|\d{1,2}/\d{1,2})",
                           re.IGNORECASE)


def _has_appt_time(text: str) -> bool:
    return bool(text) and bool(_APPT_TIME_RE.search(text))


def appointment_dt(appt: str, now: datetime) -> datetime | None:
    """Parse a board appointment ('7/2 - 9AM', '7/1 - 3:15AM', '6AM - 2PM') into
    a real datetime, using `now` for the year and (when the appt has no date) the
    day. Returns None if there's no usable time. For ranges, takes the start."""
    if not appt:
        return None
    s = appt.upper()
    dm = re.search(r"(\d{1,2})/(\d{1,2})", s)          # date part, if any
    tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([AP])M", s)   # first am/pm time
    if tm:
        hour, minute = int(tm.group(1)), int(tm.group(2) or 0)
        if tm.group(3) == "P" and hour != 12:
            hour += 12
        elif tm.group(3) == "A" and hour == 12:
            hour = 0
    else:
        t24 = re.search(r"\b(\d{1,2}):(\d{2})\b", s)    # 24h time
        if not t24:
            return None
        hour, minute = int(t24.group(1)), int(t24.group(2))
    month = int(dm.group(1)) if dm else now.month
    day = int(dm.group(2)) if dm else now.day
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59):
        return None
    try:
        return datetime(now.year, month, day, hour, minute)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Status ladder — the load lifecycle as the board writes it.
# A load goes SHIPPER -> YARD -> CONSIGNEE. The preloader runs shipper->yard,
# the driver runs yard->consignee.
#
#   DISPATCHED      driver dispatched, going to PICK UP (heading to shipper)
#   TO_SHIPPER      en route to the shipper (going to pick up)
#   AT_SHIPPER      at the shipper, loading
#   TO_YARD         picked up, en route to our yard
#   AT_YARD         staged at our yard ("it's here"), delivers on the appointment
#   TO_CONSIGNEE    en route to the consignee (being delivered)
#   AT_CONSIGNEE    arrived at the consignee, checking in / unloading
#   DELIVERED       complete
#   CANCELED        load canceled / not covered
# --------------------------------------------------------------------------
class Stage:
    UNKNOWN = "UNKNOWN"
    DISPATCHED = "DISPATCHED"       # dispatched to pick up (NOT a delivery stage)
    TO_SHIPPER = "TO_SHIPPER"       # en route to shipper to pick up
    AT_SHIPPER = "AT_SHIPPER"       # at the shipper, loading
    TO_YARD = "TO_YARD"            # loaded, heading to our yard
    AT_YARD = "AT_YARD"            # staged at yard, delivers on appointment
    TO_CONSIGNEE = "TO_CONSIGNEE"  # being delivered
    AT_CONSIGNEE = "AT_CONSIGNEE"  # arrived / unloading
    DELIVERED = "DELIVERED"
    CANCELED = "CANCELED"


# Checked in order, most-specific first. Matched against the STATUS cell after
# normalizing IN ROUTE/ENROUTE -> "TO " and dropping "THE".
_STATUS_PATTERNS: list[tuple[str, str]] = [
    (Stage.CANCELED, "CANCEL"),
    (Stage.CANCELED, "REMOVED"),
    (Stage.CANCELED, "NOT COVERING"),
    (Stage.CANCELED, "DONT HAVE COVERAGE"),
    (Stage.CANCELED, "COVERED BY BROKER"),
    (Stage.CANCELED, "DELIVERED BY THE BROKER"),
    (Stage.CANCELED, "DELIVERED BY BROKER"),
    (Stage.DELIVERED, "DELIVERED"),
    (Stage.DELIVERED, "UNLOADED"),
    (Stage.AT_CONSIGNEE, "AT CONSIGNEE"),
    (Stage.AT_CONSIGNEE, "AT DELIVERY"),
    (Stage.AT_CONSIGNEE, "AT RECEIVER"),
    (Stage.TO_CONSIGNEE, "TO CONSIGNEE"),
    (Stage.TO_CONSIGNEE, "TO RECEIVER"),
    (Stage.TO_CONSIGNEE, "TO DELIVERY"),
    (Stage.TO_CONSIGNEE, "TO VEGAS"),
    (Stage.TO_CONSIGNEE, "DELIVERING"),
    (Stage.AT_SHIPPER, "AT SHIPPER"),
    (Stage.AT_YARD, "AT YARD"),
    (Stage.AT_YARD, "IN YARD"),
    # a partner yard counts as staged too: phrases from config.PARTNER_YARD_WORDS
    *[(Stage.AT_YARD, w.upper()) for w in getattr(config, "PARTNER_YARD_WORDS", ())],
    (Stage.TO_YARD, "TO YARD"),       # covers "TO THE YARD" after THE is dropped
    (Stage.TO_YARD, "TO TYARD"),      # observed typo
    (Stage.AT_YARD, "YARD"),          # any other yard mention -> staged
    (Stage.TO_SHIPPER, "TO SHIPPER"),
    (Stage.TO_SHIPPER, "TO PICKUP"),
    (Stage.TO_SHIPPER, "TO PU"),
    (Stage.DISPATCHED, "DISPATCHED"),
    (Stage.DISPATCHED, "DISPTATCHED"),  # observed typo
    (Stage.DISPATCHED, "DISPATCH"),
]

# Only the two SETTLED states are answered straight from the board: AT_YARD
# (staged, delivers on the appointment) and DELIVERED (complete). AT_SHIPPER and
# AT_CONSIGNEE are live/in-progress — "at shipper" doesn't say if loading is done
# or when they leave; "at consignee" doesn't say arrival time / unloading /
# completed — so those (and every en-route stage) ask the driver. CANCELED is
# flagged for a human, never auto-drafted.
_BOARD_ANSWERS = {Stage.AT_YARD, Stage.DELIVERED}


def classify_status(status_text: str | None) -> str:
    """Map a free-text STATUS cell onto a Stage. 'EN ROUTE'/'IN ROUTE'/'ENROUTE'
    are all normalized, and a filler 'THE' is dropped ('EN ROUTE TO THE YARD')."""
    if not status_text:
        return Stage.UNKNOWN
    s = status_text.upper()
    s = re.sub(r"\b(IN|EN)\s*ROUTE\b", "TO", s)   # also matches "ENROUTE"
    s = re.sub(r"\bTO THE\b", "TO", s)
    s = re.sub(r"\s+", " ", s).strip()
    for stage, needle in _STATUS_PATTERNS:
        if needle in s:
            return stage
    return Stage.UNKNOWN


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
def _clean(v) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def _digit_tokens(text: str) -> set[str]:
    return set(_NUM_TOKEN_RE.findall(text or ""))


@dataclass
class LoadMatch:
    tab: str                       # daily tab it was found on, e.g. "7.1"
    tab_date: date | None
    customer: str                  # section header the load sits under
    row: int                       # 1-based row index of the load line
    cells: list[str]               # A..J of the load line
    continuation: list[list[str]] = field(default_factory=list)

    def _col(self, name: str) -> str:
        return self.cells[COL[name]]

    @property
    def load(self) -> str: return self._col("load")
    @property
    def pickup(self) -> str: return self._col("pickup")
    @property
    def delivery(self) -> str: return self._col("delivery")
    @property
    def delivery_no(self) -> str: return self._col("delivery_no")
    @property
    def preloader(self) -> str: return self._col("preloader")
    @property
    def driver(self) -> str: return self._col("driver")
    @property
    def trk(self) -> str: return self._col("trk")
    @property
    def status(self) -> str: return self._col("status")
    @property
    def note(self) -> str: return self._col("note")
    @property
    def rate(self) -> str:
        # NOTE: column J ("RATE") on the board is NOT the authoritative billable
        # rate — dispatchers put free-text notes here (e.g. "LOAD IN TMS"). The
        # agreed rate ALWAYS comes from the rate-confirmation PDF. Do not use this
        # for invoicing or any rate the broker sees.
        return self._col("rate")

    @property
    def stage(self) -> str:
        return classify_status(self.status)

    @property
    def board_answers(self) -> bool:
        """True if STATUS alone answers a status question (no driver needed)."""
        return self.stage in _BOARD_ANSWERS

    @property
    def appointment(self) -> str:
        """DELIVERY appointment/time. Lives in the DELIVERY column of a
        continuation row ('7/2 - 9AM'), falling back to the load line."""
        for cont in self.continuation:
            c = cont[COL["delivery"]]
            if _looks_like_appt(c):
                return c
        # Sometimes the load line's DELIVERY holds the appt if no continuation.
        c = self.delivery
        return c if _looks_like_appt(c) else ""

    @property
    def pickup_appointment(self) -> str:
        """PICKUP appointment/time. Lives in the PICKUP column of a continuation
        row ('7/1 - 3PM'). The pickup column often just says 'READY' (no time),
        which is not an appointment — so we require an actual time/date."""
        for cont in self.continuation:
            b = cont[COL["pickup"]]
            if _has_appt_time(b):
                return b
        return ""

    def appt_for(self, phase: str) -> str:
        """The relevant appointment string for the leg: PICKUP time for a pickup
        question, DELIVERY time for a delivery question."""
        return self.pickup_appointment if phase == "pickup" else self.appointment

    def appt_dt(self, now: datetime, phase: str = "delivery") -> datetime | None:
        """The relevant appointment as a real datetime (for timing-aware asks)."""
        return appointment_dt(self.appt_for(phase), now)

    def driver_for(self, phase: str) -> str:
        """Which person to message. 'pickup' -> PRELOADER, else DRIVER,
        falling back to whichever is populated."""
        if phase == "pickup":
            return self.preloader or self.driver
        return self.driver or self.preloader

    def all_tokens(self) -> set[str]:
        toks: set[str] = set()
        for c in self.cells:
            toks |= _digit_tokens(c)
        for cont in self.continuation:
            for c in cont:
                toks |= _digit_tokens(c)
        return toks


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _tab_date(name: str, year: int) -> date | None:
    m = _DAILY_RE.match(name)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def daily_tabs(wb, year: int) -> list[tuple[str, date]]:
    """Daily tabs sorted newest-first."""
    out = []
    for name in wb.sheetnames:
        d = _tab_date(name, year)
        if d:
            out.append((name, d))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _row_values(row) -> list[str]:
    vals = [_clean(c) for c in row[:NUM_COLS]]
    if len(vals) < NUM_COLS:
        vals += [""] * (NUM_COLS - len(vals))
    return vals


def _is_section(vals: list[str]) -> bool:
    return bool(vals[0]) and not any(vals[1:])


def _is_continuation(vals: list[str]) -> bool:
    # A secondary line under a load (READY / appointment). It must NOT carry a
    # load number, driver, or status, and must contain no 5+ digit token — a
    # standalone number means it is its own load line, not a continuation, and
    # merging it would attach its number to the wrong load.
    if vals[COL["load"]] or vals[COL["driver"]] or vals[COL["status"]]:
        return False
    if any(len(t) >= 5 for c in vals for t in _NUM_TOKEN_RE.findall(c)):
        return False
    return any(vals)


def _find_header_row(ws) -> int:
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=15, max_col=NUM_COLS,
                                         values_only=True), 1):
        if _clean(row[0]).upper() == "LOAD":
            return i
    return 3  # observed default


def iter_loads(ws):
    """Yield (row_index, customer, cells, continuation_rows) for each load on a tab."""
    header = _find_header_row(ws)
    customer = ""
    pending: list | None = None  # [row, customer, cells, [continuations]]

    for i, row in enumerate(ws.iter_rows(min_row=header + 1, max_col=NUM_COLS,
                                          values_only=True), header + 1):
        vals = _row_values(row)
        if not any(vals):
            continue
        if _is_section(vals):
            if pending:
                yield pending
                pending = None
            customer = vals[0]
            continue
        if _is_continuation(vals) and pending is not None:
            pending[3].append(vals)
            continue
        # a real load line
        if pending:
            yield pending
        pending = [i, customer, vals, []]
    if pending:
        yield pending


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def open_board(path: Path | None = None):
    path = Path(path) if path else config.board_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Board not found: {path}\n"
            "Check DESK_BOARD_FILE / DESK_DATA_DIR, and that the file is really on "
            "this disk (a cloud-synced folder may hold only a placeholder)."
        )
    return openpyxl.load_workbook(path, read_only=True, data_only=True)


# --------------------------------------------------------------------------
# Freshness. The board is an INPUT, and a frozen input is the quietest way for
# every board-derived number to go wrong: a file that has not been saved for
# three days still produces a full, confident-looking report unless the report
# says how old its input is.
# --------------------------------------------------------------------------
BOARD_STALE_HOURS = 12.0


def freshness(path: Path | None = None, when: datetime | None = None,
              stale_hours: float = BOARD_STALE_HOURS) -> dict:
    """How current the board file is: {path, mtime, age_h, tab, tab_missing,
    stale, reason}. Never raises — a board we cannot read is maximally stale."""
    when = when or datetime.now()
    p = Path(path) if path else config.board_path()
    tab = f"{when.month}.{when.day}"
    out = {"path": str(p), "mtime": None, "age_h": None, "tab": tab,
           "tab_missing": True, "stale": True, "reason": "board file unreadable"}
    try:
        mt = datetime.fromtimestamp(p.stat().st_mtime)
    except OSError:
        out["reason"] = "board file MISSING"
        return out
    out["mtime"] = mt.isoformat(sep=" ", timespec="minutes")
    out["age_h"] = (when - mt).total_seconds() / 3600
    try:
        wb = open_board(p)
        try:
            out["tab_missing"] = tab not in {n.strip() for n in wb.sheetnames}
        finally:
            wb.close()
    except Exception:
        out["tab_missing"] = True
    bits = []
    if out["age_h"] > stale_hours:
        bits.append(f"not saved for {out['age_h']:.0f}h (last {out['mtime']})")
    if out["tab_missing"]:
        bits.append(f"no {tab} tab")
    out["stale"] = bool(bits)
    out["reason"] = " · ".join(bits)
    return out


def stale_banner(f: dict | None = None, **kw) -> str:
    """The loud one-liner, or '' when the board is current."""
    f = f if f is not None else freshness(**kw)
    if not f["stale"]:
        return ""
    return (f"** BOARD INPUT STALE — {f['reason']} — every board-derived section "
            f"below is SUSPECT until the board is saved ({f['path']}) **")


def normalize_number(number: str) -> str:
    return re.sub(r"\D", "", str(number or ""))


def find_load(number: str, path: Path | None = None,
              year: int | None = None) -> LoadMatch | None:
    """Find a load by broker/load number. Searches daily tabs newest-first and
    returns the match on the most recent tab. Returns None if not found."""
    target = normalize_number(number)
    if not target:
        return None
    path = Path(path) if path else config.board_path()
    year = year or path_year(path) or date.today().year

    wb = open_board(path)
    try:
        for tab_name, tab_dt in daily_tabs(wb, year):
            ws = wb[tab_name]
            for row_i, customer, cells, conts in iter_loads(ws):
                m = LoadMatch(tab=tab_name.strip(), tab_date=tab_dt,
                              customer=customer, row=row_i, cells=cells,
                              continuation=conts)
                if target in m.all_tokens():
                    return m
        return None
    finally:
        wb.close()


def path_year(path: Path) -> int | None:
    m = re.search(r"(20\d{2})", Path(path).stem)
    return int(m.group(1)) if m else None
