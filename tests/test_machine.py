"""Tests for the command layer."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.melitta_barista_ts.const import (
    FREESTYLE_NAME_ID,
    PROCESS_PRODUCT,
    TEMP_RECIPE_ID,
    Blend,
    Intensity,
    RecipeId,
)
from custom_components.melitta_barista_ts.machine import (
    CommandTimeout,
    HandshakeError,
    MelittaMachine,
    NotAcknowledged,
    recipe_key_for_type,
)
from custom_components.melitta_barista_ts.protocol import (
    FRAME_END,
    FRAME_START,
    checksum,
    hu_verifier,
    rc4,
)


def _response(command: str, payload: bytes, *, encrypt: bool = True) -> bytes:
    """Build a frame the way the machine would answer."""
    body = command.encode() + payload
    body += bytes([checksum(body)])
    tail = body[len(command) :]
    if encrypt:
        tail = rc4(tail)
    return bytes([FRAME_START]) + command.encode() + tail + bytes([FRAME_END])


class FakeLink:
    """Collects frames written by the machine layer and replays canned answers."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.commands: list[str] = []
        self._pending = bytearray()
        self.machine: MelittaMachine | None = None
        self.responder = None

    async def write(self, chunk: bytes) -> None:
        self._pending.extend(chunk)
        if self._pending[-1:] != bytes([FRAME_END]):
            return

        frame = bytes(self._pending)
        self._pending.clear()
        self.frames.append(frame)

        command = frame[1:3].decode("ascii", errors="replace")
        if not command.startswith("H"):
            command = frame[1:2].decode("ascii", errors="replace")
        self.commands.append(command)

        if self.responder is not None:
            reply = self.responder(command, frame)
            if reply is not None:
                assert self.machine is not None
                self.machine.feed(reply)

    def decrypt_payload(self, index: int, command_length: int = 2) -> bytes:
        """Decrypt the payload of a frame we captured, minus the key prefix."""
        frame = self.frames[index]
        body = rc4(frame[1 + command_length : -1])
        return body[2:-1]  # strip key_prefix and checksum


@pytest.fixture
def link() -> FakeLink:
    return FakeLink()


@pytest.fixture
def machine(link: FakeLink) -> MelittaMachine:
    instance = MelittaMachine(link.write, frame_timeout=0.5)
    link.machine = instance
    return instance


def _handshake_responder(command: str, frame: bytes) -> bytes | None:
    """Answer HU with a well-formed response derived from the challenge."""
    if command != "HU":
        return None
    challenge = rc4(frame[3:-1])[:4]
    body = challenge + b"\x11\x22"
    return _response("HU", body + hu_verifier(body))


async def test_handshake_installs_session_key(machine, link):
    link.responder = _handshake_responder
    await machine.handshake()

    assert machine.ready
    # The handshake frame itself carries no key prefix: 4-byte challenge +
    # 2-byte verifier + checksum.
    assert len(link.frames[0]) == 1 + 2 + 7 + 1


async def test_handshake_rejects_wrong_echo(machine, link):
    def _bad_echo(command: str, frame: bytes) -> bytes | None:
        if command != "HU":
            return None
        body = b"\xde\xad\xbe\xef\x11\x22"
        return _response("HU", body + hu_verifier(body))

    link.responder = _bad_echo
    with pytest.raises(HandshakeError, match="echo mismatch"):
        await machine.handshake()
    assert not machine.ready


async def test_handshake_rejects_wrong_verifier(machine, link):
    def _bad_verifier(command: str, frame: bytes) -> bytes | None:
        if command != "HU":
            return None
        challenge = rc4(frame[3:-1])[:4]
        return _response("HU", challenge + b"\x11\x22\x00\x00")

    link.responder = _bad_verifier
    with pytest.raises(HandshakeError, match="verifier mismatch"):
        await machine.handshake()
    assert not machine.ready


async def test_handshake_times_out_when_silent(machine, link):
    link.responder = lambda command, frame: None
    with pytest.raises(CommandTimeout):
        await machine.handshake()


async def test_read_status_roundtrip(machine, link):
    def _responder(command: str, frame: bytes) -> bytes | None:
        if command == "HU":
            return _handshake_responder(command, frame)
        if command == "HX":
            return _response("HX", bytes([0, 4, 0, 2, 0, 0, 0, 55]))
        return None

    link.responder = _responder
    await machine.handshake()
    status = await machine.read_status()

    assert status.progress == 55
    assert status.is_busy


async def test_nack_raises(machine, link):
    def _responder(command: str, frame: bytes) -> bytes | None:
        if command == "HU":
            return _handshake_responder(command, frame)
        return _response("N", b"", encrypt=False)

    link.responder = _responder
    await machine.handshake()
    with pytest.raises(NotAcknowledged):
        await machine.write_numerical(13, 30)


async def test_status_listeners_receive_polled_frames(machine, link):
    seen = []
    machine.add_status_listener(seen.append)

    def _responder(command: str, frame: bytes) -> bytes | None:
        if command == "HU":
            return _handshake_responder(command, frame)
        if command == "HX":
            return _response("HX", bytes([0, 2, 0, 0, 0, 0, 0, 0]))
        return None

    link.responder = _responder
    await machine.handshake()
    await machine.read_status()

    assert len(seen) == 1
    assert not seen[0].is_busy


