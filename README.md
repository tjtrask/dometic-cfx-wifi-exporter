# dometic-cfx-wifi-exporter

Prometheus exporter for **older Dometic CFX WiFi coolers** (CFX35W, CFX40W, CFX50W, CFX65W, CFX95DZW, CFX100W).

These models use a TI CC3200 WiFi chip with a proprietary binary protocol on TCP port 6378. This is **not** the same as the newer CFX3 models, which use an ESP32 with the DDMP JSON protocol on port 13142.

## Metrics

| Metric | Labels | Description |
|--------|--------|-------------|
| `cfx_connected` | | Exporter can reach the fridge (0/1) |
| `cfx_temperature_celsius` | `zone` | Current compartment temperature |
| `cfx_set_temperature_celsius` | `zone` | Target compartment temperature |
| `cfx_compartment_on` | `zone` | Whether a compartment is enabled (0/1) |
| `cfx_door_open` | `zone` | Whether a compartment door is open (0/1) |
| `cfx_dc_voltage` | | DC supply voltage |
| `cfx_ac_available` | | Whether AC power is available (0/1) |
| `cfx_compressor_running` | | Whether the compressor is running (0/1) |
| `cfx_compressor_zone` | | Which zone the compressor is cooling (1 or 2) |
| `cfx_refrigerator_on` | | Whether the fridge is powered on (0/1) |
| `cfx_battery_protection_level` | | Battery protection (0=off, 1=low, 2=medium) |
| `cfx_alarm_setting` | | Temperature alarm (0=off, 1=+/-3C, 2=+/-5C) |
| `cfx_error_flags` | | Error code bitmask |
| `cfx_poll_errors_total` | | Total failed polls (counter) |
| `cfx_poll_success_total` | | Total successful polls (counter) |

## Requirements

- Python 3.8+ (stdlib only, zero dependencies)
- Network access to the fridge's WiFi (AP mode or Station mode)

## Usage

```bash
# Connect to the fridge's WiFi AP (default SSID: CFX_XXXXXXXX, password: 00000000)
# Then run:
python3 src/cfx_exporter.py --host 192.168.1.1

# If fridge is in Station mode on your LAN:
python3 src/cfx_exporter.py --host <fridge-ip-on-lan>
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `192.168.1.1` | Fridge IP address |
| `--port` | `6378` | Fridge TCP port |
| `--interval` | `10` | Poll interval (seconds) |
| `--listen-port` | `9124` | HTTP port for /metrics |
| `--listen-addr` | `0.0.0.0` | HTTP listen address |
| `--timeout` | `5` | TCP connection timeout (seconds) |
| `--debug` | | Enable debug logging |

### Prometheus scrape config

```yaml
scrape_configs:
  - job_name: "cfx-exporter"
    static_configs:
      - targets: ["<exporter-host>:9124"]
    scrape_interval: 15s
```

### systemd service

```ini
[Unit]
Description=Dometic CFX WiFi Exporter
After=network-online.target

[Service]
Type=simple
User=<your-user>
ExecStart=/usr/bin/python3 /opt/cfx-exporter/cfx_exporter.py --host 192.168.1.1
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Supported Models

Tested on CFX 95DZW. Should work with all older CFX W-suffix models that use the "CFX" Android app (com.zhenbang.project_532):

- CFX 35W
- CFX 40W
- CFX 50W
- CFX 65W
- CFX 95DZW
- CFX 100W

**Does NOT work with:** CFX3, CFX2, or CFX5 models (these use a different protocol — see [dometic-cfx-exporter](https://github.com/DanielLeone/dometic-cfx-exporter) for those).

## Protocol

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the full reverse-engineered protocol specification.

The communication protocol was independently determined through interoperability research of the Dometic CFX mobile application pursuant to 17 U.S.C. Section 1201(f).

## Disclaimer

This project is not affiliated with, endorsed by, or connected to Dometic Group AB. Use at your own risk. The authors assume no liability for any damage to your equipment.

## License

MIT
