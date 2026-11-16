# Dispatch Desk

A self-running dispatch assistant for a regional trucking carrier. It reads the office's dispatch board (an Excel workbook with one tab per day), the trucks' GPS and the dispatch mailbox, and publishes pages that say what needs doing and what it would do. It only suggests: it never writes to the board, never books a load, never sends an email and never messages a driver.

This repository is a standalone demo. The desk was built for a working carrier; here it runs against a fictional one, "Sample Freight", with generated data. Every driver, customer, load number, trailer number, VIN and plate in the demo is invented. Only the city names are real places. Any resemblance of an invented name to a real person or business is coincidental.

## Run it

```
py -m pip install -r requirements.txt
py demo/make_sample.py
py run_demo.py
py -m pytest tests -q
```

1. `py demo/make_sample.py` writes the sample board and the small data files to `./demo/sample/`.
2. `py run_demo.py` rebuilds that sample for the current time, runs the whole desk against it and writes the pages to `./out/DESK/`. Open `./out/DESK/INDEX.html`. Add `--keep` to reuse the sample already there.
3. `py -m pytest tests -q` runs 33 tests: 32 on the rules, 1 that the demo run produces its pages.

No network, no keys and no accounts are needed. Requirements: Python 3.10 or later, `openpyxl`, `numpy`, `scipy`, `pytest`. On Linux or macOS use `python3` in place of `py`.

## What this demo shows

The sample is four weeks of weekday tabs ending today, 25 drivers and about 20 customers across Southern California, Arizona, Las Vegas and the Central Valley. Today's tab is written so that every page has something on it:

- **Day plan.** A truck paired by hand with a trailer 29 miles away while a READY one sits a mile from its drop; two out-of-town loads with no driver, each given a suggested driver and a backhaul; a truck coming home empty from Las Vegas, with the spare trailers and emailed broker offers near it; a local driver put on a Las Vegas load.
- **Tracking.** One load that will be late, one tight, one whose GPS position is 40 minutes old (shown, not judged), one silent tracker, one delivered load the board has not caught up with, and one driver with no truck typed anywhere.
- **Trailer pools.** One customer short against the pool size it stated, one short against its own usual level.
- **Detention.** Two trucks past their free time and one about to be.
- **Roster.** A medical date that has passed (that driver is never offered an out-of-town load), a driver the board spells two ways, two different drivers whose names differ by one letter, and a driver marked local who has run out of town.
- **Empty trips home, score, week ahead, tomorrow's draft, driver packets, trucks.** Built from the same four weeks.

## Before and now

| | Done by hand | With the desk |
|---|---|---|
| Backhauls | Paired on the board, from memory | Every truck going out is matched to the nearest load coming back, across all trucks at once, with the empty miles shown |
| Loads with no driver | Found by scrolling the tab | Listed, with a suggested driver who is allowed to run that region and is free |
| Will a load make its time | Phone calls | Every open load checked against live GPS and its appointment; late, tight and already-delivered loads flagged |
| Old GPS | Not visible | A verdict only from a position under 20 minutes old; older ones are shown as "last seen" and not judged |
| Trailer pools at customers | Learned when a customer complained | Counted from trailer GPS against what each customer was promised |
| Tomorrow's tab | Retyped each afternoon | Drafted automatically from the loads still open |
| Driver paperwork | Written out per driver | One printable packet per driver, stops in running order |
| Trucks coming home empty | Not counted | Counted for every day on the workbook, by region, week, weekday and receiver, with the fuel burned |
| Did the suggestions help | No record | Every suggestion logged the first time it is made and scored against what the office did |

## What is in here

| File | What it does |
|---|---|
| `dispatch_desk/plan.py` | The day plan: trucks out matched to loads back, by region; builds most pages and the data feed |
| `dispatch_desk/assign.py` | Suggests a driver for each out-of-town load that has none |
| `dispatch_desk/actions.py` | The worklist at the top of the day plan, with the ways out of each problem |
| `dispatch_desk/track.py` | Tracking: will each open load make its appointment, from live GPS |
| `dispatch_desk/live.py` | Trucks sitting at a customer right now and whether detention is running |
| `dispatch_desk/pools.py` | Trailers at each customer against the number promised |
| `dispatch_desk/empties.py` | Empty trips home and the fuel burned on them |
| `dispatch_desk/score.py` | The score: what the desk suggested against what the office did |
| `dispatch_desk/tomorrow.py` | Drafts tomorrow's tab from the loads still open |
| `dispatch_desk/packets.py` | The printable packet each driver picks up |
| `dispatch_desk/roster.py` | Who may run where, read from the board's DRIVERS tab |
| `dispatch_desk/trucks.py` | Every tractor on one printable sheet |
| `dispatch_desk/mail.py` | Customer emails nobody has answered yet (read-only) |
| `dispatch_desk/extras.py` | Trailer swap clock, emailed broker offers, weekly empty miles, the week ahead |
| `dispatch_desk/geo.py` | Places a board cell on the map |
| `dispatch_desk/source.py` | Opens a copy of the board so the office's file is never touched |
| `dispatch_desk/home.py` | The front page |
| `dispatch_desk/settings.py` | Everything specific to one carrier: name, yard, truck numbering, regions, file locations |
| `board.py` | Reads the board workbook: tabs, sections, load rows, status wording |
| `page_design.py` | The page shell: dark on screen, white on paper |
| `config.py` | Where `board.py` finds the workbook |
| `standins/` | Offline stand-ins for the GPS client, the mailbox client, the city table, the older roster and the equipment list |
| `demo/make_sample.py` | Generates the fictional board and data files |
| `run_demo.py` | Generates the sample and runs the desk against it |
| `tests/` | 33 tests |

## Using it for a real carrier

`dispatch_desk/settings.py` holds the demo's values and says what each one is. Change them there, or point `DESK_SETTINGS_FILE` at a JSON file with the same names. Folders can be moved with `DESK_DATA_DIR`, `DESK_STATE_DIR`, `DESK_OUT_DIR`, `DESK_BOARD_FILE` and `DESK_FUEL_FILE`.

The modules in `standins/` must then be replaced with real ones that return the same shapes: a GPS client (`fleethunt_api`: `positions()`, `fetch_pois()`, `fetch_history()`), a read-only mailbox client (`gmail_draft.get_service()`), and the roster and equipment lists. `py -m dispatch_desk` runs the desk once; schedule it every 15 minutes.

## Limits

- It is the board's view. A backhaul that was pulled but never typed on the board reads as an empty trip.
- Miles are straight-line distances times a road factor, not routed miles.
- Arrival times assume 50 mph with no traffic, fuel or rest stops.
- A note typed in a driver cell ("TRAILER BLOCKED") is ignored by tracking but still gets a driver packet.
- Tabs carry no year. A workbook that runs from December into January needs a look; the sample generator keeps its tabs inside the current year, so the demo is thin in the first weeks of January.

Limits of the demo itself:

- There is no mailbox. The front page says so and the "customers waiting" block is empty.
- GPS is simulated: positions are fixed, and each one is always as old as the sample says ("40 minutes ago").
- The suggestion log for the four weekdays before today is part of the generated sample, because the desk did not run on those days. Today's log is the desk's own.
- The front page's optional "money waiting" block is off: it reads feeds from other systems, and the demo has none.
- Fuel figures (6.0 miles per gallon, $5.00 a gallon) are round placeholders.
