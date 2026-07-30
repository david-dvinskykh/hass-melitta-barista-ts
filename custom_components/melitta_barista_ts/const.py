"""Constants for the Melitta Barista TS Smart integration.

Every protocol constant in this module was obtained by reverse engineering
the Melitta Connect Android application (Eugster/Frismag OEM BLE stack).
See ``docs/protocol.md`` for the wire-format description.

This module is deliberately free of Home Assistant and bleak imports so
that the protocol layer stays unit-testable in isolation.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag

DOMAIN = "melitta_barista_ts"

# ---------------------------------------------------------------------------
# BLE GATT
# ---------------------------------------------------------------------------

#: Vendor service advertised by the machine — used for HA bluetooth discovery.
SERVICE_UUID = "0000ad00-b35c-11e4-9813-0002a5d5c51b"
#: Machine -> app notifications (status frames, command responses).
CHAR_NOTIFY = "0000ad02-b35c-11e4-9813-0002a5d5c51b"
#: App -> machine writes (write-without-response, 20-byte chunks).
CHAR_WRITE = "0000ad03-b35c-11e4-9813-0002a5d5c51b"

#: The machine's GATT MTU is not negotiated; frames must be chunked by hand.
BLE_MTU = 20

# ---------------------------------------------------------------------------
# Frame format
# ---------------------------------------------------------------------------

FRAME_START = 0x53  # 'S'
FRAME_END = 0x45  # 'E'
#: Longest a partial frame may sit in the receive buffer before it is dropped.
FRAME_ASSEMBLY_TIMEOUT = 1.0
#: How long to wait for a response/ACK frame after writing a command.
DEFAULT_FRAME_TIMEOUT = 5.0
#: Hard cap on the receive buffer, mirroring the vendor app's own limit.
MAX_FRAME_SIZE = 128

# ---------------------------------------------------------------------------
# Opcodes
# ---------------------------------------------------------------------------

CMD_ACK = "A"
CMD_NACK = "N"
CMD_READ_ALPHA = "HA"
CMD_WRITE_ALPHA = "HB"
CMD_READ_RECIPE = "HC"
CMD_RESET_DEFAULT = "HD"
CMD_START_PROCESS = "HE"
CMD_READ_FEATURES = "HI"
CMD_WRITE_RECIPE = "HJ"
CMD_READ_SERIAL = "HL"
CMD_READ_NUMERICAL = "HR"
CMD_HANDSHAKE = "HU"
CMD_READ_VERSION = "HV"
CMD_WRITE_NUMERICAL = "HW"
CMD_READ_STATUS = "HX"
CMD_CONFIRM_PROMPT = "HY"
CMD_CANCEL_PROCESS = "HZ"

#: Frames the machine sends to us: opcode -> (payload length, RC4-encrypted).
#: Frame length is used to detect frame boundaries, because a 0x45 ('E') byte
#: can legitimately occur inside RC4 ciphertext.
INBOUND_FRAMES: dict[str, tuple[int, bool]] = {
    CMD_ACK: (0, False),
    CMD_NACK: (0, False),
    "HA": (66, True),
    "HC": (66, True),
    "HF": (16, True),
    "HI": (10, True),
    "HL": (20, True),
    "HP": (14, True),
    "HQ": (15, True),
    "HR": (6, True),
    "HU": (8, True),
    "HV": (11, True),
    "HX": (8, True),
}

# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------

#: RC4 stream key used for every frame body. The vendor app ships it as an
#: AES-128-CBC blob; ``tests/test_protocol.py::test_rc4_key_matches_aes_blob``
#: re-derives it from the original key material to prove this literal is
#: correct, which keeps ``cryptography`` out of the runtime dependencies.
RC4_KEY = b"MEL_090217_V10_?R4.wozJ!(*q2ds3#"

#: Original AES key material, kept for provenance and for the derivation test.
AES_KEY = bytes(
    b & 0xFF
    for b in (
        # part B
        125,
        57,
        51,
        41,
        121,
        78,
        -30,
        10,
        -62,
        -22,
        -27,
        -19,
        -89,
        -85,
        3,
        40,
        -12,
        # part A
        99,
        -127,
        119,
        125,
        118,
        101,
        -102,
        -108,
        -39,
        100,
        -61,
        -117,
        -95,
        -65,
        -14,
    )
)
AES_IV = bytes(
    b & 0xFF
    for b in (
        -72,
        -1,
        -122,
        -122,
        64,
        -10,
        12,
        -118,
        25,
        69,
        -117,
        -123,
        58,
        -99,
        93,
        -2,
    )
)
ENCRYPTED_RC4_KEY = bytes(
    b & 0xFF
    for b in (
        -81,
        -14,
        21,
        -30,
        26,
        60,
        54,
        -89,
        11,
        -42,
        95,
        -65,
        125,
        -6,
        -99,
        -111,
        65,
        -16,
        14,
        36,
        -126,
        -40,
        13,
        -28,
        15,
        114,
        -48,
        48,
        -28,
        -9,
        -87,
        63,
        72,
        122,
        -75,
        57,
        -13,
        101,
        23,
        -7,
        123,
        -9,
        -66,
        -30,
        -87,
        5,
        -113,
        -47,
    )
)

#: 256-byte substitution table backing the HU handshake verifier.
HU_TABLE = bytes(
    b & 0xFF
    for b in (
        98,
        6,
        85,
        -106,
        36,
        23,
        112,
        -92,
        -121,
        -49,
        -87,
        5,
        26,
        64,
        -91,
        -37,
        61,
        20,
        68,
        89,
        -126,
        63,
        52,
        102,
        24,
        -27,
        -124,
        -11,
        80,
        -40,
        -61,
        115,
        90,
        -88,
        -100,
        -53,
        -79,
        120,
        2,
        -66,
        -68,
        7,
        100,
        -71,
        -82,
        -13,
        -94,
        10,
        -19,
        18,
        -3,
        -31,
        8,
        -48,
        -84,
        -12,
        -1,
        126,
        101,
        79,
        -111,
        -21,
        -28,
        121,
        123,
        -5,
        67,
        -6,
        -95,
        0,
        107,
        97,
        -15,
        111,
        -75,
        82,
        -7,
        33,
        69,
        55,
        59,
        -103,
        29,
        9,
        -43,
        -89,
        84,
        93,
        30,
        46,
        94,
        75,
        -105,
        114,
        73,
        -34,
        -59,
        96,
        -46,
        45,
        16,
        -29,
        -8,
        -54,
        51,
        -104,
        -4,
        125,
        81,
        -50,
        -41,
        -70,
        39,
        -98,
        -78,
        -69,
        -125,
        -120,
        1,
        49,
        50,
        17,
        -115,
        91,
        47,
        -127,
        60,
        99,
        -102,
        35,
        86,
        -85,
        105,
        34,
        38,
        -56,
        -109,
        58,
        77,
        118,
        -83,
        -10,
        76,
        -2,
        -123,
        -24,
        -60,
        -112,
        -58,
        124,
        53,
        4,
        108,
        74,
        -33,
        -22,
        -122,
        -26,
        -99,
        -117,
        -67,
        -51,
        -57,
        -128,
        -80,
        19,
        -45,
        -20,
        127,
        -64,
        -25,
        70,
        -23,
        88,
        -110,
        44,
        -73,
        -55,
        22,
        83,
        13,
        -42,
        116,
        109,
        -97,
        32,
        95,
        -30,
        -116,
        -36,
        57,
        12,
        -35,
        31,
        -47,
        -74,
        -113,
        92,
        -107,
        -72,
        -108,
        62,
        113,
        65,
        37,
        27,
        106,
        -90,
        3,
        14,
        -52,
        72,
        21,
        41,
        56,
        66,
        28,
        -63,
        40,
        -39,
        25,
        54,
        -77,
        117,
        -18,
        87,
        -16,
        -101,
        -76,
        -86,
        -14,
        -44,
        -65,
        -93,
        78,
        -38,
        -119,
        -62,
        -81,
        110,
        43,
        119,
        -32,
        71,
        122,
        -114,
        42,
        -96,
        104,
        48,
        -9,
        103,
        15,
        11,
        -118,
        -17,
    )
)

# ---------------------------------------------------------------------------
# Machine identification
# ---------------------------------------------------------------------------


class MachineType(IntEnum):
    """Machine model, read from numerical register 6."""

    BARISTA_T = 258
    BARISTA_TS = 259


MACHINE_TYPE_REGISTER = 6

MODEL_NAMES: dict[MachineType, str] = {
    MachineType.BARISTA_T: "Barista T Smart",
    MachineType.BARISTA_TS: "Barista TS Smart",
}

#: BLE local-name prefix -> model. The advertised name is the four-digit
#: article-number prefix followed by a hex device id, e.g. ``8604A1B2C3``.
NAME_PREFIXES: dict[str, MachineType] = {
    "8301": MachineType.BARISTA_T,
    "8311": MachineType.BARISTA_T,
    "8401": MachineType.BARISTA_T,
    "8501": MachineType.BARISTA_TS,
    "8601": MachineType.BARISTA_TS,
    "8604": MachineType.BARISTA_TS,
}

MANUFACTURER = "Melitta"


def machine_type_from_name(name: str | None) -> MachineType | None:
    """Return the model implied by a BLE local name, if recognised."""
    if not name or len(name) < 5:
        return None
    return NAME_PREFIXES.get(name[:4])


def is_supported_name(name: str | None) -> bool:
    """Whether a BLE local name looks like a Barista T/TS Smart."""
    if not name or len(name) < 5:
        return False
    return name[:4] in NAME_PREFIXES and all(
        c in "0123456789abcdefABCDEF" for c in name[4:5]
    )


# ---------------------------------------------------------------------------
# Machine status
# ---------------------------------------------------------------------------


class MachineProcess(IntEnum):
    """Top-level machine process, from the HX status frame."""

    READY = 2
    PRODUCT = 4
    CLEANING = 9
    DESCALING = 10
    FILTER_INSERT = 11
    FILTER_REPLACE = 12
    FILTER_REMOVE = 13
    SWITCH_OFF = 16
    EASY_CLEAN = 17
    INTENSIVE_CLEAN = 19
    EVAPORATING = 20
    BUSY = 99


class SubProcess(IntEnum):
    """Current step inside a running preparation."""

    GRINDING = 1
    COFFEE = 2
    STEAM = 3
    WATER = 4
    PREPARE = 5


class InfoMessage(IntFlag):
    """Non-blocking machine notices (bitfield)."""

    FILL_BEANS_1 = 1 << 0
    FILL_BEANS_2 = 1 << 1
    EASY_CLEAN = 1 << 2
    POWDER_FILLED = 1 << 3
    PREPARATION_CANCELLED = 1 << 4


class Manipulation(IntEnum):
    """Machine state that requires the user to do something."""

    NONE = 0
    BREW_UNIT_REMOVED = 1
    TRAYS_MISSING = 2
    EMPTY_TRAYS = 3
    FILL_WATER = 4
    CLOSE_POWDER_LID = 5
    FILL_POWDER = 6
    MOVE_CUP_TO_FROTHER = 11
    FLUSH_REQUIRED = 20


#: Manipulations that the machine expects to be acknowledged over BLE (HY)
#: rather than resolved by physically touching the machine.
CONFIRMABLE_MANIPULATIONS: frozenset[Manipulation] = frozenset(
    {Manipulation.MOVE_CUP_TO_FROTHER, Manipulation.FLUSH_REQUIRED}
)

# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


class Recipe(IntEnum):
    """Built-in recipe ids (register ids for HC reads)."""

    ESPRESSO = 200
    RISTRETTO = 201
    LUNGO = 202
    ESPRESSO_DOPPIO = 203
    RISTRETTO_DOPPIO = 204
    CAFE_CREME = 205
    CAFE_CREME_DOPPIO = 206
    AMERICANO = 207
    AMERICANO_EXTRA = 208
    LONG_BLACK = 209
    RED_EYE = 210
    BLACK_EYE = 211
    DEAD_EYE = 212
    CAPPUCCINO = 213
    ESPRESSO_MACCHIATO = 214
    CAFFE_LATTE = 215
    CAFE_AU_LAIT = 216
    FLAT_WHITE = 217
    LATTE_MACCHIATO = 218
    LATTE_MACCHIATO_EXTRA = 219
    LATTE_MACCHIATO_TRIPLE = 220
    MILK = 221
    MILK_FROTH = 222
    HOT_WATER = 223


#: Recipe -> slug used as the select option and translation key.
RECIPE_KEYS: dict[Recipe, str] = {recipe: recipe.name.lower() for recipe in Recipe}
RECIPE_BY_KEY: dict[str, Recipe] = {key: recipe for recipe, key in RECIPE_KEYS.items()}

#: Recipes only the dual-hopper Barista TS offers.
TS_ONLY_RECIPES: frozenset[Recipe] = frozenset(
    {Recipe.RED_EYE, Recipe.BLACK_EYE, Recipe.DEAD_EYE}
)

#: Recipe -> ``recipe_type`` byte written back in the HJ payload.
RECIPE_TYPES: dict[Recipe, int] = {
    Recipe.ESPRESSO: 0,
    Recipe.RISTRETTO: 1,
    Recipe.LUNGO: 2,
    Recipe.ESPRESSO_DOPPIO: 3,
    Recipe.RISTRETTO_DOPPIO: 4,
    Recipe.CAFE_CREME: 5,
    Recipe.CAFE_CREME_DOPPIO: 6,
    Recipe.AMERICANO: 7,
    Recipe.AMERICANO_EXTRA: 8,
    Recipe.LONG_BLACK: 9,
    Recipe.RED_EYE: 10,
    Recipe.BLACK_EYE: 11,
    Recipe.DEAD_EYE: 12,
    Recipe.CAPPUCCINO: 13,
    Recipe.ESPRESSO_MACCHIATO: 14,
    Recipe.CAFFE_LATTE: 15,
    Recipe.CAFE_AU_LAIT: 16,
    Recipe.FLAT_WHITE: 17,
    Recipe.LATTE_MACCHIATO: 18,
    Recipe.LATTE_MACCHIATO_EXTRA: 19,
    Recipe.LATTE_MACCHIATO_TRIPLE: 20,
    Recipe.MILK: 21,
    Recipe.MILK_FROTH: 22,
    Recipe.HOT_WATER: 23,
}

#: ``recipe_type`` byte -> ``recipe_key`` (drink family) byte.
RECIPE_TYPE_TO_KEY: dict[int, int] = {
    0: 0,
    1: 0,
    2: 0,
    3: 0,
    4: 0,  # espresso family
    5: 1,
    6: 1,
    7: 1,
    8: 1,
    9: 1,  # café crème family
    10: 1,
    11: 1,
    12: 1,  # red/black/dead eye
    13: 2,
    14: 2,
    15: 2,
    16: 2,
    17: 2,  # cappuccino family
    18: 3,
    19: 3,
    20: 3,  # latte macchiato family
    21: 5,  # milk
    22: 4,  # milk froth
    23: 6,  # hot water
    24: 7,  # freestyle
}

#: Fallback family byte for recipe types we do not know (the "menu" family).
RECIPE_KEY_MENU = 7

FREESTYLE_RECIPE_TYPE = 24

#: Scratch recipe slot. Brewing always writes the recipe to be brewed here
#: first, then starts the process — the machine brews whatever sits in 400.
TEMP_RECIPE_ID = 400
#: Display name shown on the machine for the temp recipe.
TEMP_RECIPE_NAME_ID = 401


def recipe_key_for_type(recipe_type: int) -> int:
    """Return the drink-family byte for a ``recipe_type``."""
    return RECIPE_TYPE_TO_KEY.get(recipe_type, RECIPE_KEY_MENU)


def available_recipes(machine_type: MachineType | None) -> list[Recipe]:
    """Recipes offered by the given model (all of them when unknown)."""
    if machine_type == MachineType.BARISTA_T:
        return [r for r in Recipe if r not in TS_ONLY_RECIPES]
    return list(Recipe)


# ---------------------------------------------------------------------------
# Recipe component fields
# ---------------------------------------------------------------------------


class ComponentProcess(IntEnum):
    """What a recipe component dispenses."""

    NONE = 0
    COFFEE = 1
    MILK = 2
    WATER = 3


class Intensity(IntEnum):
    """Grind/brew strength."""

    VERY_MILD = 0
    MILD = 1
    MEDIUM = 2
    STRONG = 3
    VERY_STRONG = 4


class BrewTemperature(IntEnum):
    """Brew temperature step."""

    LOW = 0
    NORMAL = 1
    HIGH = 2


class Hopper(IntEnum):
    """Bean hopper (Barista TS has two; the T ignores this byte)."""

    AUTO = 0
    HOPPER_1 = 1
    HOPPER_2 = 2


#: Portion is stored in 5 ml steps.
PORTION_STEP_ML = 5
MIN_PORTION_ML = 25
MAX_PORTION_ML = 220

PROCESS_OPTIONS: dict[str, int] = {
    "coffee": ComponentProcess.COFFEE,
    "milk": ComponentProcess.MILK,
    "water": ComponentProcess.WATER,
}
INTENSITY_OPTIONS: dict[str, int] = {
    "very_mild": Intensity.VERY_MILD,
    "mild": Intensity.MILD,
    "medium": Intensity.MEDIUM,
    "strong": Intensity.STRONG,
    "very_strong": Intensity.VERY_STRONG,
}
TEMPERATURE_OPTIONS: dict[str, int] = {
    "low": BrewTemperature.LOW,
    "normal": BrewTemperature.NORMAL,
    "high": BrewTemperature.HIGH,
}
HOPPER_OPTIONS: dict[str, int] = {
    "auto": Hopper.AUTO,
    "hopper_1": Hopper.HOPPER_1,
    "hopper_2": Hopper.HOPPER_2,
}

# ---------------------------------------------------------------------------
# Profiles (per-user recipe sets)
# ---------------------------------------------------------------------------

#: Profile 0 is the machine's built-in "My Coffee" set.
DEFAULT_PROFILE = 0
#: Number of selectable profiles, including profile 0.
PROFILE_COUNTS: dict[MachineType, int] = {
    MachineType.BARISTA_T: 5,
    MachineType.BARISTA_TS: 9,
}

#: Register holding the display name of user profile N (1-based).
PROFILE_NAME_REGISTERS: dict[int, int] = {n: 310 + (n - 1) * 10 for n in range(1, 9)}


def profile_count(machine_type: MachineType | None) -> int:
    """Return the number of selectable profiles for a model."""
    if machine_type is None:
        return PROFILE_COUNTS[MachineType.BARISTA_TS]
    return PROFILE_COUNTS.get(machine_type, PROFILE_COUNTS[MachineType.BARISTA_T])


class DrinkCategory(IntEnum):
    """Drink families a profile can override ("direct key" slots)."""

    ESPRESSO = 0
    CAFE_CREME = 1
    CAPPUCCINO = 2
    LATTE_MACCHIATO = 3
    MILK_FROTH = 4
    MILK = 5
    WATER = 6


#: Per-profile recipe slots start here, ten per profile.
DIRECT_KEY_BASE = 302
DIRECT_KEY_STRIDE = 10


def direct_key_id(profile: int, category: DrinkCategory) -> int:
    """Recipe id of a profile's stored variant of a drink family."""
    return DIRECT_KEY_BASE + profile * DIRECT_KEY_STRIDE + category


