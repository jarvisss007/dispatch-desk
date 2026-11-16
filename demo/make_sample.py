r"""Make the demo's sample data: a fictional carrier's dispatch board and the
small files that stand in for live GPS, the roster and the fuel figures.

    py demo/make_sample.py                  # writes ./demo/sample/
    py demo/make_sample.py --out some/dir   # anywhere else

EVERYTHING here is invented. "Sample Freight", its drivers, its customers,
its load numbers, trailer numbers, VINs and plates do not exist. Only the
city names and their rough coordinates are real (public) places.

What is written
    <MONTH> <YEAR>.xlsx     the board: four weeks of weekday tabs ending today,
                            a DRIVERS tab and an UPCOMING LOADS tab
    gps.json                trucks, trailers, geofences and a little stop history
    authority.json          the older roster: which regions a few drivers run
    equipment.json          the tractor list
    fuel.json               two round fuel figures for the empty-trips page
    detention_terms.json    free time per customer
    offers.db               three emailed broker offers (sqlite)
    state/                  what the desk would remember from earlier runs:
                            pool sizes customers stated, daily trailer counts,
                            and its suggestion log for the last four weekdays

Deterministic: a fixed random seed, with every date counted back from today
and every "live" time counted from the minute it is run. Run it twice on the
same day and the board holds the same loads.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "standins"))
from fleet_live import CITIES  # noqa: E402  (public city centres)

SEED = 4242
WEEKDAY_TABS = 20
YARD = (34.092, -117.435)
CITY = {n.upper(): (la, lo) for n, la, lo in CITIES}
HEADERS = ["LOAD", "PICKUP", "DELIVERY", "DELIVERY#", "PRELOADER", "DRIVER", "TRK", "STATUS", "NOTE", "RATE"]

# name -> (city line, lat, lng, region). Every one of these is a geofence in the sample GPS.
SITES = {
    "HOME YARD": ("FONTANA, CA", 34.092, -117.435, "LOCAL"),
    "HARBOR PAPER ONTARIO": ("ONTARIO, CA", 34.045, -117.610, "LOCAL"),
    "REDWOOD CARSON": ("CARSON, CA", 33.830, -118.260, "LOCAL"),
    "PALMCO RIVERSIDE": ("RIVERSIDE, CA", 33.975, -117.360, "LOCAL"),
    "NORTHSTAR AUTO REDLANDS": ("REDLANDS, CA", 34.065, -117.200, "LOCAL"),
    "MERIDIAN CORONA": ("CORONA, CA", 33.880, -117.580, "LOCAL"),
    "FRESHWAY TOLLESON": ("TOLLESON, AZ", 33.448, -112.262, "AZ"),
    "SUNPEAK DC": ("GOODYEAR, AZ", 33.440, -112.380, "AZ"),
    "TRAILHEAD BUCKEYE": ("BUCKEYE, AZ", 33.400, -112.560, "AZ"),
    "MERIDIAN GLENDALE": ("GLENDALE, AZ", 33.535, -112.200, "AZ"),
    "COPPER STATE RECYCLING SCOTTSDALE": ("SCOTTSDALE, AZ", 33.620, -111.900, "AZ"),
    "DESERT PET WADDELL": ("WADDELL, AZ", 33.570, -112.430, "AZ"),
    "REDWOOD TOLLESON": ("TOLLESON, AZ", 33.455, -112.245, "AZ"),
    "PALMCO VEGAS": ("LAS VEGAS, NV", 36.085, -115.150, "NV"),
    "SILVERLINE RECYCLING VEGAS": ("NORTH LAS VEGAS, NV", 36.240, -115.080, "NV"),
    "HIGH DESERT DENIM HENDERSON": ("HENDERSON, NV", 36.030, -114.990, "NV"),
    "MERIDIAN TULARE": ("TULARE, CA", 36.200, -119.340, "CV"),
    "DELTA CARTON TRACY": ("TRACY, CA", 37.700, -121.470, "CV"),
    "ORCHARD VALLEY FRESNO": ("FRESNO, CA", 36.720, -119.780, "CV"),
    "REDWOOD TRACY": ("TRACY, CA", 37.745, -121.410, "CV"),
    "REDWOOD EXETER": ("EXETER, CA", 36.300, -119.140, "CV"),
}
FENCE_NAME = {"SUNPEAK DC": "SUNPEAK DC GOODYEAR AZ"}
# receivers the desk knows only by their town
TOWN_RECEIVERS = {
    "SAGUARO FOODS": "PHOENIX, AZ", "PIMA PACKAGING": "TUCSON, AZ", "CHANDLER COLD STORAGE": "CHANDLER, AZ",
    "NEON HOME GOODS": "LAS VEGAS, NV", "DUNE CORRUGATED": "NORTH LAS VEGAS, NV",
    "GOLDEN CITRUS": "VISALIA, CA", "SAN JOAQUIN BOX": "STOCKTON, CA", "KERN PRODUCE": "BAKERSFIELD, CA",
}
RECEIVERS = {
    "AZ": ["REDWOOD TOLLESON", "SUNPEAK DC", "SAGUARO FOODS", "PIMA PACKAGING", "CHANDLER COLD STORAGE", "REDWOOD TOLLESON"],
    "NV": ["PALMCO VEGAS", "HIGH DESERT DENIM HENDERSON", "NEON HOME GOODS", "DUNE CORRUGATED"],
    "CV": ["REDWOOD TRACY", "REDWOOD EXETER", "GOLDEN CITRUS", "SAN JOAQUIN BOX", "KERN PRODUCE"],
}
SHIPPERS = {
    "AZ": ["FRESHWAY TOLLESON", "SUNPEAK DC", "TRAILHEAD BUCKEYE", "MERIDIAN GLENDALE",
           "COPPER STATE RECYCLING SCOTTSDALE", "DESERT PET WADDELL"],
    "NV": ["PALMCO VEGAS", "SILVERLINE RECYCLING VEGAS", "HIGH DESERT DENIM HENDERSON"],
    "CV": ["MERIDIAN TULARE", "DELTA CARTON TRACY", "ORCHARD VALLEY FRESNO"],
}
LOCAL_SHIPPERS = ["PALMCO RIVERSIDE", "NORTHSTAR AUTO REDLANDS", "MERIDIAN CORONA", "REDWOOD CARSON"]
BROKERS = ["NORTHWIND LOGISTICS", "BLUE RIDGE FREIGHT", "LANTERN BROKERAGE", "KESTREL XPRESS"]
MILLS = ["HARBOR PAPER ONTARIO", "REDWOOD CARSON"]
CODES = ["AZ", "NV", "CV"]
REGION_NAME = {"AZ": "Arizona", "NV": "Las Vegas", "CV": "Central Valley"}

# board name, name on the DRIVERS tab, workload, truck, regions run, line in the older roster
PEOPLE = [
    ("MARCO", "VIDAL, MARCO", "OTR", "SF01", ["AZ", "NV"], "OTR - AZ, NV, LOCAL"),
    ("IVAN", "PETROV, IVAN", "OTR", "SF02", ["AZ"], None),
    ("TOBIAS", "LIND, TOBIAS", "OTR", "SF03", ["AZ", "CV"], "OTR - AZ, N, LOCAL"),
    ("PABLO", "SERRANO, PABLO", "OTR", "SF04", ["AZ"], None),
    ("FELIX", "MORENO, FELIX", "OTR", "SF05", ["AZ", "NV"], None),
    ("GRANT", "ELLIS, GRANT", "OTR", "SF06", ["NV"], "OTR - NV ONLY"),
    ("OWEN C", "CARTER, OWEN LEE", "OTR", "SF07", ["NV", "CV"], None),
    ("MATEO", "REYES, LUCAS MATEO", "OTR", "SF08", ["CV"], "OTR - N, LOCAL"),
    ("NATHANIEL", "OKORO, NATHANEL", "OTR", "SF09", ["CV", "AZ"], None),
    ("DARNELL", "HUGHES, DARNELL", "OTR", "SF10", ["AZ"], None),
    ("DARRELL", "BOONE, DARRELL", "OTR", "SF11", ["NV"], None),
    ("KOWALSKI", "KOWALSKI, STEFAN", "OTR", "SF12", ["AZ", "CV"], None),
    ("QUENTIN", "MARSH, QUENTIN", "OTR", "SF13", ["NV", "AZ"], None),
    ("AMIR", "HADDAD, AMIR", "OTR", "SF14", ["CV"], None),
    ("HECTOR", "SALAS, HECTOR", "OTR", "SF15", ["AZ", "NV"], None),
    ("BRUNO", "COSTA, BRUNO", "OTR", "SF16", ["CV", "NV"], None),
    ("ZEKE", None, "OTR", "SF17", ["NV"], None),                      # runs loads, but nobody added him to the tab
    ("NOLAN", "FRY, NOLAN", "LOCAL", "SF18", [], "LOCAL ONLY"),
    ("THEO", "BANKS, THEODORE", "LOCAL", "SF19", [], None),
    ("DELROY", "JAMES, DELROY", "LOCAL", "SF20", [], None),
    ("CEDRIC", "VANCE, CEDRIC", "LOCAL", "SF21", [], None),
    ("MILAN", "NOVAK, MILAN", "LOCAL", "SF22", [], None),
    ("HUGO", "MEDINA, HUGO FELIX", "BALE", "SF23", [], None),
    ("RAFAEL", "ORTEGA, RAFAEL", "BALE", "SF24", [], None),
    ("OWEN P", "PRICE, OWEN", "SHUTTLE", "SF25", [], None),
    (None, "ADLER, SIMONE", "LOCAL", None, [], None),                 # on the tab, not on the board lately
]
UNIT = {p[0]: p[3] for p in PEOPLE if p[0]}
OTR = [p for p in PEOPLE if p[0] and p[2] == "OTR"]
LOCALS = ["NOLAN", "THEO", "DELROY", "CEDRIC", "MILAN"]
KNOWS = {"PALMCO RIVERSIDE": ["NOLAN", "THEO", "DELROY"], "NORTHSTAR AUTO REDLANDS": ["NOLAN", "CEDRIC", "MILAN"],
         "MERIDIAN CORONA": ["THEO", "CEDRIC"], "REDWOOD CARSON": ["DELROY", "MILAN"]}


# ---------------------------------------------------------------- small helpers
def where(name: str) -> tuple[float, float]:
    if name in SITES:
        return SITES[name][1], SITES[name][2]
    return CITY[TOWN_RECEIVERS[name]]


def miles(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3959 * 2 * math.asin(math.sqrt(h)) * 1.25


def cell(name: str) -> str:
    """How the office types a stop: the name, then the town under it."""
    if name == "MERIDIAN GLENDALE":
        return "MERIDIAN GLENDALE\nDOCK 2"            # no state typed: the section heading places it
    town = SITES[name][0] if name in SITES else TOWN_RECEIVERS[name]
    return f"{name}\n{town}"


def clock(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}{':%02d' % dt.minute if dt.minute else ''}{'AM' if dt.hour < 12 else 'PM'}"


def appt(dt: datetime) -> str:
    return f"{dt.month}/{dt.day} - {clock(dt)}"


def dur(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def weekdays_back(today: date, n: int) -> list[date]:
    out, d = [], today
    while len(out) < n:
        if d.weekday() < 5 or d == today:
            out.append(d)
        d -= timedelta(days=1)
    out = [x for x in out if x.year == today.year] or [today]      # tabs carry no year: stay inside this one
    return sorted(out)


# ---------------------------------------------------------------- the board
class Board:
    def __init__(self, today: date, now: datetime):
        self.today, self.now = today, now
        self.rng = random.Random(SEED)
        self.n = {"out": 7300100, "bh": 5200100, "loc": 6100100, "trl": 5900}
        self.tabs: dict[date, list[dict]] = {}
        self.meta: dict[date, dict] = {}

    # -- load makers
    def ref(self, kind: str) -> str:
        self.n[kind] += self.rng.randint(1, 9)
        return str(self.n[kind])

    def trailer(self) -> str:
        self.n["trl"] += 1
        return f"TRL {self.n['trl']}"

    def load(self, kind, section, pickup, delivery, d, **kw) -> dict:
        l = {"kind": kind, "ref": self.ref(kind), "section": section, "pickup": pickup, "delivery": delivery,
             "dno": "", "pre": "", "drv": "", "trk": "", "status": "", "note": "", "c_pick": "", "c_del": "",
             "first": d, "region": "LOCAL"}
        l.update(kw)
        return l

    def new_out(self, code: str, d: date) -> dict:
        rng = self.rng
        rec = rng.choice(RECEIVERS[code])
        if rng.random() < 0.6:
            section, pick = rng.choice(BROKERS), rng.choice(["MERIDIAN CORONA", "HARBOR PAPER ONTARIO", "REDWOOD CARSON"])
        else:
            section = pick = rng.choice(MILLS)
        return self.load("out", section, pick, rec, d, region=code, pre=rng.choice(LOCALS),
                         dno=f"PO {rng.randint(1000, 9999)}" if rng.random() < 0.5 else "")

    def new_bh(self, code: str, shipper: str, d: date) -> dict:
        return self.load("bh", shipper, shipper, self.rng.choice(MILLS), d, region=code, c_pick="READY",
                         note=self.trailer())

    def new_local(self, shipper: str, who: str, d: date, status="DELIVERED") -> dict:
        dest = "HARBOR PAPER ONTARIO" if shipper != "HARBOR PAPER ONTARIO" else "REDWOOD CARSON"
        return self.load("loc", shipper, shipper, dest, d, pre=who, status=status,
                         trk=UNIT.get(who, "") if self.rng.random() < 0.5 else "",
                         c_del=f"{d.month}/{d.day} - {self.rng.choice(['7AM', '9AM', '11AM', '1PM', '3PM'])}")

    # -- a finished past day
    def past_day(self, d: date, idx: int, carry: list[dict], last: bool) -> list[dict]:
        rng = self.rng
        rows, nxt, used = [], [], set()
        trucks = {c: [] for c in CODES}
        pulls, unpulled, outs = [], [], []

        out_loads = [l for l in carry if l["kind"] == "out"]
        for code, lo, hi in (("AZ", 4, 6), ("NV", 2, 4), ("CV", 2, 4)):
            out_loads += [self.new_out(code, d) for _ in range(rng.randint(lo, hi))]
        cedric_day = idx % 4 == 1
        for l in out_loads:
            code = l["region"]
            if l["first"] == d and not last and rng.random() < 0.18:      # staged tonight, delivers tomorrow
                nd = next_weekday(d)
                l.update(status="AT YARD", c_del=f"{nd.month}/{nd.day} - {rng.choice(['8AM', '10AM', '12PM'])}")
                rows.append(dict(l))
                nxt.append(l)
                continue
            pool = [p for p in OTR if code in p[4] and p[0] not in used]
            if code == "NV" and cedric_day and "CEDRIC" not in used:
                who = "CEDRIC"                                            # marked LOCAL on the tab, sent anyway
            elif pool:
                who = rng.choice(pool)[0]
            elif last:
                continue
            else:
                nd = next_weekday(d)
                l.update(status="AT YARD", c_del=f"{nd.month}/{nd.day} - 10AM")
                rows.append(dict(l))
                nxt.append(l)
                continue
            used.add(who)
            name = "KOWLASKI" if who == "KOWALSKI" and rng.random() < 0.2 else who
            t = rng.choice(["8AM", "9AM", "10AM", "11AM", "1PM"])
            l.update(drv=name, trk=f"{UNIT[who]} {rng.choice(['1AM', '2AM', '3AM', '4AM'])}", status="DELIVERED",
                     c_del=f"{d.month}/{d.day} - {t}")
            trucks[code].append((name, l))
            outs.append(l)
            rows.append(dict(l))

        for code in CODES:
            for l in [x for x in carry if x["kind"] == "bh" and x["region"] == code and x["pre"]]:
                l.update(status="DELIVERED", drv=rng.choice(LOCALS))      # pulled yesterday, at the mill today
                rows.append(dict(l))
            waiting = [x for x in carry if x["kind"] == "bh" and x["region"] == code and not x["pre"]]
            for s in SHIPPERS[code]:
                for _ in range((rng.random() < 0.55) + (rng.random() < 0.15)):
                    waiting.append(self.new_bh(code, s, d))
            pullers = [t for t in trucks[code] if rng.random() < 0.78]    # the rest ride home empty
            rng.shuffle(pullers)
            pullers = pullers[:len(waiting)]
            crossed = rng.random() < 0.35                                 # paired from memory, not by distance
            for name, out in pullers:
                l = rng.choice(waiting) if crossed else min(waiting, key=lambda x: miles(where(out["delivery"]), where(x["pickup"])))
                waiting.remove(l)
                done = last or rng.random() < 0.6
                l.update(pre=name, trk="BH", status="DELIVERED" if done else "EN ROUTE TO YARD",
                         drv=rng.choice(LOCALS) if done else "")
                pulls.append((code, name, l))
                rows.append(dict(l))
                if not done:
                    nxt.append(l)
            for l in waiting:
                rows.append(dict(l))
                unpulled.append((code, l))
                if not last and rng.random() < 0.9:
                    nxt.append(l)

        for l in [x for x in carry if x["kind"] == "loc"]:
            l.update(status="DELIVERED")
            rows.append(dict(l))
        locs = []
        for s in LOCAL_SHIPPERS:
            for _ in range(rng.randint(1, 2)):
                l = self.new_local(s, rng.choice(KNOWS[s]), d)
                if not last and rng.random() < 0.15:
                    l["status"] = "AT YARD"
                    nxt.append(l)
                rows.append(dict(l))
                locs.append(l)
        for p in OTR:
            if p[0] not in used and p[0] != "ZEKE" and rng.random() < 0.45:
                rows.append(self.new_local(rng.choice(LOCAL_SHIPPERS), p[0], d))
        for who in ("HUGO", "RAFAEL", "OWEN P"):
            if rng.random() < 0.8:
                rows.append(self.new_local("HARBOR PAPER ONTARIO", who, d))
        self.tabs[d] = rows
        self.meta[d] = {"trucks": trucks, "pulls": pulls, "unpulled": unpulled, "outs": outs, "locs": locs}
        return nxt

    # -- today: written by hand so every page has something to say
    def today_rows(self, carry: list[dict], story: dict) -> list[dict]:
        T, now, rng = self.today, self.now, self.rng
        tom = T + timedelta(days=1)
        rows: list[dict] = []
        L = self.load

        def out(section, pick, rec, code, who="", leave="", status="", when=None, pre="NOLAN", ref=None, **kw):
            l = L("out", section, pick, rec, T, region=code, drv=who, pre=pre, status=status,
                  trk=f"{UNIT[who]} {leave}".strip() if who else "", c_del=appt(when) if when else "", **kw)
            if ref:
                l["ref"], l["first"] = ref["ref"], ref["first"]
            rows.append(l)
            return l

        def bh(shipper, code, pre="", ready=True, trl="", ref=None):
            l = L("bh", shipper, shipper, "HARBOR PAPER ONTARIO", T, region=code, pre=pre, trk="BH" if pre else "",
                  c_pick="READY" if ready else "LOADING", note=f"TRL {trl}" if trl else "")
            if ref:
                l["ref"], l["first"] = ref["ref"], ref["first"]
            rows.append(l)
            return l

        at = lambda h, m=0: datetime(tom.year, tom.month, tom.day, h, m)            # noqa: E731
        ago = lambda mins: now - timedelta(minutes=mins)                             # noqa: E731

        # Arizona — five trucks in, eight trailers waiting
        out("HARBOR PAPER ONTARIO", "HARBOR PAPER ONTARIO", "REDWOOD TOLLESON", "AZ", "MARCO", "4AM",
            "EN ROUTE TO CONSIGNEE", ago(-60), pre="DELROY")                         # will be late
        out("NORTHWIND LOGISTICS", "MERIDIAN CORONA", "SUNPEAK DC", "AZ", "IVAN", "6AM",
            "EN ROUTE TO CONSIGNEE", ago(-480), pre="THEO")
        out("NORTHWIND LOGISTICS", "REDWOOD CARSON", "SAGUARO FOODS", "AZ", "PABLO", "1AM",
            "AT CONSIGNEE", ago(180), pre="MILAN", dno="PO 4471")                    # detention running
        out("KESTREL XPRESS", "HARBOR PAPER ONTARIO", "PIMA PACKAGING", "AZ", "DARNELL", "8PM",
            "DISPATCHED", at(6), pre="CEDRIC")
        out("BLUE RIDGE FREIGHT", "MERIDIAN CORONA", "CHANDLER COLD STORAGE", "AZ", status="AT YARD", when=at(7),
            pre="", ref=story["az_staged"])                                     # nobody on it
        out("HARBOR PAPER ONTARIO", "HARBOR PAPER ONTARIO", "REDWOOD TOLLESON", "AZ", "KOWALSKI", "1AM",
            "DELIVERED", ago(300), pre="DELROY")
        bh("COPPER STATE RECYCLING SCOTTSDALE", "AZ", "MARCO", trl="5312")           # far, while Tolleson waits
        bh("FRESHWAY TOLLESON", "AZ", trl="5318", ref=story["az_ready"])            # READY since two tabs ago
        bh("FRESHWAY TOLLESON", "AZ", trl="5321")
        bh("FRESHWAY TOLLESON", "AZ", trl="5324")
        bh("MERIDIAN GLENDALE", "AZ", "PABLO", trl="5340")
        bh("TRAILHEAD BUCKEYE", "AZ", "KOWALSKI", trl="5345")
        bh("DESERT PET WADDELL", "AZ", ready=False, trl="5350")
        bh("SUNPEAK DC", "AZ", trl="5331")

        # Las Vegas — four trucks in, two trailers waiting
        out("LANTERN BROKERAGE", "MERIDIAN CORONA", "PALMCO VEGAS", "NV", "GRANT", "7AM",
            "EN ROUTE TO CONSIGNEE", ago(-180), pre="CEDRIC")
        out("HARBOR PAPER ONTARIO", "HARBOR PAPER ONTARIO", "NEON HOME GOODS", "NV", "DARRELL", "8AM",
            "EN ROUTE TO CONSIGNEE", ago(-120), pre="NOLAN")                         # GPS 40 minutes old
        out("KESTREL XPRESS", "REDWOOD CARSON", "HIGH DESERT DENIM HENDERSON", "NV", "THEO", "9PM",
            "DISPATCHED", at(8), pre="MILAN")                                        # LOCAL on the DRIVERS tab
        out("LANTERN BROKERAGE", "MERIDIAN CORONA", "DUNE CORRUGATED", "NV", "ZEKE", "6AM",
            "EN ROUTE TO CONSIGNEE", ago(-240), pre="CEDRIC")                        # tracker silent
        bh("PALMCO VEGAS", "NV", "GRANT", trl="5401")
        bh("SILVERLINE RECYCLING VEGAS", "NV", trl="5410")

        # Central Valley — three trucks in, four trailers waiting
        out("REDWOOD CARSON", "REDWOOD CARSON", "REDWOOD TRACY", "CV", "MATEO", "11PM",
            "EN ROUTE TO CONSIGNEE", ago(120), pre="DELROY")                         # delivered, board not updated
        out("REDWOOD CARSON", "REDWOOD CARSON", "REDWOOD EXETER", "CV", "OWEN C", "5AM",
            "EN ROUTE TO CONSIGNEE", ago(-120), pre="MILAN")                         # tight
        out("BLUE RIDGE FREIGHT", "HARBOR PAPER ONTARIO", "GOLDEN CITRUS", "CV", "AMIR", "9PM",
            "AT YARD", at(5), pre="NOLAN")
        out("LANTERN BROKERAGE", "MERIDIAN CORONA", "SAN JOAQUIN BOX", "CV", status="AT YARD", when=at(8),
            pre="", ref=story["cv_staged"])                                     # nobody on it
        bh("DELTA CARTON TRACY", "CV", "MATEO", trl="5510")
        bh("MERIDIAN TULARE", "CV", trl="5501", ref=story["cv_ready"])              # READY since yesterday
        bh("MERIDIAN TULARE", "CV", trl="5502")
        bh("ORCHARD VALLEY FRESNO", "CV", trl="5520")

        # Local
        def loc(shipper, who="", status="DELIVERED", unit=True, drv="", pick_at=None, del_at=None, dest=None):
            l = L("loc", shipper, shipper, dest or ("HARBOR PAPER ONTARIO" if shipper != "HARBOR PAPER ONTARIO"
                                                    else "REDWOOD CARSON"), T,
                  pre=who, drv=drv, status=status, trk=UNIT.get(who, "") if unit else "",
                  c_pick=appt(pick_at) if pick_at else "", c_del=appt(del_at) if del_at else "")
            rows.append(l)
            return l

        loc("PALMCO RIVERSIDE", "NOLAN", del_at=ago(20))                             # at the mill, inside free time
        loc("PALMCO RIVERSIDE", "DELROY", "AT SHIPPER", pick_at=ago(90))             # detention about to start
        loc("PALMCO RIVERSIDE", "TOBIAS", del_at=ago(240))
        loc("NORTHSTAR AUTO REDLANDS", status="")                                    # nobody on it
        loc("NORTHSTAR AUTO REDLANDS", "CEDRIC", del_at=ago(200))
        loc("MERIDIAN CORONA", status="")                                            # nobody on it
        loc("MERIDIAN CORONA", "FELIX", del_at=ago(260))
        loc("MERIDIAN CORONA", "KOWLASKI", unit=False, del_at=ago(600))              # the same man, misspelt
        loc("REDWOOD CARSON", "HECTOR", "AT SHIPPER", pick_at=ago(190))              # direct customer, detention running
        loc("REDWOOD CARSON", "MILAN", "EN ROUTE TO YARD", drv="TRAILER BLOCKED", del_at=ago(-150))   # a note, not a man
        loc("HARBOR PAPER ONTARIO", "HUGO", del_at=ago(150))
        loc("HARBOR PAPER ONTARIO", "RAFAEL", del_at=ago(100))
        loc("HARBOR PAPER ONTARIO", "OWEN P", "AT YARD", unit=False, del_at=ago(-120))   # no truck typed anywhere
        for l in carry:                                                              # yesterday's leftovers, done now
            if l["kind"] == "loc":
                l.update(status="DELIVERED")
                rows.append(dict(l))
        rng.random()
        return rows

    def build(self) -> None:
        days = weekdays_back(self.today, WEEKDAY_TABS)
        past = [d for d in days if d < self.today]
        # four loads that were already on the board before today
        story = {
            "az_ready": self.new_bh("AZ", "FRESHWAY TOLLESON", past[-2] if len(past) > 1 else self.today),
            "cv_ready": self.new_bh("CV", "MERIDIAN TULARE", past[-1] if past else self.today),
            "az_staged": self.load("out", "BLUE RIDGE FREIGHT", "MERIDIAN CORONA", "CHANDLER COLD STORAGE",
                                   past[-1] if past else self.today, region="AZ", status="AT YARD"),
            "cv_staged": self.load("out", "LANTERN BROKERAGE", "MERIDIAN CORONA", "SAN JOAQUIN BOX",
                                   past[-1] if past else self.today, region="CV", status="AT YARD"),
        }
        story["az_ready"]["note"], story["cv_ready"]["note"] = "TRL 5318", "TRL 5501"
        carry: list[dict] = []
        for i, d in enumerate(past):
            carry = self.past_day(d, i, carry, last=(d == past[-1]))
            for k, l in story.items():
                if l["first"] <= d:
                    row = dict(l)
                    if l["kind"] == "out":
                        nd = next_weekday(d)
                        row["c_del"] = f"{nd.month}/{nd.day} - {'7AM' if k == 'az_staged' else '8AM'}"
                    self.tabs[d].append(row)
            if d == past[-1]:                               # make sure tomorrow's spare drivers were seen yesterday
                on = {l["pre"] for l in self.tabs[d]} | {l["drv"] for l in self.tabs[d]}
                for who in ("NATHANIEL", "BRUNO", "FELIX", "HECTOR", "TOBIAS"):
                    if who not in on:
                        self.tabs[d].append(self.new_local(self.rng.choice(LOCAL_SHIPPERS), who, d))
        self.tabs[self.today] = self.today_rows(carry, story)

    # -- upcoming loads
    def upcoming(self) -> list[dict]:
        rng, out = self.rng, []
        plan = {1: {"AZ": 2, "NV": 1}, 2: {"AZ": 3, "CV": 5, "NV": 2}, 3: {"AZ": 8, "NV": 2, "CV": 1},
                4: {"AZ": 3, "CV": 2}, 5: {"NV": 2, "AZ": 1}}
        for off, per in plan.items():
            d = self.today + timedelta(days=off)
            for code, n in per.items():
                for _ in range(n):
                    l = self.new_out(code, d)
                    l.update(pre="", section=rng.choice(BROKERS), c_pick=f"{d.month}/{d.day} - {rng.choice(['5AM', '6AM', '7AM'])}",
                             c_del=f"{d.month}/{d.day} - {rng.choice(['12PM', '2PM', '4PM'])}", status="")
                    out.append(l)
        return out


# ---------------------------------------------------------------- writing the workbook
def write_tab(ws, title: str, rows: list[dict]) -> None:
    bold, grey = Font(bold=True), PatternFill("solid", fgColor="D9D9D9")
    ws.append([title])
    ws.append([])
    ws.append(HEADERS)
    ws["A1"].font = Font(bold=True, size=14)
    for c in ws[3]:
        c.font = bold
    order: list[str] = []
    for l in rows:
        if l["section"] not in order:
            order.append(l["section"])
    order.sort(key=lambda s: (s not in BROKERS, s not in MILLS))        # brokers, the mills, then shippers
    for sec in order:
        ws.append([sec])
        for col in range(1, 11):
            ws.cell(ws.max_row, col).fill = grey
        ws.cell(ws.max_row, 1).font = bold
        for l in rows:
            if l["section"] != sec:
                continue
            ws.append([l["ref"], cell(l["pickup"]), cell(l["delivery"]), l["dno"], l["pre"], l["drv"], l["trk"],
                       l["status"], l["note"], ""])
            if l["c_pick"] or l["c_del"]:
                ws.append(["", l["c_pick"], l["c_del"]])
    for col, w in zip("ABCDEFGHIJ", (12, 30, 30, 12, 14, 18, 12, 26, 12, 8)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=4):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")


def write_workbook(b: Board, path: Path) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for d in sorted(b.tabs):
        write_tab(wb.create_sheet(f"{d.month}.{d.day}"), f"{d:%A}".upper() + f" {d.month}/{d.day}", b.tabs[d])
    write_tab(wb.create_sheet("UPCOMING LOADS"), "UPCOMING LOADS", b.upcoming())
    ws = wb.create_sheet("DRIVERS")
    ws.append(["DRIVER NAME", "WORKLOAD", "COMP", "CDL EXPIRY", "MEDICAL"])
    rng, T = random.Random(SEED + 1), b.today
    for board_name, tab_name, work, _, _, _ in PEOPLE:
        if not tab_name:
            continue
        cdl = T + timedelta(days=rng.randint(200, 900))
        med = T + timedelta(days=rng.randint(60, 600))
        if board_name == "QUENTIN":
            med = T - timedelta(days=12)                    # medical date passed
        if board_name == "AMIR":
            cdl = T + timedelta(days=18)                    # licence about to expire
        if board_name == "RAFAEL":
            med = T + timedelta(days=9)
        ws.append([tab_name, work, "SF", datetime(cdl.year, cdl.month, cdl.day), datetime(med.year, med.month, med.day)])
    for col, w in zip("ABCDE", (26, 12, 8, 14, 14)):
        ws.column_dimensions[col].width = w
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---------------------------------------------------------------- GPS
def gps(b: Board) -> dict:
    rng = random.Random(SEED + 2)
    full = {p[0]: " ".join(reversed([x.strip() for x in p[1].split(",")])) for p in PEOPLE if p[0] and p[1]}
    full["ZEKE"] = "ZEKE"
    yard = SITES["HOME YARD"][1:3]

    def at(name, jitter=0.0008):
        la, lo = where(name)
        return la + rng.uniform(-jitter, jitter), lo + rng.uniform(-jitter, jitter)

    # board name -> (lat, lng, state, minutes in that state, age of the fix in minutes)
    live = {
        "MARCO": (33.61, -114.60, "moving", 190, 2),
        "IVAN": (33.72, -116.22, "moving", 95, 3),
        "PABLO": (*CITY["PHOENIX, AZ"], "stopped", 210, 4),
        "DARNELL": (*yard, "stopped", 850, 5),
        "KOWALSKI": (*at("TRAILHEAD BUCKEYE"), "stopped", 65, 2),
        "GRANT": (35.27, -116.07, "moving", 120, 1),
        "DARRELL": (34.90, -117.02, "moving", 70, 40),                 # 40 minutes old
        "THEO": (*yard, "stopped", 400, 6),
        "ZEKE": (*CITY["LAS VEGAS, NV"], "stopped", 4320, 4320),        # silent for three days
        "MATEO": (*at("DELTA CARTON TRACY"), "stopped", 50, 3),
        "OWEN C": (35.37, -119.02, "moving", 150, 2),
        "AMIR": (*yard, "stopped", 600, 4),
        "DELROY": (*at("PALMCO RIVERSIDE"), "stopped", 90, 2),
        "HECTOR": (*at("REDWOOD CARSON"), "stopped", 190, 3),
        "NOLAN": (*at("HARBOR PAPER ONTARIO"), "stopped", 25, 1),
        "MILAN": (33.95, -117.95, "moving", 35, 2),
        "CEDRIC": (*at("NORTHSTAR AUTO REDLANDS"), "stopped", 15, 5),
        "HUGO": (34.02, -117.80, "moving", 20, 1),
    }
    devices = []
    for i, p in enumerate([p for p in PEOPLE if p[0]], 1):
        name = p[0]
        la, lo, state, mins, age = live.get(name, (yard[0] + rng.uniform(-0.001, 0.001), yard[1] + rng.uniform(-0.001, 0.001),
                                                   "stopped", rng.randint(200, 900), rng.randint(1, 9)))
        label = p[3] if name == "OWEN P" else f"{p[3]} {full[name]}"      # one tracker has no driver name on it
        devices.append({"id": 100 + i, "unit": p[3], "kind": "tractor", "driver": "" if name == "OWEN P" else full[name],
                        "lat": round(la, 5), "lng": round(lo, 5), "speed": 62 if state == "moving" else 0, "state": state,
                        "state_dur": dur(mins), "age_min": age, "odometer": rng.randint(90, 480) * 1000 + rng.randint(0, 999),
                        "name": label})
    parked = {
        "FRESHWAY TOLLESON": ["5318", "5321", "5324"], "SUNPEAK DC": ["5330", "5331", "5332", "5333"],
        "COPPER STATE RECYCLING SCOTTSDALE": ["5312", "5313"], "MERIDIAN GLENDALE": ["5340", "5341"],
        "TRAILHEAD BUCKEYE": ["5345"], "DESERT PET WADDELL": ["5350", "5351"],
        "PALMCO VEGAS": ["5401", "5402"], "SILVERLINE RECYCLING VEGAS": ["5410", "5411", "5412"],
        "HIGH DESERT DENIM HENDERSON": ["5420", "5421"],
        "MERIDIAN TULARE": ["5501", "5502", "5503"], "DELTA CARTON TRACY": ["5510", "5511"],
        "ORCHARD VALLEY FRESNO": ["5520", "5521"],
        "PALMCO RIVERSIDE": ["5601", "5602", "5603"], "NORTHSTAR AUTO REDLANDS": ["5610", "5611"],
        "MERIDIAN CORONA": ["5620"], "REDWOOD CARSON": ["5630", "5631"],
        "HARBOR PAPER ONTARIO": ["5640", "5641", "5642"],
        "HOME YARD": [str(n) for n in range(5701, 5709)],
    }
    n = 500
    for site, units in parked.items():
        for u in units:
            n += 1
            la, lo = at(site)
            devices.append({"id": n, "unit": u, "kind": "trailer", "driver": "", "lat": round(la, 5), "lng": round(lo, 5),
                            "speed": 0, "state": "stopped", "state_dur": dur(rng.randint(300, 4000)),
                            "age_min": rng.randint(2, 55), "odometer": 0, "name": f"TRL {u}"})
    devices.append({"id": 598, "unit": "5801", "kind": "trailer", "driver": "", "lat": 33.61, "lng": -114.60, "speed": 62,
                    "state": "moving", "state_dur": "3h 10m", "age_min": 2, "odometer": 0, "name": "TRL 5801"})
    pois = [{"name": FENCE_NAME.get(k, k), "lat": v[1], "lng": v[2], "radius_m": 500 if k == "HOME YARD" else 400}
            for k, v in SITES.items()]
    mateo = next(d["id"] for d in devices if d["unit"] == UNIT["MATEO"])
    rt = where("REDWOOD TRACY")
    history = {str(mateo): [{"lat": rt[0], "lng": rt[1], "start_min_ago": 185, "end_min_ago": 70}]}
    return {"_note": "Simulated GPS for the demo. Every unit, name and position is invented.",
            "generated": b.now.isoformat(timespec="minutes"), "devices": devices, "pois": pois, "history": history}


# ---------------------------------------------------------------- the desk's memory of earlier days
def seed_logs(b: Board, state: Path) -> None:
    rng = random.Random(SEED + 3)
    (state / "log").mkdir(parents=True, exist_ok=True)
    past = sorted(d for d in b.tabs if d < b.today)[-4:]
    for d in past:
        m = b.meta[d]
        at = datetime(d.year, d.month, d.day, 6, 15).isoformat(timespec="minutes")
        log = {"day": d.isoformat(), "drivers": {}, "backhauls": {}, "local": {}}
        for code, name, l in m["pulls"]:
            if l["first"] != d:
                continue
            tk = m["trucks"][code]
            near = min(tk, key=lambda t: miles(where(t[1]["delivery"]), where(l["pickup"])))
            pick = near if rng.random() < 0.85 else rng.choice(tk)
            log["backhauls"][l["ref"]] = {
                "who": pick[0], "alts": [], "where": REGION_NAME[code], "customer": l["section"],
                "empty_mi": round(miles(where(pick[1]["delivery"]), where(l["pickup"]))), "at": at}
        for code, l in m["unpulled"]:
            tk = m["trucks"][code]
            if l["first"] == d and tk and rng.random() < 0.6:
                pick = min(tk, key=lambda t: miles(where(t[1]["delivery"]), where(l["pickup"])))
                log["backhauls"][l["ref"]] = {
                    "who": pick[0], "alts": [], "where": REGION_NAME[code], "customer": l["section"],
                    "empty_mi": round(miles(where(pick[1]["delivery"]), where(l["pickup"]))), "at": at}
        for l in m["outs"]:
            if rng.random() < 0.45:
                code = l["region"]
                others = [p[0] for p in OTR if code in p[4] and p[0] != l["drv"]]
                rng.shuffle(others)
                roll = rng.random()
                who, alts = (l["drv"], others[:2]) if roll < 0.45 else \
                    (others[0], [l["drv"], others[1]]) if roll < 0.7 else (others[0], others[1:3])
                log["drivers"][l["ref"]] = {"who": who, "alts": alts, "where": REGION_NAME[code],
                                            "customer": l["section"], "at": at}
        for l in m["locs"][:4]:
            others = [w for w in KNOWS[l["section"]] if w != l["pre"]]
            who = l["pre"] if rng.random() < 0.6 else others[0]
            log["local"][l["ref"]] = {"who": who, "alts": [w for w in KNOWS[l["section"]] if w != who][:2],
                                      "where": "Local", "customer": l["section"], "at": at}
        (state / "log" / f"{d.isoformat()}.json").write_text(json.dumps(log, indent=1), encoding="utf-8")


def seed_pools(b: Board, state: Path) -> None:
    rng = random.Random(SEED + 4)
    usual = {"FRESHWAY TOLLESON": 5, "SUNPEAK DC": 4, "COPPER STATE RECYCLING SCOTTSDALE": 2, "MERIDIAN GLENDALE": 2,
             "TRAILHEAD BUCKEYE": 1, "DESERT PET WADDELL": 2, "PALMCO VEGAS": 5, "SILVERLINE RECYCLING VEGAS": 3,
             "MERIDIAN TULARE": 3, "DELTA CARTON TRACY": 2, "ORCHARD VALLEY FRESNO": 2, "PALMCO RIVERSIDE": 3,
             "NORTHSTAR AUTO REDLANDS": 2, "MERIDIAN CORONA": 1, "REDWOOD CARSON": 2, "HARBOR PAPER ONTARIO": 3}
    hist = {}
    for d in sorted(x for x in b.tabs if x < b.today)[-15:]:
        hist[d.isoformat()] = {s: max(0, n + rng.choice([-1, 0, 0, 0, 1])) for s, n in usual.items()}
    (state / "pool_history.json").write_text(json.dumps(hist, indent=1), encoding="utf-8")
    needs = {"_how_to": "For each customer, set trailers_on_site to the number of our trailers they want "
                        "sitting at their dock (null = not set). notes is free text.",
             "customers": {
                 "FRESHWAY TOLLESON": {"trailers_on_site": 7, "notes": "pool size the customer stated (sample)"},
                 "SUNPEAK DC": {"trailers_on_site": 4, "notes": "pool size the customer stated (sample)"},
                 "MERIDIAN TULARE": {"trailers_on_site": 3, "notes": "pool size the customer stated (sample)"}}}
    (state / "customer_needs.json").write_text(json.dumps(needs, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- the small files
def small_files(b: Board, out: Path) -> None:
    T, now = b.today, b.now
    yest = (T - timedelta(days=1)).isoformat()
    w = lambda name, obj: (out / name).write_text(json.dumps(obj, indent=1), encoding="utf-8")     # noqa: E731
    w("authority.json", {p[0]: p[5] for p in PEOPLE if p[0] and p[5]})
    makes = [("Freightliner", "CASCADIA"), ("Kenworth", "T680"), ("Peterbilt", "579"), ("Volvo", "VNL 760"),
             ("International", "LT")]
    rng = random.Random(SEED + 5)
    tractors = []
    for i in range(1, 27):                                  # SF26 is on the list but has no tracker
        mk = makes[i % len(makes)]
        tractors.append({"name": f"SF{i:02d}", "group": "TRACTOR", "year": str(2018 + i % 7), "make": mk[0], "model": mk[1],
                         "vin": f"DEMOVIN{i:010d}", "plate": f"DEMO{i:03d}", "state": "CA",
                         "own": rng.choice(["Owned", "Owned", "Leased"]), "miles": ""})
    w("equipment.json", {"_note": "Invented equipment list for the demo.", "pulled": yest, "tractors": tractors})
    w("fuel.json", {"_note": "Fictional round figures for the demo.",
                    "driverScore": {"fleetMpg": 6.0, "generated": yest},
                    "fuelDaily": {"netPpg": 5.0, "asOf": yest, "windowDays": 14}})
    w("detention_terms.json", {"_note": "Free minutes before detention starts; anything not listed gets 120.",
                               "free_min": {"SUNPEAK": 180, "HARBOR PAPER": 120}})
    db = out / "offers.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute("create table offers (source text, load_id text, origin text, destination text, rate real, "
                "pickup text, last_seen text)")
    seen = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    tom = T + timedelta(days=1)
    con.executemany("insert into offers values (?,?,?,?,?,?,?)", [
        ("kestrel", "KX-48213", "Henderson, NV", "Ontario, CA", 1000, f"{T.month}/{T.day}/{T.year} 16:00", seen),
        ("northwind", "NW-77120", "Las Vegas, NV", "Fontana, CA", 0, f"{tom.month}/{tom.day}/{tom.year} 07:00", seen),
        ("lantern", "LB-5521", "Phoenix, AZ", "Riverside, CA", 1200, f"{tom.month}/{tom.day}/{tom.year} 09:00", seen),
    ])
    con.commit()
    con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate the demo's fictional sample data.")
    ap.add_argument("--out", default=os.environ.get("DESK_DATA_DIR") or str(REPO / "demo" / "sample"))
    ap.add_argument("--state", default=None, help="the desk's state folder (default <out>/state)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    state = Path(a.state or os.environ.get("DESK_STATE_DIR") or out / "state")
    out.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    now = datetime.now().replace(second=0, microsecond=0)
    now -= timedelta(minutes=now.minute % 15)
    today = date.today()
    b = Board(today, now)
    b.build()
    for old in out.glob("*.xlsx"):
        old.unlink()
    book = out / (f"{today:%B} {today.year}".upper() + ".xlsx")
    write_workbook(b, book)
    (out / "gps.json").write_text(json.dumps(gps(b), indent=1), encoding="utf-8")
    small_files(b, out)
    seed_logs(b, state)
    seed_pools(b, state)
    (out / "sample.json").write_text(json.dumps({
        "generated": datetime.now().isoformat(timespec="seconds"), "today": today.isoformat(), "seed": SEED,
        "note": "Fictional sample data. Regenerate with: py demo/make_sample.py"}, indent=1), encoding="utf-8")
    loads = sum(len(v) for v in b.tabs.values())
    print(f"[sample] {book.name}: {len(b.tabs)} daily tabs, {loads} load rows, "
          f"{sum(1 for p in PEOPLE if p[1])} drivers on the DRIVERS tab")
    print(f"[sample] written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
