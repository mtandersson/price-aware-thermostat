"""Run the SYNTHETIC optimizer benchmark and print a markdown report.

    python -m benchmarks.run_benchmark [--seed N] [--out FILE]

SIMULATION on SYNTHETIC data only. Nothing is measured. See docs/benchmark.md.
Pure Python; no Home Assistant imports, no network access.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from benchmarks.fixtures import Scenario, build_scenarios, outdoor_temperature_c
from custom_components.price_aware_thermostat.optimizer import (
    OptimizerConfig,
    PriceInterval,
    optimize,
)


@dataclass(frozen=True)
class ModelParams:
    """Explicit thermal-model assumptions (all invented, none measured)."""

    loss_kw_per_k: float = 0.04  # heat loss = this * (indoor - outdoor)
    heater_kw: float = 2.0  # fixed heater power when on
    capacity_kwh_per_k: float = 1.5  # thermal mass of room and contents
    hysteresis_c: float = 0.2  # on below target-h, off above target+h
    step_minutes: int = 5
    initial_temperature_c: float = 6.0


@dataclass(frozen=True)
class RunResult:
    cost_sek: float
    energy_kwh: float
    peak_c: float
    min_c: float
    final_c: float
    runtime_h: float
    adjusted_share: float  # share of time the effective target differed from base
    unavailable_share: float  # share of time optimizer had no usable forecast
    optimizer_calls: int


def _targets(
    visible: list[PriceInterval], cfg: OptimizerConfig
) -> tuple[list[datetime], list[datetime], list[float], list[bool]]:
    """Effective target per visible interval (constant within an interval)."""
    ordered = sorted(visible, key=lambda p: p.start)
    targets: list[float] = []
    unavailable: list[bool] = []
    for p in ordered:
        res = optimize(p.start, ordered, cfg)
        targets.append(res.effective_target_c)
        unavailable.append(res.reason == "price_unavailable")
    return [p.start for p in ordered], [p.end for p in ordered], targets, unavailable


def simulate(
    scenario: Scenario,
    cfg: OptimizerConfig,
    optimized: bool,
    params: ModelParams = ModelParams(),
    seed: int = 42,
) -> RunResult:
    """Simulate real elapsed time (UTC) in fixed steps; heater driven by hysteresis.

    With optimized=False the optimizer is never called: the target is always
    cfg.base_temperature (fixed-setpoint baseline).
    """
    true = sorted(scenario.true_prices, key=lambda p: p.start)
    true_starts = [p.start for p in true]
    sim_start, sim_end = true[0].start, true[-1].end
    v_starts: list[datetime] = []
    v_ends: list[datetime] = []
    v_targets: list[float] = []
    v_unavail: list[bool] = []
    calls = 0
    if optimized:
        v_starts, v_ends, v_targets, v_unavail = _targets(scenario.visible_prices, cfg)
        calls = len(v_starts)

    dt = timedelta(minutes=params.step_minutes)
    dt_h = params.step_minutes / 60.0
    temp = params.initial_temperature_c
    on = False
    cost = energy = runtime = 0.0
    peak = low = temp
    steps = adjusted = unavailable = 0
    now = sim_start
    while now < sim_end:
        target = cfg.base_temperature
        if optimized:
            i = bisect_right(v_starts, now) - 1
            if i >= 0 and now < v_ends[i]:
                target = v_targets[i]
                unavailable += v_unavail[i]
            else:
                unavailable += 1  # no current interval visible: fallback to base
        if abs(target - cfg.base_temperature) > 1e-9:
            adjusted += 1
        if temp < target - params.hysteresis_c:
            on = True
        elif temp > target + params.hysteresis_c:
            on = False
        price = true[bisect_right(true_starts, now) - 1].price_sek_per_kwh
        kwh = params.heater_kw * dt_h if on else 0.0
        energy += kwh
        cost += kwh * price
        runtime += dt_h if on else 0.0
        outdoor = outdoor_temperature_c(now, scenario.outdoor_mean_c, seed)
        heat_kw = params.heater_kw if on else 0.0
        temp += (heat_kw - params.loss_kw_per_k * (temp - outdoor)) * dt_h / params.capacity_kwh_per_k
        peak, low = max(peak, temp), min(low, temp)
        steps += 1
        now += dt
    return RunResult(
        cost, energy, peak, low, temp, runtime, adjusted / steps, unavailable / steps, calls
    )


BASE = OptimizerConfig(base_temperature=6.0, minimum_temperature=5.3, maximum_temperature=7.5)
FILTERS = ("rectangle", "triangle")
LOOKAHEADS = (60, 180, 360)
SENSITIVITIES = (1.0, 3.0)
BASELINE = "fixed 6.0"


def run_all(seed: int = 42) -> list[tuple[str, str, RunResult]]:
    rows: list[tuple[str, str, RunResult]] = []
    for sc in build_scenarios(seed):
        rows.append((sc.name, BASELINE, simulate(sc, BASE, False, seed=seed)))
        for f in FILTERS:
            for la in LOOKAHEADS:
                for sens in SENSITIVITIES:
                    cfg = replace(
                        BASE,
                        filter_type=f,
                        lookahead_minutes=la,
                        sensitivity_c_per_sek_per_kwh=sens,
                    )
                    label = f"{f} {la}min s={sens:g}"
                    rows.append((sc.name, label, simulate(sc, cfg, True, seed=seed)))
    return rows


def render(rows: list[tuple[str, str, RunResult]], seed: int) -> str:
    out = [
        "# Synthetic optimizer benchmark",
        "",
        f"SIMULATION on SYNTHETIC data (seed {seed}). Nothing here is measured; "
        "no real-world savings are claimed.",
        "",
        "| scenario | strategy | cost SEK | vs fixed | energy kWh | peak C | min C | final C "
        "| heater h | adjusted % | no-forecast % |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    baseline: dict[str, float] = {}
    for name, strat, r in rows:
        if strat == BASELINE:
            baseline[name] = r.cost_sek
            delta = "-"
        else:
            delta = f"{(r.cost_sek / baseline[name] - 1) * 100:+.1f}%"
        out.append(
            f"| {name} | {strat} | {r.cost_sek:.2f} | {delta} | {r.energy_kwh:.1f} | {r.peak_c:.2f} "
            f"| {r.min_c:.2f} | {r.final_c:.2f} | {r.runtime_h:.1f} | {r.adjusted_share * 100:.0f} "
            f"| {r.unavailable_share * 100:.0f} |"
        )
    by = {(n, s): r for n, s, r in rows}
    out += [
        "",
        "## Forecast-gap sensitivity (`gap` vs `hourly`, same true prices)",
        "",
        "| strategy | cost hourly | cost gap | change |",
        "|---|---:|---:|---:|",
    ]
    for name, strat, r in rows:
        if name == "hourly" and strat != BASELINE:
            g = by[("gap", strat)]
            change = (g.cost_sek / r.cost_sek - 1) * 100
            out.append(f"| {strat} | {r.cost_sek:.2f} | {g.cost_sek:.2f} | {change:+.1f}% |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", help="also write the markdown table to this file")
    args = ap.parse_args(argv)
    text = render(run_all(args.seed), args.seed)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
