"""Tests for the Melitta BLE wire protocol."""

from __future__ import annotations

import pytest

from custom_components.melitta_barista_ts.const import (
    FRAME_END,
    FRAME_START,
    InfoMessage,
    MachineProcess,
    Manipulation,
    SubProcess,
)
from custom_components.melitta_barista_ts.protocol import (
    FrameParser,
    MachineRecipe,
    MachineStatus,
    NumericalValue,
    ProtocolError,
    RecipeComponent,
    build_frame,
    checksum,
    chunk_for_ble,
    hu_verifier,
    rc4,
)


def test_rc4_is_symmetric():
    plaintext = bytes(range(64))
    assert rc4(rc4(plaintext)) == plaintext


def test_rc4_matches_reference_vector():
    """RC4 with the well-known "Key"/"Plaintext" vector from RFC 6229 notes."""
    assert rc4(b"Plaintext", b"Key").hex() == "bbf316e8d940af0ad3"


def test_checksum_is_ones_complement_of_sum():
    assert checksum(b"\x01\x02\x03") == (~6) & 0xFF
    assert checksum(b"") == 0xFF


def test_hu_verifier_is_deterministic_and_two_bytes():
    first = hu_verifier(b"\x01\x02\x03\x04")
    assert len(first) == 2
    assert first == hu_verifier(b"\x01\x02\x03\x04")
    assert first != hu_verifier(b"\x01\x02\x03\x05")


def test_hu_verifier_rejects_empty_input():
    with pytest.raises(ProtocolError):
        hu_verifier(b"")


def test_build_frame_structure():
    frame = build_frame("HX", None, b"\xab\xcd")
    assert frame[0] == FRAME_START
    assert frame[1:3] == b"HX"
    assert frame[-1] == FRAME_END
    # key_prefix(2) + checksum(1), encrypted
    assert len(frame) == 1 + 2 + 3 + 1

    decrypted = rc4(frame[3:-1])
    assert decrypted[:2] == b"\xab\xcd"
    assert checksum(b"HX" + decrypted[:-1]) == decrypted[-1]


def test_build_frame_without_encryption_is_plaintext():
    frame = build_frame("A", None, None, encrypt=False)
    assert frame == bytes([FRAME_START]) + b"A" + bytes([checksum(b"A")]) + bytes(
        [FRAME_END]
    )


def test_chunk_for_ble_splits_at_mtu():
    assert chunk_for_ble(bytes(45), 20) == [bytes(20), bytes(20), bytes(5)]
    assert chunk_for_ble(b"", 20) == []


# --------------------------------------------------------------------------
# Frame parsing
# --------------------------------------------------------------------------


def _machine_frame(command: str, payload: bytes, *, encrypt: bool = True) -> bytes:
    """Build a frame the way the machine does — no key prefix on responses."""
    body = command.encode() + payload
    body += bytes([checksum(body)])
    tail = body[len(command) :]
    if encrypt:
        tail = rc4(tail)
    return bytes([FRAME_START]) + command.encode() + tail + bytes([FRAME_END])


def _collect() -> tuple[FrameParser, list[tuple[str, bytes]]]:
    seen: list[tuple[str, bytes]] = []
    return FrameParser(lambda cmd, payload: seen.append((cmd, payload))), seen


def test_parser_decodes_status_frame():
    parser, seen = _collect()
    payload = bytes([0, 4, 0, 2, 0b0100, 3, 0, 42])
    parser.feed(_machine_frame("HX", payload))

    assert seen == [("HX", payload)]


def test_parser_decodes_ack():
    parser, seen = _collect()
    parser.feed(_machine_frame("A", b"", encrypt=False))
    assert seen == [("A", b"")]


def test_parser_handles_split_notifications():
    parser, seen = _collect()
    frame = _machine_frame("HX", bytes(8))
    for chunk in chunk_for_ble(frame, 3):
        parser.feed(chunk)
    assert len(seen) == 1


def test_parser_ignores_leading_garbage():
    parser, seen = _collect()
    parser.feed(b"\x00\xff\x11")
    parser.feed(_machine_frame("HX", bytes(8)))
    assert len(seen) == 1


def test_parser_drops_frame_with_bad_checksum():
    parser, seen = _collect()
    frame = bytearray(_machine_frame("HX", bytes(8)))
    frame[5] ^= 0xFF
    parser.feed(bytes(frame))
    assert seen == []


def test_parser_survives_frame_markers_inside_ciphertext():
    """``S``/``E`` bytes in RC4 output must not truncate the frame."""
    parser, seen = _collect()
    # Search for a status payload whose ciphertext contains an E byte in a
    # position that would end the frame early if length were not checked.
    for candidate in range(256):
        payload = bytes([candidate]) * 8
        frame = _machine_frame("HX", payload)
        if FRAME_END in frame[3:-1]:
            parser.feed(frame)
            assert seen == [("HX", payload)]
            return
    pytest.fail("no payload produced an embedded frame-end byte")


def test_parser_recovers_after_truncated_frame():
    parser, seen = _collect()
    parser.feed(_machine_frame("HX", bytes(8))[:6])
    parser.reset()
    parser.feed(_machine_frame("HX", bytes(8)))
    assert len(seen) == 1


# --------------------------------------------------------------------------
# Payload models
# --------------------------------------------------------------------------


def test_status_payload_decoding():
    status = MachineStatus.from_payload(bytes([0, 4, 0, 1, 0b0101, 4, 0, 9]))
    assert status.process_enum is MachineProcess.PRODUCT
    assert status.sub_process_enum is SubProcess.GRINDING
    assert status.manipulation_enum is Manipulation.FILL_WATER
    assert status.progress == 9
    assert status.is_busy
    assert InfoMessage.FILL_BEANS_1 in status.info_flags
    assert InfoMessage.EASY_CLEAN in status.info_flags
    assert InfoMessage.FILL_BEANS_2 not in status.info_flags


def test_status_unknown_values_decode_to_none():
    status = MachineStatus.from_payload(bytes([0, 77, 0, 88, 0, 99, 0, 0]))
    assert status.process_enum is None
    assert status.sub_process_enum is None
    assert status.manipulation_enum is None


def test_status_payload_too_short():
    with pytest.raises(ProtocolError):
        MachineStatus.from_payload(bytes(4))


def test_recipe_component_roundtrip():
    component = RecipeComponent(
        process=1, shots=1, blend=1, intensity=3, aroma=0, temperature=2, portion=8
    )
    assert component.to_bytes().hex() == "0101010300020800"
    assert RecipeComponent.from_bytes(component.to_bytes()) == component
    assert component.portion_ml == 40


def test_recipe_payload_has_no_recipe_key_byte():
    """``HC`` responses go straight from recipe_type to the first component."""
    payload = bytearray(66)
    payload[0:2] = (200).to_bytes(2, "big")
    payload[2] = 0
    payload[3:11] = bytes.fromhex("0101010300020800")
    payload[11:19] = bytes.fromhex("0000000000020000")

    recipe = MachineRecipe.from_payload(bytes(payload))
    assert recipe.recipe_id == 200
    assert recipe.recipe_type == 0
    assert recipe.component1.intensity == 3
    assert recipe.component1.portion_ml == 40
    assert recipe.component2.process == 0


def test_numerical_value_decoding():
    payload = (150).to_bytes(2, "big") + (1234).to_bytes(4, "big")
    value = NumericalValue.from_payload(payload)
    assert value.value_id == 150
    assert value.value == 1234