async def test_reset_cancels_pending_waiters(machine, link):
    link.responder = lambda command, frame: None

    task = asyncio.create_task(machine.read_status())
    await asyncio.sleep(0)
    machine.reset()

    with pytest.raises((asyncio.CancelledError, CommandTimeout)):
        await task


# --------------------------------------------------------------------------
# Brewing
# --------------------------------------------------------------------------

ESPRESSO_RECIPE = bytes.fromhex("0101010300020800")
ESPRESSO_MILK = bytes.fromhex("0000000000020000")


def _recipe_response(recipe_id: int, recipe_type: int) -> bytes:
    payload = bytearray(66)
    payload[0:2] = recipe_id.to_bytes(2, "big")
    payload[2] = recipe_type
    payload[3:11] = ESPRESSO_RECIPE
    payload[11:19] = ESPRESSO_MILK
    return _response("HC", bytes(payload))


def _brew_responder(command: str, frame: bytes) -> bytes | None:
    if command == "HU":
        return _handshake_responder(command, frame)
    if command == "HC":
        return _recipe_response(200, 0)
    return _response("A", b"", encrypt=False)


async def test_brew_replays_the_vendor_sequence(machine, link):
    link.responder = _brew_responder
    await machine.handshake()
    await machine.brew(RecipeId.ESPRESSO)

    assert link.commands == ["HU", "HC", "HJ", "HB", "HE"]

    write_recipe = link.decrypt_payload(2)
    assert int.from_bytes(write_recipe[0:2], "big") == TEMP_RECIPE_ID
    assert write_recipe[2] == 0  # recipe_type
    assert write_recipe[3] == 0  # recipe_key for the espresso family
    assert write_recipe[4:12] == ESPRESSO_RECIPE
    assert write_recipe[12:20] == ESPRESSO_MILK

    write_name = link.decrypt_payload(3)
    assert int.from_bytes(write_name[0:2], "big") == FREESTYLE_NAME_ID
    assert write_name[2:].rstrip(b"\x00") == b"Espresso"

    start = link.decrypt_payload(4)
    assert int.from_bytes(start[0:2], "big") == PROCESS_PRODUCT
    assert int.from_bytes(start[2:4], "big") == 2
    assert int.from_bytes(start[6:8], "big") == 0


async def test_brew_two_cups_sets_the_flag(machine, link):
    link.responder = _brew_responder
    await machine.handshake()
    await machine.brew(RecipeId.ESPRESSO, two_cups=True)

    start = link.decrypt_payload(4)
    assert int.from_bytes(start[6:8], "big") == 1


async def test_brew_overrides_only_touch_requested_fields(machine, link):
    link.responder = _brew_responder
    await machine.handshake()
    await machine.brew(
        RecipeId.ESPRESSO,
        intensity=int(Intensity.VERY_STRONG),
        portion_ml=60,
        blend=int(Blend.HOPPER_2),
    )

    component = link.decrypt_payload(2)[4:12]
    assert component[0] == ESPRESSO_RECIPE[0]  # process untouched
    assert component[1] == ESPRESSO_RECIPE[1]  # shots untouched
    assert component[2] == int(Blend.HOPPER_2)
    assert component[3] == int(Intensity.VERY_STRONG)
    assert component[4] == ESPRESSO_RECIPE[4]  # aroma untouched
    assert component[5] == ESPRESSO_RECIPE[5]  # temperature untouched
    assert component[6] == 12  # 60 ml / 5


async def test_brew_writes_the_matching_recipe_key(machine, link):
    """Latte Macchiato (type 18) has to be written with recipe key 3."""

    def _responder(command: str, frame: bytes) -> bytes | None:
        if command == "HU":
            return _handshake_responder(command, frame)
        if command == "HC":
            return _recipe_response(218, 18)
        return _response("A", b"", encrypt=False)

    link.responder = _responder
    await machine.handshake()
    await machine.brew(RecipeId.LATTE_MACCHIATO)

    write_recipe = link.decrypt_payload(2)
    assert write_recipe[2] == 18
    assert write_recipe[3] == 3


@pytest.mark.parametrize(
    ("recipe_type", "expected_key"),
    [
        (0, 0),
        (4, 0),
        (5, 1),
        (12, 1),
        (13, 2),
        (14, 2),
        (18, 3),
        (21, 5),
        (22, 4),
        (23, 6),
    ],
)
def test_recipe_key_mapping(recipe_type: int, expected_key: int):
    assert recipe_key_for_type(recipe_type) == expected_key


def test_recipe_key_falls_back_to_menu():
    assert recipe_key_for_type(99) == 7


async def test_frames_are_chunked_to_the_mtu(machine, link):
    """A 66-byte HJ payload does not fit into a single BLE write."""
    link.responder = _brew_responder
    await machine.handshake()
    await machine.brew(RecipeId.ESPRESSO)

    # The reassembled HJ frame is longer than one MTU-sized write.
    assert len(link.frames[2]) > 20
