# Carrolinha API

FastAPI backend for the Carrolinha mobility explorer (Hack the City 2026, challenge #1). It serves aggregated TML validation data for 31 Aug–6 Sep 2026 from DuckDB files; it never reads raw card data at request time.

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # or: uv sync
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Interactive docs: <http://localhost:8000/docs>

## Data files

| File | In git | Built by | Contents |
|---|---|---|---|
| `warehouse.duckdb` | yes | the team's warehouse pipeline (not in this repo yet) | Tables in `sql/schema.sql`: stop, line and transfer aggregates, anomalies, golden routes |
| `journeys.duckdb` | **no** | `pipeline/build_journeys.py` | Journey paths with exact counts, including single-card paths. Server-side only |

Build the journey table from the raw validation CSVs (the warehouse must exist, because it maps raw stop ids to hubs):

```bash
.venv/bin/python pipeline/build_journeys.py "/path/to/validations_part_*.csv"
```

It records which service hours the input files fully cover (`journey_coverage`). Comparisons only use fully covered hours, so building from a partial set of files gives correct but fewer comparisons.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `PULSO_DB` | `warehouse.duckdb` | Warehouse path |
| `CARROLINHA_JOURNEYS` | `journeys.duckdb` | Journey table path; `/api/journey-traffic` returns 503 when it is missing |
| `CARROLINHA_PRIVACY_MIN` | `10` | Smallest journey count ever returned for a path, total or baseline |

Secrets go in `.env`, which is git-ignored. Never put them in `render.yaml`.

## Endpoints

The explorer endpoints (`/api/meta`, `/api/overview`, `/api/hex`, `/api/stops`, `/api/lines/{id}/profile`, `/api/transfers`, `/api/anomalies`, `/api/golden`) are documented at `/docs`. Newer endpoints:

- `GET /api/places?q=` resolves a place name to hub `stop_id`s (accent-insensitive).
- `GET /api/journey-traffic` returns journeys along a directed path (`origin`, `through`, `destination`, `any`, `match=contains|exact`), filtered by `day`, optional `hour` and `min_volume`. It returns paginated paths, a Sankey built from every shown path, totals (matched, shown, below minimum volume, below the privacy threshold) and a comparison with typical.
- `GET /api/compare?measure=` returns this hour versus typical for `stop_boardings`, `network_boardings`, `transfers` or `line_boardings`.

### Definitions

- **Journey**: one card's taps chained until a gap of more than 60 minutes; split when the card re-boards the same line, re-enters the Metro after exiting, or boards again at its own origin.
- **Path**: the hubs where the journey tapped, in order, then its destination. It is not the physical route: Metro line changes and stops passed without tapping are invisible.
- **Destination**: the Metro exit (observed), or where the card starts its next journey that day (inferred); otherwise unknown, and the path ends at the last boarding.
- **Typical**: median of the same service hour on the other days of the same type (weekdays with weekdays, weekend with weekend), excluding the selected day, with at least 2 such days (`app/baseline.py`). With one week of data a weekend day has only one comparison day, so weekend comparisons report `insufficient_baseline`.
- **Privacy threshold**: paths under `CARROLINHA_PRIVACY_MIN` journeys are counted in totals but never listed; totals and baselines under it are returned as `null`.

## Tests

```bash
.venv/bin/python -m unittest tests.test_journeys tests.test_golden
```

`tests/test_api.py` predates the move to real data and still expects mock station data in `app/reference.py`; it needs a new fixture.
