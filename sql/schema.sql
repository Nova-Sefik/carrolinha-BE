-- ===========================================================================
-- warehouse.duckdb — the tables the pipeline must produce.
--
-- This file is the contract between the data pipeline and the API.
-- The pipeline's ONLY job is to fill these tables from the raw files.
-- The API (app/duckdb_provider.py) only ever reads these tables.
--
-- Conventions
--   date      'YYYY-MM-DD' operational date (validations.operational_date)
--   hour      5..24 service hour; 24 = 00:00–00:59 after midnight
--   operator  UI group id: metro | carris | cm | rail | ferry | other
--             (mapping from agency_code in app/reference.py OPERATORS)
--   segment   regular | sub23 | senior  (from products_classification.csv)
--   stop_id   a HUB id: all operator stops within ~150 m of each other that
--             share a name are grouped into one hub, so a station served by
--             Metro, Carris and rail is one point on the map
-- ===========================================================================

CREATE TABLE dim_stop (
    stop_id             VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    lat                 DOUBLE  NOT NULL,
    lon                 DOUBLE  NOT NULL,
    h3                  VARCHAR NOT NULL,    -- h3 cell at resolution 8 containing (lat, lon)
    operators           VARCHAR[] NOT NULL,  -- UI operator ids serving the hub
    operator_stop_ids   VARCHAR NOT NULL,    -- JSON: {"metro": ["..."], "carris": ["..."]}
    shelter             BOOLEAN,
    step_free           BOOLEAN,
    realtime_display    BOOLEAN,
    wheelchair_boarding BOOLEAN
);

-- Boardings = ENTRY validations only (event_type 1, 3, 13).
-- expected = median of the same stop/hour/operator/segment on the other weekdays
-- (weekend days compare with the other weekend day).
CREATE TABLE fact_stop_hour (
    date      VARCHAR NOT NULL,
    hour      INTEGER NOT NULL,
    stop_id   VARCHAR NOT NULL,
    operator  VARCHAR NOT NULL,
    segment   VARCHAR NOT NULL,
    boardings DOUBLE  NOT NULL,
    expected  DOUBLE  NOT NULL
);

-- Routes with trip-level capacity (Carris Metropolitana via trip_id + blocks.txt;
-- ferries via vessel capacity).
CREATE TABLE dim_line (
    line_id         VARCHAR PRIMARY KEY,
    label           VARCHAR NOT NULL,
    name            VARCHAR NOT NULL,
    mode            VARCHAR NOT NULL,        -- bus | ferry
    operator        VARCHAR NOT NULL,
    seats           INTEGER NOT NULL,        -- blocks.available_seats
    standing        INTEGER NOT NULL,        -- blocks.available_standing
    capacity_source VARCHAR NOT NULL,
    shape           VARCHAR NOT NULL         -- JSON [[lon, lat], ...] from shapes.txt
);

CREATE TABLE fact_line_hour (
    date          VARCHAR NOT NULL,
    hour          INTEGER NOT NULL,
    line_id       VARCHAR NOT NULL,
    boardings     DOUBLE  NOT NULL,          -- entry validations on the line's trips
    est_peak_load DOUBLE  NOT NULL,          -- people on board at the busiest point (trip chaining)
    trips         INTEGER NOT NULL           -- trips run that hour (stop_times / vehicles)
);

-- Cross-operator transfers: consecutive taps by one card within 60 min on different operators.
CREATE TABLE fact_transfer (
    date            VARCHAR NOT NULL,
    stop_id         VARCHAR NOT NULL,        -- hub where the second tap happened
    from_operator   VARCHAR NOT NULL,
    to_operator     VARCHAR NOT NULL,
    transfers       INTEGER NOT NULL,
    median_wait_min INTEGER NOT NULL,
    p90_wait_min    INTEGER NOT NULL
);

CREATE TABLE fact_transfer_hour (
    date      VARCHAR NOT NULL,
    hour      INTEGER NOT NULL,
    stop_id   VARCHAR NOT NULL,
    transfers DOUBLE  NOT NULL
);

-- Most common origin -> destination journeys that include a transfer.
CREATE TABLE fact_flow (
    date         VARCHAR NOT NULL,
    from_stop_id VARCHAR NOT NULL,
    to_stop_id   VARCHAR NOT NULL,
    journeys     INTEGER NOT NULL
);

-- Stop-hours whose robust z-score (median absolute deviation) exceeds 3.5.
CREATE TABLE fact_anomaly (
    alert_id      VARCHAR PRIMARY KEY,
    date          VARCHAR NOT NULL,
    hour          INTEGER NOT NULL,
    stop_id       VARCHAR NOT NULL,
    observed      DOUBLE  NOT NULL,
    expected      DOUBLE  NOT NULL,
    deviation_pct DOUBLE  NOT NULL,
    robust_z      DOUBLE  NOT NULL
);
