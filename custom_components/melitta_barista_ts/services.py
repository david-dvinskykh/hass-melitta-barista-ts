"""Services for the Melitta Barista TS Smart integration."""

from __future__ import annotations

import logging
from datetime import time as dt_time

import voluptuous as vol
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_BEAN_HOPPER,
    ATTR_DRINK,
    ATTR_INTENSITY,
    ATTR_PORTION_ML,
    ATTR_PROFILE,
    ATTR_SETTING_ID,
    ATTR_TEMPERATURE,
    ATTR_TIME,
    ATTR_TWO_CUPS,
    ATTR_VALUE,
    DOMAIN,
    MAX_USER_PROFILES,
    PORTION_MAX_ML,
    PORTION_MIN_ML,
    SERVICE_BREW,
    SERVICE_CANCEL,
    SERVICE_READ_SETTING,
    SERVICE_SET_CLOCK,
    SERVICE_WRITE_SETTING,
    SLUG_TO_BLEND,
    SLUG_TO_DIRECTKEY,
    SLUG_TO_INTENSITY,
    SLUG_TO_RECIPE,
    SLUG_TO_TEMPERATURE,
)
from .coordinator import MelittaCoordinator
from .machine import MachineError

_LOGGER = logging.getLogger(__name__)

_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.ensure_list,
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_AREA_ID): cv.ensure_list,
    }
)

BREW_SCHEMA = _DEVICE_SCHEMA.extend(
    {
        vol.Optional(ATTR_DRINK): vol.In(sorted(SLUG_TO_RECIPE)),
        vol.Optional(ATTR_TWO_CUPS): cv.boolean,
        vol.Optional(ATTR_INTENSITY): vol.In(sorted(SLUG_TO_INTENSITY)),
        vol.Optional(ATTR_TEMPERATURE): vol.In(sorted(SLUG_TO_TEMPERATURE)),
        vol.Optional(ATTR_PORTION_ML): vol.All(
            vol.Coerce(int), vol.Range(min=PORTION_MIN_ML, max=PORTION_MAX_ML)
        ),
        vol.Optional(ATTR_BEAN_HOPPER): vol.In(sorted(SLUG_TO_BLEND)),
        vol.Optional(ATTR_PROFILE): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_USER_PROFILES)
        ),
    }
)

CANCEL_SCHEMA = _DEVICE_SCHEMA

SET_CLOCK_SCHEMA = _DEVICE_SCHEMA.extend({vol.Optional(ATTR_TIME): cv.time})

WRITE_SETTING_SCHEMA = _DEVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_SETTING_ID): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=32767)
        ),
        vol.Required(ATTR_VALUE): vol.Coerce(int),
    }
)

READ_SETTING_SCHEMA = _DEVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_SETTING_ID): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=32767)
        )
    }
)


def _referenced_entry_ids(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Collect the config entries behind a call's device, entity and area target.

    Resolved against the registries directly rather than through the shared
    target helper, whose module and class names have moved between Home
    Assistant releases.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_ids = set(cv.ensure_list(call.data.get(ATTR_DEVICE_ID, [])))
    entity_ids = set(cv.ensure_list(call.data.get(ATTR_ENTITY_ID, [])))

    for area_id in cv.ensure_list(call.data.get(ATTR_AREA_ID, [])):
        device_ids.update(
            device.id for device in dr.async_entries_for_area(device_registry, area_id)
        )
        entity_ids.update(
            entity.entity_id
            for entity in er.async_entries_for_area(entity_registry, area_id)
        )

    entry_ids: list[str] = []
    for entity_id in entity_ids:
        entity = entity_registry.async_get(entity_id)
        if entity is None:
            continue
        if entity.device_id is not None:
            device_ids.add(entity.device_id)
        if entity.config_entry_id is not None:
            entry_ids.append(entity.config_entry_id)

    for device_id in device_ids:
        device = device_registry.async_get(device_id)
        if device is not None:
            entry_ids.extend(device.config_entries)

    return entry_ids


def _coordinators(hass: HomeAssistant, call: ServiceCall) -> list[MelittaCoordinator]:
    """Resolve the call's target to the machines it refers to."""
    entry_ids = _referenced_entry_ids(hass, call)

    found: list[MelittaCoordinator] = []
    for entry_id in dict.fromkeys(entry_ids):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, MelittaCoordinator) and coordinator not in found:
            found.append(coordinator)

    if not found:
        raise ServiceValidationError(
            "No Melitta Barista Smart machine was found in the service target"
        )
    return found


