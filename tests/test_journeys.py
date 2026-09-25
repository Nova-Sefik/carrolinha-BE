"""Journey pipeline rules and /api/journey-traffic, /api/compare, /api/places.

Run: .venv/bin/python -m unittest tests.test_journeys
Everything runs on small synthetic files, so the expected numbers are exact.
"""
import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

TMP = tempfile.TemporaryDirectory()
WAREHOUSE = Path(TMP.name) / "warehouse.duckdb"
JOURNEYS = Path(TMP.name) / "journeys.duckdb"
os.environ["PULSO_DB"] = str(WAREHOUSE)
os.environ["CARROLINHA_JOURNEYS"] = str(JOURNEYS)

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from pipeline import build_journeys as P  # noqa: E402

STOPS = {  # hub -> (name, operator stop ids)
    "a": ("Alameda", {"metro": ["M_A"], "carris": ["1001"]}),
    "b": ("Baixa", {"metro": ["M_B"], "carris": ["1002"]}),
    "c": ("Cais do Sodré", {"metro": ["M_C"], "carris": ["1003"]}),
    "d": ("Damaia", {"carris": ["1004"]}),
}
WEEKDAYS = ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]


def build_warehouse():
    con = duckdb.connect(str(WAREHOUSE))
    con.execute((ROOT / "sql" / "schema.sql").read_text())
    for i, (sid, (name, ids)) in enumerate(STOPS.items()):
        con.execute(
            "INSERT INTO dim_stop VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
            [sid, name, 38.7 + i / 100, -9.1, "881f1d4b01fffff", list(ids), json.dumps(ids)],
        )
    # Stop 'a' at 08:00: Tue 100, other weekdays 80, 90, 70, 110 -> typical 85
    for date, v in zip(WEEKDAYS, [80, 100, 90, 70, 110]):
        con.execute("INSERT INTO fact_stop_hour VALUES (?, 8, 'a', 'metro', 'regular', ?, ?)", [date, v, v])
    con.execute("INSERT INTO fact_stop_hour VALUES ('2026-09-05', 8, 'a', 'metro', 'regular', 40, 40)")
    # Transfers on Tue: hub b has a large pair and a 2-person pair; hub d only 4 transfers in total
    con.executemany("INSERT INTO fact_transfer VALUES ('2026-09-01', ?, ?, ?, ?, ?, ?)", [
        ("b", "metro", "carris", 200, 5, 9), ("b", "ferry", "metro", 2, 30, 41), ("d", "carris", "cm", 4, 7, 9)])
    con.executemany("INSERT INTO fact_transfer_hour VALUES ('2026-09-01', ?, 'b', ?)", [(8, 150.0), (9, 3.0)])
    con.close()


def build_journey_fixture():
    """Hour 8 on every weekday: a>b>c (known), c>b>a (known), a>d (unknown), a>b (known, below privacy)."""
    con = duckdb.connect(str(JOURNEYS))
    con.execute(P.SCHEMA)
    abc = dict(zip(WEEKDAYS, [30, 40, 20, 25, 35]))  # Tue 40; others median(30, 20, 25, 35) = 27.5
    for date in WEEKDAYS:
        for path, evidence, n in [
            (["a", "b", "c"], "metro_exit", abc[date]),
            (["c", "b", "a"], "next_boarding", 15),
            (["a", "d"], "unknown", 12),
            (["a", "b"], "metro_exit", 3),
        ]:
            con.execute(
                "INSERT INTO fact_journey_path VALUES (?, 8, ?, ?, ?, ?, ?, ?)",
                [date, path, "|" + "|".join(path) + "|", ["metro"] * (len(path) - 1), len(path) - 1, evidence, n],
            )
    for date in WEEKDAYS + ["2026-09-05", "2026-09-06"]:
        for hour in range(5, 25):
            complete = hour != 9 and date != "2026-09-06"
            con.execute("INSERT INTO journey_coverage VALUES (?, ?, 60, ?)", [date, hour, complete])
    con.execute("INSERT INTO journey_meta VALUES ('taps', '1000'), ('journeys', '500')")
    con.close()


