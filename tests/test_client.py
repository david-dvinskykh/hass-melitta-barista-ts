"""Tests for the BLE transport's connect behaviour."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak import BleakError

from custom_components.melitta_barista_ts.client import (
    MelittaBleClient,
    MelittaConnectionError,
)
from custom_components.melitta_barista_ts.const import CONNECT_ATTEMPTS, SERVICE_UUID
from custom_components.melitta_barista_ts.machine import HandshakeError

ESTABLISH = "custom_components.melitta_barista_ts.client.establish_connection"
AGENT = "custom_components.melitta_barista_ts.client.async_ensure_agent"
WRITE_CHAR = "0000ad03-b35c-11e4-9813-0002a5d5c51b"


def _fake_gatt_client() -> MagicMock:
    """A BleakClient stand-in exposing the vendor service."""
    char = MagicMock()
    char.uuid = WRITE_CHAR
    char.properties = ["write-without-response"]

    service = MagicMock()
    service.characteristics = [char]

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


async def test_reconnect_after_bonding_skips_the_unbonded_attempt() -> None:
    """Once bonded, go straight to the bonded path."""
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
    assert attempts == [False, True]


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
