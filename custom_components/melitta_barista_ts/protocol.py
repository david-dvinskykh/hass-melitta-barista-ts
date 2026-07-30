"""Wire protocol for Melitta Barista T/TS Smart coffee machines.

The machine speaks a framed, RC4-obfuscated protocol over two GATT
characteristics::

    S <opcode> <RC4( [session_key] payload checksum )> E

``opcode`` is one or two ASCII characters and stays in clear text; everything
between it and the trailing ``E`` is RC4 encrypted with a fixed key. A session
key negotiated by the ``HU`` handshake is prepended to the payload of every
subsequent frame.

This module contains no Home Assistant or bleak imports: it only turns bytes
into frames and back. The transport lives in :mod:`.client`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic

from .const import (
    BLE_MTU,
    CMD_ACK,
    CMD_CANCEL_PROCESS,
    CMD_CONFIRM_PROMPT,
    CMD_HANDSHAKE,
    CMD_NACK,
    CMD_READ_ALPHA,
    CMD_READ_FEATURES,
    CMD_READ_NUMERICAL,
    CMD_READ_RECIPE,
    CMD_READ_SERIAL,
    CMD_READ_STATUS,
    CMD_READ_VERSION,
    CMD_RESET_DEFAULT,
    CMD_START_PROCESS,
    CMD_WRITE_ALPHA,
    CMD_WRITE_NUMERICAL,
    CMD_WRITE_RECIPE,
    DEFAULT_FRAME_TIMEOUT,
    FRAME_ASSEMBLY_TIMEOUT,
    FRAME_END,
    FRAME_START,
    HU_TABLE,
    INBOUND_FRAMES,
    MAX_FRAME_SIZE,
    PORTION_STEP_ML,
    RC4_KEY,
    InfoMessage,
    MachineProcess,
    Manipulation,
    SubProcess,
)

_LOGGER = logging.getLogger(__name__)

WriteFunc = Callable[[bytes], Awaitable[None]]


class ProtocolError(Exception):
    """The machine did not answer, or answered with something unusable."""


class CommandRejected(ProtocolError):
    """The machine explicitly refused a command (NACK)."""


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def rc4(data: bytes, key: bytes = RC4_KEY) -> bytes:
    """RC4 keystream XOR — the same call encrypts and decrypts."""
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 0xFF
        state[i], state[j] = state[j], state[i]
    out = bytearray(len(data))
    i = j = 0
    for pos, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        out[pos] = byte ^ state[(state[i] + state[j]) & 0xFF]
    return bytes(out)


def checksum(data: bytes) -> int:
    """Frame checksum: one's complement of the byte sum."""
    return (~sum(data)) & 0xFF


def hu_verifier(data: bytes) -> bytes:
    """Two-byte handshake verifier over ``data``.

    Two independent folds through :data:`HU_TABLE` seeded one apart, each
    finished with its own additive constant. Both the challenge we send and
    the machine's reply carry one of these, which is what proves each side
    knows the table.
    """
    if not data:
        raise ValueError("hu_verifier needs at least one byte")

    first = HU_TABLE[data[0] % 256]
    for byte in data[1:]:
        first = HU_TABLE[(first ^ byte) % 256]

    second = HU_TABLE[(data[0] + 1) % 256]
    for byte in data[1:]:
        second = HU_TABLE[(second ^ byte) % 256]

    return bytes([(first + 93) & 0xFF, (second + 167) & 0xFF])


