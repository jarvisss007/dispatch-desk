"""Dispatch Desk — the rules that have already been wrong once, pinned.

Offline: no board, no GPS, no Gmail. Each test names the mistake it guards.
"""
from datetime import date, datetime, timedelta

import board
from dispatch_desk import assign, extras, live, packets, plan, pools, tomorrow

C = board.COL
S = board.Stage


def row(ref, pickup="", delivery="", pre="", drv="", trk="", status="", note="", section="X", cont=None):
    cells = [""] * board.NUM_COLS
    cells[C["load"]], cells[C["pickup"]], cells[C["delivery"]] = ref, pickup, delivery
    cells[C["preloader"]], cells[C["driver"]], cells[C["trk"]] = pre, drv, trk
    cells[C["status"]], cells[C["note"]] = status, note
    return {"section": section, "cells": cells, "cont": cont or [], "ref": ref,
            "stage": board.classify_status(status)}


def classified(rows, day=date(2026, 11, 10)):
    return plan.classify(rows, {}, day)


# ---- regions -------------------------------------------------------------
def test_moreno_valley_is_not_reno():
    # 11/10: "MORENO VALLEY, CA" matched RENO and put a local driver in Nevada
    assert plan.region_of("PARCEL HUB DOCK 3, MORENO VALLEY, CA") == "LOCAL"
    assert plan.region_of("SPARKS / RENO, NV") == "NV"


def test_regions_basic():
    assert plan.region_of("REDWOOD TOLLESON - TOLLESON, AZ") == "AZ"
    assert plan.region_of("DUNE CORRUGATED - N. LAS VEGAS, NV") == "NV"
    assert plan.region_of("REDWOOD TRACY - TRACY, CA") == "CV"
    assert plan.region_of("HARBOR PAPER ONTARIO") == "LOCAL"


# ---- who is a truck going out of town ------------------------------------
def test_preloader_on_outbound_load_is_not_a_truck_going_there():
    # 11/10: Nolan (LOCAL ONLY) was the PRELOADER on an Arizona load and got "sent" to Tolleson
    rows = classified([
        row("70001001", "LOCAL SHIPPER, ONTARIO, CA", "RECEIVER DOCK, PHOENIX, AZ", pre="NOLAN", status="AT YARD"),
        row("5100002", "FRESHWAY TOLLESON, AZ", "HARBOR PAPER ONTARIO", section="FRESHWAY TOLLESON",
            cont=[["", "READY"] + [""] * 8]),
    ])
    reg = plan.plan(rows, {"NOLAN": "LOCAL ONLY"})["AZ"]
    assert reg["trucks"] == []
    assert [r["ref"] for r in reg["unstaffed_out"]] == ["70001001"]
    assert reg["matches"] == []


def test_local_only_driver_is_never_suggested_out_of_town():
    rows = classified([
        row("111", "ONTARIO, CA", "REDWOOD TOLLESON - TOLLESON, AZ", drv="DELROY", status="EN ROUTE TO CONSIGNEE"),
        row("222", "FRESHWAY TOLLESON, AZ", "HARBOR PAPER ONTARIO", cont=[["", "READY"] + [""] * 8]),
    ])
    reg = plan.plan(rows, {"DELROY": "LOCAL ONLY"})["AZ"]
    assert not [m for m in reg["matches"] if m["how"] == "suggested"]
    assert [t["who"] for t in reg["not_allowed"]] == ["DELROY"]


def test_authority_lookup():
    t = {"MARCO": "OTR - AZ, N, NV, LOCAL", "PETER JAMES HOLLIS": "LOCAL ONLY",
         "CONRAD BRANDT": "OTR - NV ONLY", "PETER JAMES QUINN": "OTR - N, NV, LOCAL"}
    assert plan.authority("MARCO", "AZ", t)[0] == "ok"
    assert plan.authority("HOLLIS", "AZ", t)[0] == "no"          # matched as a word of the full name
    assert plan.authority("BRANDT", "NV", t)[0] == "ok"
    assert plan.authority("BRANDT", "AZ", t)[0] == "no"
    assert plan.authority("QUINN", "CV", t)[0] == "ok"          # N = Central Valley
    assert plan.authority("NEWGUY", "AZ", t)[0] == "unknown"


