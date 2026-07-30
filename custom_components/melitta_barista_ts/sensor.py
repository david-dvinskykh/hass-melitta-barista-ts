"""Sensors for the Melitta Barista TS integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MelittaConfigEntry
from .const import (
    RECIPE_KEYS,
    RECIPE_TYPES,
    MachineProcess,
    Manipulation,
    Recipe,
    Setting,
    SubProcess,
)
from .coordinator import MelittaCoordinator, MelittaData
from .entity import MelittaEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MelittaSensorDescription(SensorEntityDescription):
    """Describes a Melitta sensor."""

    value_fn: Callable[[MelittaData], str | int | None]


def _state(data: MelittaData) -> str | None:
    if data.status is None:
        return None
    if data.status.process is None:
        return "unknown_state"
    return data.status.process.name.lower()


def _activity(data: MelittaData) -> str:
    if data.status is None or data.status.sub_process is None:
        return "idle"
    return data.status.sub_process.name.lower()


def _action_required(data: MelittaData) -> str | None:
    if data.status is None:
        return None
    return data.status.manipulation.name.lower()


SENSORS: tuple[MelittaSensorDescription, ...] = (
    MelittaSensorDescription(
        key="state",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=[process.name.lower() for process in MachineProcess]
        + ["unknown_state"],
        value_fn=_state,
    ),
    MelittaSensorDescription(
        key="activity",
        translation_key="activity",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", *(sub.name.lower() for sub in SubProcess)],
        value_fn=_activity,
    ),
    MelittaSensorDescription(
        key="action_required",
        translation_key="action_required",
        device_class=SensorDeviceClass.ENUM,
        options=[manipulation.name.lower() for manipulation in Manipulation],
        value_fn=_action_required,
    ),
    MelittaSensorDescription(
        key="progress",
        translation_key="progress",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda data: data.status.progress if data.status else None,
    ),
    MelittaSensorDescription(
        key="total_drinks",
        translation_key="total_drinks",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="drinks",
        value_fn=lambda data: data.counters.get(0),
    ),
    MelittaSensorDescription(
        key="water_filter",
        translation_key="water_filter",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.settings.get(Setting.WATER_FILTER),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for a coffee machine."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        MelittaSensor(coordinator, description) for description in SENSORS
    ]
    entities.extend(MelittaDrinkCounter(coordinator, recipe) for recipe in RECIPE_TYPES)
    async_add_entities(entities)


class MelittaSensor(MelittaEntity, SensorEntity):
    """A value derived from the machine's status or settings."""

    entity_description: MelittaSensorDescription

    def __init__(
        self,
        coordinator: MelittaCoordinator,
        description: MelittaSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | None:
        """Current value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class MelittaDrinkCounter(MelittaEntity, SensorEntity):
    """Lifetime counter for a single drink.

    Twenty-four of these would clutter the device page, so they ship disabled
    and can be enabled individually.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "drinks"

    def __init__(self, coordinator: MelittaCoordinator, recipe: Recipe) -> None:
        """Initialise the counter for one drink."""
        super().__init__(coordinator, f"counter_{RECIPE_KEYS[recipe]}")
        self._recipe = recipe
        self._attr_translation_key = "drink_counter"
        self._attr_translation_placeholders = {
            "drink": recipe.name.replace("_", " ").title()
        }

    @property
    def native_value(self) -> int | None:
        """Number of drinks of this kind the machine has made."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.counters.get(int(self._recipe))
