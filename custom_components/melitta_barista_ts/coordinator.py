"""Update coordinator for the Melitta Barista TS integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import MachineInfo, MelittaClient, MelittaConnectionError
from .const import (
    CONF_AUTO_CONFIRM,
    CONF_COUNTER_INTERVAL,
    CONF_POLL_INTERVAL,
    CONFIRMABLE_MANIPULATIONS,
    DEFAULT_AUTO_CONFIRM,
    DEFAULT_COUNTER_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MachineProcess,
    Setting,
)
from .protocol import MachineStatus, ProtocolError

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MelittaData:
    """Snapshot handed to the entities on every refresh."""

    status: MachineStatus | None = None
    settings: dict[Setting, int] = field(default_factory=dict)
    counters: dict[int, int] = field(default_factory=dict)
    info: MachineInfo = field(default_factory=MachineInfo)


class MelittaCoordinator(DataUpdateCoordinator[MelittaData]):
    """Polls machine status and keeps derived data fresh.

    Status also arrives unsolicited over BLE notifications; those are pushed
    straight to the entities so a brew's progress updates smoothly between
    polls.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: MelittaClient,
    ) -> None:
        """Initialise the coordinator for one machine."""
        self.client = client
        self.address = client.address
        self._counter_interval = entry.options.get(
            CONF_COUNTER_INTERVAL, DEFAULT_COUNTER_INTERVAL
        )
        self._counters_read_at: float | None = None
        self._settings_stale = True

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {client.address}",
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            ),
        )

        client.set_device_lookup(self._lookup_device)
        client.add_status_callback(self._handle_pushed_status)
        client.add_disconnect_callback(self._handle_disconnect)

    # -- wiring ---------------------------------------------------------

    async def async_setup(self) -> None:
        """Subscribe to advertisements so reconnects see a fresh device."""
        self.config_entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._handle_advertisement,
                {"address": self.address, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )

    @callback
    def _lookup_device(self):
        """Freshest ``BLEDevice`` known to Home Assistant, if any."""
        return bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )

    @callback
    def _handle_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        self.client.set_ble_device(service_info.device)

    @callback
    def _handle_pushed_status(self, status: MachineStatus) -> None:
        """Publish a status frame that arrived outside a poll."""
        if self.data is None:
            return
        self.data.status = status
        self.async_set_updated_data(self.data)
        self._maybe_auto_confirm(status)

    @callback
    def _handle_disconnect(self) -> None:
        """Refresh promptly after an unexpected disconnect.

        Settings are marked stale too: while we were away the machine may have
        been reconfigured at its own control panel.
        """
        self._settings_stale = True
        self.hass.async_create_task(self.async_request_refresh())

    # -- polling --------------------------------------------------------

    async def _async_update_data(self) -> MelittaData:
        first_refresh = self.data is None
        data = self.data or MelittaData()
        try:
            data.status = await self.client.async_update_status()
            if self._settings_stale:
                data.settings = await self.client.async_read_settings()
                self._settings_stale = False
            if not first_refresh and self._counters_due(data.status):
                data.counters = await self.client.async_read_counters()
                self._counters_read_at = monotonic()
        except MelittaConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except ProtocolError as err:
            raise UpdateFailed(f"machine did not answer: {err}") from err

        data.info = self.client.info
        self._maybe_auto_confirm(data.status)
        return data

    def _counters_due(self, status: MachineStatus | None) -> bool:
        """Whether to sweep the drink counters on this refresh.

        The sweep is 25 round trips over the machine's single BLE slot, so it
        only runs while the machine is idle — during a preparation it would
        compete with the status updates the user is watching.
        """
        if self._counter_interval <= 0:
            return False
        if status is not None and status.process is not MachineProcess.READY:
            return False
        if self._counters_read_at is None:
            return True
        return monotonic() - self._counters_read_at >= self._counter_interval

    def invalidate_settings(self) -> None:
        """Force a settings re-read on the next poll.

        Called after a write so the machine's own view — which may clamp or
        reject the value — replaces the optimistic one.
        """
        self._settings_stale = True

    # -- prompts --------------------------------------------------------

    @callback
    def _maybe_auto_confirm(self, status: MachineStatus | None) -> None:
        """Acknowledge soft prompts automatically when the user asked for it.

        Only prompts that a BLE ``HY`` can genuinely clear are auto-confirmed.
        Physical ones (fill water, empty trays) would just loop.
        """
        if status is None:
            return
        if not self.config_entry.options.get(CONF_AUTO_CONFIRM, DEFAULT_AUTO_CONFIRM):
            return
        if status.manipulation not in CONFIRMABLE_MANIPULATIONS:
            return
        _LOGGER.debug("Auto-confirming prompt %s", status.manipulation.name)
        self.hass.async_create_task(self._async_confirm())

    async def _async_confirm(self) -> None:
        try:
            await self.client.async_confirm_prompt()
        except (MelittaConnectionError, ProtocolError) as err:
            _LOGGER.debug("Auto-confirm failed: %s", err)
