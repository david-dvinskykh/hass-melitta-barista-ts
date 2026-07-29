"""Button platform for the Melitta Barista TS Smart."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import BREW_READY_PROCESSES
from .coordinator import MelittaConfigEntry, MelittaCoordinator
from .entity import MelittaEntity
from .machine import MachineError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MelittaBrewButton(coordinator),
            MelittaCancelButton(coordinator),
            MelittaSyncClockButton(coordinator),
        ]
    )


class MelittaBrewButton(MelittaEntity, ButtonEntity):
    """Brew the currently selected drink."""

    _attr_translation_key = "brew"

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the button."""
        super().__init__(coordinator, "brew")

    @property
    def available(self) -> bool:
        """Only offered while the machine is idle and ready."""
        if not super().available:
            return False
        status = self.coordinator.data.status
        return status is not None and status.process in BREW_READY_PROCESSES

    async def async_press(self) -> None:
        """Start the selected drink."""
        try:
            await self.coordinator.async_brew()
        except MachineError as err:
            raise HomeAssistantError(f"Could not start brewing: {err}") from err


class MelittaCancelButton(MelittaEntity, ButtonEntity):
    """Cancel the running process."""

    _attr_translation_key = "cancel"

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the button."""
        super().__init__(coordinator, "cancel")

    @property
    def available(self) -> bool:
        """Only offered while something is actually running."""
        if not super().available:
            return False
        status = self.coordinator.data.status
        return status is not None and status.is_busy

    async def async_press(self) -> None:
        """Stop the current process."""
        try:
            await self.coordinator.async_cancel()
        except MachineError as err:
            raise HomeAssistantError(f"Could not cancel: {err}") from err


class MelittaSyncClockButton(MelittaEntity, ButtonEntity):
    """Push Home Assistant's local time to the machine."""

    _attr_translation_key = "sync_clock"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the button."""
        super().__init__(coordinator, "sync_clock")

    async def async_press(self) -> None:
        """Write the current local time to the machine's clock."""
        now = dt_util.now()
        try:
            await self.coordinator.async_set_clock(now.hour, now.minute)
        except MachineError as err:
            raise HomeAssistantError(f"Could not set the clock: {err}") from err
