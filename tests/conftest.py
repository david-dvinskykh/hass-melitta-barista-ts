"""Shared fixtures for the Melitta Barista TS Smart tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from custom_components.melitta_barista_ts.const import SERVICE_UUID

pytest_plugins = "pytest_homeassistant_custom_component"

ADDRESS = "AA:BB:CC:DD:EE:FF"
LOCAL_NAME = "860400E250429374203-"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components."""
    return


def make_service_info(
    *,
    address: str = ADDRESS,
    name: str = LOCAL_NAME,
    service_uuids: list[str] | None = None,
) -> BluetoothServiceInfoBleak:
    """Build a Bluetooth discovery payload for the machine."""
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={},
        service_uuids=[SERVICE_UUID] if service_uuids is None else service_uuids,
        tx_power=-127,
        rssi=-60,
        platform_data=(),
    )
    device = BLEDevice(address=address, name=name, details={})

    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=advertisement.manufacturer_data,
        service_data=advertisement.service_data,
        service_uuids=advertisement.service_uuids,
        source="local",
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=0,
        tx_power=-127,
    )


@pytest.fixture
def service_info() -> BluetoothServiceInfoBleak:
    """A discovery payload for a Barista TS Smart."""
    return make_service_info()


@pytest.fixture
def mock_setup_entry():
    """Skip the real setup so config flow tests stay focused."""
    with patch(
        "custom_components.melitta_barista_ts.async_setup_entry", return_value=True
    ) as mocked:
        yield mocked
