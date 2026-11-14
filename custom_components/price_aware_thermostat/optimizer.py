"""Pure, deterministic electricity-price-to-setpoint optimizer.

The library has no Home Assistant dependencies, writes no entity state, and never
controls a heater. Prices must be supplied as SEK/kWh in UTC-aware intervals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Literal, Sequence

Filter = Literal["rectangle", "triangle"]


@dataclass(frozen=True)
class PriceInterval:
    start: datetime
    end: datetime
    price_sek_per_kwh: float

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Price intervals require timezone-aware datetimes")
        if self.end.astimezone(timezone.utc) <= self.start.astimezone(timezone.utc):
            raise ValueError("Each price interval must have positive duration")
        if not isfinite(self.price_sek_per_kwh):
            raise ValueError("Price must be finite; negative prices are allowed")


@dataclass(frozen=True)
class OptimizerConfig:
    base_temperature: float = 6.0
    minimum_temperature: float = 5.3
    maximum_temperature: float = 7.5
    lookahead_minutes: int = 180
    filter_type: Filter = "triangle"
    deadband_sek_per_kwh: float = 0.20
    sensitivity_c_per_sek_per_kwh: float = 2.0
    min_coverage_fraction: float = 0.75

    def __post_init__(self) -> None:
        floats = (
            self.base_temperature,
            self.minimum_temperature,
            self.maximum_temperature,
            self.deadband_sek_per_kwh,
            self.sensitivity_c_per_sek_per_kwh,
            self.min_coverage_fraction,
        )
        if not all(isfinite(v) for v in floats):
            raise ValueError("Optimizer parameters must be finite")
        if not self.minimum_temperature <= self.base_temperature <= self.maximum_temperature:
            raise ValueError("minimum <= base <= maximum is required")
        if self.lookahead_minutes < 15:
            raise ValueError("Lookahead must be at least 15 minutes")
        if self.filter_type not in ("rectangle", "triangle"):
            raise ValueError("Unknown filter type")
        if self.deadband_sek_per_kwh < 0 or self.sensitivity_c_per_sek_per_kwh < 0:
            raise ValueError("Deadband and sensitivity must not be negative")
        if not 0 < self.min_coverage_fraction <= 1:
            raise ValueError("Coverage fraction must be in (0,1]")


@dataclass(frozen=True)
class OptimizationResult:
    effective_target_c: float
    reason: Literal["preheat", "expensive", "normal", "price_unavailable"]
    current_price_sek_per_kwh: float | None
    future_weighted_price_sek_per_kwh: float | None
    price_difference_sek_per_kwh: float | None
    future_coverage_fraction: float


def normalize_nordpool_response(
    response: dict, area: str
) -> list[PriceInterval]:
    """Parse the official nordpool.get_prices_for_date response for one area.

    The action's `price` field is denominated in currency/MWh. This adapter
    assumes the service was invoked with currency='SEK' and converts to SEK/kWh.
    The caller must explicitly request SEK; other currencies must not be passed.
    """
    if area not in response:
        raise ValueError(f"Market area {area!r} not present in response")
    result = []
    for raw in response[area]:
        try:
            start = datetime.fromisoformat(raw["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(raw["end"].replace("Z", "+00:00"))
            price = float(raw["price"]) / 1000.0
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid Nord Pool interval") from exc
        result.append(PriceInterval(start, end, price))
    return result


def _fallback(config: OptimizerConfig, coverage: float = 0.0) -> OptimizationResult:
    return OptimizationResult(config.base_temperature, "price_unavailable", None, None, None, coverage)


def optimize(
    now: datetime,
    prices: Sequence[PriceInterval],
    config: OptimizerConfig = OptimizerConfig(),
) -> OptimizationResult:
    """Compute price-adjusted target; never actuate a switch.

    Compare the price for the interval containing `now` against a duration-
    weighted forecast starting *after the current price interval*. Supports
    mixed hourly and 15-minute data, timezones/DST, and negative prices.

    Triangle weighting is a linearly decaying time integral, not a weight per
    row. Thus changing interval resolution does not artificially alter weight.

    Coverage requires a contiguous forecast starting at the next interval;
    forecasts that stop near the end of the published day may be used if they
    cover the configured minimum fraction. An internal gap is never bridged.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    utc_now = now.astimezone(timezone.utc)
    ordered = sorted(prices, key=lambda p: p.start.astimezone(timezone.utc))
    for previous, following in zip(ordered, ordered[1:]):
        if previous.end.astimezone(timezone.utc) > following.start.astimezone(timezone.utc):
            raise ValueError("Overlapping price intervals")

    current = next(
        (p for p in ordered if p.start.astimezone(timezone.utc) <= utc_now < p.end.astimezone(timezone.utc)),
        None,
    )
    if current is None:
        return _fallback(config)

    anchor = current.end.astimezone(timezone.utc)
    horizon_seconds = config.lookahead_minutes * 60.0
    horizon_end = anchor + timedelta(minutes=config.lookahead_minutes)
    cursor = anchor
    weighted_price_sum = 0.0
    weight_sum = 0.0
    coverage_seconds = 0.0

    for interval in ordered:
        start = interval.start.astimezone(timezone.utc)
        end = interval.end.astimezone(timezone.utc)
        if end <= cursor or start >= horizon_end:
            continue
        if start > cursor:
            # An internal gap ends the valid contiguous forecast.
            break
        finish = min(end, horizon_end)
        segment = (finish - cursor).total_seconds()
        if segment <= 0:
            continue
        if config.filter_type == "rectangle":
            weight = segment
        else:
            # Exact area under w(t) = 1 - t/horizon; t measured from anchor.
            t0 = (cursor - anchor).total_seconds()
            t1 = (finish - anchor).total_seconds()
            weight = (t1 - t0) - (t1 * t1 - t0 * t0) / (2.0 * horizon_seconds)
        weighted_price_sum += interval.price_sek_per_kwh * weight
        weight_sum += weight
        coverage_seconds += segment
        cursor = finish
        if cursor >= horizon_end:
            break

    coverage = coverage_seconds / horizon_seconds
    if coverage + 1e-12 < config.min_coverage_fraction or weight_sum <= 0:
        return _fallback(config, coverage)

    future_price = weighted_price_sum / weight_sum
    price_diff = future_price - current.price_sek_per_kwh
    adjusted_delta = max(0.0, abs(price_diff) - config.deadband_sek_per_kwh)
    if price_diff < 0:
        adjusted_delta *= -1
    raw_target = config.base_temperature + config.sensitivity_c_per_sek_per_kwh * adjusted_delta
    target = min(config.maximum_temperature, max(config.minimum_temperature, raw_target))
    reason: Literal["preheat", "expensive", "normal", "price_unavailable"]
    if target > config.base_temperature + 1e-9:
        reason = "preheat"
    elif target < config.base_temperature - 1e-9:
        reason = "expensive"
    else:
        reason = "normal"
    return OptimizationResult(
        target,
        reason,
        current.price_sek_per_kwh,
        future_price,
        price_diff,
        coverage,
    )
