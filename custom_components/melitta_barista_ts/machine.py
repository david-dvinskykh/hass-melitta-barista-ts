"""Command layer for the Melitta Barista T/TS Smart.

Sits between the raw :mod:`.protocol` codec and the BLE transport: owns the
session key from the HU handshake, serialises request/response pairs, and
exposes the machine's operations as coroutines. The transport is injected as
a plain ``write`` coroutine so this layer stays testable without Bluetooth.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from .const import (
    BLE_MTU,
    BREW_STEP_DELAY,
    CMD_ACK,
    CMD_CANCEL_PROCESS,
    CMD_HANDSHAKE,
    CMD_NACK,
    CMD_READ_ALPHA,
    CMD_READ_NUMERICAL,
    CMD_READ_RECIPE,
    CMD_READ_STATUS,
    CMD_READ_VERSION,
    CMD_START_PROCESS,
    CMD_WRITE_ALPHA,
    CMD_WRITE_NUMERICAL,
    CMD_WRITE_RECIPE,
    DEFAULT_FRAME_TIMEOUT,
    FREESTYLE_NAME_ID,
    PORTION_STEP_ML,
    PROCESS_PRODUCT,
    RECIPE_DISPLAY_NAMES,
    RECIPE_KEY_MENU,
    RECIPE_TYPE_TO_KEY,
    TEMP_RECIPE_ID,
    RecipeId,
)
from .protocol import (
    AlphanumericValue,
    FrameParser,
    MachineRecipe,
    MachineStatus,
    NumericalValue,
    ProtocolError,
    RecipeComponent,
    build_frame,
    chunk_for_ble,
    hu_verifier,
)

_LOGGER = logging.getLogger(__name__)

WriteFunc = Callable[[bytes], Awaitable[None]]

_ACK_KEY = "__ack__"


class MachineError(Exception):
    """A command could not be completed."""


class HandshakeError(MachineError):
    """The HU handshake did not produce a usable session key."""


class NotAcknowledged(MachineError):
    """The machine answered a write command with ``N``."""


class CommandTimeout(MachineError):
    """The machine did not answer within the frame timeout."""


def recipe_key_for_type(recipe_type: int) -> int:
    """Return the ``recipe_key`` byte that pairs with ``recipe_type``."""
    return RECIPE_TYPE_TO_KEY.get(recipe_type, RECIPE_KEY_MENU)


class MelittaMachine:
    """Request/response layer over a BLE write function."""

    def __init__(
        self,
        write: WriteFunc,
        *,
        frame_timeout: float = DEFAULT_FRAME_TIMEOUT,
        mtu: int = BLE_MTU,
    ) -> None:
        """Bind the transport and reset session state."""
        self._write = write
        self._frame_timeout = frame_timeout
        self._mtu = mtu
        self._parser = FrameParser(self._on_frame)
        self._request_lock = asyncio.Lock()
        self._waiters: dict[str, asyncio.Future[bytes]] = {}
        self._key_prefix: bytes | None = None
        self._challenge: bytes | None = None
        self._status_listeners: list[Callable[[MachineStatus], None]] = []

    # -- session ---------------------------------------------------------

    @property
    def ready(self) -> bool:
        """True once a session key has been negotiated."""
        return self._key_prefix is not None

    def reset(self) -> None:
        """Forget the session — call this whenever the link drops."""
        self._key_prefix = None
        self._challenge = None
        self._parser.reset()
        for future in self._waiters.values():
            if not future.done():
                future.cancel()
        self._waiters.clear()

    def add_status_listener(
        self, listener: Callable[[MachineStatus], None]
    ) -> Callable[[], None]:
        """Register a callback for unsolicited status frames."""
        self._status_listeners.append(listener)

        def _remove() -> None:
            if listener in self._status_listeners:
                self._status_listeners.remove(listener)

        return _remove

    # -- inbound ---------------------------------------------------------

    def feed(self, data: bytes) -> None:
        """Push raw notification bytes into the frame parser."""
        self._parser.feed(data)

    def _on_frame(self, command: str, payload: bytes) -> None:
        _LOGGER.debug("RX %s payload=%s", command, payload.hex())

        if command == CMD_HANDSHAKE:
            self._resolve(CMD_HANDSHAKE, payload)
            return

        if command in (CMD_ACK, CMD_NACK):
            self._resolve(_ACK_KEY, b"\x01" if command == CMD_ACK else b"")
            return

        if command == CMD_READ_STATUS:
            self._notify_status(payload)

        self._resolve(command, payload)

    def _notify_status(self, payload: bytes) -> None:
        try:
            status = MachineStatus.from_payload(payload)
        except ProtocolError:
            _LOGGER.debug("Ignoring malformed status payload %s", payload.hex())
            return
        for listener in list(self._status_listeners):
            listener(status)

    def _resolve(self, key: str, payload: bytes) -> None:
        future = self._waiters.pop(key, None)
        if future is not None and not future.done():
            future.set_result(payload)

    # -- outbound --------------------------------------------------------

    async def _send(
        self, command: str, payload: bytes | None, *, handshake: bool
    ) -> None:
        frame = build_frame(
            command,
            payload,
            None if handshake else self._key_prefix,
        )
        _LOGGER.debug("TX %s payload=%s", command, (payload or b"").hex())
        for chunk in chunk_for_ble(frame, self._mtu):
            await self._write(chunk)

    async def _request(
        self,
        command: str,
        payload: bytes | None,
        wait_for: str,
        *,
        handshake: bool = False,
        timeout: float | None = None,
    ) -> bytes:
        """Send a frame and wait for the frame keyed by ``wait_for``."""
        async with self._request_lock:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bytes] = loop.create_future()
            self._waiters[wait_for] = future
            try:
                await self._send(command, payload, handshake=handshake)
                return await asyncio.wait_for(
                    future, timeout if timeout is not None else self._frame_timeout
                )
            except TimeoutError as err:
                raise CommandTimeout(f"no response to {command}") from err
            finally:
                self._waiters.pop(wait_for, None)

    async def _request_ack(self, command: str, payload: bytes | None) -> None:
        result = await self._request(command, payload, _ACK_KEY)
        if not result:
            raise NotAcknowledged(f"machine rejected {command}")

    # -- handshake -------------------------------------------------------

    async def handshake(self) -> None:
        """Run the HU challenge/response and install the session key.

        The machine stays silent until it has seen a valid challenge, so this
        must be the first frame on every new connection.
        """
        self._key_prefix = None
        challenge = os.urandom(4)
        self._challenge = challenge

        payload = challenge + hu_verifier(challenge)
        response = await self._request(
            CMD_HANDSHAKE, payload, CMD_HANDSHAKE, handshake=True
        )

        if len(response) < 8:
            raise HandshakeError(
                f"handshake response too short ({len(response)} bytes)"
            )

        echoed, key_prefix, verifier = response[0:4], response[4:6], response[6:8]
        if echoed != challenge:
            raise HandshakeError(
                f"handshake echo mismatch: sent {challenge.hex()}, got {echoed.hex()}"
            )
        expected = hu_verifier(response[0:6])
        if verifier != expected:
            raise HandshakeError(
                f"handshake verifier mismatch: expected {expected.hex()}, "
                f"got {verifier.hex()}"
            )

        self._key_prefix = key_prefix
        _LOGGER.debug("Handshake complete, session key %s", key_prefix.hex())

    # -- reads -----------------------------------------------------------

    async def read_status(self) -> MachineStatus:
        """Poll the machine status (``HX``)."""
        return MachineStatus.from_payload(
            await self._request(CMD_READ_STATUS, None, CMD_READ_STATUS)
        )

    async def read_firmware(self) -> str:
        """Read the firmware version string (``HV``)."""
        payload = await self._request(CMD_READ_VERSION, None, CMD_READ_VERSION)
        return payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()

    async def read_numerical(
        self, value_id: int, *, timeout: float | None = None
    ) -> int:
        """Read a numerical register (``HR``).

        ``timeout`` shortens the wait for one read. A register the machine
        does not implement is answered with silence rather than a refusal,
        so scanning a range at the full frame timeout would spend most of
        its time waiting for answers that are never coming.
        """
        payload = await self._request(
            CMD_READ_NUMERICAL, _pack_id(value_id), CMD_READ_NUMERICAL, timeout=timeout
        )
        return NumericalValue.from_payload(payload).value

    async def read_alphanumeric(self, value_id: int) -> str:
        """Read a text register (``HA``)."""
        payload = await self._request(
            CMD_READ_ALPHA, _pack_id(value_id), CMD_READ_ALPHA
        )
        return AlphanumericValue.from_payload(payload).value

    async def read_recipe(self, recipe_id: int) -> MachineRecipe:
        """Read a stored recipe (``HC``)."""
        payload = await self._request(
            CMD_READ_RECIPE, _pack_id(recipe_id), CMD_READ_RECIPE
        )
        return MachineRecipe.from_payload(payload)

    # -- writes ----------------------------------------------------------

    async def write_numerical(self, value_id: int, value: int) -> None:
        """Write a numerical register (``HW``)."""
        payload = _pack_id(value_id) + value.to_bytes(4, "big", signed=True)
        await self._request_ack(CMD_WRITE_NUMERICAL, payload)

    async def write_alphanumeric(self, value_id: int, text: str) -> None:
        """Write a text register (``HB``)."""
        encoded = text.encode("utf-8")[:64]
        payload = _pack_id(value_id) + encoded.ljust(64, b"\x00")
        await self._request_ack(CMD_WRITE_ALPHA, payload)

    async def write_recipe(
        self,
        recipe_id: int,
        recipe_type: int,
        component1: RecipeComponent,
        component2: RecipeComponent,
        *,
        recipe_key: int | None = None,
    ) -> None:
        """Write a recipe into a machine slot (``HJ``)."""
        payload = bytearray(66)
        payload[0:2] = _pack_id(recipe_id)
        payload[2] = recipe_type & 0xFF
        offset = 3
        if recipe_key is not None:
            payload[offset] = recipe_key & 0xFF
            offset += 1
        payload[offset : offset + 8] = component1.to_bytes()
        payload[offset + 8 : offset + 16] = component2.to_bytes()
        await self._request_ack(CMD_WRITE_RECIPE, bytes(payload))

    async def start_process(self, process: int, *, two_cups: bool = False) -> None:
        """Start a machine process (``HE``)."""
        payload = bytearray(18)
        payload[0:2] = process.to_bytes(2, "big")
        payload[2:4] = (2).to_bytes(2, "big")
        if two_cups:
            payload[6:8] = (1).to_bytes(2, "big")
        await self._request_ack(CMD_START_PROCESS, bytes(payload))

    async def cancel_process(self, process: int) -> None:
        """Cancel a running process (``HZ``)."""
        await self._request_ack(CMD_CANCEL_PROCESS, _pack_id(process) + b"\x00\x00")

    # -- composite -------------------------------------------------------

    async def brew(
        self,
        recipe_id: RecipeId | int,
        *,
        name: str | None = None,
        two_cups: bool = False,
        intensity: int | None = None,
        temperature: int | None = None,
        portion_ml: int | None = None,
        blend: int | None = None,
    ) -> None:
        """Brew a drink from any recipe slot.

        Sending ``HE`` on its own is acknowledged but does nothing. The
        machine brews whatever sits in its scratch slot, so the vendor
        sequence has to be replayed: read the stored recipe, copy it (with
        any overrides applied) into the scratch slot, set the name shown on
        the display, then start the process.

        ``recipe_id`` is either a built-in recipe (200–223) or a profile's
        direct-key slot; both read back in the same shape. ``name`` overrides
        the label shown on the machine while it brews, which the built-in
        table cannot supply for profile slots.
        """
        recipe = await self.read_recipe(int(recipe_id))

        component1 = _apply_overrides(
            recipe.component1,
            intensity=intensity,
            temperature=temperature,
            portion_ml=portion_ml,
            blend=blend,
        )
        # The second component is milk or water for mixed drinks; only the
        # bean hopper is meaningful there, the rest belongs to component 1.
        component2 = _apply_overrides(recipe.component2, blend=blend)

        await self.write_recipe(
            TEMP_RECIPE_ID,
            recipe.recipe_type,
            component1,
            component2,
            recipe_key=recipe_key_for_type(recipe.recipe_type),
        )
        await asyncio.sleep(BREW_STEP_DELAY)

        await self.write_alphanumeric(
            FREESTYLE_NAME_ID, name if name else _display_name(recipe_id)
        )
        await asyncio.sleep(BREW_STEP_DELAY)

        await self.start_process(PROCESS_PRODUCT, two_cups=two_cups)


def _pack_id(value: int) -> bytes:
    """Pack a 16-bit big-endian register or recipe ID."""
    return int(value).to_bytes(2, "big", signed=True)


def _display_name(recipe_id: RecipeId | int) -> str:
    try:
        return RECIPE_DISPLAY_NAMES[RecipeId(int(recipe_id))]
    except ValueError:
        return str(int(recipe_id))


def _apply_overrides(
    component: RecipeComponent,
    *,
    intensity: int | None = None,
    temperature: int | None = None,
    portion_ml: int | None = None,
    blend: int | None = None,
) -> RecipeComponent:
    """Return a copy of ``component`` with the supplied fields replaced."""
    updated = RecipeComponent(
        process=component.process,
        shots=component.shots,
        blend=component.blend,
        intensity=component.intensity,
        aroma=component.aroma,
        temperature=component.temperature,
        portion=component.portion,
        reserve=component.reserve,
    )
    if intensity is not None:
        updated.intensity = intensity
    if temperature is not None:
        updated.temperature = temperature
    if portion_ml is not None:
        updated.portion = max(1, min(255, round(portion_ml / PORTION_STEP_ML)))
    if blend:
        updated.blend = blend
    return updated
