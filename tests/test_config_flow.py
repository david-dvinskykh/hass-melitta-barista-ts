"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista_ts.const import DOMAIN

from .conftest import ADDRESS, make_service_info

DISCOVERY_PATH = "homeassistant.components.bluetooth.async_discovered_service_info"
FLOW_DISCOVERY_PATH = (
    "custom_components.melitta_barista_ts.config_flow.async_discovered_service_info"
)


async def test_bluetooth_discovery_creates_entry(
    hass: HomeAssistant, service_info, mock_setup_entry
) -> None:
    """A discovered machine can be confirmed and set up."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Melitta Barista TS Smart"
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_bluetooth_discovery_of_barista_t_names_the_model(
    hass: HomeAssistant, mock_setup_entry
) -> None:
    """The advertised article number distinguishes T from TS."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=make_service_info(name="8301ABCDEF", service_uuids=[]),
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["title"] == "Melitta Barista T Smart"


async def test_bluetooth_discovery_rejects_other_devices(
    hass: HomeAssistant,
) -> None:
    """Something advertising neither the service nor a known name is ignored."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=make_service_info(name="Some Speaker", service_uuids=[]),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_of_configured_device_aborts(
    hass: HomeAssistant, service_info
) -> None:
    """A machine that is already set up is not offered again."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_lists_machines_in_range(
    hass: HomeAssistant, service_info, mock_setup_entry
) -> None:
    """Manual setup offers the machines the Bluetooth stack can see."""
    with patch(FLOW_DISCOVERY_PATH, return_value=[service_info]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_user_flow_without_machines_aborts(hass: HomeAssistant) -> None:
    """Nothing in range means nothing to add."""
    with patch(FLOW_DISCOVERY_PATH, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_flow_skips_configured_machines(
    hass: HomeAssistant, service_info
) -> None:
    """An already configured machine is filtered out of the picker."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}
    ).add_to_hass(hass)

    with patch(FLOW_DISCOVERY_PATH, return_value=[service_info]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
