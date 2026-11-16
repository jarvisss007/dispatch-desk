r"""Everything about ONE carrier, in one place.

The rest of the package is rules (how to pair a backhaul, when detention
starts, what counts as an empty trip). This file is the part another carrier
would change: its name, where its yard is, how it numbers its trucks, which
towns make up each of its regions, and where its files live.

The values below are the DEMO values: a fictional carrier, "Sample Freight",
with a yard in Fontana, CA. To use your own, edit this file, or point the
environment variable DESK_SETTINGS_FILE at a JSON file whose keys are the
names below (paths as strings, YARD as [lat, lng], REGIONS as
[[code, name, regex], ...]).

Paths can also be moved one at a time with environment variables:

    DESK_OUT_DIR      pages are written to <DESK_OUT_DIR>/DESK   (default ./out)
    DESK_DATA_DIR     inputs: the board workbook and the small data files
                      (default ./demo/sample)
    DESK_STATE_DIR    what the desk remembers between runs: suggestion log,
                      trailer counts, pool sizes (default <DESK_DATA_DIR>/state)
    DESK_BOARD_FILE   one workbook to read, instead of the newest monthly one
    DESK_FUEL_FILE    the fuel figures file for the empty-trips page
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v) if v else default


# ---------------------------------------------------------------- who
COMPANY = "Sample Freight"
MAIL_DOMAIN = "samplefreight.example"       # mail from this domain is "ours", anything else is a customer
MAILBOX = "dispatch mailbox"

# ---------------------------------------------------------------- where files live
OUT_DIR = _env_path("DESK_OUT_DIR", REPO / "out")
DATA_DIR = _env_path("DESK_DATA_DIR", REPO / "demo" / "sample")
STATE_DIR = _env_path("DESK_STATE_DIR", DATA_DIR / "state")
BOARD_FILE = os.environ.get("DESK_BOARD_FILE") or None
BOARD_DIRS = [DATA_DIR]                     # searched for the newest "<MONTH> <YEAR>.xlsx"
FUEL_FILE = _env_path("DESK_FUEL_FILE", DATA_DIR / "fuel.json")
DETENTION_TERMS_FILE = DATA_DIR / "detention_terms.json"
GEOCODE_FILE = DATA_DIR / "geocode.json"    # optional street-level cache: {"v2|<CELL TEXT>": {"lat":, "lng":}}
OFFERS_DB = DATA_DIR / "offers.db"          # optional sqlite file of emailed broker offers
MONEY_FEEDS: dict = {}                      # optional: {"pod_chase": path, "finance": path} for the home page
EXTRA_PAGES: list = []                      # optional: [("Group", [("Label", "../PAGE.html"), ...])]

# ---------------------------------------------------------------- the yard and the trucks
YARD = (34.092, -117.435)                   # Fontana, CA (city centre)
YARD_FENCE = "HOME YARD"                    # the yard's geofence name in the GPS portal
SKIP_FENCES = r"HOME YARD|SHOP|FUEL"        # geofences that are not customers
UNIT_PATTERN = r"\b(SF\s?-?\d{2,3})\b"      # how a tractor is written in the TRK cell: SF07, SF-12

# ---------------------------------------------------------------- regions
# (code, name, regex over the pickup / delivery text). Anything else is LOCAL.
REGIONS = [
    ("AZ", "Arizona",
     r",\s*AZ\b|PHOENIX|TOLLESON|BUCKEYE|GLENDALE,?\s*AZ|PEORIA|TUCSON|"
     r"SCOTTSDALE|\bMESA\b|CHANDLER|WADDELL|GOODYEAR|TEMPE|YUMA"),
    ("NV", "Las Vegas", r",\s*NV\b|VEGAS|HENDERSON|PRIMM|\bRENO\b"),
    ("CV", "Central Valley",
     r"TULARE|FRESNO|TRACY|BAKERSFIELD|DELANO|SANGER|DINUBA|PARLIER|KINGSBURG|"
     r"VISALIA|STOCKTON|LATHROP|MODESTO|MADERA|EXETER|PORTERVILLE|GOSHEN|WATSONVILLE"),
]
# A section heading that names a town which also exists locally: (regex over the section, region).
SECTION_REGION_HINTS = [(r"MERIDIAN GLENDALE", "AZ")]

# ---------------------------------------------------------------- customers
# Customers billed directly on the driver's written gate times (not through a broker).
DIRECT_CUSTOMERS = r"HARBOR PAPER|REDWOOD"
# Board sections that are brokers, not shippers.
BROKER_SECTIONS = r"NORTHWIND|BLUE RIDGE|LANTERN|KESTREL"
# Workloads on the DRIVERS tab that are tied to one site (never sent out of town).
SITE_WORKLOADS = "LOCAL, BALE or SHUTTLE"


def _apply_overrides() -> None:
    f = os.environ.get("DESK_SETTINGS_FILE")
    if not f:
        return
    raw = json.loads(Path(f).read_text(encoding="utf-8"))
    g = globals()
    for k, v in raw.items():
        if k not in g or k.startswith("_"):
            raise KeyError(f"unknown setting {k!r} in {f}")
        if isinstance(g[k], Path):
            v = Path(v)
        elif k == "YARD":
            v = tuple(v)
        elif k in ("REGIONS", "SECTION_REGION_HINTS"):
            v = [tuple(x) for x in v]
        g[k] = v


_apply_overrides()
EYEBROW = f"{COMPANY} · Dispatch Desk"
