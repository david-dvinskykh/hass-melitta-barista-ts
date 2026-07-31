"""BLE transport for the Melitta Barista T/TS Smart.

Owns the GATT connection and hands decoded notifications to
:class:`~.machine.MelittaMachine`. Connections are established lazily and
torn down on error; the coordinator drives reconnection by simply asking for
the next status.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Final

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

#: Returns the machine as each adapter that can hear it sees it, best
#: signal first — or a single device, which is all the tests need.
DeviceProvider = Callable[[], "list[BLEDevice] | BLEDevice | None"]


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
        self._write_char = None
        self._notify_chars: list[str] = []
        self._connect_lock = asyncio.Lock()
        self._closing = False
        #: Adapter or proxy that carried the last working session. Bonds are
        #: held per adapter, so the one that worked is worth going back to.
        self.last_good_source: str | None = None
        self._rotation = 0
        self._candidates = 0
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
        session does not come up — mirroring what the vendor app does. An
        existing bond makes that first attempt the fast one: the link comes up
        unencrypted and the machine asks for encryption itself, using the key
        both sides already hold.
        """
        if self.connected:
            return

        async with self._connect_lock:
            if self.connected:
                return
            await self._async_disconnect_locked()

            device = self._pick_device()
            if device is None:
                raise MelittaConnectionError(
                    f"{self._name} is not currently visible to any Bluetooth adapter"
                )

            if self._use_pairing_agent:
                await async_ensure_agent()

            last_error: Exception | None = None
            silent_machine = False
            refused = False

            for index, pair in enumerate((False, True)):
                if index:
                    # Let the adapter or proxy release the previous connection
                    # slot before asking for a bond on a fresh one.
                    await asyncio.sleep(PAIR_SETTLE_DELAY)
                try:
                    await self._async_open_session(device, pair=pair)
                except (MelittaConnectionError, MachineError, BleakError) as err:
                    _LOGGER.debug(
                        "Session setup failed for %s via %s (pair=%s): %s",
                        device.address,
                        scanner_source(device),
                        pair,
                        err,
                    )
                    last_error = err
                    silent_machine = silent_machine or _went_silent(err)
                    refused = refused or _is_pairing_refused(err)
                    await self._async_disconnect_locked()
                    continue

                self.last_good_source = scanner_source(device)
                _LOGGER.debug(
                    "Session established with %s via %s (pair=%s)",
                    device.address,
                    self.last_good_source,
                    pair,
                )
                return

            # Bonds are per adapter, and one holding a key the machine has
            # forgotten refuses every attempt for good. Give up on this
            # adapter and let the next connect try another one, rather than
            # asking the same one again every poll for ever.
            self._rotation += 1
            if refused and self.last_good_source == scanner_source(device):
                self.last_good_source = None

            message = f"could not establish a session with {self._name}: {last_error}"
            if refused and self._candidates > 1:
                message += (
                    f". {scanner_source(device)} holds a bond the machine no "
                    "longer accepts; the next attempt will use one of the "
                    f"other {self._candidates - 1} adapter(s) that can reach it"
                )
            elif refused:
                # Nothing to fall back to, so this is as far as the
                # integration gets on its own.
                message += (
                    f". {scanner_source(device)} is the only adapter that can "
                    "reach the machine, and the bond it holds is one the "
                    "machine no longer accepts. Put the machine into pairing "
                    "mode from its own menu and confirm the code on its "
                    "display; melitta_barista_ts.repair_connection drops the "
                    "bond first if pairing is still refused"
                )
            elif silent_machine:
                # The link came up and the machine ignored the handshake, so
                # there is no bond it trusts — and Numeric Comparison cannot be
                # completed from this side alone. Say so, or the failure reads
                # like a machine that is simply out of range.
                message += (
                    ". The machine answered the connection but not the "
                    "handshake, which means it is not bonded with Home "
                    "Assistant. Put it into pairing mode from its own menu "
                    "and confirm the code shown on its display"
                )
            raise MelittaConnectionError(message)

    def _pick_device(self) -> BLEDevice | None:
        """Choose which adapter's view of the machine to connect through.

        The adapter that carried the last working session comes first: bonds
        are held per adapter, and going back to the bonded one avoids a
        handshake the machine would ignore. Failing that the candidates are
        rotated, so an adapter that cannot bond is not retried every single
        poll while eight others go untried.
        """
        candidates = self._device_provider()
        if not isinstance(candidates, (list, tuple)):
            self._candidates = 1
            return candidates  # a provider that only knows one device
        self._candidates = len(candidates)
        if not candidates:
            return None

        if self.last_good_source is not None:
            for device in candidates:
                if scanner_source(device) == self.last_good_source:
                    return device

        return candidates[self._rotation % len(candidates)]

    async def async_clear_bond(self) -> None:
        """Drop the bond on the adapter the machine is reachable through."""
        device = self._pick_device()
        if device is None:
            raise MelittaConnectionError(
                f"{self._name} is not currently visible to any Bluetooth adapter"
            )
        async with self._connect_lock:
            await self._async_disconnect_locked()
            await self._async_clear_bond(device)
        self.last_good_source = None

    async def _async_clear_bond(self, device: BLEDevice) -> None:
        """Forget the bond this side holds, so the next attempt pairs afresh.

        A machine that has been reset — or bonded with a phone since — no
        longer holds the key the adapter or proxy kept, and every pairing
        attempt from then on is refused. Only the local half of the bond can
        be dropped from here, but that is the half that is stale.

        Best effort throughout: an unreachable machine, or a proxy whose
        firmware was built without the unpair support, leaves the bond in
        place and the caller simply fails as it would have anyway.
        """
        _LOGGER.info("Clearing the stale bond for %s", device.address)
        try:
            async with asyncio.timeout(self._connect_timeout):
                client = await establish_connection(
                    BleakClient,
                    device,
                    self._name,
                    max_attempts=CONNECT_ATTEMPTS,
                    pair=False,
                )
        except (BleakError, TimeoutError, OSError) as err:
            _LOGGER.debug("Could not connect to clear the bond: %s", err)
            return

        try:
            await client.unpair()
            _LOGGER.info("Dropped the bond for %s", device.address)
        except (BleakError, OSError, NotImplementedError, AttributeError) as err:
            _LOGGER.warning(
                "Could not drop the bond for %s (%s). If pairing keeps being "
                "refused, forget the machine on the Bluetooth adapter — or on "
                "the ESPHome proxy — and try again",
                device.address,
                err,
            )
        finally:
            with contextlib.suppress(BleakError, OSError, TimeoutError):
                await client.disconnect()

    async def _async_open_session(self, device: BLEDevice, *, pair: bool) -> None:
        """Connect, subscribe and handshake, or raise.

        ``establish_connection`` has no overall timeout of its own — it retries
        internally, and each attempt carries a 60 s safety timeout — so an
        unreachable machine can otherwise block for minutes and get the whole
        config entry setup cancelled. The bound below is what keeps a failed
        connect cheap.
        """
        _LOGGER.debug(
            "Connecting to %s via %s (pair=%s)",
            device.address,
            scanner_source(device),
            pair,
        )
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
        except TimeoutError as err:
            # A bare TimeoutError carries no message, and cutting the library
            # off mid-retry also discards whatever it was about to report — so
            # say what timed out and where, or the log line reads as an
            # unexplained blank failure.
            raise MelittaConnectionError(
                f"connect via {scanner_source(device)} timed out after "
                f"{self._connect_timeout:g}s"
            ) from err
        except BleakError as err:
            raise MelittaConnectionError(f"could not connect: {err}") from err

        self._client = client
        try:
            candidates = _write_char_candidates(client)
            # This firmware exposes a second notify characteristic (AD06)
            # besides the documented AD02. Subscribing to every one of them
            # costs nothing — the frame parser ignores anything that is not a
            # well-formed frame — and means a reply cannot be missed just
            # because it arrived on the channel we did not expect.
            self._notify_chars = _notify_chars(client)
            for uuid in self._notify_chars:
                await client.start_notify(uuid, self._on_notify)
        except MelittaConnectionError:
            raise
        except Exception as err:
            raise MelittaConnectionError(f"session setup failed: {err}") from err

        await self._async_handshake_on_a_working_char(candidates)

    async def _async_handshake_on_a_working_char(self, candidates: list) -> None:
        """Handshake, trying each writable characteristic until one answers.

        Firmware revisions disagree about which characteristic of the vendor
        service accepts frames, and writing to the wrong one fails silently:
        the write is accepted, nothing comes back, and the machine drops the
        link. Rather than guess from the UUID, send the handshake and let the
        machine pick — the one that answers is the right one.
        """
        last_error: Exception | None = None

        for char in candidates:
            self._write_char = char
            self.machine.reset()
            try:
                async with asyncio.timeout(self._connect_timeout):
                    await self.machine.handshake()
            except (MachineError, TimeoutError) as err:
                _LOGGER.debug(
                    "No handshake on write characteristic %s: %s", char.uuid, err
                )
                last_error = err
                continue
            except Exception as err:
                raise MelittaConnectionError(f"session setup failed: {err}") from err

            _LOGGER.debug("Handshake answered on write characteristic %s", char.uuid)
            return

        self._write_char = None
        raise MelittaConnectionError(
            f"no handshake on any writable characteristic "
            f"({', '.join(char.uuid for char in candidates)}): {last_error}"
        )

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
        self._notify_chars = []
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
        self._notify_chars = []
        self.machine.reset()

    # -- transport -------------------------------------------------------

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        self.machine.feed(bytes(data))

    async def _write(self, data: bytes) -> None:
        client = self._client
        char = self._write_char
        if client is None or char is None or not client.is_connected:
            raise MelittaConnectionError("not connected")
        # Both vendor write characteristics declare plain "write" — asking for
        # a write-without-response on those is not a valid GATT operation, and
        # the frame never reaches the machine.
        response = "write-without-response" not in char.properties
        await client.write_gatt_char(char, data, response=response)

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


