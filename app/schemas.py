"""
Response models: the API contract, in code.

The frontend builds against these shapes. The mock provider and the DuckDB
provider must both return exactly these models, so swapping one for the other
never breaks the UI. FastAPI turns them into the OpenAPI docs at /docs.
"""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

OperatorId = Literal["metro", "carris", "cm", "rail", "ferry", "other"]
SegmentId = Literal["all", "sub23", "senior"]


# ---------------------------------------------------------------- /api/meta
class Day(BaseModel):
    date: str = Field(examples=["2026-09-01"])
    label: str = Field(examples=["Tue 1 Sep"])
    weekday: str
    is_weekend: bool


class Operator(BaseModel):
    id: OperatorId
    name: str
    color: str = Field(description="Hex colour to use for this operator everywhere in the UI")
    agency_codes: List[str] = Field(description="agency_code values in validations / plans API")


class Segment(BaseModel):
    id: SegmentId
    label: str


class HourLabel(BaseModel):
    hour: int = Field(description="Service hour 5–24; 24 = 00:00–00:59 after midnight")
    label: str = Field(examples=["08:00"])


class Scales(BaseModel):
    """Fixed maxima for colour scales. Use these, never per-hour maxima, so the
    time slider animation stays comparable across hours and days."""
    stop_boardings_max: float
    hex_boardings_max: float
    network_hour_max: float


class LineRef(BaseModel):
    line_id: str
    label: str
    name: str
    mode: Literal["bus", "ferry"]
    operator: OperatorId


class Meta(BaseModel):
    is_mock: bool
    week_start: str
    week_end: str
    note: str
    days: List[Day]
    hours: List[HourLabel]
    operators: List[Operator]
    segments: List[Segment]
    hex_resolution: int
    scales: Scales
    lines: List[LineRef]


# ------------------------------------------------------------ /api/overview
class Kpis(BaseModel):
    boardings: int = Field(description="Entry validations that day, after filters")
    busiest_hour: int
    transfers: int = Field(description="Cross-operator transfers detected that day")
    alerts: int


class OperatorShare(BaseModel):
    operator: OperatorId
    boardings: int
    share: float = Field(description="0–1")


class HourValue(BaseModel):
    hour: int
    boardings: float


class InterchangeRef(BaseModel):
    stop_id: str
    name: str
    transfers: int


class Overview(BaseModel):
    date: str
    kpis: Kpis
    operator_share: List[OperatorShare]
    network_hourly: List[HourValue] = Field(description="The pulse strip above the time slider")
    top_interchanges: List[InterchangeRef]


# ----------------------------------------------------------------- /api/hex
class HexCell(BaseModel):
    h3: str = Field(description="H3 cell index; feed straight into deck.gl H3HexagonLayer")
    boardings: float
    expected: float = Field(description="Same hour on a typical weekday (for anomaly colouring)")
    ratio: float = Field(description="boardings / expected")


class HexResponse(BaseModel):
    date: str
    hour: int
    resolution: int
    cells: List[HexCell]


