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
    REFUSED_SOURCES,
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

    # Before anything that talks to the machine: the logo has nothing to do
    # with whether it can be reached, and a machine that is switched off
    # would otherwise keep the placeholder on the integrations page.
    if options.get(CONF_BRAND_ICON, DEFAULT_BRAND_ICON):
        await async_serve_brand(hass)
        entry.async_on_unload(lambda: async_stop_serving_brand(hass))

    # Adapters the machine has refused to bond with, remembered across the
    # config entry retries that build a fresh client every time.
    refused: set[str] = hass.data.setdefault(REFUSED_SOURCES, {}).setdefault(
        address, set()
    )

    def _device_provider():
        """The machine as every adapter that can hear it sees it.

        Home Assistant would hand out whichever adapter heard it loudest, but
        bonds are held per adapter: the loudest one may be holding a key the
        machine no longer accepts, and then pairing is refused for good. So
        adapters that have been refused are dropped from the list — until
        every one of them has, at which point the whole list comes back and
        the cycle starts over rather than leaving the machine unreachable.
        """
        devices = [
            scanner_device.ble_device
            for scanner_device in bluetooth.async_scanner_devices_by_address(
                hass, address, connectable=True
            )
        ]
        if not devices:
            single = bluetooth.async_ble_device_from_address(
                hass, address, connectable=True
            )
            devices = [single] if single is not None else []

        usable = [device for device in devices if scanner_source(device) not in refused]
        if not usable and devices:
            _LOGGER.debug("Every adapter has been refused; starting over")
            refused.clear()
            return devices
        return usable

    if not _device_provider():
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
        on_pairing_refused=refused.add,
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
