"""Switch platform for the Melitta Barista TS Smart."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MelittaConfigEntry, MelittaCoordinator
from .entity import MelittaLocalEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch entities."""
    async_add_entities([MelittaTwoCupsSwitch(entry.runtime_data)])


class MelittaTwoCupsSwitch(MelittaLocalEntity, SwitchEntity):
    """Brew two cups at once on the next brew."""

    _attr_translation_key = "two_cups"

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the switch."""
        super().__init__(coordinator, "two_cups")

    @property
    def is_on(self) -> bool:
        """Whether the next brew makes two cups."""
        return self.coordinator.brew_settings.two_cups

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Make the next brew a double."""
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Make the next brew a single."""
        self._set(False)

    def _set(self, value: bool) -> None:
        self.coordinator.brew_settings.two_cups = value
        self.coordinator.async_update_listeners()
