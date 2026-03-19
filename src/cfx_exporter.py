#!/usr/bin/env python3
"""Prometheus exporter for Dometic CFX Classic (CC3200-based) coolers.

Connects to the fridge via TCP port 6378 and exposes metrics at /metrics.
Works with older CFX WiFi models: CFX35W, CFX40W, CFX50W, CFX65W, CFX95DZW, CFX100W.

This does NOT work with newer CFX3 models (which use DDMP on port 13142).
"""

import argparse
import logging
import signal
import socket
import struct
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger("cfx-exporter")

# --- Protocol constants (reverse-engineered from com.semcon.dometic.cfx.tcp) ---

CFX_PORT = 6378
MAGIC_BYTE = 0x33
INIT_BYTE = 0xCC
PAYLOAD_LEN = 0x0C  # 12 = 15 - 3
SEND_SIZE = 15
RECV_SIZE = 20
TEMP_OFFSET = 50

# Commands
CMD_CHECK_INFO = 0x00
CMD_SET_TEMP_COMP1 = 0x01
CMD_SET_TEMP_COMP2 = 0x02
CMD_SET_ABS = 0x03
CMD_SET_CF = 0x04
CMD_TURN_ON_OFF = 0x05
CMD_SET_TEMP_CALIB = 0x06
CMD_SET_ALARM = 0x07
CMD_RESET_WIFI = 0x08

# Response byte positions
POS_INIT = 0
POS_COMMAND = 1
POS_PAYLOAD_SIZE = 2
POS_SET_TEMP_1 = 3
POS_SET_TEMP_2 = 4
POS_ABS = 5
POS_CF_COMPONOFF = 6
POS_REFRIG_ONOFF = 7
POS_TEMP_CALIB = 8
POS_ALARM = 9
POS_DC_VOLTAGE = 10
POS_TEMP_1 = 11
POS_TEMP_2 = 12
POS_COMPRESSOR = 13
POS_DOORS_AC = 14
POS_ERRORS = 15

# Bitmasks
TEMP_UNIT_MASK = 0x01
COMP1_OFF_MASK = 0x02
COMP2_OFF_MASK = 0x04
COMPRESSOR_ON_MASK = 0x02
COMPRESSOR_FOR_MASK = 0x01
DOOR_1_MASK = 0x01
DOOR_2_MASK = 0x02
AC_AVAILABLE_MASK = 0x04
DOOR_1_ALARM_MASK = 0x10
DOOR_2_ALARM_MASK = 0x20
ERR_ABS = 0x01
ERR_COMPRESSOR = 0x02
ERR_THERMOSTAT = 0x04
ERR_SOLENOID = 0x08
ERR_DOOR_OPEN = 0x10
ERR_COMP_ONE_CYCLE = 0x20


def checksum(data: bytes) -> int:
    """Calculate checksum: sum of all bytes except last, mod 256."""
    return sum(data[:-1]) & 0xFF


def build_check_info() -> bytes:
    """Build a CHECK_INFO command packet (15 bytes)."""
    pkt = bytearray([MAGIC_BYTE, CMD_CHECK_INFO, PAYLOAD_LEN,
                      0, 0, 0, 6, 0, 0, 0, 0, 0, 0, 0, 0])
    pkt[-1] = checksum(pkt)
    return bytes(pkt)


