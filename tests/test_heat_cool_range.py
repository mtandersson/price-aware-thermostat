"""Pure heat_cool direction selection and range price adjustment."""

import pytest

from custom_components.price_aware_thermostat.ac_control import RangeSelector, adjust_range


def test_heats_below_low_cools_above_high_and_idles_between() -> None:
    sel = RangeSelector(changeover=0)
    assert sel.select(21.7, 22, 24, 0.3, 0.3, 0) == "heat"
    assert sel.select(22.2, 22, 24, 0.3, 0.3, 1) == "heat"  # hysteresis keeps heating
    assert sel.select(22.3, 22, 24, 0.3, 0.3, 2) == "off"
    assert sel.status == "idle"
    assert sel.select(24.2, 22, 24, 0.3, 0.3, 3) == "off"  # inside the cooling band
    assert sel.select(24.3, 22, 24, 0.3, 0.3, 4) == "cool"
    assert sel.select(23.8, 22, 24, 0.3, 0.3, 5) == "cool"
    assert sel.select(23.7, 22, 24, 0.3, 0.3, 6) == "off"


def test_never_switches_directly_and_waits_for_the_changeover() -> None:
    sel = RangeSelector(changeover=1800)
    assert sel.select(21.0, 22, 24, 0.3, 0.3, 0) == "heat"
    # Even a sudden hot reading only ends heating; it never starts cooling.
    assert sel.select(25.0, 22, 24, 0.3, 0.3, 10) == "off"
    assert sel.status == "changeover_wait"
    assert sel.select(25.0, 22, 24, 0.3, 0.3, 1809) == "off"
    assert sel.select(25.0, 22, 24, 0.3, 0.3, 1810) == "cool"
    # Back to heat also waits.
    assert sel.select(21.0, 22, 24, 0.3, 0.3, 1900) == "off"
    assert sel.status == "changeover_wait"
    assert sel.select(21.0, 22, 24, 0.3, 0.3, 1900 + 1800) == "heat"
    # The same direction may restart without waiting.
    sel.stop(4000)
    assert sel.select(21.0, 22, 24, 0.3, 0.3, 4001) == "heat"


def test_locked_direction_never_starts_and_the_other_is_not_used() -> None:
    sel = RangeSelector(changeover=0)
    assert sel.select(26.0, 22, 24, 0.3, 0.3, 0, cool_allowed=False) == "off"
    assert sel.status == "outdoor_lockout"
    assert sel.select(20.0, 22, 24, 0.3, 0.3, 1, heat_allowed=False) == "off"
    assert sel.status == "outdoor_lockout"
    assert sel.select(20.0, 22, 24, 0.3, 0.3, 2) == "heat"
    assert sel.select(20.0, 22, 24, 0.3, 0.3, 3, heat_allowed=False) == "off"
    assert sel.direction == "off"


def test_resume_stop_and_validation() -> None:
    sel = RangeSelector()
    sel.resume("cool", 5)
    assert sel.direction == "cool" and sel.last_active_at == 5
    sel.resume("off", 6)
    assert sel.direction == "cool"
    sel.stop(7)
    sel.stop(8)
    assert sel.direction == "off" and sel.last_active_at == 7
    with pytest.raises(ValueError):
        sel.select(22.0, 24, 22, 0.3, 0.3, 0)
    with pytest.raises(ValueError):
        sel.select(float("nan"), 22, 24, 0.3, 0.3, 0)
    with pytest.raises(ValueError):
        RangeSelector(changeover=-1)


def test_adjust_range_keeps_the_gap() -> None:
    assert adjust_range(22, 24, 21.2, 24.8, 1.0) == (21.2, 24.8, False)
    assert adjust_range(22, 24, 22.8, 23.2, 1.0) == (22.5, 23.5, True)
    assert adjust_range(22, 24, 22.2, 23.8, 1.0) == (22.2, 23.8, False)
    # A range exactly the gap wide admits no inward adjustment.
    assert adjust_range(22, 23, 22.4, 22.6, 1.0) == (22.0, 23.0, True)
