"""Consistency checks between the code, strings.json and services.yaml."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from custom_components.melitta_barista_ts import binary_sensor, sensor
from custom_components.melitta_barista_ts.const import (
    BLEND_SLUGS,
    INTENSITY_SLUGS,
    RECIPE_SLUGS,
    TEMPERATURE_SLUGS,
)

COMPONENT = Path(__file__).parent.parent / "custom_components" / "melitta_barista_ts"
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
SERVICES = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
TRANSLATIONS = sorted((COMPONENT / "translations").glob("*.json"))


def _key_paths(obj: dict, prefix: str = "") -> set[str]:
    paths = set()
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        if isinstance(value, dict):
            paths |= _key_paths(value, path)
    return paths


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_translation_matches_strings(path: Path) -> None:
    """Every translation carries exactly the keys strings.json declares."""
    translation = json.loads(path.read_text(encoding="utf-8"))
    expected = _key_paths(STRINGS)
    actual = _key_paths(translation)

    assert not expected - actual, f"{path.name} is missing {sorted(expected - actual)}"
    assert not actual - expected, f"{path.name} has extra {sorted(actual - expected)}"


def test_services_yaml_and_strings_agree() -> None:
    """Each service and field in services.yaml is documented in strings.json."""
    assert set(SERVICES) == set(STRINGS["services"])

    for name, spec in SERVICES.items():
        fields = set((spec or {}).get("fields", {}))
        documented = set(STRINGS["services"][name].get("fields", {}))
        assert fields == documented, f"{name}: {fields ^ documented}"


def test_sensor_enum_options_are_translated() -> None:
    """Enum sensors only report states strings.json can render."""
    for description in sensor.SENSORS:
        if not description.options:
            continue
        states = set(STRINGS["entity"]["sensor"][description.translation_key]["state"])
        assert set(description.options) == states, description.key


@pytest.mark.parametrize(
    ("translation_key", "slugs"),
    [
        ("drink", set(RECIPE_SLUGS.values())),
        ("intensity", set(INTENSITY_SLUGS.values())),
        ("brew_temperature", set(TEMPERATURE_SLUGS.values())),
        ("bean_hopper", set(BLEND_SLUGS.values())),
    ],
)
def test_select_options_are_translated(translation_key: str, slugs: set[str]) -> None:
    """Select options match both the entity strings and the service selectors."""
    assert set(STRINGS["entity"]["select"][translation_key]["state"]) == slugs
    assert set(STRINGS["selector"][translation_key]["options"]) == slugs


def test_service_selector_options_match_the_code() -> None:
    """The drink list offered by the brew service is the full recipe set."""
    options = set(SERVICES["brew"]["fields"]["drink"]["selector"]["select"]["options"])
    assert options == set(RECIPE_SLUGS.values())


def test_every_entity_and_service_has_an_icon() -> None:
    """Nothing falls back to the bare domain icon in the dashboard."""
    icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))

    for platform, keys in STRINGS["entity"].items():
        assert set(keys) == set(icons["entity"].get(platform, {})), platform

    assert set(STRINGS["services"]) == set(icons["services"])


def test_every_entity_translation_key_is_declared() -> None:
    """No platform references a translation key strings.json does not define."""
    declared_sensors = set(STRINGS["entity"]["sensor"])
    for description in sensor.SENSORS:
        assert description.translation_key in declared_sensors

    declared_binary = set(STRINGS["entity"]["binary_sensor"])
    for description in binary_sensor.BINARY_SENSORS:
        assert description.translation_key in declared_binary

    # The per-drink counter sensors share one templated key.
    assert "drink_cups" in declared_sensors
