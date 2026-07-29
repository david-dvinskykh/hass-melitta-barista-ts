"""Shared entity base for the Melitta Barista TS Smart integration."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import MelittaCoordinator


class MelittaEntity(CoordinatorEntity[MelittaCoordinator]):
    """Base entity bound to one coffee machine."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MelittaCoordinator, key: str) -> None:
        """Attach the entity to the coordinator's device."""
        super().__init__(coordinator)
        address: str = coordinator.config_entry.data[CONF_ADDRESS]
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer=MANUFACTURER,
            model=coordinator.data.model_name,
            name=coordinator.config_entry.title,
            sw_version=coordinator.data.firmware,
        )

    @property
    def available(self) -> bool:
        """Entities are unavailable while the machine cannot be reached."""
        return super().available and self.coordinator.data.status is not None


class MelittaLocalEntity(MelittaEntity):
    """Entity whose value lives in Home Assistant, not on the machine.

    Brew settings are staged locally and only sent when a drink is started,
    so they stay usable while the machine is asleep.
    """

    @property
    def available(self) -> bool:
        """Always available — no machine round-trip is involved."""
        return True
