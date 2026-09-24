"""The one definition of "typical" used by every comparison in the API.

Typical = median of the same service hour on the other days of the same type
(weekdays with weekdays, weekend with weekend), never including the selected
day. The anomaly baseline (fact_stop_hour.expected) uses the same rule.
"""

from statistics import median
from typing import Dict, Iterable, List, Optional, Tuple

from . import reference as R

MIN_BASELINE_DAYS = 2

METHOD = (
    "Typical = median of the same service hour on the other days of the same type "
    "(weekdays with weekdays, weekend with weekend), excluding the selected day. "
    f"At least {MIN_BASELINE_DAYS} comparison days are required."
)

_DAY = {d["date"]: d for d in R.DAYS}


def day_type(date: str) -> str:
    return "weekend" if _DAY[date]["is_weekend"] else "weekday"


def comparable_days(date: str, eligible: Optional[Iterable[str]] = None) -> List[str]:
    """Other days of the same type, optionally restricted to days with usable data."""
    allowed = None if eligible is None else set(eligible)
    return [
        d["date"] for d in R.DAYS
        if d["is_weekend"] == _DAY[date]["is_weekend"] and d["date"] != date
        and (allowed is None or d["date"] in allowed)
    ]


def typical(values: Dict[str, float], days: List[str]) -> Optional[float]:
    """Median over the baseline days; a covered day with no value counts as 0."""
    if len(days) < MIN_BASELINE_DAYS:
        return None
    return round(median(values.get(d, 0.0) for d in days), 1)


def difference(current: Optional[float], base: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """Absolute and percentage difference; None when either side is unknown or the base is 0."""
    if current is None or base is None:
        return None, None
    diff = round(current - base, 1)
    return diff, (None if base == 0 else round(100 * diff / base, 1))


def build_comparison(values, date, hours, is_complete, privacy_min=0):
    """Current vs typical for `hours` of `date`, plus the whole day's hourly profile.

    values: {(date, hour): value} for the selected day and its comparable days.
    is_complete(date, hour): whether the source data fully covers that hour.
    privacy_min: values under it are returned as null (0 disables the check).
    """
    from . import schemas as S  # local import keeps this module free of pydantic at import time

    def shown(value):
        if value is None or (privacy_min and value < privacy_min):
            return None
        return round(value, 1)

    same_type = comparable_days(date)
    selected_ok = bool(hours) and all(is_complete(date, h) for h in hours)
    days = [d for d in same_type if all(is_complete(d, h) for h in hours)] if hours else []

    def total(day):
        return sum(values.get((day, h), 0.0) for h in hours)

    current_raw = total(date) if selected_ok else None
    typical_raw = typical({d: total(d) for d in days}, days) if selected_ok else None
    current, base = shown(current_raw), shown(typical_raw)
    diff, pct = difference(current, base)

    if not selected_ok:
        status, note = "incomplete_coverage", "The selected period is not fully covered by the source data."
    elif typical_raw is None:
        status = "insufficient_baseline"
        kind = "weekday" if day_type(date) == "weekday" else "weekend day"
        plural = "" if len(days) == 1 else "s"
        note = f"Only {len(days)} other {kind}{plural} fully cover this period; {MIN_BASELINE_DAYS} are needed."
    elif current is None:
        status, note = "below_privacy_threshold", f"Fewer than {privacy_min} journeys: not shown."
    else:
        status = "ok"
        note = None if base is not None else f"Typical is below the privacy threshold of {privacy_min}."

    hourly = []
    for h in R.HOURS:
        covered = is_complete(date, h)
        base_days = [d for d in same_type if is_complete(d, h)]
        hourly.append(S.ComparisonHour(
            hour=h,
            current=shown(values.get((date, h), 0.0)) if covered else None,
            typical=shown(typical({d: values.get((d, h), 0.0) for d in base_days}, base_days)),
            complete=covered,
        ))

    return S.Comparison(
        status=status, current=current, typical=base, difference=diff, difference_pct=pct,
        method=METHOD, day_type=day_type(date),
        baseline_days=[S.BaselineDay(date=d, value=shown(total(d))) for d in days],
        sample_days=len(days), min_sample_days=MIN_BASELINE_DAYS, hours_compared=list(hours),
        hourly=hourly, note=note,
    )
