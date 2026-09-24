"""Journey traffic along directed paths.

Reads journeys.duckdb, built by pipeline/build_journeys.py. That file holds exact
counts, so nothing leaves this module below PRIVACY_MIN: such paths are counted
in the totals but never listed, and any total or baseline under the threshold
is returned as null.
"""

import os
import threading
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Dict, List, Optional

import duckdb

from . import baseline as B
from . import reference as R
from . import schemas as S

PRIVACY_MIN = int(os.environ.get("CARROLINHA_PRIVACY_MIN", "10"))
SANKEY_NODES_PER_LAYER = 20
SANKEY_MIDDLE_LAYERS = 3
UNKNOWN = "__unknown__"
MORE = "__more__"

METHOD = (
    "A journey = one card's taps chained until a gap of more than 60 minutes (split when the card re-boards "
    "the same line, re-enters the Metro after exiting, or boards again at its own origin). A path = the hubs "
    "where it tapped, in order: boardings and Metro exits, then the destination. Destination = the Metro exit "
    "(observed) or where the card starts its next journey that day (inferred); otherwise unknown. Journeys are "
    "counted in the service hour of their first boarding. Paths match in the given direction only."
)

LIMITATIONS = [
    "A path is the sequence of tap locations, not the physical route: Metro line changes and stops passed "
    "without tapping are not recorded.",
    "When the destination is unknown, the path ends at the last boarding, which is not a destination.",
    f"Paths with fewer than {PRIVACY_MIN} journeys are counted in the totals but never listed.",
]


class JourneysUnavailable(Exception):
    pass


def _like(stop_id: str) -> str:
    return stop_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _chain(path) -> str:
    return "|" + "|".join(path) + "|"


def _key(path, known: bool) -> str:
    return ">".join(path) + (":known" if known else ":unknown")