# ---- nearest backhaul ----------------------------------------------------
def test_nearest_pairs_minimises_the_total_not_each_truck():
    def t(lat):
        return {"row": {"dpos": (lat, -112.0, "x")}}
    def l(lat, ready=True):
        return {"ppos": (lat, -112.0, "x"), "ready": ready, "days": 0}
    trucks, loads = [t(33.0), t(33.1)], [l(33.1), l(33.6)]
    # greedy would give truck 0 the load at 33.1 and send truck 1 far; optimum crosses them over
    pairs = dict(plan.nearest_pairs(trucks, loads))
    total = sum(plan.deadhead(trucks[i]["row"], loads[j]) for i, j in pairs.items())
    other = plan.deadhead(trucks[0]["row"], loads[0]) + plan.deadhead(trucks[1]["row"], loads[1])
    assert total <= other + 1e-6


def test_ready_trailer_beats_a_nearer_one_that_is_not_ready():
    truck = [{"row": {"dpos": (33.0, -112.0, "x")}}]
    loads = [{"ppos": (33.0, -112.0, "x"), "ready": False, "days": 0},
             {"ppos": (33.3, -112.0, "x"), "ready": True, "days": 0}]
    assert plan.nearest_pairs(truck, loads) == [(0, 1)]


# ---- names ---------------------------------------------------------------
def test_split_names():
    assert packets.split_names("MARCO/ DELROY", set()) == ["MARCO", "DELROY"]
    assert packets.split_names("?", set()) == []
    assert packets.split_names("REDWOOD TOLLESON", {"REDWOOD TOLLESON"}) == []


def test_lookalikes_are_listed_not_merged():
    pairs = packets.lookalikes(["KOWALSKI", "KOWLASKI", "DARRELL", "DARNELL", "MARCO"])
    assert ("KOWALSKI", "KOWLASKI") in pairs
    assert ("DARRELL", "DARNELL") in pairs      # two different drivers: shown, never merged


# ---- carry-over ----------------------------------------------------------
def test_window_reaches_back_to_the_last_weekday():
    mk = lambda d: (f"{d.month}.{d.day}", d, [])                                # noqa: E731
    fri, sat, sun = date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8)
    assert [x[1] for x in tomorrow.window_for([mk(fri), mk(sat), mk(sun)])] == [fri, sat, sun]
    mon, tue = date(2026, 11, 9), date(2026, 11, 10)
    assert [x[1] for x in tomorrow.window_for([mk(fri), mk(mon), mk(tue)])] == [tue]


def test_delivered_and_canceled_are_not_carried():
    d = date(2026, 11, 10)
    tab = [("11.10", d, [row("1", status="DELIVERED"), row("2", status="AT YARD"), row("3", status="CANCELED")])]
    assert [l["ref"] for l in tomorrow.carry_set(tab)] == ["2"]


# ---- live detention ------------------------------------------------------
def test_detention_rules():
    now = datetime(2026, 11, 10, 14, 0)
    appt = datetime(2026, 11, 10, 9, 0)
    # early arrival: clock starts at the appointment, 2h free -> running since 11:00
    j = live.judge(datetime(2026, 11, 10, 8, 0), appt, 120, now)
    assert j["state"] == "running" and j["over_min"] == 180
    # late for the appointment: no detention at all (house rule)
    assert live.judge(datetime(2026, 11, 10, 9, 40), appt, 120, now)["state"] == "late"
    # inside the grace: on time, clock from arrival
    assert live.judge(appt + timedelta(minutes=10), appt, 120, now)["state"] == "running"
    # about to start
    assert live.judge(datetime(2026, 11, 10, 11, 30), datetime(2026, 11, 10, 11, 30), 120,
                      datetime(2026, 11, 10, 13, 0))["state"] == "soon"
    assert live.judge(datetime(2026, 11, 10, 8, 0), None, 120, now)["state"] == "unknown"


