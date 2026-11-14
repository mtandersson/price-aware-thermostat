"""SYNTHETIC price and outdoor-temperature fixtures.

Nothing here is measured or fetched. Every series is generated from a seeded
pseudo-random generator and simple deterministic shapes, so output is identical
for the same seed. Prices are SEK/kWh and are NOT real Nord Pool prices.
Pure Python; no Home Assistant imports.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.price_aware_thermostat.optimizer import PriceInterval

STOCKHOLM = ZoneInfo("Europe/Stockholm")

# Invented daily price shape in SEK/kWh by local hour: cheap night, peaks morning/evening.
_HOUR_SHAPE = (
    0.35, 0.30, 0.28, 0.28, 0.32, 0.45, 0.80, 1.20,
    1.30, 1.00, 0.80, 0.70, 0.65, 0.60, 0.65, 0.80,
    1.00, 1.40, 1.50, 1.20, 0.90, 0.70, 0.50, 0.40,
)  # fmt: skip


def local_midnight_utc(day: date, tz: ZoneInfo = STOCKHOLM) -> datetime:
    """UTC instant of local midnight at the start of `day`."""
    return datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)


def utc_range(start_day: date, days: int, tz: ZoneInfo = STOCKHOLM) -> tuple[datetime, datetime]:
    """Real UTC span covering `days` local calendar days (23/25 h days at DST)."""
    return local_midnight_utc(start_day, tz), local_midnight_utc(
        start_day + timedelta(days=days), tz
    )


def hourly_prices(
    start_day: date,
    days: int,
    seed: int,
    negative_midday: bool = False,
    flat: bool = False,
    tz: ZoneInfo = STOCKHOLM,
) -> list[PriceInterval]:
    """SYNTHETIC hourly prices covering whole local days (real elapsed time).

    flat=True is a negative control: near-constant price with small noise.
    """
    rng = random.Random(seed)
    start, end = utc_range(start_day, days, tz)
    result: list[PriceInterval] = []
    day_offset: dict[date, float] = {}
    cursor = start
    while cursor < end:
        local = cursor.astimezone(tz)
        offset = day_offset.setdefault(local.date(), rng.uniform(-0.2, 0.4))
        price = _HOUR_SHAPE[local.hour] + offset + rng.gauss(0.0, 0.08)
        if flat:
            price = 0.80 + rng.gauss(0.0, 0.03)
        if negative_midday and 11 <= local.hour <= 14 and local.toordinal() % 2 == 0:
            price = -0.15 - rng.uniform(0.0, 0.25)
        result.append(PriceInterval(cursor, cursor + timedelta(hours=1), round(price, 4)))
        cursor += timedelta(hours=1)
    return result


def to_quarter_hours(prices: list[PriceInterval]) -> list[PriceInterval]:
    """Split every hourly interval into four 15-minute intervals with the same price."""
    result: list[PriceInterval] = []
    for p in prices:
        step = (p.end - p.start) / 4
        for i in range(4):
            result.append(
                PriceInterval(p.start + step * i, p.start + step * (i + 1), p.price_sek_per_kwh)
            )
    return result


def drop_forecast_window(
    prices: list[PriceInterval],
    start_hour: int = 20,
    end_hour: int = 2,
    tz: ZoneInfo = STOCKHOLM,
) -> list[PriceInterval]:
    """Remove intervals whose local start hour is in [start_hour, end_hour), wrapping midnight.

    Models a recurring SYNTHETIC forecast gap (e.g. next-day prices unpublished).
    """

    def in_gap(h: int) -> bool:
        if start_hour > end_hour:
            return h >= start_hour or h < end_hour
        return start_hour <= h < end_hour

    return [p for p in prices if not in_gap(p.start.astimezone(tz).hour)]


def outdoor_temperature_c(
    when: datetime, mean_c: float, seed: int, amplitude_c: float = 3.0, tz: ZoneInfo = STOCKHOLM
) -> float:
    """SYNTHETIC outdoor temperature: daily sinusoid (max 14:00 local) plus a seeded daily offset."""
    local = when.astimezone(tz)
    rng = random.Random(seed * 1_000_003 + local.toordinal())
    daily_offset = rng.uniform(-3.0, 3.0)
    hours = local.hour + local.minute / 60.0
    return mean_c + daily_offset + amplitude_c * math.cos(2 * math.pi * (hours - 14.0) / 24.0)


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    true_prices: list[PriceInterval]  # what the electricity is actually billed at
    visible_prices: list[PriceInterval]  # what the optimizer is allowed to see
    outdoor_mean_c: float


def build_scenarios(seed: int = 42) -> list[Scenario]:
    """All SYNTHETIC scenarios. Same seed gives identical scenarios."""
    winter = hourly_prices(date(2026, 1, 12), 7, seed)
    fall = hourly_prices(date(2026, 10, 23), 5, seed + 1)  # spans 2026-10-25 (25 h)
    spring = hourly_prices(date(2026, 3, 27), 5, seed + 2)  # spans 2026-03-29 (23 h)
    negative = hourly_prices(date(2026, 1, 12), 7, seed + 3, negative_midday=True)
    winter_q = to_quarter_hours(winter)
    flat = hourly_prices(date(2026, 1, 12), 7, seed + 4, flat=True)
    return [
        Scenario("hourly", "7 days, 60-min prices", winter, winter, -3.0),
        Scenario("quarter", "same prices as `hourly`, 15-min resolution", winter, winter_q, -3.0),
        Scenario("dst_fall", "5 days across 2026-10-25 (25 h local day)", fall, fall, 3.0),
        Scenario("dst_spring", "5 days across 2026-03-29 (23 h local day)", spring, spring, 1.0),
        Scenario(
            "negative", "7 days, negative midday prices on alternate days", negative, negative, -3.0
        ),
        Scenario("flat", "negative control: near-constant price (0.80 +- 0.03)", flat, flat, -3.0),
        Scenario(
            "gap",
            "`hourly` prices, optimizer cannot see 20:00-02:00 local each day",
            winter,
            drop_forecast_window(winter),
            -3.0,
        ),
    ]