def scanner_source(device: BLEDevice) -> str:
    """Name the adapter or proxy the device was last heard on.

    Home Assistant picks the scanner with the strongest signal, which is not
    always the one that can hold a connection — a proxy can hear the machine
    fine and still fail to establish GATT. Naming it makes that visible.
    """
    details = getattr(device, "details", None)
    if isinstance(details, dict):
        # Remote scanners (ESPHome proxies) carry the proxy's MAC here.
        source = details.get("source")
        if source:
            return str(source)
        # BlueZ instead exposes an object path like
        # /org/bluez/hci0/dev_FC_E1_FF_68_54_2C — the adapter is in it.
        path = details.get("path")
        if isinstance(path, str):
            parts = path.split("/")
            if len(parts) > 3 and parts[3]:
                return parts[3]
    return "unknown adapter"


_PAIRING_REFUSED_MARKERS: Final = (
    # bleak-esphome, when the proxy reports an SMP failure of its own
    "pairing failed",
    # BlueZ, via org.bluez.Error.AuthenticationFailed / -Rejected
    "authentication failed",
    "authenticationfailed",
    "authentication rejected",
    "authenticationrejected",
)


def _is_pairing_refused(err: Exception) -> bool:
    """True when bonding was refused rather than simply unreachable.

    Worth telling apart: a refusal points at a bond one side no longer
    honours, which dropping it can fix, while a timeout only means the radio
    could not reach the machine — clearing a perfectly good bond over that
    would force a fresh Numeric Comparison for nothing.
    """
    text = str(err).lower()
    return any(marker in text for marker in _PAIRING_REFUSED_MARKERS)