def test_stopped_duration_parse():
    assert live._dur_min("3h 36m") == 216
    assert live._dur_min("2d 20h 35m") == 2 * 1440 + 20 * 60 + 35
    assert live._dur_min("8s") is None and live._dur_min("") is None


# ---- pools / fences ------------------------------------------------------
def test_fence_matching():
    fences = [{"name": "SUNPEAK DC GOODYEAR AZ"}, {"name": "PALMCO VEGAS"}, {"name": "PALMCO RIVERSIDE"},
              {"name": "REDWOOD TOLLESON"}, {"name": "REDWOOD BELL"}]
    assert pools.fence_for("SUNPEAK DC", fences)["name"] == "SUNPEAK DC GOODYEAR AZ"
    assert pools.fence_for("PALMCO VEGAS", fences)["name"] == "PALMCO VEGAS"
    assert pools.fence_for("REDWOOD BELL", fences)["name"] == "REDWOOD BELL"
    assert pools.fence_for("MERIDIAN TULARE", fences) is None


# ---- swap clock ----------------------------------------------------------
def test_swap_clock_flags_only_loads_ready_since_an_earlier_day():
    ready = [["", "READY"] + [""] * 8]
    d1, d2 = date(2026, 11, 9), date(2026, 11, 10)
    old = classified([row("A", "FRESHWAY TOLLESON, AZ", "HARBOR PAPER ONTARIO", cont=ready)], d1)
    today = classified([row("A", "FRESHWAY TOLLESON, AZ", "HARBOR PAPER ONTARIO", cont=ready),
                        row("B", "FRESHWAY TOLLESON, AZ", "HARBOR PAPER ONTARIO", cont=ready)], d2)
    out = extras.swap_clock([(d1, old)], today, d2, plan.NOT_PULLED)
    assert [(x["row"]["ref"], x["days"]) for x in out] == [("A", 1)]


# ---- local suggestions ---------------------------------------------------
def test_local_suggestion_prefers_who_knows_the_customer_and_spreads_work():
    past = [classified([row(str(i), "PALMCO RIVERSIDE", "HARBOR PAPER ONTARIO", pre="TOBIAS", section="PALMCO RIVERSIDE")
                        for i in range(3)]
                       + [row("9", "PALMCO RIVERSIDE", "HARBOR PAPER ONTARIO", pre="IVAN", section="PALMCO RIVERSIDE"),
                          row("10", "PALMCO RIVERSIDE", "HARBOR PAPER ONTARIO", pre="IVAN", section="PALMCO RIVERSIDE")])]
    today = classified([row("T1", "A", "B", pre="TOBIAS"), row("T2", "A", "B", pre="IVAN"),
                        row("N1", "PALMCO RIVERSIDE", "HARBOR PAPER ONTARIO", section="PALMCO RIVERSIDE"),
                        row("N2", "PALMCO RIVERSIDE", "HARBOR PAPER ONTARIO", section="PALMCO RIVERSIDE")])
    loads = [r for r in today if r["ref"].startswith("N")]
    sug = assign.suggest_local(loads, today, assign.section_history(past), set())
    assert sug[0]["who"] == "TOBIAS"                 # most history with the customer
    assert {s["who"] for s in sug} == {"TOBIAS", "IVAN"}   # second load goes to the other man


# ---- roster (the board's DRIVERS tab) ------------------------------------
def _people():
    def p(first, last, work):
        return {"name": f"{first.split()[0]} {last}".title(), "first": first.split()[0], "words": first.split() + last.split(),
                "workload": work, "comp": "SF", "cdl": None, "medical": None}
    return [p("LUCAS MATEO", "REYES", "OTR"), p("THEODORE", "BANKS", "LOCAL"), p("NATHANEL", "OKORO", "OTR"),
            p("DARNELL", "HUGHES", "OTR"), p("DARRELL JAMES", "BOONE", "OTR"), p("OWEN", "PRICE", "SHUTTLE"),
            p("OWEN LEE", "CARTER", "OTR"), p("FELIX", "MORENO", "OTR"), p("HUGO FELIX", "MEDINA", "BALE"),
            p("NOLAN", "FRY", "LOCAL"), p("GRANT", "ELLIS", "OTR"), p("DIEGO", "GRANTHAM", "OTR")]


