"""Switches for the Melitta Barista TS integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MelittaConfigEntry
from .client import MelittaConnectionError
from .const import MachineType, Setting
from .coordinator import MelittaCoordinator
from .entity import MelittaControlEntity
from .protocol import ProtocolError

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class MelittaSwitchDescription(SwitchEntityDescription):
    """Describes a Melitta switch."""

    setting: Setting
    #: True when the register stores "off" instead of "on".
    inverted: bool = False
    #: Models this switch applies to; ``None`` means every model.
    models: frozenset[MachineType] | None = None


SWITCHES: tuple[MelittaSwitchDescription, ...] = (
    MelittaSwitchDescription(
        key="energy_saving",
        translation_key="energy_saving",
        setting=Setting.ENERGY_SAVING,
        entity_category=EntityCategory.CONFIG,
    ),
    MelittaSwitchDescription(
        key="rinse_on_start",
        translation_key="rinse_on_start",
        setting=Setting.RINSING_OFF,
        inverted=True,
        entity_category=EntityCategory.CONFIG,
    ),
    MelittaSwitchDescription(
        key="auto_bean_select",
        translation_key="auto_bean_select",
        setting=Setting.AUTO_BEAN_SELECT,
        entity_category=EntityCategory.CONFIG,
        models=frozenset({MachineType.BARISTA_TS}),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switches for a coffee machine."""
    coordinator = entry.runtime_data
    machine_type = coordinator.client.info.machine_type
    async_add_entities(
        MelittaSwitch(coordinator, description)
        for description in SWITCHES
        if description.models is None
        or machine_type is None
        or machine_type in description.models
    )


class MelittaSwitch(MelittaControlEntity, SwitchEntity):
    """A boolean machine setting."""

    entity_description: MelittaSwitchDescription

    def __init__(
        self,
        coordinator: MelittaCoordinator,
        description: MelittaSwitchDescription,
    ) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Current state of the setting."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.settings.get(self.entity_description.setting)
        if raw is None:
            return None
        return bool(raw) != self.entity_description.inverted

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the setting."""
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the setting."""
        await self._async_write(False)

    async def _async_write(self, state: bool) -> None:
        setting = self.entity_description.setting
        raw = int(state != self.entity_description.inverted)
        try:
            await self.coordinator.client.async_write_setting(setting, raw)
        except (MelittaConnectionError, ProtocolError) as err:
            raise HomeAssistantError(
                f"Could not change {self.entity_description.key}: {err}"
            ) from err
        if self.coordinator.data is not None:
            self.coordinator.data.settings[setting] = raw
        self.coordinator.invalidate_settings()
        self.async_write_ha_state()
