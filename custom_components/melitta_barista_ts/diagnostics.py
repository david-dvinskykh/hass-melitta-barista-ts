"""Diagnostics for the Melitta Barista TS Smart integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .coordinator import MelittaConfigEntry

TO_REDACT = {CONF_ADDRESS, "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MelittaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    status = data.status

    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT
        ),
        "connected": coordinator.client.connected,
        "last_update_success": coordinator.last_update_success,
        "machine": {
            "model": data.model_name,
            "machine_type": None
            if data.machine_type is None
            else int(data.machine_type),
            "firmware": data.firmware,
            "total_cups": data.total_cups,
            "clock_minutes": data.clock_minutes,
            "auto_off_after": data.auto_off_after,
            "water_hardness": data.water_hardness,
            "cup_counters": data.cup_counters,
        },
        "status": None if status is None else asdict(status),
        "brew_settings": {
            "selected_drink": int(coordinator.selected_drink),
            "intensity": int(coordinator.brew_settings.intensity),
            "temperature": int(coordinator.brew_settings.temperature),
            "portion_ml": coordinator.brew_settings.portion_ml,
            "blend": int(coordinator.brew_settings.blend),
            "two_cups": coordinator.brew_settings.two_cups,
        },
    }