# ---------------------------------------------------------------------------
# Payload models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RecipeComponent:
    """One dispensing step of a recipe (8 bytes on the wire)."""

    process: int = 0
    shots: int = 1
    hopper: int = 1
    intensity: int = 2
    aroma: int = 0
    temperature: int = 1
    portion: int = 5  # in 5 ml steps
    reserved: int = 0

    @property
    def portion_ml(self) -> int:
        """Portion size in millilitres."""
        return self.portion * PORTION_STEP_ML

    @classmethod
    def from_ml(cls, portion_ml: int, **kwargs: int) -> RecipeComponent:
        """Build a component from a millilitre portion size."""
        return cls(portion=max(1, round(portion_ml / PORTION_STEP_ML)), **kwargs)

    def to_bytes(self) -> bytes:
        """Serialise the component."""
        return struct.pack(
            "8B",
            self.process,
            self.shots,
            self.hopper,
            self.intensity,
            self.aroma,
            self.temperature,
            self.portion,
            self.reserved,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> RecipeComponent:
        """Parse a component from its 8 wire bytes."""
        if len(data) < 8:
            raise ProtocolError(f"recipe component needs 8 bytes, got {len(data)}")
        return cls(*struct.unpack("8B", data[:8]))


@dataclass(slots=True)
class Recipe:
    """A recipe as stored in the machine (``HC`` response / ``HJ`` write)."""

    recipe_id: int = 0
    recipe_type: int = 0
    component1: RecipeComponent = field(default_factory=RecipeComponent)
    component2: RecipeComponent = field(default_factory=RecipeComponent)

    @classmethod
    def from_payload(cls, data: bytes) -> Recipe:
        """Parse an ``HC`` response: id(2) + type(1) + comp1(8) + comp2(8).

        Note the asymmetry with :meth:`MelittaProtocol.write_recipe`: the read
        response has no ``recipe_key`` byte, the write payload does.
        """
        if len(data) < 19:
            raise ProtocolError(f"recipe payload needs 19 bytes, got {len(data)}")
        recipe_id, recipe_type = struct.unpack(">hB", data[:3])
        return cls(
            recipe_id=recipe_id,
            recipe_type=recipe_type,
            component1=RecipeComponent.from_bytes(data[3:11]),
            component2=RecipeComponent.from_bytes(data[11:19]),
        )


@dataclass(slots=True)
class MachineStatus:
    """Decoded ``HX`` status frame."""

    process: MachineProcess | None = None
    sub_process: SubProcess | None = None
    info_messages: InfoMessage = field(default_factory=lambda: InfoMessage(0))
    manipulation: Manipulation = Manipulation.NONE
    progress: int = 0
    raw_process: int = 0
    raw_manipulation: int = 0

    @property
    def is_ready(self) -> bool:
        """Machine is idle and nothing blocks the next drink."""
        return (
            self.process is MachineProcess.READY
            and self.manipulation is Manipulation.NONE
        )

    @property
    def is_brewing(self) -> bool:
        """A drink is being prepared."""
        return self.process is MachineProcess.PRODUCT

    @classmethod
    def from_payload(cls, data: bytes) -> MachineStatus:
        """Parse a status frame payload."""
        if len(data) < 8:
            raise ProtocolError(f"status payload needs 8 bytes, got {len(data)}")
        raw_process, raw_sub, info, raw_manip, progress = struct.unpack(
            ">hhBBh", data[:8]
        )
        return cls(
            process=_as_enum(MachineProcess, raw_process),
            sub_process=_as_enum(SubProcess, raw_sub),
            info_messages=InfoMessage(info & 0x1F),
            manipulation=_as_enum(Manipulation, raw_manip) or Manipulation.NONE,
            progress=max(0, min(100, progress)),
            raw_process=raw_process,
            raw_manipulation=raw_manip,
        )


def _as_enum(enum_cls, value: int):
    """Coerce to an enum member, or ``None`` for values we do not know."""
    try:
        return enum_cls(value)
    except ValueError:
        _LOGGER.debug("Unknown %s value %s", enum_cls.__name__, value)
        return None


# ---------------------------------------------------------------------------
# Protocol engine
# ---------------------------------------------------------------------------


class MelittaProtocol:
    """Frame codec plus the request/response bookkeeping around it.

    The class is transport agnostic: every method that talks to the machine
    takes a ``write`` coroutine that puts a chunk on the wire, and incoming
    bytes are fed back in through :meth:`feed`.
    """

    def __init__(self, *, frame_timeout: float = DEFAULT_FRAME_TIMEOUT) -> None:
        """Initialise the codec with a per-request response timeout."""
        self._frame_timeout = frame_timeout
        self._session_key: bytes | None = None
        self._challenge: bytes | None = None
        self._buffer = bytearray()
        self._buffer_started = 0.0
        self._pending: dict[str, asyncio.Future[bytes]] = {}
        self._ack: asyncio.Future[bool] | None = None
        self._handshake_done = asyncio.Event()
        self._request_lock = asyncio.Lock()
        self._status_callback: Callable[[MachineStatus], None] | None = None

    # -- state ----------------------------------------------------------

    @property
    def handshake_complete(self) -> bool:
        """Whether a session key has been negotiated."""
        return self._session_key is not None

    def reset(self) -> None:
        """Forget the session — call this on every disconnect."""
        self._session_key = None
        self._challenge = None
        self._buffer.clear()
        self._handshake_done.clear()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._ack and not self._ack.done():
            self._ack.cancel()
        self._ack = None

    def set_status_callback(
        self, callback: Callable[[MachineStatus], None] | None
    ) -> None:
        """Register a sink for unsolicited status frames."""
        self._status_callback = callback

    # -- framing --------------------------------------------------------

    def build_frame(
        self, opcode: str, payload: bytes = b"", *, with_session_key: bool = True
    ) -> bytes:
        """Assemble and encrypt one outbound frame."""
        body = bytearray()
        if with_session_key and self._session_key:
            body.extend(self._session_key)
        body.extend(payload)
        opcode_bytes = opcode.encode("ascii")
        body.append(checksum(opcode_bytes + bytes(body)))
        return (
            bytes([FRAME_START]) + opcode_bytes + rc4(bytes(body)) + bytes([FRAME_END])
        )

    @staticmethod
    def chunks(frame: bytes) -> list[bytes]:
        """Split a frame into GATT-sized writes."""
        return [frame[i : i + BLE_MTU] for i in range(0, len(frame), BLE_MTU)]

    def feed(self, data: bytes) -> None:
        """Feed bytes received from the notify characteristic."""
        for byte in data:
            self._feed_byte(byte)

    def _feed_byte(self, byte: int) -> None:
        now = monotonic()

        if not self._buffer:
            if byte == FRAME_START:
                self._buffer.append(byte)
                self._buffer_started = now
            return

        if now - self._buffer_started > FRAME_ASSEMBLY_TIMEOUT:
            _LOGGER.debug("Dropping stale partial frame (%d bytes)", len(self._buffer))
            self._buffer.clear()
            if byte == FRAME_START:
                self._buffer.append(byte)
                self._buffer_started = now
            return

        if len(self._buffer) >= MAX_FRAME_SIZE:
            _LOGGER.debug("Receive buffer overflow, resynchronising")
            self._buffer.clear()
            return

        self._buffer.append(byte)

        # 0x45 also occurs inside ciphertext, so a frame is only complete when
        # the buffer length matches what the opcode declares.
        if byte != FRAME_END or len(self._buffer) < 4:
            return
        if (parsed := self._match_frame(bytes(self._buffer))) is None:
            return
        self._buffer.clear()
        opcode, payload = parsed
        self._dispatch(opcode, payload)

    def _match_frame(self, buffer: bytes) -> tuple[str, bytes] | None:
        """Decode ``buffer`` if its length matches a known inbound frame."""
        for opcode_len in (2, 1):
            if len(buffer) < 2 + opcode_len:
                continue
            opcode = buffer[1 : 1 + opcode_len].decode("ascii", "replace")
            spec = INBOUND_FRAMES.get(opcode)
            if spec is None:
                continue
            payload_len, encrypted = spec
            # S + opcode + payload + checksum + E
            if len(buffer) != 1 + opcode_len + payload_len + 1 + 1:
                continue
            body = buffer[1 + opcode_len : -1]
            if encrypted:
                body = rc4(body)
            payload, received = body[:-1], body[-1]
            expected = checksum(buffer[1 : 1 + opcode_len] + payload)
            if received != expected:
                _LOGGER.warning(
                    "Checksum mismatch on %s: got 0x%02X want 0x%02X (frame %s)",
                    opcode,
                    received,
                    expected,
                    buffer.hex(),
                )
                return None
            return opcode, payload
        return None

    def _dispatch(self, opcode: str, payload: bytes) -> None:
        _LOGGER.debug("RX %s %s", opcode, payload.hex())

        if opcode in (CMD_ACK, CMD_NACK):
            if self._ack and not self._ack.done():
                self._ack.set_result(opcode == CMD_ACK)
            return

        if opcode == CMD_HANDSHAKE:
            self._handle_handshake(payload)
        elif opcode == CMD_READ_STATUS and self._status_callback:
            try:
                self._status_callback(MachineStatus.from_payload(payload))
            except ProtocolError as err:
                _LOGGER.debug("Ignoring malformed status frame: %s", err)

        if (future := self._pending.pop(opcode, None)) and not future.done():
            future.set_result(payload)

    # -- handshake ------------------------------------------------------

    def _handle_handshake(self, payload: bytes) -> None:
        """Validate the ``HU`` reply and install the session key.

        Layout (8 bytes): echoed challenge(4) + session key(2) + verifier(2).
        A reply that fails any check leaves the session key unset, so that
        :meth:`handshake` reports failure instead of installing a key that
        would make every later frame undecryptable.
        """
        try:
            if len(payload) < 8:
                raise ProtocolError(f"HU reply too short ({len(payload)} bytes)")
            if self._challenge is None:
                raise ProtocolError("unsolicited HU reply")
            if payload[0:4] != self._challenge:
                raise ProtocolError(
                    f"HU challenge mismatch: sent {self._challenge.hex()}, "
                    f"got {payload[0:4].hex()}"
                )
            expected = hu_verifier(payload[0:6])
            if payload[6:8] != expected:
                raise ProtocolError(
                    f"HU verifier mismatch: want {expected.hex()}, "
                    f"got {payload[6:8].hex()}"
                )
        except ProtocolError as err:
            _LOGGER.warning("Rejecting handshake reply: %s", err)
        else:
            self._session_key = payload[4:6]
            _LOGGER.debug("Session key installed: %s", self._session_key.hex())
        finally:
            self._handshake_done.set()

    async def handshake(self, write: WriteFunc) -> bool:
        """Run the ``HU`` challenge/response. Returns success."""
        self._handshake_done.clear()
        self._session_key = None
        challenge = os.urandom(4)
        self._challenge = challenge

        frame = self.build_frame(
            CMD_HANDSHAKE, challenge + hu_verifier(challenge), with_session_key=False
        )
        for chunk in self.chunks(frame):
            await write(chunk)

        try:
            async with asyncio.timeout(self._frame_timeout):
                await self._handshake_done.wait()
        except TimeoutError:
            _LOGGER.warning("Handshake timed out after %.1fs", self._frame_timeout)
            return False
        return self._session_key is not None

    # -- request helpers ------------------------------------------------

    async def _write_frame(self, write: WriteFunc, opcode: str, payload: bytes) -> None:
        for chunk in self.chunks(self.build_frame(opcode, payload)):
            await write(chunk)

    async def command(
        self,
        write: WriteFunc,
        opcode: str,
        payload: bytes = b"",
        *,
        retries: int = 2,
    ) -> None:
        """Send a write command and wait for ``A``. Raises on rejection."""
        for attempt in range(1, retries + 1):
            async with self._request_lock:
                self._ack = asyncio.get_running_loop().create_future()
                try:
                    await self._write_frame(write, opcode, payload)
                    async with asyncio.timeout(self._frame_timeout):
                        acked = await self._ack
                except TimeoutError:
                    acked = None
                finally:
                    self._ack = None

            if acked is True:
                return
            if acked is False:
                raise CommandRejected(f"machine rejected {opcode}")
            if attempt < retries:
                _LOGGER.debug("No ACK for %s, retry %d", opcode, attempt)
                await asyncio.sleep(0.3)

        raise ProtocolError(f"no response to {opcode} after {retries} attempts")

    async def request(
        self,
        write: WriteFunc,
        opcode: str,
        payload: bytes = b"",
        *,
        response_timeout: float | None = None,
    ) -> bytes:
        """Send a read command and return the matching response payload.

        ``response_timeout`` overrides the default for this call only, for
        optional commands that some firmware revisions never answer.
        """
        async with self._request_lock:
            future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
            self._pending[opcode] = future
            try:
                await self._write_frame(write, opcode, payload)
                async with asyncio.timeout(response_timeout or self._frame_timeout):
                    return await future
            except TimeoutError as err:
                raise ProtocolError(f"no response to {opcode}") from err
            finally:
                self._pending.pop(opcode, None)

    # -- high-level commands -------------------------------------------

    async def read_status(self, write: WriteFunc) -> MachineStatus:
        """Read the machine status (``HX``)."""
        return MachineStatus.from_payload(await self.request(write, CMD_READ_STATUS))

    async def read_version(self, write: WriteFunc) -> str:
        """Read the firmware version string (``HV``)."""
        return _decode_text(await self.request(write, CMD_READ_VERSION))

    async def read_serial(self, write: WriteFunc) -> str:
        """Read the serial number (``HL``)."""
        return _decode_text(await self.request(write, CMD_READ_SERIAL))

    async def read_features(self, write: WriteFunc) -> int:
        """Read the capability bitfield (``HI``).

        Not answered by every firmware revision, hence the short timeout.
        """
        payload = await self.request(write, CMD_READ_FEATURES, response_timeout=3.0)
        return payload[0] if payload else 0

    async def read_number(self, write: WriteFunc, register: int) -> int:
        """Read a numerical register (``HR``)."""
        payload = await self.request(
            write, CMD_READ_NUMERICAL, struct.pack(">h", register)
        )
        if len(payload) < 6:
            raise ProtocolError(f"short HR response for register {register}")
        return struct.unpack(">i", payload[2:6])[0]

    async def write_number(self, write: WriteFunc, register: int, value: int) -> None:
        """Write a numerical register (``HW``)."""
        await self.command(
            write, CMD_WRITE_NUMERICAL, struct.pack(">hi", register, value)
        )

    async def read_text(self, write: WriteFunc, register: int) -> str:
        """Read an alphanumeric register (``HA``)."""
        payload = await self.request(write, CMD_READ_ALPHA, struct.pack(">h", register))
        if len(payload) < 2:
            raise ProtocolError(f"short HA response for register {register}")
        return _decode_text(payload[2:])

    async def write_text(self, write: WriteFunc, register: int, value: str) -> None:
        """Write an alphanumeric register (``HB``); payload is fixed at 64 bytes."""
        encoded = value.encode("utf-8")[:64].ljust(64, b"\x00")
        await self.command(
            write, CMD_WRITE_ALPHA, struct.pack(">h", register) + encoded
        )

    async def read_recipe(self, write: WriteFunc, recipe_id: int) -> Recipe:
        """Read a stored recipe (``HC``)."""
        payload = await self.request(
            write, CMD_READ_RECIPE, struct.pack(">h", recipe_id)
        )
        return Recipe.from_payload(payload)

    async def write_recipe(
        self,
        write: WriteFunc,
        recipe_id: int,
        recipe_type: int,
        component1: RecipeComponent,
        component2: RecipeComponent,
        *,
        recipe_key: int | None = None,
    ) -> None:
        """Write a recipe (``HJ``, fixed 66-byte payload).

        Layout: id(2) + type(1) [+ key(1)] + component1(8) + component2(8),
        zero padded. The ``recipe_key`` byte is only present when given — the
        machine expects it for the temp slot but not for stored slots.
        """
        payload = bytearray(66)
        struct.pack_into(">hB", payload, 0, recipe_id, recipe_type & 0xFF)
        offset = 3
        if recipe_key is not None:
            payload[offset] = recipe_key & 0xFF
            offset += 1
        payload[offset : offset + 8] = component1.to_bytes()
        payload[offset + 8 : offset + 16] = component2.to_bytes()
        await self.command(write, CMD_WRITE_RECIPE, bytes(payload))

    async def start_process(
        self, write: WriteFunc, process: int, *, two_cups: bool = False
    ) -> None:
        """Start a machine process (``HE``).

        Layout: process(2) + constant 2(2) + zeros(4) + two-cups flag(2) +
        zeros(8). Brewing uses ``MachineProcess.PRODUCT``; maintenance
        programs pass their own process value.
        """
        body = bytearray(16)
        struct.pack_into(">h", body, 0, 2)
        if two_cups:
            struct.pack_into(">h", body, 6, 1)
        await self.command(
            write, CMD_START_PROCESS, struct.pack(">h", process) + bytes(body)
        )

    async def cancel_process(
        self, write: WriteFunc, process: int = MachineProcess.PRODUCT
    ) -> None:
        """Abort a running process (``HZ``)."""
        await self.command(
            write, CMD_CANCEL_PROCESS, struct.pack(">h", process) + b"\x00\x00"
        )

    async def confirm_prompt(self, write: WriteFunc) -> None:
        """Acknowledge an on-machine prompt (``HY``)."""
        await self.command(write, CMD_CONFIRM_PROMPT, b"\x00\x00\x00\x00")

    async def reset_to_default(self, write: WriteFunc, register: int) -> None:
        """Restore a register to its factory value (``HD``)."""
        await self.command(write, CMD_RESET_DEFAULT, struct.pack(">h", register))


def _decode_text(data: bytes) -> str:
    """Decode a zero-padded machine string."""
    return data.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
