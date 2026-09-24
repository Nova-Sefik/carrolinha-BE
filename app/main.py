"""
Carrolinha API — FastAPI app.

    uvicorn app.main:app --reload              # mock data (default)
    PULSO_PROVIDER=duckdb PULSO_DB=warehouse.duckdb uvicorn app.main:app

Interactive docs: http://localhost:8000/docs
"""

import os
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import reference as R
from . import schemas as S

app = FastAPI(
    title="Carrolinha API",
    version="0.1.0",
    description="Metropolitan demand twin for TML — Hack the City 2026, challenge #1.",
)


# Hackathon setting: any origin may call the API. Tighten before real use.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
)


def get_provider():
    from .duckdb_provider import get_duckdb_provider

    return get_duckdb_provider(os.environ.get("PULSO_DB", "warehouse.duckdb"))


# ------------------------------------------------------------ shared params
def day_param(
    day: str = Query("2026-09-01", description="ISO date within the dataset week")
) -> str:
    if day not in R.DATES:
        raise HTTPException(422, f"day must be one of {R.DATES}")
    return day


def hour_param(
    hour: int = Query(8, ge=5, le=24, description="Service hour 5-24; 24 = 00:00-00:59")
) -> int:
    return hour


def ops_param(
    ops: str = Query(
        ",".join(R.OPERATOR_IDS), description="Comma-separated operator ids"
    )
) -> List[str]:
    chosen = [o.strip() for o in ops.split(",") if o.strip()]
    bad = [o for o in chosen if o not in R.OPERATOR_IDS]
    if bad:
        raise HTTPException(422, f"unknown operator(s) {bad}; valid: {R.OPERATOR_IDS}")
    return chosen


def segment_param(segment: S.SegmentId = Query("all")) -> str:
    return segment


# ------------------------------------------------------------------- routes
@app.get("/api/health")
def health(p=Depends(get_provider)):
    return {
        "ok": True,
        "provider": "mock" if getattr(p, "is_mock", False) else "duckdb",
    }


@app.get("/api/meta", response_model=S.Meta)
def meta(p=Depends(get_provider)):
    return p.meta()


@app.get("/api/overview", response_model=S.Overview)
def overview(
    day: str = Depends(day_param),
    ops: List[str] = Depends(ops_param),
    segment: str = Depends(segment_param),
    p=Depends(get_provider),
):
    return p.overview(day, ops, segment)


@app.get("/api/hex", response_model=S.HexResponse)
def hex_cells(
    day: str = Depends(day_param),
    hour: int = Depends(hour_param),
    ops: List[str] = Depends(ops_param),
    segment: str = Depends(segment_param),
    p=Depends(get_provider),
):
    return p.hex(day, hour, ops, segment)


@app.get("/api/stops", response_model=S.StopsResponse)
def stops(
    day: str = Depends(day_param),
    hour: int = Depends(hour_param),
    ops: List[str] = Depends(ops_param),
    segment: str = Depends(segment_param),
    p=Depends(get_provider),
):
    return p.stops(day, hour, ops, segment)


@app.get("/api/stops/{stop_id}", response_model=S.StopDetail)
def stop_detail(
    stop_id: str,
    day: str = Depends(day_param),
    hour: int = Depends(hour_param),
    ops: List[str] = Depends(ops_param),
    segment: str = Depends(segment_param),
    p=Depends(get_provider),
):
    res = p.stop_detail(stop_id, day, hour, ops, segment)
    if res is None:
        raise HTTPException(404, f"unknown stop_id {stop_id!r}")
    return res


@app.get("/api/lines/{line_id}/profile", response_model=S.LineProfile)
def line_profile(line_id: str, day: str = Depends(day_param), p=Depends(get_provider)):
    res = p.line_profile(line_id, day)
    if res is None:
        raise HTTPException(404, f"unknown line_id {line_id!r}")
    return res


@app.get("/api/transfers", response_model=S.TransfersResponse)
def transfers(day: str = Depends(day_param), p=Depends(get_provider)):
    return p.transfers(day)


