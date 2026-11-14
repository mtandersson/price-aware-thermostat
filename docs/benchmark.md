# Synthetic optimizer benchmark

**These results are SIMULATION on SYNTHETIC data.** No price, temperature or
heater measurement is used anywhere in this benchmark. It makes **no claim of
real-world savings**. Real savings depend on the actual installation: price
spread, building thermal mass and insulation, heater, thermostat hysteresis,
grid fees and taxes, and the weather. The purpose is to check the optimizer's
behaviour under explicit assumptions, not to predict a bill.

## How to run

The benchmark is pure Python (3.12+), offline, seeded and deterministic. It
does not import Home Assistant and does not access the network.

    python -m benchmarks.run_benchmark                 # markdown table to stdout
    python -m benchmarks.run_benchmark --seed 7 --out /tmp/bench.md

It takes a few seconds. The same seed always produces byte-identical output
(tested in `tests/test_benchmark.py`). Code: `benchmarks/fixtures.py`
(synthetic scenarios) and `benchmarks/run_benchmark.py` (simulation and report).

## What is measured and what is assumed

| Item | Status |
|---|---|
| Prices (SEK/kWh) | **Synthetic.** A made-up daily shape (cheap night, peaks at about 08:00 and 18:00) plus a seeded daily offset and Gaussian noise. Not Nord Pool data. Price is energy only; no grid fee, tax or VAT. |
| Outdoor temperature | **Synthetic.** Daily cosine (max 14:00 local, amplitude 3 C) around a scenario mean (-3 C winter, 3 C October, 1 C March) plus a seeded daily offset of +-3 C. |
| Thermal loss | **Assumed.** Heat loss = 0.04 kW/K x (indoor - outdoor). |
| Thermal capacity | **Assumed.** 1.5 kWh/K (single lumped node, no walls, no ground, no solar gain). |
| Heater | **Assumed.** Fixed 2.0 kW when on, 100 % efficient, off otherwise. Cost = heater kWh x true price of the interval. |
| Thermostat | **Assumed.** Hysteresis of +-0.2 C around the effective target; on below target-0.2, off above target+0.2. |
| Time step | 5 minutes of real elapsed (UTC) time. DST days are 23 or 25 hours long. |
| Start state | Indoor temperature 6.0 C, heater off. |
| Optimizer | The real `optimizer.optimize()` with base 6.0 C, min 5.3 C, max 7.5 C, deadband 0.20 SEK/kWh, min coverage 0.75; filter, lookahead and sensitivity vary. The optimizer is evaluated at the start of each price interval (its output is constant within an interval). |
| Baseline | Fixed 6.0 C setpoint; `optimize()` is never called. |
| Measured data | **None.** |

### Scenarios (all synthetic)

- `hourly`: 7 days from 2026-01-12, 60-minute prices.
- `quarter`: the identical hourly prices split into four 15-minute intervals.
- `dst_fall`: 5 days across 2026-10-25 (Europe/Stockholm, 25-hour day).
- `dst_spring`: 5 days across 2026-03-29 (23-hour day).
- `negative`: as `hourly` but midday prices are negative on alternate days.
- `flat`: negative control, price 0.80 SEK/kWh with tiny noise (smaller than the deadband).
- `gap`: the `hourly` prices, but the optimizer cannot see 20:00-02:00 local each day (billing still uses the true prices). During the gap there is no current interval, so the base target is used; intervals before the gap see a truncated forecast.

## Results (seed 42, selected rows)

Cost is simulated heater cost in SEK over the scenario. "vs fixed" compares
with the fixed-setpoint run of the same scenario. "adj %" is the share of time
the effective target differed from 6.0 C; "no-fc %" is the share of time with
no usable forecast. The full table (all 13 strategies per scenario, including
heater runtime) is printed by the script.

| scenario | strategy | cost SEK | vs fixed | energy kWh | peak C | min C | final C | adj % | no-fc % |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hourly | fixed 6.0 | 44.44 | - | 52.7 | 6.30 | 5.78 | 5.86 | 0 | 0 |
| hourly | rectangle 60min s=1 | 38.89 | -12.5% | 53.5 | 6.78 | 5.63 | 6.21 | 39 | 1 |
| hourly | rectangle 180min s=3 | 30.88 | -30.5% | 56.2 | 7.77 | 5.27 | 6.08 | 59 | 2 |
| hourly | triangle 180min s=3 | 33.68 | -24.2% | 55.7 | 7.59 | 5.55 | 6.22 | 52 | 2 |
| hourly | triangle 360min s=1 | 35.82 | -19.4% | 53.5 | 6.70 | 5.44 | 6.11 | 65 | 3 |
| hourly | triangle 360min s=3 | 29.86 | -32.8% | 56.0 | 7.63 | 5.28 | 6.14 | 65 | 3 |
| quarter | rectangle 60min s=1 | 39.47 | -11.2% | 53.3 | 6.64 | 5.69 | 6.17 | 18 | 0 |
| quarter | triangle 180min s=3 | 33.61 | -24.4% | 54.8 | 7.53 | 5.57 | 6.11 | 38 | 1 |
| quarter | triangle 360min s=3 | 29.88 | -32.8% | 55.5 | 7.57 | 5.37 | 6.15 | 60 | 3 |
| dst_fall | fixed 6.0 | 11.96 | - | 14.3 | 6.35 | 5.78 | 6.02 | 0 | 0 |
| dst_fall | triangle 180min s=3 | 9.28 | -22.4% | 17.3 | 7.61 | 5.79 | 6.02 | 54 | 2 |
| dst_fall | triangle 360min s=3 | 8.05 | -32.7% | 18.3 | 7.79 | 5.79 | 6.06 | 64 | 4 |
| dst_spring | fixed 6.0 | 17.43 | - | 22.8 | 6.30 | 5.78 | 6.00 | 0 | 0 |
| dst_spring | triangle 180min s=3 | 11.42 | -34.5% | 25.3 | 7.30 | 5.56 | 6.11 | 53 | 3 |
| dst_spring | triangle 360min s=3 | 10.65 | -38.9% | 25.8 | 7.73 | 5.75 | 5.86 | 66 | 4 |
| negative | fixed 6.0 | 37.38 | - | 52.7 | 6.30 | 5.78 | 5.86 | 0 | 0 |
| negative | triangle 180min s=3 | 16.03 | -57.1% | 56.0 | 7.79 | 5.43 | 6.02 | 60 | 2 |
| negative | triangle 360min s=3 | 14.22 | -61.9% | 56.0 | 7.78 | 5.17 | 5.79 | 74 | 3 |
| flat | fixed 6.0 | 42.14 | - | 52.7 | 6.30 | 5.78 | 5.86 | 0 | 0 |
| flat | triangle 360min s=3 | 42.14 | +0.0% | 52.7 | 6.30 | 5.78 | 5.86 | 0 | 3 |
| gap | triangle 180min s=3 | 34.56 | -22.2% | 55.7 | 7.59 | 5.78 | 6.20 | 35 | 38 |
| gap | triangle 360min s=3 | 31.33 | -29.5% | 56.0 | 7.68 | 5.78 | 6.12 | 38 | 46 |

