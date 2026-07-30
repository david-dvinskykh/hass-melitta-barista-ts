"""Config and options flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from habluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista_ts.client import (
    MachineInfo,
    MelittaConnectionError,
)
from custom_components.melitta_barista_ts.const import (
    CONF_AUTO_CONFIRM,
    CONF_COUNTER_INTERVAL,
    CONF_POLL_INTERVAL,
    DOMAIN,
    SERVICE_UUID,
    MachineType,
)

from .conftest import ADDRESS


def service_info(
    name: str = "8604A1B2C3", address: str = ADDRESS
) -> BluetoothServiceInfoBleak:
    """Build a discovery result for a machine."""
    device = BLEDevice(address, name, {})
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={},
        service_uuids=[SERVICE_UUID],
        tx_power=-127,
        rssi=-60,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[SERVICE_UUID],
        source="local",
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=0,
        tx_power=-127,
    )


@pytest.fixture
def mock_validation():
    """Patch the client used to verify a machine during the flow."""
    with patch(
        "custom_components.melitta_barista_ts.config_flow.MelittaClient", autospec=True
    ) as client_class:
        client = client_class.return_value
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.info = MachineInfo(machine_type=MachineType.BARISTA_TS)
        yield client


@pytest.fixture
def mock_setup_entry():
    """Skip the real entry setup once the flow finishes."""
    with patch(
        "custom_components.melitta_barista_ts.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def test_bluetooth_discovery(
    hass: HomeAssistant, mock_validation, mock_setup_entry
) -> None:
    """A discovered machine is confirmed and created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Melitta Barista TS Smart"
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == ADDRESS


async def test_bluetooth_discovery_ignores_other_devices(hass: HomeAssistant) -> None:
    """A device advertising the service but with a foreign name is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(name="SomeOtherKettle"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_aborts_when_configured(
    hass: HomeAssistant, mock_validation
) -> None:
    """A machine that is already set up is not offered again."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_retry_after_a_failed_connection(
    hass: HomeAssistant, mock_validation, mock_setup_entry
) -> None:
    """A machine that was busy can be retried from the same form."""
    mock_validation.connect.side_effect = MelittaConnectionError("busy")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_validation.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_lists_machines_in_range(
    hass: HomeAssistant, mock_validation, mock_setup_entry
) -> None:
    """The manual flow offers the machines Home Assistant can see."""
    with patch(
        "custom_components.melitta_barista_ts.config_flow"
        ".async_discovered_service_info",
        return_value=[
            service_info(),
            service_info(name="NotAMachine", address="11:22:33:44:55:66"),
        ],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_user_flow_without_machines(hass: HomeAssistant) -> None:
    """Nothing in range is a clear abort, not an empty picker."""
    with patch(
        "custom_components.melitta_barista_ts.config_flow"
        ".async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_flow_skips_configured_machines(hass: HomeAssistant) -> None:
    """Already configured machines are filtered out of the picker."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}
    ).add_to_hass(hass)

    with patch(
        "custom_components.melitta_barista_ts.config_flow"
        ".async_discovered_service_info",
        return_value=[service_info()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_options_flow(hass: HomeAssistant, mock_setup_entry) -> None:
    """Options are shown with defaults and saved."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POLL_INTERVAL: 10,
            CONF_COUNTER_INTERVAL: 0,
            CONF_AUTO_CONFIRM: True,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_POLL_INTERVAL: 10,
        CONF_COUNTER_INTERVAL: 0,
        CONF_AUTO_CONFIRM: True,
    }


async def test_options_flow_rejects_an_absurd_interval(
    hass: HomeAssistant, mock_setup_entry
) -> None:
    """The poll interval is bounded."""
    import voluptuous as vol

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_POLL_INTERVAL: 1,
                CONF_COUNTER_INTERVAL: 600,
                CONF_AUTO_CONFIRM: False,
            },
        )