build_warehouse()
build_journey_fixture()
client = TestClient(main.app)


def traffic(query):
    response = client.get(f"/api/journey-traffic?day=2026-09-01&hour=8&{query}")
    assert response.status_code == 200, response.text
    return response.json()


class UsesJourneyFixture(unittest.TestCase):
    def setUp(self):
        os.environ["PULSO_DB"] = str(WAREHOUSE)
        os.environ["CARROLINHA_JOURNEYS"] = str(JOURNEYS)


class JourneyTrafficTests(UsesJourneyFixture):
    def test_direction_is_respected(self):
        self.assertEqual(traffic("origin=a&destination=c")["totals"]["matched_volume"], 40)
        self.assertEqual(traffic("origin=c&destination=a")["totals"]["matched_volume"], 15)

    def test_through_is_contiguous_and_privacy_paths_are_counted_not_listed(self):
        body = traffic("through=b")
        totals = body["totals"]
        self.assertEqual(totals["matched_volume"], 58)
        self.assertEqual(totals["shown_volume"], 55)
        self.assertEqual(totals["below_privacy_threshold"], 3)
        self.assertNotIn("a>b:known", [p["key"] for p in body["paths"]])
        self.assertTrue(all(p["journeys"] >= body["privacy_min"] for p in body["paths"]))

    def test_small_totals_are_hidden(self):
        body = traffic("origin=a&destination=b&match=exact")
        self.assertIsNone(body["totals"]["matched_volume"])
        self.assertEqual(body["paths"], [])
        self.assertEqual(body["comparison"]["status"], "below_privacy_threshold")
        self.assertIsNone(body["comparison"]["current"])

    def test_destination_excludes_unknown_destinations(self):
        self.assertEqual(traffic("destination=d")["totals"]["shown_volume"], 0)
        self.assertEqual(traffic("any=d")["totals"]["matched_volume"], 12)

    def test_min_volume_never_grows_the_result_and_totals_add_up(self):
        previous = None
        for min_volume in [0, 12, 13, 16, 41]:
            totals = traffic(f"min_volume={min_volume}")["totals"]
            if previous is not None:
                self.assertLessEqual(totals["shown_volume"], previous)
            previous = totals["shown_volume"]
            self.assertEqual(
                totals["shown_volume"] + totals["below_min_volume"] + totals["below_privacy_threshold"],
                totals["matched_volume"],
            )

    def test_sankey_and_table_match_totals(self):
        body = traffic("limit=500")
        origin_total = sum(n["value"] for n in body["sankey"]["nodes"] if n["layer"] == 0)
        self.assertEqual(origin_total, body["totals"]["shown_volume"])
        self.assertEqual(sum(p["journeys"] for p in body["paths"]), body["totals"]["shown_volume"])
        self.assertIn("unknown", [n["kind"] for n in body["sankey"]["nodes"]])

    def test_comparison_uses_other_weekdays_only(self):
        comparison = traffic("origin=a&destination=c")["comparison"]
        self.assertEqual(comparison["status"], "ok")
        self.assertEqual(comparison["current"], 40)
        self.assertEqual(comparison["typical"], 27.5)
        self.assertEqual(comparison["difference_pct"], 45.5)
        self.assertEqual([d["date"] for d in comparison["baseline_days"]], ["2026-08-31", "2026-09-02", "2026-09-03", "2026-09-04"])

    def test_per_path_typical(self):
        paths = {p["key"]: p for p in traffic("origin=a")["paths"]}
        self.assertEqual(paths["a>b>c:known"]["typical"], 27.5)
        self.assertEqual(paths["a>d:unknown"]["difference_pct"], 0.0)

    def test_incomplete_coverage_and_weekend_are_not_zero(self):
        partial = client.get("/api/journey-traffic?day=2026-09-01&hour=9").json()["comparison"]
        self.assertEqual(partial["status"], "incomplete_coverage")
        self.assertIsNone(partial["current"])
        weekend = client.get("/api/journey-traffic?day=2026-09-05&hour=8").json()["comparison"]
        self.assertEqual(weekend["status"], "insufficient_baseline")
        self.assertIsNone(weekend["typical"])

    def test_validation(self):
        self.assertEqual(client.get("/api/journey-traffic?origin=nope").status_code, 422)
        self.assertEqual(client.get("/api/journey-traffic?match=exact").status_code, 422)
        self.assertEqual(client.get("/api/journey-traffic?hour=24").status_code, 200)
        self.assertEqual(client.get("/api/journey-traffic?limit=10000").status_code, 200)
        self.assertEqual(client.get("/api/journey-traffic?limit=10001").status_code, 422)


