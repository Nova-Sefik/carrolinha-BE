"""POST /api/planner: the OpenAI planning assistant, running next to the data.

The model chooses read-only tools; every tool calls the same provider code as
the public endpoints, so an AI answer, the explorer and the API always show the
same numbers. The browser receives the exact tool result it renders (context);
the model never produces chart values or geometry.

OPENAI_API_KEY lives only in the backend environment (.env locally, a Render
secret in production).
"""

import json
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from . import reference as R

ROOT = Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    """Local convenience: read KEY=VALUE lines from .env without overriding real env vars."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file()

MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-astra")
MAX_TURNS = 6
ALLOWED_ORIGINS = {
    o.strip() for o in os.environ.get(
        "CARROLINHA_ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174",
    ).split(",") if o.strip()
}
PER_MINUTE = int(os.environ.get("CARROLINHA_PLANNER_PER_MINUTE", "8"))
PER_DAY = int(os.environ.get("CARROLINHA_PLANNER_PER_DAY", "300"))

ANALYSES = ["route", "supply", "transfer", "anomaly", "journey", "compare"]
CHARTS = ["mobility_map", "route_opportunities", "demand_supply", "anomalies", "journey_path_traffic", "hour_vs_average"]
ACTIONS = ["investigate_direct_link", "increase_frequency", "rebalance_service", "improve_transfer", "monitor"]


def api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


# ------------------------------------------------------------------ abuse
class RateLimiter:
    """In-memory limits per client and per day. Real protection needs user sign-in."""

    def __init__(self):
        self.lock = threading.Lock()
        self.recent = defaultdict(deque)
        self.day, self.today = time.strftime("%Y-%m-%d"), 0

    def check(self, client: str) -> None:
        now = time.time()
        with self.lock:
            if time.strftime("%Y-%m-%d") != self.day:
                self.day, self.today = time.strftime("%Y-%m-%d"), 0
            window = self.recent[client]
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= PER_MINUTE:
                raise HTTPException(429, "Too many planner requests; wait a minute and try again.")
            if self.today >= PER_DAY:
                raise HTTPException(429, "The planner's daily request limit has been reached.")
            window.append(now)
            self.today += 1


limiter = RateLimiter()


def check_origin(origin: Optional[str]) -> None:
    if origin and origin not in ALLOWED_ORIGINS:
        raise HTTPException(403, "This origin may not use the planner.")


# ------------------------------------------------------------------ tools
_LIVE = {
    "day": {"type": ["string", "null"], "description": "ISO service date 2026-08-31..2026-09-06. Null keeps the explorer day."},
    "hour": {"type": ["integer", "null"], "minimum": 5, "maximum": 24, "description": "Service hour 5-24 (24 = 00:00-00:59). Null keeps the explorer hour."},
    "ops": {"type": "array", "items": {"type": "string", "enum": R.OPERATOR_IDS}, "description": "Operators to include. Empty keeps the explorer operators."},
    "segment": {"type": ["string", "null"], "enum": ["all", "sub23", "senior", None], "description": "Passenger segment. Null keeps the explorer segment."},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Maximum evidence rows."},
}


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function", "name": name, "strict": True, "description": description,
        "parameters": {"type": "object", "additionalProperties": False,
                       "required": list(properties), "properties": properties},
    }


TOOLS = [
    _tool("find_places", "Resolve a place name to hub stop ids. Use when a location name is uncertain or ambiguous.",
          {"query": {"type": "string", "description": "Part of a stop or station name"}}),
    _tool("query_live_stop_demand", "Stop boardings versus the same-time expected baseline for a day and hour.", _LIVE),
    _tool("query_live_line_capacity", "Estimated peak on-board load versus places offered per bus/ferry line.",
          {**_LIVE, "line": {"type": ["string", "null"], "description": "Line id or label; null ranks all lines."}}),
    _tool("query_live_transfers", "Interchange volumes and median/p90 transfer waits for a day.", _LIVE),
    _tool("query_live_anomalies", "Stop-hours with observed boardings far from expected.", _LIVE),
    _tool("query_live_golden_routes",
          "Backend-ranked direct-line opportunities where many weekday journeys need several vehicles: best route, "
          "golden route, time saving, projected riders.",
          {"origin": {"type": ["string", "null"], "description": "Optional endpoint name; null searches the network."},
           "destination": {"type": ["string", "null"], "description": "Optional endpoint name; null searches the network."},
           "verdict": {"type": ["string", "null"], "enum": ["strong", "viable", "weak", None]},
           "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    _tool("query_live_journey_traffic",
          "Journeys along a directed path of tap locations, e.g. 'traffic from Odivelas through Campo Grande', "
          "'paths above 100 journeys', 'journeys ending at Oriente'. Includes current vs typical.",
          {"day": _LIVE["day"],
           "hour": {"type": ["integer", "null"], "minimum": 5, "maximum": 24, "description": "Service hour; null keeps the explorer hour unless whole_day is true."},
           "whole_day": {"type": "boolean", "description": "True for the whole service day instead of one hour."},
           "origin": {"type": ["string", "null"], "description": "Place name or stop id where journeys start."},
           "through": {"type": "array", "items": {"type": "string"}, "description": "Places passed consecutively, in order."},
           "destination": {"type": ["string", "null"], "description": "Place where journeys end (known destinations only)."},
           "any": {"type": "array", "items": {"type": "string"}, "description": "Journeys touching any of these places."},
           "match": {"type": "string", "enum": ["contains", "exact"], "description": "exact = the whole journey is exactly origin, through, destination."},
           "min_volume": {"type": "integer", "minimum": 0, "description": "Only list paths with at least this many journeys; 0 lists all."},
           "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    _tool("query_live_compare",
          "This hour versus typical (median of the same hour on other days of the same type) for stop, network, "
          "transfer or line boardings. For journey paths use query_live_journey_traffic, which includes the comparison.",
          {"measure": {"type": "string", "enum": ["stop_boardings", "network_boardings", "transfers", "line_boardings"]},
           "subject": {"type": ["string", "null"], "description": "Stop/hub name or id, or line id/label; null only for network_boardings."},
           "day": _LIVE["day"], "hour": _LIVE["hour"], "ops": _LIVE["ops"], "segment": _LIVE["segment"]}),
]

INSTRUCTIONS = """You are Carrolinha, an urban-mobility planning assistant for Lisbon Metropolitan Area planners.

