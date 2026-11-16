r"""Where a board cell is on the map — offline, three sources, best first.

  1. a GPS-portal geofence with the same name  (the customer's real gate)
  2. an optional address cache                  (street-level, read-only)
  3. the city named in the text                 (city centre — good to a few miles)

Miles are straight-line x 1.25, which is what highway distance runs to on
these corridors. They are for RANKING which backhaul is nearer, not for
billing or pay. Nothing here calls a geocoding service.
"""
from __future__ import annotations

import json
import math
import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dispatch_desk import pools, settings  # noqa: E402

ROAD_FACTOR = 1.25
YARD = tuple(settings.YARD)        # where the trailers sit; every 'miles home' is measured to here
_CITY_ST = re.compile(r"([A-Z][A-Z .']{2,24}?)\s*,?\s*\b(AZ|NV|CA)\b")


def miles(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3959 * 2 * math.asin(math.sqrt(h)) * ROAD_FACTOR


@lru_cache(maxsize=1)
def _fences() -> tuple:
    try:
        import fleethunt_api as fh
        return tuple((f["name"], float(f["lat"]), float(f["lng"])) for f in fh.fetch_pois()
                     if f.get("lat") and f.get("lng"))
    except Exception:
        return ()


@lru_cache(maxsize=1)
def _cities() -> dict:
    try:
        import fleet_live
        return {n.upper(): (la, lo) for n, la, lo in fleet_live.CITIES}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _addr_cache() -> dict:
    try:
        raw = json.loads(settings.GEOCODE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k[3:]: (v["lat"], v["lng"]) for k, v in raw.items()
            if k.startswith("v2|") and isinstance(v, dict) and "lat" in v}


@lru_cache(maxsize=4096)
def locate(text: str, section: str = "") -> tuple | None:
    """(lat, lng, how) or None. `section` is the board's shipper heading."""
    fences = [{"name": n, "lat": la, "lng": lo} for n, la, lo in _fences()]
    up = re.sub(r"\s+", " ", (text or "").upper()).strip()
    first = (text or "").split("\n")[0]
    for name in (section, first):
        if name and name.strip():
            f = pools.fence_for(name, fences)
            if f:
                return (f["lat"], f["lng"], "gate")
    if up in _addr_cache():
        return (*_addr_cache()[up], "address")
    cities = _cities()
    for m in _CITY_ST.finditer(up):
        words = m.group(1).replace(".", "").split()
        for n in (3, 2, 1):                     # 'NORTH LAS VEGAS' before 'VEGAS'
            if len(words) >= n:
                key = f"{' '.join(words[-n:])}, {m.group(2)}"
                key = re.sub(r"^N ", "NORTH ", key)
                if key in cities:
                    return (*cities[key], "city")
    for key, pos in cities.items():             # 'DOCK 217 TUCSON' — city with no state
        if re.search(rf"\b{re.escape(key.split(',')[0])}\b", up) and key.endswith((" AZ", " NV")):
            return (*pos, "city")
    return None
