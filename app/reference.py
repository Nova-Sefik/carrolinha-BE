"""
Reference data shared by every provider.

Operators, days, hours and segments are real facts about the dataset.
"""

# --------------------------------------------------------------------------
# Real: the dataset week and its conventions
# --------------------------------------------------------------------------

WEEK_START = "2026-08-31"
WEEK_END = "2026-09-06"

# Service hours shown in the UI. 24 means 00:00–00:59 after midnight, the
# GTFS convention for trips that run past midnight on the same service day.
HOURS = list(range(5, 25))

DAYS = [
    {
        "date": "2026-08-31",
        "label": "Mon 31 Aug",
        "weekday": "Mon",
        "is_weekend": False,
    },
    {"date": "2026-09-01", "label": "Tue 1 Sep", "weekday": "Tue", "is_weekend": False},
    {"date": "2026-09-02", "label": "Wed 2 Sep", "weekday": "Wed", "is_weekend": False},
    {"date": "2026-09-03", "label": "Thu 3 Sep", "weekday": "Thu", "is_weekend": False},
    {"date": "2026-09-04", "label": "Fri 4 Sep", "weekday": "Fri", "is_weekend": False},
    {"date": "2026-09-05", "label": "Sat 5 Sep", "weekday": "Sat", "is_weekend": True},
    {"date": "2026-09-06", "label": "Sun 6 Sep", "weekday": "Sun", "is_weekend": True},
]
DATES = [d["date"] for d in DAYS]

# UI operator groups -> agency_code values in validations / plans API.
# Codes confirmed from https://go.tmlmobilidade.pt/hub/api/v1/plans
OPERATORS = [
    {"id": "metro", "name": "Metro", "color": "#2a78d6", "agency_codes": ["2"]},
    {"id": "carris", "name": "Carris", "color": "#eb6834", "agency_codes": ["1"]},
    {
        "id": "cm",
        "name": "Carris Metropolitana",
        "color": "#1baf7a",
        "agency_codes": ["41", "42", "43", "44"],
    },
    {
        "id": "rail",
        "name": "Rail (CP, Fertagus)",
        "color": "#eda100",
        "agency_codes": ["3", "15"],
    },
    {
        "id": "ferry",
        "name": "Ferries (Transtejo)",
        "color": "#e87ba4",
        "agency_codes": ["4"],
    },
    {
        "id": "other",
        "name": "Other operators",
        "color": "#008300",
        "agency_codes": ["8", "16", "21"],
    },
]
OPERATOR_IDS = [o["id"] for o in OPERATORS]

# Passenger segments come from products_classification.csv (is_junior / is_senior).
SEGMENTS = [
    {"id": "all", "label": "All passengers"},
    {"id": "sub23", "label": "Sub-23"},
    {"id": "senior", "label": "65+"},
]
SEGMENT_IDS = [s["id"] for s in SEGMENTS]

# Entry validations. Metro exit taps (4, 14) are only used for origin–destination.
ENTRY_EVENT_TYPES = [1, 3, 13]
EXIT_EVENT_TYPES = [4, 14]

HEX_RESOLUTION = 8  # ~0.74 km² per cell

# --------------------------------------------------------------------------
# MOCK ONLY: invented numbers so the frontend can be built before the pipeline
# --------------------------------------------------------------------------