class CompareAndPlacesTests(UsesJourneyFixture):
    def test_stop_boardings_vs_typical(self):
        body = client.get("/api/compare?measure=stop_boardings&subject=a&day=2026-09-01&hour=8").json()
        self.assertEqual(body["comparison"]["current"], 100)
        self.assertEqual(body["comparison"]["typical"], 85)
        self.assertEqual(body["comparison"]["sample_days"], 4)

    def test_weekend_baseline_is_insufficient(self):
        body = client.get("/api/compare?measure=stop_boardings&subject=a&day=2026-09-05&hour=8").json()
        self.assertEqual(body["comparison"]["status"], "insufficient_baseline")
        self.assertIsNone(body["comparison"]["typical"])

    def test_compare_validation(self):
        self.assertEqual(client.get("/api/compare?measure=stop_boardings").status_code, 422)
        self.assertEqual(client.get("/api/compare?measure=transfers&subject=nope").status_code, 404)

    def test_places_ignore_accents(self):
        places = client.get("/api/places?q=sodre").json()["places"]
        self.assertEqual([p["stop_id"] for p in places], ["c"])

    def test_ai_tools_expose_backend_comparison_charts(self):
        compare = client.post("/api/tools/query_live_compare", json={
            "args": {"measure": "network_boardings", "subject": None, "day": "2026-09-01", "hour": 8,
                     "ops": [], "segment": "all"},
            "live_filters": {},
        })
        self.assertEqual(compare.status_code, 200, compare.text)
        self.assertEqual(compare.json()["allowed_charts"], ["hour_vs_average"])
        self.assertEqual(compare.json()["comparison"]["status"], "ok")

        journey = client.post("/api/tools/query_live_journey_traffic", json={
            "args": {"day": "2026-09-01", "hour": 8, "whole_day": False, "origin": "a",
                     "through": [], "destination": "c", "any": [], "match": "contains",
                     "min_volume": 0, "limit": 20},
            "live_filters": {},
        })
        self.assertEqual(journey.status_code, 200, journey.text)
        self.assertIn("hour_vs_average", journey.json()["allowed_charts"])
        self.assertEqual(journey.json()["comparison"]["current"], 40)


class TransferPrivacyTests(UsesJourneyFixture):
    def setUp(self):
        self.hubs = {h["stop_id"]: h for h in client.get("/api/transfers?day=2026-09-01").json()["interchanges"]}

    def test_small_pairs_are_counted_but_not_listed(self):
        hub = self.hubs["b"]
        self.assertEqual(hub["transfers"], 202)
        self.assertEqual(hub["transfers_below_privacy"], 2)
        self.assertEqual([(p["from_operator"], p["to_operator"]) for p in hub["pairs"]], [("metro", "carris")])
        self.assertEqual(hub["worst_median_wait_min"], 5)  # the hidden pair's 30-minute wait never leaks

    def test_small_hubs_and_hours_are_hidden(self):
        self.assertNotIn("d", self.hubs)
        hourly = {h["hour"]: h["transfers"] for h in self.hubs["b"]["hourly"]}
        self.assertEqual(hourly[8], 150)
        self.assertIsNone(hourly[9])

    def test_stop_detail_hides_small_transfer_summary(self):
        body = client.get("/api/stops/d?day=2026-09-01&hour=8").json()
        self.assertIsNone(body["transfers_here"])


