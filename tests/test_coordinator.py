"""Coordinator behaviour: refresh policy, pushed status, prompt handling."""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.melitta_barista_ts.client import (
    MelittaConnectionError,
)
from custom_components.melitta_barista_ts.const import (
    CONF_AUTO_CONFIRM,
    CONF_POLL_INTERVAL,
    DOMAIN,
    MachineProcess,
    Manipulation,
    Setting,
)
from custom_components.melitta_barista_ts.protocol import MachineStatus

from .conftest import ADDRESS


async def setup_entry(
    hass: HomeAssistant, options: dict | None = None
) -> MockConfigEntry:
    """Set up a config entry with the given options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        options=options or {},
        title="Melitta Barista TS Smart",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities_go_unavailable_when_the_machine_drops_off(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """A failed refresh marks the entities unavailable."""
    await setup_entry(hass)
    assert hass.states.get("sensor.melitta_barista_ts_smart_state").state == "ready"

    mock_client.async_update_status.side_effect = MelittaConnectionError("out of range")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert (
        hass.states.get("sensor.melitta_barista_ts_smart_state").state == "unavailable"
    )


async def test_recovery_after_a_failed_refresh(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """Entities come back once the machine answers again."""
    await setup_entry(hass)
    mock_client.async_update_status.side_effect = MelittaConnectionError("gone")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    mock_client.async_update_status.side_effect = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()

    assert hass.states.get("sensor.melitta_barista_ts_smart_state").state == "ready"


async def test_poll_interval_option_is_applied(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """The configured interval drives the refresh."""
    entry = await setup_entry(hass, {CONF_POLL_INTERVAL: 60})
    coordinator = entry.runtime_data

    assert coordinator.update_interval == timedelta(seconds=60)

    calls = mock_client.async_update_status.await_count
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert mock_client.async_update_status.await_count == calls

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert mock_client.async_update_status.await_count > calls


async def test_changing_options_reloads_the_entry(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """Saving options reloads so the new interval takes effect."""
    entry = await setup_entry(hass)

    hass.config_entries.async_update_entry(entry, options={CONF_POLL_INTERVAL: 30})
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.update_interval == timedelta(seconds=30)


async def test_settings_are_read_once_then_after_a_write(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """Settings are cached, and re-read to confirm a write."""
    await setup_entry(hass)
    assert mock_client.async_read_settings.await_count == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert mock_client.async_read_settings.await_count == 1

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.melitta_barista_ts_smart_water_hardness", "value": 4},
        blocking=True,
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()

    assert mock_client.async_read_settings.await_count == 2


async def test_settings_are_re_read_after_a_disconnect(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """A reconnect re-reads settings, in case they changed on the machine."""
    entry = await setup_entry(hass)
    assert mock_client.async_read_settings.await_count == 1

    entry.runtime_data._handle_disconnect()
    await hass.async_block_till_done()

    assert mock_client.async_read_settings.await_count == 2


async def test_pushed_status_before_the_first_refresh_is_ignored(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """A notification arriving before any data exists does not crash."""
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data
    coordinator.data = None

    coordinator._handle_pushed_status(MachineStatus(process=MachineProcess.READY))
    await hass.async_block_till_done()


async def test_soft_prompts_are_auto_confirmed_when_enabled(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """A "move cup to frother" prompt is acknowledged over BLE when opted in."""
    entry = await setup_entry(hass, {CONF_AUTO_CONFIRM: True})

    entry.runtime_data._handle_pushed_status(
        MachineStatus(
            process=MachineProcess.READY,
            manipulation=Manipulation.MOVE_CUP_TO_FROTHER,
        )
    )
    await hass.async_block_till_done()

    mock_client.async_confirm_prompt.assert_awaited()


async def test_physical_prompts_are_never_auto_confirmed(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """Prompts needing hands stay for the user, even with auto-confirm on."""
    entry = await setup_entry(hass, {CONF_AUTO_CONFIRM: True})

    entry.runtime_data._handle_pushed_status(
        MachineStatus(
            process=MachineProcess.READY, manipulation=Manipulation.FILL_WATER
        )
    )
    await hass.async_block_till_done()

    mock_client.async_confirm_prompt.assert_not_awaited()
    assert (
        hass.states.get("sensor.melitta_barista_ts_smart_action_required").state
        == "fill_water"
    )
    assert (
        hass.states.get("binary_sensor.melitta_barista_ts_smart_water_tank_empty").state
        == "on"
    )


async def test_prompts_are_left_alone_by_default(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """Auto-confirm is off unless the user turns it on."""
    entry = await setup_entry(hass)

    entry.runtime_data._handle_pushed_status(
        MachineStatus(
            process=MachineProcess.READY,
            manipulation=Manipulation.MOVE_CUP_TO_FROTHER,
        )
    )
    await hass.async_block_till_done()

    mock_client.async_confirm_prompt.assert_not_awaited()


async def test_controls_are_unavailable_while_disconnected(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """Writable entities hide when the link is down; sensors keep their value."""
    await setup_entry(hass)
    hardness = "number.melitta_barista_ts_smart_water_hardness"
    assert hass.states.get(hardness).state == "3.0"

    mock_client.connected = False
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert hass.states.get(hardness).state == "unavailable"
    assert hass.states.get("sensor.melitta_barista_ts_smart_state").state == "ready"


async def test_settings_missing_from_the_machine_leave_entities_unknown(
    hass: HomeAssistant, mock_client, mock_bluetooth_device
) -> None:
    """A register the firmware does not answer yields an unknown state."""
    mock_client.async_read_settings.return_value = {Setting.WATER_HARDNESS: 2}
    await setup_entry(hass)

    assert (
        hass.states.get("number.melitta_barista_ts_smart_water_hardness").state == "2.0"
    )
    assert (
        hass.states.get("switch.melitta_barista_ts_smart_energy_saving").state
        == "unknown"
    )
