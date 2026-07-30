"""Numbers for the Melitta Barista TS integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MelittaConfigEntry
from .client import MelittaConnectionError
from .const import (
    AUTO_OFF_MAX_MINUTES,
    AUTO_OFF_MIN_MINUTES,
    AUTO_OFF_STEP_MINUTES,
    WATER_HARDNESS_MAX,
    WATER_HARDNESS_MIN,
    Setting,
)
from .coordinator import MelittaCoordinator
from .entity import MelittaControlEntity
from .protocol import ProtocolError

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class MelittaNumberDescription(NumberEntityDescription):
    """Describes a Melitta number."""

    setting: Setting
    #: Converts the register value to what is shown, and back.
    to_state: Callable[[int], float] = float
    to_register: Callable[[float], int] = int


NUMBERS: tuple[MelittaNumberDescription, ...] = (
    MelittaNumberDescription(
        key="water_hardness",
        translation_key="water_hardness",
        setting=Setting.WATER_HARDNESS,
        entity_category=EntityCategory.CONFIG,
        native_min_value=WATER_HARDNESS_MIN,
        native_max_value=WATER_HARDNESS_MAX,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    MelittaNumberDescription(
        key="auto_off_after",
        translation_key="auto_off_after",
        setting=Setting.AUTO_OFF_AFTER,
        entity_category=EntityCategory.CONFIG,
        native_min_value=AUTO_OFF_MIN_MINUTES,
        native_max_value=AUTO_OFF_MAX_MINUTES,
        native_step=AUTO_OFF_STEP_MINUTES,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up numbers for a coffee machine."""
    coordinator = entry.runtime_data
    async_add_entities(
        MelittaNumber(coordinator, description) for description in NUMBERS
    )


class MelittaNumber(MelittaControlEntity, NumberEntity):
    """A numeric machine setting."""

    entity_description: MelittaNumberDescription

    def __init__(
        self,
        coordinator: MelittaCoordinator,
        description: MelittaNumberDescription,
    ) -> None:
        """Initialise the number."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Current setting value."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.settings.get(self.entity_description.setting)
        return None if raw is None else self.entity_description.to_state(raw)

    async def async_set_native_value(self, value: float) -> None:
        """Write the setting to the machine."""
        setting = self.entity_description.setting
        raw = self.entity_description.to_register(value)
        try:
            await self.coordinator.client.async_write_setting(setting, raw)
        except (MelittaConnectionError, ProtocolError) as err:
            raise HomeAssistantError(
                f"Could not set {self.entity_description.key}: {err}"
            ) from err
        if self.coordinator.data is not None:
            self.coordinator.data.settings[setting] = raw
        self.coordinator.invalidate_settings()
        self.async_write_ha_state()