#: Which profile slot a recipe is customised through.
RECIPE_CATEGORIES: dict[Recipe, DrinkCategory] = {
    Recipe.ESPRESSO: DrinkCategory.ESPRESSO,
    Recipe.RISTRETTO: DrinkCategory.ESPRESSO,
    Recipe.LUNGO: DrinkCategory.ESPRESSO,
    Recipe.ESPRESSO_DOPPIO: DrinkCategory.ESPRESSO,
    Recipe.RISTRETTO_DOPPIO: DrinkCategory.ESPRESSO,
    Recipe.CAFE_CREME: DrinkCategory.CAFE_CREME,
    Recipe.CAFE_CREME_DOPPIO: DrinkCategory.CAFE_CREME,
    Recipe.AMERICANO: DrinkCategory.CAFE_CREME,
    Recipe.AMERICANO_EXTRA: DrinkCategory.CAFE_CREME,
    Recipe.LONG_BLACK: DrinkCategory.CAFE_CREME,
    Recipe.RED_EYE: DrinkCategory.CAFE_CREME,
    Recipe.BLACK_EYE: DrinkCategory.CAFE_CREME,
    Recipe.DEAD_EYE: DrinkCategory.CAFE_CREME,
    Recipe.CAPPUCCINO: DrinkCategory.CAPPUCCINO,
    Recipe.CAFFE_LATTE: DrinkCategory.CAPPUCCINO,
    Recipe.CAFE_AU_LAIT: DrinkCategory.CAPPUCCINO,
    Recipe.FLAT_WHITE: DrinkCategory.CAPPUCCINO,
    Recipe.ESPRESSO_MACCHIATO: DrinkCategory.LATTE_MACCHIATO,
    Recipe.LATTE_MACCHIATO: DrinkCategory.LATTE_MACCHIATO,
    Recipe.LATTE_MACCHIATO_EXTRA: DrinkCategory.LATTE_MACCHIATO,
    Recipe.LATTE_MACCHIATO_TRIPLE: DrinkCategory.LATTE_MACCHIATO,
    Recipe.MILK: DrinkCategory.MILK,
    Recipe.MILK_FROTH: DrinkCategory.MILK_FROTH,
    Recipe.HOT_WATER: DrinkCategory.WATER,
}


