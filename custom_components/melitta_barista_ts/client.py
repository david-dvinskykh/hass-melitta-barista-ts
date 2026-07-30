"""BLE transport for the Melitta Barista T/TS Smart.

Owns the GATT connection and hands decoded notifications to
:class:`~.machine.MelittaMachine`. Connections are established lazily and
torn down on error; the coordinator drives reconnection by simply asking for
the next status.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from .const import (
    CHAR_NOTIFY_UUID,
    CHAR_WRITE_UUID_CANDIDATES,
    CONNECT_ATTEMPTS,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_FRAME_TIMEOUT,
    PAIR_SETTLE_DELAY,
    SERVICE_UUID,
)
from .machine import MachineError, MelittaMachine
from .pairing import async_ensure_agent
from .protocol import MachineStatus

_LOGGER = logging.getLogger(__name__)

DeviceProvider = Callable[[], BLEDevice | None]


class MelittaConnectionError(MachineError):
    """The machine could not be reached over BLE."""


class MelittaBleClient:
    """Connect to the machine on demand and keep the session alive."""

    def __init__(
        self,
        device_provider: DeviceProvider,
        *,
        name: str,
        frame_timeout: float = DEFAULT_FRAME_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        use_pairing_agent: bool = True,
    ) -> None:
        """Store connection parameters; no I/O happens here."""
        self._device_provider = device_provider
        self._name = name
        self._connect_timeout = connect_timeout
        self._use_pairing_agent = use_pairing_agent
        self._client: BleakClient | None = None
        self._write_char: str | None = None
        self._connect_lock = asyncio.Lock()
        self._closing = False
        self._bonded = False
        self.machine = MelittaMachine(self._write, frame_timeout=frame_timeout)

    # -- state -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        """True when the GATT link is up and the handshake has completed."""
        return (
            self._client is not None
            and self._client.is_connected
            and self.machine.ready
        )

    def add_status_listener(
        self, listener: Callable[[MachineStatus], None]
    ) -> Callable[[], None]:
        """Subscribe to status frames pushed by the machine."""
        return self.machine.add_status_listener(listener)

    # -- connection ------------------------------------------------------

    async def async_connect(self) -> None:
        """Ensure a connected, handshaken session.

        Safe to call repeatedly; returns immediately when already connected.

        The machine bonds with Numeric Comparison, so a first connection has
        to request pairing. Bonding is expensive and only needed once, so this
        tries the cheap unbonded path first and escalates only when the
        session does not come up — mirroring what the vendor app does.
        """
        if self.connected:
            return

        async with self._connect_lock:
            if self.connected:
                return
            await self._async_disconnect_locked()

            device = self._device_provider()
            if device is None:
                raise MelittaConnectionError(
                    f"{self._name} is not currently visible to any Bluetooth adapter"
                )

            if self._use_pairing_agent:
                await async_ensure_agent()

            # Once bonded, skip straight to the fast path on every reconnect.
            attempts = (True,) if self._bonded else (False, True)
            last_error: Exception | None = None

            for index, pair in enumerate(attempts):
                if index:
                    # Let the adapter or proxy release the previous connection
                    # slot before asking for a bond on a fresh one.
                    await asyncio.sleep(PAIR_SETTLE_DELAY)
                try:
                    await self._async_open_session(device, pair=pair)
                except (MelittaConnectionError, MachineError, BleakError) as err:
                    _LOGGER.debug(
                        "Session setup failed for %s (pair=%s): %s",
                        device.address,
                        pair,
                        err,
                    )
                    last_error = err
                    await self._async_disconnect_locked()
                    continue

                self._bonded = True
                _LOGGER.debug(
                    "Session established with %s (pair=%s)", device.address, pair
                )
                return

            self._bonded = False
            raise MelittaConnectionError(
                f"could not establish a session with {self._name}: {last_error}"
            )

    async def _async_open_session(self, device: BLEDevice, *, pair: bool) -> None:
        """Connect, subscribe and handshake, or raise.

        ``establish_connection`` has no overall timeout of its own — it retries
        internally, and each attempt carries a 60 s safety timeout — so an
        unreachable machine can otherwise block for minutes and get the whole
        config entry setup cancelled. The bound below is what keeps a failed
        connect cheap.
        """
        try:
            async with asyncio.timeout(self._connect_timeout):
                client = await establish_connection(
                    BleakClient,
                    device,
                    self._name,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=CONNECT_ATTEMPTS,
                    pair=pair,
                )
        except (BleakError, TimeoutError) as err:
            raise MelittaConnectionError(f"could not connect: {err}") from err

        self._client = client
        try:
            self._write_char = _resolve_write_char(client)
            await client.start_notify(CHAR_NOTIFY_UUID, self._on_notify)
            async with asyncio.timeout(self._connect_timeout):
                await self.machine.handshake()
        except MachineError:
            raise
        except Exception as err:
            raise MelittaConnectionError(f"session setup failed: {err}") from err

    async def async_disconnect(self) -> None:
        """Tear down the GATT link."""
        async with self._connect_lock:
            await self._async_disconnect_locked()

    async def async_close(self) -> None:
        """Disconnect permanently; further connect attempts are refused."""
        self._closing = True
        await self.async_disconnect()

    async def _async_disconnect_locked(self) -> None:
        client, self._client = self._client, None
        self._write_char = None
        self.machine.reset()
        if client is None:
            return
        try:
            await client.disconnect()
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("Error while disconnecting: %s", err)

    def _on_disconnect(self, _client: BleakClient) -> None:
        _LOGGER.debug("%s disconnected", self._name)
        self._client = None
        self._write_char = None
        self.machine.reset()

    # -- transport -------------------------------------------------------

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        self.machine.feed(bytes(data))

    async def _write(self, data: bytes) -> None:
        client = self._client
        char = self._write_char
        if client is None or char is None or not client.is_connected:
            raise MelittaConnectionError("not connected")
        await client.write_gatt_char(char, data, response=False)

    # -- convenience -----------------------------------------------------

    async def async_run(self, action, *args, **kwargs):
        """Connect if needed, then run ``action`` on the machine.

        A transport error drops the session so the next call reconnects
        instead of retrying on a dead handle.
        """
        if self._closing:
            raise MelittaConnectionError("client is shutting down")
        await self.async_connect()
        try:
            return await action(*args, **kwargs)
        except (BleakError, TimeoutError) as err:
            await self.async_disconnect()
            raise MelittaConnectionError(f"transport error: {err}") from err


def _resolve_write_char(client: BleakClient) -> str:
    """Pick the characteristic used for outgoing frames.

    Firmware revisions disagree on whether that is ``AD01`` or ``AD03``, so
    prefer the known candidates in order and otherwise fall back to any
    writable characteristic inside the vendor service.
    """
    service = client.services.get_service(SERVICE_UUID)
    if service is None:
        raise MelittaConnectionError(f"service {SERVICE_UUID} not found on the device")

    available = {char.uuid.lower(): char for char in service.characteristics}

    for candidate in CHAR_WRITE_UUID_CANDIDATES:
        char = available.get(candidate.lower())
        if char is not None and _is_writable(char):
            return char.uuid

    for char in service.characteristics:
        if _is_writable(char) and char.uuid.lower() != CHAR_NOTIFY_UUID.lower():
            _LOGGER.debug("Falling back to write characteristic %s", char.uuid)
            return char.uuid

    raise MelittaConnectionError("no writable characteristic in the vendor service")


def _is_writable(char) -> bool:
    """True when the characteristic accepts writes."""
    return bool({"write", "write-without-response"} & set(char.properties))
