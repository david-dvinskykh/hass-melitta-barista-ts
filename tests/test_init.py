"""Tests for setup, the coordinator and the entities."""

from __future__ import annotations

from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista_ts.client import MelittaConnectionError
from custom_components.melitta_barista_ts.const import (
    CONF_BRAND_ICON,
    DOMAIN,
    SERVICE_BREW,
    SERVICE_CANCEL,
    CareProgramme,
    DirectKeyCategory,
    Intensity,
    MachineType,
    RecipeId,
    directkey_recipe_id,
    profile_name_id,
)
from custom_components.melitta_barista_ts.coordinator import STALE_AFTER_SECONDS
from custom_components.melitta_barista_ts.protocol import (
    MachineRecipe,
    MachineStatus,
    RecipeComponent,
)

from .conftest import ADDRESS

CLIENT_PATH = "custom_components.melitta_barista_ts.MelittaBleClient"
BLE_DEVICE_PATH = (
    "custom_components.melitta_barista_ts.bluetooth.async_ble_device_from_address"
)

READY = MachineStatus(
    process=2, sub_process=0, info_messages=0, manipulation=0, progress=0
)
BREWING = MachineStatus(
    process=4, sub_process=1, info_messages=0, manipulation=0, progress=25
)
NEEDS_WATER = MachineStatus(
    process=2, sub_process=0, info_messages=0, manipulation=4, progress=0
)

ESPRESSO = MachineRecipe(
    recipe_id=200,
    recipe_type=0,
    component1=RecipeComponent(
        process=1, shots=1, blend=1, intensity=3, aroma=0, temperature=2, portion=8
    ),
    component2=RecipeComponent(process=0, shots=0, blend=0, intensity=0, temperature=2),
)


#: Profile 1 is named, the rest were never given one and read back empty.
PROFILE_NAMES = {1: "Anna"}


