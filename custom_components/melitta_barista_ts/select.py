"""Select platform for the Melitta Barista TS Smart."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BLEND_SLUGS,
    INTENSITY_SLUGS,
    RECIPE_SLUGS,
    SLUG_TO_BLEND,
    SLUG_TO_INTENSITY,
    SLUG_TO_RECIPE,
    SLUG_TO_TEMPERATURE,
    TEMPERATURE_SLUGS,
)
from .coordinator import MelittaConfigEntry, MelittaCoordinator
from .entity import MelittaLocalEntity
from .machine import MachineError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MelittaDrinkSelect(coordinator),
            MelittaIntensitySelect(coordinator),
            MelittaTemperatureSelect(coordinator),
            MelittaHopperSelect(coordinator),
        ]
    )


class MelittaDrinkSelect(MelittaLocalEntity, SelectEntity):
    """Which drink the brew button will make."""

    _attr_translation_key = "drink"

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the selector."""
        super().__init__(coordinator, "drink")

    @property
    def options(self) -> list[str]:
        """Drinks this machine offers."""
        return self.coordinator.available_drink_slugs

    @property
    def current_option(self) -> str:
        """Currently selected drink."""
        return RECIPE_SLUGS[self.coordinator.selected_drink]

    async def async_select_option(self, option: str) -> None:
        """Select a drink and load its stored parameters."""
        try:
            await self.coordinator.async_select_drink(SLUG_TO_RECIPE[option])
        except MachineError as err:
            raise HomeAssistantError(f"Could not select {option}: {err}") from err


class MelittaIntensitySelect(MelittaLocalEntity, SelectEntity):
    """Coffee strength used for the next brew."""

    _attr_translation_key = "intensity"
    _attr_options = list(INTENSITY_SLUGS.values())

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the selector."""
        super().__init__(coordinator, "intensity")

    @property
    def current_option(self) -> str:
        """Staged strength."""
        return INTENSITY_SLUGS[self.coordinator.brew_settings.intensity]

    async def async_select_option(self, option: str) -> None:
        """Stage a strength for the next brew."""
        self.coordinator.brew_settings.intensity = SLUG_TO_INTENSITY[option]
        self.coordinator.async_update_listeners()


class MelittaTemperatureSelect(MelittaLocalEntity, SelectEntity):
    """Brew temperature used for the next brew."""

    _attr_translation_key = "brew_temperature"
    _attr_options = list(TEMPERATURE_SLUGS.values())

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the selector."""
        super().__init__(coordinator, "brew_temperature")

    @property
    def current_option(self) -> str:
        """Staged temperature."""
        return TEMPERATURE_SLUGS[self.coordinator.brew_settings.temperature]

    async def async_select_option(self, option: str) -> None:
        """Stage a temperature for the next brew."""
        self.coordinator.brew_settings.temperature = SLUG_TO_TEMPERATURE[option]
        self.coordinator.async_update_listeners()


class MelittaHopperSelect(MelittaLocalEntity, SelectEntity):
    """Bean hopper used for the next brew (dual-hopper machines only)."""

    _attr_translation_key = "bean_hopper"
    _attr_options = list(BLEND_SLUGS.values())

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the selector."""
        super().__init__(coordinator, "bean_hopper")

    @property
    def current_option(self) -> str:
        """Staged hopper."""
        return BLEND_SLUGS[self.coordinator.brew_settings.blend]

    async def async_select_option(self, option: str) -> None:
        """Stage a hopper for the next brew."""
        self.coordinator.brew_settings.blend = SLUG_TO_BLEND[option]
        self.coordinator.async_update_listeners()
