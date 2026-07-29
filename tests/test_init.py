"""Tests for setup, the coordinator and the entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista_ts.client import MelittaConnectionError
from custom_components.melitta_barista_ts.const import (
    DOMAIN,
    SERVICE_BREW,
    SERVICE_CANCEL,
    Intensity,
    RecipeId,
)
from custom_components.melitta_barista_ts.protocol import (
    MachineRecipe,
    MachineStatus,
    RecipeComponent,
)

from .conftest import ADDRESS

CLIENT_PATH = "custom_components.melitta_barista_ts.MelittaBleClient"
BLE_DEVICE_PATH = (
    "custom_components.melitta_barista_ts.bluetooth.async_ble_device_from_address"
)

READY = MachineStatus(
    process=2, sub_process=0, info_messages=0, manipulation=0, progress=0
)
BREWING = MachineStatus(
    process=4, sub_process=1, info_messages=0, manipulation=0, progress=25
)
NEEDS_WATER = MachineStatus(
    process=2, sub_process=0, info_messages=0, manipulation=4, progress=0
)

ESPRESSO = MachineRecipe(
    recipe_id=200,
    recipe_type=0,
    component1=RecipeComponent(
        process=1, shots=1, blend=1, intensity=3, aroma=0, temperature=2, portion=8
    ),
    component2=RecipeComponent(process=0, shots=0, blend=0, intensity=0, temperature=2),
)


def _make_client(status: MachineStatus = READY) -> MagicMock:
    """A stand-in for the BLE client that answers from memory."""
    machine = MagicMock()
    machine.read_status = AsyncMock(return_value=status)
    machine.read_firmware = AsyncMock(return_value="EF_1.00R4__386")
    machine.read_recipe = AsyncMock(return_value=ESPRESSO)
    machine.read_numerical = AsyncMock(return_value=7)
    machine.write_numerical = AsyncMock()
    machine.brew = AsyncMock()
    machine.cancel_process = AsyncMock()

    client = MagicMock()
    client.machine = machine
    client.connected = True
    client.async_connect = AsyncMock()
    client.async_disconnect = AsyncMock()
    client.async_close = AsyncMock()
    client.add_status_listener = MagicMock(return_value=lambda: None)

    async def _run(action, *args, **kwargs):
        return await action(*args, **kwargs)

    client.async_run = AsyncMock(side_effect=_run)
    return client


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        title="Melitta Barista TS Smart",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock):
    with (
        patch(BLE_DEVICE_PATH, return_value=MagicMock()),
        patch(CLIENT_PATH, return_value=client),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_setup_and_unload(hass: HomeAssistant, config_entry) -> None:
    client = _make_client()
    await _setup(hass, config_entry, client)

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_BREW)
    assert hass.services.has_service(DOMAIN, SERVICE_CANCEL)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    client.async_close.assert_awaited()


async def test_setup_retries_when_machine_not_in_range(
    hass: HomeAssistant, config_entry
) -> None:
    with patch(BLE_DEVICE_PATH, return_value=None):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_when_first_connect_fails(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client()
    client.async_connect = AsyncMock(side_effect=MelittaConnectionError("nope"))
    await _setup(hass, config_entry, client)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_status_entities_reflect_the_machine(
    hass: HomeAssistant, config_entry
) -> None:
    await _setup(hass, config_entry, _make_client(BREWING))

    assert hass.states.get("sensor.melitta_barista_ts_smart_status").state == "brewing"
    assert hass.states.get("sensor.melitta_barista_ts_smart_step").state == "grinding"
    assert hass.states.get("sensor.melitta_barista_ts_smart_progress").state == "25"
    assert (
        hass.states.get("binary_sensor.melitta_barista_ts_smart_brewing").state == "on"
    )


async def test_manipulation_maps_to_a_problem_sensor(
    hass: HomeAssistant, config_entry
) -> None:
    await _setup(hass, config_entry, _make_client(NEEDS_WATER))

    state = hass.states.get("binary_sensor.melitta_barista_ts_smart_water_tank_empty")
    assert state.state == "on"
    assert (
        hass.states.get("sensor.melitta_barista_ts_smart_needs_attention").state
        == "fill_water"
    )


async def test_brew_button_is_unavailable_while_busy(
    hass: HomeAssistant, config_entry
) -> None:
    await _setup(hass, config_entry, _make_client(BREWING))

    assert (
        hass.states.get("button.melitta_barista_ts_smart_brew").state
        == STATE_UNAVAILABLE
    )
    assert (
        hass.states.get("button.melitta_barista_ts_smart_cancel").state
        != STATE_UNAVAILABLE
    )


async def test_brew_button_starts_the_selected_drink(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client()
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_brew"},
        blocking=True,
    )

    client.machine.brew.assert_awaited_once()
    args, kwargs = client.machine.brew.await_args
    assert args[0] is RecipeId.ESPRESSO
    assert kwargs["two_cups"] is False


async def test_selecting_a_drink_loads_its_stored_parameters(
    hass: HomeAssistant, config_entry
) -> None:
    """Picking a drink seeds strength, temperature and cup size from the machine."""
    client = _make_client()
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.melitta_barista_ts_smart_drink",
            "option": "cappuccino",
        },
        blocking=True,
    )

    client.machine.read_recipe.assert_awaited_with(int(RecipeId.CAPPUCCINO))
    assert hass.states.get("select.melitta_barista_ts_smart_strength").state == "strong"
    assert (
        hass.states.get("select.melitta_barista_ts_smart_brew_temperature").state
        == "high"
    )
    assert hass.states.get("number.melitta_barista_ts_smart_cup_size").state == "40.0"


async def test_staged_settings_are_applied_to_the_brew(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client()
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.melitta_barista_ts_smart_strength",
            "option": "very_mild",
        },
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.melitta_barista_ts_smart_cup_size", "value": 120},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.melitta_barista_ts_smart_two_cups"},
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_brew"},
        blocking=True,
    )

    _, kwargs = client.machine.brew.await_args
    assert kwargs["intensity"] == int(Intensity.VERY_MILD)
    assert kwargs["portion_ml"] == 120
    assert kwargs["two_cups"] is True


async def test_brew_service_overrides_staged_settings(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client()
    await _setup(hass, config_entry, client)

    device_registry_entry = next(
        iter(
            entity
            for entity in hass.states.async_entity_ids("button")
            if "brew" in entity
        )
    )
    assert device_registry_entry

    await hass.services.async_call(
        DOMAIN,
        SERVICE_BREW,
        {
            "entity_id": "button.melitta_barista_ts_smart_brew",
            "drink": "flat_white",
            "intensity": "very_strong",
            "portion_ml": 200,
        },
        blocking=True,
    )

    _, kwargs = client.machine.brew.await_args
    args, _ = client.machine.brew.await_args
    assert args[0] is RecipeId.FLAT_WHITE
    assert kwargs["intensity"] == int(Intensity.VERY_STRONG)
    assert kwargs["portion_ml"] == 200


async def test_cancel_service_stops_the_current_process(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client(BREWING)
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CANCEL,
        {"entity_id": "button.melitta_barista_ts_smart_cancel"},
        blocking=True,
    )

    client.machine.cancel_process.assert_awaited_once_with(4)
