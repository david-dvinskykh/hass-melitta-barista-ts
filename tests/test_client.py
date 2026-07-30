"""Tests for the BLE transport's connect behaviour."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak import BleakError

from custom_components.melitta_barista_ts.client import (
    MelittaBleClient,
    MelittaConnectionError,
    _notify_chars,
    _write_char_candidates,
    scanner_source,
)
from custom_components.melitta_barista_ts.const import CONNECT_ATTEMPTS, SERVICE_UUID
from custom_components.melitta_barista_ts.machine import CommandTimeout, HandshakeError

ESTABLISH = "custom_components.melitta_barista_ts.client.establish_connection"
AGENT = "custom_components.melitta_barista_ts.client.async_ensure_agent"
WRITE_CHAR = "0000ad03-b35c-11e4-9813-0002a5d5c51b"


def _char(uuid: str, *properties: str) -> MagicMock:
    char = MagicMock()
    char.uuid = uuid
    char.properties = list(properties) or ["write-without-response"]
    return char


NOTIFY_CHAR = "0000ad02-b35c-11e4-9813-0002a5d5c51b"


def _fake_gatt_client(*chars: MagicMock) -> MagicMock:
    """A BleakClient stand-in exposing the vendor service."""
    service = MagicMock()
    service.characteristics = list(chars) or [
        _char(WRITE_CHAR, "write"),
        _char(NOTIFY_CHAR, "notify"),
    ]

    client = MagicMock()
    client.is_connected = True
    client.services.get_service.return_value = service
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def _no_pairing_agent():
    with patch(AGENT, AsyncMock(return_value=False)):
        yield


@pytest.fixture(autouse=True)
def _no_settle_delay():
    """Collapse the pause between the unbonded and bonded attempts."""
    with patch("custom_components.melitta_barista_ts.client.PAIR_SETTLE_DELAY", 0):
        yield


def _ready_handshake(client: MelittaBleClient) -> AsyncMock:
    """A handshake that installs a session key, as the real one does.

    Without it the client never reports itself connected, however well the
    GATT link came up.
    """

    async def _handshake() -> None:
        client.machine._key_prefix = b"\x11\x22"

    return AsyncMock(side_effect=_handshake)


def _make_client(**kwargs) -> MelittaBleClient:
    device = MagicMock()
    device.address = "AA:BB:CC:DD:EE:FF"
    return MelittaBleClient(lambda: device, name="Machine", **kwargs)


async def test_connect_bounds_a_hanging_establish_connection() -> None:
    """A stuck connect must not run past the configured timeout.

    ``establish_connection`` takes no timeout of its own and retries with a
    60 s safety timeout per attempt, so without an explicit bound a single
    connect can outlast Home Assistant's config entry setup and get it
    cancelled.
    """

    async def _never_returns(*args, **kwargs):
        await asyncio.sleep(3600)

    client = _make_client(connect_timeout=0.05)
    with (
        patch(ESTABLISH, side_effect=_never_returns),
        pytest.raises(MelittaConnectionError),
    ):
        await asyncio.wait_for(client.async_connect(), timeout=5)

    assert not client.connected


async def test_connect_caps_the_library_retry_count() -> None:
    """Retries are bounded by us, not left at the library default of four."""
    established = AsyncMock(return_value=_fake_gatt_client())
    client = _make_client()

    with (
        patch(ESTABLISH, established),
        patch.object(client.machine, "handshake", AsyncMock()),
    ):
        await client.async_connect()

    assert established.await_args.kwargs["max_attempts"] == CONNECT_ATTEMPTS


async def test_first_connect_tries_unbonded_then_bonded() -> None:
    """The machine bonds with numeric comparison; escalate only if needed."""
    gatt = _fake_gatt_client()
    established = AsyncMock(return_value=gatt)
    client = _make_client()

    calls = []

    async def _handshake() -> None:
        calls.append(None)
        if len(calls) == 1:
            raise HandshakeError("no session key")
        # The real handshake installs the session key; that is what makes
        # the client report itself as connected.
        client.machine._key_prefix = b"\x11\x22"

    handshake = AsyncMock(side_effect=_handshake)
    with (
        patch(ESTABLISH, established),
        patch.object(client.machine, "handshake", handshake),
    ):
        await client.async_connect()

    attempts = [call.kwargs["pair"] for call in established.await_args_list]
    assert attempts == [False, True]
    assert client.connected


async def test_reconnect_starts_from_the_unbonded_attempt_again() -> None:
    """An existing bond makes ``pair=False`` the cheap path, not a wasted one.

    The machine asks for encryption itself using the key both sides already
    hold, so a reconnect never has to request bonding a second time.
    """
    gatt = _fake_gatt_client()
    established = AsyncMock(return_value=gatt)

    client = _make_client()
    with (
        patch(ESTABLISH, established),
        patch.object(client.machine, "handshake", AsyncMock()),
    ):
        await client.async_connect()
        await client.async_disconnect()
        gatt.is_connected = True
        await client.async_connect()

    attempts = [call.kwargs["pair"] for call in established.await_args_list]
    assert attempts == [False, False]


async def test_refused_pairing_drops_the_bond_and_pairs_again() -> None:
    """A bond the machine has forgotten is cleared, not retried forever."""
    gatt = _fake_gatt_client()
    gatt.unpair = AsyncMock()
    attempts: list[bool] = []

    async def _establish(*args, pair: bool = False, **kwargs):
        attempts.append(pair)
        # Both the plain and the pairing attempt fail; the unpair connect and
        # the attempt after it succeed.
        if len(attempts) == 1:
            raise BleakError("connection dropped")
        if len(attempts) == 2:
            raise BleakError("Pairing failed due to error: 102")
        return gatt

    client = _make_client()
    with (
        patch(ESTABLISH, AsyncMock(side_effect=_establish)),
        patch.object(client.machine, "handshake", _ready_handshake(client)),
    ):
        await client.async_connect()

    # pair=False, pair=True, the unpair connect, then pair=True once more.
    assert attempts == [False, True, False, True]
    gatt.unpair.assert_awaited_once()
    gatt.disconnect.assert_awaited()
    assert client.connected


async def test_the_bond_is_dropped_at_most_once_per_connect() -> None:
    """Clearing it twice would only cost another connect window."""
    gatt = _fake_gatt_client()
    gatt.unpair = AsyncMock()
    established = AsyncMock(side_effect=BleakError("Pairing failed due to error: 102"))

    client = _make_client()
    with (
        patch(ESTABLISH, established),
        pytest.raises(MelittaConnectionError, match="Pairing failed"),
    ):
        await client.async_connect()

    assert [call.kwargs["pair"] for call in established.await_args_list] == [
        False,
        True,
        False,  # the connect made to unpair, which fails too
        True,
    ]


async def test_an_unreachable_machine_keeps_its_bond() -> None:
    """A timeout says nothing about the bond — do not throw it away."""
    gatt = _fake_gatt_client()
    gatt.unpair = AsyncMock()

    client = _make_client()
    with (
        patch(ESTABLISH, AsyncMock(side_effect=BleakError("device not found"))),
        pytest.raises(MelittaConnectionError),
    ):
        await client.async_connect()

    gatt.unpair.assert_not_awaited()


async def test_a_proxy_that_cannot_unpair_is_reported_not_raised() -> None:
    """Older proxy firmware has no unpair; the attempt after it still runs."""
    gatt = _fake_gatt_client()
    gatt.unpair = AsyncMock(side_effect=BleakError("not supported"))
    calls: list[bool] = []

    async def _establish(*args, pair: bool = False, **kwargs):
        calls.append(pair)
        if len(calls) <= 2:
            raise BleakError("Pairing failed due to error: 102")
        return gatt

    client = _make_client()
    with (
        patch(ESTABLISH, AsyncMock(side_effect=_establish)),
        patch.object(client.machine, "handshake", _ready_handshake(client)),
    ):
        await client.async_connect()

    assert calls == [False, True, False, True]
    assert client.connected


async def test_connect_reports_the_last_failure() -> None:
    """Both attempts failing surfaces one error naming the cause."""
    client = _make_client()
    with (
        patch(ESTABLISH, AsyncMock(side_effect=BleakError("out of slots"))),
        pytest.raises(MelittaConnectionError, match="out of slots"),
    ):
        await client.async_connect()


async def test_connect_without_a_visible_device() -> None:
    """A machine that is not advertising fails fast and clearly."""
    client = MelittaBleClient(lambda: None, name="Machine")
    with pytest.raises(MelittaConnectionError, match="not currently visible"):
        await client.async_connect()


async def test_missing_vendor_service_is_reported() -> None:
    """A device without the AD00 service is not our machine."""
    gatt = _fake_gatt_client()
    gatt.services.get_service.return_value = None

    client = _make_client()
    with (
        patch(ESTABLISH, AsyncMock(return_value=gatt)),
        pytest.raises(MelittaConnectionError, match=SERVICE_UUID),
    ):
        await client.async_connect()


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({"source": "44:1D:64:E4:5C:32"}, "44:1D:64:E4:5C:32"),
        ({"path": "/org/bluez/hci0/dev_FC_E1_FF_68_54_2C"}, "hci0"),
        ({}, "unknown adapter"),
        (None, "unknown adapter"),
    ],
)
def test_scanner_source_names_the_adapter(details, expected) -> None:
    """Proxies report a MAC, BlueZ an object path; both should be readable."""
    device = MagicMock()
    device.details = details
    assert scanner_source(device) == expected


ALT_WRITE_CHAR = "0000ad01-b35c-11e4-9813-0002a5d5c51b"


def test_write_char_candidates_are_ordered_by_preference() -> None:
    """Known UUIDs come first, then anything else writable."""
    other = "0000ad04-b35c-11e4-9813-0002a5d5c51b"
    client = _fake_gatt_client(
        _char(other),
        _char(ALT_WRITE_CHAR),
        _char(WRITE_CHAR),
        _char(NOTIFY_CHAR, "notify"),
    )

    uuids = [char.uuid for char in _write_char_candidates(client)]
    assert uuids == [WRITE_CHAR, ALT_WRITE_CHAR, other]


async def test_handshake_falls_back_to_the_other_write_characteristic() -> None:
    """Writing to the wrong characteristic fails silently — try the next one.

    The machine accepts the write, answers nothing and drops the link, so the
    only way to tell the characteristics apart is to see which one replies.
    """
    gatt = _fake_gatt_client(
        _char(WRITE_CHAR, "write"),
        _char(ALT_WRITE_CHAR, "write"),
        _char(NOTIFY_CHAR, "notify"),
    )
    client = _make_client()
    attempts = []

    async def _handshake() -> None:
        attempts.append(client._write_char.uuid)
        if client._write_char.uuid == WRITE_CHAR:
            raise CommandTimeout("no response to HU")
        client.machine._key_prefix = b"\x11\x22"

    with (
        patch(ESTABLISH, AsyncMock(return_value=gatt)),
        patch.object(client.machine, "handshake", AsyncMock(side_effect=_handshake)),
    ):
        await client.async_connect()

    assert attempts == [WRITE_CHAR, ALT_WRITE_CHAR]
    assert client._write_char.uuid == ALT_WRITE_CHAR
    assert client.connected


async def test_silent_characteristic_everywhere_is_reported() -> None:
    """When nothing answers, say so and name what was tried."""
    gatt = _fake_gatt_client(
        _char(WRITE_CHAR, "write"),
        _char(ALT_WRITE_CHAR, "write"),
        _char(NOTIFY_CHAR, "notify"),
    )
    client = _make_client()

    with (
        patch(ESTABLISH, AsyncMock(return_value=gatt)),
        patch.object(
            client.machine,
            "handshake",
            AsyncMock(side_effect=CommandTimeout("no response to HU")),
        ),
        pytest.raises(MelittaConnectionError, match="no handshake on any writable"),
    ):
        await client.async_connect()


ALT_NOTIFY_CHAR = "0000ad06-b35c-11e4-9813-0002a5d5c51b"


def test_every_notify_characteristic_is_collected() -> None:
    """This firmware has a second notify channel besides the documented one."""
    client = _fake_gatt_client(
        _char(ALT_NOTIFY_CHAR, "notify", "read"),
        _char(WRITE_CHAR, "write"),
        _char(NOTIFY_CHAR, "notify"),
    )

    # The documented channel comes first; the extra one is still subscribed.
    assert _notify_chars(client) == [NOTIFY_CHAR, ALT_NOTIFY_CHAR]


async def test_all_notify_characteristics_are_subscribed() -> None:
    """A reply must not be missed for arriving on the unexpected channel."""
    gatt = _fake_gatt_client(
        _char(WRITE_CHAR, "write"),
        _char(NOTIFY_CHAR, "notify"),
        _char(ALT_NOTIFY_CHAR, "notify", "read"),
    )
    client = _make_client()

    with (
        patch(ESTABLISH, AsyncMock(return_value=gatt)),
        patch.object(client.machine, "handshake", AsyncMock()),
    ):
        await client.async_connect()

    subscribed = [call.args[0] for call in gatt.start_notify.await_args_list]
    assert subscribed == [NOTIFY_CHAR, ALT_NOTIFY_CHAR]


@pytest.mark.parametrize(
    ("properties", "expected_response"),
    [
        (("write",), True),
        (("write", "write-without-response"), False),
        (("write-without-response",), False),
    ],
)
async def test_write_honours_the_declared_write_type(
    properties, expected_response
) -> None:
    """A characteristic that only declares "write" needs a response write.

    Asking for write-without-response there is not a valid GATT operation and
    the frame never reaches the machine.
    """
    gatt = _fake_gatt_client(
        _char(WRITE_CHAR, *properties), _char(NOTIFY_CHAR, "notify")
    )
    client = _make_client()

    with (
        patch(ESTABLISH, AsyncMock(return_value=gatt)),
        patch.object(client.machine, "handshake", AsyncMock()),
    ):
        await client.async_connect()
        await client._write(b"frame")

    assert gatt.write_gatt_char.await_args.kwargs["response"] is expected_response


async def test_a_silent_machine_is_reported_as_unbonded() -> None:
    """Connecting fine but ignoring the handshake means there is no bond.

    Numeric Comparison needs someone at the machine, so the error has to say
    that rather than read like a machine out of range.
    """
    gatt = _fake_gatt_client(
        _char(WRITE_CHAR, "write"),
        _char(NOTIFY_CHAR, "notify"),
    )
    client = _make_client()

    with (
        patch(ESTABLISH, AsyncMock(return_value=gatt)),
        patch.object(
            client.machine,
            "handshake",
            AsyncMock(side_effect=CommandTimeout("no response to HU")),
        ),
        pytest.raises(MelittaConnectionError, match="pairing mode"),
    ):
        await client.async_connect()


async def test_an_unreachable_machine_says_nothing_about_pairing() -> None:
    """A machine that never answered the radio is a different problem."""
    client = _make_client()

    with (
        patch(ESTABLISH, AsyncMock(side_effect=BleakError("device not found"))),
        pytest.raises(MelittaConnectionError) as caught,
    ):
        await client.async_connect()

    assert "pairing mode" not in str(caught.value)


async def test_the_working_adapter_is_remembered() -> None:
    """Bonds live per adapter, so the next connect should go back there."""
    device = MagicMock()
    device.address = "AA:BB:CC:DD:EE:FF"
    device.details = {"source": "44:1D:64:E4:81:3A"}
    client = MelittaBleClient(lambda: device, name="Machine")

    assert client.last_good_source is None
    with (
        patch(ESTABLISH, AsyncMock(return_value=_fake_gatt_client())),
        patch.object(client.machine, "handshake", AsyncMock()),
    ):
        await client.async_connect()

    assert client.last_good_source == "44:1D:64:E4:81:3A"