def _make_client(status: MachineStatus = READY) -> MagicMock:
    """A stand-in for the BLE client that answers from memory."""

    async def _read_alphanumeric(value_id: int) -> str:
        for profile, name in PROFILE_NAMES.items():
            if profile_name_id(profile) == value_id:
                return name
        return ""

    machine = MagicMock()
    machine.read_status = AsyncMock(return_value=status)
    machine.read_firmware = AsyncMock(return_value="EF_1.00R4__386")
    machine.read_recipe = AsyncMock(return_value=ESPRESSO)
    machine.read_alphanumeric = AsyncMock(side_effect=_read_alphanumeric)
    machine.read_numerical = AsyncMock(return_value=7)
    machine.write_numerical = AsyncMock()
    machine.brew = AsyncMock()
    machine.cancel_process = AsyncMock()

    client = MagicMock()
    client.machine = machine
    client.connected = True
    client.async_connect = AsyncMock()
    client.async_disconnect = AsyncMock()
    client.async_close = AsyncMock()
    client.add_status_listener = MagicMock(return_value=lambda: None)

    async def _run(action, *args, **kwargs):
        return await action(*args, **kwargs)

    client.async_run = AsyncMock(side_effect=_run)
    return client


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS},
        title="Melitta Barista TS Smart",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock):
    with (
        patch(BLE_DEVICE_PATH, return_value=MagicMock()),
        patch(CLIENT_PATH, return_value=client),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _poll_again(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run one more coordinator refresh.

    Setup only connects and reads the status; profiles, settings and cup
    counters are deliberately left to the next poll so Home Assistant does not
    cancel the entry setup while ~35 BLE round trips run.
    """
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_setup_and_unload(hass: HomeAssistant, config_entry) -> None:
    client = _make_client()
    await _setup(hass, config_entry, client)

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, SERVICE_BREW)
    assert hass.services.has_service(DOMAIN, SERVICE_CANCEL)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    client.async_close.assert_awaited()


async def test_setup_retries_when_machine_not_in_range(
    hass: HomeAssistant, config_entry
) -> None:
    with patch(BLE_DEVICE_PATH, return_value=None):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_when_first_connect_fails(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client()
    client.async_connect = AsyncMock(side_effect=MelittaConnectionError("nope"))
    await _setup(hass, config_entry, client)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_status_entities_reflect_the_machine(
    hass: HomeAssistant, config_entry
) -> None:
    await _setup(hass, config_entry, _make_client(BREWING))

    assert hass.states.get("sensor.melitta_barista_ts_smart_status").state == "brewing"
    assert hass.states.get("sensor.melitta_barista_ts_smart_step").state == "grinding"
    assert hass.states.get("sensor.melitta_barista_ts_smart_progress").state == "25"
    assert (
        hass.states.get("binary_sensor.melitta_barista_ts_smart_brewing").state == "on"
    )


async def test_manipulation_maps_to_a_problem_sensor(
    hass: HomeAssistant, config_entry
) -> None:
    await _setup(hass, config_entry, _make_client(NEEDS_WATER))

    state = hass.states.get("binary_sensor.melitta_barista_ts_smart_water_tank_empty")
    assert state.state == "on"
    assert (
        hass.states.get("sensor.melitta_barista_ts_smart_needs_attention").state
        == "fill_water"
    )


async def test_brew_button_is_unavailable_while_busy(
    hass: HomeAssistant, config_entry
) -> None:
    await _setup(hass, config_entry, _make_client(BREWING))

    assert (
        hass.states.get("button.melitta_barista_ts_smart_brew").state
        == STATE_UNAVAILABLE
    )
    assert (
        hass.states.get("button.melitta_barista_ts_smart_cancel").state
        != STATE_UNAVAILABLE
    )


async def test_brew_button_starts_the_selected_drink(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client()
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_brew"},
        blocking=True,
    )

    client.machine.brew.assert_awaited_once()
    args, kwargs = client.machine.brew.await_args
    assert args[0] == int(RecipeId.ESPRESSO)
    assert kwargs["two_cups"] is False


async def test_selecting_a_drink_loads_its_stored_parameters(
    hass: HomeAssistant, config_entry
) -> None:
    """Picking a drink seeds strength, temperature and cup size from the machine."""
    client = _make_client()
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.melitta_barista_ts_smart_drink",
            "option": "cappuccino",
        },
        blocking=True,
    )

    client.machine.read_recipe.assert_awaited_with(int(RecipeId.CAPPUCCINO))
    assert hass.states.get("select.melitta_barista_ts_smart_strength").state == "strong"
    assert (
        hass.states.get("select.melitta_barista_ts_smart_brew_temperature").state
        == "high"
    )
    assert hass.states.get("number.melitta_barista_ts_smart_cup_size").state == "40.0"


async def test_staged_settings_are_applied_to_the_brew(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client()
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.melitta_barista_ts_smart_strength",
            "option": "very_mild",
        },
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.melitta_barista_ts_smart_cup_size", "value": 120},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.melitta_barista_ts_smart_two_cups"},
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_brew"},
        blocking=True,
    )

    _, kwargs = client.machine.brew.await_args
    assert kwargs["intensity"] == int(Intensity.VERY_MILD)
    assert kwargs["portion_ml"] == 120
    assert kwargs["two_cups"] is True


async def test_brew_service_overrides_staged_settings(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client()
    await _setup(hass, config_entry, client)

    device_registry_entry = next(
        iter(
            entity
            for entity in hass.states.async_entity_ids("button")
            if "brew" in entity
        )
    )
    assert device_registry_entry

    await hass.services.async_call(
        DOMAIN,
        SERVICE_BREW,
        {
            "entity_id": "button.melitta_barista_ts_smart_brew",
            "drink": "flat_white",
            "intensity": "very_strong",
            "portion_ml": 200,
        },
        blocking=True,
    )

    _, kwargs = client.machine.brew.await_args
    args, _ = client.machine.brew.await_args
    assert args[0] == int(RecipeId.FLAT_WHITE)
    assert kwargs["intensity"] == int(Intensity.VERY_STRONG)
    assert kwargs["portion_ml"] == 200


async def test_cancel_service_stops_the_current_process(
    hass: HomeAssistant, config_entry
) -> None:
    client = _make_client(BREWING)
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CANCEL,
        {"entity_id": "button.melitta_barista_ts_smart_cancel"},
        blocking=True,
    )

    client.machine.cancel_process.assert_awaited_once_with(4)


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------


async def test_profile_options_come_from_the_machine(
    hass: HomeAssistant, config_entry
) -> None:
    """Named profiles show their name; unnamed ones get a placeholder."""
    await _setup(hass, config_entry, _make_client())
    await _poll_again(hass, config_entry)

    state = hass.states.get("select.melitta_barista_ts_smart_profile")
    assert state.state == "My Coffee"
    assert state.attributes["options"][:3] == ["My Coffee", "Anna", "Profile 2"]


async def test_profile_count_follows_the_model(
    hass: HomeAssistant, config_entry
) -> None:
    """A Barista T offers four user profiles, the TS eight."""
    client = _make_client()
    client.machine.read_numerical = AsyncMock(return_value=int(MachineType.BARISTA_T))
    await _setup(hass, config_entry, client)
    await _poll_again(hass, config_entry)

    options = hass.states.get("select.melitta_barista_ts_smart_profile").attributes[
        "options"
    ]
    assert len(options) == 5  # My Coffee + four user profiles


async def test_selecting_a_profile_reads_its_direct_key(
    hass: HomeAssistant, config_entry
) -> None:
    """Switching profile reloads the currently selected direct key from it."""
    client = _make_client()
    await _setup(hass, config_entry, client)
    await _poll_again(hass, config_entry)

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.melitta_barista_ts_smart_profile", "option": "Anna"},
        blocking=True,
    )

    client.machine.read_recipe.assert_awaited_with(
        directkey_recipe_id(1, DirectKeyCategory.ESPRESSO)
    )


async def test_profile_brew_button_uses_the_direct_key_slot(
    hass: HomeAssistant, config_entry
) -> None:
    """Brewing from a profile targets that profile's direct-key recipe."""
    client = _make_client()
    await _setup(hass, config_entry, client)
    await _poll_again(hass, config_entry)

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.melitta_barista_ts_smart_profile", "option": "Anna"},
        blocking=True,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.melitta_barista_ts_smart_profile_drink",
            "option": "cappuccino",
        },
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_brew_from_profile"},
        blocking=True,
    )

    args, kwargs = client.machine.brew.await_args
    assert args[0] == directkey_recipe_id(1, DirectKeyCategory.CAPPUCCINO)
    assert kwargs["name"] == "Cappuccino"