def test_board_names_find_the_right_roster_row():
    from dispatch_desk import roster
    P = _people()
    one = lambda w: [p["name"] for p in roster.match(w, P)]                     # noqa: E731
    assert one("MATEO") == ["Lucas Reyes"]                  # a middle name
    assert one("THEO") == ["Theodore Banks"]                # a short form
    assert one("NATHANIEL") == ["Nathanel Okoro"]           # the board's spelling differs from the tab's
    assert one("DARNELL") == ["Darnell Hughes"]             # one letter from Darrell: two different men
    assert one("DARRELL") == ["Darrell Boone"]
    assert one("FELIX NEW") == ["Felix Moreno"]             # first name beats Medina's middle name
    assert one("GRANT") == ["Grant Ellis"]                  # not Grantham
    assert one("OWEN C") == ["Owen Carter"]
    assert roster.workload("OWEN", P)[0] is None            # Price (shuttle) or Carter (OTR): never a guess
    assert one("ZEKE") == []


def test_drivers_tab_outranks_the_older_roster_and_needs_proof_of_a_region():
    t = plan.Roster({"NOLAN": "OTR - AZ, NV"})               # stale row in the older roster
    t.people, t.proven = _people(), {"MATEO": {"AZ": 28}, "THEO": {"AZ": 9}, "ZEKE": {"NV": 7}}
    assert plan.authority("NOLAN", "AZ", t)[0] == "no"       # LOCAL on the tab wins
    assert plan.authority("THEO", "AZ", t)[0] == "no"       # local on the tab, whatever the board shows
    assert plan.authority("MATEO", "AZ", t)[0] == "ok"     # OTR + trips on the board
    assert plan.authority("MATEO", "NV", t)[0] == "unknown"    # OTR, never been there
    assert plan.authority("ZEKE", "NV", t)[0] == "ok"       # not on the tab, proven on the board
    assert plan.authority("ZEKE", "AZ", t)[0] == "unknown"


def test_one_man_written_two_ways_is_offered_one_load():
    # 11/10: "OWEN C" and "OWEN CARTER" were each given an Arizona load
    today = classified([row("L1", "A", "B", pre="OWEN C"), row("L2", "A", "B", pre="OWEN CARTER"),
                        row("L3", "A", "B", pre="MARCO")])
    hist = assign.lane_history([], C)
    same = lambda w: "Owen Carter" if w.startswith("OWEN") else w            # noqa: E731
    ok = lambda w, c: ("ok", "roster")                                           # noqa: E731
    got = assign.candidates("AZ", today, [], hist, ok, set(), same)
    assert sorted(got) in (["MARCO", "OWEN C"], ["MARCO", "OWEN CARTER"])
    assert assign.candidates("AZ", today, [], hist, ok, {"OWEN C"}, same).keys() == {"MARCO"}


# ---- the score -----------------------------------------------------------
def test_score_bins():
    from dispatch_desk import score
    log = {"drivers": {"A": {"who": "MARCO", "alts": ["MATEO"]}, "B": {"who": "MARCO", "alts": ["MATEO"]},
                       "C": {"who": "MARCO", "alts": []}, "D": {"who": "MARCO", "alts": []},
                       "E": {"who": "OWEN C", "alts": []}},
           "backhauls": {}, "local": {}}
    az = ("ONTARIO, CA", "REDWOOD TOLLESON - TOLLESON, AZ")
    rows = classified([row("A", *az, drv="MARCO"), row("B", *az, drv="MATEO"), row("C", *az, drv="PABLO"),
                       row("D", *az), row("E", *az, drv="OWEN CARTER")])
    same = lambda w: "Owen Carter" if w.startswith("OWEN") else w            # noqa: E731
    got = {i["load"]: i["result"] for i in score.judge(log, rows, same)["items"]}
    assert got == {"A": "taken", "B": "near", "C": "other", "D": "open", "E": "taken"}
    assert score.judge({"drivers": {"Z": {"who": "MARCO", "alts": []}}}, rows)["count"]["gone"] == 1


