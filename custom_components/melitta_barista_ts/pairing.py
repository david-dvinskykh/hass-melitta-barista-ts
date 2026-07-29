"""Optional BlueZ pairing agent.

The Barista Smart bonds with Numeric Comparison rather than Just Works. BlueZ
will only complete that if some agent answers ``RequestConfirmation``, and the
default agent Home Assistant runs with (``NoInputNoOutput``) does not, so
pairing fails with ``Operation not permitted``.

Registering a ``DisplayYesNo`` agent that auto-confirms fixes it. This is
best-effort: it only applies to a local BlueZ adapter, and every failure path
degrades to "no agent registered" rather than breaking setup. Setups using an
ESPHome Bluetooth proxy do their bonding on the proxy and never need this.

.. note::
   ``from __future__ import annotations`` must not be used here — dbus-fast
   reads the annotations at class creation time to derive D-Bus signatures.
"""

import asyncio
import contextlib
import logging

_LOGGER = logging.getLogger(__name__)

AGENT_PATH = "/org/homeassistant/melitta_barista_ts/agent"
AGENT_CAPABILITY = "DisplayYesNo"

_lock = asyncio.Lock()
_registered = False
_bus = None


def _import_dbus():
    """Import dbus-fast, returning ``None`` when it is unavailable."""
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
        from dbus_fast.service import ServiceInterface, method

        return BusType, MessageBus, ServiceInterface, method
    except ImportError:
        _LOGGER.debug("dbus-fast not available, skipping pairing agent")
        return None


def _build_agent_class(ServiceInterface, method):
    """Create the Agent1 implementation bound to the imported dbus-fast."""

    class AutoConfirmAgent(ServiceInterface):
        """Agent1 that accepts every Numeric Comparison request."""

        def __init__(self):
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self):
            _LOGGER.debug("Pairing agent released")

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821
            _LOGGER.debug("Auto-confirming passkey %s for %s", passkey, device)

        @method()
        def RequestAuthorization(self, device: "o"):  # noqa: F821
            _LOGGER.debug("Authorising %s", device)

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821
            _LOGGER.debug("Authorising service %s on %s", uuid, device)

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: F821
            _LOGGER.debug("Passkey requested for %s", device)
            return 0

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: F821
            _LOGGER.debug("PIN requested for %s", device)
            return "0000"

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821
            _LOGGER.debug("Passkey %s displayed for %s", passkey, device)

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821
            _LOGGER.debug("PIN %s displayed for %s", pincode, device)

        @method()
        def Cancel(self):
            _LOGGER.debug("Pairing cancelled")

    return AutoConfirmAgent


async def async_ensure_agent() -> bool:
    """Register the auto-confirming agent once per Home Assistant process.

    Returns ``True`` when an agent is registered and usable. A ``False``
    result is not fatal — it just means bonding has to be arranged some other
    way (``bluetoothctl``, or a Bluetooth proxy).
    """
    global _registered, _bus

    async with _lock:
        if _registered:
            return True

        imported = _import_dbus()
        if imported is None:
            return False
        BusType, MessageBus, ServiceInterface, method = imported

        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as err:
            _LOGGER.debug("Cannot reach the system bus: %s", err)
            return False

        try:
            agent = _build_agent_class(ServiceInterface, method)()
            bus.export(AGENT_PATH, agent)

            introspection = await bus.introspect("org.bluez", "/org/bluez")
            proxy = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
            manager = proxy.get_interface("org.bluez.AgentManager1")

            await manager.call_register_agent(AGENT_PATH, AGENT_CAPABILITY)
            with contextlib.suppress(Exception):
                # Losing the race for default agent is fine — BlueZ still
                # routes confirmations to a registered agent.
                await manager.call_request_default_agent(AGENT_PATH)
        except Exception as err:
            _LOGGER.debug("Could not register pairing agent: %s", err)
            with contextlib.suppress(Exception):
                bus.disconnect()
            return False

        _bus = bus
        _registered = True
        _LOGGER.info("Registered %s BlueZ pairing agent", AGENT_CAPABILITY)
        return True


async def async_release_agent() -> None:
    """Unregister the agent and drop the bus connection."""
    global _registered, _bus

    async with _lock:
        if not _registered or _bus is None:
            return
        bus: object | None = _bus
        _registered = False
        _bus = None

        with contextlib.suppress(Exception):
            introspection = await bus.introspect("org.bluez", "/org/bluez")
            proxy = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
            manager = proxy.get_interface("org.bluez.AgentManager1")
            await manager.call_unregister_agent(AGENT_PATH)

        with contextlib.suppress(Exception):
            bus.disconnect()
