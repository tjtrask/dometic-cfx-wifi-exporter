# Dometic CFX Classic — WiFi Protocol Specification

This document describes the communication protocol used by older Dometic CFX WiFi coolers
(CFX W-suffix models). The protocol was independently determined through interoperability
research pursuant to 17 U.S.C. Section 1201(f).

## Overview

- **Transport**: Raw TCP socket
- **Port**: 6378
- **WiFi Chip**: Texas Instruments CC3200 SimpleLink
- **AP Mode Default IP**: 192.168.1.1
- **AP Mode Default Password**: 00000000
- **Format**: Binary command/response, fixed-length packets
- **Checksum**: Simple byte sum, mod 256

## Send Packet (15 bytes)

```
Byte:  [  0  ][  1  ][  2  ][  3  ][  4  ][  5  ][ 6 ][ 7 ][ 8 ][ 9 ][ 10][ 11][ 12][ 13][ 14 ]
Field: [MAGIC][ CMD ][ LEN ][ D3  ][ D4  ][ D5  ][ D6][ D7][ D8][ D9][D10][D11][D12][D13][ CKS ]
Value: [ 0x33][ var ][ 0x0C][ ... payload data ...                                      ][ sum ]
```

| Byte | Field | Description |
|------|-------|-------------|
| 0 | Magic | Always `0x33` (51) |
| 1 | Command | Command byte (see table below) |
| 2 | Length | Payload length: `0x0C` (12 = packet_size - 3) |
| 3-13 | Payload | Command-specific data (defaults: `[0,0,0,6,0,0,0,0,0,0,0]`) |
| 14 | Checksum | `sum(bytes[0:14]) & 0xFF` |

### Default send buffer

```
Hex:     33 00 0C 00 00 00 06 00 00 00 00 00 00 00 [checksum]
```

## Commands

| Byte 1 | Name | Payload fields |
|--------|------|----------------|
| `0x00` | CHECK_INFO | None — polls full state |
| `0x01` | SET_TEMP_COMP1 | byte[3] = temp+50, byte[6] = cf_flags |
| `0x02` | SET_TEMP_COMP2 | byte[4] = temp+50, byte[6] = cf_flags |
| `0x03` | SET_ABS | byte[5] = level (0-2) |
| `0x04` | SET_CF | byte[6] = 0(Celsius) / 1(Fahrenheit) |
| `0x05` | TURN_ON_OFF | byte[7] = 0(off) / 1(on) |
| `0x06` | SET_TEMP_CALIB | byte[8] = calibration offset |
| `0x07` | SET_ALARM | byte[9] = 0(off) / 1(+/-3C) / 2(+/-5C) |
| `0x08` | RESET_WIFI | Special format (see below) |

## Response Packet (20 bytes)

```
Byte:  [  0 ][  1 ][  2 ][ 3 ][ 4 ][ 5 ][  6  ][ 7 ][ 8 ][ 9 ][ 10 ][ 11 ][ 12 ][ 13 ][ 14  ][ 15 ][ 16][ 17][ 18][ 19 ]
Field: [INIT][ CMD][ LEN][ST1][ST2][ABS ][CF/CO][REF][CAL][ALM][ VDC][TMP1][TMP2][COMP][DOOR ][ERR ][UNK][UNK][UNK][ CKS]
```

| Byte | Field | Decoding |
|------|-------|----------|
| 0 | Initialization | `0xCC` (204) when operational |
| 1 | Command echo | Should match the sent command |
| 2 | Payload length | `0x11` (17 = 20 - 3) |
| 3 | Set temp zone 1 | `value - 50` = Celsius |
| 4 | Set temp zone 2 | `value - 50` = Celsius |
| 5 | Battery protection | 0=off, 1=low, 2=medium |
| 6 | CF/CompOnOff | Bitfield (see below) |
| 7 | Refrigerator on | 0=off, 1=on |
| 8 | Temp calibration | Raw offset |
| 9 | Alarm setting | 0=off, 1=+/-3C, 2=+/-5C |
| 10 | DC voltage | `value / 10.0` volts (if value < 40, use `(value + 256) / 10.0`) |
| 11 | Current temp zone 1 | `value - 50` = Celsius |
| 12 | Current temp zone 2 | `value - 50` = Celsius |
| 13 | Compressor | Bitfield (see below) |
| 14 | Doors/AC | Bitfield (see below) |
| 15 | Error codes | Bitfield (see below) |
| 16-18 | Reserved | Always `0x00` in testing |
| 19 | Checksum | `sum(bytes[0:19]) & 0xFF` |

