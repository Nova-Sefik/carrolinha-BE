"""Run: python -m unittest tests.test_api (or: python tests/test_api.py).

The tests build a small fixture warehouse from sql/schema.sql. The fixture is
owned by this test so production reference data can change independently.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import h3  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app import reference as R  # noqa: E402

SEGMENT_SHARE = {"regular": 0.7, "sub23": 0.2, "senior": 0.1}
STATIONS = [
    ("oriente", "Oriente", 38.7678, -9.0998, 18_000, ["metro", "cm"], (True, True, True, True)),
    ("cais-do-sodre", "Cais do Sodré", 38.7062, -9.1450, 15_000, ["metro", "carris"], (True, True, True, True)),
    ("campo-grande", "Campo Grande", 38.7599, -9.1579, 12_000, ["metro", "carris"], (True, True, False, True)),
]
LINES = [
    {"line_id": "1709", "label": "1709", "name": "Oriente – Campo Grande", "mode": "bus", "operator": "cm",
     "seats": 45, "standing": 35, "capacity_source": "test fixture", "shape": [[-9.0998, 38.7678], [-9.1579, 38.7599]],
     "daily": 4_000, "est_share": 0.35, "trips": [4] * len(R.HOURS)},
    {"line_id": "CA-CS", "label": "CA-CS", "name": "Cais circular", "mode": "bus", "operator": "carris",
     "seats": 35, "standing": 25, "capacity_source": "test fixture", "shape": [[-9.1450, 38.7062], [-9.0998, 38.7678]],
     "daily": 3_000, "est_share": 0.30, "trips": [3] * len(R.HOURS)},
]
INTERCHANGES = [
    ("oriente", [("cm", "metro", 400, 7, 14), ("metro", "cm", 250, 9, 18)]),
    ("cais-do-sodre", [("carris", "metro", 300, 6, 12)]),
]
FLOWS = [("oriente", "campo-grande", 120), ("cais-do-sodre", "oriente", 90)]
ALERT_SEEDS = [("oriente", "2026-09-01", 8, 1.8, 4.2)]


def build_warehouse(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute((ROOT / "sql" / "schema.sql").read_text())

    for sid, name, lat, lon, _, ops, fac in STATIONS:
        con.execute(
            "INSERT INTO dim_stop VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [sid, name, lat, lon, h3.latlng_to_cell(lat, lon, R.HEX_RESOLUTION), ops,
             json.dumps({o: [f"{o}-{sid}"] for o in ops}), *map(bool, fac)],
        )

    rows = []
    for d in R.DAYS:
        profile = R.PROFILE_WEEKEND if d["is_weekend"] else R.PROFILE_WEEKDAY
        for sid, _, _, _, daily, ops, _ in STATIONS:
            for h, p in zip(R.HOURS, profile):
                for op in ops:
                    for seg, share in SEGMENT_SHARE.items():
                        v = round(daily * p * share / len(ops), 1)
                        rows.append((d["date"], h, sid, op, seg, v, v))
    con.executemany("INSERT INTO fact_stop_hour VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    for L in LINES:
        con.execute(
            "INSERT INTO dim_line VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            [L["line_id"], L["label"], L["name"], L["mode"], L["operator"], L["seats"],
             L["standing"], L["capacity_source"], json.dumps(L["shape"])],
        )
        con.executemany(
            "INSERT INTO fact_line_hour VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(d, h, L["line_id"], L["daily"] * p, L["daily"] * p * L["est_share"], t,
              t * (L["seats"] + L["standing"]))
             for d in R.DATES
             for h, p, t in zip(R.HOURS, R.PROFILE_WEEKDAY, L["trips"])],
        )

    for sid, pairs in INTERCHANGES:
        for d in R.DATES:
            con.executemany(
                "INSERT INTO fact_transfer VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(d, sid, *p) for p in pairs],
            )
            total = sum(p[2] for p in pairs)
            con.executemany(
                "INSERT INTO fact_transfer_hour VALUES (?, ?, ?, ?)",
                [(d, h, sid, total * p) for h, p in zip(R.HOURS, R.PROFILE_WEEKDAY)],
            )

    stop_ids = {s[0] for s in STATIONS}
    con.executemany(
        "INSERT INTO fact_flow (date, from_stop_id, to_stop_id, journeys) VALUES (?, ?, ?, ?)",
        [(d, a, b, n) for d in R.DATES for a, b, n in FLOWS
         if a in stop_ids and b in stop_ids],
    )
    con.executemany(
        "INSERT INTO fact_anomaly VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(f"{sid}-{d}-{h}", d, h, sid, 1000 * m, 1000.0, (m - 1) * 100, z)
         for sid, d, h, m, z in ALERT_SEEDS],
    )
    con.close()


_TMP = tempfile.TemporaryDirectory()
DB = Path(_TMP.name) / "warehouse.duckdb"
build_warehouse(DB)
os.environ["PULSO_DB"] = str(DB)
client = TestClient(main.app)


URLS = [
    "/api/health", "/api/meta", "/api/overview?day=2026-09-01",
    "/api/overview?day=2026-09-05&ops=metro,ferry&segment=senior",
    "/api/hex?day=2026-09-01&hour=8", "/api/hex?day=2026-09-06&hour=24&ops=carris",
    "/api/stops?day=2026-09-01&hour=18", "/api/stops/oriente?day=2026-09-01&hour=18",
    "/api/lines/1709/profile?day=2026-09-01", "/api/lines/CA-CS/profile?day=2026-09-06",
    "/api/transfers?day=2026-09-02", "/api/anomalies", "/api/anomalies?day=2026-09-01",
]


class ApiTests(unittest.TestCase):
    def setUp(self):
        os.environ["PULSO_DB"] = str(DB)

    def test_all_endpoints(self):
        for url in URLS:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 200, (url, response.status_code, response.text[:300]))

    def test_errors(self):
        self.assertEqual(client.get("/api/stops/nope").status_code, 404)
        self.assertEqual(client.get("/api/lines/nope/profile").status_code, 404)
        self.assertEqual(client.get("/api/hex?day=2025-01-01").status_code, 422)
        self.assertEqual(client.get("/api/hex?hour=3").status_code, 422)
        self.assertEqual(client.get("/api/hex?ops=uber").status_code, 422)

    def test_consistency(self):
        """The same boardings, aggregated by different endpoints, must agree."""
        query = "day=2026-09-01&hour=8&ops=metro,carris&segment=sub23"
        stops = client.get(f"/api/stops?{query}").json()["stops"]
        cells = client.get(f"/api/hex?{query}").json()["cells"]
        self.assertTrue(stops and cells)
        self.assertLessEqual(abs(sum(s["boardings"] for s in stops) - sum(c["boardings"] for c in cells)), 1)

        overview = client.get("/api/overview?day=2026-09-01&ops=metro,carris&segment=sub23").json()
        at_eight = next(item for item in overview["network_hourly"] if item["hour"] == 8)
        self.assertLessEqual(abs(at_eight["boardings"] - sum(s["boardings"] for s in stops)), 1)

        alerts = client.get("/api/anomalies?day=2026-09-01").json()["alerts"]
        self.assertEqual(overview["kpis"]["alerts"], len(alerts))


if __name__ == "__main__":
    unittest.main()
