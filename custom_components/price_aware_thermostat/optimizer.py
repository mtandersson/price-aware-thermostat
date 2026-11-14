"""Pure, deterministic electricity-price-to-setpoint optimizer.

The library has no Home Assistant dependencies, writes no entity state, and never
controls a heater. Prices must be supplied as SEK/kWh in UTC-aware intervals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal

Filter = Literal["rectangle", "triangle"]
HvacMode = Literal["heat", "cool"]
Reason = Literal["preheat", "precool", "expensive", "normal", "price_unavailable"]


@dataclass(frozen=True)
class PriceInterval:
    start: datetime
    end: datetime
    price_sek_per_kwh: float

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Price intervals require timezone-aware datetimes")
        if self.end.astimezone(UTC) <= self.start.astimezone(UTC):
            raise ValueError("Each price interval must have positive duration")
        if not isfinite(self.price_sek_per_kwh):
            raise ValueError("Price must be finite; negative prices are allowed")


@dataclass(frozen=True)
class OptimizerConfig:
    base_temperature: float = 6.0
    # Absolute safety limits (also the UI setpoint range in the integration).
    minimum_temperature: float = 5.0
    maximum_temperature: float = 25.0
    lookahead_minutes: int = 180
    filter_type: Filter = "triangle"
    deadband_sek_per_kwh: float = 0.20
    sensitivity_c_per_sek_per_kwh: float = 2.0
    min_coverage_fraction: float = 0.75
    # Heating raises the room target while electricity is relatively cheap;
    # cooling lowers it. The adjustment sign reverses when electricity is
    # relatively expensive. Compensation for a device's native setpoint is
    # deliberately not part of the optimizer.
    hvac_mode: HvacMode = "heat"
    # Symmetric limit on the price adjustment around the base target, applied
    # before the absolute safety limits. None means only the absolute limits
    # apply (kept for library callers; the integration always passes a number).
    max_deviation_c: float | None = 0.8

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
        if self.hvac_mode not in ("heat", "cool"):
            raise ValueError("Unknown HVAC mode")
        if self.max_deviation_c is not None and (
            isinstance(self.max_deviation_c, bool)
            or not isinstance(self.max_deviation_c, (int, float))
            or not isfinite(self.max_deviation_c)
            or self.max_deviation_c < 0
        ):
            raise ValueError("Maximum deviation must be a finite number, not negative")

    @property
    def lower_bound(self) -> float:
        """Lowest permitted effective target, including any deviation limit."""
        if self.max_deviation_c is None:
            return self.minimum_temperature
        return max(self.minimum_temperature, self.base_temperature - self.max_deviation_c)

    @property
    def upper_bound(self) -> float:
        """Highest permitted effective target, including any deviation limit."""
        if self.max_deviation_c is None:
            return self.maximum_temperature
        return min(self.maximum_temperature, self.base_temperature + self.max_deviation_c)


@dataclass(frozen=True)
class OptimizationResult:
    effective_target_c: float
    reason: Reason
    current_price_sek_per_kwh: float | None
    future_weighted_price_sek_per_kwh: float | None
    price_difference_sek_per_kwh: float | None
    future_coverage_fraction: float
    # Adjustment the price signal asked for before comfort limits were applied.
    # `clamped` shows when limits reduced or removed it, even if the reason is
    # "normal" because the final target equals the base target.
    requested_adjustment_c: float = 0.0
    clamped: bool = False


def normalize_nordpool_response(
    response: Mapping[str, object], area: str
) -> list[PriceInterval]:
    """Validate and normalize official Nord Pool prices for one market area.

    The official action returns price data in currency/MWh. Callers must request
    SEK explicitly; the normalized price then uses SEK/kWh. All untrusted
    service-response values are checked before use, not merely cast into types.
    """
    raw_intervals = response.get(area)
    if not isinstance(raw_intervals, list):
        raise ValueError(f"Missing or invalid price series for market area {area!r}")

    result: list[PriceInterval] = []
    for raw in raw_intervals:
        if not isinstance(raw, dict):
            raise ValueError("Invalid Nord Pool interval")
        start_value = raw.get("start")
        end_value = raw.get("end")
        price_value = raw.get("price")
        if (
            not isinstance(start_value, str)
            or not isinstance(end_value, str)
            or not isinstance(price_value, (str, int, float))
            or isinstance(price_value, bool)
        ):
            raise ValueError("Invalid Nord Pool interval")
        try:
            start = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
            price = float(price_value) / 1000.0
            result.append(PriceInterval(start, end, price))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid Nord Pool interval") from exc

    # Official 15-minute series must be chronological, even through Swedish
    # DST folds where the local wall clock moves backwards. Reject overlaps
    # before they are cached, rather than deferring the failure to optimize().
    result.sort(key=lambda interval: interval.start.astimezone(UTC))
    for previous, following in zip(result, result[1:], strict=False):
        if previous.end.astimezone(UTC) > following.start.astimezone(UTC):
            raise ValueError("Overlapping Nord Pool price intervals")
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
    utc_now = now.astimezone(UTC)
    ordered = sorted(prices, key=lambda p: p.start.astimezone(UTC))
    for previous, following in zip(ordered, ordered[1:], strict=False):
        if previous.end.astimezone(UTC) > following.start.astimezone(UTC):
            raise ValueError("Overlapping price intervals")

    current = next(
        (p for p in ordered if p.start.astimezone(UTC) <= utc_now < p.end.astimezone(UTC)),
        None,
    )
    if current is None:
        return _fallback(config)

    anchor = current.end.astimezone(UTC)
    horizon_seconds = config.lookahead_minutes * 60.0
    horizon_end = anchor + timedelta(minutes=config.lookahead_minutes)
    cursor = anchor
    weighted_price_sum = 0.0
    weight_sum = 0.0
    coverage_seconds = 0.0

    for interval in ordered:
        start = interval.start.astimezone(UTC)
        end = interval.end.astimezone(UTC)
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
    # A cheaper present than future favours storing heat (higher target) or
    # cold (lower target); the sign for cooling is therefore reversed.
    direction = 1.0 if config.hvac_mode == "heat" else -1.0
    # `+ 0.0` normalises a negative zero so diagnostics never show -0.0.
    adjustment = direction * config.sensitivity_c_per_sek_per_kwh * adjusted_delta + 0.0
    raw_target = config.base_temperature + adjustment
    target = min(config.upper_bound, max(config.lower_bound, raw_target))
    reason: Reason
    if target > config.base_temperature + 1e-9:
        reason = "preheat" if config.hvac_mode == "heat" else "expensive"
    elif target < config.base_temperature - 1e-9:
        reason = "expensive" if config.hvac_mode == "heat" else "precool"
    else:
        reason = "normal"
    return OptimizationResult(
        target,
        reason,
        current.price_sek_per_kwh,
        future_price,
        price_diff,
        coverage,
        requested_adjustment_c=adjustment,
        clamped=abs(target - raw_target) > 1e-9,
    )
