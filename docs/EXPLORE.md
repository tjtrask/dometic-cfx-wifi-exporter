# Dometic CFX Classic WiFi — Protocol Exploration

## Device Under Test

- **Model**: Dometic CFX 95DZW (dual-zone, WiFi-only — older generation)
- **WiFi Chip**: Texas Instruments CC3200 SimpleLink
- **WiFi Mode**: Access Point (AP) — SSID: `CFX_84F0CD44`
- **Default Password**: `00000000`
- **MAC**: `C4:BE:84:F0:CD:44` (TI OUI, last 3 bytes match SSID suffix)
- **AP IP**: `192.168.1.1` (DHCP range .2-.254, /24)
- **Firmware**: `2.4.0.002_31.1.3.0.1_1.0.3.22_0x4000010`
- **WiFi Modes Supported**: AP, Station (can join existing WiFi), P2P

## Key Finding: NOT the CFX3 Protocol

The newer CFX3 generation uses an ESP32 with DDMP (JSON over TCP port 13142).
The older CFX (our model) uses a **TI CC3200** with a completely different protocol:

- **Port 80**: Stock TI SimpleLink HTTP server (WiFi config pages only, no fridge data)
- **Port 6378**: Custom binary command/response protocol for fridge data
- **No other TCP ports open** (full 1-65535 scan confirmed)

Existing open-source projects (DanielLeone/dometic-cfx-exporter, keshavdv/dometic-cfx3,
andrewbackway/esphome-dometic_cfx_wifi) all target the CFX3/DDMP protocol and do NOT
work with this older generation. **This is new reverse-engineering work.**

## Android App

- **Package**: `com.zhenbang.project_532` (older "CFX" app, NOT the "CFX3" app)
- **Developer**: Zhenbang / Semcon for Dometic Group AB
- **Supported models**: CFX35W, CFX40W, CFX50W, CFX65W, CFX95DZW, CFX100W
- **Key class**: `com.semcon.dometic.cfx.tcp.CfxTcpCommunication`
- **APK saved**: `apk/cfx-app.apk` (gitignored)
- **Decompiled source**: `apk/decompiled/` (gitignored)

## Binary Protocol (Port 6378) — FULLY REVERSE-ENGINEERED

### Transport
- Raw TCP socket to fridge IP on port 6378
- Binary command/response — NOT HTTP, NOT JSON, NOT DDMP
- Checksum: **simple sum** of all bytes except last, truncated to uint8

### Send Packet (15 bytes)

```
Byte:  [  0  ][  1  ][  2  ][  3  ][  4  ][  5  ][ 6 ][ 7 ][ 8 ][ 9 ][ 10][ 11][ 12][ 13][ 14 ]
Field: [MAGIC][ CMD ][ LEN ][ D3  ][ D4  ][ D5  ][ D6][ D7][ D8][ D9][D10][D11][D12][D13][ CKS ]
Value: [ 0x33][ var ][ 0x0C][ ... payload data ...                                      ][ sum ]
```

- **Byte 0**: Magic byte `0x33` (51 decimal)
- **Byte 1**: Command byte
- **Byte 2**: Payload length `0x0C` (12 = total_length - 3)
- **Bytes 3-13**: Payload (command-specific, defaults to `[0,0,0,6,0,0,0,0,0,0,0]`)
- **Byte 14**: Checksum = `sum(bytes[0:14]) & 0xFF`

### Default send buffer
```
Decimal: [51, 0, 12, 0, 0, 0, 6, 0, 0, 0, 0, 0, 0, 0, CHECKSUM]
Hex:     [33, 00, 0C, 00, 00, 00, 06, 00, 00, 00, 00, 00, 00, 00, CHECKSUM]
```

### Commands (byte 1 values)

| Value | Name | Payload fields used |
|-------|------|---------------------|
| 0x00 | CHECK_INFO | None (just polls state) |
| 0x01 | SET_TEMP_COMP1 | byte[3]=temp+50, byte[6]=cf_flags |
| 0x02 | SET_TEMP_COMP2 | byte[4]=temp+50, byte[6]=cf_flags |
| 0x03 | SET_ABS | byte[5]=level (0-2) |
| 0x04 | SET_CF | byte[6]=0(C)/1(F) |
| 0x05 | TURN_ON_OFF | byte[7]=0(off)/1(on) |
| 0x06 | SET_TEMP_CALIB | byte[8]=calibration |
| 0x07 | SET_ALARM | byte[9]=level (0=off, 1=+/-3C, 2=+/-5C) |
| 0x08 | RESET_WIFI | (special format, see WiFi section) |

### Response Packet (20 bytes)

```
Byte:  [  0 ][  1 ][  2 ][ 3 ][ 4 ][ 5 ][  6  ][ 7 ][ 8 ][ 9 ][ 10 ][ 11 ][ 12 ][ 13 ][ 14  ][ 15 ][ 16][ 17][ 18][ 19 ]
Field: [INIT][ CMD][ LEN][ST1][ST2][ABS ][CF/CO][REF][CAL][ALM][ VDC][TMP1][TMP2][COMP][DOOR ][ERR ][UNK][UNK][UNK][ CKS]
```