# ---------------------------------------------------------------------------
# Settings registers
# ---------------------------------------------------------------------------


class Setting(IntEnum):
    """Numerical settings registers (HR read / HW write)."""

    WATER_HARDNESS = 11
    ENERGY_SAVING = 12
    AUTO_OFF_AFTER = 13
    AUTO_OFF_WHEN = 14
    LANGUAGE = 15
    AUTO_BEAN_SELECT = 16
    RINSING_OFF = 18
    CLOCK_READ = 20
    CLOCK_WRITE = 21
    BREW_TEMPERATURE = 22
    WATER_FILTER = 91


WATER_HARDNESS_MIN = 1
WATER_HARDNESS_MAX = 4
AUTO_OFF_MIN_MINUTES = 30
AUTO_OFF_MAX_MINUTES = 480
AUTO_OFF_STEP_MINUTES = 30

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

#: Per-drink counter register = base + ``recipe_type``.
CUP_COUNTER_BASE = 100
TOTAL_CUPS_REGISTER = 150
#: Consecutive unanswered counter registers before the sweep is abandoned.
#: Reading all 24 is 24 round trips; firmware that answers none of them should
#: not cost 24 timeouts on every refresh.
MAX_COUNTER_MISSES = 3

# ---------------------------------------------------------------------------
# Config entry options
# ---------------------------------------------------------------------------

