# Melitta Barista TS Smart for Home Assistant

Custom integration that controls a **Melitta Caffeo Barista TS Smart** (and
the single-hopper **Barista T Smart**) over Bluetooth Low Energy — the same
link the Melitta Connect app uses. No cloud, no account, no vendor app.

> Not affiliated with or endorsed by Melitta. The protocol is
> reverse-engineered; see [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## What it does

- **Brews** any of the 24 built-in drinks, or the drinks stored in a user
  profile, optionally overriding strength, brew temperature, cup size, bean
  hopper and two-cup mode per brew.
- **Reports** live status: process, current step, progress, and what the
  machine is waiting for (water, trays, brew unit, beans, Easy Clean).
- **Counts** drinks — total and per drink type.
- **Adjusts** the auto-off delay and syncs the machine clock.

Everything runs locally over BLE. A machine that is switched off is simply
unavailable until it wakes up.

## Requirements

- Home Assistant 2025.2 or newer (developed and tested against 2026.2).
- A Bluetooth adapter reachable by Home Assistant, or an ESPHome Bluetooth
  proxy in range of the machine.
- Bluetooth enabled in the machine's own menu.

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories**.
2. Add `https://github.com/david-dvinskykh/hass-melitta-barista-ts`,
   category **Integration**.
3. Install **Melitta Barista TS Smart**, then restart Home Assistant.

### Manual

Copy `custom_components/melitta_barista_ts` into your `config/custom_components/`
directory and restart Home Assistant.

## Setup

The machine advertises continuously while switched on, so Home Assistant
normally discovers it by itself — look for a discovered **Melitta Barista TS
Smart** on the Devices & Services page and confirm it. Otherwise add it
manually: **Settings → Devices & Services → Add Integration → Melitta Barista
TS Smart**.

### Bonding

The machine pairs using *numeric comparison*. BlueZ only completes that if
something answers the confirmation prompt, and Home Assistant's default
agent does not — so the integration registers its own auto-confirming agent
at connect time. That is the **Register a Bluetooth pairing agent** option,
on by default.

Turn it off if you bond through an ESPHome Bluetooth proxy (bonding happens
on the proxy) or if you pair by hand:

```
bluetoothctl
[bluetooth]# agent DisplayYesNo
[bluetooth]# default-agent
[bluetooth]# pair AA:BB:CC:DD:EE:FF
```

## Entities

| Entity | Type | Notes |
|---|---|---|
| Status | sensor | Ready, brewing, cleaning, descaling, … |
| Step | sensor | Grinding, extracting, steaming, … |
| Needs attention | sensor | What the machine is waiting for |
| Progress | sensor | 0–100 % during a drink |
| Total drinks | sensor | Lifetime counter |
| *drink* count | sensor | Per-drink counters, disabled by default |
| Machine clock, Firmware | sensor | Diagnostic, disabled by default |
| Brewing, Maintenance running | binary sensor | |
| Water tank empty, Trays full, Trays missing, Brew unit removed, Powder lid open, Bean hopper 1/2 empty, Easy Clean required | binary sensor | Problem class |
| Drink | select | What the Brew button makes |
| Profile, Profile drink | select | What the Brew from profile button makes |
| Strength, Brew temperature, Bean hopper | select | Applied to the next brew |
| Cup size | number | Millilitres, 5 ml steps |
| Two cups | switch | Applied to the next brew |
| Switch off after | number | Written to the machine |
| Brew, Brew from profile, Cancel, Sync clock | button | |
| Espresso key, Café Crème key, Cappuccino key, Latte Macchiato key, Milk froth key, Milk key, Hot water key | button | The machine's direct-select keys |

Selecting a drink reads that recipe from the machine and loads its stored
strength, temperature and cup size into the corresponding entities — so the
staged values always start from what the machine itself would do. Change one
and only that field is overridden; the rest of the recipe is left alone.

Nothing is sent to the machine until you brew.

### Profiles

The machine keeps a set of drinks per user profile — one for each
direct-select key, so seven per profile rather than the full menu. **Profile**
lists what the machine has stored (its names are read from the machine, so
renaming a profile there renames the option here), **Profile drink** picks
which of the seven to make, and **Brew from profile** starts it.

"My Coffee" is the machine's own name for the unnamed default profile. The
Barista TS keeps eight user profiles on top of it, the Barista T four.

The plain **Brew** button is unaffected — it always uses the built-in
recipes, which cover all 24 drinks.

### The machine's own keys

There is also one button per direct-select key on the machine's front panel:
Espresso, Café Crème, Cappuccino, Latte Macchiato, Milk froth, Milk and Hot
water. Pressing one makes what the selected profile stores under that key —
the same as pressing the key on the machine, so the staged strength,
temperature and cup size are deliberately *not* applied. Switch profile and
the keys follow, exactly as they do on the machine.

Use **Brew from profile** instead when the staged values should apply.

## Services

### `melitta_barista_ts.brew`

Brews a drink. Every field is optional and falls back to whatever is staged
on the entities.

```yaml
action: melitta_barista_ts.brew
target:
  device_id: abc123
data:
  drink: cappuccino
  intensity: strong
  portion_ml: 120
  two_cups: false
  bean_hopper: hopper_2
```

Add `profile` to brew that profile's version of the drink instead of the
built-in recipe — `0` for My Coffee, `1`–`8` for the user profiles:

```yaml
action: melitta_barista_ts.brew
target:
  device_id: abc123
data:
  profile: 2
  drink: latte_macchiato
```

Because a profile only stores the seven direct-select drinks, combining
`profile` with something like `flat_white` is rejected with an explanatory
error rather than silently brewing something else.

### `melitta_barista_ts.cancel`

Stops the running process.

### `melitta_barista_ts.set_clock`

Sets the machine's clock; defaults to Home Assistant's local time.

### `melitta_barista_ts.read_setting` / `write_setting`

Raw access to the numerical registers, for the settings whose value
encodings are not confirmed yet (see `docs/PROTOCOL.md`). `read_setting`
returns a response:

```yaml
action: melitta_barista_ts.read_setting
target:
  device_id: abc123
data:
  setting_id: 11
response_variable: hardness
```

Writing an unknown register can leave the machine in an unexpected state.
Read a register before writing it, and note the original value.

## Example automation

```yaml
automation:
  - alias: Morning cappuccino
    triggers:
      - trigger: time
        at: "07:15:00"
    conditions:
      - condition: state
        entity_id: binary_sensor.melitta_barista_ts_smart_water_tank_empty
        state: "off"
    actions:
      - action: melitta_barista_ts.brew
        target:
          device_id: !input machine
        data:
          drink: cappuccino
          intensity: strong
```

## Troubleshooting

**Machine not discovered.** Switch it on, enable Bluetooth in its menu, and
check that an adapter or proxy is within range. The machine advertises a
random BLE address, but Home Assistant tracks it by the resolved identity
address once bonded.

**Pairing fails with "Operation not permitted".** No agent answered the
confirmation. Leave the pairing-agent option on, or pair manually with
`bluetoothctl` as shown above.

**"Pairing failed due to error: 102", or the log shows the bond being
cleared.** The adapter or proxy still holds a bond the machine has forgotten
— after a factory reset, or after it bonded with a phone since. The
integration notices a refused pairing, drops its own half of the bond and
pairs again, which takes one extra connect attempt. If pairing keeps being
refused, the other half is stale too: forget the machine in its Bluetooth
menu, or clear the bond table on the ESPHome proxy, then reconnect.

**Connects, then every frame reports a checksum mismatch.** The handshake
produced a bad session key. The integration rejects mismatched handshakes and
retries, so this usually clears on the next poll — if it does not, delete the
bond on both sides and pair again.

**Commands are acknowledged but nothing happens.** Sending `HE` alone does
not brew; the full sequence is required. If you are scripting against the
protocol directly, see the brewing section of `docs/PROTOCOL.md`.

Enable debug logging to see every frame:

```yaml
logger:
  logs:
    custom_components.melitta_barista_ts: debug
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install pytest-homeassistant-custom-component
pytest
```

`protocol.py` and `machine.py` have no Home Assistant or Bluetooth imports,
so the wire format and command sequencing are testable on their own.

## Credits

Protocol documentation is based on the reverse-engineering work published in
[dzerik/melitta-barista-ha](https://github.com/dzerik/melitta-barista-ha)
(MIT). This is an independent implementation — see [`NOTICE`](NOTICE).

## License

MIT — see [`LICENSE`](LICENSE).