# --------------------------------------------------------------- /api/stops
class StopPoint(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    operators: List[OperatorId]
    boardings: float
    expected: float
    ratio: float


class StopsResponse(BaseModel):
    date: str
    hour: int
    stops: List[StopPoint]


# ---------------------------------------------------------- /api/stops/{id}
class HourObsExp(BaseModel):
    hour: int
    boardings: float
    expected: float


class WeekRow(BaseModel):
    date: str
    weekday: str
    hourly: List[float] = Field(description="Boardings for hours 5..24, in order")


class Facilities(BaseModel):
    shelter: bool
    step_free: bool
    realtime_display: bool
    wheelchair_boarding: bool


class NowValue(BaseModel):
    boardings: float
    expected: float
    deviation_pct: float = Field(description="(boardings / expected − 1) × 100")


class TransfersHere(BaseModel):
    transfers: int
    worst_median_wait_min: int


class StopDetail(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    operators: List[OperatorId]
    operator_stop_ids: Dict[str, List[str]] = Field(description="The operator stop_ids grouped into this hub")
    date: str
    hour: int
    now: NowValue
    hourly: List[HourObsExp]
    week_grid: List[WeekRow]
    mix: Dict[str, float] = Field(description="Share of boardings: regular, sub23, senior (sums to 1)")
    facilities: Facilities
    transfers_here: Optional[TransfersHere] = None


# ---------------------------------------------------------- /api/lines/{id}
class Vehicle(BaseModel):
    seats: int
    standing: int
    places: int
    source: str


class LineHour(BaseModel):
    hour: int
    boardings: float
    est_peak_load: float = Field(description="Estimated people on board at the busiest point of an average trip that hour")
    trips: int
    places_offered: int = Field(description="trips × vehicle places")
    load_factor: float = Field(description="est_peak_load / places_offered; >1 means over capacity")


class PeakRef(BaseModel):
    hour: int
    load_factor: float


class WhatIf(BaseModel):
    move_to_hours: List[int] = Field(description="The Nth moved trip goes to move_to_hours[N-1]")
    move_from_hours: List[int] = Field(description="…and is taken from move_from_hours[N-1]")
    note: str


class LineProfile(BaseModel):
    line_id: str
    label: str
    name: str
    mode: Literal["bus", "ferry"]
    operator: OperatorId
    vehicle: Vehicle
    shape: List[List[float]] = Field(description="[[lon, lat], ...] for a deck.gl PathLayer")
    date: str
    hours: List[LineHour]
    peak: PeakRef
    whatif: WhatIf


# ----------------------------------------------------------- /api/transfers
class TransferPair(BaseModel):
    from_operator: OperatorId
    to_operator: OperatorId
    transfers: int
    median_wait_min: int
    p90_wait_min: int


class HourCount(BaseModel):
    hour: int
    transfers: Optional[float] = Field(description="Null when under the privacy threshold")


class Interchange(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    transfers: int
    worst_median_wait_min: int
    fragile: bool = Field(description="Any operator pair with median wait ≥ 10 min")
    transfers_below_privacy: int = Field(0, description="Transfers in operator pairs under the privacy threshold: counted in `transfers`, not listed in `pairs`")
    pairs: List[TransferPair]
    hourly: List[HourCount]


class Place(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float


class Flow(BaseModel):
    from_stop_id: str
    from_name: str
    from_lon: float
    from_lat: float
    to_stop_id: str
    to_name: str
    to_lon: float
    to_lat: float
    journeys: int
    via: List[Place] = Field(default_factory=list, description="Hubs where people change vehicle, in order")
    modes: List[str] = Field(default_factory=list, description="Operator of each leg, in order")
    via_share: Optional[float] = Field(None, description="Share of this flow's journeys that follow this chain")


class TransfersResponse(BaseModel):
    date: str
    interchanges: List[Interchange]
    flows: List[Flow] = Field(description="Draw from -> via... -> to")
    method: str


# ----------------------------------------------------------- /api/anomalies
class HourObsExp2(BaseModel):
    hour: int
    observed: float
    expected: float


class Alert(BaseModel):
    alert_id: str
    stop_id: str
    name: str
    lat: float
    lon: float
    date: str
    hour: int
    observed: float
    expected: float
    deviation_pct: float
    robust_z: float
    direction: Literal["above", "below"]
    hourly: List[HourObsExp2]


class AnomaliesResponse(BaseModel):
    alerts: List[Alert]
    method: str


# ------------------------------------------------------------- /api/golden
class GoldenLeg(BaseModel):
    operator: str
    line_id: str
    label: str
    name: str = ""


class GoldenPath(BaseModel):
    legs: List[GoldenLeg]
    via: List[Place]
    journeys_per_day: float
    share: float = Field(description="Share of the route's multi-vehicle journeys following this chain")


class GoldenReplaced(BaseModel):
    operator: str
    line_id: str
    label: str
    name: str = ""
    riders_removed_per_day: float
    line_riders_per_day: float
    share_of_line: Optional[float]
    frequency_review_recommended: bool = Field(description="True when projected removed riders are at least 15% of current line riders")


class GoldenHub(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    transfers_removed_per_day: float
    hub_boardings_per_day: float
    share_of_hub: Optional[float]


class GoldenDirect(BaseModel):
    operator: str
    line_id: str
    label: str
    name: str = ""
    journeys_per_day: float


class GoldenFlag(BaseModel):
    level: Literal["benefit", "info", "risk"]
    text: str


class GoldenHour(BaseModel):
    hour: int
    journeys: float


class GoldenRoute(BaseModel):
    route_id: str
    rank: int
    from_: Place = Field(alias="from")
    to: Place
    distance_km: float
    multi_per_day: float = Field(description="Weekday journeys between the two areas needing 2+ vehicles (both ways)")
    direct_per_day: float
    multi_share: float
    avg_legs: float
    current_min: Optional[float] = Field(description="Median door-to-door minutes today")
    projected_min: float = Field(description="Direct line: distance x 1.3 / 18 km/h + 6 min average wait")
    saved_min: Optional[float]
    riders_per_day: float = Field(description="Projected riders: multi_per_day x capture rate")
    person_hours_per_day: Optional[float]
    peak_hour: Optional[int]
    peak_riders: Optional[float]
    trips_needed_peak: Optional[int]
    peak_headway_min: Optional[int] = Field(description="Minutes between peak vehicles implied by trips_needed_peak")
    share_a_to_b: Optional[float]
    share_b_to_a: Optional[float]
    from_to_per_day: Optional[float] = Field(description="Multi-vehicle weekday journeys from the returned from place to the returned to place")
    to_from_per_day: Optional[float] = Field(description="Multi-vehicle weekday journeys in the reverse direction")
    spike_z: float
    demand_top_percent: int = Field(description="Approximate upper-tail percentage represented by spike_z, minimum 1")
    shown_paths_share: float = Field(description="Share of multi-vehicle journeys covered by the returned current_paths list")
    verdict: Literal["strong", "viable", "weak"]
    flags: List[GoldenFlag]
    hourly: List[GoldenHour]
    paths: List[GoldenPath]
    replaced: List[GoldenReplaced]
    hubs: List[GoldenHub]
    direct_lines: List[GoldenDirect]

    model_config = {"populate_by_name": True}


class GoldenResponse(BaseModel):
    routes: List[GoldenRoute]
    method: str
    assumptions: Dict[str, float]


# ------------------------------------------------------------- /api/places
class PlaceMatch(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    operators: List[OperatorId]


class PlacesResponse(BaseModel):
    query: str
    places: List[PlaceMatch]


# ------------------------------------------ comparisons (hour vs typical)
ComparisonStatus = Literal[
    "ok", "insufficient_baseline", "incomplete_coverage", "below_privacy_threshold"
]


class BaselineDay(BaseModel):
    date: str
    value: Optional[float] = Field(description="Null when below the privacy threshold")


class ComparisonHour(BaseModel):
    hour: int
    current: Optional[float] = Field(description="Null when not covered or below the privacy threshold")
    typical: Optional[float] = Field(description="Null when the baseline is insufficient or below the privacy threshold")
    complete: bool = Field(description="The selected day's data fully covers this hour")


class Comparison(BaseModel):
    status: ComparisonStatus
    current: Optional[float]
    typical: Optional[float]
    difference: Optional[float] = Field(description="current - typical")
    difference_pct: Optional[float] = Field(description="100 x difference / typical; null when typical is 0 or unknown")
    method: str
    day_type: Literal["weekday", "weekend"]
    baseline_days: List[BaselineDay]
    sample_days: int
    min_sample_days: int
    hours_compared: List[int]
    hourly: List[ComparisonHour] = Field(description="The whole service day, current vs typical, for context")
    note: Optional[str] = None


CompareMeasure = Literal["stop_boardings", "network_boardings", "transfers", "line_boardings"]


class CompareSubject(BaseModel):
    id: str
    name: str


class CompareResponse(BaseModel):
    measure: CompareMeasure
    subject: Optional[CompareSubject]
    date: str
    hour: int
    comparison: Comparison


# -------------------------------------------------------- /api/journey-traffic
DestinationEvidence = Literal["known", "unknown"]


class JourneyPath(BaseModel):
    key: str = Field(description="Stable id: stop ids joined by '>' plus ':known' or ':unknown'")
    rank: int
    path: List[Place] = Field(description="Tap locations in order; the last one is the destination only when destination is known")
    destination: DestinationEvidence
    metro_exit_share: Optional[float] = Field(description="Share of these journeys whose destination is a Metro exit (the rest are next-boarding inferred); null when unknown")
    modes: List[str] = Field(description="Most common operator sequence, one per boarding")
    modes_share: float = Field(description="Share of these journeys that used that operator sequence")
    journeys: int
    share_pct: float = Field(description="Share of the shown volume")
    typical: Optional[float] = Field(description="Typical volume for this path; null when the baseline is insufficient or below the privacy threshold")
    difference_pct: Optional[float]


class SankeyNode(BaseModel):
    id: str
    name: str
    layer: int = Field(description="0 = origin, last = destination")
    kind: Literal["stop", "other", "more", "unknown"]
    stop_id: Optional[str]
    value: int


class SankeyLink(BaseModel):
    source: int
    target: int
    value: int


class JourneySankey(BaseModel):
    layers: List[str]
    nodes: List[SankeyNode]
    links: List[SankeyLink]


class JourneyTotals(BaseModel):
    matched_volume: Optional[int] = Field(description="Every journey matching the filters; null when below the privacy threshold")
    shown_volume: int = Field(description="Journeys in the paths returned (sum of all pages); equals the Sankey total")
    shown_paths: int
    below_min_volume: int = Field(description="Journeys in paths under min_volume")
    below_privacy_threshold: int = Field(description="Journeys in paths under the privacy threshold; counted but never listed")


class JourneyFilters(BaseModel):
    day: str
    hour: Optional[int]
    origin: Optional[Place]
    through: List[Place]
    destination: Optional[Place]
    any: List[Place]
    match: Literal["contains", "exact"]
    min_volume: int
    human_summary: str
    logic: str


class CoveredDay(BaseModel):
    date: str
    hours: List[int] = Field(description="Fully covered service hours")


class JourneyCoverage(BaseModel):
    complete: bool = Field(description="The selected day/hour is fully covered by the source files")
    complete_hours: List[int] = Field(description="Fully covered service hours on the selected day")
    covered_days: List[CoveredDay] = Field([], description="Every day of the week with its fully covered hours, so clients can suggest periods that have data")
    source: str


class JourneyTrafficResponse(BaseModel):
    applied_filters: JourneyFilters
    totals: JourneyTotals
    paths: List[JourneyPath]
    offset: int
    limit: int
    sankey: JourneySankey
    comparison: Optional[Comparison]
    coverage: JourneyCoverage
    privacy_min: int
    method: str
    limitations: List[str]
