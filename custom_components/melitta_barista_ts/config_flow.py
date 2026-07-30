"""Config flow for the Melitta Barista TS Smart integration."""

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
from homeassistant.helpers import selector

from .const import (
    BLE_NAME_PREFIXES,
    BLE_NAME_PREFIXES_T,
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
    SERVICE_UUID,
)

_LOGGER = logging.getLogger(__name__)


def is_supported(info: BluetoothServiceInfoBleak) -> bool:
    """True when an advertisement looks like a Barista Smart machine."""
    if SERVICE_UUID.lower() in {uuid.lower() for uuid in info.service_uuids}:
        return True
    return bool(info.name) and info.name[:4] in BLE_NAME_PREFIXES


def suggested_title(info: BluetoothServiceInfoBleak) -> str:
    """Best-effort model name from the advertised article number."""
    prefix = (info.name or "")[:4]
    if prefix in BLE_NAME_PREFIXES_T:
        return "Melitta Barista T Smart"
    return "Melitta Barista TS Smart"


class MelittaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Prepare the discovery caches."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a machine found by the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        if not is_supported(discovery_info):
            return self.async_abort(reason="not_supported")

        self._discovery = discovery_info
        self.context["title_placeholders"] = {
            "name": suggested_title(discovery_info),
            "address": discovery_info.address,
        }
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a discovered machine."""
        assert self._discovery is not None
        title = suggested_title(self._discovery)

        if user_input is not None:
            # The form can sit open while the same machine is added another
            # way; re-check rather than creating a duplicate entry.
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=title, data={CONF_ADDRESS: self._discovery.address}
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": title,
                "address": self._discovery.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick from the machines currently in range."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            info = self._discovered[address]
            return self.async_create_entry(
                title=suggested_title(info), data={CONF_ADDRESS: address}
            )

        configured = self._async_current_ids()
        self._discovered = {
            info.address: info
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.address not in configured and is_supported(info)
        }

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{suggested_title(info)} ({address})"
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> MelittaOptionsFlow:
        """Return the options flow handler."""
        return MelittaOptionsFlow()


class MelittaOptionsFlow(OptionsFlow):
    """Tune polling and connection behaviour."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the connection options."""
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
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=300, step=1, unit_of_measurement="s"
                        )
                    ),
                    vol.Required(
                        CONF_FRAME_TIMEOUT,
                        default=options.get(CONF_FRAME_TIMEOUT, DEFAULT_FRAME_TIMEOUT),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=30, step=1, unit_of_measurement="s"
                        )
                    ),
                    vol.Required(
                        CONF_CONNECT_TIMEOUT,
                        default=options.get(
                            CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5, max=120, step=1, unit_of_measurement="s"
                        )
                    ),
                    vol.Required(
                        CONF_PAIRING_AGENT,
                        default=options.get(CONF_PAIRING_AGENT, DEFAULT_PAIRING_AGENT),
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_BRAND_ICON,
                        default=options.get(CONF_BRAND_ICON, DEFAULT_BRAND_ICON),
                    ): selector.BooleanSelector(),
                }
            ),
        )
