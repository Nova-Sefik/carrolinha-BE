"""
Reference data shared by every provider.

Operators, days, hours and segments are real facts about the dataset.
"""

# --------------------------------------------------------------------------
# Real: the dataset week and its conventions
# --------------------------------------------------------------------------

WEEK_START = "2026-08-31"
WEEK_END = "2026-09-06"

# Service hours shown in the UI. 24 means 00:00-00:59 after midnight, the
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

# Entry validations. Metro exit taps (4, 14) are only used for origin-destination.
ENTRY_EVENT_TYPES = [1, 3, 13]
EXIT_EVENT_TYPES = [4, 14]

HEX_RESOLUTION = 8  # ~0.74 km² per cell

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
