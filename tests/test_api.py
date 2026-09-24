"""Run: python -m pytest -q   (or: python tests/test_api.py)"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

DB = ROOT / "mock_warehouse.duckdb"


def client(kind):
    os.environ["PULSO_PROVIDER"] = kind
    os.environ["PULSO_DB"] = str(DB)
    return TestClient(main.app)


URLS = [
    "/api/health", "/api/meta", "/api/overview?day=2026-09-01",
    "/api/overview?day=2026-09-05&ops=metro,ferry&segment=senior",
    "/api/hex?day=2026-09-01&hour=8", "/api/hex?day=2026-09-06&hour=24&ops=carris",
    "/api/stops?day=2026-09-01&hour=18", "/api/stops/oriente?day=2026-09-01&hour=18",
    "/api/lines/1709/profile?day=2026-09-01", "/api/lines/CA-CS/profile?day=2026-09-06",
    "/api/transfers?day=2026-09-02", "/api/anomalies", "/api/anomalies?day=2026-09-01",
]


def test_all_endpoints_both_providers():
    for kind in ("mock", "duckdb"):
        c = client(kind)
        for u in URLS:
            r = c.get(u)
            assert r.status_code == 200, (kind, u, r.status_code, r.text[:300])


def test_errors():
    c = client("mock")
    assert c.get("/api/stops/nope").status_code == 404
    assert c.get("/api/lines/nope/profile").status_code == 404
    assert c.get("/api/hex?day=2025-01-01").status_code == 422
    assert c.get("/api/hex?hour=3").status_code == 422
    assert c.get("/api/hex?ops=uber").status_code == 422


def test_parity():
    """Stop-level numbers must match between mock and duckdb (hex differs by design:
    the mock smooths over neighbouring cells, the real pipeline does not)."""
    a, b = client("mock"), client("duckdb")
    for u in ["/api/stops?day=2026-09-01&hour=8&ops=metro,carris&segment=sub23",
              "/api/stops/campogrande?day=2026-09-03&hour=17",
              "/api/lines/3001/profile?day=2026-09-02", "/api/anomalies"]:
        ja, jb = a.get(u).json(), b.get(u).json()
        if "stops" in ja:
            ma = {s["stop_id"]: s["boardings"] for s in ja["stops"]}
            mb = {s["stop_id"]: s["boardings"] for s in jb["stops"]}
            assert ma.keys() == mb.keys()
            assert all(abs(ma[k] - mb[k]) <= 0.2 for k in ma), u
        elif "hourly" in ja and "week_grid" in ja:
            assert all(abs(x["boardings"] - y["boardings"]) <= 0.2 for x, y in zip(ja["hourly"], jb["hourly"]))
        elif "hours" in ja:
            assert [h["trips"] for h in ja["hours"]] == [h["trips"] for h in jb["hours"]]
        else:
            assert [x["alert_id"] for x in ja["alerts"]] == [x["alert_id"] for x in jb["alerts"]]
    ta = a.get("/api/overview?day=2026-09-04").json()["kpis"]
    tb = b.get("/api/overview?day=2026-09-04").json()["kpis"]
    assert abs(ta["boardings"] - tb["boardings"]) <= 2 and ta["alerts"] == tb["alerts"]


if __name__ == "__main__":
    test_all_endpoints_both_providers(); test_errors(); test_parity(); print("all tests passed")
