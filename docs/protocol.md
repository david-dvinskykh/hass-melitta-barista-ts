# Melitta Barista Smart BLE protocol

Notes on the protocol used by the Melitta Caffeo Barista T/TS Smart, as
implemented in `custom_components/melitta_barista_ts/protocol.py`. The machines
use the Eugster/Frismag OEM Bluetooth stack, which is shared with other brands
built on the same platform.

Nothing here is official. It comes from reverse engineering the vendor Android
app, cross-checked against the community work linked at the bottom.

## Advertisement and GATT

The machine advertises a local name made of its four-digit article-number
prefix followed by a hex device id, e.g. `8604A1B2C3`.

| Prefix | Model |
| --- | --- |
| `8301`, `8311`, `8401` | Barista T Smart |
| `8501`, `8601`, `8604` | Barista TS Smart |

| UUID | Role |
| --- | --- |
| `0000ad00-b35c-11e4-9813-0002a5d5c51b` | vendor service |
| `0000ad02-b35c-11e4-9813-0002a5d5c51b` | notify (machine → client) |
| `0000ad03-b35c-11e4-9813-0002a5d5c51b` | write (client → machine) |

The MTU is not negotiated: every frame is written in 20-byte chunks with
write-without-response, and notifications arrive in 20-byte chunks too.

## Frame format

```
  0x53 ('S')  opcode (1-2 ASCII)  RC4( [session key] payload checksum )  0x45 ('E')
```

* The opcode stays in clear text; everything between it and the terminator is
  RC4 encrypted with a fixed 32-byte key.
* `checksum` is the one's complement of the sum of the opcode bytes and the
  payload: `(~sum) & 0xFF`. The session key is *not* part of the sum.
* The session key (2 bytes, from the handshake) prefixes the payload of every
  frame after the handshake.

Because the body is ciphertext, a `0x45` byte can occur inside it. A frame is
therefore only considered complete when a terminator arrives **and** the buffer
length matches the length declared for that opcode. A partial frame older than
one second is discarded so the stream can resynchronise.

### Frames the machine sends

| Opcode | Payload | Encrypted | Meaning |
| --- | --- | --- | --- |
| `A` | 0 | no | acknowledged |
| `N` | 0 | no | rejected |
| `HA` | 66 | yes | alphanumeric register |
| `HC` | 66 | yes | recipe |
| `HI` | 10 | yes | capability bits |
| `HL` | 20 | yes | serial number |
| `HR` | 6 | yes | numerical register |
| `HU` | 8 | yes | handshake reply |
| `HV` | 11 | yes | firmware version |
| `HX` | 8 | yes | status |

`HF`, `HP` and `HQ` also arrive with fixed lengths but are not decoded here.

## Encryption

The RC4 key is a fixed ASCII string that the vendor app ships as an
AES-128-CBC blob. `const.py` embeds the decrypted key and keeps the original
key material next to it; `tests/test_protocol.py` re-derives the key from that
material so the literal cannot drift.

## Handshake (`HU`)

1. The client sends four random challenge bytes plus a two-byte verifier over
   them, without a session key.
2. The machine replies with `challenge(4) + session_key(2) + verifier(2)`,
   where the verifier covers the first six bytes of its own reply.
3. Every later frame carries `session_key` in front of its payload.

The verifier is two independent folds through a fixed 256-byte substitution
table, seeded one apart, each finished with an additive constant (93 and 167).
Both sides checking it proves each knows the table.

A reply is rejected unless the echoed challenge matches what was sent *and* the
verifier is correct — otherwise a bad session key would silently break every
subsequent frame.

## Commands