def test_week_ahead_flags_a_day_with_more_loads_than_drivers_who_can_leave():
    d0 = date(2026, 11, 10)                                  # a Tuesday
    def out(n, tag):
        return classified([row(f"{tag}{i}", "ONTARIO, CA", "REDWOOD TOLLESON - TOLLESON, AZ") for i in range(n)])
    past = [(d0 - timedelta(days=7 * k), out(6, f"p{k}")) for k in range(1, 4)]
    wk = extras.week_ahead(past, [], [], {"AZ": 8}, d0 - timedelta(days=1), 1, ["AZ"])
    tue = next(a for a in wk if a["day"] == d0)["regions"]["AZ"]
    assert tue == {"booked": 0, "normal": 6, "can_leave": 4, "gap": 2}   # 8 drivers, 2-day turn -> 4 leave


# ---- tracking ------------------------------------------------------------
def test_appointment_windows():
    from dispatch_desk import track
    now = datetime(2026, 11, 11, 9, 0)
    assert track.window("11/11 - 9AM", now) == (datetime(2026, 11, 11, 9), datetime(2026, 11, 11, 9, 15))
    assert track.window("11/11 - 8AM - 4PM", now)[1] == datetime(2026, 11, 11, 16)
    assert track.window("11/11 - 9PM - 11/12 - 3AM", now)[1] == datetime(2026, 11, 12, 3)
    assert track.window("11/11 - 3:30 PM", now)[0] == datetime(2026, 11, 11, 15, 30)
    assert track.window("FCFS", now) is None


def test_tracking_verdicts():
    from dispatch_desk import track
    now, close = datetime(2026, 11, 11, 9, 0), datetime(2026, 11, 11, 12, 0)
    assert track.verdict(1, False, close, now)["state"] == "there"
    assert track.verdict(100, True, close, now)["state"] == "ontime"       # 2h drive, 3h left
    assert track.verdict(130, True, close, now)["state"] == "tight"        # 2h36 drive, 24 min spare
    assert track.verdict(200, True, close, now)["state"] == "late"         # 4h drive
    assert track.verdict(100, False, close, now)["state"] == "waiting"     # parked but still in time
    assert track.verdict(None, True, close, now)["state"] == "unplaced"


def test_a_truck_that_already_stopped_there_is_not_late():
    # 11/11: early-morning deliveries showed LATE at 9 AM because the board status had not been updated
    from dispatch_desk import track
    now = datetime(2026, 11, 11, 9, 0)
    item = {"state": "late", "opens": "2026-11-11T03:45", "_check": (7, (34.05, -117.60))}
    hist = lambda dev, day: {"stops": [{"latitude": "34.05", "longitude": "-117.60",        # noqa: E731
                                        "stop_start": "2026-11-11 03:30:00", "stop_end": "2026-11-11 04:40:00"}]
                             if day == "2026-11-11" else []}
    track._already_there([item], now, hist)
    assert item["state"] == "been" and "_check" not in item


def test_an_old_gps_position_is_shown_but_never_judged():
    # 11/13: positions 80 min old were being judged as if the truck were still there
    from dispatch_desk import track
    assert track.freshness(0) == "live" and track.freshness(track.LIVE_MIN) == "live"
    assert track.freshness(track.LIVE_MIN + 1) == "old" and track.freshness(track.STALE_MIN) == "old"
    assert track.freshness(track.STALE_MIN + 1) == "silent"
    say = track._last_seen(79, "stopped", 240)
    assert "240 mi from this stop" in say and "79 min ago" in say and "too old" in say
    assert "at this stop" in track._last_seen(30, "stopped", 1)


