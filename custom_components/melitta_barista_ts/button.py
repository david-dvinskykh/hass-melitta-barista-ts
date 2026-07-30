"""Buttons for the Melitta Barista TS integration.

One button brews whatever the recipe select is set to; the rest cover the
machine's own programs. Parametrised brewing lives in the services.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MelittaConfigEntry
from .client import MelittaClient, MelittaConnectionError
from .const import MAINTENANCE_PROGRAMS, MachineProcess
from .coordinator import MelittaCoordinator
from .entity import MelittaControlEntity
from .protocol import ProtocolError

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class MelittaButtonDescription(ButtonEntityDescription):
    """Describes a Melitta button."""

    press_fn: Callable[[MelittaClient], Coroutine[Any, Any, None]]


def _maintenance(program: str) -> Callable[[MelittaClient], Coroutine[Any, Any, None]]:
    """Build a press handler that starts a maintenance program."""
    process: MachineProcess = MAINTENANCE_PROGRAMS[program]

    async def _press(client: MelittaClient) -> None:
        await client.async_start_maintenance(process)

    return _press


BUTTONS: tuple[MelittaButtonDescription, ...] = (
    MelittaButtonDescription(
        key="brew",
        translation_key="brew",
        press_fn=lambda client: client.async_brew(client.selected_recipe),
    ),
    MelittaButtonDescription(
        key="cancel",
        translation_key="cancel",
        press_fn=lambda client: client.async_cancel(),
    ),
    MelittaButtonDescription(
        key="confirm_prompt",
        translation_key="confirm_prompt",
        press_fn=lambda client: client.async_confirm_prompt(),
    ),
    MelittaButtonDescription(
        key="rinse",
        translation_key="rinse",
        entity_category=EntityCategory.CONFIG,
        press_fn=_maintenance("rinse"),
    ),
    MelittaButtonDescription(
        key="easy_clean",
        translation_key="easy_clean",
        entity_category=EntityCategory.CONFIG,
        press_fn=_maintenance("easy_clean"),
    ),
    MelittaButtonDescription(
        key="intensive_clean",
        translation_key="intensive_clean",
        entity_category=EntityCategory.CONFIG,
        press_fn=_maintenance("intensive_clean"),
    ),
    MelittaButtonDescription(
        key="descale",
        translation_key="descale",
        entity_category=EntityCategory.CONFIG,
        press_fn=_maintenance("descale"),
    ),
    MelittaButtonDescription(
        key="filter_insert",
        translation_key="filter_insert",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        press_fn=_maintenance("filter_insert"),
    ),
    MelittaButtonDescription(
        key="filter_replace",
        translation_key="filter_replace",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        press_fn=_maintenance("filter_replace"),
    ),
    MelittaButtonDescription(
        key="filter_remove",
        translation_key="filter_remove",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        press_fn=_maintenance("filter_remove"),
    ),
    MelittaButtonDescription(
        key="switch_off",
        translation_key="switch_off",
        entity_category=EntityCategory.CONFIG,
        press_fn=_maintenance("switch_off"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MelittaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons for a coffee machine."""
    coordinator = entry.runtime_data
    async_add_entities(
        MelittaButton(coordinator, description) for description in BUTTONS
    )


class MelittaButton(MelittaControlEntity, ButtonEntity):
    """A one-shot machine action."""

    entity_description: MelittaButtonDescription

    def __init__(
        self,
        coordinator: MelittaCoordinator,
        description: MelittaButtonDescription,
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Run the action, then refresh so the new state shows up at once."""
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except (MelittaConnectionError, ProtocolError) as err:
            raise HomeAssistantError(
                f"Coffee machine rejected {self.entity_description.key}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
