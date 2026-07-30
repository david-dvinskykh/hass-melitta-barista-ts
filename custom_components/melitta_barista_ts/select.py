"""Select platform for the Melitta Barista TS Smart."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BLEND_SLUGS,
    DIRECTKEY_SLUGS,
    INTENSITY_SLUGS,
    RECIPE_SLUGS,
    SLUG_TO_BLEND,
    SLUG_TO_DIRECTKEY,
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
            MelittaProfileSelect(coordinator),
            MelittaProfileDrinkSelect(coordinator),
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


class MelittaProfileSelect(MelittaLocalEntity, SelectEntity):
    """Which user profile the profile brew button draws from.

    Options are the names stored on the machine, so they change when a
    profile is renamed there. "My Coffee" is the machine's own name for the
    unnamed default profile.
    """

    _attr_translation_key = "profile"

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the selector."""
        super().__init__(coordinator, "profile")

    @property
    def available(self) -> bool:
        """Unavailable until the profile names have been read."""
        return bool(self.coordinator.data.profile_names)

    @property
    def options(self) -> list[str]:
        """Profile names as read from the machine."""
        return self.coordinator.profile_options

    @property
    def current_option(self) -> str | None:
        """Currently selected profile."""
        return self.coordinator.selected_profile_option

    async def async_select_option(self, option: str) -> None:
        """Switch profile and reload the selected direct key from it."""
        profile = self.coordinator.profile_for_option(option)
        if profile is None:
            raise HomeAssistantError(f"Unknown profile {option}")
        try:
            await self.coordinator.async_select_profile(profile)
        except MachineError as err:
            raise HomeAssistantError(f"Could not select {option}: {err}") from err


class MelittaProfileDrinkSelect(MelittaLocalEntity, SelectEntity):
    """Which of the profile's seven direct keys to brew.

    A profile stores one drink per direct-select key, not the whole menu,
    so this is a narrower list than the built-in Drink selector.
    """

    _attr_translation_key = "profile_drink"
    _attr_options = list(DIRECTKEY_SLUGS.values())

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the selector."""
        super().__init__(coordinator, "profile_drink")

    @property
    def current_option(self) -> str:
        """Currently selected direct key."""
        return DIRECTKEY_SLUGS[self.coordinator.selected_profile_drink]

    async def async_select_option(self, option: str) -> None:
        """Select a direct key and load its stored parameters."""
        try:
            await self.coordinator.async_select_profile_drink(SLUG_TO_DIRECTKEY[option])
        except MachineError as err:
            raise HomeAssistantError(f"Could not select {option}: {err}") from err
