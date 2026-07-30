"""BLE transport for Melitta Barista T/TS Smart machines.

Owns the GATT connection and turns the byte-level :mod:`.protocol` into the
handful of operations the entities need. All machine access is serialised
through a single lock: the machine has one BLE slot and drops frames that
overlap.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    CHAR_NOTIFY,
    CHAR_WRITE,
    COMMAND_SETTLE_DELAY,
    CONNECT_TIMEOUT,
    CUP_COUNTER_BASE,
    DEFAULT_PROFILE,
    FREESTYLE_RECIPE_TYPE,
    MACHINE_TYPE_REGISTER,
    MAX_COUNTER_MISSES,
    MODEL_NAMES,
    PROFILE_NAME_REGISTERS,
    RECIPE_CATEGORIES,
    RECIPE_TYPES,
    TEMP_RECIPE_ID,
    TEMP_RECIPE_NAME_ID,
    TOTAL_CUPS_REGISTER,
    ComponentProcess,
    MachineProcess,
    MachineType,
    Recipe,
    Setting,
    direct_key_id,
    machine_type_from_name,
    profile_count,
    recipe_key_for_type,
)
from .protocol import (
    MachineStatus,
    MelittaProtocol,
    ProtocolError,
    RecipeComponent,
)

_LOGGER = logging.getLogger(__name__)


class MelittaConnectionError(Exception):
    """The machine could not be reached."""


class MachineNotReady(MelittaConnectionError):
    """The machine is reachable but cannot start a drink right now.

    A subclass of :class:`MelittaConnectionError` so callers that only care
    about "the command did not happen" need no extra handler.
    """


@dataclass(slots=True)
class MachineInfo:
    """Everything we learn about the machine once per connection."""

    machine_type: MachineType | None = None
    firmware: str | None = None
    serial: str | None = None
    profile_names: dict[int, str] = field(default_factory=dict)

    @property
    def model(self) -> str:
        """Human-readable model name."""
        if self.machine_type is None:
            return "Barista Smart"
        return MODEL_NAMES[self.machine_type]


class MelittaClient:
    """Connection-oriented client for one coffee machine."""

    def __init__(
        self,
        address: str,
        ble_device: BLEDevice | None = None,
        *,
        frame_timeout: float | None = None,
    ) -> None:
        """Initialise the client for one machine address."""
        self._address = address
        self._ble_device = ble_device
        self._client: BleakClientWithServiceCache | None = None
        self._protocol = MelittaProtocol(
            **({"frame_timeout": frame_timeout} if frame_timeout else {})
        )
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._info = MachineInfo()
        self._status: MachineStatus | None = None
        self._active_profile = DEFAULT_PROFILE
        self._selected_recipe = Recipe.ESPRESSO
        self._status_callbacks: list[Callable[[MachineStatus], None]] = []
        self._disconnect_callbacks: list[Callable[[], None]] = []
        self._device_lookup: Callable[[], BLEDevice | None] | None = None
        self._expected_disconnect = False
        self._protocol.set_status_callback(self._on_status)

    # -- introspection --------------------------------------------------

    @property
    def address(self) -> str:
        """The machine's BLE address."""
        return self._address

    @property
    def connected(self) -> bool:
        """Whether a usable, handshaken link exists."""
        return (
            self._client is not None
            and self._client.is_connected
            and self._protocol.handshake_complete
        )

    @property
    def info(self) -> MachineInfo:
        """Static machine information from the current session."""
        return self._info

    @property
    def status(self) -> MachineStatus | None:
        """Most recent status frame."""
        return self._status

    @property
    def active_profile(self) -> int:
        """Profile used when brewing."""
        return self._active_profile

    @active_profile.setter
    def active_profile(self, profile: int) -> None:
        self._active_profile = profile

    @property
    def selected_recipe(self) -> Recipe:
        """Recipe the brew button will prepare."""
        return self._selected_recipe

    @selected_recipe.setter
    def selected_recipe(self, recipe: Recipe) -> None:
        self._selected_recipe = recipe

    @property
    def profile_count(self) -> int:
        """Number of selectable profiles on this model."""
        return profile_count(self._info.machine_type)

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Refresh the cached device from a new advertisement."""
        self._ble_device = ble_device

    def set_device_lookup(self, lookup: Callable[[], BLEDevice | None]) -> None:
        """Install a callable returning the freshest known ``BLEDevice``.

        bleak-retry-connector calls this between retries so a reconnect can
        pick up a device object from a different (e.g. closer) adapter.
        """
        self._device_lookup = lookup

    def add_status_callback(self, callback: Callable[[MachineStatus], None]) -> None:
        """Subscribe to pushed status updates."""
        self._status_callbacks.append(callback)

    def add_disconnect_callback(self, callback: Callable[[], None]) -> None:
        """Subscribe to unexpected disconnects."""
        self._disconnect_callbacks.append(callback)

    # -- connection lifecycle -------------------------------------------

    async def connect(self) -> None:
        """Connect, subscribe and handshake. No-op when already connected."""
        if self.connected:
            return
        async with self._connect_lock:
            if self.connected:
                return
            await self._disconnect_locked()
            device = self._resolve_device()
            if device is None:
                raise MelittaConnectionError(
                    f"{self._address} is not in range (no advertisement seen)"
                )

            _LOGGER.debug("Connecting to %s", self._address)
            self._expected_disconnect = False
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self._address,
                    self._on_disconnected,
                    max_attempts=3,
                    ble_device_callback=self._device_lookup,
                    timeout=CONNECT_TIMEOUT,
                )
            except (BleakError, TimeoutError, OSError) as err:
                raise MelittaConnectionError(f"connect failed: {err}") from err

            self._client = client
            self._protocol.reset()

            try:
                await client.start_notify(CHAR_NOTIFY, self._on_notification)
                if not await self._protocol.handshake(self._write):
                    raise MelittaConnectionError("handshake rejected by machine")
                await self._read_machine_info()
            except MelittaConnectionError:
                await self._disconnect_locked()
                raise
            except (BleakError, ProtocolError, TimeoutError, OSError) as err:
                await self._disconnect_locked()
                raise MelittaConnectionError(f"setup failed: {err}") from err

            _LOGGER.info(
                "Connected to %s (%s, firmware %s)",
                self._address,
                self._info.model,
                self._info.firmware or "unknown",
            )

    async def disconnect(self) -> None:
        """Close the link deliberately."""
        async with self._connect_lock:
            self._expected_disconnect = True
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        client, self._client = self._client, None
        self._protocol.reset()
        if client is None:
            return
        try:
            await client.disconnect()
        except (BleakError, TimeoutError, OSError) as err:
            _LOGGER.debug("Error while disconnecting %s: %s", self._address, err)

    def _resolve_device(self) -> BLEDevice | None:
        if self._device_lookup and (device := self._device_lookup()):
            self._ble_device = device
        return self._ble_device

    def _on_disconnected(self, _client: BleakClientWithServiceCache) -> None:
        self._protocol.reset()
        self._client = None
        if self._expected_disconnect:
            return
        _LOGGER.debug("%s disconnected unexpectedly", self._address)
        for callback in self._disconnect_callbacks:
            callback()

    def _on_notification(self, _sender: object, data: bytearray) -> None:
        self._protocol.feed(bytes(data))

    def _on_status(self, status: MachineStatus) -> None:
        self._status = status
        for callback in self._status_callbacks:
            callback(status)

    async def _write(self, data: bytes) -> None:
        client = self._client
        if client is None or not client.is_connected:
            raise MelittaConnectionError("not connected")
        await client.write_gatt_char(CHAR_WRITE, data, response=False)

    # -- reads ----------------------------------------------------------

    async def _read_machine_info(self) -> None:
        """Populate :attr:`info`. Individual reads are allowed to fail."""
        self._info.machine_type = machine_type_from_name(
            self._ble_device.name if self._ble_device else None
        )

        try:
            raw_type = await self._protocol.read_number(
                self._write, MACHINE_TYPE_REGISTER
            )
        except ProtocolError as err:
            _LOGGER.debug("Machine type register unavailable: %s", err)
        else:
            try:
                self._info.machine_type = MachineType(raw_type)
            except ValueError:
                _LOGGER.debug("Unknown machine type id %s", raw_type)

        for attr, coro in (
            ("firmware", self._protocol.read_version(self._write)),
            ("serial", self._protocol.read_serial(self._write)),
        ):
            try:
                setattr(self._info, attr, await coro)
            except (ProtocolError, BleakError) as err:
                _LOGGER.debug("Could not read %s: %s", attr, err)

        await self._read_profile_names()

    async def _read_profile_names(self) -> None:
        names: dict[int, str] = {}
        for profile in range(1, self.profile_count):
            register = PROFILE_NAME_REGISTERS.get(profile)
            if register is None:
                continue
            try:
                name = await self._protocol.read_text(self._write, register)
            except (ProtocolError, BleakError) as err:
                _LOGGER.debug("Could not read profile %d name: %s", profile, err)
                continue
            if name:
                names[profile] = name
        self._info.profile_names = names

    async def async_update_status(self) -> MachineStatus:
        """Poll the machine status, connecting first if needed."""
        await self.connect()
        async with self._lock:
            status = await self._protocol.read_status(self._write)
        self._status = status
        return status

    async def async_read_settings(self) -> dict[Setting, int]:
        """Read every settings register we expose. Missing ones are skipped."""
        wanted = (
            Setting.WATER_HARDNESS,
            Setting.ENERGY_SAVING,
            Setting.AUTO_OFF_AFTER,
            Setting.AUTO_BEAN_SELECT,
            Setting.RINSING_OFF,
            Setting.BREW_TEMPERATURE,
            Setting.WATER_FILTER,
            Setting.CLOCK_READ,
        )
        await self.connect()
        values: dict[Setting, int] = {}
        async with self._lock:
            for setting in wanted:
                if (
                    setting is Setting.AUTO_BEAN_SELECT
                    and self._info.machine_type is MachineType.BARISTA_T
                ):
                    continue  # single hopper, register is absent
                try:
                    values[setting] = await self._protocol.read_number(
                        self._write, setting
                    )
                except ProtocolError as err:
                    _LOGGER.debug("Setting %s unavailable: %s", setting.name, err)
        return values

    async def async_read_counters(self) -> dict[int, int]:
        """Read drink counters, keyed by recipe id, plus the grand total.

        The total is stored under key ``0``. This is 25 round trips, so it
        gives up early on firmware that does not answer at all rather than
        holding the machine's single BLE slot for a timeout per register.
        """
        await self.connect()
        counters: dict[int, int] = {}
        async with self._lock:
            try:
                counters[0] = await self._protocol.read_number(
                    self._write, TOTAL_CUPS_REGISTER
                )
            except ProtocolError as err:
                _LOGGER.debug("Total counter unavailable, skipping counters: %s", err)
                return counters

            misses = 0
            for recipe, recipe_type in RECIPE_TYPES.items():
                try:
                    counters[int(recipe)] = await self._protocol.read_number(
                        self._write, CUP_COUNTER_BASE + recipe_type
                    )
                except ProtocolError as err:
                    _LOGGER.debug("Counter for %s unavailable: %s", recipe.name, err)
                    misses += 1
                    if misses >= MAX_COUNTER_MISSES:
                        _LOGGER.debug("Giving up on the remaining drink counters")
                        break
                else:
                    misses = 0
        return counters

    async def async_read_setting(self, setting: Setting) -> int:
        """Read one settings register."""
        await self.connect()
        async with self._lock:
            return await self._protocol.read_number(self._write, setting)

    async def async_write_setting(self, setting: Setting, value: int) -> None:
        """Write one settings register."""
        await self.connect()
        async with self._lock:
            await self._protocol.write_number(self._write, setting, value)

    # -- actions --------------------------------------------------------

    async def async_brew(self, recipe: Recipe, *, two_cups: bool = False) -> None:
        """Brew a built-in recipe using the active profile's variant.

        The machine has no "brew recipe N" command. Instead the recipe is
        copied into the scratch slot and the generic product process is
        started, which is exactly what the vendor app does.
        """
        recipe_id = int(recipe)
        if self._active_profile != DEFAULT_PROFILE:
            category = RECIPE_CATEGORIES.get(recipe)
            if category is not None:
                recipe_id = direct_key_id(self._active_profile, category)

        await self.connect()
        async with self._lock:
            self._require_ready()
            try:
                stored = await self._protocol.read_recipe(self._write, recipe_id)
            except ProtocolError as err:
                if recipe_id == int(recipe):
                    raise
                _LOGGER.debug(
                    "Profile %d has no stored %s (%s), using the default recipe",
                    self._active_profile,
                    recipe.name,
                    err,
                )
                stored = await self._protocol.read_recipe(self._write, int(recipe))

            recipe_type = RECIPE_TYPES.get(recipe, stored.recipe_type)
            await self._stage_and_start(
                recipe_type,
                stored.component1,
                stored.component2,
                name=_recipe_label(recipe),
                two_cups=two_cups,
            )

    async def async_brew_custom(
        self,
        *,
        process: ComponentProcess,
        portion_ml: int,
        intensity: int,
        temperature: int,
        hopper: int,
        milk_portion_ml: int = 0,
        name: str = "Home Assistant",
        two_cups: bool = False,
    ) -> None:
        """Brew a recipe assembled here rather than stored on the machine."""
        primary = RecipeComponent.from_ml(
            portion_ml,
            process=int(process),
            shots=1,
            hopper=hopper,
            intensity=intensity,
            temperature=temperature,
        )
        if milk_portion_ml > 0:
            secondary = RecipeComponent.from_ml(
                milk_portion_ml,
                process=int(ComponentProcess.MILK),
                shots=1,
                hopper=hopper,
                intensity=intensity,
                temperature=temperature,
            )
        else:
            secondary = RecipeComponent(process=int(ComponentProcess.NONE), shots=0)

        await self.connect()
        async with self._lock:
            self._require_ready()
            await self._stage_and_start(
                FREESTYLE_RECIPE_TYPE,
                primary,
                secondary,
                name=name,
                two_cups=two_cups,
            )

    async def _stage_and_start(
        self,
        recipe_type: int,
        component1: RecipeComponent,
        component2: RecipeComponent,
        *,
        name: str,
        two_cups: bool,
    ) -> None:
        """Write the scratch recipe and its label, then start brewing."""
        await self._protocol.write_recipe(
            self._write,
            TEMP_RECIPE_ID,
            recipe_type,
            component1,
            component2,
            recipe_key=recipe_key_for_type(recipe_type),
        )
        await asyncio.sleep(COMMAND_SETTLE_DELAY)
        await self._protocol.write_text(self._write, TEMP_RECIPE_NAME_ID, name)
        await asyncio.sleep(COMMAND_SETTLE_DELAY)
        await self._protocol.start_process(
            self._write, MachineProcess.PRODUCT, two_cups=two_cups
        )

    async def async_cancel(self) -> None:
        """Abort the running preparation."""
        await self.connect()
        async with self._lock:
            await self._protocol.cancel_process(self._write, MachineProcess.PRODUCT)

    async def async_confirm_prompt(self) -> None:
        """Acknowledge the prompt the machine is showing."""
        await self.connect()
        async with self._lock:
            await self._protocol.confirm_prompt(self._write)

    async def async_start_maintenance(self, process: MachineProcess) -> None:
        """Start a maintenance program."""
        await self.connect()
        async with self._lock:
            await self._protocol.start_process(self._write, process)

    def _require_ready(self) -> None:
        """Refuse to brew when the machine has told us it cannot."""
        status = self._status
        if status is None:
            return
        if not status.is_ready:
            state = status.process.name if status.process else "unknown"
            raise MachineNotReady(
                f"machine is not ready (state {state}, "
                f"action required: {status.manipulation.name})"
            )


def _recipe_label(recipe: Recipe) -> str:
    """Name shown on the machine display while brewing."""
    return recipe.name.replace("_", " ").title()
