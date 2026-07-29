"""Polling coordinator for the Melitta Barista TS Smart."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import MelittaBleClient, MelittaConnectionError
from .const import (
    CUP_COUNTER_BASE_ID,
    DOMAIN,
    MACHINE_TYPE_NAMES,
    MINUTES_PER_DAY,
    RECIPE_SLUGS,
    TOTAL_CUPS_ID,
    TS_ONLY_RECIPES,
    Blend,
    BrewTemperature,
    Intensity,
    MachineType,
    RecipeId,
    SettingId,
)
from .machine import MachineError
from .protocol import MachineStatus, ProtocolError, RecipeComponent

_LOGGER = logging.getLogger(__name__)

#: Registers that change slowly are re-read on this cadence, not every poll.
SETTINGS_REFRESH_SECONDS = 300.0

#: Recipe types whose per-drink counters we surface.
_CUP_COUNTER_TYPES = range(24)


@dataclass
class BrewSettings:
    """Brew parameters applied on top of the machine's stored recipe.

    They are seeded from the machine whenever the selected drink changes, so
    "no override" and "the machine's own value" stay the same thing.
    """

    intensity: Intensity = Intensity.MEDIUM
    temperature: BrewTemperature = BrewTemperature.NORMAL
    portion_ml: int = 40
    blend: Blend = Blend.DEFAULT
    two_cups: bool = False

    def seed_from(self, component: RecipeComponent) -> None:
        """Adopt the values of a recipe component read from the machine."""
        try:
            self.intensity = Intensity(component.intensity)
        except ValueError:
            _LOGGER.debug("Unknown intensity byte %s", component.intensity)
        try:
            self.temperature = BrewTemperature(component.temperature)
        except ValueError:
            _LOGGER.debug("Unknown temperature byte %s", component.temperature)
        self.portion_ml = component.portion_ml


@dataclass
class MelittaData:
    """Everything the entities render."""

    status: MachineStatus | None = None
    firmware: str | None = None
    machine_type: MachineType | None = None
    total_cups: int | None = None
    cup_counters: dict[int, int] = field(default_factory=dict)
    clock_minutes: int | None = None
    auto_off_after: int | None = None
    water_hardness: int | None = None

    @property
    def model_name(self) -> str:
        """Human-readable model, falling back to the product line."""
        if self.machine_type is None:
            return "Barista Smart"
        return MACHINE_TYPE_NAMES.get(self.machine_type, "Barista Smart")


class MelittaCoordinator(DataUpdateCoordinator[MelittaData]):
    """Keeps the machine state fresh and serialises access to the link."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: MelittaBleClient,
        *,
        poll_interval: float,
    ) -> None:
        """Set up the coordinator and the local brew settings."""
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            config_entry=entry,
            update_interval=timedelta(seconds=poll_interval),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=1.0, immediate=True
            ),
        )
        self.client = client
        self.data = MelittaData()
        self.brew_settings = BrewSettings()
        self.selected_drink: RecipeId = RecipeId.ESPRESSO
        self._settings_read_at: float | None = None
        self._counters_due = True
        self._unsub_status = client.add_status_listener(self._handle_pushed_status)

    # -- lifecycle -------------------------------------------------------

    async def async_shutdown(self) -> None:
        """Stop polling and drop the BLE session."""
        self._unsub_status()
        await super().async_shutdown()
        await self.client.async_close()

    @callback
    def _handle_pushed_status(self, status: MachineStatus) -> None:
        """Adopt a status frame that arrived outside of a poll."""
        if self.data.status != status:
            self.data.status = status
            self.async_update_listeners()

    # -- polling ---------------------------------------------------------

    async def _async_update_data(self) -> MelittaData:
        data = self.data
        try:
            await self.client.async_connect()
            data.status = await self.client.async_run(self.client.machine.read_status)

            if data.firmware is None:
                data.firmware = await self._safe_read_firmware()
            if data.machine_type is None:
                data.machine_type = await self._safe_read_machine_type()

            if self._settings_due:
                await self._async_refresh_settings(data)
                self._settings_read_at = monotonic()

            if self._counters_due:
                await self._async_refresh_counters(data)
                self._counters_due = False
        except MelittaConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except (MachineError, ProtocolError) as err:
            raise UpdateFailed(f"communication error: {err}") from err

        return data

    @property
    def _settings_due(self) -> bool:
        """True when the slow-moving registers should be re-read."""
        if self._settings_read_at is None:
            return True
        return monotonic() - self._settings_read_at >= SETTINGS_REFRESH_SECONDS

    async def _async_refresh_settings(self, data: MelittaData) -> None:
        """Read the registers that change without us asking."""
        data.clock_minutes = await self._safe_read_numerical(SettingId.CLOCK)
        data.auto_off_after = await self._safe_read_numerical(SettingId.AUTO_OFF_AFTER)
        data.water_hardness = await self._safe_read_numerical(SettingId.WATER_HARDNESS)

    async def _async_refresh_counters(self, data: MelittaData) -> None:
        """Read the cup counters.

        There are 25 of them and they only move when a drink is made, so this
        runs on demand rather than on the poll interval.
        """
        data.total_cups = await self._safe_read_numerical(TOTAL_CUPS_ID)
        for recipe_type in _CUP_COUNTER_TYPES:
            value = await self._safe_read_numerical(CUP_COUNTER_BASE_ID + recipe_type)
            if value is not None:
                data.cup_counters[recipe_type] = value

    @callback
    def async_invalidate_settings(self) -> None:
        """Force the next poll to re-read the settings registers."""
        self._settings_read_at = None

    @callback
    def async_invalidate_counters(self) -> None:
        """Force the next poll to re-read the cup counters."""
        self._counters_due = True

    async def _safe_read_numerical(self, value_id: int) -> int | None:
        """Read a register, treating an unsupported ID as "no value"."""
        try:
            return await self.client.async_run(
                self.client.machine.read_numerical, int(value_id)
            )
        except (MachineError, ProtocolError) as err:
            _LOGGER.debug("Register %s unavailable: %s", int(value_id), err)
            return None

    async def _safe_read_firmware(self) -> str | None:
        try:
            return await self.client.async_run(self.client.machine.read_firmware)
        except (MachineError, ProtocolError) as err:
            _LOGGER.debug("Firmware version unavailable: %s", err)
            return None

    async def _safe_read_machine_type(self) -> MachineType | None:
        raw = await self._safe_read_numerical(SettingId.MACHINE_TYPE)
        if raw is None:
            return None
        try:
            return MachineType(raw)
        except ValueError:
            _LOGGER.debug("Unknown machine type %s", raw)
            return None

    # -- available drinks -------------------------------------------------

    @property
    def available_drinks(self) -> list[RecipeId]:
        """Recipes this machine offers, narrowed once the model is known."""
        if self.data.machine_type == MachineType.BARISTA_T:
            return [r for r in RecipeId if r not in TS_ONLY_RECIPES]
        return list(RecipeId)

    @property
    def available_drink_slugs(self) -> list[str]:
        """Option values for the drink selector."""
        return [RECIPE_SLUGS[recipe] for recipe in self.available_drinks]

    # -- commands ---------------------------------------------------------

    async def async_select_drink(self, recipe: RecipeId) -> None:
        """Select a drink and seed the brew settings from its recipe."""
        self.selected_drink = recipe
        try:
            stored = await self.client.async_run(
                self.client.machine.read_recipe, int(recipe)
            )
        except (MachineError, ProtocolError) as err:
            _LOGGER.debug("Could not read recipe %s: %s", int(recipe), err)
        else:
            self.brew_settings.seed_from(stored.component1)
        self.async_update_listeners()

    async def async_brew(
        self,
        recipe: RecipeId | None = None,
        *,
        two_cups: bool | None = None,
        intensity: Intensity | None = None,
        temperature: BrewTemperature | None = None,
        portion_ml: int | None = None,
        blend: Blend | None = None,
    ) -> None:
        """Brew a drink, defaulting every parameter to the current settings."""
        settings = self.brew_settings
        target = recipe if recipe is not None else self.selected_drink

        await self.client.async_run(
            self.client.machine.brew,
            target,
            two_cups=settings.two_cups if two_cups is None else two_cups,
            intensity=int(settings.intensity if intensity is None else intensity),
            temperature=int(
                settings.temperature if temperature is None else temperature
            ),
            portion_ml=settings.portion_ml if portion_ml is None else portion_ml,
            blend=int(settings.blend if blend is None else blend) or None,
        )
        self.async_invalidate_counters()
        await self.async_request_refresh()

    async def async_cancel(self) -> None:
        """Cancel whatever process the machine is running."""
        status = self.data.status
        if status is None:
            raise MachineError("machine state is unknown")
        await self.client.async_run(self.client.machine.cancel_process, status.process)
        await self.async_request_refresh()

    async def async_set_clock(self, hour: int, minute: int) -> None:
        """Set the machine's clock."""
        minutes = (hour * 60 + minute) % MINUTES_PER_DAY
        await self.client.async_run(
            self.client.machine.write_numerical, int(SettingId.CLOCK_SET), minutes
        )
        self.async_invalidate_settings()
        await self.async_request_refresh()

    async def async_write_setting(self, setting_id: int, value: int) -> None:
        """Write a numerical register."""
        await self.client.async_run(
            self.client.machine.write_numerical, setting_id, value
        )
        self.async_invalidate_settings()
        await self.async_request_refresh()

    async def async_read_setting(self, setting_id: int) -> int:
        """Read a numerical register."""
        return await self.client.async_run(
            self.client.machine.read_numerical, setting_id
        )


type MelittaConfigEntry = ConfigEntry[MelittaCoordinator]

__all__ = [
    "DOMAIN",
    "BrewSettings",
    "MelittaConfigEntry",
    "MelittaCoordinator",
    "MelittaData",
]
