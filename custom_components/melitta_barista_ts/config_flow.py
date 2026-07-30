"""Config flow for the Melitta Barista TS integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .client import MelittaClient, MelittaConnectionError
from .const import (
    CONF_AUTO_CONFIRM,
    CONF_COUNTER_INTERVAL,
    CONF_POLL_INTERVAL,
    DEFAULT_AUTO_CONFIRM,
    DEFAULT_COUNTER_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    MODEL_NAMES,
    is_supported_name,
    machine_type_from_name,
)
from .protocol import ProtocolError

_LOGGER = logging.getLogger(__name__)


def _title(name: str | None) -> str:
    """Config entry title for a discovered machine."""
    machine_type = machine_type_from_name(name)
    if machine_type is None:
        return f"{MANUFACTURER} Barista Smart"
    return f"{MANUFACTURER} {MODEL_NAMES[machine_type]}"


class MelittaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual setup of a coffee machine."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow state."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a machine found by the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        if not is_supported_name(discovery_info.name):
            return self.async_abort(reason="not_supported")

        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info.name)}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a discovered machine."""
        assert self._discovery is not None
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                description_placeholders={
                    "name": _title(self._discovery.name),
                    "address": self._discovery.address,
                },
            )
        return await self._async_create(self._discovery)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick from the machines Home Assistant can see."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self._async_create(self._discovered[address])

        current = self._async_current_ids()
        self._discovered = {
            info.address: info
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.address not in current and is_supported_name(info.name)
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{_title(info.name)} ({address})"
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def _async_create(
        self, discovery: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Verify the machine talks to us, then create the entry."""
        error: str | None = None
        title = _title(discovery.name)
        client = MelittaClient(discovery.address, discovery.device)
        try:
            await client.connect()
        except MelittaConnectionError as err:
            _LOGGER.debug("Could not reach %s: %s", discovery.address, err)
            error = "cannot_connect"
        except ProtocolError as err:
            _LOGGER.debug("Handshake failed for %s: %s", discovery.address, err)
            error = "invalid_auth"
        else:
            title = f"{MANUFACTURER} {client.info.model}"
        finally:
            await client.disconnect()

        if error is None:
            return self.async_create_entry(
                title=title, data={CONF_ADDRESS: discovery.address}
            )

        # A discovered machine may just have been busy — let the user retry.
        if self._discovery is not None:
            return self.async_show_form(
                step_id="confirm",
                errors={"base": error},
                description_placeholders={
                    "name": _title(discovery.name),
                    "address": discovery.address,
                },
            )
        return self.async_abort(reason=error)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MelittaOptionsFlow:
        """Return the options flow."""
        return MelittaOptionsFlow()


class MelittaOptionsFlow(OptionsFlow):
    """Tune polling and prompt handling."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
                    ),
                    vol.Required(
                        CONF_COUNTER_INTERVAL,
                        default=options.get(
                            CONF_COUNTER_INTERVAL, DEFAULT_COUNTER_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=86400)),
                    vol.Required(
                        CONF_AUTO_CONFIRM,
                        default=options.get(CONF_AUTO_CONFIRM, DEFAULT_AUTO_CONFIRM),
                    ): cv.boolean,
                }
            ),
        )
