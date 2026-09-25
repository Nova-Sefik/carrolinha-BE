"""
DuckDBProvider: the real backend. Reads warehouse.duckdb (tables defined in
sql/schema.sql) and returns exactly the same models as MockProvider.

Switch to it with:
    CARROLINHA_PROVIDER=duckdb CARROLINHA_DB=warehouse.duckdb uvicorn app.main:app
"""

import json
import math
import threading
from functools import lru_cache
from typing import List, Optional

import duckdb

from . import reference as R
from . import schemas as S
from .privacy import PRIVACY_MIN, shown

ALL_SEGMENTS = ["regular", "sub23", "senior"]

GOLDEN_METHOD = (
    "Journeys = a card's taps chained until a 60-min gap; destination = where the card starts its next "
    "journey (or its Metro exit). Areas = H3 cells of ~5 km². A pair of areas becomes a golden line when, "
    "on a typical weekday, an outlying number of people (robust z of log volume >= 2.5) need 2+ vehicles "
    "between them, that is at least half of all trips between them, the ends are 3+ km apart and a direct "
    "bus would save 8+ minutes. Projections assume 60% of those travellers switch."
)


def _segments(segment: str) -> List[str]:
    return ALL_SEGMENTS if segment == "all" else [segment]


def _in(values: List[str]) -> str:
    """A safe SQL IN-list for values already validated against a whitelist."""
    return "(" + ",".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


def _golden_derived(multi_per_day, share_a_to_b, trips_needed_peak, spike_z):
    """Canonical API derivatives for a golden route; clients only format these values."""
    share_b_to_a = None if share_a_to_b is None else round(1 - share_a_to_b, 4)
    from_to_per_day = None if share_a_to_b is None else round(multi_per_day * share_a_to_b, 1)
    to_from_per_day = None if from_to_per_day is None else round(multi_per_day - from_to_per_day, 1)
    peak_headway_min = None if not trips_needed_peak else int(math.floor(60 / trips_needed_peak + 0.5))
    upper_tail_pct = 100 * (1 - (0.5 * (1 + math.erf(spike_z / math.sqrt(2)))))
    return {
        "share_b_to_a": share_b_to_a,
        "from_to_per_day": from_to_per_day,
        "to_from_per_day": to_from_per_day,
        "peak_headway_min": peak_headway_min,
        "demand_top_percent": max(1, int(math.floor(upper_tail_pct + 0.5))),
    }