async def test_brew_service_accepts_a_profile(
    hass: HomeAssistant, config_entry
) -> None:
    """The service can target a profile without touching the selectors."""
    client = _make_client()
    await _setup(hass, config_entry, client)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_BREW,
        {
            "entity_id": "button.melitta_barista_ts_smart_brew",
            "profile": 3,
            "drink": "latte_macchiato",
        },
        blocking=True,
    )

    args, _ = client.machine.brew.await_args
    assert args[0] == directkey_recipe_id(3, DirectKeyCategory.LATTE_MACCHIATO)
    # The selectors are untouched by a one-off service call.
    profile = hass.states.get("select.melitta_barista_ts_smart_profile")
    assert profile.state == "My Coffee"


async def test_brew_service_rejects_a_drink_no_profile_stores(
    hass: HomeAssistant, config_entry
) -> None:
    """Profiles hold seven direct keys, not the whole 24-drink menu."""
    client = _make_client()
    await _setup(hass, config_entry, client)

    with pytest.raises(ServiceValidationError, match="not stored in a profile"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_BREW,
            {
                "entity_id": "button.melitta_barista_ts_smart_brew",
                "profile": 1,
                "drink": "flat_white",
            },
            blocking=True,
        )

    client.machine.brew.assert_not_awaited()


async def test_brew_service_rejects_a_profile_the_machine_lacks(
    hass: HomeAssistant, config_entry
) -> None:
    """A Barista T has four user profiles, so profile 7 does not exist."""
    client = _make_client()
    client.machine.read_numerical = AsyncMock(return_value=int(MachineType.BARISTA_T))
    await _setup(hass, config_entry, client)
    await _poll_again(hass, config_entry)

    with pytest.raises(HomeAssistantError, match="no profile 7"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_BREW,
            {
                "entity_id": "button.melitta_barista_ts_smart_brew",
                "profile": 7,
                "drink": "espresso",
            },
            blocking=True,
        )

    client.machine.brew.assert_not_awaited()


async def test_setup_defers_the_expensive_reads(
    hass: HomeAssistant, config_entry
) -> None:
    """Entry setup must stay cheap — Home Assistant cancels slow setups."""
    client = _make_client()
    await _setup(hass, config_entry, client)

    # Only the machine-type probe; no profile names, no cup counters.
    assert client.machine.read_alphanumeric.await_count == 0
    assert client.machine.read_numerical.await_count == 1

    await _poll_again(hass, config_entry)

    assert client.machine.read_alphanumeric.await_count == 8
    assert client.machine.read_numerical.await_count > 25


async def test_direct_key_buttons_mirror_the_front_panel(
    hass: HomeAssistant, config_entry
) -> None:
    """One button per direct-select key the machine has."""
    await _setup(hass, config_entry, _make_client())
    await _poll_again(hass, config_entry)

    keys = [
        entity_id
        for entity_id in hass.states.async_entity_ids("button")
        if entity_id.endswith("_key")
    ]
    assert len(keys) == len(DirectKeyCategory)