# id, name, lat, lon, typical weekday entries, operators, facilities
# facilities: shelter, step_free, realtime_display, wheelchair_boarding
STATIONS = [
    (
        "marques",
        "Marquês de Pombal",
        38.7253,
        -9.1500,
        42000,
        ["metro", "carris"],
        (1, 1, 1, 1),
    ),
    (
        "campogrande",
        "Campo Grande",
        38.7597,
        -9.1583,
        38000,
        ["metro", "carris", "cm"],
        (1, 1, 1, 1),
    ),
    (
        "caisdosodre",
        "Cais do Sodré",
        38.7063,
        -9.1446,
        36000,
        ["metro", "rail", "ferry", "carris"],
        (1, 1, 1, 1),
    ),
    (
        "oriente",
        "Oriente",
        38.7678,
        -9.0994,
        34000,
        ["metro", "rail", "cm", "carris"],
        (1, 1, 1, 1),
    ),
    (
        "baixa",
        "Baixa-Chiado",
        38.7107,
        -9.1395,
        30000,
        ["metro", "carris"],
        (0, 1, 1, 1),
    ),
    ("alameda", "Alameda", 38.7372, -9.1336, 26000, ["metro", "carris"], (1, 1, 0, 1)),
    (
        "entrecampos",
        "Entrecampos",
        38.7480,
        -9.1486,
        25000,
        ["metro", "rail", "carris"],
        (1, 1, 1, 1),
    ),
    (
        "saldanha",
        "Saldanha",
        38.7350,
        -9.1450,
        24000,
        ["metro", "carris"],
        (1, 0, 1, 0),
    ),
    (
        "seterios",
        "Sete Rios",
        38.7418,
        -9.1686,
        23000,
        ["metro", "rail", "cm", "carris"],
        (1, 1, 1, 1),
    ),
    (
        "terreiro",
        "Terreiro do Paço",
        38.7075,
        -9.1335,
        14000,
        ["metro", "ferry", "carris"],
        (1, 1, 0, 1),
    ),
    (
        "colegiomilitar",
        "Colégio Militar",
        38.7530,
        -9.1886,
        15000,
        ["metro", "cm", "carris"],
        (1, 0, 0, 0),
    ),
    (
        "reboleira",
        "Reboleira",
        38.7580,
        -9.2230,
        13000,
        ["metro", "rail", "cm"],
        (1, 1, 0, 1),
    ),
    ("pontinha", "Pontinha", 38.7625, -9.1970, 12000, ["metro", "cm"], (0, 1, 0, 1)),
    ("odivelas", "Odivelas", 38.7930, -9.1730, 11000, ["metro", "cm"], (1, 1, 0, 1)),
    (
        "santaapolonia",
        "Santa Apolónia",
        38.7137,
        -9.1227,
        9000,
        ["metro", "rail", "carris"],
        (1, 1, 1, 1),
    ),
    (
        "alcantara",
        "Alcântara",
        38.7050,
        -9.1760,
        9000,
        ["rail", "carris"],
        (0, 0, 1, 0),
    ),
    ("alges", "Algés", 38.7010, -9.2290, 10000, ["rail", "carris", "cm"], (1, 0, 1, 0)),
    ("belem", "Belém", 38.6970, -9.2060, 8000, ["rail", "carris"], (1, 0, 1, 0)),
    ("amoreiras", "Amoreiras", 38.7240, -9.1620, 7000, ["carris"], (0, 0, 0, 0)),
    ("sacavem", "Sacavém", 38.7930, -9.1010, 6000, ["rail", "cm"], (0, 0, 0, 0)),
    (
        "cacilhas",
        "Cacilhas",
        38.6880,
        -9.1480,
        16000,
        ["ferry", "cm", "other"],
        (1, 1, 1, 1),
    ),
    ("pragal", "Pragal", 38.6680, -9.1630, 9000, ["rail", "cm", "other"], (1, 1, 0, 1)),
    ("corroios", "Corroios", 38.6380, -9.1470, 6000, ["rail", "other"], (0, 1, 0, 1)),
    ("seixal", "Seixal", 38.6400, -9.1020, 5000, ["ferry", "cm"], (1, 0, 0, 0)),
    (
        "barreiro",
        "Barreiro",
        38.6570,
        -9.0790,
        12000,
        ["ferry", "rail", "other"],
        (1, 1, 0, 1),
    ),
    ("montijo", "Montijo", 38.7050, -8.9730, 4000, ["ferry", "cm"], (0, 0, 0, 0)),
]

