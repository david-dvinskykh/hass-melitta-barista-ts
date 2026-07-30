"""Sensor platform for the Melitta Barista TS Smart."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CARE_SLUGS,
    RECIPE_DISPLAY_NAMES,
    CareProgramme,
    MachineProcess,
    Manipulation,
    RecipeId,
    SubProcess,
)
from .coordinator import MelittaConfigEntry, MelittaCoordinator, MelittaData
from .entity import MelittaEntity

UNKNOWN: Final = "unknown_state"

PROCESS_SLUGS: Final[dict[MachineProcess, str]] = {
    MachineProcess.READY: "ready",
    MachineProcess.PRODUCT: "brewing",
    MachineProcess.CLEANING: "cleaning",
    MachineProcess.DESCALING: "descaling",
    MachineProcess.FILTER_INSERT: "filter_insert",
    MachineProcess.FILTER_REPLACE: "filter_replace",
    MachineProcess.FILTER_REMOVE: "filter_remove",
    MachineProcess.SWITCH_OFF: "switching_off",
    MachineProcess.EASY_CLEAN: "easy_clean",
    MachineProcess.INTENSIVE_CLEAN: "intensive_clean",
    MachineProcess.EVAPORATING: "evaporating",
    MachineProcess.BUSY: "busy",
}

SUB_PROCESS_SLUGS: Final[dict[SubProcess, str]] = {
    SubProcess.NONE: "idle",
    SubProcess.GRINDING: "grinding",
    SubProcess.COFFEE: "extracting",
    SubProcess.STEAM: "steaming",
    SubProcess.WATER: "dispensing_water",
    SubProcess.PREPARE: "preparing",
}

MANIPULATION_SLUGS: Final[dict[Manipulation, str]] = {
    Manipulation.NONE: "none",
    Manipulation.BU_REMOVED: "brew_unit_removed",
    Manipulation.TRAYS_MISSING: "trays_missing",
    Manipulation.EMPTY_TRAYS: "empty_trays",
    Manipulation.FILL_WATER: "fill_water",
    Manipulation.CLOSE_POWDER_LID: "close_powder_lid",
    Manipulation.FILL_POWDER: "fill_powder",
}


@dataclass(frozen=True, kw_only=True)
class MelittaSensorDescription(SensorEntityDescription):
    """Describes a Melitta sensor."""

    value_fn: Callable[[MelittaData], str | int | None]


def _process(data: MelittaData) -> str | None:
    if data.status is None:
        return None
    member = data.status.process_enum
    return PROCESS_SLUGS.get(member, UNKNOWN) if member else UNKNOWN


def _sub_process(data: MelittaData) -> str | None:
    if data.status is None:
        return None
    member = data.status.sub_process_enum
    return SUB_PROCESS_SLUGS.get(member, UNKNOWN) if member is not None else UNKNOWN


def _attention(data: MelittaData) -> str | None:
    if data.status is None:
        return None
    member = data.status.manipulation_enum
    return MANIPULATION_SLUGS.get(member, UNKNOWN) if member is not None else UNKNOWN


def _clock(data: MelittaData) -> str | None:
    minutes = data.clock_minutes
    if minutes is None or not 0 <= minutes < 24 * 60:
        return None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


SENSORS: Final[tuple[MelittaSensorDescription, ...]] = (
    MelittaSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[*PROCESS_SLUGS.values(), UNKNOWN],
        value_fn=_process,
    ),
    MelittaSensorDescription(
        key="sub_status",
        translation_key="sub_status",
        device_class=SensorDeviceClass.ENUM,
        options=[*SUB_PROCESS_SLUGS.values(), UNKNOWN],
        value_fn=_sub_process,
    ),
    MelittaSensorDescription(
        key="attention",
        translation_key="attention",
        device_class=SensorDeviceClass.ENUM,
        options=[*MANIPULATION_SLUGS.values(), UNKNOWN],
        value_fn=_attention,
    ),
    MelittaSensorDescription(
        key="progress",
        translation_key="progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: None if data.status is None else data.status.progress,
    ),
    MelittaSensorDescription(
        key="total_cups",
        translation_key="total_cups",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data.total_cups,
    ),
    MelittaSensorDescription(
        key="clock",
        translation_key="clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_clock,
    ),
    MelittaSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.firmware,
    ),
)


def _care_count(programme: CareProgramme) -> Callable[[MelittaData], int | None]:
    """Read one care tally out of the polled data."""
    return lambda data: data.care_counts.get(programme)


#: How many times the machine has run each care programme — the same tallies
#: its own Statistics → Care screen shows. They say what has been done, not
#: what is due: the machine asks for the next one on its display.
CARE_SENSORS: Final[tuple[MelittaSensorDescription, ...]] = tuple(
    MelittaSensorDescription(
        key=slug,
        translation_key=slug,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_care_count(programme),
    )
    for programme, slug in CARE_SLUGS.items()
)

#: Per-drink counters live at ``recipe_id - 200`` in the counter block.
_COUNTER_RECIPES: Final[tuple[RecipeId, ...]] = tuple(RecipeId)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor entities."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        MelittaSensor(coordinator, description)
        for description in (*SENSORS, *CARE_SENSORS)
    ]
    entities.extend(
        MelittaCupCounter(coordinator, recipe) for recipe in _COUNTER_RECIPES
    )
    async_add_entities(entities)


class MelittaSensor(MelittaEntity, SensorEntity):
    """A value read from the machine."""

    entity_description: MelittaSensorDescription

    def __init__(
        self, coordinator: MelittaCoordinator, description: MelittaSensorDescription
    ) -> None:
        """Bind the sensor to its description."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | None:
        """Current value."""
        return self.entity_description.value_fn(self.coordinator.data)


class MelittaCupCounter(MelittaEntity, SensorEntity):
    """Lifetime cup counter for a single drink."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MelittaCoordinator, recipe: RecipeId) -> None:
        """Bind the counter to a recipe."""
        super().__init__(coordinator, f"cups_{int(recipe)}")
        self._recipe_type = int(recipe) - int(RecipeId.ESPRESSO)
        self._attr_translation_key = "drink_cups"
        self._attr_translation_placeholders = {"drink": RECIPE_DISPLAY_NAMES[recipe]}

    @property
    def native_value(self) -> int | None:
        """Number of cups of this drink the machine has made."""
        return self.coordinator.data.cup_counters.get(self._recipe_type)