Forecast-gap sensitivity (same true prices, `gap` vs `hourly`): cost rises by
+2.5 % to +9.4 % depending on strategy (for example triangle 360min s=1: 35.82
to 39.20 SEK, +9.4 %; rectangle 60min s=3: +2.5 %). Longer lookaheads and low
sensitivities lose proportionally more.

## What the simulation shows, and what it does not

Under these invented assumptions:

- With a large, regular daily price spread, shifting reduced simulated cost in
  every scenario that has a spread, by about 7 % to 62 %. Those numbers are an
  artefact of the invented spread (night 0.28 vs evening peak 1.5 SEK/kWh plus
  noise) and should not be read as expected savings. Real spreads and the
  presence of fixed fees will change them, possibly to zero.
- **Negative result, flat prices:** with no spread (`flat`) the optimizer never
  adjusts the target and cost is identical to the baseline (+0.0 %). Shifting
  gives nothing when there is nothing to shift.
- Energy use goes up with shifting (up to about +4 kWh, roughly 0 % to +28 %
  in these runs; more in the short DST scenarios) because a warmer room loses
  more heat. Savings come from moving consumption to cheaper hours, and
  whether they exceed the extra energy depends on the spread.
- With a 60-minute lookahead the rectangle and triangle filters are identical
  (the lookahead covers one interval), so differences between filters only
  appear at 180 and 360 minutes. There the rectangle filter was sometimes
  cheaper and sometimes dearer than the triangle filter (for example 30.88 vs
  33.68 SEK at 180 min and s=3 in `hourly`, but 35.82 vs 36.06 SEK at 360 min
  and s=1); one seed and one price model do not establish a general ranking.
- Peak temperature approaches the 7.5 C limit plus hysteresis at high
  sensitivity (up to 7.79 C). The minimum temperature fell as low as 5.09 C in
  `negative` with a 5.3 C configured minimum, because the 0.2 C hysteresis is
  applied around the effective target and the heater has finite power. The
  configured minimum is a target bound, not a guaranteed room minimum; a frost
  protection margin must account for hysteresis and heater capability.
- 15-minute vs hourly data of the same prices give similar but not identical
  costs, because the current interval (and so the forecast anchor) differs
  within the hour. They are exactly equivalent only when the forecast anchor
  coincides (tested in `tests/test_benchmark.py`).
- Cost is not corrected for the stored heat difference between start and end;
  final temperatures differ by up to about 0.4 C (about 0.6 kWh of 50), which
  is small relative to the differences above but is a limitation.
- The thermal model is a single lumped node with constant parameters, no
  solar gain, no humidity or stored-goods effects, an ideal heater, and a
  perfect observation of room temperature. Forecast prices equal true prices
  except in the explicit `gap` scenario.

## Using your own exported data (not committed)

The repository contains no personal or measured data and none should be added.
To benchmark against your own export:

1. Export prices (for example from your price provider or Home Assistant
   history) and indoor/outdoor temperatures to local files outside the
   repository, or under a git-ignored path such as `benchmarks/local/`
   (add it to your own `.git/info/exclude`, not to the shared repository).
2. Convert prices to `PriceInterval` objects (UTC-aware start/end, SEK/kWh)
   in a local script, and build a `benchmarks.fixtures.Scenario` with
   `true_prices`, `visible_prices` (what the optimizer would have known at the
   time; drop intervals to model gaps) and an outdoor mean.
3. To use measured outdoor temperature rather than the synthetic profile,
   pass your own function in place of `outdoor_temperature_c` in a local copy
   of `simulate()`. Fit `loss_kw_per_k` and `capacity_kwh_per_k` in
   `ModelParams` to your room, for example from a heater-off cooling curve.
4. Report such results as "simulation on measured price/temperature inputs",
   keeping the thermal model labelled as an assumption. Measured inputs through
   a model are still not measured savings; only a controlled comparison on the
   real installation can show that.