# How strongly each operator weighs in a station's boardings (mock only).
OPERATOR_WEIGHT = {
    "metro": 0.6,
    "carris": 0.2,
    "cm": 0.25,
    "rail": 0.25,
    "ferry": 0.3,
    "other": 0.15,
}

DAY_FACTOR = {
    "2026-08-31": 0.95,
    "2026-09-01": 1.0,
    "2026-09-02": 1.0,
    "2026-09-03": 1.0,
    "2026-09-04": 0.97,
    "2026-09-05": 0.6,
    "2026-09-06": 0.45,
}

_WD = [
    0.010,
    0.035,
    0.085,
    0.110,
    0.080,
    0.050,
    0.045,
    0.048,
    0.050,
    0.048,
    0.052,
    0.062,
    0.085,
    0.095,
    0.070,
    0.045,
    0.030,
    0.022,
    0.015,
    0.008,
]
_WE = [
    0.008,
    0.020,
    0.035,
    0.050,
    0.060,
    0.070,
    0.075,
    0.078,
    0.078,
    0.075,
    0.075,
    0.075,
    0.075,
    0.070,
    0.060,
    0.045,
    0.035,
    0.025,
    0.018,
    0.010,
]
PROFILE_WEEKDAY = [v / sum(_WD) for v in _WD]
PROFILE_WEEKEND = [v / sum(_WE) for v in _WE]

# Planted anomalies: (station, date, hour, multiplier, robust z)
ALERT_SEEDS = [
    ("oriente", "2026-09-01", 18, 2.42, 6.8),
    ("caisdosodre", "2026-08-31", 8, 0.53, -4.1),
    ("campogrande", "2026-09-03", 17, 1.63, 4.4),
    ("pragal", "2026-09-02", 7, 1.38, 3.7),
    ("seterios", "2026-09-04", 19, 0.65, -3.9),
]

# Routes with trip-level capacity. Bus capacity comes from blocks.txt
# (available_seats + available_standing); ferry capacity from the vessel.
# est_share = fraction of a trip's boardings on board at its busiest point
# (from inferred alightings). A ferry has no intermediate stops, so it is 1.0.
LINES = [
    {
        "line_id": "1709",
        "label": "1709",
        "name": "Amadora Este ↔ Colégio Militar",
        "mode": "bus",
        "operator": "cm",
        "seats": 37,
        "standing": 52,
        "capacity_source": "blocks.txt",
        "daily": 9800,
        "est_share": 0.36,
        "shape": [
            [-9.2230, 38.7580],
            [-9.2115, 38.7565],
            [-9.2000, 38.7545],
            [-9.1886, 38.7530],
        ],
        "trips": [2, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 3, 4, 4, 3, 3, 2, 2, 2, 1],
    },
    {
        "line_id": "3001",
        "label": "3001",
        "name": "Pragal ↔ Cacilhas (Terminal)",
        "mode": "bus",
        "operator": "cm",
        "seats": 37,
        "standing": 52,
        "capacity_source": "blocks.txt",
        "daily": 7200,
        "est_share": 0.34,
        "shape": [[-9.1630, 38.6680], [-9.1560, 38.6780], [-9.1480, 38.6880]],
        "trips": [2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 2, 2, 2, 1, 1],
    },
    {
        "line_id": "1733",
        "label": "1733",
        "name": "Pontinha ↔ Sete Rios",
        "mode": "bus",
        "operator": "cm",
        "seats": 31,
        "standing": 47,
        "capacity_source": "blocks.txt",
        "daily": 8400,
        "est_share": 0.33,
        "shape": [
            [-9.1970, 38.7625],
            [-9.1850, 38.7540],
            [-9.1770, 38.7480],
            [-9.1686, 38.7418],
        ],
        "trips": [2, 3, 4, 4, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 3, 3, 2, 2, 2, 1],
    },
    {
        "line_id": "CA-CS",
        "label": "Cacilhas–Cais do Sodré",
        "name": "Ferry Cacilhas → Cais do Sodré",
        "mode": "ferry",
        "operator": "ferry",
        "seats": 590,
        "standing": 0,
        "capacity_source": "Transtejo fleet",
        "daily": 13000,
        "est_share": 1.0,
        "shape": [[-9.1480, 38.6880], [-9.1465, 38.6975], [-9.1446, 38.7063]],
        "trips": [2, 3, 4, 4, 3, 2, 2, 2, 2, 2, 2, 3, 4, 4, 3, 2, 2, 2, 1, 1],
    },
]
WHATIF_ADD_HOURS = [8, 8, 7, 18]
WHATIF_REMOVE_HOURS = [12, 13, 11, 14]

