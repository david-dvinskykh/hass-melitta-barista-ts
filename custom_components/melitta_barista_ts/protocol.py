"""Wire protocol for the Melitta Barista T/TS Smart BLE interface.

This module is deliberately free of Home Assistant and Bluetooth imports so
that the framing, crypto and payload parsing can be exercised in isolation.

Frame layout, before encryption::

    S | command (1-2 ASCII) | [key_prefix (2)] | [payload] | checksum | E

Everything between the command bytes and the trailing ``E`` is then
RC4-encrypted with a fresh keystream per frame. ``A``/``N`` acknowledgement
frames are sent and received in the clear.
"""

from __future__ import annotations

import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from .const import (
    FRAME_END,
    FRAME_START,
    HU_TABLE,
    INBOUND_COMMANDS,
    PORTION_STEP_ML,
    RC4_KEY,
    RX_BUFFER_LIMIT,
    RX_FRAME_ASSEMBLY_TIMEOUT,
    InfoMessage,
    MachineProcess,
    Manipulation,
    SubProcess,
)

_LOGGER = logging.getLogger(__name__)


class ProtocolError(Exception):
    """Raised when a frame cannot be built or decoded."""


def rc4(data: bytes, key: bytes = RC4_KEY) -> bytes:
    """Apply the RC4 keystream to ``data``.

    RC4 is symmetric, so this both encrypts and decrypts. The machine resets
    the key schedule for every frame, so callers must not reuse state.
    """
    s = list(range(256))
    j = 0
    key_len = len(key)
    for i in range(256):
        j = (j + s[i] + key[i % key_len]) & 0xFF
        s[i], s[j] = s[j], s[i]

    out = bytearray(len(data))
    i = j = 0
    for index, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[index] = byte ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def checksum(payload: bytes) -> int:
    """Return the frame checksum over ``payload`` (command bytes onwards)."""
    return (~sum(payload)) & 0xFF


def hu_verifier(data: bytes) -> bytes:
    """Compute the 2-byte HU handshake verifier over ``data``.

    Two independent folds through :data:`HU_TABLE`, seeded one index apart
    and finished with different additive constants.
    """
    if not data:
        raise ProtocolError("HU verifier needs at least one byte")

    first = HU_TABLE[data[0] & 0xFF]
    for byte in data[1:]:
        first = HU_TABLE[(first ^ byte) & 0xFF]

    second = HU_TABLE[(data[0] + 1) & 0xFF]
    for byte in data[1:]:
        second = HU_TABLE[(second ^ byte) & 0xFF]

    return bytes([(first + 93) & 0xFF, (second + 167) & 0xFF])


