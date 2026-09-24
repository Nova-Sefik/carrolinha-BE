"""Run: python -m pytest -q   (or: python tests/test_api.py)

The tests build a small fixture warehouse from sql/schema.sql, filled with the
mock reference data in app/reference.py, so they need no pipeline output.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import h3  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app import reference as R  # noqa: E402

SEGMENT_SHARE = {"regular": 0.7, "sub23": 0.2, "senior": 0.1}


def build_warehouse(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute((ROOT / "sql" / "schema.sql").read_text())

    for sid, name, lat, lon, _, ops, fac in R.STATIONS:
        con.execute(
            "INSERT INTO dim_stop VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [sid, name, lat, lon, h3.latlng_to_cell(lat, lon, R.HEX_RESOLUTION), ops,
             json.dumps({o: [f"{o}-{sid}"] for o in ops}), *map(bool, fac)],
        )

    rows = []
    for d in R.DAYS:
        profile = R.PROFILE_WEEKEND if d["is_weekend"] else R.PROFILE_WEEKDAY
        for sid, _, _, _, daily, ops, _ in R.STATIONS:
            for h, p in zip(R.HOURS, profile):
                for op in ops:
                    for seg, share in SEGMENT_SHARE.items():
                        v = round(daily * p * share / len(ops), 1)
                        rows.append((d["date"], h, sid, op, seg, v, v))
    con.executemany("INSERT INTO fact_stop_hour VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    for L in R.LINES:
        con.execute(
            "INSERT INTO dim_line VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [L["line_id"], L["label"], L["name"], L["mode"], L["operator"], L["seats"],
             L["standing"], L["capacity_source"], json.dumps(L["shape"])],
        )
        con.executemany(
            "INSERT INTO fact_line_hour VALUES (?, ?, ?, ?, ?, ?)",
            [(d, h, L["line_id"], L["daily"] * p, L["daily"] * p * L["est_share"], t)
             for d in R.DATES
             for h, p, t in zip(R.HOURS, R.PROFILE_WEEKDAY, L["trips"])],
        )

    for sid, pairs in R.INTERCHANGES:
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

    stop_ids = {s[0] for s in R.STATIONS}
    con.executemany(
        "INSERT INTO fact_flow VALUES (?, ?, ?, ?)",
        [(d, a, b, n) for d in R.DATES for a, b, n in R.FLOWS
         if a in stop_ids and b in stop_ids],
    )
    con.executemany(
        "INSERT INTO fact_anomaly VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(f"{sid}-{d}-{h}", d, h, sid, 1000 * m, 1000.0, (m - 1) * 100, z)
         for sid, d, h, m, z in R.ALERT_SEEDS],
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


def test_all_endpoints():
    for u in URLS:
        r = client.get(u)
        assert r.status_code == 200, (u, r.status_code, r.text[:300])


def test_errors():
    assert client.get("/api/stops/nope").status_code == 404
    assert client.get("/api/lines/nope/profile").status_code == 404
    assert client.get("/api/hex?day=2025-01-01").status_code == 422
    assert client.get("/api/hex?hour=3").status_code == 422
    assert client.get("/api/hex?ops=uber").status_code == 422


def test_consistency():
    """The same boardings, aggregated by different endpoints, must agree."""
    q = "day=2026-09-01&hour=8&ops=metro,carris&segment=sub23"
    stops = client.get(f"/api/stops?{q}").json()["stops"]
    cells = client.get(f"/api/hex?{q}").json()["cells"]
    assert stops and cells
    assert abs(sum(s["boardings"] for s in stops) - sum(c["boardings"] for c in cells)) <= 1

    ov = client.get("/api/overview?day=2026-09-01&ops=metro,carris&segment=sub23").json()
    at8 = next(x for x in ov["network_hourly"] if x["hour"] == 8)
    assert abs(at8["boardings"] - sum(s["boardings"] for s in stops)) <= 1

    alerts = client.get("/api/anomalies?day=2026-09-01").json()["alerts"]
    assert ov["kpis"]["alerts"] == len(alerts)


if __name__ == "__main__":
    test_all_endpoints(); test_errors(); test_consistency(); print("all tests passed")