def _went_silent(err: Exception) -> bool:
    """True when the machine took the connection but answered nothing.

    Distinct from every other failure in the ladder: the radio link and the
    GATT table are fine, so the machine is deliberately ignoring us — which
    is what it does over a link it has no bond for.
    """
    return "no handshake on any writable characteristic" in str(err)


def _notify_chars(client: BleakClient) -> list[str]:
    """List every characteristic of the vendor service that can notify."""
    service = client.services.get_service(SERVICE_UUID)
    if service is None:
        raise MelittaConnectionError(f"service {SERVICE_UUID} not found on the device")

    found = [
        char.uuid for char in service.characteristics if "notify" in char.properties
    ]
    if not found:
        raise MelittaConnectionError("no notify characteristic in the vendor service")
    # Keep the documented channel first; it is the one that normally answers.
    found.sort(key=lambda uuid: uuid.lower() != CHAR_NOTIFY_UUID.lower())
    return found


def _write_char_candidates(client: BleakClient) -> list:
    """List the characteristics that could carry outgoing frames, best first.

    Firmware revisions disagree on whether that is ``AD01`` or ``AD03``, and
    the UUID alone does not settle it — so return every writable
    characteristic of the vendor service and let the handshake decide.
    """
    service = client.services.get_service(SERVICE_UUID)
    if service is None:
        raise MelittaConnectionError(f"service {SERVICE_UUID} not found on the device")

    _LOGGER.debug(
        "Vendor service characteristics: %s",
        ", ".join(
            f"{char.uuid} {sorted(char.properties)}" for char in service.characteristics
        ),
    )

    available = {char.uuid.lower(): char for char in service.characteristics}
    ordered: list = []

    for candidate in CHAR_WRITE_UUID_CANDIDATES:
        char = available.get(candidate.lower())
        if char is not None and _is_writable(char):
            ordered.append(char)

    for char in service.characteristics:
        if _is_writable(char) and char not in ordered:
            ordered.append(char)

    if not ordered:
        raise MelittaConnectionError("no writable characteristic in the vendor service")
    return ordered


def _is_writable(char) -> bool:
    """True when the characteristic accepts writes."""
    return bool({"write", "write-without-response"} & set(char.properties))