class PipelineRuleTests(unittest.TestCase):
    """Runs pipeline.build_journeys on a handmade validations CSV."""

    @classmethod
    def setUpClass(cls):
        rows = []
        tz = timezone(timedelta(hours=1))

        def tap(card, clock, event, agency, line, stop, date="20260901", day=1):
            hour, minute = map(int, clock.split(":"))
            local = datetime(2026, 9, day, hour, minute, tzinfo=tz)
            rows.append({"_id": len(rows), "created_at": int(local.timestamp() * 1000), "operational_date": date,
                         "agency_id": "x", "agency_code": agency, "card_serial_number_hash": card, "category": "",
                         "device_id": "", "event_type": event, "line_id": line, "pattern_id": "", "product_id": "",
                         "received_at": "", "stop_id": stop, "trip_id": "", "vehicle_id": ""})

        # 1: Metro a -> exit b, bus from b; next journey starts at c -> destination c (inferred)
        tap("card1", "08:00", "1", "2", "A", "M_A"); tap("card1", "08:20", "4", "2", "A", "M_B")
        tap("card1", "08:30", "1", "1", "700", "1002"); tap("card1", "18:00", "1", "1", "701", "1003")
        # 2: a 90-minute gap splits the chain
        tap("card2", "08:00", "1", "1", "700", "1001"); tap("card2", "09:30", "1", "1", "702", "1002")
        # 3: a duplicate tap is ignored; the Metro exit is the observed destination
        tap("card3", "08:00", "1", "2", "A", "M_A"); tap("card3", "08:01", "1", "2", "A", "M_A")
        tap("card3", "08:25", "4", "2", "A", "M_C")
        # 4: re-boarding the same line starts a new journey
        tap("card4", "08:00", "1", "1", "700", "1001"); tap("card4", "08:40", "1", "1", "700", "1002")
        # 5: after midnight belongs to the same operational day as hour 24
        tap("card5", "00:30", "1", "1", "700", "1004", day=2)

        csv_path = Path(TMP.name) / "validations_part_1.csv"
        with csv_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        cls.out = Path(TMP.name) / "journeys_built.duckdb"
        P.build(str(csv_path), str(WAREHOUSE), str(cls.out))
        con = duckdb.connect(str(cls.out), read_only=True)
        cls.rows = {
            (tuple(path), evidence, hour): n
            for path, evidence, hour, n in con.execute(
                "SELECT path, destination_evidence, hour, sum(journeys) FROM fact_journey_path GROUP BY ALL").fetchall()
        }
        con.close()

    def test_metro_exit_then_bus_then_next_boarding(self):
        self.assertEqual(self.rows[(("a", "b", "c"), "next_boarding", 8)], 1)
        self.assertEqual(self.rows[(("c",), "unknown", 18)], 1)

    def test_gap_split_and_same_line_split(self):
        self.assertEqual(self.rows[(("a", "b"), "next_boarding", 8)], 2)  # card2 and card4 first journeys
        self.assertEqual(self.rows[(("b",), "unknown", 9)], 1)            # card2 second journey
        self.assertEqual(self.rows[(("b",), "unknown", 8)], 1)            # card4 second journey

    def test_duplicate_tap_and_metro_exit_destination(self):
        self.assertEqual(self.rows[(("a", "c"), "metro_exit", 8)], 1)

    def test_hour_24(self):
        self.assertEqual(self.rows[(("d",), "unknown", 24)], 1)


if __name__ == "__main__":
    unittest.main()