async def test_direct_key_button_brews_the_stored_recipe(
    hass: HomeAssistant, config_entry
) -> None:
    """Pressing a key makes what the profile stores, ignoring staged settings.

    A key on the front panel does not know about Home Assistant's staged
    strength or cup size, so neither should its button.
    """
    client = _make_client()
    await _setup(hass, config_entry, client)
    await _poll_again(hass, config_entry)

    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.melitta_barista_ts_smart_strength",
            "option": "very_mild",
        },
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_cappuccino_key"},
        blocking=True,
    )

    args, kwargs = client.machine.brew.await_args
    assert args[0] == directkey_recipe_id(0, DirectKeyCategory.CAPPUCCINO)
    assert kwargs["name"] == "Cappuccino"
    # No overrides at all — the machine's own recipe values stand.
    assert kwargs.keys() == {"name"}


async def test_direct_key_button_follows_the_selected_profile(
    hass: HomeAssistant, config_entry
) -> None:
    """The keys act on whichever profile is selected, like the machine does."""
    client = _make_client()
    await _setup(hass, config_entry, client)
    await _poll_again(hass, config_entry)

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.melitta_barista_ts_smart_profile", "option": "Anna"},
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.melitta_barista_ts_smart_espresso_key"},
        blocking=True,
    )

    args, _ = client.machine.brew.await_args
    assert args[0] == directkey_recipe_id(1, DirectKeyCategory.ESPRESSO)


async def test_care_tallies_reach_their_sensors(hass: HomeAssistant, config_entry):
    """Each care tally comes from its own register, none from a neighbour's."""
    care = {
        CareProgramme.COFFEE_SYSTEM_CLEANING: 25,
        CareProgramme.DESCALING: 24,
        CareProgramme.FILTER_CHANGE: 1,
        CareProgramme.MILK_SYSTEM_CLEANING: 40,
    }

    async def _read_numerical(value_id: int) -> int:
        return care.get(value_id, 7)

    client = _make_client()
    client.machine.read_numerical = AsyncMock(side_effect=_read_numerical)
    await _setup(hass, config_entry, client)
    await _poll_again(hass, config_entry)

    machine_name = "melitta_barista_ts_smart"
    assert (
        hass.states.get(f"sensor.{machine_name}_coffee_system_cleanings").state == "25"
    )
    assert hass.states.get(f"sensor.{machine_name}_descalings").state == "24"
    assert hass.states.get(f"sensor.{machine_name}_filter_changes").state == "1"
    assert hass.states.get(f"sensor.{machine_name}_milk_system_cleanings").state == "40"


async def test_one_failed_poll_does_not_grey_out_the_dashboard(
    hass: HomeAssistant, config_entry
) -> None:
    """The machine drops the BLE link routinely; that is not a fault.

    Entities that follow the last poll alone flicker between their value and
    unavailable every time the link has to be re-established.
    """
    client = _make_client()
    await _setup(hass, config_entry, client)

    client.async_connect = AsyncMock(side_effect=MelittaConnectionError("dropped"))
    await _poll_again(hass, config_entry)

    assert not config_entry.runtime_data.last_update_success
    assert hass.states.get("sensor.melitta_barista_ts_smart_status").state == "ready"


async def test_a_machine_that_stays_away_does_go_unavailable(
    hass: HomeAssistant, config_entry
) -> None:
    """Holding the last reading for ever would be worse than saying nothing."""
    client = _make_client()
    await _setup(hass, config_entry, client)

    client.async_connect = AsyncMock(side_effect=MelittaConnectionError("switched off"))
    with patch(
        "custom_components.melitta_barista_ts.coordinator.monotonic",
        return_value=monotonic() + STALE_AFTER_SECONDS + 1,
    ):
        await _poll_again(hass, config_entry)

    assert (
        hass.states.get("sensor.melitta_barista_ts_smart_status").state
        == STATE_UNAVAILABLE
    )


async def test_the_logo_is_served_only_when_asked_for(
    hass: HomeAssistant, config_entry
) -> None:
    """Patching someone else's page is opt-in, and reversible."""
    with patch("custom_components.melitta_barista_ts.async_serve_brand") as serve:
        await _setup(hass, config_entry, _make_client())
    serve.assert_not_called()

    hass.config_entries.async_update_entry(
        config_entry, options={CONF_BRAND_ICON: True}
    )
    with (
        patch(BLE_DEVICE_PATH, return_value=MagicMock()),
        patch(CLIENT_PATH, return_value=_make_client()),
        patch("custom_components.melitta_barista_ts.async_serve_brand") as serve,
        patch("custom_components.melitta_barista_ts.async_stop_serving_brand") as stop,
    ):
        await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()
        serve.assert_awaited_once()

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()
        stop.assert_called_once()
