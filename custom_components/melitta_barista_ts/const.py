"""Constants for the Melitta Barista TS Smart integration.

Protocol constants come from the reverse-engineered Eugster/Frismag BLE
stack used by the Melitta Barista T / TS Smart machines. See
``docs/PROTOCOL.md`` for the wire format and the provenance of the
crypto material below.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag
from typing import Final

DOMAIN: Final = "melitta_barista_ts"
MANUFACTURER: Final = "Melitta"

#: hass.data key holding, per machine address, the adapters whose bond the
#: machine has refused. Outlives the config entry retries, which build a
#: fresh client each time.
REFUSED_SOURCES: Final = f"{DOMAIN}_refused_sources"

# --------------------------------------------------------------------------
# BLE GATT
# --------------------------------------------------------------------------

SERVICE_UUID: Final = "0000ad00-b35c-11e4-9813-0002a5d5c51b"
CHAR_NOTIFY_UUID: Final = "0000ad02-b35c-11e4-9813-0002a5d5c51b"

# The vendor app writes to a characteristic inside the AD00 service. Field
# reports disagree on whether that is AD01 or AD03 (it appears to differ by
# firmware revision), so the client resolves it at connect time and only
# falls back to this preference order when several candidates are writable.
CHAR_WRITE_UUID_CANDIDATES: Final[tuple[str, ...]] = (
    "0000ad03-b35c-11e4-9813-0002a5d5c51b",
    "0000ad01-b35c-11e4-9813-0002a5d5c51b",
)

# BLE local-name prefixes. The machines advertise their article number as the
# local name, e.g. "860400E250429374203-".
BLE_NAME_PREFIXES_T: Final[frozenset[str]] = frozenset({"8301", "8311", "8401"})
BLE_NAME_PREFIXES_TS: Final[frozenset[str]] = frozenset({"8501", "8601", "8604"})
BLE_NAME_PREFIXES: Final[frozenset[str]] = BLE_NAME_PREFIXES_T | BLE_NAME_PREFIXES_TS

# --------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------

FRAME_START: Final = 0x53  # 'S'
FRAME_END: Final = 0x45  # 'E'
BLE_MTU: Final = 20
RX_BUFFER_LIMIT: Final = 128
RX_FRAME_ASSEMBLY_TIMEOUT: Final = 1.0

CMD_ACK: Final = "A"
CMD_NACK: Final = "N"
CMD_READ_ALPHA: Final = "HA"
CMD_WRITE_ALPHA: Final = "HB"
CMD_READ_RECIPE: Final = "HC"
CMD_START_PROCESS: Final = "HE"
CMD_WRITE_RECIPE: Final = "HJ"
CMD_READ_NUMERICAL: Final = "HR"
CMD_HANDSHAKE: Final = "HU"
CMD_READ_VERSION: Final = "HV"
CMD_WRITE_NUMERICAL: Final = "HW"
CMD_READ_STATUS: Final = "HX"
CMD_CANCEL_PROCESS: Final = "HZ"

#: Commands the machine can send to us, mapped to
#: ``(payload_size, rc4_encrypted)``. The parser needs the exact payload size
#: to find frame boundaries, because both ``S`` and ``E`` occur inside RC4
#: ciphertext.
INBOUND_COMMANDS: Final[dict[str, tuple[int, bool]]] = {
    CMD_ACK: (0, False),
    CMD_NACK: (0, False),
    "HA": (66, True),
    "HC": (66, True),
    "HF": (16, True),
    "HL": (20, True),
    "HP": (14, True),
    "HQ": (15, True),
    "HR": (6, True),
    "HU": (8, True),
    "HV": (11, True),
    "HX": (8, True),
}

# --------------------------------------------------------------------------
# Crypto
# --------------------------------------------------------------------------

#: RC4 stream key. The vendor app ships it as an AES-CBC encrypted blob; the
#: decrypted plaintext is this ASCII string. Embedding the result directly
#: keeps the integration free of a ``cryptography`` dependency — the
#: derivation is documented in ``docs/PROTOCOL.md`` for verification.
RC4_KEY: Final = b"MEL_090217_V10_?R4.wozJ!(*q2ds3#"

# fmt: off
#: 256-byte lookup table used by the HU handshake verifier.
HU_TABLE: Final = bytes(
    b & 0xFF
    for b in (
        98, 6, 85, -106, 36, 23, 112, -92, -121, -49, -87, 5, 26, 64,
        -91, -37, 61, 20, 68, 89, -126, 63, 52, 102, 24, -27, -124, -11,
        80, -40, -61, 115, 90, -88, -100, -53, -79, 120, 2, -66, -68, 7,
        100, -71, -82, -13, -94, 10, -19, 18, -3, -31, 8, -48, -84, -12,
        -1, 126, 101, 79, -111, -21, -28, 121, 123, -5, 67, -6, -95, 0,
        107, 97, -15, 111, -75, 82, -7, 33, 69, 55, 59, -103, 29, 9,
        -43, -89, 84, 93, 30, 46, 94, 75, -105, 114, 73, -34, -59, 96,
        -46, 45, 16, -29, -8, -54, 51, -104, -4, 125, 81, -50, -41, -70,
        39, -98, -78, -69, -125, -120, 1, 49, 50, 17, -115, 91, 47,
        -127, 60, 99, -102, 35, 86, -85, 105, 34, 38, -56, -109, 58, 77,
        118, -83, -10, 76, -2, -123, -24, -60, -112, -58, 124, 53, 4,
        108, 74, -33, -22, -122, -26, -99, -117, -67, -51, -57, -128,
        -80, 19, -45, -20, 127, -64, -25, 70, -23, 88, -110, 44, -73,
        -55, 22, 83, 13, -42, 116, 109, -97, 32, 95, -30, -116, -36, 57,
        12, -35, 31, -47, -74, -113, 92, -107, -72, -108, 62, 113, 65,
        37, 27, 106, -90, 3, 14, -52, 72, 21, 41, 56, 66, 28, -63, 40,
        -39, 25, 54, -77, 117, -18, 87, -16, -101, -76, -86, -14, -44,
        -65, -93, 78, -38, -119, -62, -81, 110, 43, 119, -32, 71, 122,
        -114, 42, -96, 104, 48, -9, 103, 15, 11, -118, -17,
    )
)
# fmt: on

# --------------------------------------------------------------------------
# Machine status enums (HX payload)
# --------------------------------------------------------------------------


class MachineProcess(IntEnum):
    """Top-level process reported in the HX status frame."""

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
    """Second-level process detail reported in the HX status frame."""

    NONE = 0
    GRINDING = 1
    COFFEE = 2
    STEAM = 3
    WATER = 4
    PREPARE = 5


class InfoMessage(IntFlag):
    """Bitfield of transient machine notices (HX byte 4)."""

    NONE = 0
    FILL_BEANS_1 = 1 << 0
    FILL_BEANS_2 = 1 << 1
    EASY_CLEAN = 1 << 2
    POWDER_FILLED = 1 << 3
    PREPARATION_CANCELLED = 1 << 4


class Manipulation(IntEnum):
    """Physical action the machine is waiting for (HX byte 5)."""

    NONE = 0
    BU_REMOVED = 1
    TRAYS_MISSING = 2
    EMPTY_TRAYS = 3
    FILL_WATER = 4
    CLOSE_POWDER_LID = 5
    FILL_POWDER = 6


#: Machine states in which a brew request can be accepted.
BREW_READY_PROCESSES: Final[frozenset[int]] = frozenset({MachineProcess.READY})

# --------------------------------------------------------------------------
# Recipes
# --------------------------------------------------------------------------


class RecipeId(IntEnum):
    """Built-in recipe slot IDs, readable with ``HC``."""

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


#: Stable slugs used as service/select option values and translation keys.
RECIPE_SLUGS: Final[dict[RecipeId, str]] = {
    RecipeId.ESPRESSO: "espresso",
    RecipeId.RISTRETTO: "ristretto",
    RecipeId.LUNGO: "lungo",
    RecipeId.ESPRESSO_DOPPIO: "espresso_doppio",
    RecipeId.RISTRETTO_DOPPIO: "ristretto_doppio",
    RecipeId.CAFE_CREME: "cafe_creme",
    RecipeId.CAFE_CREME_DOPPIO: "cafe_creme_doppio",
    RecipeId.AMERICANO: "americano",
    RecipeId.AMERICANO_EXTRA: "americano_extra",
    RecipeId.LONG_BLACK: "long_black",
    RecipeId.RED_EYE: "red_eye",
    RecipeId.BLACK_EYE: "black_eye",
    RecipeId.DEAD_EYE: "dead_eye",
    RecipeId.CAPPUCCINO: "cappuccino",
    RecipeId.ESPRESSO_MACCHIATO: "espresso_macchiato",
    RecipeId.CAFFE_LATTE: "caffe_latte",
    RecipeId.CAFE_AU_LAIT: "cafe_au_lait",
    RecipeId.FLAT_WHITE: "flat_white",
    RecipeId.LATTE_MACCHIATO: "latte_macchiato",
    RecipeId.LATTE_MACCHIATO_EXTRA: "latte_macchiato_extra",
    RecipeId.LATTE_MACCHIATO_TRIPLE: "latte_macchiato_triple",
    RecipeId.MILK: "milk",
    RecipeId.MILK_FROTH: "milk_froth",
    RecipeId.HOT_WATER: "hot_water",
}

SLUG_TO_RECIPE: Final[dict[str, RecipeId]] = {
    slug: recipe for recipe, slug in RECIPE_SLUGS.items()
}

#: Display names written to the machine's freestyle name slot before brewing.
RECIPE_DISPLAY_NAMES: Final[dict[RecipeId, str]] = {
    RecipeId.ESPRESSO: "Espresso",
    RecipeId.RISTRETTO: "Ristretto",
    RecipeId.LUNGO: "Lungo",
    RecipeId.ESPRESSO_DOPPIO: "Espresso Doppio",
    RecipeId.RISTRETTO_DOPPIO: "Ristretto Doppio",
    RecipeId.CAFE_CREME: "Cafe Creme",
    RecipeId.CAFE_CREME_DOPPIO: "Cafe Creme Doppio",
    RecipeId.AMERICANO: "Americano",
    RecipeId.AMERICANO_EXTRA: "Americano Extra",
    RecipeId.LONG_BLACK: "Long Black",
    RecipeId.RED_EYE: "Red Eye",
    RecipeId.BLACK_EYE: "Black Eye",
    RecipeId.DEAD_EYE: "Dead Eye",
    RecipeId.CAPPUCCINO: "Cappuccino",
    RecipeId.ESPRESSO_MACCHIATO: "Espresso Macchiato",
    RecipeId.CAFFE_LATTE: "Caffe Latte",
    RecipeId.CAFE_AU_LAIT: "Cafe au Lait",
    RecipeId.FLAT_WHITE: "Flat White",
    RecipeId.LATTE_MACCHIATO: "Latte Macchiato",
    RecipeId.LATTE_MACCHIATO_EXTRA: "Latte Macchiato Extra",
    RecipeId.LATTE_MACCHIATO_TRIPLE: "Latte Macchiato Triple",
    RecipeId.MILK: "Milk",
    RecipeId.MILK_FROTH: "Milk Froth",
    RecipeId.HOT_WATER: "Hot Water",
}

#: Recipes only present on the dual-hopper Barista TS Smart.
TS_ONLY_RECIPES: Final[frozenset[RecipeId]] = frozenset(
    {RecipeId.RED_EYE, RecipeId.BLACK_EYE, RecipeId.DEAD_EYE}
)

#: ``recipe_type`` byte → ``recipe_key`` byte, required when writing HJ.
RECIPE_TYPE_TO_KEY: Final[dict[int, int]] = {
    0: 0,
    1: 0,
    2: 0,
    3: 0,
    4: 0,  # espresso family  → ESPRESSO
    5: 1,
    6: 1,
    7: 1,
    8: 1,
    9: 1,  # cafe creme family → COFFEE
    10: 1,
    11: 1,
    12: 1,  # eye drinks        → COFFEE
    13: 2,
    14: 2,
    15: 2,
    16: 2,
    17: 2,  # cappuccino family → CAPPUCCINO
    18: 3,
    19: 3,
    20: 3,  # latte macchiato   → MACCHIATO
    21: 5,  # milk              → MILK
    22: 4,  # milk froth        → MILK_FROTH
    23: 6,  # hot water         → WATER
    24: 7,  # freestyle         → MENU
}

RECIPE_KEY_MENU: Final = 7

#: Scratch slot the machine brews from, plus the name slot shown on its display.
TEMP_RECIPE_ID: Final = 400
FREESTYLE_NAME_ID: Final = 401

#: ``HE`` process argument selecting "make a drink".
PROCESS_PRODUCT: Final = 4

# --------------------------------------------------------------------------
# User profiles (DirectKey slots)
# --------------------------------------------------------------------------


class DirectKeyCategory(IntEnum):
    """The seven drink slots every profile stores.

    A profile does not hold all 24 recipes — it holds one drink per
    direct-select key on the machine's front panel.
    """

    ESPRESSO = 0
    CAFE_CREME = 1
    CAPPUCCINO = 2
    LATTE_MACCHIATO = 3
    MILK_FROTH = 4
    MILK = 5
    WATER = 6


#: Direct-key recipes are laid out in blocks of ten, one block per profile:
#: ``302 + profile * 10 + category``.
DIRECTKEY_BASE_ID: Final = 302
PROFILE_STRIDE: Final = 10

#: Profile names live in the same block, at its start — profile 1 at 310.
#: Profile 0 has no stored name; the machine calls it "My Coffee".
PROFILE_NAME_BASE_ID: Final = 310
MY_COFFEE_PROFILE: Final = 0
MY_COFFEE_NAME: Final = "My Coffee"


def directkey_recipe_id(profile: int, category: DirectKeyCategory) -> int:
    """Return the recipe slot holding ``category`` for ``profile``."""
    return DIRECTKEY_BASE_ID + profile * PROFILE_STRIDE + int(category)


def profile_name_id(profile: int) -> int:
    """Return the text register holding a user profile's name.

    Only valid for profiles 1 and up — "My Coffee" has no stored name.
    """
    if profile < 1:
        raise ValueError(f"profile {profile} has no name register")
    return PROFILE_NAME_BASE_ID + (profile - 1) * PROFILE_STRIDE


#: Direct-key slugs deliberately reuse the built-in recipe slugs, so a drink
#: means the same thing whether it comes from the menu or from a profile.
DIRECTKEY_SLUGS: Final[dict[DirectKeyCategory, str]] = {
    DirectKeyCategory.ESPRESSO: "espresso",
    DirectKeyCategory.CAFE_CREME: "cafe_creme",
    DirectKeyCategory.CAPPUCCINO: "cappuccino",
    DirectKeyCategory.LATTE_MACCHIATO: "latte_macchiato",
    DirectKeyCategory.MILK_FROTH: "milk_froth",
    DirectKeyCategory.MILK: "milk",
    DirectKeyCategory.WATER: "hot_water",
}
SLUG_TO_DIRECTKEY: Final[dict[str, DirectKeyCategory]] = {
    slug: category for category, slug in DIRECTKEY_SLUGS.items()
}

#: Labels written to the machine's display when brewing from a profile.
DIRECTKEY_DISPLAY_NAMES: Final[dict[DirectKeyCategory, str]] = {
    DirectKeyCategory.ESPRESSO: "Espresso",
    DirectKeyCategory.CAFE_CREME: "Cafe Creme",
    DirectKeyCategory.CAPPUCCINO: "Cappuccino",
    DirectKeyCategory.LATTE_MACCHIATO: "Latte Macchiato",
    DirectKeyCategory.MILK_FROTH: "Milk Froth",
    DirectKeyCategory.MILK: "Milk",
    DirectKeyCategory.WATER: "Hot Water",
}


class ComponentProcess(IntEnum):
    """What a recipe component dispenses."""

    NONE = 0
    COFFEE = 1
    STEAM = 2
    WATER = 3


class Intensity(IntEnum):
    """Coffee strength."""

    VERY_MILD = 0
    MILD = 1
    MEDIUM = 2
    STRONG = 3
    VERY_STRONG = 4


class BrewTemperature(IntEnum):
    """Brew temperature, as the three levels the machine's own menu offers.

    Not a cold/normal/hot scale: a stock profile espresso reads back as 0,
    and the machine cannot brew cold espresso.
    """

    LOW = 0
    MEDIUM = 1
    HIGH = 2


class Blend(IntEnum):
    """Bean hopper selection. Single-hopper machines ignore this byte."""

    DEFAULT = 0
    HOPPER_1 = 1
    HOPPER_2 = 2


INTENSITY_SLUGS: Final[dict[Intensity, str]] = {
    Intensity.VERY_MILD: "very_mild",
    Intensity.MILD: "mild",
    Intensity.MEDIUM: "medium",
    Intensity.STRONG: "strong",
    Intensity.VERY_STRONG: "very_strong",
}
SLUG_TO_INTENSITY: Final[dict[str, Intensity]] = {
    slug: value for value, slug in INTENSITY_SLUGS.items()
}

TEMPERATURE_SLUGS: Final[dict[BrewTemperature, str]] = {
    BrewTemperature.LOW: "low",
    BrewTemperature.MEDIUM: "medium",
    BrewTemperature.HIGH: "high",
}
SLUG_TO_TEMPERATURE: Final[dict[str, BrewTemperature]] = {
    slug: value for value, slug in TEMPERATURE_SLUGS.items()
}

BLEND_SLUGS: Final[dict[Blend, str]] = {
    Blend.DEFAULT: "recipe_default",
    Blend.HOPPER_1: "hopper_1",
    Blend.HOPPER_2: "hopper_2",
}
SLUG_TO_BLEND: Final[dict[str, Blend]] = {
    slug: value for value, slug in BLEND_SLUGS.items()
}

#: The ``portion`` byte counts 5 ml steps.
PORTION_STEP_ML: Final = 5
PORTION_MIN_ML: Final = 5
PORTION_MAX_ML: Final = 255 * PORTION_STEP_ML

# --------------------------------------------------------------------------
# Numerical registers (HR / HW)
# --------------------------------------------------------------------------


class SettingId(IntEnum):
    """Numerical registers readable with ``HR`` and writable with ``HW``."""

    MACHINE_TYPE = 6
    WATER_HARDNESS = 11
    ENERGY_SAVING = 12
    AUTO_OFF_AFTER = 13
    AUTO_OFF_WHEN = 14
    LANGUAGE = 15
    AUTO_BEAN_SELECT = 16
    RINSING_OFF = 18
    CLOCK = 20
    CLOCK_SET = 21
    TEMPERATURE = 22
    FILTER = 91


class MachineType(IntEnum):
    """Values reported by the ``MACHINE_TYPE`` register."""

    BARISTA_T = 258
    BARISTA_TS = 259


MACHINE_TYPE_NAMES: Final[dict[MachineType, str]] = {
    MachineType.BARISTA_T: "Barista T Smart",
    MachineType.BARISTA_TS: "Barista TS Smart",
}

#: User profiles per model, excluding "My Coffee". The TS keeps eight, the
#: single-hopper T four.
USER_PROFILE_COUNTS: Final[dict[MachineType, int]] = {
    MachineType.BARISTA_T: 4,
    MachineType.BARISTA_TS: 8,
}
MAX_USER_PROFILES: Final = 8


def user_profile_count(machine_type: MachineType | None) -> int:
    """Return how many named user profiles a model offers.

    Assumes the larger TS set until the model has been read, so profiles are
    not hidden from a machine that simply has not answered yet.
    """
    if machine_type is None:
        return MAX_USER_PROFILES
    return USER_PROFILE_COUNTS.get(machine_type, MAX_USER_PROFILES)


#: Per-drink cup counters live at ``CUP_COUNTER_BASE_ID + recipe_type``.
CUP_COUNTER_BASE_ID: Final = 100
#: Counts every dispense including hot water, so it runs ahead of the total
#: the machine's own statistics screen shows.
TOTAL_CUPS_ID: Final = 150


class CareProgramme(IntEnum):
    """How many times each care programme has been run.

    Read off a Barista TS Smart and matched one by one against the numbers
    on its own Statistics → Care screen.
    """

    COFFEE_SYSTEM_CLEANING = 161
    DESCALING = 162
    FILTER_CHANGE = 163
    MILK_SYSTEM_CLEANING = 164


CARE_SLUGS: Final[dict[CareProgramme, str]] = {
    CareProgramme.MILK_SYSTEM_CLEANING: "milk_system_cleanings",
    CareProgramme.COFFEE_SYSTEM_CLEANING: "coffee_system_cleanings",
    CareProgramme.DESCALING: "descalings",
    CareProgramme.FILTER_CHANGE: "filter_changes",
}

AUTO_OFF_MIN_MINUTES: Final = 5
AUTO_OFF_MAX_MINUTES: Final = 480

MINUTES_PER_DAY: Final = 24 * 60

# --------------------------------------------------------------------------
# Config entry options
# --------------------------------------------------------------------------

CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_FRAME_TIMEOUT: Final = "frame_timeout"
CONF_CONNECT_TIMEOUT: Final = "connect_timeout"
CONF_PAIRING_AGENT: Final = "pairing_agent"
CONF_BRAND_ICON: Final = "brand_icon"

DEFAULT_POLL_INTERVAL: Final = 5.0
DEFAULT_FRAME_TIMEOUT: Final = 5.0
#: Generous, because bonding is the slow part: a plain connect through a
#: Bluetooth proxy has been measured at around 20 s on this machine, and the
#: Numeric Comparison exchange on top of it needs more still. Home Assistant
#: gives a config entry 300 s to set up, which the whole ladder fits inside.
DEFAULT_CONNECT_TIMEOUT: Final = 60.0
DEFAULT_PAIRING_AGENT: Final = True
DEFAULT_BRAND_ICON: Final = False

#: Delay the vendor app inserts between the steps of the brew sequence.
BREW_STEP_DELAY: Final = 0.2

#: Retries inside a single ``establish_connection`` call. Kept low because the
#: caller bounds the whole attempt with its own timeout — the library default
#: of four, each with a 60 s safety timeout, is far longer than Home Assistant
#: will wait for a config entry to set up.
CONNECT_ATTEMPTS: Final = 2

#: Pause before escalating to a bonded connect, so the adapter or Bluetooth
#: proxy can release the connection slot the failed attempt was holding.
PAIR_SETTLE_DELAY: Final = 2.0

# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------

SERVICE_BREW: Final = "brew"
SERVICE_CANCEL: Final = "cancel"
SERVICE_SET_CLOCK: Final = "set_clock"
SERVICE_WRITE_SETTING: Final = "write_setting"
SERVICE_READ_SETTING: Final = "read_setting"
SERVICE_SCAN_SETTINGS: Final = "scan_settings"
SERVICE_REPAIR_CONNECTION: Final = "repair_connection"
SERVICE_READ_FRAME: Final = "read_frame"

ATTR_DRINK: Final = "drink"
ATTR_TWO_CUPS: Final = "two_cups"
ATTR_INTENSITY: Final = "intensity"
ATTR_TEMPERATURE: Final = "temperature"
ATTR_PORTION_ML: Final = "portion_ml"
ATTR_BEAN_HOPPER: Final = "bean_hopper"
ATTR_PROFILE: Final = "profile"
ATTR_SETTING_ID: Final = "setting_id"
ATTR_COMMAND: Final = "command"
ATTR_FIRST_ID: Final = "first_id"
ATTR_LAST_ID: Final = "last_id"
ATTR_VALUE: Final = "value"
ATTR_TIME: Final = "time"