CONF_POLL_INTERVAL = "poll_interval"
CONF_COUNTER_INTERVAL = "counter_interval"
CONF_AUTO_CONFIRM = "auto_confirm_prompts"

DEFAULT_POLL_INTERVAL = 5
DEFAULT_COUNTER_INTERVAL = 600
DEFAULT_AUTO_CONFIRM = False

MIN_POLL_INTERVAL = 2
MAX_POLL_INTERVAL = 120

#: BLE connect attempt budget handed to bleak-retry-connector.
CONNECT_TIMEOUT = 20.0
#: How long the machine may take to answer the HU handshake.
HANDSHAKE_TIMEOUT = 5.0
#: Pause between chained protocol writes; the firmware drops frames that
#: arrive back-to-back during a multi-step brew sequence.
COMMAND_SETTLE_DELAY = 0.2

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

SERVICE_BREW = "brew"
SERVICE_BREW_CUSTOM = "brew_custom"
SERVICE_CANCEL = "cancel"
SERVICE_CONFIRM_PROMPT = "confirm_prompt"
SERVICE_START_MAINTENANCE = "start_maintenance"

ATTR_RECIPE = "recipe"
ATTR_TWO_CUPS = "two_cups"
ATTR_PROCESS = "process"
ATTR_INTENSITY = "intensity"
ATTR_TEMPERATURE = "temperature"
ATTR_PORTION = "portion"
ATTR_HOPPER = "hopper"
ATTR_MILK_PORTION = "milk_portion"
ATTR_NAME = "name"
ATTR_PROGRAM = "program"

#: Maintenance programs that can be started over BLE.
MAINTENANCE_PROGRAMS: dict[str, MachineProcess] = {
    "rinse": MachineProcess.EVAPORATING,
    "easy_clean": MachineProcess.EASY_CLEAN,
    "intensive_clean": MachineProcess.INTENSIVE_CLEAN,
    "descale": MachineProcess.DESCALING,
    "filter_insert": MachineProcess.FILTER_INSERT,
    "filter_replace": MachineProcess.FILTER_REPLACE,
    "filter_remove": MachineProcess.FILTER_REMOVE,
    "switch_off": MachineProcess.SWITCH_OFF,
}