def test_one_truck_several_loads_are_taken_in_order():
    # 11/12: Pablo sat in Phoenix 9 min from his 10:30 stop and was also shown "15 h LATE" for a 3:15 AM
    # Ontario delivery, measured from Phoenix. He is not driving to Ontario: that load needs checking, not an ETA.
    from dispatch_desk import track
    now = datetime(2026, 11, 12, 10, 30)
    ont, phx, tus = (34.05, -117.60), (33.45, -112.07), (32.22, -110.97)
    mk = lambda ref, state, opens, closes, stop, mi, eta="": {                  # noqa: E731
        "ref": ref, "unit": "SF02", "state": state, "opens": opens, "closes": closes, "eta": eta,
        "say": "", "_stop": stop, "_mi": mi, "_moving": True}
    old = mk("A", "late", "2026-11-12T03:15", "2026-11-12T03:30", ont, 350, "2026-11-12T17:30")
    cur = mk("B", "there", "2026-11-12T10:30", "2026-11-12T10:45", phx, 1, "2026-11-12T10:30")
    nxt = mk("C", "ontime", "2026-11-12T11:00", "2026-11-12T11:15", tus, 135, "2026-11-12T13:12")
    track._in_order([old, cur, nxt], now)
    assert old["state"] == "passed" and "load B" in old["say"]
    assert cur["state"] == "there"
    # Tucson is ~2h40 from Phoenix: an 11 AM appointment cannot be made, and it says which stop comes first
    assert nxt["state"] == "late" and nxt["say"].endswith("after load B first")
    # a single late load on a truck stays late: nothing else explains it
    solo = mk("D", "late", "2026-11-12T09:00", "2026-11-12T09:15", ont, 60, "2026-11-12T11:42")
    track._in_order([solo], now)
    assert solo["state"] == "late"


def test_a_note_in_the_driver_cell_is_not_a_driver():
    from dispatch_desk import track
    assert track._namelike("FELIX NEW") and track._namelike("OWEN C")
    assert not track._namelike("2ND LOAD") and not track._namelike("TRAILER BLOCKED")     # 11/15: 5 of 9 "no truck"
    assert not track._namelike("QUINN CALLED OFF") and not track._namelike("PRIORITY TO PICKUP")
    assert not track._namelike("VERY STRICT AT SHIPPER. NEED LOW LOCKS AND STRAPS AND CLEAN TRAILER")
    assert "never reported" in track._silent_for(9e9) and "days" in track._silent_for(5000 * 60)


def test_who_was_closer_to_the_backhaul():
    from dispatch_desk import score
    it = lambda res, d, b: {"kind": "Backhaul", "result": res, "desk_mi": d, "board_mi": b}   # noqa: E731
    c = score.closer([it("other", 149, 3), it("other", 5, 60), it("other", 7, 12), it("other", 7, None),
                      it("taken", 2, 2), it("open", 1, None),
                      {"kind": "Local load", "result": "other", "desk_mi": None, "board_mi": None}])
    assert (c["office"], c["desk"], c["even"], c["unmeasured"]) == (1, 1, 1, 1)
    assert c["office_mi"] == 75 and c["desk_mi"] == 161


def test_the_boards_puller_is_measured_from_where_he_unloads():
    from dispatch_desk import plan as P
    out = {"dr": "AZ", "pr": "LOCAL", "drv": ["MARCO"], "pre": [], "dpos": (33.45, -112.07)}
    bh = {"dr": "LOCAL", "pr": "AZ", "drv": [], "pre": ["MARCO"], "ppos": (33.45, -112.07)}
    assert P.puller_miles(bh, [out, bh]) == 0
    assert P.puller_miles({**bh, "pre": ["IVAN"]}, [out, bh]) is None      # no delivery there: nothing to measure


def _trip_row(n, ref, pr, dr, drv=(), pre=(), ready=False, stage=None, pos=(33.45, -112.07)):
    cells = [""] * 40
    return {"n": n, "ref": ref, "pr": pr, "dr": dr, "drv": list(drv), "pre": list(pre), "ready": ready,
            "stage": stage, "section": "SHIPPER " + ref, "cells": cells,
            "dpos": pos if dr != "LOCAL" else None, "ppos": pos if pr != "LOCAL" else None}


