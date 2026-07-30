"""Tests for the BLE client's command sequences.

The transport is replaced by a recorder, so these assert the *order* and
*content* of the protocol calls a brew is made of — which is where the machine
is fussiest.
"""

from __future__ import annotations

import pytest

from custom_components.melitta_barista_ts.client import (
    MelittaClient,
    MelittaConnectionError,
)
from custom_components.melitta_barista_ts.const import (
    TEMP_RECIPE_ID,
    TEMP_RECIPE_NAME_ID,
    ComponentProcess,
    MachineProcess,
    Manipulation,
    Recipe,
    Setting,
    direct_key_id,
)
from custom_components.melitta_barista_ts.protocol import (
    MachineStatus,
    ProtocolError,
    RecipeComponent,
)
from custom_components.melitta_barista_ts.protocol import Recipe as WireRecipe


class RecordingProtocol:
    """Stands in for :class:`MelittaProtocol`, recording every call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.stored: dict[int, WireRecipe] = {}
        self.missing: set[int] = set()

    async def read_recipe(self, _write, recipe_id: int) -> WireRecipe:
        self.calls.append(("read_recipe", recipe_id))
        if recipe_id in self.missing:
            raise ProtocolError(f"no recipe {recipe_id}")
        return self.stored.get(
            recipe_id,
            WireRecipe(
                recipe_id=recipe_id,
                recipe_type=13,
                component1=RecipeComponent(process=1, portion=8),
                component2=RecipeComponent(process=2, portion=20),
            ),
        )

    async def write_recipe(
        self, _write, recipe_id, recipe_type, component1, component2, *, recipe_key=None
    ) -> None:
        self.calls.append(
            ("write_recipe", recipe_id, recipe_type, recipe_key, component1, component2)
        )

    async def write_text(self, _write, register: int, value: str) -> None:
        self.calls.append(("write_text", register, value))

    async def start_process(
        self, _write, process: int, *, two_cups: bool = False
    ) -> None:
        self.calls.append(("start_process", process, two_cups))

    async def cancel_process(self, _write, process: int) -> None:
        self.calls.append(("cancel_process", process))

    async def confirm_prompt(self, _write) -> None:
        self.calls.append(("confirm_prompt",))

    async def read_number(self, _write, register: int) -> int:
        self.calls.append(("read_number", register))
        return 3

    async def write_number(self, _write, register: int, value: int) -> None:
        self.calls.append(("write_number", register, value))


@pytest.fixture
def client(monkeypatch) -> MelittaClient:
    """Return a client with a recording protocol and no real BLE underneath."""
    client = MelittaClient("AA:BB:CC:DD:EE:FF")
    client._protocol = RecordingProtocol()

    async def _connected() -> None:
        return None

    monkeypatch.setattr(client, "connect", _connected)
    # Idle and unblocked, so brewing is allowed.
    client._status = MachineStatus.from_payload(b"\x00\x02\x00\x00\x00\x00\x00\x00")
    return client


def calls(client: MelittaClient) -> list[tuple]:
    return client._protocol.calls


@pytest.mark.asyncio
async def test_brew_stages_the_recipe_then_starts_the_process(client):
    await client.async_brew(Recipe.CAPPUCCINO)

    names = [call[0] for call in calls(client)]
    assert names == ["read_recipe", "write_recipe", "write_text", "start_process"]

    assert calls(client)[0] == ("read_recipe", int(Recipe.CAPPUCCINO))

    _, recipe_id, recipe_type, recipe_key, component1, component2 = calls(client)[1]
    assert recipe_id == TEMP_RECIPE_ID
    assert recipe_type == 13  # cappuccino
    assert recipe_key == 2  # cappuccino family
    assert component1.portion_ml == 40
    assert component2.portion_ml == 100

    assert calls(client)[2] == ("write_text", TEMP_RECIPE_NAME_ID, "Cappuccino")
    assert calls(client)[3] == ("start_process", MachineProcess.PRODUCT, False)


@pytest.mark.asyncio
async def test_brew_two_cups_passes_the_flag(client):
    await client.async_brew(Recipe.ESPRESSO, two_cups=True)
    assert calls(client)[-1] == ("start_process", MachineProcess.PRODUCT, True)


@pytest.mark.asyncio
async def test_brew_uses_the_active_profile_slot(client):
    client.active_profile = 2
    await client.async_brew(Recipe.CAPPUCCINO)

    expected = direct_key_id(2, 2)  # profile 2, cappuccino family
    assert calls(client)[0] == ("read_recipe", expected)


@pytest.mark.asyncio
async def test_brew_falls_back_when_the_profile_has_no_stored_recipe(client):
    client.active_profile = 3
    client._protocol.missing.add(direct_key_id(3, 2))

    await client.async_brew(Recipe.CAPPUCCINO)

    read_ids = [call[1] for call in calls(client) if call[0] == "read_recipe"]
    assert read_ids == [direct_key_id(3, 2), int(Recipe.CAPPUCCINO)]
    assert calls(client)[-1][0] == "start_process"


@pytest.mark.asyncio
async def test_brew_propagates_a_failed_default_recipe_read(client):
    client._protocol.missing.add(int(Recipe.ESPRESSO))
    with pytest.raises(ProtocolError):
        await client.async_brew(Recipe.ESPRESSO)


@pytest.mark.asyncio
async def test_brew_refuses_when_the_machine_needs_attention(client):
    client._status = MachineStatus(
        process=MachineProcess.READY, manipulation=Manipulation.FILL_WATER
    )
    with pytest.raises(MelittaConnectionError, match="not ready"):
        await client.async_brew(Recipe.ESPRESSO)
    assert calls(client) == []


@pytest.mark.asyncio
async def test_brew_is_allowed_before_the_first_status_frame(client):
    client._status = None
    await client.async_brew(Recipe.ESPRESSO)
    assert calls(client)[-1][0] == "start_process"


@pytest.mark.asyncio
async def test_brew_custom_without_milk_leaves_the_second_component_empty(client):
    await client.async_brew_custom(
        process=ComponentProcess.COFFEE,
        portion_ml=120,
        intensity=3,
        temperature=2,
        hopper=1,
        name="Morning",
    )

    _, recipe_id, recipe_type, recipe_key, component1, component2 = calls(client)[0]
    assert recipe_id == TEMP_RECIPE_ID
    assert recipe_type == 24  # freestyle
    assert recipe_key == 7  # menu family
    assert component1.process == ComponentProcess.COFFEE
    assert component1.portion_ml == 120
    assert component1.intensity == 3
    assert component1.temperature == 2
    assert component2.process == ComponentProcess.NONE
    assert component2.shots == 0

    assert calls(client)[1] == ("write_text", TEMP_RECIPE_NAME_ID, "Morning")


@pytest.mark.asyncio
async def test_brew_custom_with_milk_adds_a_milk_component(client):
    await client.async_brew_custom(
        process=ComponentProcess.COFFEE,
        portion_ml=40,
        milk_portion_ml=100,
        intensity=2,
        temperature=1,
        hopper=2,
    )

    component2 = calls(client)[0][5]
    assert component2.process == ComponentProcess.MILK
    assert component2.portion_ml == 100
    assert component2.hopper == 2


@pytest.mark.asyncio
async def test_cancel_and_confirm(client):
    await client.async_cancel()
    await client.async_confirm_prompt()
    assert calls(client) == [
        ("cancel_process", MachineProcess.PRODUCT),
        ("confirm_prompt",),
    ]


@pytest.mark.asyncio
async def test_maintenance_starts_the_requested_program(client):
    await client.async_start_maintenance(MachineProcess.DESCALING)
    assert calls(client) == [("start_process", MachineProcess.DESCALING, False)]


@pytest.mark.asyncio
async def test_settings_round_trip(client):
    assert await client.async_read_setting(Setting.WATER_HARDNESS) == 3
    await client.async_write_setting(Setting.WATER_HARDNESS, 2)
    assert calls(client) == [
        ("read_number", Setting.WATER_HARDNESS),
        ("write_number", Setting.WATER_HARDNESS, 2),
    ]


@pytest.mark.asyncio
async def test_read_settings_skips_the_second_hopper_on_the_barista_t(client):
    from custom_components.melitta_barista_ts.const import MachineType

    client._info.machine_type = MachineType.BARISTA_T
    values = await client.async_read_settings()

    assert Setting.AUTO_BEAN_SELECT not in values
    assert Setting.WATER_HARDNESS in values


@pytest.mark.asyncio
async def test_read_counters_includes_the_total_and_each_drink(client):
    counters = await client.async_read_counters()

    assert counters[0] == 3  # total
    assert counters[int(Recipe.ESPRESSO)] == 3
    assert len(counters) == 25  # 24 drinks + total


def test_status_pushes_reach_subscribers():
    client = MelittaClient("AA:BB:CC:DD:EE:FF")
    seen: list[MachineStatus] = []
    client.add_status_callback(seen.append)

    client._on_status(MachineStatus(process=MachineProcess.READY))

    assert len(seen) == 1
    assert client.status is seen[0]