| Opcode | Direction | Payload | Purpose |
| --- | --- | --- | --- |
| `HR` | read | register (int16 BE) | read a numerical register |
| `HW` | write | register (int16) + value (int32) | write a numerical register |
| `HA` | read | register (int16) | read a text register |
| `HB` | write | register (int16) + 64 bytes | write a text register |
| `HC` | read | recipe id (int16) | read a stored recipe |
| `HJ` | write | 66 bytes, see below | write a recipe |
| `HE` | write | 18 bytes, see below | start a process |
| `HZ` | write | process (int16) + 2 zero bytes | cancel a process |
| `HY` | write | 4 zero bytes | confirm the on-screen prompt |
| `HD` | write | register (int16) | reset a register to factory default |
| `HV` / `HL` / `HI` | read | none | version / serial / capability bits |
| `HX` | read | none | status |

Write commands are answered with `A` or `N`, not with a data frame.

### Status (`HX`, 8 bytes)

| Offset | Type | Field |
| --- | --- | --- |
| 0 | int16 BE | process |
| 2 | int16 BE | sub-process |
| 4 | uint8 | info-message bits |
| 5 | uint8 | required user action |
| 6 | int16 BE | progress, 0-100 |

Process: 2 ready, 4 preparing, 9 cleaning, 10 descaling, 11/12/13 filter
insert/replace/remove, 16 switching off, 17 easy clean, 19 intensive clean,
20 rinsing, 99 busy.

Sub-process: 1 grinding, 2 coffee, 3 steam, 4 water, 5 preparing.

Info bits: `1` fill hopper 1, `2` fill hopper 2, `4` easy clean due, `8` ground
coffee filled, `16` preparation cancelled.

Required action: 0 none, 1 brewing unit removed, 2 trays missing, 3 empty
trays, 4 fill water, 5 close ground-coffee lid, 6 add ground coffee, 11 move
cup to frother, 20 rinsing required. Codes 11 and 20 can be cleared over BLE
with `HY`; the others need physical intervention.

### Recipes

A recipe is a type byte plus two 8-byte components:

| Offset | Field |
| --- | --- |
| 0 | process — 0 none, 1 coffee, 2 milk, 3 water |
| 1 | shots |
| 2 | bean hopper — 0 auto, 1, 2 (TS only) |
| 3 | strength, 0-4 |
| 4 | aroma |
| 5 | temperature, 0-2 |
| 6 | portion, in 5 ml steps |
| 7 | reserved |

`HC` responses are `id(2) + type(1) + component1(8) + component2(8)`. The `HJ`
write payload inserts a drink-family byte after the type — `id(2) + type(1) +
key(1) + component1(8) + component2(8)`, zero padded to 66 bytes.

Recipe ids 200-223 are the built-in drinks. Ids `302 + profile*10 + family`
hold each profile's customised version of a drink family. Id 400 is a scratch
slot and 401 the name shown while it is being prepared.

### Brewing

There is no "brew drink N" command. Preparing a drink is three steps:

1. `HC` the recipe you want (either the built-in id, or the active profile's
   slot for that drink family).
2. `HJ` it into slot 400, including the drink-family byte.
3. `HB` a display name into register 401.
4. `HE` with process 4.

Short pauses between the steps matter — the firmware drops frames that arrive
back-to-back during this sequence.

`HE` payload: `process(2) + 0x0002(2) + zeros(4) + two_cups(2) + zeros(8)`.
Setting the two-cups flag makes the machine prepare the drink twice.

Maintenance programs use the same `HE` command with their own process value.

### Registers

Settings: 11 water hardness (1-4), 12 energy saving, 13 auto-off delay in
minutes, 14 auto-off mode, 15 language, 16 automatic bean select (TS only),
18 rinsing disabled, 20 clock (read), 21 clock (write), 22 brew temperature,
91 water filter.

The clock is minutes since midnight, and is read and written through different
registers.

Counters: `100 + recipe_type` per drink, 150 for the grand total.

Profile names: `310 + (n-1) * 10` for profile *n*.

## Credits

The protocol description above builds on
[dzerik/melitta-barista-ha](https://github.com/dzerik/melitta-barista-ha)
(MIT licensed), which documented the Eugster/Frismag stack for Melitta and
Nivona machines, and on the discussion in the
[Home Assistant community thread](https://community.home-assistant.io/t/melitta-barista-ts-smart-coffeemachine/448204).
