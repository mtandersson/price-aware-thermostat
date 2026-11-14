"""Exceptions shown to users must be translatable and registered in strings.json."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant.common import MockConfigEntry

from custom_components.price_aware_thermostat.const import (
    CONF_AREA,
    CONF_BASE_TEMP,
    CONF_HEATER,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NORDPOOL_ENTRY,
    CONF_SENSOR,
    CONF_SHADOW,
    DOMAIN,
)

COMPONENT = Path(__file__).parents[2] / "custom_components" / "price_aware_thermostat"


async def test_translated_exceptions_carry_keys_and_placeholders(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Translations",
        data={
            CONF_SENSOR: "sensor.tr_temperature",
            CONF_HEATER: "switch.tr_heater",
            CONF_NORDPOOL_ENTRY: "fake-source",
            CONF_AREA: "SE4",
            CONF_MIN_TEMP: 5.0,
            CONF_BASE_TEMP: 6.0,
            CONF_MAX_TEMP: 8.0,
        },
        options={CONF_SHADOW: True},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.tr_temperature", "6.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.tr_heater", "off")
    with patch(
        "custom_components.price_aware_thermostat.coordinator."
        "NordPoolPriceCoordinator._async_update_data",
        return_value=(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    entity = hass.data["entity_components"][CLIMATE_DOMAIN].get_entity(
        "climate.translations"
    )
    assert entity is not None

    with pytest.raises(ServiceValidationError) as temp:
        await entity.async_set_temperature(temperature=15.0)
    assert temp.value.translation_domain == DOMAIN
    assert temp.value.translation_key == "temperature_out_of_range"
    assert temp.value.translation_placeholders == {"min_temp": "5.0", "max_temp": "8.0"}

    with pytest.raises(ServiceValidationError) as mode:
        await entity.async_set_hvac_mode("cool")
    assert mode.value.translation_domain == DOMAIN
    assert mode.value.translation_key == "unsupported_hvac_mode"
    assert mode.value.translation_placeholders == {"hvac_mode": "cool"}

    with pytest.raises(ServiceValidationError) as preset:
        await entity.async_set_preset_mode("turbo")
    assert preset.value.translation_domain == DOMAIN
    assert preset.value.translation_key == "unsupported_preset_mode"
    assert preset.value.translation_placeholders == {"preset_mode": "turbo"}
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_every_translation_key_in_code_exists_in_strings() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert strings == english
    used: set[str] = set()
    for path in COMPONENT.glob("*.py"):
        used |= set(re.findall(r'translation_domain=DOMAIN,\s*translation_key="([^"]+)"', path.read_text()))
    assert used
    assert used <= set(strings["exceptions"])
    for key in used:
        message = strings["exceptions"][key]["message"]
        assert set(re.findall(r"{(\w+)}", message)) <= {
            "min_temp",
            "max_temp",
            "hvac_mode",
            "preset_mode",
        }
