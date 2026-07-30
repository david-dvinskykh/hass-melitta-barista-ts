"""End-to-end tests against the Home Assistant test harness.

These need ``pytest-homeassistant-custom-component``; when it is absent (for
example when only the protocol tests are being run) the whole module is
skipped.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.melitta_barista_ts.const import (
    DOMAIN,
    MachineProcess,
    MachineType,
    Recipe,
    Setting,
    SubProcess,
)
from custom_components.melitta_barista_ts.protocol import MachineStatus

from .conftest import ADDRESS


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> MockConfigEntry:
    """Return a config entry that has been set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        title="Melitta Barista TS Smart",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def poll_again(hass: HomeAssistant) -> None:
    """Advance past the poll interval so one more refresh runs."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()


async def test_setup_and_unload(hass: HomeAssistant, loaded_entry) -> None:
    """The entry loads and unloads cleanly."""
    assert loaded_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_out_of_range(
    hass: HomeAssistant, mock_client
) -> None:
    """Setup is retried when the machine has not been seen."""
    with patch(
        "custom_components.melitta_barista_ts.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        entry = MockConfigEntry(
            domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_registry_entry(hass: HomeAssistant, loaded_entry) -> None:
    """The machine shows up as one device with its model and firmware."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, ADDRESS)})

    assert device is not None
    assert device.manufacturer == "Melitta"
    assert device.model == "Barista TS Smart"
    assert device.sw_version == "1.10.5"
    assert device.serial_number == "SN12345678"


async def test_every_platform_creates_entities(
    hass: HomeAssistant, loaded_entry
) -> None:
    """Each platform registers its entities."""
    entities = er.async_entries_for_config_entry(
        er.async_get(hass), loaded_entry.entry_id
    )
    by_domain: dict[str, int] = {}
    for entity in entities:
        by_domain[entity.domain] = by_domain.get(entity.domain, 0) + 1

    for platform in (
        Platform.SENSOR,
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.SELECT,
        Platform.NUMBER,
        Platform.SWITCH,
        Platform.TIME,
    ):
        assert by_domain.get(platform.value), f"no {platform.value} entities"

    # 24 drink counters ship disabled, so they are registered but not stated.
    assert by_domain[Platform.SENSOR.value] == 6 + 24


async def test_state_sensors_reflect_the_status(
    hass: HomeAssistant, loaded_entry
) -> None:
    """Status fields land on the right entities."""
    assert hass.states.get("sensor.melitta_barista_ts_smart_state").state == "ready"
    assert hass.states.get("sensor.melitta_barista_ts_smart_activity").state == "idle"
    assert hass.states.get("sensor.melitta_barista_ts_smart_progress").state == "0"
    brewing = hass.states.get("binary_sensor.melitta_barista_ts_smart_brewing")
    assert brewing.state == "off"


async def test_counters_are_read_after_setup_not_during_it(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """The 25-register counter sweep is deferred so setup stays quick."""
    mock_client.async_read_counters.assert_not_awaited()
    assert (
        hass.states.get("sensor.melitta_barista_ts_smart_drinks_total").state
        == "unknown"
    )

    await poll_again(hass)

    mock_client.async_read_counters.assert_awaited()
    assert (
        hass.states.get("sensor.melitta_barista_ts_smart_drinks_total").state == "1234"
    )


async def test_counters_are_skipped_while_brewing(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """A preparation keeps the BLE slot free of the counter sweep."""
    mock_client.async_update_status.return_value = MachineStatus(
        process=MachineProcess.PRODUCT, sub_process=SubProcess.COFFEE, progress=50
    )

    await poll_again(hass)

    mock_client.async_read_counters.assert_not_awaited()


async def test_settings_reach_their_entities(hass: HomeAssistant, loaded_entry) -> None:
    """Settings registers populate the control entities."""
    hardness = hass.states.get("number.melitta_barista_ts_smart_water_hardness")
    assert hardness.state == "3.0"
    assert (
        hass.states.get("switch.melitta_barista_ts_smart_energy_saving").state == "on"
    )
    # RINSING_OFF is 0, so "rinse when switching on" is enabled.
    assert (
        hass.states.get("switch.melitta_barista_ts_smart_rinse_when_switching_on").state
        == "on"
    )
    assert (
        hass.states.get("select.melitta_barista_ts_smart_brew_temperature").state
        == "normal"
    )
    assert hass.states.get("time.melitta_barista_ts_smart_clock").state == "08:30:00"


async def test_pushed_status_updates_entities(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """A notification pushed by the machine updates the states immediately."""
    coordinator = loaded_entry.runtime_data
    coordinator._handle_pushed_status(
        MachineStatus(
            process=MachineProcess.PRODUCT,
            sub_process=SubProcess.GRINDING,
            progress=35,
        )
    )
    await hass.async_block_till_done()

    assert hass.states.get("sensor.melitta_barista_ts_smart_state").state == "product"
    assert (
        hass.states.get("sensor.melitta_barista_ts_smart_activity").state == "grinding"
    )
    assert hass.states.get("sensor.melitta_barista_ts_smart_progress").state == "35"
    brewing = hass.states.get("binary_sensor.melitta_barista_ts_smart_brewing")
    assert brewing.state == "on"


async def test_brew_button_uses_the_selected_recipe(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """Pressing brew prepares whatever the drink select holds."""
    mock_client.selected_recipe = Recipe.CAPPUCCINO

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_brew"},
        blocking=True,
    )

    mock_client.async_brew.assert_awaited_once_with(Recipe.CAPPUCCINO)


async def test_recipe_select_options_and_selection(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """The drink select offers the model's drinks and remembers the choice."""
    state = hass.states.get("select.melitta_barista_ts_smart_drink")
    assert state is not None
    assert len(state.attributes["options"]) == 24
    assert "dead_eye" in state.attributes["options"]

    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.melitta_barista_ts_smart_drink",
            "option": "flat_white",
        },
        blocking=True,
    )

    assert mock_client.selected_recipe is Recipe.FLAT_WHITE


async def test_profile_select_uses_machine_names(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """Profiles are labelled with the names stored on the machine."""
    state = hass.states.get("select.melitta_barista_ts_smart_profile")
    assert state.attributes["options"][:3] == ["my_coffee", "David", "Guest"]

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.melitta_barista_ts_smart_profile", "option": "Guest"},
        blocking=True,
    )

    assert mock_client.active_profile == 2


async def test_number_write(hass: HomeAssistant, loaded_entry, mock_client) -> None:
    """Setting a number writes the register."""
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": "number.melitta_barista_ts_smart_water_hardness",
            "value": 2,
        },
        blocking=True,
    )

    mock_client.async_write_setting.assert_awaited_once_with(Setting.WATER_HARDNESS, 2)


