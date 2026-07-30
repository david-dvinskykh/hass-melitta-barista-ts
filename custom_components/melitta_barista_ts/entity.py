"""Shared entity base for the Melitta Barista TS integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import MelittaCoordinator


class MelittaEntity(CoordinatorEntity[MelittaCoordinator]):
    """Base class tying every entity to the machine device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MelittaCoordinator, key: str) -> None:
        """Initialise the entity and attach it to the machine device."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_translation_key = key
        info = coordinator.client.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=info.model,
            name=f"{MANUFACTURER} {info.model}",
            serial_number=info.serial or None,
            sw_version=info.firmware or None,
        )

    @property
    def available(self) -> bool:
        """Available while the last poll succeeded."""
        return super().available and self.coordinator.data is not None


class MelittaControlEntity(MelittaEntity):
    """Base for entities that write to the machine.

    Controls are only usable while the BLE link is up — a write would
    otherwise sit and time out.
    """

    @property
    def available(self) -> bool:
        """Available only while the machine is actually reachable."""
        return super().available and self.coordinator.client.connected
