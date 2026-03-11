from __future__ import annotations

import re
from datetime import datetime


def parse_user_date(raw: str, *, now: datetime | None = None) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    normalized = re.sub(r"[.\s/]+", "-", value)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    parts = normalized.split("-")
    if len(parts) == 2:
        day_s, month_s = parts
        year = (now or datetime.now()).year
    elif len(parts) == 3:
        day_s, month_s, year_s = parts
        if len(year_s) == 2 and year_s.isdigit():
            year = 2000 + int(year_s)
        elif len(year_s) == 4 and year_s.isdigit():
            year = int(year_s)
        else:
            return None
    else:
        return None
    if not (day_s.isdigit() and month_s.isdigit()):
        return None
    try:
        return datetime(year, int(month_s), int(day_s))
    except ValueError:
        return None