class JourneyStore:
    def __init__(self, path: str):
        if not os.path.exists(path):
            raise JourneysUnavailable(
                f"{path} has not been built. Run: python pipeline/build_journeys.py <validations_glob>"
            )
        self.con = duckdb.connect(path, read_only=True)
        self.lock = threading.Lock()
        self.complete = {
            (d, h) for d, h in self._q("SELECT date, hour FROM journey_coverage WHERE complete")
        }
        self.meta = dict(self._q("SELECT k, v FROM journey_meta"))

    def _q(self, sql: str, params: Optional[list] = None):
        with self.lock:
            return self.con.execute(sql, params or []).fetchall()

    def is_complete(self, date: str, hour: int) -> bool:
        return (date, hour) in self.complete

    def complete_hours(self, date: str) -> List[int]:
        return [h for h in R.HOURS if (date, h) in self.complete]

    # ------------------------------------------------------------ filters
    @staticmethod
    def _path_filter(origin, through, destination, anywhere, match):
        clauses, params = [], []
        if match == "exact":
            clauses.append("chain = ? AND destination_evidence <> 'unknown'")
            params.append(_chain([s for s in [origin, *through, destination] if s]))
        else:
            if origin:
                clauses.append("chain LIKE ? ESCAPE '\\'")
                params.append(f"|{_like(origin)}|%")
            if through:
                clauses.append("chain LIKE ? ESCAPE '\\'")
                params.append("%|" + "|".join(_like(s) for s in through) + "|%")
            if destination:
                clauses.append("chain LIKE ? ESCAPE '\\' AND destination_evidence <> 'unknown'")
                params.append(f"%|{_like(destination)}|")
        if anywhere:
            clauses.append("(" + " OR ".join("chain LIKE ? ESCAPE '\\'" for _ in anywhere) + ")")
            params += [f"%|{_like(s)}|%" for s in anywhere]
        return " AND ".join(clauses) or "TRUE", params

    # ------------------------------------------------------------ traffic
    def traffic(self, *, day, hour, origin, through, destination, anywhere, match, min_volume,
                compare, limit, offset, places: Dict[str, S.Place]) -> S.JourneyTrafficResponse:
        where, params = self._path_filter(origin, through, destination, anywhere, match)
        hour_sql = "AND hour = ?" if hour is not None else ""
        scope = [day] + ([hour] if hour is not None else [])
        grouped = f"""
            WITH m AS (
                SELECT path, destination_evidence <> 'unknown' AS known, operators, sum(journeys) AS j,
                       sum(journeys) FILTER (WHERE destination_evidence = 'metro_exit') AS mx
                FROM fact_journey_path WHERE date = ? {hour_sql} AND {where} GROUP BY ALL),
            g AS (
                SELECT path, known, sum(j) AS j, sum(mx) AS mx, arg_max(operators, j) AS ops, max(j) AS ops_j
                FROM m GROUP BY path, known)"""
        threshold = max(PRIVACY_MIN, min_volume)

        matched, below_privacy, below_min, shown_paths, shown_volume = self._q(
            f"""{grouped}
            SELECT coalesce(sum(j), 0), coalesce(sum(j) FILTER (WHERE j < ?), 0),
                   coalesce(sum(j) FILTER (WHERE j >= ? AND j < ?), 0),
                   count(*) FILTER (WHERE j >= ?), coalesce(sum(j) FILTER (WHERE j >= ?), 0)
            FROM g""",
            scope + params + [PRIVACY_MIN, PRIVACY_MIN, min_volume, threshold, threshold],
        )[0]
        shown = self._q(
            f"{grouped} SELECT path, known, j, mx, ops, ops_j FROM g WHERE j >= ? ORDER BY j DESC, path",
            scope + params + [threshold],
        )

        # Names for every stop on a shown path (filters were validated by the caller)
        names = dict(places)
        missing = {s for row in shown for s in row[0]} - set(names)
        names.update(self._names(missing))

        page = shown[offset:offset + limit]
        per_path_typical = self._path_typical(day, hour, page) if compare else {}
        paths = []
        for rank, (path, known, j, mx, ops, ops_j) in enumerate(page, start=offset + 1):
            key = _key(path, known)
            typ = per_path_typical.get(key)
            typ = None if typ is None or typ < PRIVACY_MIN else typ
            paths.append(S.JourneyPath(
                key=key, rank=rank, path=[names[s] for s in path if s in names],
                destination="known" if known else "unknown",
                metro_exit_share=round((mx or 0) / j, 2) if known else None,
                modes=list(ops), modes_share=round(ops_j / j, 2), journeys=int(j),
                share_pct=round(100 * j / shown_volume, 1) if shown_volume else 0.0,
                typical=typ, difference_pct=B.difference(j, typ)[1],
            ))

        visible_matched = int(matched) if matched >= PRIVACY_MIN else None
        comparison = self._comparison(day, hour, where, params) if compare else None
        coverage_ok = self.is_complete(day, hour) if hour is not None else bool(self.complete_hours(day))
        limitations = list(LIMITATIONS)
        if not coverage_ok:
            limitations.append("The selected period is not fully covered by the source validation files, so counts are incomplete.")
        if visible_matched is None and matched:
            limitations.append(f"Fewer than {PRIVACY_MIN} journeys match these filters, so no numbers are shown.")

        return S.JourneyTrafficResponse(
            applied_filters=S.JourneyFilters(
                day=day, hour=hour, origin=places.get(origin), through=[places[s] for s in through],
                destination=places.get(destination), any=[places[s] for s in anywhere], match=match,
                min_volume=min_volume,
                human_summary=self._summary(day, hour, origin, through, destination, anywhere, match, min_volume, places),
                logic="Origin, through and destination form one directed path (A→B is not B→A); "
                      "'any' matches journeys touching any of those stops; all filters combine with AND.",
            ),
            totals=S.JourneyTotals(
                matched_volume=visible_matched, shown_volume=int(shown_volume), shown_paths=int(shown_paths),
                below_min_volume=int(below_min), below_privacy_threshold=int(below_privacy),
            ),
            paths=paths, offset=offset, limit=limit,
            sankey=self._sankey(shown, names),
            comparison=comparison,
            coverage=S.JourneyCoverage(
                complete=coverage_ok, complete_hours=self.complete_hours(day),
                source=f"Built from {int(self.meta.get('taps', 0)):,} validation taps "
                       f"({int(self.meta.get('journeys', 0)):,} journeys); hours without full coverage are flagged.",
            ),
            privacy_min=PRIVACY_MIN, method=METHOD, limitations=limitations,
        )

    def _names(self, ids) -> Dict[str, S.Place]:
        if not ids:
            return {}
        warehouse = os.environ.get("PULSO_DB", "warehouse.duckdb")
        from .duckdb_provider import get_duckdb_provider  # lazy: avoids a circular import

        return get_duckdb_provider(warehouse).places_by_id(ids)

    # ----------------------------------------------------------- baselines
    def _day_hour_totals(self, days, where, params) -> Dict[tuple, float]:
        rows = self._q(
            f"""SELECT date, hour, sum(journeys) FROM fact_journey_path
                WHERE date IN ({",".join("?" for _ in days)}) AND {where} GROUP BY date, hour""",
            list(days) + params,
        )
        return {(d, h): float(n) for d, h, n in rows}

    def _comparison(self, day, hour, where, params) -> S.Comparison:
        days = [day] + B.comparable_days(day)
        values = self._day_hour_totals(days, where, params)
        hours = [hour] if hour is not None else self.complete_hours(day)
        return B.build_comparison(values, day, hours, self.is_complete, PRIVACY_MIN)

    def _path_typical(self, day, hour, page) -> Dict[str, Optional[float]]:
        """Typical volume of each listed path, from the same baseline days as the headline comparison."""
        if not page:
            return {}
        hours = [hour] if hour is not None else self.complete_hours(day)
        if not hours or not all(self.is_complete(day, h) for h in hours):
            return {}
        base_days = [d for d in B.comparable_days(day) if all(self.is_complete(d, h) for h in hours)]
        if len(base_days) < B.MIN_BASELINE_DAYS:
            return {}
        chains = [_chain(row[0]) for row in page]
        rows = self._q(
            f"""SELECT date, path, destination_evidence <> 'unknown', sum(journeys) FROM fact_journey_path
                WHERE date IN ({",".join("?" for _ in base_days)}) AND hour IN ({",".join("?" for _ in hours)})
                  AND chain IN ({",".join("?" for _ in chains)}) GROUP BY ALL""",
            base_days + hours + chains,
        )
        per_key = defaultdict(dict)
        for d, path, known, n in rows:
            per_key[_key(path, known)][d] = float(n)
        return {_key(row[0], row[1]): B.typical(per_key.get(_key(row[0], row[1]), {}), base_days) for row in page}

    # --------------------------------------------------------------- sankey
    @staticmethod
    def _sankey(shown, names) -> S.JourneySankey:
        sequences = []
        for path, known, j, *_ in shown:
            seq = list(path) + ([] if known else [UNKNOWN])
            middle = seq[1:-1] if len(seq) > 1 else []
            if len(middle) > SANKEY_MIDDLE_LAYERS:
                middle = middle[:SANKEY_MIDDLE_LAYERS - 1] + [MORE]
            sequences.append((seq[0], middle, seq[-1], int(j)))
        depth = max((len(m) for _, m, _, _ in sequences), default=0)
        last = depth + 1

        # Keep the busiest stops of each layer; the rest share one visible "Other" node
        totals = Counter()
        for origin, middle, dest, j in sequences:
            totals[(0, origin)] += j
            for i, stop in enumerate(middle):
                totals[(i + 1, stop)] += j
            totals[(last, dest)] += j
        keep, others = set(), Counter()
        for layer in range(last + 1):
            ranked = sorted(((n, s) for (l, s), n in totals.items() if l == layer), reverse=True)
            for index, (_, stop) in enumerate(ranked):
                if index < SANKEY_NODES_PER_LAYER or stop in (UNKNOWN, MORE):
                    keep.add((layer, stop))
                else:
                    others[layer] += 1

        nodes, index_of, links = [], {}, Counter()

        def node(layer, stop):
            key = (layer, stop) if (layer, stop) in keep else (layer, None)
            if key not in index_of:
                if key[1] is None:
                    name, kind, stop_id = f"Other ({others[layer]} stops)", "other", None
                elif stop == UNKNOWN:
                    name, kind, stop_id = "Unknown destination", "unknown", None
                elif stop == MORE:
                    name, kind, stop_id = "More stops", "more", None
                else:
                    place = names.get(stop)
                    name, kind, stop_id = (place.name if place else stop), "stop", stop
                index_of[key] = len(nodes)
                nodes.append(S.SankeyNode(id=f"{layer}:{key[1] or 'other'}", name=name, layer=layer,
                                          kind=kind, stop_id=stop_id, value=0))
            return index_of[key]

        for origin, middle, dest, j in sequences:
            chain = [node(0, origin)] + [node(i + 1, s) for i, s in enumerate(middle)] + [node(last, dest)]
            for a, b in zip(chain, chain[1:]):
                links[(a, b)] += j
            for n in set(chain):
                nodes[n].value += j
        layers = ["Origin"] + [f"Via {i}" for i in range(1, depth + 1)] + ["Destination"]
        return S.JourneySankey(
            layers=layers, nodes=nodes,
            links=[S.SankeyLink(source=a, target=b, value=v) for (a, b), v in links.items()],
        )

    @staticmethod
    def _summary(day, hour, origin, through, destination, anywhere, match, min_volume, places) -> str:
        label = next(d["label"] for d in R.DAYS if d["date"] == day)
        parts = [label + (f" {('00' if hour == 24 else f'{hour:02d}')}:00" if hour is not None else " (whole day)")]
        path = []
        if origin:
            path.append(f"from {places[origin].name}")
        if through:
            path.append("through " + " → ".join(places[s].name for s in through))
        if destination:
            path.append(f"to {places[destination].name}")
        if path:
            parts.append(("exactly " if match == "exact" else "") + " ".join(path))
        if anywhere:
            parts.append("touching " + " or ".join(places[s].name for s in anywhere))
        if min_volume:
            parts.append(f"paths with ≥ {min_volume} journeys")
        return " · ".join(parts)


@lru_cache(maxsize=1)
def get_journey_store(path: str) -> JourneyStore:
    return JourneyStore(path)
