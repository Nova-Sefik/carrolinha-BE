"""One privacy threshold for every count that describes linked taps by the same card.

Journeys and transfers link a card's taps, so a very small group can describe
one person's routine. Groups under PRIVACY_MIN are counted in totals but never
returned on their own; any single value under it is returned as null.
"""

import os
from typing import Optional

PRIVACY_MIN = int(os.environ.get("CARROLINHA_PRIVACY_MIN", "10"))


def shown(value: Optional[float]) -> Optional[float]:
    return None if value is None or value < PRIVACY_MIN else value
