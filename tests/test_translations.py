"""Consistency checks for manifest, strings, translations, icons and services.

These are filesystem-level checks, so they run without Home Assistant. They
catch the mistakes that are easy to make and annoying to find: a service option
that has no translation, a drink added to the enum but not to the picker, an
icon for an entity that no longer exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from custom_components.melitta_barista_ts.const import (
    DOMAIN,
    HOPPER_OPTIONS,
    INTENSITY_OPTIONS,
    MAINTENANCE_PROGRAMS,
    PROCESS_OPTIONS,
    RECIPE_KEYS,
    SERVICE_UUID,
    TEMPERATURE_OPTIONS,
)

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN


def load_json(name: str) -> dict:
    """Read one of the integration's JSON files."""
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def strings() -> dict:
    """Return the English source strings."""
    return load_json("strings.json")


@pytest.fixture(scope="module")
def services() -> dict:
    """Return the service descriptions."""
    return yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))


def key_tree(value: object) -> object:
    """Reduce a nested dict to its key structure, dropping the values."""
    if isinstance(value, dict):
        return {key: key_tree(inner) for key, inner in value.items()}
    return None


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_manifest_is_consistent_with_the_code() -> None:
    """The manifest domain and discovery UUID match the constants."""
    manifest = load_json("manifest.json")

    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    assert manifest["dependencies"] == ["bluetooth_adapters"]
    assert manifest["bluetooth"] == [
        {"service_uuid": SERVICE_UUID, "connectable": True}
    ]
    for field in ("name", "documentation", "issue_tracker", "version", "codeowners"):
        assert manifest[field], f"manifest is missing {field}"


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------


def test_english_translation_matches_strings(strings: dict) -> None:
    """``translations/en.json`` is a copy of ``strings.json``."""
    assert load_json("translations/en.json") == strings


LANGUAGES = [path.stem for path in sorted((COMPONENT / "translations").glob("*.json"))]


@pytest.mark.parametrize("language", LANGUAGES)
def test_translation_has_the_same_keys_as_strings(strings: dict, language: str) -> None:
    """Every translation covers exactly the English key structure."""
    translation = load_json(f"translations/{language}.json")
    assert key_tree(translation) == key_tree(strings), (
        f"{language}.json has a different key structure than strings.json"
    )


def test_translations_have_no_empty_values() -> None:
    """No translation ships a blank string."""

    def walk(node: object, path: str) -> list[str]:
        if isinstance(node, dict):
            return [
                problem
                for key, value in node.items()
                for problem in walk(value, f"{path}.{key}")
            ]
        return [] if str(node).strip() else [path]

    for path in sorted((COMPONENT / "translations").glob("*.json")):
        empty = walk(load_json(f"translations/{path.name}"), path.stem)
        assert not empty, f"empty translations: {empty}"


# ---------------------------------------------------------------------------
# Recipes and options
# ---------------------------------------------------------------------------


def test_every_recipe_has_a_name(strings: dict) -> None:
    """Each drink is translated for the select and for the service picker."""
    select_states = set(strings["entity"]["select"]["recipe"]["state"])
    selector_options = set(strings["selector"]["recipe"]["options"])

    assert select_states == set(RECIPE_KEYS.values())
    assert selector_options == set(RECIPE_KEYS.values())


def test_recipe_keys_are_unique() -> None:
    """Drink slugs are unique, so the select cannot lose an option."""
    assert len(set(RECIPE_KEYS.values())) == len(RECIPE_KEYS)


@pytest.mark.parametrize(
    ("selector", "options"),
    [
        ("process", PROCESS_OPTIONS),
        ("intensity", INTENSITY_OPTIONS),
        ("temperature", TEMPERATURE_OPTIONS),
        ("hopper", HOPPER_OPTIONS),
        ("program", MAINTENANCE_PROGRAMS),
    ],
)
def test_selector_options_are_translated(
    strings: dict, selector: str, options: dict
) -> None:
    """Each selector's options match the constants behind them."""
    assert set(strings["selector"][selector]["options"]) == set(options)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def test_services_are_documented(strings: dict, services: dict) -> None:
    """Every service and every field has a name and a description."""
    assert set(services) == set(strings["services"])

    for name, service in services.items():
        described = strings["services"][name]
        assert described["name"] and described["description"]
        assert set(service.get("fields", {})) == set(described.get("fields", {})), (
            f"field mismatch for service {name}"
        )
        for field in described.get("fields", {}).values():
            assert field["name"] and field["description"]


def test_service_selector_options_match_the_constants(services: dict) -> None:
    """The YAML pickers list exactly the values the code accepts."""
    expected = {
        ("brew", "recipe"): set(RECIPE_KEYS.values()),
        ("brew_custom", "process"): set(PROCESS_OPTIONS),
        ("brew_custom", "intensity"): set(INTENSITY_OPTIONS),
        ("brew_custom", "temperature"): set(TEMPERATURE_OPTIONS),
        ("brew_custom", "hopper"): set(HOPPER_OPTIONS),
        ("start_maintenance", "program"): set(MAINTENANCE_PROGRAMS),
    }
    for (service, field), values in expected.items():
        options = services[service]["fields"][field]["selector"]["select"]["options"]
        assert set(options) == values, f"{service}.{field} options are out of date"


def test_every_service_targets_a_device(services: dict) -> None:
    """Every service takes a device, and restricts the picker to this domain."""
    for name, service in services.items():
        device = service["fields"]["device_id"]
        assert device["required"] is True, name
        assert device["selector"]["device"]["integration"] == DOMAIN, name


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------


def test_icons_match_translated_entities(strings: dict) -> None:
    """Icons exist for exactly the entities that have names."""
    icons = load_json("icons.json")

    for platform, entities in strings["entity"].items():
        assert set(icons["entity"].get(platform, {})) == set(entities), (
            f"icon keys for {platform} do not match the entity keys"
        )

    assert set(icons["services"]) == set(strings["services"])