async def test_switch_inversion(hass: HomeAssistant, loaded_entry, mock_client) -> None:
    """Turning off "rinse when switching on" sets the RINSING_OFF register."""
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.melitta_barista_ts_smart_rinse_when_switching_on"},
        blocking=True,
    )

    mock_client.async_write_setting.assert_awaited_once_with(Setting.RINSING_OFF, 1)


async def test_clock_write(hass: HomeAssistant, loaded_entry, mock_client) -> None:
    """The clock is written as minutes since midnight."""
    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": "time.melitta_barista_ts_smart_clock", "time": "07:45:00"},
        blocking=True,
    )

    mock_client.async_write_setting.assert_awaited_once_with(
        Setting.CLOCK_WRITE, 7 * 60 + 45
    )


async def test_maintenance_button(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """Maintenance buttons start the matching program."""
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_descale"},
        blocking=True,
    )

    mock_client.async_start_maintenance.assert_awaited_once_with(
        MachineProcess.DESCALING
    )


async def test_brew_service(hass: HomeAssistant, loaded_entry, mock_client) -> None:
    """The brew service targets the device and passes the drink through."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, ADDRESS)})

    await hass.services.async_call(
        DOMAIN,
        "brew",
        {"device_id": device.id, "recipe": "latte_macchiato", "two_cups": True},
        blocking=True,
    )

    mock_client.async_brew.assert_awaited_once_with(
        Recipe.LATTE_MACCHIATO, two_cups=True
    )


async def test_brew_custom_service(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """The custom brew service maps its options onto protocol values."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, ADDRESS)})

    await hass.services.async_call(
        DOMAIN,
        "brew_custom",
        {
            "device_id": device.id,
            "process": "coffee",
            "portion": 40,
            "milk_portion": 100,
            "intensity": "strong",
            "temperature": "high",
            "hopper": "hopper_2",
            "name": "Morning",
        },
        blocking=True,
    )

    kwargs = mock_client.async_brew_custom.await_args.kwargs
    assert kwargs["portion_ml"] == 40
    assert kwargs["milk_portion_ml"] == 100
    assert kwargs["intensity"] == 3  # strong
    assert kwargs["temperature"] == 2  # high
    assert kwargs["hopper"] == 2
    assert kwargs["name"] == "Morning"


async def test_maintenance_service(
    hass: HomeAssistant, loaded_entry, mock_client
) -> None:
    """The maintenance service resolves the program name."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, ADDRESS)})

    await hass.services.async_call(
        DOMAIN,
        "start_maintenance",
        {"device_id": device.id, "program": "easy_clean"},
        blocking=True,
    )

    mock_client.async_start_maintenance.assert_awaited_once_with(
        MachineProcess.EASY_CLEAN
    )


async def test_service_rejects_an_unknown_device(
    hass: HomeAssistant, loaded_entry
) -> None:
    """A bad device id is a validation error, not a crash."""
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "cancel",
            {"device_id": "does-not-exist"},
            blocking=True,
        )


async def test_barista_t_hides_ts_only_entities(
    hass: HomeAssistant, mock_client, mock_bluetooth_device, machine_info
) -> None:
    """A Barista T gets neither the second hopper sensor nor bean select."""
    machine_info.machine_type = MachineType.BARISTA_T
    mock_client.profile_count = 5

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        title="Melitta Barista T Smart",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_ids = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    assert not any("beans_low_2" in eid or "hopper_2" in eid for eid in entity_ids)
    assert not any("auto_bean_select" in eid for eid in entity_ids)

    drink_select = hass.states.get("select.melitta_barista_t_smart_drink")
    assert len(drink_select.attributes["options"]) == 21


async def test_diagnostics(hass: HomeAssistant, hass_client, loaded_entry) -> None:
    """Diagnostics expose decoded state and redact identifying details."""
    from homeassistant.components.diagnostics import REDACTED
    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )

    await poll_again(hass)  # counters land on the second refresh
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, loaded_entry
    )

    assert diagnostics["machine"]["model"] == "Barista TS Smart"
    assert diagnostics["machine"]["serial"] == REDACTED
    assert diagnostics["entry"]["data"]["address"] == REDACTED
    assert diagnostics["status"]["process"] == "READY"
    assert diagnostics["settings"]["WATER_HARDNESS"] == 3
    assert diagnostics["counters"]["0"] == 1234