class FridgeState:
    """Parsed state from a CHECK_INFO response."""

    def __init__(self, data: bytes):
        if len(data) != RECV_SIZE:
            raise ValueError(f"Expected {RECV_SIZE} bytes, got {len(data)}")

        expected_cs = checksum(data)
        if data[-1] != expected_cs:
            raise ValueError(f"Checksum mismatch: expected 0x{expected_cs:02x}, "
                             f"got 0x{data[-1]:02x}")

        self.raw = data
        self.set_temp_1 = data[POS_SET_TEMP_1] - TEMP_OFFSET
        self.set_temp_2 = data[POS_SET_TEMP_2] - TEMP_OFFSET
        self.abs_level = data[POS_ABS]

        cf = data[POS_CF_COMPONOFF]
        self.temp_unit_fahrenheit = bool(cf & TEMP_UNIT_MASK)
        self.comp1_on = not bool(cf & COMP1_OFF_MASK)
        self.comp2_on = not bool(cf & COMP2_OFF_MASK)

        self.refrigerator_on = data[POS_REFRIG_ONOFF] != 0
        self.temp_calibration = data[POS_TEMP_CALIB]
        self.alarm_setting = data[POS_ALARM]

        # Voltage: unsigned byte / 10. Java signed byte < 40 means > 215 unsigned.
        v_raw = data[POS_DC_VOLTAGE]
        self.dc_voltage = (v_raw + 256) / 10.0 if v_raw < 40 else v_raw / 10.0

        self.temp_1 = data[POS_TEMP_1] - TEMP_OFFSET
        self.temp_2 = data[POS_TEMP_2] - TEMP_OFFSET

        comp = data[POS_COMPRESSOR]
        self.compressor_on = bool(comp & COMPRESSOR_ON_MASK)
        self.compressor_for_zone = 1 if (comp & COMPRESSOR_FOR_MASK) else 2

        doors = data[POS_DOORS_AC]
        self.door_1_open = bool(doors & DOOR_1_MASK)
        self.door_2_open = bool(doors & DOOR_2_MASK)
        self.ac_available = bool(doors & AC_AVAILABLE_MASK)
        self.door_1_alarm = bool(doors & DOOR_1_ALARM_MASK)
        self.door_2_alarm = bool(doors & DOOR_2_ALARM_MASK)

        err = data[POS_ERRORS]
        self.errors = []
        if err & ERR_ABS:
            self.errors.append("abs_error")
        if err & ERR_COMPRESSOR:
            self.errors.append("compressor_faulty")
        if err & ERR_THERMOSTAT:
            self.errors.append("thermostat_faulty")
        if err & ERR_SOLENOID:
            self.errors.append("solenoid_valve_faulty")
        if err & ERR_DOOR_OPEN:
            self.errors.append("door_open")
        if err & ERR_COMP_ONE_CYCLE:
            self.errors.append("compressor_one_cycle")
        self.error_byte = err

    def __repr__(self):
        return (f"FridgeState(temp1={self.temp_1}C, temp2={self.temp_2}C, "
                f"set1={self.set_temp_1}C, set2={self.set_temp_2}C, "
                f"voltage={self.dc_voltage}V, compressor={'on' if self.compressor_on else 'off'})")