@app.get("/api/anomalies", response_model=S.AnomaliesResponse)
def anomalies(
    day: Optional[str] = Query(None, description="Omit for the whole week"),
    p=Depends(get_provider),
):
    if day is not None and day not in R.DATES:
        raise HTTPException(422, f"day must be one of {R.DATES}")
    return p.anomalies(day)


@app.get("/api/golden", response_model=S.GoldenResponse, response_model_by_alias=True)
def golden(p=Depends(get_provider)):
    """Golden lines: direct links where many people need 2+ vehicles today (typical weekday)."""
    return p.golden()


@app.get("/api/places", response_model=S.PlacesResponse)
def places(
    q: str = Query(..., min_length=2, description="Part of a stop or hub name"),
    limit: int = Query(10, ge=1, le=50),
    p=Depends(get_provider),
):
    """Resolve a place name to hub stop_ids for path filters."""
    return p.search_places(q, limit)


@app.get("/api/compare", response_model=S.CompareResponse)
def compare(
    measure: S.CompareMeasure = Query(..., description="What to compare"),
    subject: Optional[str] = Query(None, description="stop_id for stop measures, line_id for line_boardings"),
    day: str = Depends(day_param),
    hour: int = Depends(hour_param),
    ops: List[str] = Depends(ops_param),
    segment: str = Depends(segment_param),
    p=Depends(get_provider),
):
    """This hour versus typical: the median of the same hour on the other days of the same type."""
    if measure != "network_boardings" and not subject:
        raise HTTPException(422, f"subject is required for {measure}")
    res = p.compare(measure, subject, day, hour, ops, segment)
    if res is None:
        raise HTTPException(404, f"unknown subject {subject!r}")
    return res


def _stop_list(value: Optional[str]) -> List[str]:
    return [s.strip() for s in (value or "").split(",") if s.strip()]


def get_journeys():
    from .journeys import JourneysUnavailable, get_journey_store

    try:
        return get_journey_store(os.environ.get("CARROLINHA_JOURNEYS", "journeys.duckdb"))
    except JourneysUnavailable as error:
        raise HTTPException(503, str(error))


@app.get("/api/journey-traffic", response_model=S.JourneyTrafficResponse)
def journey_traffic(
    day: str = Depends(day_param),
    hour: Optional[int] = Query(None, ge=5, le=24, description="Service hour; omit for the whole day"),
    origin: Optional[str] = Query(None, description="stop_id where the journey starts"),
    through: Optional[str] = Query(None, description="Comma-separated stop_ids passed in this order, consecutively"),
    destination: Optional[str] = Query(None, description="stop_id where the journey ends (known destinations only)"),
    anywhere: Optional[str] = Query(None, alias="any", description="Comma-separated stop_ids; journeys touching any of them"),
    match: Literal["contains", "exact"] = Query("contains", description="exact = the whole journey is origin, through, destination"),
    min_volume: int = Query(0, ge=0, description="Only list paths with at least this many journeys"),
    compare: bool = Query(True, description="Include current vs typical"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    p=Depends(get_provider),
    j=Depends(get_journeys),
):
    """Journeys along a directed path, with volume threshold and hour-vs-typical comparison."""
    through_ids, any_ids = _stop_list(through), _stop_list(anywhere)
    requested = [s for s in [origin, *through_ids, destination, *any_ids] if s]
    found = p.places_by_id(requested)
    unknown = sorted(set(requested) - set(found))
    if unknown:
        raise HTTPException(422, f"unknown stop_id(s) {unknown}; resolve names with /api/places?q=")
    if match == "exact" and not (origin or through_ids or destination):
        raise HTTPException(422, "match=exact needs origin, through or destination")
    return j.traffic(
        day=day, hour=hour, origin=origin, through=through_ids, destination=destination,
        anywhere=any_ids, match=match, min_volume=min_volume, compare=compare,
        limit=limit, offset=offset, places=found,
    )
