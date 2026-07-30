"""Tests for the Melitta Barista wire protocol.

These cover the pure codec — framing, encryption, the handshake verifier and
payload parsing — and run without Home Assistant installed.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from custom_components.melitta_barista_ts.const import (
    AES_IV,
    AES_KEY,
    ENCRYPTED_RC4_KEY,
    FRAME_END,
    FRAME_START,
    RC4_KEY,
    InfoMessage,
    MachineProcess,
    MachineType,
    Manipulation,
    Recipe,
    SubProcess,
    available_recipes,
    is_supported_name,
    machine_type_from_name,
    recipe_key_for_type,
)
from custom_components.melitta_barista_ts.protocol import (
    CommandRejected,
    MachineStatus,
    MelittaProtocol,
    ProtocolError,
    RecipeComponent,
    checksum,
    hu_verifier,
    rc4,
)

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def test_rc4_is_symmetric():
    plain = b"the quick brown fox"
    assert rc4(rc4(plain)) == plain


@pytest.mark.parametrize(
    ("key", "plain", "expected"),
    [
        (b"Key", b"Plaintext", "bbf316e8d940af0ad3"),
        (b"Secret", b"Attack at dawn", "45a01f645fc35b383552544b9bf5"),
    ],
)
def test_rc4_matches_published_vectors(key, plain, expected):
    """Pin the cipher itself against well-known RC4 test vectors."""
    assert rc4(plain, key).hex() == expected


def test_rc4_keystream_for_the_machine_key():
    """An all-zero plaintext exposes the keystream, pinning the embedded key."""
    assert rc4(bytes(8)).hex() == "cd3f5e9d775cb3d4"


def test_rc4_key_matches_aes_blob():
    """The embedded RC4 key must equal what the vendor AES blob decrypts to."""
    cryptography = pytest.importorskip("cryptography")
    assert cryptography  # silence unused-import linters
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).decryptor()
    decrypted = decryptor.update(ENCRYPTED_RC4_KEY) + decryptor.finalize()
    pad = decrypted[-1]
    assert 1 <= pad <= 16
    assert decrypted[-pad:] == bytes([pad]) * pad
    assert decrypted[:-pad] == RC4_KEY


def test_checksum_is_ones_complement():
    assert checksum(b"\x01\x02") == 0xFC
    assert checksum(b"") == 0xFF


def test_hu_verifier_is_two_bytes_and_stable():
    first = hu_verifier(b"\x01\x02\x03\x04")
    assert len(first) == 2
    assert first == hu_verifier(b"\x01\x02\x03\x04")
    assert first != hu_verifier(b"\x01\x02\x03\x05")


def test_hu_verifier_rejects_empty_input():
    with pytest.raises(ValueError):
        hu_verifier(b"")


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def test_build_frame_layout():
    protocol = MelittaProtocol()
    frame = protocol.build_frame("HX")

    assert frame[0] == FRAME_START
    assert frame[-1] == FRAME_END
    assert frame[1:3] == b"HX"
    # Body is one checksum byte, encrypted.
    body = rc4(frame[3:-1])
    assert body == bytes([checksum(b"HX")])


def test_build_frame_includes_session_key_after_handshake():
    protocol = MelittaProtocol()
    protocol._session_key = b"\xab\xcd"

    body = rc4(protocol.build_frame("HW", b"\x00\x0b")[3:-1])
    assert body[:2] == b"\xab\xcd"
    assert body[2:4] == b"\x00\x0b"
    assert body[4] == checksum(b"HW" + b"\xab\xcd\x00\x0b")

    without = rc4(protocol.build_frame("HU", b"", with_session_key=False)[3:-1])
    assert without == bytes([checksum(b"HU")])


def test_chunks_respect_the_gatt_mtu():
    protocol = MelittaProtocol()
    chunks = protocol.chunks(bytes(45))
    assert [len(chunk) for chunk in chunks] == [20, 20, 5]


def build_inbound(opcode: str, payload: bytes, *, encrypted: bool = True) -> bytes:
    """Build a frame the way the machine would send it."""
    body = payload + bytes([checksum(opcode.encode() + payload)])
    if encrypted:
        body = rc4(body)
    return bytes([FRAME_START]) + opcode.encode() + body + bytes([FRAME_END])


def test_round_trip_status_frame():
    protocol = MelittaProtocol()
    received: list[MachineStatus] = []
    protocol.set_status_callback(received.append)

    payload = struct.pack(">hhBBh", MachineProcess.PRODUCT, SubProcess.COFFEE, 0, 0, 42)
    protocol.feed(build_inbound("HX", payload))

    assert len(received) == 1
    assert received[0].process is MachineProcess.PRODUCT
    assert received[0].sub_process is SubProcess.COFFEE
    assert received[0].progress == 42
    assert received[0].is_brewing


def test_frame_split_across_notifications():
    protocol = MelittaProtocol()
    received: list[MachineStatus] = []
    protocol.set_status_callback(received.append)

    frame = build_inbound("HX", struct.pack(">hhBBh", 2, 0, 0, 0, 0))
    for chunk in (frame[:3], frame[3:7], frame[7:]):
        protocol.feed(chunk)

    assert len(received) == 1
    assert received[0].is_ready


def test_leading_garbage_is_skipped():
    protocol = MelittaProtocol()
    received: list[MachineStatus] = []
    protocol.set_status_callback(received.append)

    frame = build_inbound("HX", struct.pack(">hhBBh", 2, 0, 0, 0, 0))
    protocol.feed(b"\x00\xff\x12" + frame)

    assert len(received) == 1


def test_bad_checksum_drops_the_frame():
    protocol = MelittaProtocol()
    received: list[MachineStatus] = []
    protocol.set_status_callback(received.append)

    payload = struct.pack(">hhBBh", 2, 0, 0, 0, 0)
    body = bytearray(payload + b"\x00")  # deliberately wrong checksum
    frame = bytes([FRAME_START]) + b"HX" + rc4(bytes(body)) + bytes([FRAME_END])
    protocol.feed(frame)

    assert received == []


def test_end_byte_inside_ciphertext_does_not_split_the_frame():
    """A 0x45 in the encrypted body must not be mistaken for the terminator."""
    protocol = MelittaProtocol()
    received: list[MachineStatus] = []
    protocol.set_status_callback(received.append)

    # Search for a status payload whose ciphertext contains 0x45.
    for progress in range(101):
        payload = struct.pack(">hhBBh", 2, 0, 0, 0, progress)
        frame = build_inbound("HX", payload)
        if FRAME_END in frame[3:-1]:
            break
    else:  # pragma: no cover - the loop always finds one in practice
        pytest.skip("no payload with an embedded terminator byte found")

    protocol.feed(frame)
    assert len(received) == 1
    assert received[0].progress == progress


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def test_status_from_payload_decodes_every_field():
    payload = struct.pack(
        ">hhBBh",
        MachineProcess.READY,
        SubProcess.PREPARE,
        InfoMessage.FILL_BEANS_1 | InfoMessage.EASY_CLEAN,
        Manipulation.FILL_WATER,
        7,
    )
    status = MachineStatus.from_payload(payload)

    assert status.process is MachineProcess.READY
    assert status.sub_process is SubProcess.PREPARE
    assert InfoMessage.FILL_BEANS_1 in status.info_messages
    assert InfoMessage.EASY_CLEAN in status.info_messages
    assert status.manipulation is Manipulation.FILL_WATER
    assert status.progress == 7
    # Ready, but blocked by a required action.
    assert not status.is_ready


def test_status_tolerates_unknown_codes():
    status = MachineStatus.from_payload(struct.pack(">hhBBh", 1234, 99, 0, 250, 0))
    assert status.process is None
    assert status.sub_process is None
    assert status.manipulation is Manipulation.NONE
    assert status.raw_process == 1234
    assert status.raw_manipulation == 250


def test_status_rejects_short_payload():
    with pytest.raises(ProtocolError):
        MachineStatus.from_payload(b"\x00\x02")


def test_recipe_component_round_trip():
    component = RecipeComponent(
        process=1, shots=2, hopper=2, intensity=3, aroma=1, temperature=2, portion=24
    )
    assert RecipeComponent.from_bytes(component.to_bytes()) == component
    assert component.portion_ml == 120


def test_recipe_component_from_ml_rounds_to_the_nearest_step():
    assert RecipeComponent.from_ml(118).portion == 24  # 120 ml
    assert RecipeComponent.from_ml(30).portion == 6
    assert RecipeComponent.from_ml(1).portion == 1  # never zero


# ---------------------------------------------------------------------------
# Request/response plumbing
# ---------------------------------------------------------------------------


class FakeMachine:
    """Collects writes and answers with canned frames."""

    def __init__(self, protocol: MelittaProtocol) -> None:
        self.protocol = protocol
        self.frames: list[bytes] = []
        self._buffer = bytearray()
        self.reply: bytes | None = None

    async def write(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        if self._buffer[-1] == FRAME_END:
            self.frames.append(bytes(self._buffer))
            self._buffer.clear()
            if self.reply is not None:
                self.protocol.feed(self.reply)


@pytest.mark.asyncio
async def test_handshake_installs_the_session_key():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)

    async def answer(chunk: bytes) -> None:
        await machine.write(chunk)
        if not machine.frames:
            return
        # Echo the challenge back with a session key and a fresh verifier.
        challenge = rc4(machine.frames[0][3:-1])[:4]
        head = challenge + b"\x11\x22"
        protocol.feed(build_inbound("HU", head + hu_verifier(head)))

    assert await protocol.handshake(answer) is True
    assert protocol.handshake_complete
    assert protocol._session_key == b"\x11\x22"


@pytest.mark.asyncio
async def test_handshake_rejects_a_wrong_challenge_echo():
    protocol = MelittaProtocol(frame_timeout=1)

    async def answer(_chunk: bytes) -> None:
        head = b"\xde\xad\xbe\xef" + b"\x11\x22"
        protocol.feed(build_inbound("HU", head + hu_verifier(head)))

    assert await protocol.handshake(answer) is False
    assert not protocol.handshake_complete


@pytest.mark.asyncio
async def test_handshake_rejects_a_wrong_verifier():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)

    async def answer(chunk: bytes) -> None:
        await machine.write(chunk)
        challenge = rc4(machine.frames[0][3:-1])[:4]
        protocol.feed(build_inbound("HU", challenge + b"\x11\x22" + b"\x00\x00"))

    assert await protocol.handshake(answer) is False
    assert not protocol.handshake_complete


@pytest.mark.asyncio
async def test_handshake_times_out_when_the_machine_stays_silent():
    protocol = MelittaProtocol(frame_timeout=0.05)

    async def silent(_chunk: bytes) -> None:
        return None

    assert await protocol.handshake(silent) is False


@pytest.mark.asyncio
async def test_command_resolves_on_ack():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)
    machine.reply = build_inbound("A", b"", encrypted=False)

    await protocol.write_number(machine.write, 11, 3)

    assert len(machine.frames) == 1
    body = rc4(machine.frames[0][3:-1])
    assert struct.unpack(">hi", body[:6]) == (11, 3)


@pytest.mark.asyncio
async def test_command_raises_on_nack():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)
    machine.reply = build_inbound("N", b"", encrypted=False)

    with pytest.raises(CommandRejected):
        await protocol.write_number(machine.write, 11, 3)


@pytest.mark.asyncio
async def test_command_retries_then_gives_up():
    protocol = MelittaProtocol(frame_timeout=0.05)
    machine = FakeMachine(protocol)

    with pytest.raises(ProtocolError):
        await protocol.command(machine.write, "HW", b"\x00\x0b", retries=2)

    assert len(machine.frames) == 2


@pytest.mark.asyncio
async def test_read_number_returns_the_register_value():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)
    machine.reply = build_inbound("HR", struct.pack(">hi", 150, 1234))

    assert await protocol.read_number(machine.write, 150) == 1234


@pytest.mark.asyncio
async def test_read_version_strips_padding():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)
    machine.reply = build_inbound("HV", b"1.10.5".ljust(11, b"\x00"))

    assert await protocol.read_version(machine.write) == "1.10.5"


@pytest.mark.asyncio
async def test_read_recipe_parses_both_components():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)

    payload = bytearray(66)
    struct.pack_into(">hB", payload, 0, 213, 13)
    payload[3:11] = RecipeComponent(process=1, portion=8).to_bytes()
    payload[11:19] = RecipeComponent(process=2, portion=20).to_bytes()
    machine.reply = build_inbound("HC", bytes(payload))

    recipe = await protocol.read_recipe(machine.write, 213)
    assert recipe.recipe_id == 213
    assert recipe.recipe_type == 13
    assert recipe.component1.portion_ml == 40
    assert recipe.component2.portion_ml == 100


@pytest.mark.asyncio
async def test_write_recipe_layout_includes_the_family_byte():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)
    machine.reply = build_inbound("A", b"", encrypted=False)

    component1 = RecipeComponent(process=1, portion=8)
    component2 = RecipeComponent(process=2, portion=20)
    await protocol.write_recipe(
        machine.write, 400, 13, component1, component2, recipe_key=2
    )

    body = rc4(machine.frames[0][3:-1])
    assert struct.unpack(">h", body[0:2])[0] == 400
    assert body[2] == 13
    assert body[3] == 2  # recipe_key
    assert body[4:12] == component1.to_bytes()
    assert body[12:20] == component2.to_bytes()
    assert len(body) == 67  # 66 payload + checksum


@pytest.mark.asyncio
async def test_start_process_sets_the_two_cup_flag():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)
    machine.reply = build_inbound("A", b"", encrypted=False)

    await protocol.start_process(machine.write, MachineProcess.PRODUCT, two_cups=True)

    body = rc4(machine.frames[0][3:-1])
    assert struct.unpack(">h", body[0:2])[0] == MachineProcess.PRODUCT
    assert struct.unpack(">h", body[2:4])[0] == 2
    assert struct.unpack(">h", body[8:10])[0] == 1


@pytest.mark.asyncio
async def test_write_text_pads_to_64_bytes():
    protocol = MelittaProtocol(frame_timeout=1)
    machine = FakeMachine(protocol)
    machine.reply = build_inbound("A", b"", encrypted=False)

    await protocol.write_text(machine.write, 401, "Cappuccino")

    body = rc4(machine.frames[0][3:-1])
    assert struct.unpack(">h", body[0:2])[0] == 401
    assert body[2:12] == b"Cappuccino"
    assert body[12:66] == bytes(54)


@pytest.mark.asyncio
async def test_reset_clears_pending_requests():
    protocol = MelittaProtocol(frame_timeout=5)
    machine = FakeMachine(protocol)

    task = asyncio.create_task(protocol.read_status(machine.write))
    await asyncio.sleep(0)
    protocol.reset()

    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# Model / recipe metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("8604A1B2C3", MachineType.BARISTA_TS),
        ("8601FFFF", MachineType.BARISTA_TS),
        ("8501abcd", MachineType.BARISTA_TS),
        ("8301abcd", MachineType.BARISTA_T),
        ("8401abcd", MachineType.BARISTA_T),
        ("Melitta", None),
        ("", None),
        (None, None),
    ],
)
def test_machine_type_from_name(name, expected):
    assert machine_type_from_name(name) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("8604A1B2C3", True),
        ("8604", False),  # prefix alone is not a machine name
        ("8604ZZZZ", False),  # suffix must be hex
        ("1234abcd", False),
        (None, False),
    ],
)
def test_is_supported_name(name, expected):
    assert is_supported_name(name) is expected


def test_available_recipes_per_model():
    ts_recipes = available_recipes(MachineType.BARISTA_TS)
    t_recipes = available_recipes(MachineType.BARISTA_T)

    assert len(ts_recipes) == 24
    assert len(t_recipes) == 21
    assert Recipe.RED_EYE in ts_recipes
    assert Recipe.RED_EYE not in t_recipes
    # Unknown model: expose everything rather than hide drinks.
    assert available_recipes(None) == ts_recipes


def test_recipe_key_for_type_covers_every_recipe():
    from custom_components.melitta_barista_ts.const import RECIPE_TYPES

    for recipe, recipe_type in RECIPE_TYPES.items():
        key = recipe_key_for_type(recipe_type)
        assert 0 <= key <= 7, recipe
    # Freestyle and unknown types both land on the menu family.
    assert recipe_key_for_type(24) == 7
    assert recipe_key_for_type(200) == 7