# ---------------------------------------------------------------------------
# Payload models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RecipeComponent:
    """One dispensing step of a recipe (8 bytes on the wire)."""

    process: int = 0
    shots: int = 1
    blend: int = 1
    intensity: int = 2
    aroma: int = 0
    temperature: int = 1
    portion: int = 8
    reserve: int = 0

    @property
    def portion_ml(self) -> int:
        """Portion size in millilitres."""
        return self.portion * PORTION_STEP_ML

    def to_bytes(self) -> bytes:
        """Serialise to the 8-byte wire representation."""
        return struct.pack(
            "8B",
            self.process & 0xFF,
            self.shots & 0xFF,
            self.blend & 0xFF,
            self.intensity & 0xFF,
            self.aroma & 0xFF,
            self.temperature & 0xFF,
            self.portion & 0xFF,
            self.reserve & 0xFF,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> RecipeComponent:
        """Parse the 8-byte wire representation."""
        if len(data) < 8:
            raise ProtocolError(f"recipe component needs 8 bytes, got {len(data)}")
        return cls(*struct.unpack("8B", data[:8]))


@dataclass(slots=True)
class MachineRecipe:
    """A recipe as returned by ``HC``.

    The read response has no ``recipe_key`` byte; that field only exists in
    the ``HJ`` write payload and is derived from ``recipe_type``.
    """

    recipe_id: int
    recipe_type: int
    component1: RecipeComponent
    component2: RecipeComponent

    @classmethod
    def from_payload(cls, data: bytes) -> MachineRecipe:
        """Parse an ``HC`` response payload."""
        if len(data) < 19:
            raise ProtocolError(f"recipe payload needs 19 bytes, got {len(data)}")
        recipe_id = struct.unpack_from(">h", data, 0)[0]
        return cls(
            recipe_id=recipe_id,
            recipe_type=data[2],
            component1=RecipeComponent.from_bytes(data[3:11]),
            component2=RecipeComponent.from_bytes(data[11:19]),
        )


@dataclass(frozen=True, slots=True)
class MachineStatus:
    """Decoded ``HX`` status frame."""

    process: int
    sub_process: int
    info_messages: int
    manipulation: int
    progress: int

    @classmethod
    def from_payload(cls, data: bytes) -> MachineStatus:
        """Parse an ``HX`` response payload."""
        if len(data) < 8:
            raise ProtocolError(f"status payload needs 8 bytes, got {len(data)}")
        process, sub_process = struct.unpack_from(">hh", data, 0)
        progress = struct.unpack_from(">h", data, 6)[0]
        return cls(
            process=process,
            sub_process=sub_process,
            info_messages=data[4],
            manipulation=data[5],
            progress=progress,
        )

    @property
    def process_enum(self) -> MachineProcess | None:
        """The process as an enum member, or ``None`` when unrecognised."""
        try:
            return MachineProcess(self.process)
        except ValueError:
            return None

    @property
    def sub_process_enum(self) -> SubProcess | None:
        """The sub-process as an enum member, or ``None`` when unrecognised."""
        try:
            return SubProcess(self.sub_process)
        except ValueError:
            return None

    @property
    def manipulation_enum(self) -> Manipulation | None:
        """The pending manipulation, or ``None`` when unrecognised."""
        try:
            return Manipulation(self.manipulation)
        except ValueError:
            return None

    @property
    def info_flags(self) -> InfoMessage:
        """Info bits as a flag set, ignoring bits we do not know about."""
        known = 0
        for flag in InfoMessage:
            known |= flag.value
        return InfoMessage(self.info_messages & known)

    @property
    def is_busy(self) -> bool:
        """True while the machine is running any process other than idle."""
        return self.process != MachineProcess.READY


@dataclass(frozen=True, slots=True)
class NumericalValue:
    """Decoded ``HR`` response."""

    value_id: int
    value: int

    @classmethod
    def from_payload(cls, data: bytes) -> NumericalValue:
        """Parse an ``HR`` response payload."""
        if len(data) < 6:
            raise ProtocolError(f"numerical payload needs 6 bytes, got {len(data)}")
        value_id, value = struct.unpack_from(">hi", data, 0)
        return cls(value_id=value_id, value=value)


@dataclass(frozen=True, slots=True)
class AlphanumericValue:
    """Decoded ``HA`` response."""

    value_id: int
    value: str

    @classmethod
    def from_payload(cls, data: bytes) -> AlphanumericValue:
        """Parse an ``HA`` response payload."""
        if len(data) < 2:
            raise ProtocolError(f"alphanumeric payload needs 2 bytes, got {len(data)}")
        value_id = struct.unpack_from(">h", data, 0)[0]
        text = data[2:].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return cls(value_id=value_id, value=text)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def build_frame(
    command: str,
    payload: bytes | None = None,
    key_prefix: bytes | None = None,
    *,
    encrypt: bool = True,
) -> bytes:
    """Assemble a ready-to-transmit frame.

    ``key_prefix`` is the 2-byte session value handed out by the machine
    during the HU handshake; it is omitted for the handshake itself and for
    plaintext acknowledgements.
    """
    command_bytes = command.encode("ascii")
    body = bytearray(command_bytes)
    if key_prefix:
        body.extend(key_prefix)
    if payload:
        body.extend(payload)
    body.append(checksum(body))

    encrypted_part = bytes(body[len(command_bytes) :])
    if encrypt and encrypted_part:
        encrypted_part = rc4(encrypted_part)

    return bytes([FRAME_START]) + command_bytes + encrypted_part + bytes([FRAME_END])


def chunk_for_ble(frame: bytes, mtu: int) -> list[bytes]:
    """Split ``frame`` into ``mtu``-sized BLE writes."""
    return [frame[offset : offset + mtu] for offset in range(0, len(frame), mtu)]


def _match_command(buffer: bytes) -> tuple[str, int] | None:
    """Return ``(command, command_length)`` if ``buffer`` is a complete frame."""
    two_char = buffer[1:3].decode("ascii", errors="replace")
    if two_char in INBOUND_COMMANDS:
        payload_size, _ = INBOUND_COMMANDS[two_char]
        # S + command(2) + payload + checksum + E
        if len(buffer) == 2 + 2 + payload_size + 1:
            return two_char, 2

    one_char = buffer[1:2].decode("ascii", errors="replace")
    if one_char in INBOUND_COMMANDS:
        payload_size, _ = INBOUND_COMMANDS[one_char]
        if len(buffer) == 2 + 1 + payload_size + 1:
            return one_char, 1

    return None


class FrameParser:
    """Reassembles frames from the notification byte stream.

    RC4 ciphertext can contain both ``S`` and ``E``, so a trailing ``E`` only
    ends a frame when the accumulated length matches a known command's frame
    size. Anything else is treated as payload and collection continues.
    """

    def __init__(
        self,
        on_frame: Callable[[str, bytes], None],
        *,
        assembly_timeout: float = RX_FRAME_ASSEMBLY_TIMEOUT,
    ) -> None:
        """Store the frame sink and reset the receive buffer."""
        self._on_frame = on_frame
        self._assembly_timeout = assembly_timeout
        self._buffer = bytearray()
        self._started_at = 0.0

    def reset(self) -> None:
        """Drop any partially received frame."""
        self._buffer.clear()

    def feed(self, data: bytes) -> None:
        """Consume a chunk of notification data."""
        for byte in data:
            self._feed_byte(byte)

    def _start_frame(self, byte: int) -> None:
        self._buffer.clear()
        if byte == FRAME_START:
            self._buffer.append(byte)
            self._started_at = time.monotonic()

    def _feed_byte(self, byte: int) -> None:
        if not self._buffer:
            self._start_frame(byte)
            return

        if time.monotonic() - self._started_at > self._assembly_timeout:
            _LOGGER.debug(
                "Discarding stale partial frame (%d bytes)", len(self._buffer)
            )
            self._start_frame(byte)
            return

        if len(self._buffer) >= RX_BUFFER_LIMIT:
            _LOGGER.debug("Receive buffer overflow, resyncing")
            self._start_frame(byte)
            return

        self._buffer.append(byte)

        if byte != FRAME_END or len(self._buffer) < 4:
            return

        match = _match_command(bytes(self._buffer))
        if match is None:
            # An ``E`` inside ciphertext — keep collecting.
            return

        frame = bytes(self._buffer)
        self._buffer.clear()
        self._emit(frame, *match)

    def _emit(self, frame: bytes, command: str, command_length: int) -> None:
        _, encrypted = INBOUND_COMMANDS[command]
        body = frame[1 + command_length : -1]

        if not body:
            self._on_frame(command, b"")
            return

        if encrypted:
            body = rc4(body)

        payload, received = body[:-1], body[-1]
        expected = checksum(frame[1 : 1 + command_length] + payload)
        if received != expected:
            _LOGGER.warning(
                "Checksum mismatch on %s: got 0x%02X expected 0x%02X",
                command,
                received,
                expected,
            )
            return

        self._on_frame(command, payload)
