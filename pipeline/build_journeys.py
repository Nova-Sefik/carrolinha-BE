"""Build journeys.duckdb: complete journey paths from raw TML validations.

    python pipeline/build_journeys.py [validations_glob] [--warehouse warehouse.duckdb] [--out journeys.duckdb]

The output holds exact counts, including paths taken by a single card, so it is
git-ignored and must stay server-side. The API applies the privacy threshold
(app/journeys.py PRIVACY_MIN) to everything it returns.

Journey rule (the same one the golden-route step uses): a card's taps are
chained until a gap of more than 60 minutes. A chain is also split when the card
re-boards the same line, enters the Metro again after exiting it, or boards
again at the journey's own origin (a return trip, not a transfer).

Destination:
  metro_exit     the journey ends with a Metro exit tap (observed)
  next_boarding  where the same card starts its next journey that operational day (inferred)
  unknown        neither exists; the path ends at the last boarding, which is not a destination

Hours follow the API convention: service hour 5..24 of the operational date,
24 = 00:00-00:59 after midnight. Journeys starting 01:00-04:59 are counted in
journey_meta but not in fact_journey_path.
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import reference as R  # noqa: E402

DEFAULT_GLOB = os.path.expanduser("~/Downloads/_datasets/TML/validations/validations/validations_part_*.csv")
GAP_MS = 60 * 60 * 1000
DUPLICATE_MS = 5 * 60 * 1000
LISBON_OFFSET_H = 1  # WEST (UTC+1) for the whole dataset week
FULL_HOUR_MINUTES = 55  # an hour with taps in >= 55 distinct minutes is fully covered

ENTRY = {str(e) for e in R.ENTRY_EVENT_TYPES}
EXIT = {str(e) for e in R.EXIT_EVENT_TYPES}
OPERATOR_OF_AGENCY = {code: o["id"] for o in R.OPERATORS for code in o["agency_codes"]}

SCHEMA = """
CREATE TABLE fact_journey_path (
    date                 VARCHAR   NOT NULL,  -- operational date of the first boarding
    hour                 INTEGER   NOT NULL,  -- service hour 5..24 of the first boarding
    path                 VARCHAR[] NOT NULL,  -- hub stop_ids in order: boardings, Metro exits, then the destination
    chain                VARCHAR   NOT NULL,  -- '|a|b|c|' for directed substring matching
    operators            VARCHAR[] NOT NULL,  -- one per boarding (leg)
    legs                 INTEGER   NOT NULL,
    destination_evidence VARCHAR   NOT NULL,  -- metro_exit | next_boarding | unknown
    journeys             INTEGER   NOT NULL
);
CREATE TABLE journey_coverage (
    date     VARCHAR NOT NULL,
    hour     INTEGER NOT NULL,
    minutes  INTEGER NOT NULL,                -- distinct minutes with any tap in the source files
    complete BOOLEAN NOT NULL                 -- this hour and its neighbours are fully covered
);
CREATE TABLE journey_meta (k VARCHAR PRIMARY KEY, v VARCHAR NOT NULL);
"""


def service_hour(local_hour):
    """Local clock hour -> API service hour, or None for 01:00-04:59."""
    if local_hour == 0:
        return 24
    return local_hour if local_hour >= 5 else None


def iso(op_date):
    return f"{op_date[:4]}-{op_date[4:6]}-{op_date[6:]}"


def hub_lookup(warehouse):
    con = duckdb.connect(warehouse, read_only=True)
    lookup = {}
    for stop_id, raw in con.execute("SELECT stop_id, operator_stop_ids FROM dim_stop").fetchall():
        for operator, ids in json.loads(raw).items():
            for raw_id in ids:
                lookup[(operator, raw_id)] = stop_id
    con.close()
    return lookup


class Journey:
    __slots__ = ("date", "hour", "legs", "unmapped")

    def __init__(self, op_date, local_hour):
        self.date = iso(op_date)
        self.hour = service_hour(local_hour)
        self.legs = []  # [operator, line, board_hub, board_ts, exit_hub]
        self.unmapped = False

    @property
    def origin(self):
        return self.legs[0][2]

    @property
    def ends_with_exit(self):
        return self.legs[-1][4] is not None

    def observed_chain(self):
        """Boarding hubs in order, with each Metro exit after its leg."""
        chain = []
        for _, _, board_hub, _, exit_hub in self.legs:
            chain.append(board_hub)
            if exit_hub is not None:
                chain.append(exit_hub)
        return chain


def build(glob, warehouse, out):
    started = time.time()
    hubs = hub_lookup(warehouse)
    con = duckdb.connect()
    src = f"read_csv('{glob}', all_varchar=true, header=true)"
    stats = Counter()

    # Coverage per local clock hour of each operational date
    minutes = {}
    for op_date, local_hour, mins in con.execute(f"""
            SELECT operational_date, hour(ts), count(DISTINCT minute(ts)) FROM (
              SELECT operational_date, make_timestamp(created_at::BIGINT * 1000) + INTERVAL {LISBON_OFFSET_H} HOUR ts
              FROM {src}) GROUP BY ALL""").fetchall():
        minutes[(iso(op_date), local_hour)] = mins

    cursor = con.execute(f"""
        SELECT card_serial_number_hash, created_at::BIGINT ts, operational_date, agency_code,
               event_type, line_id, stop_id,
               hour(make_timestamp(created_at::BIGINT * 1000) + INTERVAL {LISBON_OFFSET_H} HOUR) local_hour
        FROM {src}
        WHERE event_type IN ({",".join(f"'{e}'" for e in ENTRY | EXIT)})
        ORDER BY card_serial_number_hash, ts""")

    paths = Counter()

    def emit(journey, next_journey):
        if journey is None or not journey.legs:
            return
        stats["journeys"] += 1
        if journey.unmapped:
            stats["journeys_unmapped_stop"] += 1
            return
        if journey.hour is None:
            stats["journeys_outside_service_hours"] += 1
            return
        chain = journey.observed_chain()
        if journey.ends_with_exit:
            evidence = "metro_exit"
        elif next_journey is not None and next_journey.date == journey.date and next_journey.origin is not None:
            evidence = "next_boarding"
            chain.append(next_journey.origin)
        else:
            evidence = "unknown"
        path = [hub for index, hub in enumerate(chain) if index == 0 or hub != chain[index - 1]]
        operators = tuple(leg[0] for leg in journey.legs)
        paths[(journey.date, journey.hour, tuple(path), operators, evidence)] += 1
        stats[f"destination_{evidence}"] += 1

    card = None
    current = pending = None
    last_ts = 0
    while True:
        batch = cursor.fetchmany(200_000)
        if not batch:
            break
        for card_hash, ts, op_date, agency, event, line, raw_stop, local_hour in batch:
            stats["taps"] += 1
            if card_hash != card:
                emit(pending, current)
                emit(current, None)
                card, current, pending, last_ts = card_hash, None, None, 0
            operator = OPERATOR_OF_AGENCY.get(agency)
            hub = hubs.get((operator, raw_stop)) if operator else None
            if hub is None:
                stats["taps_unmapped"] += 1

            if current is not None and ts - last_ts > GAP_MS:
                emit(pending, current)
                pending, current = current, None

            if event in EXIT:
                # Only an open Metro leg can be closed by a (Metro) exit tap
                if current is not None and operator == "metro" and current.legs[-1][0] == "metro" and not current.ends_with_exit:
                    current.legs[-1][4] = hub
                    current.unmapped |= hub is None
                    last_ts = ts
                else:
                    stats["exits_without_entry"] += 1
                continue

            if current is not None:
                last = current.legs[-1]
                if last[1] == line and last[2] == hub and ts - last[3] < DUPLICATE_MS and last[4] is None:
                    stats["duplicate_taps"] += 1
                    last_ts = ts
                    continue
                same_line = last[0] == operator and last[1] == line
                metro_reentry = operator == "metro" and last[0] == "metro" and last[4] is not None
                return_trip = hub is not None and hub == current.origin
                if same_line or metro_reentry or return_trip:
                    emit(pending, current)
                    pending, current = current, None

            if current is None:
                current = Journey(op_date, local_hour)
            current.legs.append([operator or "unknown", line, hub, ts, None])
            current.unmapped |= hub is None
            last_ts = ts
    emit(pending, current)
    emit(current, None)
    con.close()

    rows = [
        (date, hour, list(path), "|" + "|".join(path) + "|", list(operators), len(operators), evidence, n)
        for (date, hour, path, operators, evidence), n in paths.items()
    ]

    if os.path.exists(out):
        os.remove(out)
    db = duckdb.connect(out)
    db.execute(SCHEMA)
    db.executemany("INSERT INTO fact_journey_path VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

    full = {key for key, mins in minutes.items() if mins >= FULL_HOUR_MINUTES}
    coverage = []
    for day in R.DATES:
        for hour in R.HOURS:
            local = 0 if hour == 24 else hour
            before, after = (23 if hour == 24 else hour - 1), (1 if hour == 24 else (0 if hour == 23 else hour + 1))
            complete = all((day, h) in full for h in (before, local, after))
            coverage.append((day, hour, minutes.get((day, local), 0), complete))
    db.executemany("INSERT INTO journey_coverage VALUES (?, ?, ?, ?)", coverage)

    meta = {
        **{k: str(v) for k, v in stats.items()},
        "source_glob": glob,
        "path_rows": str(len(rows)),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gap_minutes": str(GAP_MS // 60000),
    }
    db.executemany("INSERT INTO journey_meta VALUES (?, ?)", list(meta.items()))
    db.close()
    print(json.dumps(meta, indent=2))
    print(f"wrote {out} in {time.time() - started:.0f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("glob", nargs="?", default=DEFAULT_GLOB)
    parser.add_argument("--warehouse", default=os.path.join(ROOT, "warehouse.duckdb"))
    parser.add_argument("--out", default=os.path.join(ROOT, "journeys.duckdb"))
    args = parser.parse_args()
    build(args.glob, args.warehouse, args.out)
