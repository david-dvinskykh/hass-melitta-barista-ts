"""Diagnostics for the Melitta Barista TS integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import MelittaConfigEntry

TO_REDACT = {CONF_ADDRESS, "serial", "profile_names"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MelittaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    status = data.status if data else None
    info = coordinator.client.info

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connection": {
            "connected": coordinator.client.connected,
            "last_update_success": coordinator.last_update_success,
        },
        "machine": async_redact_data(
            {
                "model": info.model,
                "machine_type": info.machine_type.name if info.machine_type else None,
                "firmware": info.firmware,
                "serial": info.serial,
                "profile_names": info.profile_names,
                "active_profile": coordinator.client.active_profile,
            },
            TO_REDACT,
        ),
        "status": (
            {
                "process": status.process.name if status.process else None,
                "raw_process": status.raw_process,
                "sub_process": (
                    status.sub_process.name if status.sub_process else None
                ),
                "info_messages": [flag.name for flag in status.info_messages],
                "manipulation": status.manipulation.name,
                "raw_manipulation": status.raw_manipulation,
                "progress": status.progress,
            }
            if status
            else None
        ),
        "settings": (
            {setting.name: value for setting, value in data.settings.items()}
            if data
            else {}
        ),
        "counters": data.counters if data else {},
    }