def _one_coordinator(hass: HomeAssistant, call: ServiceCall) -> MelittaCoordinator:
    """Resolve exactly one targeted machine."""
    coordinators = _coordinators(hass, call)
    if len(coordinators) > 1:
        raise ServiceValidationError(
            f"{call.service} can only target a single machine at a time"
        )
    return coordinators[0]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services once."""
    if hass.services.has_service(DOMAIN, SERVICE_BREW):
        return

    async def _async_brew(call: ServiceCall) -> None:
        drink = call.data.get(ATTR_DRINK)
        intensity = call.data.get(ATTR_INTENSITY)
        temperature = call.data.get(ATTR_TEMPERATURE)
        hopper = call.data.get(ATTR_BEAN_HOPPER)
        profile = call.data.get(ATTR_PROFILE)

        # A profile stores one drink per direct-select key, not the whole
        # menu, so only those seven drinks can be brewed from one.
        if profile is not None and drink is not None and drink not in SLUG_TO_DIRECTKEY:
            raise ServiceValidationError(
                f"'{drink}' is not stored in a profile. Profiles hold "
                f"{', '.join(sorted(SLUG_TO_DIRECTKEY))} — brew '{drink}' "
                "without a profile to use the built-in recipe."
            )

        overrides = {
            "two_cups": call.data.get(ATTR_TWO_CUPS),
            "intensity": SLUG_TO_INTENSITY[intensity] if intensity else None,
            "temperature": SLUG_TO_TEMPERATURE[temperature] if temperature else None,
            "portion_ml": call.data.get(ATTR_PORTION_ML),
            "blend": SLUG_TO_BLEND[hopper] if hopper else None,
        }

        for coordinator in _coordinators(hass, call):
            try:
                if profile is None:
                    await coordinator.async_brew(
                        SLUG_TO_RECIPE[drink] if drink else None, **overrides
                    )
                else:
                    await coordinator.async_brew_profile(
                        profile,
                        SLUG_TO_DIRECTKEY[drink] if drink else None,
                        **overrides,
                    )
            except MachineError as err:
                raise HomeAssistantError(f"Could not start brewing: {err}") from err

    async def _async_cancel(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            try:
                await coordinator.async_cancel()
            except MachineError as err:
                raise HomeAssistantError(f"Could not cancel: {err}") from err

    async def _async_set_clock(call: ServiceCall) -> None:
        value: dt_time | None = call.data.get(ATTR_TIME)
        if value is None:
            now = dt_util.now()
            value = dt_time(now.hour, now.minute)

        for coordinator in _coordinators(hass, call):
            try:
                await coordinator.async_set_clock(value.hour, value.minute)
            except MachineError as err:
                raise HomeAssistantError(f"Could not set the clock: {err}") from err

    async def _async_write_setting(call: ServiceCall) -> None:
        setting_id = call.data[ATTR_SETTING_ID]
        value = call.data[ATTR_VALUE]
        for coordinator in _coordinators(hass, call):
            try:
                await coordinator.async_write_setting(setting_id, value)
            except MachineError as err:
                raise HomeAssistantError(
                    f"Could not write register {setting_id}: {err}"
                ) from err

    async def _async_read_setting(call: ServiceCall) -> ServiceResponse:
        setting_id = call.data[ATTR_SETTING_ID]
        coordinator = _one_coordinator(hass, call)
        try:
            value = await coordinator.async_read_setting(setting_id)
        except MachineError as err:
            raise HomeAssistantError(
                f"Could not read register {setting_id}: {err}"
            ) from err
        return {"setting_id": setting_id, "value": value}

    hass.services.async_register(DOMAIN, SERVICE_BREW, _async_brew, BREW_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, _async_cancel, CANCEL_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_CLOCK, _async_set_clock, SET_CLOCK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WRITE_SETTING, _async_write_setting, WRITE_SETTING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_SETTING,
        _async_read_setting,
        READ_SETTING_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
