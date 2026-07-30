"""The machine's wall clock, exposed as a time entity.

The clock is stored as minutes since midnight and read/written through two
different registers.
"""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MelittaConfigEntry
from .client import MelittaConnectionError
from .const import Setting
from .coordinator import MelittaCoordinator
from .entity import MelittaControlEntity
from .protocol import ProtocolError

PARALLEL_UPDATES = 1

MINUTES_PER_DAY = 24 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the machine clock."""
    async_add_entities([MelittaClock(entry.runtime_data)])


class MelittaClock(MelittaControlEntity, TimeEntity):
    """The clock shown on the machine's display."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MelittaCoordinator) -> None:
        """Initialise the clock entity."""
        super().__init__(coordinator, "clock")

    @property
    def native_value(self) -> time | None:
        """Time currently set on the machine."""
        if self.coordinator.data is None:
            return None
        minutes = self.coordinator.data.settings.get(Setting.CLOCK_READ)
        if minutes is None:
            return None
        minutes %= MINUTES_PER_DAY
        return time(hour=minutes // 60, minute=minutes % 60)

    async def async_set_value(self, value: time) -> None:
        """Set the machine clock."""
        minutes = value.hour * 60 + value.minute
        try:
            await self.coordinator.client.async_write_setting(
                Setting.CLOCK_WRITE, minutes
            )
        except (MelittaConnectionError, ProtocolError) as err:
            raise HomeAssistantError(f"Could not set the machine clock: {err}") from err
        if self.coordinator.data is not None:
            self.coordinator.data.settings[Setting.CLOCK_READ] = minutes
        self.coordinator.invalidate_settings()
        self.async_write_ha_state()
