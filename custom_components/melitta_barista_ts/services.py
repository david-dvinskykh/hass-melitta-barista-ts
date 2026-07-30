"""Service calls for the Melitta Barista TS integration.

Everything a button entity can do is also available as a service, plus the
parametrised calls that do not map to a single entity: brewing a named recipe,
brewing a recipe assembled in the call itself, and starting maintenance
programs.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .client import MelittaConnectionError
from .const import (
    ATTR_HOPPER,
    ATTR_INTENSITY,
    ATTR_MILK_PORTION,
    ATTR_NAME,
    ATTR_PORTION,
    ATTR_PROCESS,
    ATTR_PROGRAM,
    ATTR_RECIPE,
    ATTR_TEMPERATURE,
    ATTR_TWO_CUPS,
    DOMAIN,
    HOPPER_OPTIONS,
    INTENSITY_OPTIONS,
    MAINTENANCE_PROGRAMS,
    MAX_PORTION_ML,
    MIN_PORTION_ML,
    PROCESS_OPTIONS,
    RECIPE_BY_KEY,
    SERVICE_BREW,
    SERVICE_BREW_CUSTOM,
    SERVICE_CANCEL,
    SERVICE_CONFIRM_PROMPT,
    SERVICE_START_MAINTENANCE,
    TEMPERATURE_OPTIONS,
    ComponentProcess,
)
from .coordinator import MelittaCoordinator
from .protocol import ProtocolError

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"

_DEVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})

_BREW_SCHEMA = _DEVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_RECIPE): vol.In(sorted(RECIPE_BY_KEY)),
        vol.Optional(ATTR_TWO_CUPS, default=False): cv.boolean,
    }
)

_BREW_CUSTOM_SCHEMA = _DEVICE_SCHEMA.extend(
    {
        vol.Optional(ATTR_PROCESS, default="coffee"): vol.In(sorted(PROCESS_OPTIONS)),
        vol.Required(ATTR_PORTION): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_PORTION_ML, max=MAX_PORTION_ML)
        ),
        vol.Optional(ATTR_MILK_PORTION, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_PORTION_ML)
        ),
        vol.Optional(ATTR_INTENSITY, default="medium"): vol.In(
            sorted(INTENSITY_OPTIONS)
        ),
        vol.Optional(ATTR_TEMPERATURE, default="normal"): vol.In(
            sorted(TEMPERATURE_OPTIONS)
        ),
        vol.Optional(ATTR_HOPPER, default="hopper_1"): vol.In(sorted(HOPPER_OPTIONS)),
        vol.Optional(ATTR_NAME, default="Home Assistant"): cv.string,
        vol.Optional(ATTR_TWO_CUPS, default=False): cv.boolean,
    }
)

_MAINTENANCE_SCHEMA = _DEVICE_SCHEMA.extend(
    {vol.Required(ATTR_PROGRAM): vol.In(sorted(MAINTENANCE_PROGRAMS))}
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services once."""
    if hass.services.has_service(DOMAIN, SERVICE_BREW):
        return

    hass.services.async_register(DOMAIN, SERVICE_BREW, _async_brew, _BREW_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_BREW_CUSTOM, _async_brew_custom, _BREW_CUSTOM_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, _async_cancel, _DEVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_CONFIRM_PROMPT, _async_confirm_prompt, _DEVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_MAINTENANCE, _async_maintenance, _MAINTENANCE_SCHEMA
    )


def _coordinator(call: ServiceCall) -> MelittaCoordinator:
    """Resolve the target device to its coordinator."""
    hass = call.hass
    device_id: str = call.data[ATTR_DEVICE_ID]
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_loaded",
        translation_placeholders={"device_id": device_id},
    )


async def _async_run(call: ServiceCall, coro) -> None:
    """Await a machine action, mapping failures to HA errors."""
    try:
        await coro
    except (MelittaConnectionError, ProtocolError) as err:
        raise HomeAssistantError(
            f"{call.service} failed on the coffee machine: {err}"
        ) from err


async def _async_brew(call: ServiceCall) -> None:
    """Brew one of the machine's built-in recipes."""
    coordinator = _coordinator(call)
    recipe = RECIPE_BY_KEY[call.data[ATTR_RECIPE]]
    await _async_run(
        call,
        coordinator.client.async_brew(recipe, two_cups=call.data[ATTR_TWO_CUPS]),
    )
    await coordinator.async_request_refresh()


async def _async_brew_custom(call: ServiceCall) -> None:
    """Brew a drink described entirely by the service data."""
    coordinator = _coordinator(call)
    await _async_run(
        call,
        coordinator.client.async_brew_custom(
            process=ComponentProcess(PROCESS_OPTIONS[call.data[ATTR_PROCESS]]),
            portion_ml=call.data[ATTR_PORTION],
            milk_portion_ml=call.data[ATTR_MILK_PORTION],
            intensity=INTENSITY_OPTIONS[call.data[ATTR_INTENSITY]],
            temperature=TEMPERATURE_OPTIONS[call.data[ATTR_TEMPERATURE]],
            hopper=HOPPER_OPTIONS[call.data[ATTR_HOPPER]],
            name=call.data[ATTR_NAME],
            two_cups=call.data[ATTR_TWO_CUPS],
        ),
    )
    await coordinator.async_request_refresh()


async def _async_cancel(call: ServiceCall) -> None:
    """Abort the running preparation."""
    coordinator = _coordinator(call)
    await _async_run(call, coordinator.client.async_cancel())
    await coordinator.async_request_refresh()


async def _async_confirm_prompt(call: ServiceCall) -> None:
    """Acknowledge the prompt shown on the machine."""
    coordinator = _coordinator(call)
    await _async_run(call, coordinator.client.async_confirm_prompt())
    await coordinator.async_request_refresh()


async def _async_maintenance(call: ServiceCall) -> None:
    """Start a cleaning, descaling or filter program."""
    coordinator = _coordinator(call)
    process = MAINTENANCE_PROGRAMS[call.data[ATTR_PROGRAM]]
    await _async_run(call, coordinator.client.async_start_maintenance(process))
    await coordinator.async_request_refresh()
