"""STAND-IN for a GPS vendor client. It never touches the network.

The Dispatch Desk was written against a small client module for the
FleetHunt tracking API (`import fleethunt_api as fh`). That client, and its
key, are not part of this repository. This file offers the same few functions
and reads a generated sample file instead:

    <DESK_DATA_DIR>/gps.json        (written by demo/make_sample.py)

    positions()            every tracked tractor and trailer, as Device objects
    fetch_pois()           the geofences: [{"name", "lat", "lng", "radius_m"}]
    fetch_history(id, day) {"stops": [{"latitude", "longitude", "stop_start", "stop_end"}]}
    BASE, _key()           present because the desk's one-off backfill tool names them

The simulated trackers report relative to the clock: a device stored with
"age_min": 40 is always "last heard from 40 minutes ago", whenever you run it.
To use real GPS, replace this module with a client that returns the same shapes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

BASE = "http://gps.invalid/api"         # never called
LIVE_MAX_MIN = 6 * 60


@dataclass
class Device:
    unit: str            # board unit: SF07 (tractor) or 5312 (trailer)
    kind: str            # 'tractor' | 'trailer'
    driver: str          # name hint from the device label, may be ""
    lat: float
    lng: float
    speed: float         # mph
    state: str           # 'moving' | 'idle' | 'stopped' | 'offline'
    moving: bool
    odometer: float
    dt_tracker: str      # last report, 'YYYY-MM-DD HH:MM:SS'
    age_min: float       # minutes since that report
    name: str            # the raw device label
    state_dur: str = ""  # how long in the current state ('1d 5h 40m')
    device_id: int = 0

    @property
    def live(self) -> bool:
        return self.age_min <= LIVE_MAX_MIN


def _key() -> None:
    return None                          # there is no key: nothing here can reach a real service


def _file() -> Path:
    base = os.environ.get("DESK_DATA_DIR") or Path(__file__).resolve().parent.parent / "demo" / "sample"
    return Path(base) / "gps.json"


def _data() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def positions(devices=None, now=None) -> list[Device]:
    clock = datetime.now()
    out = []
    for r in (devices if devices is not None else _data().get("devices", [])):
        age = float(r.get("age_min", 0))
        state = r.get("state", "stopped")
        seen = clock - timedelta(minutes=min(age, 60 * 24 * 365))
        out.append(Device(
            unit=r["unit"], kind=r["kind"], driver=r.get("driver", ""), lat=float(r["lat"]), lng=float(r["lng"]),
            speed=float(r.get("speed", 0)), state=state, moving=(state == "moving"),
            odometer=float(r.get("odometer", 0)), dt_tracker=f"{seen:%Y-%m-%d %H:%M:%S}", age_min=age,
            name=r.get("name", r["unit"]), state_dur=r.get("state_dur", ""), device_id=int(r.get("id", 0))))
    return out


def fetch_pois(key=None, base: str = BASE) -> list[dict]:
    return [{"name": p["name"].upper(), "lat": float(p["lat"]), "lng": float(p["lng"]),
             "radius_m": float(p.get("radius_m", 200))} for p in _data().get("pois", [])]


def fetch_history(device_id: int, day: str, min_stop: int = 10, key=None, base: str = BASE) -> dict:
    """Stops for one device on one local day ('YYYY-MM-DD'). The sample stores
    them as minutes before "now"; they are turned into clock times here."""
    clock = datetime.now()
    stops = []
    for s in _data().get("history", {}).get(str(device_id), []):
        a = clock - timedelta(minutes=s["start_min_ago"])
        b = clock - timedelta(minutes=s["end_min_ago"])
        if f"{a:%Y-%m-%d}" == day or f"{b:%Y-%m-%d}" == day:
            stops.append({"latitude": str(s["lat"]), "longitude": str(s["lng"]),
                          "stop_start": f"{a:%Y-%m-%d %H:%M:%S}", "stop_end": f"{b:%Y-%m-%d %H:%M:%S}"})
    return {"stops": stops}
