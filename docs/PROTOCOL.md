# Melitta Barista T/TS Smart — BLE protocol

Notes on the reverse-engineered Bluetooth Low Energy protocol this
integration speaks. The machines use an Eugster/Frismag OEM BLE stack that
also appears on other brands, so the framing below is not Melitta-specific —
only the RC4 key and the handshake table are.

Everything here was derived from the vendor Android app and from published
reverse-engineering work (see [Credits](#credits)). Treat it as a good
description of observed behaviour rather than a vendor specification.

## GATT

| | |
|---|---|
| Service | `0000ad00-b35c-11e4-9813-0002a5d5c51b` |
| Notify characteristic | `0000ad02-b35c-11e4-9813-0002a5d5c51b` |
| Write characteristic | `0000ad01-…` or `0000ad03-…`, see below |
| Write size | 20 bytes — longer frames must be chunked |
| Advertised name | article number, e.g. `860400E250429374203-` |

Sources disagree about the write characteristic: protocol notes name `AD01`,
while implementations that run against real hardware use `AD03`. This
integration does not choose statically — it enumerates the service and sends
the handshake on each writable characteristic until one answers, because
writing to the wrong one fails silently rather than erroring.

The full characteristic table, read from firmware `02590029014`:

| Characteristic | Properties |
|---|---|
| `AD01` | `write` |
| `AD02` | `notify` |
| `AD03` | `write` |
| `AD06` | `notify`, `read` |

Two things this table settles, both of which cost a day of debugging:

- **Writes need a response.** Both write characteristics declare plain
  `write`, not `write-without-response`. Requesting a write-without-response
  on them is not a valid GATT operation: the frame is accepted locally and
  never reaches the machine.
- **There is a second notify characteristic**, `AD06`, that no protocol notes
  mention. Subscribe to every notify characteristic of the service rather
  than to `AD02` alone.

On this firmware `AD03` is the one that answers.

### Bonding

The machine pairs with **Numeric Comparison**, not Just Works. BlueZ only
completes that if a registered agent answers `RequestConfirmation`, and the
`NoInputNoOutput` agent Home Assistant runs with does not — pairing fails
with `Operation not permitted`.

`pairing.py` registers a `DisplayYesNo` agent that auto-confirms. It is
best-effort and only relevant for a local BlueZ adapter; ESPHome Bluetooth
proxies bond on the proxy instead.

**Bonding is mandatory, and its absence is silent.** An unbonded connection
is accepted, the service and characteristics resolve, notifications
subscribe — and then every write is ignored and the machine drops the link a
second later. Observed on hardware:

```
(pair=False) → no handshake on any writable characteristic: no response to HU
(pair=True)  → handshake answered on AD03, session established
```

So the bond has to be requested from the connector itself
(`establish_connection(pair=True)`); a local pairing agent alone does not
produce one, and on a Bluetooth proxy the agent plays no part at all.

## Framing

Before encryption:

```
S (0x53) | command (1–2 ASCII) | [key_prefix (2)] | [payload] | checksum (1) | E (0x45)
```

Then everything after the command bytes and before the trailing `E` is
RC4-encrypted:

```
S | command | RC4(key_prefix + payload + checksum) | E
```

- `key_prefix` is the session value from the handshake. It is absent from
  the handshake frame itself and from responses.
- `A` (ACK) and `N` (NACK) are sent in the clear.
- Checksum is `~(sum of command bytes + key_prefix + payload) & 0xFF`,
  computed before encryption.

### Parsing

RC4 ciphertext contains arbitrary bytes, including `0x53` and `0x45`, so
frame boundaries cannot be found by scanning for markers. The parser instead:

1. starts collecting at `S` when the buffer is empty,
2. treats every later byte as data,
3. on `E`, checks whether the buffer length matches
   `1 + len(command) + payload_size + 1 + 1` for a known command,
4. accepts the frame only on a match, and keeps collecting otherwise.

This is why `INBOUND_COMMANDS` in `const.py` must carry an exact payload size
for every command the machine can send — including ones whose contents we do
not decode.

A partial frame older than one second is discarded, and the buffer resets at
128 bytes.

## Encryption

RC4 with a fixed 32-byte key, re-keyed for every frame (no stream state
carries over).

The vendor app stores the key as an AES-CBC blob and decrypts it at runtime.
Doing the same here would only add a `cryptography` dependency to arrive at a
constant, so `const.RC4_KEY` holds the plaintext directly:

```
MEL_090217_V10_?R4.wozJ!(*q2ds3#
```

To verify it yourself, AES-CBC-decrypt the 48-byte blob from the app with the
32-byte key (`part_b || part_a`) and IV published in the reference
implementation, then strip PKCS#5 padding.

## Handshake (`HU`)

App-initiated; the machine stays silent until it sees a valid challenge.

1. App picks 4 random bytes and sends `challenge(4) + verifier(2)`.
2. Machine replies with `echo(4) + key_prefix(2) + verifier(2)`.
3. App checks that the echo matches what it sent and that the verifier
   matches `hu_verifier(response[0:6])`, then uses `key_prefix` in every
   later frame.

The verifier is two folds through a 256-byte table (`const.HU_TABLE`), seeded
one index apart and finished with `+93` and `+167`.

Rejecting a mismatched response matters: accepting a bad `key_prefix` would
make every subsequent frame undecryptable in a way that looks like a checksum
bug.

## Commands

App → machine:

| Command | Meaning | Payload |
|---|---|---|
| `HU` | handshake challenge | 6 |
| `HA` | read text register | 2 |
| `HB` | write text register | 66 |
| `HC` | read recipe | 2 |
| `HE` | start process | 18 |
| `HJ` | write recipe | 66 |
| `HR` | read numerical register | 2 |
| `HV` | read firmware version | 0 |
| `HW` | write numerical register | 6 |
| `HX` | read status | 0 |
| `HZ` | cancel process | 4 |

Machine → app:

| Command | Meaning | Payload | Encrypted |
|---|---|---|---|
| `A` | ACK | 0 | no |
| `N` | NACK | 0 | no |
| `HU` | handshake response | 8 | yes |
| `HA` | text value | 66 | yes |
| `HC` | recipe | 66 (19 used) | yes |
| `HR` | numerical value | 6 | yes |
| `HV` | firmware version | 11 | yes |
| `HX` | status | 8 | yes |
| `HF`, `HL`, `HP`, `HQ` | not decoded | 16, 20, 14, 15 | yes |

`HB`, `HE`, `HJ`, `HW` and `HZ` are write-only: the machine answers `A`/`N`
rather than echoing the command.

## Status (`HX`, 8 bytes)

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | process (big-endian) |
| 2 | 2 | sub-process (big-endian) |
| 4 | 1 | info bitfield |
| 5 | 1 | manipulation |
| 6 | 2 | progress, 0–100 |

Process: `READY=2`, `PRODUCT=4`, `CLEANING=9`, `DESCALING=10`,
`FILTER_INSERT=11`, `FILTER_REPLACE=12`, `FILTER_REMOVE=13`,
`SWITCH_OFF=16`, `EASY_CLEAN=17`, `INTENSIVE_CLEAN=19`, `EVAPORATING=20`,
`BUSY=99`.

Sub-process: `GRINDING=1`, `COFFEE=2`, `STEAM=3`, `WATER=4`, `PREPARE=5`.

Info bits: `FILL_BEANS_1`, `FILL_BEANS_2`, `EASY_CLEAN`, `POWDER_FILLED`,
`PREPARATION_CANCELLED`.

Manipulation: `NONE=0`, `BU_REMOVED=1`, `TRAYS_MISSING=2`, `EMPTY_TRAYS=3`,
`FILL_WATER=4`, `CLOSE_POWDER_LID=5`, `FILL_POWDER=6`.

## Brewing

`HE` on its own is acknowledged but brews nothing — the machine dispenses
whatever sits in its scratch slot. The vendor sequence has to be replayed in
full, with roughly 200 ms between the writes:

```
HC  read recipe 200..223
HJ  write it into slot 400, with recipe_key derived from recipe_type
HB  write the display name into text slot 401
HE  start process 4 (PRODUCT)
```

### Recipe layout

`HC` response (19 significant bytes):

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | recipe id |
| 2 | 1 | recipe type |
| 3 | 8 | component 1 |
| 11 | 8 | component 2 |

`HJ` request (66 bytes) inserts a `recipe_key` byte at offset 3, shifting the
components to 4 and 12. **The read response has no `recipe_key`** — getting
this wrong shifts every component field by one byte.

`recipe_key` is a function of `recipe_type`: espresso family → 0, coffee
family → 1, cappuccino family → 2, latte macchiato → 3, milk froth → 4, milk
→ 5, water → 6, freestyle → 7. Espresso Macchiato (type 14) maps to
cappuccino (2), not macchiato.

### Recipe component (8 bytes)

| Offset | Field | Values |
|---|---|---|
| 0 | process | none=0, coffee=1, steam=2, water=3 |
| 1 | shots | 0–3 |
| 2 | blend | default=0, hopper 1=1, hopper 2=2 |
| 3 | intensity | very mild=0 … very strong=4 |
| 4 | aroma | standard=0, intense=1 |
| 5 | temperature | cold=0, normal=1, high=2 |
| 6 | portion | × 5 = millilitres |
| 7 | reserved | 0 |

### Start process (`HE`, 18 bytes)

| Offset | Size | Value |
|---|---|---|
| 0 | 2 | process type — 4 for a drink |
| 2 | 2 | 2 |
| 4 | 2 | 0 |
| 6 | 2 | 1 to dispense two cups |
| 8 | 8 | 0 |

### Recipe IDs

200–223 in the order Espresso, Ristretto, Lungo, Espresso Doppio, Ristretto
Doppio, Café Crème, Café Crème Doppio, Americano, Americano Extra, Long
Black, Red Eye, Black Eye, Dead Eye, Cappuccino, Espresso Macchiato, Caffè
Latte, Café au Lait, Flat White, Latte Macchiato, Latte Macchiato Extra,
Latte Macchiato Triple, Milk, Milk Froth, Hot Water.

`recipe_type` is `recipe_id - 200`. Red Eye, Black Eye and Dead Eye exist
only on the TS.

Slot 400 is the brewing scratch slot; text slot 401 holds the name shown on
the display while brewing.

## User profiles (direct-key slots)

Besides the 24-drink menu, the machine stores per-profile drinks — one for
each direct-select key on its front panel. These live in blocks of ten:

```
recipe id = 302 + profile * 10 + category
```

`profile` is 0 for the unnamed default the machine calls "My Coffee", then 1
upwards for the user profiles (eight on the TS, four on the T). `category` is
one of:

| Value | Direct key |
|---|---|
| 0 | Espresso |
| 1 | Café Crème |
| 2 | Cappuccino |
| 3 | Latte Macchiato |
| 4 | Milk Froth |
| 5 | Milk |
| 6 | Hot Water |

Profile names are text registers at the start of the same block:
`310 + (profile - 1) * 10`, so profile 1 is 310, profile 2 is 320. Profile 0
has no name register.

Direct-key slots read back through `HC` in exactly the same shape as the
built-in recipes, so brewing one is the same four-step sequence with a
different source id. The one difference is the display name: there is no
built-in name for a profile slot, so the category label is written to slot
401 instead.

Both formulas are confirmed against a Barista TS Smart. Reading profile 2's
espresso key:

```
TX HC 0142                                   # 0x0142 = 322 = 302 + 2*10 + 0
RX HC 0142 00 0101010300000800 0000...       # slot echoed, recipe_type 0
```

and the name registers returned the machine's own profile names. A profile
that was never named reads back as the machine's placeholder — literally
`--4--`, `--5--` and so on — not as an empty string.

## Numerical registers (`HR` / `HW`)

| ID | Meaning |
|---|---|
| 6 | machine type — 258 = Barista T, 259 = Barista TS |
| 11 | water hardness |
| 12 | energy saving |
| 13 | auto-off delay, minutes |
| 14 | auto-off mode |
| 15 | language |
| 16 | automatic bean select |
| 18 | rinse on switch-off |
| 20 | clock, minutes since midnight (read) |
| 21 | clock (write) — `hour * 60 + minute` |
| 22 | temperature |
| 91 | filter |
| 100 + recipe_type | per-drink cup counter |
| 150 | total cup counter |

Value encodings for registers 12, 14, 15, 16, 18, 22 and 91 have not been
confirmed, which is why the integration exposes them only through the raw
`read_setting` / `write_setting` services rather than as entities.

## Confidence

Verified against this implementation's tests and consistent across the
sources: framing, checksum, RC4, handshake, status layout, recipe layout,
brew sequence, recipe/counter IDs.

Confirmed on a Barista TS Smart running firmware `02590029014`: the
characteristic table above, that bonding is required, that writes take a
response, the status and recipe layouts, the numerical registers listed
above, and the direct-key and profile-name formulas.

One open question from that session: the `temperature` byte of a recipe
component. This integration labels `0` as "cold", following the reference
implementation — but a stock profile espresso reads back with `temperature =
0`, and the machine cannot brew cold espresso. The built-in espresso recipe
uses `2`. A low/medium/high scale fits the observed values better than
cold/normal/high, and the machine's own menu offers three brew temperatures.

Still unverified: `HF`/`HL`/`HP`/`HQ` contents, and the value encodings for
registers 12, 14, 15, 16, 18, 22 and 91.

## Credits

The protocol details were reconstructed from
[dzerik/melitta-barista-ha](https://github.com/dzerik/melitta-barista-ha)
(MIT licensed), whose `docs/PROTOCOL.md` documents the vendor app analysis.
The code in this repository is an independent implementation; the constants
it needs — the RC4 key and the handshake table — are protocol facts taken
from that work. See `NOTICE`.