## Bitfield Decoding

### Byte 6 — CF/CompOnOff

| Bit | Mask | Meaning |
|-----|------|---------|
| 0 | `0x01` | Temperature unit: 0=Celsius, 1=Fahrenheit |
| 1 | `0x02` | Compartment 1 OFF (inverted: 1=disabled, 0=enabled) |
| 2 | `0x04` | Compartment 2 OFF (inverted: 1=disabled, 0=enabled) |

### Byte 13 — Compressor

| Bit | Mask | Meaning |
|-----|------|---------|
| 0 | `0x01` | Working for compartment: 1=zone 1, 0=zone 2 |
| 1 | `0x02` | Compressor ON |

### Byte 14 — Doors/AC

| Bit | Mask | Meaning |
|-----|------|---------|
| 0 | `0x01` | Door 1 open |
| 1 | `0x02` | Door 2 open |
| 2 | `0x04` | AC power available |
| 4 | `0x10` | Door 1 alarm (open too long) |
| 5 | `0x20` | Door 2 alarm (open too long) |

### Byte 15 — Error Codes

| Bit | Mask | Meaning |
|-----|------|---------|
| 0 | `0x01` | Battery protection (ABS) error |
| 1 | `0x02` | Compressor faulty |
| 2 | `0x04` | Thermostat faulty |
| 3 | `0x08` | Solenoid valve faulty |
| 4 | `0x10` | Door open error |
| 5 | `0x20` | Compressor worked for one cycle only |

## Data Encoding

### Temperature

All temperature values use offset encoding:
- **Decode**: `raw_byte - 50 = Celsius`
- **Encode**: `celsius + 50 = raw_byte`
- **Range**: -50°C to +77°C

### Voltage

- **Decode**: `raw_byte / 10.0 = volts`
- If `raw_byte < 40`: `(raw_byte + 256) / 10.0` (unsigned byte correction)
- Example: raw 126 → 12.6V, raw 5 → 26.1V

### Checksum

```python
def checksum(packet: bytes) -> int:
    return sum(packet[:-1]) & 0xFF
```

Sum of all bytes except the last, modulo 256.

## WiFi Credential Change (Command 0x08)

Uses a special variable-length format:

```
[0x33] [0xEE] [ssid_length] [password_length] [ssid_bytes...] [password_bytes...]
```

Expected success response: `CC EE 02 01 BD`

## WiFi Modes

The CC3200 supports three modes configurable via the HTTP interface at port 80:

- **AP mode** (default): Fridge creates its own WiFi network (SSID: `CFX_XXXXXXXX`)
- **Station mode**: Fridge joins an existing WiFi network
- **P2P mode**: Peer-to-peer WiFi Direct

Mode is changed via HTTP POST to `http://<fridge-ip>/mode_config` on port 80.
The data protocol on port 6378 works identically in all modes.

## Example Session

```
→ SEND (15 bytes): 33 00 0C 00 00 00 06 00 00 00 00 00 00 00 45
← RECV (20 bytes): CC 00 11 34 34 00 00 01 0A 00 7E 35 34 03 00 20 00 00 00 5A

Parsed response:
  Zone 1: 3°C (set: 2°C)
  Zone 2: 2°C (set: 2°C)
  DC Voltage: 12.6V
  Compressor: ON (cooling zone 1)
  Doors: both closed
  AC: not available
  Fridge: ON
  Errors: 0x20 (compressor one cycle)
```