class DuckDBProvider:
    is_mock = False

    def __init__(self, path: str):
        self.con = duckdb.connect(path, read_only=True)
        self.lock = threading.Lock()  # one connection, many request threads
        self.days = {d["date"]: d for d in R.DAYS}

    def _q(self, sql: str, params: Optional[list] = None):
        with self.lock:
            return self.con.execute(sql, params or []).fetchall()

    def _scalar(self, sql: str, params: Optional[list] = None):
        rows = self._q(sql, params)
        return rows[0][0] if rows else None

    # ------------------------------------------------------------------ meta
    def meta(self) -> S.Meta:
        stop_max = (
            self._scalar(
                "SELECT max(v) FROM (SELECT sum(boardings) v FROM fact_stop_hour GROUP BY date, hour, stop_id)"
            )
            or 1
        )
        hex_max = (
            self._scalar(
                """SELECT max(v) FROM (SELECT sum(f.boardings) v FROM fact_stop_hour f
                                  JOIN dim_stop s USING (stop_id) GROUP BY f.date, f.hour, s.h3)"""
            )
            or 1
        )
        net_max = (
            self._scalar(
                "SELECT max(v) FROM (SELECT sum(boardings) v FROM fact_stop_hour GROUP BY date, hour)"
            )
            or 1
        )
        lines = self._q(
            "SELECT line_id, label, name, mode, operator FROM dim_line ORDER BY line_id"
        )
        return S.Meta(
            is_mock=False,
            week_start=R.WEEK_START,
            week_end=R.WEEK_END,
            note="Computed from TML validations, 31 Aug–6 Sep 2026.",
            days=[S.Day(**d) for d in R.DAYS],
            hours=[
                S.HourLabel(hour=h, label=("00" if h == 24 else f"{h:02d}") + ":00")
                for h in R.HOURS
            ],
            operators=[S.Operator(**o) for o in R.OPERATORS],
            segments=[S.Segment(**s) for s in R.SEGMENTS],
            hex_resolution=R.HEX_RESOLUTION,
            scales=S.Scales(
                stop_boardings_max=round(stop_max, 1),
                hex_boardings_max=round(hex_max, 1),
                network_hour_max=round(net_max, 1),
            ),
            lines=[
                S.LineRef(line_id=r[0], label=r[1], name=r[2], mode=r[3], operator=r[4])
                for r in lines
            ],
        )

    # -------------------------------------------------------------- overview
    def overview(self, date: str, ops: List[str], segment: str) -> S.Overview:
        filt = f"date = ? AND operator IN {_in(ops)} AND segment IN {_in(_segments(segment))}"
        hourly = dict(
            self._q(
                f"SELECT hour, sum(boardings) FROM fact_stop_hour WHERE {filt} GROUP BY hour",
                [date],
            )
        )
        series = [hourly.get(h, 0.0) for h in R.HOURS]
        busiest = R.HOURS[series.index(max(series))] if any(series) else R.HOURS[0]
        share_rows = dict(
            self._q(
                f"""SELECT operator, sum(boardings) FROM fact_stop_hour
                                      WHERE date = ? AND segment IN {_in(_segments(segment))} GROUP BY operator""",
                [date],
            )
        )
        tot = sum(share_rows.values()) or 1
        top = self._q(
            """SELECT t.stop_id, s.name, sum(t.transfers) n FROM fact_transfer t JOIN dim_stop s USING (stop_id)
                         WHERE t.date = ? GROUP BY t.stop_id, s.name ORDER BY n DESC LIMIT 4""",
            [date],
        )
        return S.Overview(
            date=date,
            kpis=S.Kpis(
                boardings=int(sum(series)),
                busiest_hour=busiest,
                transfers=int(
                    self._scalar(
                        "SELECT coalesce(sum(transfers), 0) FROM fact_transfer WHERE date = ?",
                        [date],
                    )
                ),
                alerts=int(
                    self._scalar(
                        "SELECT count(*) FROM fact_anomaly WHERE date = ?", [date]
                    )
                ),
            ),
            operator_share=[
                S.OperatorShare(
                    operator=o,
                    boardings=int(share_rows.get(o, 0)),
                    share=round(share_rows.get(o, 0) / tot, 4),
                )
                for o in R.OPERATOR_IDS
            ],
            network_hourly=[
                S.HourValue(hour=h, boardings=round(v, 1))
                for h, v in zip(R.HOURS, series)
            ],
            top_interchanges=[
                S.InterchangeRef(stop_id=r[0], name=r[1], transfers=int(r[2]))
                for r in top
            ],
        )

    # ------------------------------------------------------------ map layers
    def hex(self, date: str, hour: int, ops: List[str], segment: str) -> S.HexResponse:
        rows = self._q(
            f"""SELECT s.h3, sum(f.boardings) b, sum(f.expected) e
                           FROM fact_stop_hour f JOIN dim_stop s USING (stop_id)
                           WHERE f.date = ? AND f.hour = ? AND f.operator IN {_in(ops)}
                             AND f.segment IN {_in(_segments(segment))}
                           GROUP BY s.h3 HAVING sum(f.expected) >= 1 ORDER BY b DESC""",
            [date, hour],
        )
        return S.HexResponse(
            date=date,
            hour=hour,
            resolution=R.HEX_RESOLUTION,
            cells=[
                S.HexCell(
                    h3=r[0],
                    boardings=round(r[1], 1),
                    expected=round(r[2], 1),
                    ratio=round(r[1] / r[2], 3),
                )
                for r in rows
            ],
        )

    def stops(
        self, date: str, hour: int, ops: List[str], segment: str
    ) -> S.StopsResponse:
        rows = self._q(
            f"""SELECT s.stop_id, s.name, s.lat, s.lon, s.operators, sum(f.boardings), sum(f.expected)
                           FROM fact_stop_hour f JOIN dim_stop s USING (stop_id)
                           WHERE f.date = ? AND f.hour = ? AND f.operator IN {_in(ops)}
                             AND f.segment IN {_in(_segments(segment))}
                           GROUP BY ALL ORDER BY s.stop_id""",
            [date, hour],
        )
        return S.StopsResponse(
            date=date,
            hour=hour,
            stops=[
                S.StopPoint(
                    stop_id=r[0],
                    name=r[1],
                    lat=r[2],
                    lon=r[3],
                    operators=list(r[4]),
                    boardings=round(r[5], 1),
                    expected=round(r[6], 1),
                    ratio=round(r[5] / r[6], 3) if r[6] else 1.0,
                )
                for r in rows
            ],
        )

    def stop_detail(
        self, stop_id: str, date: str, hour: int, ops: List[str], segment: str
    ) -> Optional[S.StopDetail]:
        info = self._q(
            """SELECT stop_id, name, lat, lon, operators, operator_stop_ids, shelter, step_free,
                                 realtime_display, wheelchair_boarding FROM dim_stop WHERE stop_id = ?""",
            [stop_id],
        )
        if not info:
            return None
        s = info[0]
        filt = f"stop_id = ? AND operator IN {_in(ops)} AND segment IN {_in(_segments(segment))}"
        cells = {
            (r[0], r[1]): (r[2], r[3])
            for r in self._q(
                f"SELECT date, hour, sum(boardings), sum(expected) FROM fact_stop_hour WHERE {filt} GROUP BY date, hour",
                [stop_id],
            )
        }
        mix_rows = dict(
            self._q(
                "SELECT segment, sum(boardings) FROM fact_stop_hour WHERE stop_id = ? AND date = ? GROUP BY segment",
                [stop_id, date],
            )
        )
        mix_tot = sum(mix_rows.values()) or 1
        b, e = cells.get((date, hour), (0.0, 0.0))
        # Waits only from operator pairs large enough to show; tiny hubs show no transfer summary
        tr = self._q(
            """SELECT sum(transfers), max(median_wait_min) FILTER (WHERE transfers >= ?)
               FROM fact_transfer WHERE stop_id = ? AND date = ?""",
            [PRIVACY_MIN, stop_id, date],
        )
        return S.StopDetail(
            stop_id=s[0],
            name=s[1],
            lat=s[2],
            lon=s[3],
            operators=list(s[4]),
            operator_stop_ids=json.loads(s[5]),
            date=date,
            hour=hour,
            now=S.NowValue(
                boardings=round(b, 1),
                expected=round(e, 1),
                deviation_pct=round((b / e - 1) * 100, 1) if e else 0.0,
            ),
            hourly=[
                S.HourObsExp(
                    hour=h,
                    boardings=round(cells.get((date, h), (0, 0))[0], 1),
                    expected=round(cells.get((date, h), (0, 0))[1], 1),
                )
                for h in R.HOURS
            ],
            week_grid=[
                S.WeekRow(
                    date=d["date"],
                    weekday=d["weekday"],
                    hourly=[
                        round(cells.get((d["date"], h), (0, 0))[0], 1) for h in R.HOURS
                    ],
                )
                for d in R.DAYS
            ],
            mix={k: round(mix_rows.get(k, 0) / mix_tot, 4) for k in ALL_SEGMENTS},
            facilities=S.Facilities(
                shelter=bool(s[6]),
                step_free=bool(s[7]),
                realtime_display=bool(s[8]),
                wheelchair_boarding=bool(s[9]),
            ),
            transfers_here=(
                S.TransfersHere(
                    transfers=int(tr[0][0]), worst_median_wait_min=int(tr[0][1])
                )
                if tr and tr[0][0] is not None and tr[0][0] >= PRIVACY_MIN and tr[0][1] is not None
                else None
            ),
        )

    # ----------------------------------------------------------------- lines
    def line_profile(self, line_id: str, date: str) -> Optional[S.LineProfile]:
        L = self._q(
            """SELECT line_id, label, name, mode, operator, seats, standing, capacity_source, shape,
                              whatif_to, whatif_from FROM dim_line WHERE line_id = ?""",
            [line_id],
        )
        if not L:
            return None
        L = L[0]
        places = L[5] + L[6]
        data = {
            r[0]: r[1:]
            for r in self._q(
                "SELECT hour, boardings, est_peak_load, trips, places_offered FROM fact_line_hour WHERE line_id = ? AND date = ?",
                [line_id, date],
            )
        }
        rows = []
        for h in R.HOURS:
            b, est, trips, offered = data.get(h, (0.0, 0.0, 0, None))
            # real places offered when the pipeline knows each trip's vehicle, else trips x typical vehicle
            offered = int(offered) if offered is not None else int(trips) * places
            rows.append(
                S.LineHour(
                    hour=h,
                    boardings=round(b, 1),
                    est_peak_load=round(est, 1),
                    trips=int(trips),
                    places_offered=offered,
                    load_factor=round(est / offered, 3) if offered else 0.0,
                )
            )
        peak = max(rows, key=lambda r: r.load_factor)
        return S.LineProfile(
            line_id=L[0],
            label=L[1],
            name=L[2],
            mode=L[3],
            operator=L[4],
            vehicle=S.Vehicle(seats=L[5], standing=L[6], places=places, source=L[7]),
            shape=json.loads(L[8]),
            date=date,
            hours=rows,
            peak=S.PeakRef(hour=peak.hour, load_factor=peak.load_factor),
            whatif=S.WhatIf(
                move_to_hours=json.loads(L[9]) if L[9] else [],
                move_from_hours=json.loads(L[10]) if L[10] else [],
                note="Computed in the browser: for N moved trips, trips[to[i]] += 1 and trips[from[i]] -= 1 "
                "for i < N; each moved trip adds/removes vehicle.places to places_offered; "
                "load_factor = est_peak_load / places_offered.",
            ),
        )

    # ------------------------------------------------------------- transfers
    def transfers(self, date: str) -> S.TransfersResponse:
        pairs = self._q(
            """SELECT t.stop_id, s.name, s.lat, s.lon, t.from_operator, t.to_operator, t.transfers,
                                  t.median_wait_min, t.p90_wait_min
                           FROM fact_transfer t JOIN dim_stop s USING (stop_id) WHERE t.date = ?""",
            [date],
        )
        hourly = {}
        for st, h, n in self._q(
            "SELECT stop_id, hour, transfers FROM fact_transfer_hour WHERE date = ?",
            [date],
        ):
            hourly.setdefault(st, {})[h] = n
        by_stop = {}
        for r in pairs:
            by_stop.setdefault(
                r[0], {"name": r[1], "lat": r[2], "lon": r[3], "pairs": []}
            )["pairs"].append(r[4:])
        inter = []
        for st, d in by_stop.items():
            total = sum(p[2] for p in d["pairs"])
            if total < PRIVACY_MIN:
                continue  # too few transfers to show this hub at all
            # Operator pairs under the threshold are counted in the total but never listed
            listed = [p for p in d["pairs"] if p[2] >= PRIVACY_MIN]
            # "Fragile" = a connection that matters (>= 5% of the hub's transfers, >= 50/day)
            # where the typical passenger waits 12+ minutes. Tiny pairs are ignored.
            big = [p for p in listed if p[2] >= max(50, 0.05 * total)] or listed
            worst = max((p[3] for p in big), default=0)
            inter.append(
                S.Interchange(
                    stop_id=st,
                    name=d["name"],
                    lat=d["lat"],
                    lon=d["lon"],
                    transfers=int(total),
                    worst_median_wait_min=int(worst),
                    fragile=worst >= 12,
                    transfers_below_privacy=int(total - sum(p[2] for p in listed)),
                    pairs=[
                        S.TransferPair(
                            from_operator=p[0],
                            to_operator=p[1],
                            transfers=int(p[2]),
                            median_wait_min=int(p[3]),
                            p90_wait_min=int(p[4]),
                        )
                        for p in listed
                    ],
                    hourly=[
                        S.HourCount(hour=h, transfers=shown(round(hourly.get(st, {}).get(h, 0.0), 1)))
                        for h in R.HOURS
                    ],
                )
            )
        inter.sort(key=lambda x: -x.transfers)
        inter = inter[
            :20
        ]  # the list and map show the 20 busiest interchanges; KPIs use all
        cols = {r[0] for r in self._q("DESCRIBE fact_flow")}
        extra = "f.via, f.modes, f.via_share" if "via" in cols else "NULL, NULL, NULL"
        flows = self._q(
            f"""SELECT f.from_stop_id, a.name, a.lon, a.lat, f.to_stop_id, b.name, b.lon, b.lat, f.journeys, {extra}
                FROM fact_flow f JOIN dim_stop a ON a.stop_id = f.from_stop_id
                JOIN dim_stop b ON b.stop_id = f.to_stop_id
                WHERE f.date = ? ORDER BY f.journeys DESC LIMIT 50""",
            [date],
        )
        via_ids = {v for r in flows if r[9] for v in json.loads(r[9])}
        places = self._places(via_ids)
        return S.TransfersResponse(
            date=date,
            interchanges=inter,
            flows=[
                S.Flow(
                    from_stop_id=r[0], from_name=r[1], from_lon=r[2], from_lat=r[3],
                    to_stop_id=r[4], to_name=r[5], to_lon=r[6], to_lat=r[7], journeys=int(r[8]),
                    via=[places[v] for v in (json.loads(r[9]) if r[9] else []) if v in places],
                    modes=json.loads(r[10]) if r[10] else [],
                    via_share=r[11],
                )
                for r in flows
            ],
            method="A transfer = two entry taps by the same card within 60 minutes on different operators, "
            "where the first operator stops within 400 m of the hub. Wait = minutes from the first leg's "
            "arrival (Metro exit tap, or the ferry/train timetable, or distance ÷ typical speed for buses) "
            "to the next tap. Fragile = a connection with 5%+ of the hub's transfers and a median wait of 12+ min. "
            f"Operator pairs, hours and hubs with fewer than {PRIVACY_MIN} transfers are counted in totals but not shown.",
        )

    def _places(self, ids) -> dict:
        ids = [i for i in ids if i]
        if not ids:
            return {}
        rows = self._q(f"SELECT stop_id, name, lat, lon FROM dim_stop WHERE stop_id IN {_in(ids)}")
        return {r[0]: S.Place(stop_id=r[0], name=r[1], lat=r[2], lon=r[3]) for r in rows}

    # ---------------------------------------------------------------- golden
    def golden(self) -> S.GoldenResponse:
        tables = {r[0] for r in self._q("SELECT table_name FROM information_schema.tables")}
        if "golden_route" not in tables:
            return S.GoldenResponse(routes=[], method=GOLDEN_METHOD, assumptions={})
        rows = self._q("""SELECT route_id, rank, from_stop_id, to_stop_id, distance_km, multi_per_day, direct_per_day,
                                 multi_share, avg_legs, current_min, projected_min, saved_min, riders_per_day,
                                 person_hours_per_day, peak_hour, peak_riders, trips_needed_peak, share_a_to_b, spike_z,
                                 verdict, flags, hourly, paths, replaced, hubs, direct_lines
                          FROM golden_route ORDER BY rank""")
        ids = set()
        for r in rows:
            ids |= {r[2], r[3]}
            ids |= {v for p in json.loads(r[22] or "[]") for v in p["via"]}
            ids |= {h["stop_id"] for h in json.loads(r[24] or "[]")}
        pl = self._places(ids)
        out = []
        for r in rows:
            paths = json.loads(r[22] or "[]")
            replaced = json.loads(r[23] or "[]")
            share_a_to_b = r[17]
            derived = _golden_derived(r[5], share_a_to_b, r[16], r[18])
            out.append(S.GoldenRoute(
                route_id=r[0], rank=r[1], from_=pl[r[2]], to=pl[r[3]], distance_km=r[4], multi_per_day=r[5],
                direct_per_day=r[6], multi_share=r[7], avg_legs=r[8], current_min=r[9], projected_min=r[10],
                saved_min=r[11], riders_per_day=r[12], person_hours_per_day=r[13], peak_hour=r[14], peak_riders=r[15],
                trips_needed_peak=r[16], share_a_to_b=share_a_to_b, spike_z=r[18],
                shown_paths_share=round(sum(p["share"] for p in paths), 4), verdict=r[19],
                **derived,
                flags=[S.GoldenFlag(**f) for f in json.loads(r[20] or "[]")],
                hourly=[S.GoldenHour(**h) for h in json.loads(r[21] or "[]")],
                paths=[S.GoldenPath(legs=[S.GoldenLeg(**l) for l in p["legs"]],
                                    via=[pl[v] for v in p["via"] if v in pl],
                                    journeys_per_day=p["journeys_per_day"], share=p["share"]) for p in paths],
                replaced=[S.GoldenReplaced(**{
                    **x,
                    "frequency_review_recommended": (x.get("share_of_line") or 0) >= 0.15,
                }) for x in replaced],
                hubs=[S.GoldenHub(**{**h, "name": pl[h["stop_id"]].name, "lat": pl[h["stop_id"]].lat,
                                     "lon": pl[h["stop_id"]].lon}) for h in json.loads(r[24] or "[]") if h["stop_id"] in pl],
                direct_lines=[S.GoldenDirect(**d) for d in json.loads(r[25] or "[]")],
            ))
        meta = dict(self._q("SELECT k, v FROM golden_meta")) if "golden_meta" in tables else {}
        return S.GoldenResponse(routes=out, method=GOLDEN_METHOD, assumptions=meta)

    # ------------------------------------------------------------- anomalies
    def anomalies(self, date: Optional[str]) -> S.AnomaliesResponse:
        where, params = ("WHERE a.date = ?", [date]) if date else ("", [])
        rows = self._q(
            f"""SELECT a.alert_id, a.stop_id, s.name, s.lat, s.lon, a.date, a.hour, a.observed, a.expected,
                                  a.deviation_pct, a.robust_z
                           FROM fact_anomaly a JOIN dim_stop s USING (stop_id) {where}
                           ORDER BY abs(a.robust_z) DESC""",
            params,
        )
        out = []
        for r in rows:
            series = {
                h: (b, e)
                for h, b, e in self._q(
                    "SELECT hour, sum(boardings), sum(expected) FROM fact_stop_hour WHERE stop_id = ? AND date = ? GROUP BY hour",
                    [r[1], r[5]],
                )
            }
            out.append(
                S.Alert(
                    alert_id=r[0],
                    stop_id=r[1],
                    name=r[2],
                    lat=r[3],
                    lon=r[4],
                    date=r[5],
                    hour=r[6],
                    observed=round(r[7], 1),
                    expected=round(r[8], 1),
                    deviation_pct=round(r[9], 1),
                    robust_z=round(r[10], 2),
                    direction="above" if r[7] >= r[8] else "below",
                    hourly=[
                        S.HourObsExp2(
                            hour=h,
                            observed=round(series.get(h, (0, 0))[0], 1),
                            expected=round(series.get(h, (0, 0))[1], 1),
                        )
                        for h in R.HOURS
                    ],
                )
            )
        return S.AnomaliesResponse(
            alerts=out,
            method="Expected = median of the same stop and hour on the other weekdays. "
            "Flag when the robust z-score (median absolute deviation) exceeds 3.5.",
        )


    # ---------------------------------------------------------------- places
    def places_by_id(self, ids) -> dict:
        return self._places(set(ids))

    def search_places(self, query: str, limit: int) -> S.PlacesResponse:
        """Hubs whose name contains the query, exact and prefix matches first, then busiest."""
        if not hasattr(self, "_volume"):
            self._volume = dict(self._q("SELECT stop_id, sum(boardings) FROM fact_stop_hour GROUP BY stop_id"))
        norm = query.strip().lower()
        rows = self._q(
            """SELECT stop_id, name, lat, lon, operators,
                      CASE WHEN lower(strip_accents(name)) = strip_accents(?) THEN 0
                           WHEN lower(strip_accents(name)) LIKE strip_accents(?) THEN 1 ELSE 2 END AS rank_
               FROM dim_stop
               WHERE lower(strip_accents(name)) LIKE strip_accents(?) OR stop_id = ?""",
            [norm, norm + "%", "%" + norm + "%", query.strip()],
        )
        rows.sort(key=lambda r: (r[5], -self._volume.get(r[0], 0), r[1]))
        return S.PlacesResponse(
            query=query,
            places=[S.PlaceMatch(stop_id=r[0], name=r[1], lat=r[2], lon=r[3], operators=r[4]) for r in rows[:limit]],
        )

    # -------------------------------------------------------------- compare
    def compare(self, measure: str, subject: Optional[str], date: str, hour: int,
                ops: List[str], segment: str) -> Optional[S.CompareResponse]:
        """Hour vs typical for a warehouse measure. Returns None for an unknown subject."""
        from . import baseline as B

        days = [date] + B.comparable_days(date)
        day_sql = f"date IN {_in(days)}"
        if measure == "network_boardings":
            name = None
            sql = (f"SELECT date, hour, sum(boardings) FROM fact_stop_hour WHERE {day_sql} "
                   f"AND operator IN {_in(ops)} AND segment IN {_in(_segments(segment))} GROUP BY 1, 2")
            params = []
        elif measure in ("stop_boardings", "transfers"):
            name = self._scalar("SELECT name FROM dim_stop WHERE stop_id = ?", [subject])
            if measure == "stop_boardings":
                sql = (f"SELECT date, hour, sum(boardings) FROM fact_stop_hour WHERE {day_sql} AND stop_id = ? "
                       f"AND operator IN {_in(ops)} AND segment IN {_in(_segments(segment))} GROUP BY 1, 2")
            else:
                sql = f"SELECT date, hour, sum(transfers) FROM fact_transfer_hour WHERE {day_sql} AND stop_id = ? GROUP BY 1, 2"
            params = [subject]
        else:  # line_boardings
            name = self._scalar("SELECT label || ' ' || name FROM dim_line WHERE line_id = ?", [subject])
            sql = f"SELECT date, hour, sum(boardings) FROM fact_line_hour WHERE {day_sql} AND line_id = ? GROUP BY 1, 2"
            params = [subject]
        if measure != "network_boardings" and name is None:
            return None
        values = {(d, h): float(v) for d, h, v in self._q(sql, params)}
        # The warehouse covers every service hour of the dataset week
        # Transfers link a card's taps, so they get the privacy threshold; boardings do not
        comparison = B.build_comparison(values, date, [hour], lambda d, h: True,
                                        PRIVACY_MIN if measure == "transfers" else 0)
        return S.CompareResponse(
            measure=measure, subject=S.CompareSubject(id=subject, name=name) if name else None,
            date=date, hour=hour, comparison=comparison,
        )

@lru_cache(maxsize=1)
def get_duckdb_provider(path: str) -> DuckDBProvider:
    return DuckDBProvider(path)
