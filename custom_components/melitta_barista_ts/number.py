"""Number platform for the Melitta Barista TS Smart."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AUTO_OFF_MAX_MINUTES,
    AUTO_OFF_MIN_MINUTES,
    PORTION_MAX_ML,
    PORTION_MIN_ML,
    PORTION_STEP_ML,
    SettingId,
)
from .coordinator import MelittaConfigEntry, MelittaCoordinator
from .entity import MelittaEntity, MelittaLocalEntity
from .machine import MachineError

#: The upper wire limit is 1275 ml; cap the UI at something a cup can hold.
PORTION_UI_MAX_ML = min(PORTION_MAX_ML, 400)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MelittaPortionNumber(coordinator),
            MelittaAutoOffNumber(coordinator),
        ]
    )


class MelittaPortionNumber(MelittaLocalEntity, NumberEntity):
    """Cup size used for the next brew."""

    _attr_translation_key = "portion"
    _attr_device_class = NumberDeviceClass.VOLUME
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS
    _attr_native_min_value = PORTION_MIN_ML
    _attr_native_max_value = PORTION_UI_MAX_ML
    _attr_native_step = PORTION_STEP_ML
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the number."""
        super().__init__(coordinator, "portion")

    @property
    def native_value(self) -> float:
        """Staged cup size."""
        return float(self.coordinator.brew_settings.portion_ml)

    async def async_set_native_value(self, value: float) -> None:
        """Stage a cup size for the next brew."""
        rounded = round(value / PORTION_STEP_ML) * PORTION_STEP_ML
        self.coordinator.brew_settings.portion_ml = int(rounded)
        self.coordinator.async_update_listeners()


class MelittaAutoOffNumber(MelittaEntity, NumberEntity):
    """Idle time after which the machine switches itself off."""

    _attr_translation_key = "auto_off_after"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = AUTO_OFF_MIN_MINUTES
    _attr_native_max_value = AUTO_OFF_MAX_MINUTES
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Register the number."""
        super().__init__(coordinator, "auto_off_after")

    @property
    def available(self) -> bool:
        """Only available once the register has been read successfully."""
        return super().available and self.coordinator.data.auto_off_after is not None

    @property
    def native_value(self) -> float | None:
        """Current auto-off delay."""
        value = self.coordinator.data.auto_off_after
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Write a new auto-off delay to the machine."""
        try:
            await self.coordinator.async_write_setting(
                int(SettingId.AUTO_OFF_AFTER), int(value)
            )
        except MachineError as err:
            raise HomeAssistantError(
                f"Could not set the auto-off delay: {err}"
            ) from err