You MUST call at least one query tool before answering, and you may call several for comparisons. Every tool reads the live mobility backend. Arguments left null or empty keep the explorer's current day, hour, operators and passenger segment. Use find_places when a location name is uncertain.

Which tool:
- Traffic along a path, journeys from/through/to places, or paths above a volume: query_live_journey_traffic. Pass places in the user's order; direction matters and A→B is not B→A. Use min_volume for "above N journeys".
- This hour versus average/normal/typical: query_live_compare for stops, network, transfers or lines; query_live_journey_traffic (its comparison block) for paths.
- Best route, golden route, direct-line opportunities, time savings: query_live_golden_routes.
- Stop demand: query_live_stop_demand. Line crowding: query_live_line_capacity. Interchanges and waits: query_live_transfers. Unusual activity: query_live_anomalies.

Rules:
- Use only numbers returned by tools. Never invent, extrapolate, estimate a null, or compute a new percentage or average yourself.
- A null value means the data is not covered, the baseline is insufficient, or it is below the privacy threshold. Say which, using the tool's status or note.
- "Typical" is the median of the same hour on other days of the same type. Call it typical, and state how many comparison days it used.
- A journey path is the sequence of tap locations, not the physical route. Do not describe it as the vehicle route or corridor load.
- For path answers, state the resolved path, its direction, any volume threshold, the date and hour, and the coverage or privacy limits that apply.
- Estimated line load is not measured occupancy. Golden routes are candidates for investigation, not approved services.

