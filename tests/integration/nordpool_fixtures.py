"""Sanitized synthetic Nord Pool action responses (no real market data).

The official ``nordpool.get_prices_for_date`` action returns, per market area,
a list of ``{"start", "end", "price"}`` records with ISO timestamps carrying
the local UTC offset and prices in currency/MWh. These helpers generate
equivalent shapes for any Europe/Stockholm calendar day, including the DST
days that have 23 or 25 hours. The real contract remains unverified.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

STOCKHOLM = ZoneInfo("Europe/Stockholm")


def local_midnight(day: date) -> datetime:
    """Return the first instant of a Stockholm calendar day."""
    return datetime(day.year, day.month, day.day, tzinfo=STOCKHOLM)


def day_response(
    day: date,
    step: timedelta,
    area: str = "SE4",
    other_areas: tuple[str, ...] = ("SE3",),
) -> dict[str, list[dict[str, str | float]]]:
    """Cover one local day with contiguous intervals of the given length."""
    start_utc = local_midnight(day).astimezone(UTC)
    end_utc = local_midnight(day + timedelta(days=1)).astimezone(UTC)

    def series(offset: float) -> list[dict[str, str | float]]:
        records: list[dict[str, str | float]] = []
        cursor = start_utc
        index = 0
        while cursor < end_utc:
            following = cursor + step
            records.append({
                "start": cursor.astimezone(STOCKHOLM).isoformat(),
                "end": following.astimezone(STOCKHOLM).isoformat(),
                "price": round(400.0 + offset + (index % 24) * 10.0, 2),
            })
            cursor = following
            index += 1
        return records

    result = {area: series(0.0)}
    for position, other in enumerate(other_areas, start=1):
        result[other] = series(position * 50.0)
    return result
