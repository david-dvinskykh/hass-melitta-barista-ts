"""Binary sensor platform for the Melitta Barista TS Smart."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CARE_DUE_SLUGS,
    CareDue,
    InfoMessage,
    MachineProcess,
    Manipulation,
)
from .coordinator import MelittaConfigEntry, MelittaCoordinator, MelittaData
from .entity import MelittaEntity


@dataclass(frozen=True, kw_only=True)
class MelittaBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Melitta binary sensor."""

    value_fn: Callable[[MelittaData], bool | None]


def _manipulation_is(target: Manipulation) -> Callable[[MelittaData], bool | None]:
    def _check(data: MelittaData) -> bool | None:
        if data.status is None:
            return None
        return data.status.manipulation == target

    return _check


def _info_flag(flag: InfoMessage) -> Callable[[MelittaData], bool | None]:
    def _check(data: MelittaData) -> bool | None:
        if data.status is None:
            return None
        return bool(data.status.info_flags & flag)

    return _check


def _brewing(data: MelittaData) -> bool | None:
    if data.status is None:
        return None
    return data.status.process == MachineProcess.PRODUCT


def _maintenance(data: MelittaData) -> bool | None:
    if data.status is None:
        return None
    return data.status.process in {
        MachineProcess.CLEANING,
        MachineProcess.DESCALING,
        MachineProcess.EASY_CLEAN,
        MachineProcess.INTENSIVE_CLEAN,
        MachineProcess.EVAPORATING,
    }


def _care_due(programme: CareDue) -> Callable[[MelittaData], bool | None]:
    """True while the machine is asking for that programme to be run."""
    return lambda data: data.care_due.get(programme)


#: What the machine puts on its display, as a flag per programme. Confirmed
#: on the coffee system: the flag stood at 1 while the machine asked for the
#: cleaning and dropped to 0 the moment it had been done. The other two sit
#: at the same offset in the same block, ten registers apart, in the order
#: the machine's own Care screen lists them.
CARE_DUE_SENSORS: Final[tuple[MelittaBinarySensorDescription, ...]] = tuple(
    MelittaBinarySensorDescription(
        key=slug,
        translation_key=slug,
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_care_due(programme),
    )
    for programme, slug in CARE_DUE_SLUGS.items()
)

BINARY_SENSORS: Final[tuple[MelittaBinarySensorDescription, ...]] = (
    MelittaBinarySensorDescription(
        key="brewing",
        translation_key="brewing",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_brewing,
    ),
    MelittaBinarySensorDescription(
        key="maintenance",
        translation_key="maintenance",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_maintenance,
    ),
    MelittaBinarySensorDescription(
        key="water_tank_empty",
        translation_key="water_tank_empty",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_manipulation_is(Manipulation.FILL_WATER),
    ),
    MelittaBinarySensorDescription(
        key="trays_full",
        translation_key="trays_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_manipulation_is(Manipulation.EMPTY_TRAYS),
    ),
    MelittaBinarySensorDescription(
        key="trays_missing",
        translation_key="trays_missing",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_manipulation_is(Manipulation.TRAYS_MISSING),
    ),
    MelittaBinarySensorDescription(
        key="brew_unit_removed",
        translation_key="brew_unit_removed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_manipulation_is(Manipulation.BU_REMOVED),
    ),
    MelittaBinarySensorDescription(
        key="powder_lid_open",
        translation_key="powder_lid_open",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_manipulation_is(Manipulation.CLOSE_POWDER_LID),
    ),
    MelittaBinarySensorDescription(
        key="beans_empty_hopper_1",
        translation_key="beans_empty_hopper_1",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_info_flag(InfoMessage.FILL_BEANS_1),
    ),
    MelittaBinarySensorDescription(
        key="beans_empty_hopper_2",
        translation_key="beans_empty_hopper_2",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_info_flag(InfoMessage.FILL_BEANS_2),
    ),
    MelittaBinarySensorDescription(
        key="easy_clean_required",
        translation_key="easy_clean_required",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_info_flag(InfoMessage.EASY_CLEAN),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor entities."""
    async_add_entities(
        MelittaBinarySensor(entry.runtime_data, description)
        for description in (*BINARY_SENSORS, *CARE_DUE_SENSORS)
    )


class MelittaBinarySensor(MelittaEntity, BinarySensorEntity):
    """A boolean condition reported by the machine."""

    entity_description: MelittaBinarySensorDescription

    def __init__(
        self,
        coordinator: MelittaCoordinator,
        description: MelittaBinarySensorDescription,
    ) -> None:
        """Bind the sensor to its description."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Current state."""
        return self.entity_description.value_fn(self.coordinator.data)