| Byte | Field | Decoding |
|------|-------|----------|
| 0 | Initialization | 0xCC (204) when operational |
| 1 | Command echo | Should match sent command |
| 2 | Payload length | 0x11 (17 = 20 - 3) |
| 3 | Set temp comp 1 | `value - 50` = Celsius |
| 4 | Set temp comp 2 | `value - 50` = Celsius |
| 5 | ABS level | 0=off, 1=low, 2=medium |
| 6 | CF/CompOnOff | Bitfield (see below) |
| 7 | Refrigerator on | 0=off, 1=on |
| 8 | Temp calibration | Raw offset value |
| 9 | Alarm setting | 0=off, 1=+/-3C, 2=+/-5C |
| 10 | DC voltage | `value / 10.0` volts (unsigned; if <40, add 256) |
| 11 | Temp comp 1 | `value - 50` = Celsius |
| 12 | Temp comp 2 | `value - 50` = Celsius |
| 13 | Compressor | Bitfield (see below) |
| 14 | Doors/AC | Bitfield (see below) |
| 15 | Error codes | Bitfield (see below) |
| 16-18 | Unknown | Always 0x00 in our tests |
| 19 | Checksum | `sum(bytes[0:19]) & 0xFF` |

### Bitfield Decoding

**Byte 6 — CF/CompOnOff:**
- Bit 0 (0x01): Temp unit — 0=Celsius, 1=Fahrenheit
- Bit 1 (0x02): Compartment 1 OFF (1=off, 0=on, inverted!)
- Bit 2 (0x04): Compartment 2 OFF (1=off, 0=on, inverted!)

**Byte 13 — Compressor:**
- Bit 0 (0x01): Working for compartment — 1=comp1, 0=comp2
- Bit 1 (0x02): Compressor ON

**Byte 14 — Doors/AC:**
- Bit 0 (0x01): Door 1 open
- Bit 1 (0x02): Door 2 open
- Bit 2 (0x04): AC power available
- Bit 4 (0x10): Door 1 alarm (open too long)
- Bit 5 (0x20): Door 2 alarm (open too long)

**Byte 15 — Error codes:**
- Bit 0 (0x01): ABS error (battery protection triggered)
- Bit 1 (0x02): Compressor faulty
- Bit 2 (0x04): Thermostat faulty
- Bit 3 (0x08): Solenoid valve faulty
- Bit 4 (0x10): Door open error
- Bit 5 (0x20): Compressor worked for one cycle

### Temperature Encoding
- All temperatures: `raw_byte - 50 = Celsius`
- Range: -50C to +77C (raw 0-127, note: byte is signed in Java, unsigned here)
- When setting temp, send `celsius + 50`

### Voltage Encoding
- Raw byte / 10.0 = volts
- If raw < 40, add 256 first (unsigned byte handling from signed Java byte)
- Example: raw=126 → 12.6V

### Checksum Algorithm
```python
def checksum(packet):
    return sum(packet[:-1]) & 0xFF
```
Simple sum of all bytes except the last, modulo 256.

## Live Test Results (2026-03-19)

First successful communication with live CFX 95DZW:

```
SEND: 33 00 0c 00 00 00 06 00 00 00 00 00 00 00 45
RECV: cc 00 11 34 34 00 00 01 0a 00 7e 35 34 03 00 20 00 00 00 5a
```

**Parsed:**
| Field | Value |
|-------|-------|
| Zone 1 temperature | 3 C (set: 2 C) |
| Zone 2 temperature | 2 C (set: 2 C) |
| DC voltage | 12.6 V |
| Compressor | ON, working for zone 1 |
| Doors | Both closed |
| AC power | Not available (DC only) |
| Temp unit | Celsius |
| Refrigerator | ON |
| Battery protection | OFF |
| Alarm | OFF |
| Errors | COMPRESSOR_ONE_CYCLE (0x20) |

## WiFi Credential Change (Special Command)

Uses a different packet format:
```
[0x33] [0xEE] [ssid_len] [pass_len] [ssid_bytes...] [pass_bytes...]
```
Expected success response: `[0xCC, 0xEE, 0x02, 0x01, 0xBD]`

## Station Mode

The fridge supports joining an existing WiFi network (Station mode) via
`dev_config.html`. This means it could join the truck's Peplink WiFi directly,
eliminating the need for a dedicated WiFi bridge on frontpi.

**Plan**: Develop and test in AP mode now. Switch to Station mode at home and
verify before publishing — both modes should work for open-source users.

## HTTP Server (Port 80) — For Reference Only

Stock TI SimpleLink pages. No fridge data here.

| Path | Purpose |
|---|---|
| `/` | Entry page (iframe to status.html) |
| `/main.html` | Same as `/` |
| `/status.html` | Device info, MAC, SSID, IP, version |
| `/dev_config.html` | Device name, mode (AP/Station/P2P), SSID config |
| `/ip_config.html` | IP settings |
| `/profiles_config.html` | Saved WiFi profiles |
| `/tools.html` | Ping test |
| `/date_time_info.txt` | Current date/time (CSV) |
| `/sys_up_time.txt` | System uptime |
