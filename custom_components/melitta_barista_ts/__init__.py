"""The Melitta Barista TS Smart integration."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .brand import async_serve_brand, async_stop_serving_brand
from .client import MelittaBleClient, scanner_source
from .const import (
    CONF_BRAND_ICON,
    CONF_CONNECT_TIMEOUT,
    CONF_FRAME_TIMEOUT,
    CONF_PAIRING_AGENT,
    CONF_POLL_INTERVAL,
    DEFAULT_BRAND_ICON,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_FRAME_TIMEOUT,
    DEFAULT_PAIRING_AGENT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import MelittaConfigEntry, MelittaCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: MelittaConfigEntry) -> bool:
    """Set up a coffee machine from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    options = entry.options

    client: MelittaBleClient | None = None

    def _device_provider():
        """Look up the current BLEDevice, preferring the path that last worked.

        Home Assistant hands out whichever adapter or proxy heard the machine
        loudest, and that flips between them advert by advert. The machine
        bonds per adapter, so a flip lands on one holding no key it trusts:
        the link comes up, the machine ignores the handshake, and the session
        dies seconds after it started. Going back to the adapter that carried
        the last working session avoids the whole cycle.
        """
        preferred = client.last_good_source if client is not None else None
        if preferred is not None:
            for scanner_device in bluetooth.async_scanner_devices_by_address(
                hass, address, connectable=True
            ):
                if scanner_source(scanner_device.ble_device) == preferred:
                    return scanner_device.ble_device

        return bluetooth.async_ble_device_from_address(hass, address, connectable=True)

    if _device_provider() is None:
        raise ConfigEntryNotReady(
            f"Could not find {address}. Make sure the machine is switched on and "
            "in range of a Bluetooth adapter or proxy."
        )

    client = MelittaBleClient(
        _device_provider,
        name=entry.title,
        frame_timeout=options.get(CONF_FRAME_TIMEOUT, DEFAULT_FRAME_TIMEOUT),
        connect_timeout=options.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
        use_pairing_agent=options.get(CONF_PAIRING_AGENT, DEFAULT_PAIRING_AGENT),
    )

    coordinator = MelittaCoordinator(
        hass,
        entry,
        client,
        poll_interval=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await client.async_close()
        raise

    entry.runtime_data = coordinator

    # Keep the entry alive while the machine is switched off: an advert from
    # the machine is enough to trigger an immediate refresh.
    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            lambda *_: hass.async_create_task(coordinator.async_request_refresh()),
            bluetooth.BluetoothCallbackMatcher(address=address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    if options.get(CONF_BRAND_ICON, DEFAULT_BRAND_ICON):
        await async_serve_brand(hass)
        entry.async_on_unload(lambda: async_stop_serving_brand(hass))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async_setup_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MelittaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: MelittaConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


__all__ = ["DOMAIN", "async_setup_entry", "async_unload_entry"]