class FridgePoller:
    """Polls the fridge periodically and caches the latest state."""

    def __init__(self, host: str, port: int = CFX_PORT, interval: float = 10.0,
                 timeout: float = 5.0):
        self.host = host
        self.port = port
        self.interval = interval
        self.timeout = timeout
        self.state: FridgeState | None = None
        self.connected = False
        self.last_poll: float = 0
        self.poll_errors: int = 0
        self.poll_success: int = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> FridgeState | None:
        """Connect, send CHECK_INFO, parse response."""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))

            cmd = build_check_info()
            sock.sendall(cmd)

            data = b""
            deadline = time.monotonic() + self.timeout
            while len(data) < RECV_SIZE and time.monotonic() < deadline:
                chunk = sock.recv(RECV_SIZE - len(data))
                if not chunk:
                    break
                data += chunk

            if len(data) != RECV_SIZE:
                raise ValueError(f"Short read: {len(data)} bytes")

            state = FridgeState(data)

            with self._lock:
                self.state = state
                self.connected = True
                self.last_poll = time.time()
                self.poll_success += 1

            logger.debug("Poll OK: %s", state)
            return state

        except Exception as e:
            with self._lock:
                self.connected = False
                self.poll_errors += 1
            logger.warning("Poll failed: %s", e)
            return None

        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def _run(self):
        """Background polling loop."""
        logger.info("Polling %s:%d every %.0fs", self.host, self.port, self.interval)
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)
        logger.info("Poller stopped")

    def start(self):
        """Start background polling thread."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop background polling."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves Prometheus metrics."""

    poller: FridgePoller  # set by factory

    def do_GET(self):
        if self.path == "/metrics":
            body = self._render_metrics()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with self.poller._lock:
                connected = self.poller.connected
            status = "ok" if connected else "disconnected"
            self.wfile.write(f'{{"status":"{status}"}}'.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def _render_metrics(self) -> str:
        lines = []

        def gauge(name, help_text, value, labels=None):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            label_str = ""
            if labels:
                pairs = ",".join(f'{k}="{v}"' for k, v in labels.items())
                label_str = f"{{{pairs}}}"
            lines.append(f"{name}{label_str} {value}")

        with self.poller._lock:
            state = self.poller.state
            connected = self.poller.connected
            poll_errors = self.poller.poll_errors
            poll_success = self.poller.poll_success

        gauge("cfx_connected", "Whether the exporter can reach the fridge",
              1 if connected else 0)

        lines.append("# HELP cfx_poll_errors_total Total number of failed polls")
        lines.append("# TYPE cfx_poll_errors_total counter")
        lines.append(f"cfx_poll_errors_total {poll_errors}")

        lines.append("# HELP cfx_poll_success_total Total number of successful polls")
        lines.append("# TYPE cfx_poll_success_total counter")
        lines.append(f"cfx_poll_success_total {poll_success}")

        if state is None:
            return "\n".join(lines) + "\n"

        # Temperatures
        gauge("cfx_temperature_celsius",
              "Current compartment temperature in Celsius",
              state.temp_1, {"zone": "1"})
        gauge("cfx_temperature_celsius",
              "Current compartment temperature in Celsius",
              state.temp_2, {"zone": "2"})

        gauge("cfx_set_temperature_celsius",
              "Target compartment temperature in Celsius",
              state.set_temp_1, {"zone": "1"})
        gauge("cfx_set_temperature_celsius",
              "Target compartment temperature in Celsius",
              state.set_temp_2, {"zone": "2"})

        # Power
        gauge("cfx_dc_voltage", "DC supply voltage", f"{state.dc_voltage:.1f}")
        gauge("cfx_ac_available", "Whether AC power is available",
              1 if state.ac_available else 0)

        # Compressor
        gauge("cfx_compressor_running", "Whether the compressor is running",
              1 if state.compressor_on else 0)
        if state.compressor_on:
            gauge("cfx_compressor_zone",
                  "Which zone the compressor is cooling (1 or 2)",
                  state.compressor_for_zone)

        # Refrigerator state
        gauge("cfx_refrigerator_on", "Whether the refrigerator is powered on",
              1 if state.refrigerator_on else 0)
        gauge("cfx_compartment_on", "Whether a compartment is enabled",
              1 if state.comp1_on else 0, {"zone": "1"})
        gauge("cfx_compartment_on", "Whether a compartment is enabled",
              1 if state.comp2_on else 0, {"zone": "2"})

        # Doors
        gauge("cfx_door_open", "Whether a compartment door is open",
              1 if state.door_1_open else 0, {"zone": "1"})
        gauge("cfx_door_open", "Whether a compartment door is open",
              1 if state.door_2_open else 0, {"zone": "2"})

        # Settings
        gauge("cfx_battery_protection_level",
              "Battery protection level (0=off, 1=low, 2=medium)",
              state.abs_level)
        gauge("cfx_alarm_setting",
              "Temperature alarm (0=off, 1=+/-3C, 2=+/-5C)",
              state.alarm_setting)

        # Errors
        gauge("cfx_error_flags", "Error code bitmask from fridge",
              state.error_byte)

        return "\n".join(lines) + "\n"

    def log_message(self, format, *args):
        """Suppress default access logs unless debug."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(format, *args)


def make_handler(poller: FridgePoller):
    """Create a MetricsHandler class bound to a specific poller."""
    handler = type("BoundMetricsHandler", (MetricsHandler,), {"poller": poller})
    return handler


def main():
    parser = argparse.ArgumentParser(
        description="Prometheus exporter for Dometic CFX Classic coolers")
    parser.add_argument("--host", default="192.168.1.1",
                        help="Fridge IP address (default: 192.168.1.1)")
    parser.add_argument("--port", type=int, default=CFX_PORT,
                        help=f"Fridge TCP port (default: {CFX_PORT})")
    parser.add_argument("--interval", type=float, default=10.0,
                        help="Poll interval in seconds (default: 10)")
    parser.add_argument("--listen-port", type=int, default=9124,
                        help="HTTP port for /metrics (default: 9124)")
    parser.add_argument("--listen-addr", default="0.0.0.0",
                        help="HTTP listen address (default: 0.0.0.0)")
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="TCP connection timeout (default: 5s)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    poller = FridgePoller(
        host=args.host,
        port=args.port,
        interval=args.interval,
        timeout=args.timeout,
    )

    def shutdown(signum, frame):
        logger.info("Shutting down...")
        poller.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    poller.start()

    handler = make_handler(poller)
    server = HTTPServer((args.listen_addr, args.listen_port), handler)
    logger.info("Serving metrics on http://%s:%d/metrics",
                args.listen_addr, args.listen_port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        server.server_close()


if __name__ == "__main__":
    main()