Answer concisely: name the primary analysis, give 2-5 findings, choose one compatible chart, and give 2-4 follow-up questions. Return zero to four recommendations; each must use an evidence_key copied exactly from a row of the primary tool result. Charts: journey_path_traffic for path Sankeys, hour_vs_average for current-vs-typical, route_opportunities for golden routes, demand_supply for stop or line pressure, anomalies for alerts, mobility_map for anything geographic. Use an evidence key for chart.highlight when relevant, otherwise an empty string."""

RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["answer", "analysis", "findings", "recommendations", "chart", "followups"],
    "properties": {
        "answer": {"type": "string"},
        "analysis": {"type": "string", "enum": ANALYSES},
        "findings": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["evidence_key", "action", "priority", "title", "rationale"],
            "properties": {
                "evidence_key": {"type": "string"},
                "action": {"type": "string", "enum": ACTIONS},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "title": {"type": "string"},
                "rationale": {"type": "string"},
            },
        }},
        "chart": {"type": "object", "additionalProperties": False, "required": ["type", "highlight"],
                  "properties": {"type": {"type": "string", "enum": CHARTS}, "highlight": {"type": "string"}}},
        "followups": {"type": "array", "items": {"type": "string"}},
    },
}


# -------------------------------------------------------------- executors
def _dump(model) -> Any:
    return model.model_dump(mode="json", by_alias=True)


def _clock(hour: int) -> str:
    return ("00" if hour == 24 else f"{hour:02d}") + ":00"


def _filters(args: dict, base: dict) -> dict:
    day = args.get("day") or base.get("day") or "2026-09-01"
    hour = args.get("hour") or base.get("hour") or 8
    ops = [o for o in (args.get("ops") or base.get("ops") or []) if o in R.OPERATOR_IDS] or list(R.OPERATOR_IDS)
    segment = args.get("segment") or base.get("segment") or "all"
    if day not in R.DATES:
        raise ValueError(f"day must be one of {R.DATES}")
    if hour not in R.HOURS:
        raise ValueError("hour must be 5-24")
    return {"day": day, "hour": int(hour), "ops": ops, "segment": segment if segment in R.SEGMENT_IDS else "all",
            "limit": int(args.get("limit") or 16)}


def _applied(f: dict) -> dict:
    return {**{k: f[k] for k in ("day", "hour", "ops", "segment")},
            "human_summary": f"{f['day']} · {_clock(f['hour'])} · {', '.join(f['ops'])} · {f['segment']}",
            "logic": "Operators use OR; day, hour, operator selection and passenger segment use AND."}


def _result(analysis: str, evidence: list, applied: dict, charts: List[str], limitations: List[str], **extra) -> dict:
    return {"schema_version": "live-2.0", "source": "mobility_backend", "analysis": analysis, "intent": analysis,
            "evidence": evidence, "applied_filters": applied, "allowed_charts": charts,
            "filter_limitations": [x for x in limitations if x], **extra}


def resolve_place(provider, value: Optional[str]):
    """A stop id, or the best name match (exact and prefix first, then busiest)."""
    if not value:
        return None
    if provider.places_by_id([value]):
        return value
    matches = provider.search_places(value, 1).places
    return matches[0].stop_id if matches else None


def execute_tool(name: str, args: dict, base: dict, provider) -> dict:
    """Run a tool; analysis results carry {name, args} so a client can re-run them with new filters."""
    result = _execute(name, args, base, provider)
    if result.get("analysis"):
        result["tool"] = {"name": name, "args": args}
    return result


def _execute(name: str, args: dict, base: dict, provider) -> dict:
    if name == "find_places":
        places = provider.search_places(args.get("query", ""), 8)
        return {"places": [_dump(p) for p in places.places]}

    if name == "query_live_golden_routes":
        data = _dump(provider.golden())
        origin, destination = (args.get("origin") or "").lower(), (args.get("destination") or "").lower()

        def hit(place, query):
            return not query or query in place["name"].lower() or query in place["stop_id"].lower()

        def pair(route):
            if origin and destination:
                return (hit(route["from"], origin) and hit(route["to"], destination)) or \
                       (hit(route["from"], destination) and hit(route["to"], origin))
            return hit(route["from"], origin or destination) or hit(route["to"], origin or destination)

        routes = [r for r in data["routes"] if pair(r) and (not args.get("verdict") or r["verdict"] == args["verdict"])]
        evidence = [{
            "key": r["route_id"], "rank": r["rank"], "from": r["from"]["name"], "to": r["to"]["name"],
            "from_place": r["from"], "to_place": r["to"], "from_to": r["from_to_per_day"], "to_from": r["to_from_per_day"],
            "supported_journeys": r["multi_per_day"], "multi_vehicle_journeys_per_day": r["multi_per_day"],
            "existing_direct_journeys_per_day": r["direct_per_day"], "multi_vehicle_share": r["multi_share"],
            "average_legs": r["avg_legs"], "current_minutes": r["current_min"], "projected_minutes": r["projected_min"],
            "minutes_saved": r["saved_min"], "projected_riders_per_day": r["riders_per_day"],
            "person_hours_saved_per_day": r["person_hours_per_day"], "peak_hour": r["peak_hour"],
            "peak_riders": r["peak_riders"], "trips_needed_at_peak": r["trips_needed_peak"],
            "peak_headway_minutes": r["peak_headway_min"], "distance_km": r["distance_km"],
            "demand_spike_z": r["spike_z"], "demand_top_percent": r["demand_top_percent"], "verdict": r["verdict"],
            "flags": r["flags"], "hourly": r["hourly"], "current_paths": r["paths"],
            "shown_paths_share": r["shown_paths_share"], "affected_lines": r["replaced"],
            "transfer_hubs_removed": r["hubs"], "current_direct_lines": r["direct_lines"],
            "measure": "backend_ranked_direct_line",
        } for r in routes[: int(args.get("limit") or 16)]]
        summary = " · ".join(x for x in [args.get("origin") and f"from {args['origin']}",
                                         args.get("destination") and f"to {args['destination']}", args.get("verdict")] if x)
        return _result("route", evidence, {
            "origin": args.get("origin"), "destination": args.get("destination"), "verdict": args.get("verdict"),
            "human_summary": summary or "all ranked routes",
            "logic": "Named endpoints match as a pair in either direction, combined with verdict using AND.",
        }, ["route_opportunities", "mobility_map"], [data["method"],
            "Golden routes describe a typical weekday and ignore the explorer day, hour, operator and segment filters."],
            assumptions=data.get("assumptions"))

    if name == "query_live_compare":
        f = _filters(args, base)
        measure, subject = args["measure"], args.get("subject")
        if measure in ("stop_boardings", "transfers"):
            subject = resolve_place(provider, subject)
        elif measure == "line_boardings" and subject:
            wanted = subject.lower()
            subject = next((l.line_id for l in provider.meta().lines
                            if wanted in (l.line_id.lower(), l.label.lower())), None)
        else:
            subject = None
        res = provider.compare(measure, subject, f["day"], f["hour"], f["ops"], f["segment"]) \
            if measure == "network_boardings" or subject else None
        if res is None:
            return _result("compare", [], {**_applied(f), "measure": measure}, ["hour_vs_average"],
                           [f"Could not resolve {args.get('subject')!r} for {measure}."])
        data = _dump(res)
        cmp = data["comparison"]
        label = data["subject"]["name"] if data["subject"] else "whole network"
        evidence = [{"key": f"{measure}-{subject or 'network'}-{h['hour']}", "hour": h["hour"], "label": _clock(h["hour"]),
                     "current": h["current"], "typical": h["typical"], "complete": h["complete"],
                     "selected": h["hour"] == f["hour"], "measure": measure} for h in cmp["hourly"]]
        return _result("compare", evidence, {**_applied(f), "measure": measure, "subject": data["subject"],
                                             "human_summary": f"{label} · {measure.replace('_', ' ')} · {f['day']} {_clock(f['hour'])} vs typical"},
                       ["hour_vs_average"], [cmp["method"], cmp["note"]], comparison=cmp, subject=data["subject"], measure=measure)

    if name == "query_live_journey_traffic":
        from .journeys import JourneysUnavailable, get_journey_store

        f = _filters({**args, "hour": args.get("hour")}, base)
        hour = None if args.get("whole_day") else f["hour"]
        names = {"origin": args.get("origin"), "destination": args.get("destination")}
        resolved, unresolved = {}, []
        for key, value in names.items():
            resolved[key] = resolve_place(provider, value)
            if value and not resolved[key]:
                unresolved.append(value)
        lists = {}
        for key in ("through", "any"):
            lists[key] = []
            for value in args.get(key) or []:
                stop = resolve_place(provider, value)
                (lists[key].append(stop) if stop else unresolved.append(value))
        if unresolved:
            return _result("journey", [], {"day": f["day"], "hour": hour}, ["journey_path_traffic"],
                           [f"Could not resolve: {', '.join(unresolved)}. Ask the user or call find_places."])
        match = args.get("match") or "contains"
        if match == "exact" and not (resolved["origin"] or lists["through"] or resolved["destination"]):
            match = "contains"
        requested = [s for s in [resolved["origin"], *lists["through"], resolved["destination"], *lists["any"]] if s]
        try:
            store = get_journey_store(os.environ.get("CARROLINHA_JOURNEYS", "journeys.duckdb"))
        except JourneysUnavailable as error:
            return _result("journey", [], {"day": f["day"], "hour": hour}, ["journey_path_traffic"], [str(error)])
        data = _dump(store.traffic(
            day=f["day"], hour=hour, origin=resolved["origin"], through=lists["through"],
            destination=resolved["destination"], anywhere=lists["any"], match=match,
            min_volume=int(args.get("min_volume") or 0), compare=True,
            limit=min(int(args.get("limit") or 20), 100), offset=0, places=provider.places_by_id(requested),
        ))
        evidence = [{
            "key": p["key"], "rank": p["rank"], "path": [s["name"] for s in p["path"]], "stops": p["path"],
            "from": p["path"][0]["name"], "to": p["path"][-1]["name"],
            "from_place": p["path"][0], "to_place": p["path"][-1],
            "journeys": p["journeys"], "supported_journeys": p["journeys"], "share_pct": p["share_pct"],
            "typical": p["typical"], "difference_pct": p["difference_pct"], "destination": p["destination"],
            "metro_exit_share": p["metro_exit_share"], "modes": p["modes"], "measure": "journey_path",
        } for p in data["paths"] if p["path"]]
        notes = [f"“{v}” resolved to {provider.places_by_id([s])[s].name} ({s})"
                 for v, s in [(names["origin"], resolved["origin"]), (names["destination"], resolved["destination"])]
                 if v and s and v != s]
        applied = {**data["applied_filters"], "whole_day": hour is None}
        return _result("journey", evidence, applied, ["journey_path_traffic", "hour_vs_average", "mobility_map"],
                       data["limitations"] + notes,
                       journey={k: data[k] for k in ("totals", "sankey", "coverage", "privacy_min", "method")},
                       comparison=data["comparison"])

    f = _filters(args, base)
    if name == "query_live_stop_demand":
        stops = sorted(_dump(provider.stops(f["day"], f["hour"], f["ops"], f["segment"]))["stops"],
                       key=lambda s: -s["boardings"])[: f["limit"]]
        evidence = [{"key": s["stop_id"], "stop_id": s["stop_id"], "stop": s["name"], "lat": s["lat"], "lon": s["lon"],
                     "mode": ", ".join(s["operators"]), "validations": s["boardings"], "departures": 1,
                     "validations_per_departure": s["boardings"], "nominal_capacity": s["expected"],
                     "pressure_pct": round(s["ratio"] * 100), "boardings": s["boardings"], "expected": s["expected"],
                     "measure": "boardings_vs_expected", "period": f"{f['day']} {_clock(f['hour'])}"} for s in stops]
        return _result("supply", evidence, _applied(f), ["demand_supply", "mobility_map"],
                       ["Expected boardings are a same-time baseline, not vehicle capacity. Use line capacity for crowding claims."])

    if name == "query_live_line_capacity":
        wanted = (args.get("line") or "").lower()
        lines = [l for l in provider.meta().lines if not wanted or wanted in (l.line_id.lower(), l.label.lower())]
        evidence = []
        for line in lines:
            profile = provider.line_profile(line.line_id, f["day"])
            if profile is None:
                continue
            p = _dump(profile)
            at = next((h for h in p["hours"] if h["hour"] == f["hour"]), None) or max(p["hours"], key=lambda h: h["load_factor"])
            evidence.append({
                "key": f"{p['line_id']}-{at['hour']}", "stop": f"{p['label']} · {p['name']}", "line_id": p["line_id"],
                "mode": p["mode"], "operator": p["operator"], "period": f"{f['day']} {_clock(at['hour'])}",
                "validations": at["boardings"], "departures": at["trips"], "validations_per_departure": at["est_peak_load"],
                "nominal_capacity": at["places_offered"], "pressure_pct": round(at["load_factor"] * 100),
                "estimated_peak_load": at["est_peak_load"], "places_offered": at["places_offered"], "trips": at["trips"],
                "measure": "estimated_load_vs_places", "vehicle_places": p["vehicle"]["places"],
            })
        evidence = sorted(evidence, key=lambda r: -r["pressure_pct"])[: f["limit"]]
        return _result("supply", evidence, _applied(f), ["demand_supply"],
                       ["On-board load is estimated from boardings and inferred alightings; it is not measured occupancy."])

    if name == "query_live_transfers":
        data = _dump(provider.transfers(f["day"]))
        evidence = [{"key": r["stop_id"], "stop_id": r["stop_id"], "stop": r["name"], "lat": r["lat"], "lon": r["lon"],
                     "observed": r["transfers"], "expected": 0, "change_pct": r["worst_median_wait_min"],
                     "transfers": r["transfers"], "worst_median_wait_min": r["worst_median_wait_min"],
                     "fragile": r["fragile"], "pairs": r["pairs"], "period": f["day"]}
                    for r in data["interchanges"][: f["limit"]]]
        return _result("transfer", evidence, _applied(f), ["mobility_map"],
                       [data["method"], "The transfer aggregate is day-level; hour, operator and segment filters do not apply."])

    if name == "query_live_anomalies":
        data = _dump(provider.anomalies(f["day"] if args.get("day") else None))
        evidence = [{"key": r["alert_id"], "stop_id": r["stop_id"], "stop": r["name"], "lat": r["lat"], "lon": r["lon"],
                     "period": f"{r['date']} {_clock(r['hour'])}", "observed": r["observed"], "expected": r["expected"],
                     "change_pct": r["deviation_pct"], "robust_z": r["robust_z"], "direction": r["direction"]}
                    for r in data["alerts"][: f["limit"]]]
        return _result("anomaly", evidence, _applied(f), ["anomalies", "mobility_map"],
                       [data["method"], "The week alert feed is not segmented by operator or passenger type."])

    raise ValueError(f"Unknown tool {name}")


def model_view(result: dict) -> dict:
    """What the model reads: the same numbers minus heavy drawing data it never needs."""
    view = {k: v for k, v in result.items() if k != "journey"}
    if "journey" in result:
        view["journey"] = {k: v for k, v in result["journey"].items() if k != "sankey"}
    view["evidence"] = [{k: v for k, v in row.items() if k not in ("stops", "from_place", "to_place", "hourly", "current_paths")}
                        for row in result.get("evidence", [])]
    return view


# -------------------------------------------------------- recommendations
def _geometry(row: dict) -> Optional[dict]:
    start, end = row.get("from_place"), row.get("to_place")
    if start and end and start.get("stop_id") == end.get("stop_id"):
        row = {**row, "stop_id": start["stop_id"], "stop": start["name"], "lat": start["lat"], "lon": start["lon"]}
    elif start and end:
        return {"kind": "corridor", "from": row.get("from"), "to": row.get("to"),
                "from_point": row["from_place"], "to_point": row["to_place"],
                "via_points": (row.get("stops") or [])[1:-1],
                "supported_journeys": row.get("supported_journeys") or row.get("multi_vehicle_journeys_per_day") or 0}
    if isinstance(row.get("lat"), (int, float)) and isinstance(row.get("lon"), (int, float)):
        return {"kind": "stop", "stop_id": row.get("stop_id") or row.get("key"), "stop": row.get("stop"),
                "lat": row["lat"], "lon": row["lon"], "pressure_pct": row.get("pressure_pct"),
                "change_pct": row.get("change_pct")}
    return None


def validate_recommendations(items, evidence: List[dict]):
    rows = {str(r["key"]): r for r in evidence}
    recommendations, overlays, used = [], [], set()
    for item in (items or [])[:4]:
        key = str(item.get("evidence_key", ""))
        row = rows.get(key)
        title = str(item.get("title", "")).strip()[:100]
        rationale = str(item.get("rationale", "")).strip()[:320]
        if not row or key in used or item.get("action") not in ACTIONS or item.get("priority") not in ("high", "medium", "low") \
                or not title or not rationale:
            continue
        rec = {"evidence_key": key, "action": item["action"], "priority": item["priority"], "title": title, "rationale": rationale}
        recommendations.append(rec)
        used.add(key)
        geometry = _geometry(row)
        if geometry:
            overlays.append({"id": f"proposal-{key}", **rec, **geometry})
    return recommendations, overlays


# ------------------------------------------------------------------ loop
def plan(body: dict, provider) -> dict:
    from openai import OpenAI

    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(400, "question is required.")
    if not api_key():
        raise HTTPException(503, "The planner is not configured: set OPENAI_API_KEY in the backend environment.")

    base = body.get("live_filters") or {}
    client = OpenAI(api_key=api_key(), timeout=45, max_retries=1)
    inputs: List[Any] = [{"role": "user", "content": json.dumps({
        "question": question[:2000],
        "recent_conversation": (body.get("conversation") or [])[-8:],
        "current_explorer_filters": base,
        "available_visualizations": CHARTS,
    })}]
    runs, tools_used = [], []
    response = None
    for _ in range(MAX_TURNS):
        response = client.responses.create(
            model=MODEL, instructions=INSTRUCTIONS, input=inputs, tools=TOOLS,
            tool_choice="auto" if runs else "required", parallel_tool_calls=True,
            safety_identifier="carrolinha_planner", max_output_tokens=1200,
            text={"format": {"type": "json_schema", "name": "carrolinha_planner_response",
                             "strict": True, "schema": RESPONSE_SCHEMA}},
        )
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        inputs += response.output
        for call in calls:
            try:
                result = execute_tool(call.name, json.loads(call.arguments or "{}"), base, provider)
            except (ValueError, KeyError, TypeError) as error:
                result = {"error": str(error)}
            tools_used.append(call.name)
            if result.get("analysis"):
                runs.append(result)
            inputs.append({"type": "function_call_output", "call_id": call.call_id,
                           "output": json.dumps(model_view(result) if result.get("analysis") else result)})

    if not response or not response.output_text:
        raise HTTPException(502, "The planner did not finish its answer.")
    if not runs:
        raise HTTPException(502, "The planner answered without querying mobility data.")
    parsed = json.loads(response.output_text)
    selected = next((r for r in reversed(runs) if r["analysis"] == parsed["analysis"]), runs[-1])
    context = {**selected, "question": question}
    recommendations, overlays = validate_recommendations(parsed.get("recommendations"), context["evidence"])
    return {
        **parsed, "recommendations": recommendations, "map_overlays": overlays, "feasibility": [],
        "context": context, "resolved_filters": context["applied_filters"],
        "tools_used": list(dict.fromkeys(tools_used)), "model": MODEL,
    }
