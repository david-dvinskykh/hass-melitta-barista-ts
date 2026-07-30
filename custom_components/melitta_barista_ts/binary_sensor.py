"""Binary sensors for the Melitta Barista TS integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MelittaConfigEntry
from .const import InfoMessage, MachineType, Manipulation
from .coordinator import MelittaCoordinator, MelittaData
from .entity import MelittaEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MelittaBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Melitta binary sensor."""

    value_fn: Callable[[MelittaData], bool]
    #: Models this sensor applies to; ``None`` means every model.
    models: frozenset[MachineType] | None = None


def _has_info(data: MelittaData, flag: InfoMessage) -> bool:
    return data.status is not None and flag in data.status.info_messages


BINARY_SENSORS: tuple[MelittaBinarySensorDescription, ...] = (
    MelittaBinarySensorDescription(
        key="brewing",
        translation_key="brewing",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda data: data.status is not None and data.status.is_brewing,
    ),
    MelittaBinarySensorDescription(
        key="action_pending",
        translation_key="action_pending",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: (
            data.status is not None
            and data.status.manipulation is not Manipulation.NONE
        ),
    ),
    MelittaBinarySensorDescription(
        key="water_low",
        translation_key="water_low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: (
            data.status is not None
            and data.status.manipulation is Manipulation.FILL_WATER
        ),
    ),
    MelittaBinarySensorDescription(
        key="trays_full",
        translation_key="trays_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: (
            data.status is not None
            and data.status.manipulation
            in (Manipulation.EMPTY_TRAYS, Manipulation.TRAYS_MISSING)
        ),
    ),
    MelittaBinarySensorDescription(
        key="beans_low",
        translation_key="beans_low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _has_info(data, InfoMessage.FILL_BEANS_1),
    ),
    MelittaBinarySensorDescription(
        key="beans_low_2",
        translation_key="beans_low_2",
        device_class=BinarySensorDeviceClass.PROBLEM,
        models=frozenset({MachineType.BARISTA_TS}),
        value_fn=lambda data: _has_info(data, InfoMessage.FILL_BEANS_2),
    ),
    MelittaBinarySensorDescription(
        key="easy_clean_due",
        translation_key="easy_clean_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: _has_info(data, InfoMessage.EASY_CLEAN),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for a coffee machine."""
    coordinator = entry.runtime_data
    machine_type = coordinator.client.info.machine_type
    async_add_entities(
        MelittaBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if description.models is None
        or machine_type is None
        or machine_type in description.models
    )


class MelittaBinarySensor(MelittaEntity, BinarySensorEntity):
    """A boolean derived from the machine's status frame."""

    entity_description: MelittaBinarySensorDescription

    def __init__(
        self,
        coordinator: MelittaCoordinator,
        description: MelittaBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Current state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