# Interchanges: station, [(from_operator, to_operator, transfers/day, median wait, p90 wait)]
INTERCHANGES = [
    (
        "caisdosodre",
        [
            ("ferry", "metro", 3900, 4, 9),
            ("rail", "metro", 2700, 5, 10),
            ("ferry", "carris", 1150, 11, 19),
        ],
    ),
    (
        "campogrande",
        [
            ("cm", "metro", 4200, 5, 10),
            ("carris", "metro", 1900, 6, 12),
            ("metro", "cm", 1700, 12, 22),
        ],
    ),
    (
        "marques",
        [
            ("carris", "metro", 3100, 6, 11),
            ("metro", "carris", 2400, 7, 14),
            ("cm", "metro", 800, 9, 16),
        ],
    ),
    (
        "oriente",
        [
            ("rail", "metro", 2600, 4, 8),
            ("cm", "metro", 2300, 8, 15),
            ("metro", "cm", 1400, 13, 24),
        ],
    ),
    ("cacilhas", [("cm", "ferry", 3300, 6, 12), ("other", "ferry", 900, 9, 17)]),
    ("seterios", [("rail", "metro", 2100, 5, 9), ("cm", "metro", 1300, 7, 13)]),
    ("barreiro", [("rail", "ferry", 2400, 3, 7), ("other", "ferry", 800, 10, 18)]),
    ("entrecampos", [("rail", "metro", 1800, 4, 8)]),
]

# Most common journeys that include a transfer: origin, destination, journeys/day
FLOWS = [
    ("reboleira", "marques", 2100),
    ("cacilhas", "baixa", 2600),
    ("barreiro", "terreiro", 1900),
    ("odivelas", "campogrande", 2300),
    ("pragal", "entrecampos", 1700),
    ("alges", "caisdosodre", 1500),
    ("sacavem", "oriente", 1200),
    ("seixal", "caisdosodre", 900),
    ("montijo", "caisdosodre", 700),
    ("pontinha", "seterios", 1100),
]

# Rough outline of the Tejo in (lon, lat), used only to keep mock hexagons off the water.
WATER = [
    (-9.35, 38.680),
    (-9.30, 38.685),
    (-9.25, 38.695),
    (-9.22, 38.697),
    (-9.18, 38.700),
    (-9.15, 38.703),
    (-9.13, 38.706),
    (-9.115, 38.712),
    (-9.105, 38.725),
    (-9.098, 38.745),
    (-9.094, 38.765),
    (-9.09, 38.785),
    (-9.08, 38.800),
    (-9.03, 38.800),
    (-8.98, 38.780),
    (-8.95, 38.750),
    (-8.95, 38.700),
    (-8.98, 38.695),
    (-9.02, 38.680),
    (-9.06, 38.665),
    (-9.085, 38.655),
    (-9.10, 38.648),
    (-9.12, 38.660),
    (-9.14, 38.678),
    (-9.15, 38.686),
    (-9.18, 38.680),
    (-9.22, 38.676),
    (-9.26, 38.668),
    (-9.35, 38.640),
]
