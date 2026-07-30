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
    DIRECTKEY_DISPLAY_NAMES,
    DOMAIN,
    MACHINE_TYPE_NAMES,
    MINUTES_PER_DAY,
    MY_COFFEE_NAME,
    MY_COFFEE_PROFILE,
    RECIPE_SLUGS,
    TOTAL_CUPS_ID,
    TS_ONLY_RECIPES,
    Blend,
    BrewTemperature,
    DirectKeyCategory,
    Intensity,
    MachineType,
    RecipeId,
    SettingId,
    directkey_recipe_id,
    profile_name_id,
    user_profile_count,
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
    profile_names: dict[int, str] = field(default_factory=dict)

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
        self.selected_profile: int = MY_COFFEE_PROFILE
        self.selected_profile_drink: DirectKeyCategory = DirectKeyCategory.ESPRESSO
        self._settings_read_at: float | None = None
        self._counters_due = True
        self._first_refresh_done = False
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

            # The first refresh runs inside async_setup_entry, which Home
            # Assistant will cancel if it drags on. Connecting and reading the
            # status is enough to prove the machine is there; the ~35 further
            # round trips for settings, profiles and counters wait for the
            # next poll.
            if self._first_refresh_done:
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

        self._first_refresh_done = True
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
        await self._async_refresh_profiles(data)

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

    # -- profiles ---------------------------------------------------------

    async def _async_refresh_profiles(self, data: MelittaData) -> None:
        """Read the user profile names.

        "My Coffee" (profile 0) has no name register — the machine labels it
        itself. A profile the user has never named reads back empty, and gets
        a positional placeholder so it still appears in the picker.
        """
        names: dict[int, str] = {MY_COFFEE_PROFILE: MY_COFFEE_NAME}

        for profile in range(1, user_profile_count(data.machine_type) + 1):
            try:
                stored = await self.client.async_run(
                    self.client.machine.read_alphanumeric, profile_name_id(profile)
                )
            except (MachineError, ProtocolError) as err:
                _LOGGER.debug("Profile %s name unavailable: %s", profile, err)
                stored = None
            names[profile] = (stored or "").strip() or f"Profile {profile}"

        data.profile_names = names

    @property
    def profile_options(self) -> list[str]:
        """Labels for the profile selector, in machine order.

        Profile names come from the machine, so two profiles can carry the
        same name. Duplicates get their number appended, otherwise the
        selector would silently collapse them onto one option.
        """
        labels: list[str] = []
        seen: dict[str, int] = {}
        for profile, name in sorted(self.data.profile_names.items()):
            seen[name] = seen.get(name, 0) + 1
            labels.append(name if seen[name] == 1 else f"{name} ({profile})")
        return labels

    def profile_for_option(self, option: str) -> int | None:
        """Map a selector label back to its profile number."""
        for profile, label in zip(
            sorted(self.data.profile_names), self.profile_options, strict=True
        ):
            if label == option:
                return profile
        return None

    @property
    def selected_profile_option(self) -> str | None:
        """The label currently shown by the profile selector."""
        options = self.profile_options
        order = sorted(self.data.profile_names)
        if self.selected_profile in order:
            return options[order.index(self.selected_profile)]
        return None

    @property
    def selected_profile_name(self) -> str:
        """Plain name of the selected profile, for logs and machine display."""
        return self.data.profile_names.get(
            self.selected_profile, f"Profile {self.selected_profile}"
        )

    async def async_select_profile(self, profile: int) -> None:
        """Switch the active profile and reload the selected direct key."""
        self.selected_profile = profile
        await self.async_select_profile_drink(self.selected_profile_drink)

    async def async_select_profile_drink(self, category: DirectKeyCategory) -> None:
        """Select a direct key and seed the brew settings from its recipe."""
        self.selected_profile_drink = category
        recipe_id = directkey_recipe_id(self.selected_profile, category)
        try:
            stored = await self.client.async_run(
                self.client.machine.read_recipe, recipe_id
            )
        except (MachineError, ProtocolError) as err:
            _LOGGER.debug("Could not read direct key %s: %s", recipe_id, err)
        else:
            self.brew_settings.seed_from(stored.component1)
        self.async_update_listeners()

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
        target = recipe if recipe is not None else self.selected_drink

        await self._async_brew_slot(
            int(target),
            name=None,
            two_cups=two_cups,
            intensity=intensity,
            temperature=temperature,
            portion_ml=portion_ml,
            blend=blend,
        )

    async def async_brew_profile(
        self,
        profile: int | None = None,
        category: DirectKeyCategory | None = None,
        *,
        two_cups: bool | None = None,
        intensity: Intensity | None = None,
        temperature: BrewTemperature | None = None,
        portion_ml: int | None = None,
        blend: Blend | None = None,
    ) -> None:
        """Brew the drink a profile stores under one of its direct keys."""
        target_profile = self.selected_profile if profile is None else profile
        target_category = self.selected_profile_drink if category is None else category

        known = self.data.profile_names
        if known and target_profile not in known:
            raise MachineError(
                f"this machine has no profile {target_profile} "
                f"(it has {min(known)} to {max(known)})"
            )

        await self._async_brew_slot(
            directkey_recipe_id(target_profile, target_category),
            name=DIRECTKEY_DISPLAY_NAMES[target_category],
            two_cups=two_cups,
            intensity=intensity,
            temperature=temperature,
            portion_ml=portion_ml,
            blend=blend,
        )

    async def _async_brew_slot(
        self,
        recipe_id: int,
        *,
        name: str | None,
        two_cups: bool | None,
        intensity: Intensity | None,
        temperature: BrewTemperature | None,
        portion_ml: int | None,
        blend: Blend | None,
    ) -> None:
        """Brew a recipe slot, filling unset parameters from the staged settings."""
        settings = self.brew_settings

        await self.client.async_run(
            self.client.machine.brew,
            recipe_id,
            name=name,
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
