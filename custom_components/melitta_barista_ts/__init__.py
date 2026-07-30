"""The Melitta Barista TS Smart integration."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import MelittaClient
from .coordinator import MelittaCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

type MelittaConfigEntry = ConfigEntry[MelittaCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the integration's services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MelittaConfigEntry) -> bool:
    """Set up a coffee machine from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"Could not find Melitta coffee machine {address}; "
            "is it powered on and in range of a Bluetooth adapter or proxy?"
        )

    client = MelittaClient(address, ble_device)
    coordinator = MelittaCoordinator(hass, entry, client)
    await coordinator.async_setup()

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await client.disconnect()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MelittaConfigEntry) -> bool:
    """Unload a config entry and drop the BLE link."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.disconnect()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: MelittaConfigEntry) -> None:
    """Reload when the options change (poll interval, prompt handling)."""
    await hass.config_entries.async_reload(entry.entry_id)
