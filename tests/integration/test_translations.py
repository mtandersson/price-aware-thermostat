"""Translation files must stay structurally in sync and document every field."""

import json
import re
from pathlib import Path
from typing import Any

COMPONENT = Path(__file__).parents[2] / "custom_components" / "price_aware_thermostat"
TRANSLATIONS = COMPONENT / "translations"
FORM_STEPS = (("config", "user"), ("config", "reconfigure"), ("options", "init"))


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((TRANSLATIONS / name).read_text(encoding="utf-8"))
    return data


def _key_paths(node: Any, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    paths: set[str] = set()
    for key, value in node.items():
        paths |= _key_paths(value, f"{prefix}/{key}")
    return paths


def _placeholders(node: Any) -> dict[str, set[str]]:
    return {
        path: set(re.findall(r"\{(\w+)\}", value))
        for path, value in _leaves(node).items()
    }


def _leaves(node: Any, prefix: str = "") -> dict[str, str]:
    if not isinstance(node, dict):
        return {prefix: node}
    leaves: dict[str, str] = {}
    for key, value in node.items():
        leaves.update(_leaves(value, f"{prefix}/{key}"))
    return leaves


def test_swedish_has_exactly_the_english_key_structure() -> None:
    assert _key_paths(_load("sv.json")) == _key_paths(_load("en.json"))


def test_swedish_keeps_placeholders_and_has_no_empty_strings() -> None:
    swedish = _load("sv.json")
    assert _placeholders(swedish) == _placeholders(_load("en.json"))
    assert all(isinstance(v, str) and v.strip() for v in _leaves(swedish).values())


def test_english_matches_strings_json() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    assert _load("en.json") == strings


def test_every_form_field_has_a_description() -> None:
    english = _load("en.json")
    for section, step_id in FORM_STEPS:
        step = english[section]["step"][step_id]
        assert set(step["data_description"]) == set(step["data"]), step_id
