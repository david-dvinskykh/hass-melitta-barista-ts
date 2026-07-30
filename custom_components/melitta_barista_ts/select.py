"""Selects for the Melitta Barista TS integration."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MelittaConfigEntry
from .client import MelittaConnectionError
from .const import (
    DEFAULT_PROFILE,
    RECIPE_BY_KEY,
    RECIPE_KEYS,
    TEMPERATURE_OPTIONS,
    Setting,
    available_recipes,
)
from .coordinator import MelittaCoordinator
from .entity import MelittaControlEntity, MelittaEntity
from .protocol import ProtocolError

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)

#: Option shown for profile 0, the machine's built-in set.
DEFAULT_PROFILE_OPTION = "my_coffee"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up selects for a coffee machine."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MelittaRecipeSelect(coordinator),
            MelittaProfileSelect(coordinator),
            MelittaTemperatureSelect(coordinator),
        ]
    )


class MelittaRecipeSelect(MelittaEntity, SelectEntity):
    """Which drink the brew button will prepare.

    This is a Home Assistant-side choice — nothing is sent to the machine
    until the drink is actually brewed.
    """

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Initialise the drink select with the model's recipes."""
        super().__init__(coordinator, "recipe")
        self._attr_options = [
            RECIPE_KEYS[recipe]
            for recipe in available_recipes(coordinator.client.info.machine_type)
        ]

    @property
    def current_option(self) -> str:
        """Currently selected drink."""
        return RECIPE_KEYS[self.coordinator.client.selected_recipe]

    async def async_select_option(self, option: str) -> None:
        """Remember the drink to brew."""
        self.coordinator.client.selected_recipe = RECIPE_BY_KEY[option]
        self.async_write_ha_state()


class MelittaProfileSelect(MelittaEntity, SelectEntity):
    """Which user profile's drink settings brewing should use."""

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Initialise the profile select with the machine's profile names."""
        super().__init__(coordinator, "profile")
        self._attr_options = [DEFAULT_PROFILE_OPTION] + [
            self._profile_option(profile)
            for profile in range(1, coordinator.client.profile_count)
        ]

    def _profile_option(self, profile: int) -> str:
        """Option label for a profile, preferring the machine's own name."""
        name = self.coordinator.client.info.profile_names.get(profile)
        return name or f"profile_{profile}"

    @property
    def current_option(self) -> str:
        """Currently selected profile."""
        profile = self.coordinator.client.active_profile
        if profile == DEFAULT_PROFILE:
            return DEFAULT_PROFILE_OPTION
        return self._profile_option(profile)

    async def async_select_option(self, option: str) -> None:
        """Remember the profile to brew from."""
        if option == DEFAULT_PROFILE_OPTION:
            self.coordinator.client.active_profile = DEFAULT_PROFILE
        else:
            self.coordinator.client.active_profile = self._attr_options.index(option)
        self.async_write_ha_state()


class MelittaTemperatureSelect(MelittaControlEntity, SelectEntity):
    """The machine's global brew temperature setting."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Initialise the brew temperature select."""
        super().__init__(coordinator, "brew_temperature")
        self._attr_options = list(TEMPERATURE_OPTIONS)
        self._values = {value: key for key, value in TEMPERATURE_OPTIONS.items()}

    @property
    def current_option(self) -> str | None:
        """Configured temperature step."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.settings.get(Setting.BREW_TEMPERATURE)
        return self._values.get(value) if value is not None else None

    async def async_select_option(self, option: str) -> None:
        """Write the temperature step to the machine."""
        value = TEMPERATURE_OPTIONS[option]
        try:
            await self.coordinator.client.async_write_setting(
                Setting.BREW_TEMPERATURE, value
            )
        except (MelittaConnectionError, ProtocolError) as err:
            raise HomeAssistantError(
                f"Could not set the brew temperature: {err}"
            ) from err
        if self.coordinator.data is not None:
            self.coordinator.data.settings[Setting.BREW_TEMPERATURE] = value
        self.coordinator.invalidate_settings()
        self.async_write_ha_state()
