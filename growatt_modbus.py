#!/usr/bin/env python3
"""Poll Growatt SPH inverters over Modbus TCP (via an EW11 RS485 bridge) and
publish the decoded registers to MQTT, with optional Home Assistant discovery.

Configuration lives in config.yaml (see config.yaml.example). The Modbus decode,
client helpers and config loading live in the shared ``growatt`` package so the
control side can reuse them.
"""

import os
import re
import time
import json
import signal
import logging

import paho.mqtt.client as mqtt
from pymodbus.client import ModbusTcpClient

from growatt.config import load_config, device_framer
from growatt.registers import SENSOR_META
from growatt.client import get_inverter_serial_number, sync_inverter_time
from growatt.monitor import (
    read_inverter_holding_registers,
    read_inverter_input_registers,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("growatt")

# Set by the signal handler so the main loop can exit cleanly on docker stop.
_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    log.info("Received signal %s, shutting down after this cycle", signum)
    _shutdown = True


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------
def make_mqtt_client(mqtt_cfg):
    """Create and connect a persistent MQTT client."""
    client = mqtt.Client(client_id="growatt", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    if mqtt_cfg.get("username"):
        client.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password"))
    client.connect(mqtt_cfg["broker"], mqtt_cfg["port"])
    client.loop_start()
    return client


# Read-health counters published to a separate per-device diagnostics topic (every cycle,
# including failed ones). Lets us watch how often / when a dongle garbles or drops frames,
# so we can decide whether to swap it back for the (very reliable) EW11.
# key -> (HA name, state_class). Totals are monotonic counters; HA handles restarts.
DIAGNOSTIC_SENSORS = {
    "readErrorsTotal":  ("Garbled reads", "total_increasing"),
    "pollSkippedTotal": ("Skipped polls", "total_increasing"),
    "pollOkTotal":      ("Successful polls", "total_increasing"),
    "lastCycleRetries": ("Last poll retries", "measurement"),
}


def diagnostics_topic(mqtt_cfg, serial):
    return f"{mqtt_cfg['topic_prefix']}/{serial}/diagnostics"


def publish_discovery(client, mqtt_cfg, serial, device_name):
    """Publish Home Assistant MQTT discovery configs for known sensors."""
    prefix = mqtt_cfg["discovery_prefix"]
    state_topic = f"{mqtt_cfg['topic_prefix']}/{serial}/state"
    device = {
        "identifiers": [serial],
        "manufacturer": "Growatt",
        "name": f"Growatt {device_name or serial}",
    }
    for key, (name, device_class, unit, state_class) in SENSOR_META.items():
        config = {
            "name": name,
            "unique_id": f"{serial}_{key}",
            "object_id": f"growatt_{serial}_{key}",
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "state_class": state_class,
            "device": device,
        }
        if device_class:
            config["device_class"] = device_class
        if unit:
            config["unit_of_measurement"] = unit
        topic = f"{prefix}/sensor/{serial}_{key}/config"
        client.publish(topic, json.dumps(config), retain=True)
    # Read-health diagnostics: separate state topic, marked as diagnostic entities.
    diag_topic = diagnostics_topic(mqtt_cfg, serial)
    for key, (name, state_class) in DIAGNOSTIC_SENSORS.items():
        config = {
            "name": name,
            "unique_id": f"{serial}_{key}",
            "object_id": f"growatt_{serial}_{key}",
            "state_topic": diag_topic,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "state_class": state_class,
            "entity_category": "diagnostic",
            "device": device,
        }
        topic = f"{prefix}/sensor/{serial}_{key}/config"
        client.publish(topic, json.dumps(config), retain=True)
    log.info("Published HA discovery for %s (%d sensors + %d diagnostics)",
             serial, len(SENSOR_META), len(DIAGNOSTIC_SENSORS))


def publish_diagnostics(client, mqtt_cfg, st):
    """Publish a device's read-health counters (retained) so HA/Grafana can track flakiness.

    No-op until we have resolved the serial at least once, since the diagnostic entities hang
    off the HA device keyed by serial; counts accrued before then publish on the next cycle
    that does resolve it.
    """
    serial = st.get("serial")
    if not serial:
        return
    payload = {
        "serialNumber": serial,
        "readErrorsTotal": st["readErrorsTotal"],
        "pollSkippedTotal": st["pollSkippedTotal"],
        "pollOkTotal": st["pollOkTotal"],
        "lastCycleRetries": st["lastCycleRetries"],
    }
    client.publish(diagnostics_topic(mqtt_cfg, serial), json.dumps(payload), retain=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def poll_device(dev, config, mqtt_client, discovered, stats):
    """Poll a single inverter, publish its data, and track read-health counters."""
    host = dev["host"]
    st = stats.setdefault(host, {
        "serial": None,
        "readErrorsTotal": 0,
        "pollSkippedTotal": 0,
        "pollOkTotal": 0,
        "lastCycleRetries": 0,
    })
    mqtt_cfg = config["mqtt"]

    # retries + a slightly longer timeout: raw RTU-over-TCP dongles occasionally drop or
    # garble a frame (no on-device retry/reassembly like the EW11 had).
    client = ModbusTcpClient(host=host, port=dev.get("port", 502),
                             framer=device_framer(dev), timeout=5, retries=3)
    if not client.connect():
        log.warning("Failed to connect to Modbus device %s", host)
        st["pollSkippedTotal"] += 1
        publish_diagnostics(mqtt_client, mqtt_cfg, st)
        return
    try:
        serial_number = (get_inverter_serial_number(client) or "").strip()
        # Guard against a garbled/blank serial read: publishing under it would create a
        # phantom HA device (e.g. 'unknown_serial') with retained entities.
        if not re.fullmatch(r"[A-Za-z0-9]{6,}", serial_number) or serial_number == "unknown_serial":
            log.warning("Bad/blank serial from %s (%r), skipping cycle", host, serial_number)
            st["pollSkippedTotal"] += 1
            publish_diagnostics(mqtt_client, mqtt_cfg, st)
            return
        st["serial"] = serial_number
        log.info("Device %s serial number: %s", host, serial_number)

        if config["time_sync"]["enabled"]:
            sync_inverter_time(client, config["time_sync"]["max_drift_seconds"])

        # All-or-nothing: a failed/short read returns None (see client.py); skip the cycle
        # rather than publish partial data (and don't poison `discovered` / retained state).
        # A single garbled frame is common on the RTU-over-TCP dongles and pymodbus' own
        # retries don't catch it, so re-read a few times before giving up, tallying each
        # failed attempt so we can watch dongle health over time.
        read_retries = config.get("read_retries", 3)
        holding_registers = input_registers = None
        attempts_used = 0
        for attempt in range(1, read_retries + 1):
            attempts_used = attempt
            holding_registers = read_inverter_holding_registers(client)
            input_registers = read_inverter_input_registers(client)
            if holding_registers is not None and input_registers is not None:
                break
            st["readErrorsTotal"] += 1
            log.warning("Incomplete read from %s (attempt %d/%d)",
                        host, attempt, read_retries)
        if holding_registers is None or input_registers is None:
            log.warning("Giving up on %s after %d attempt(s), skipping cycle",
                        host, read_retries)
            st["pollSkippedTotal"] += 1
            publish_diagnostics(mqtt_client, mqtt_cfg, st)
            return

        st["pollOkTotal"] += 1
        st["lastCycleRetries"] = attempts_used - 1
        holding_registers['serialNumber'] = serial_number
        input_registers['serialNumber'] = serial_number

        retain = mqtt_cfg["retain"]

        # Per-device topic: merged holding+input state, retained. This is the single
        # source consumed by telegraf and Home Assistant (discovery + manual sensors).
        state = {**holding_registers, **input_registers}
        state_topic = f"{mqtt_cfg['topic_prefix']}/{serial_number}/state"
        mqtt_client.publish(state_topic, json.dumps(state), retain=retain)

        publish_diagnostics(mqtt_client, mqtt_cfg, st)

        # Publish HA discovery once per serial per run.
        if mqtt_cfg["discovery"] and serial_number not in discovered:
            publish_discovery(mqtt_client, mqtt_cfg, serial_number, dev.get("name"))
            discovered.add(serial_number)
    finally:
        client.close()


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    config = load_config()
    mqtt_client = make_mqtt_client(config["mqtt"])
    discovered = set()
    stats = {}  # per-host read-health counters, surfaced as HA diagnostic sensors

    log.info("Polling %d device(s) every %ss",
             len(config["devices"]), config["poll_interval"])
    try:
        while not _shutdown:
            for dev in config["devices"]:
                try:
                    poll_device(dev, config, mqtt_client, discovered, stats)
                except Exception as e:
                    log.exception("Unexpected error polling %s: %s", dev.get("host"), e)
            # Sleep in short slices so a signal interrupts us promptly.
            for _ in range(config["poll_interval"]):
                if _shutdown:
                    break
                time.sleep(1)
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        log.info("Stopped")


if __name__ == "__main__":
    main()
