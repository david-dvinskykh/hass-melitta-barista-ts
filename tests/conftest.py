"""Test fixtures for the Melitta Barista TS integration.

``const`` and ``protocol`` are deliberately free of Home Assistant imports so
the wire protocol can be tested on its own. When Home Assistant is not
installed, importing ``custom_components.melitta_barista_ts`` would still fail
because the package ``__init__`` pulls in Home Assistant, so a stand-in package
object is registered that exposes the submodules without executing it — and the
Home Assistant fixtures below are skipped along with the tests that need them.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HAS_HOMEASSISTANT = importlib.util.find_spec("homeassistant") is not None

#: Address every test machine uses.
ADDRESS = "AA:BB:CC:DD:EE:FF"


def _register_stub_packages() -> None:
    """Expose the integration's submodules without running its ``__init__``."""
    for name, path in (
        ("custom_components", ROOT / "custom_components"),
        (
            "custom_components.melitta_barista_ts",
            ROOT / "custom_components" / "melitta_barista_ts",
        ),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [str(path)]  # type: ignore[attr-defined]
        sys.modules[name] = module


if not HAS_HOMEASSISTANT:
    _register_stub_packages()


if HAS_HOMEASSISTANT:
    from custom_components.melitta_barista_ts.client import MachineInfo
    from custom_components.melitta_barista_ts.const import (
        MachineProcess,
        MachineType,
        Manipulation,
        Recipe,
        Setting,
    )
    from custom_components.melitta_barista_ts.protocol import MachineStatus

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Load the integration from ``custom_components`` in every test."""
        return enable_custom_integrations

    @pytest.fixture
    def machine_info() -> MachineInfo:
        """Machine information as read on connect."""
        return MachineInfo(
            machine_type=MachineType.BARISTA_TS,
            firmware="1.10.5",
            serial="SN12345678",
            profile_names={1: "David", 2: "Guest"},
        )

    @pytest.fixture
    def status() -> MachineStatus:
        """Return the status of a ready machine."""
        return MachineStatus(
            process=MachineProcess.READY,
            sub_process=None,
            manipulation=Manipulation.NONE,
            progress=0,
        )

    @pytest.fixture
    def mock_client(machine_info, status):
        """Patch the BLE client so no Bluetooth is touched."""
        with patch(
            "custom_components.melitta_barista_ts.MelittaClient", autospec=True
        ) as client_class:
            client = client_class.return_value
            client.address = ADDRESS
            client.connected = True
            client.info = machine_info
            client.status = status
            client.active_profile = 0
            client.selected_recipe = Recipe.ESPRESSO
            client.profile_count = 9
            client.async_update_status = AsyncMock(return_value=status)
            client.async_read_settings = AsyncMock(
                return_value={
                    Setting.WATER_HARDNESS: 3,
                    Setting.ENERGY_SAVING: 1,
                    Setting.AUTO_OFF_AFTER: 60,
                    Setting.AUTO_BEAN_SELECT: 0,
                    Setting.RINSING_OFF: 0,
                    Setting.BREW_TEMPERATURE: 1,
                    Setting.WATER_FILTER: 0,
                    Setting.CLOCK_READ: 8 * 60 + 30,
                }
            )
            client.async_read_counters = AsyncMock(
                return_value={0: 1234, int(Recipe.ESPRESSO): 500}
            )
            client.async_brew = AsyncMock()
            client.async_brew_custom = AsyncMock()
            client.async_cancel = AsyncMock()
            client.async_confirm_prompt = AsyncMock()
            client.async_start_maintenance = AsyncMock()
            client.async_write_setting = AsyncMock()
            client.disconnect = AsyncMock()
            yield client

    @pytest.fixture
    def mock_bluetooth_device():
        """Pretend the machine is in range of a connectable adapter."""
        with (
            patch(
                "custom_components.melitta_barista_ts.bluetooth"
                ".async_ble_device_from_address",
                return_value=object(),
            ),
            patch(
                "custom_components.melitta_barista_ts.coordinator.bluetooth"
                ".async_register_callback",
                return_value=lambda: None,
            ),
            patch(
                "custom_components.melitta_barista_ts.coordinator.bluetooth"
                ".async_ble_device_from_address",
                return_value=object(),
            ),
        ):
            yield