def test_empty_trips_home_are_counted_once_and_matched_to_a_pull():
    from datetime import date
    from dispatch_desk import empties as E
    d1, d2, d3, d4 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)
    days = [
        (d1, [_trip_row(0, "OUT1", "LOCAL", "AZ"),                               # on the board, no driver yet
              _trip_row(1, "BH9", "AZ", "LOCAL", ready=True)]),                  # READY, no puller
        (d2, [_trip_row(0, "OUT1", "LOCAL", "AZ", drv=["MARCO"]),                 # same load, rolled over: one trip
              _trip_row(1, "OUT2", "LOCAL", "AZ", drv=["IVAN"]),
              _trip_row(2, "BH9", "AZ", "LOCAL", ready=True),
              _trip_row(3, "BH8", "AZ", "LOCAL", ready=True)]),
        (d3, [_trip_row(0, "BH1", "AZ", "LOCAL", pre=["MARCO"]),                  # typed the tab after: still his
              _trip_row(1, "OUT3", "LOCAL", "AZ", drv=["IVAN"]),
              _trip_row(2, "BH9", "AZ", "LOCAL", ready=True)]),
        (d4, [_trip_row(0, "OUT4", "LOCAL", "AZ", drv=["MARCO"])]),               # today: not judged
    ]
    a = E.analyze(days, ["AZ", "NV", "CV"], 5, today=d4)
    got = {(t["who"], t["day"]): t for t in a["trips"]}
    assert set(got) == {("MARCO", d2), ("IVAN", d2), ("IVAN", d3)}
    assert got[("MARCO", d2)]["loaded"] and got[("MARCO", d2)]["bh"] == "BH1"
    assert not got[("IVAN", d2)]["loaded"] and not got[("IVAN", d3)]["loaded"]
    # two READY trailers sat there on d2; BH9 is still there on d3 but may cover one truck only, ever
    assert {got[("IVAN", d2)]["could"], got[("IVAN", d3)]["could"]} <= {"BH9 SHIPPER BH9", "BH8 SHIPPER BH8", None}
    assert got[("IVAN", d2)]["could"] and got[("IVAN", d2)]["could"] != got[("IVAN", d3)]["could"]
    t = E.total(a["trips"])
    assert (t["trips"], t["loaded"], t["empty"]) == (3, 1, 2)
    assert (a["from"], a["to"], a["tabs"]) == (d1, d3, 3)


def test_the_run_home_uses_the_regions_road_factor():
    from dispatch_desk import empties as E, geo
    pos = (33.45, -112.26)
    assert abs(E.home_miles(pos, "AZ") - geo.miles(pos, geo.YARD) / 1.25 * 1.05) < 0.01
    assert 290 < E.home_miles(pos, "AZ") < 340           # Tolleson to the yard is about 315 road miles
    assert E.home_miles(None, "AZ") is None
    assert E._place("REDWOOD TRACY - TRACY,CA") == E._place("REDWOOD TRACY  -  TRACY , CA")


def test_fuel_figures_come_from_the_dashboard_file_or_not_at_all(tmp_path):
    import json
    from dispatch_desk import empties as E
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"driverScore": {"fleetMpg": 5.5, "generated": "x"},
                             "fuelDaily": {"netPpg": 6.0, "asOf": "2026-11-12", "windowDays": 14}}), encoding="utf-8")
    fuel = E.fuel_rates(f)
    assert (fuel["mpg"], fuel["ppg"]) == (5.5, 6.0)
    assert E.dollars(550, fuel) == 600
    assert E.dollars(550, None) is None
    assert E.fuel_rates(tmp_path / "missing.json") is None
    f.write_text(json.dumps({"driverScore": {"fleetMpg": 0}, "fuelDaily": {"netPpg": 6.0}}), encoding="utf-8")
    assert E.fuel_rates(f) is None                       # an unbelievable figure shows no dollars
