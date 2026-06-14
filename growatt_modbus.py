#!/usr/bin/env python3
"""Poll Growatt SPH inverters over Modbus TCP (via an EW11 RS485 bridge) and
publish the decoded registers to MQTT, with optional Home Assistant discovery.

Configuration lives in config.yaml (see config.yaml.example). The Modbus decode,
client helpers and config loading live in the shared ``growatt`` package so the
control side can reuse them.
"""

import os
import time
import json
import signal
import logging

import paho.mqtt.client as mqtt
from pymodbus.client import ModbusTcpClient

from growatt.config import load_config
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
    log.info("Published HA discovery for %s (%d sensors)", serial, len(SENSOR_META))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def poll_device(dev, config, mqtt_client, discovered):
    """Poll a single inverter and publish its data."""
    client = ModbusTcpClient(host=dev["host"], port=dev.get("port", 502))
    if not client.connect():
        log.warning("Failed to connect to Modbus device %s", dev["host"])
        return
    try:
        serial_number = get_inverter_serial_number(client)
        log.info("Device %s serial number: %s", dev["host"], serial_number)

        if config["time_sync"]["enabled"]:
            sync_inverter_time(client, config["time_sync"]["max_drift_seconds"])

        holding_registers = read_inverter_holding_registers(client)
        holding_registers['serialNumber'] = serial_number
        input_registers = read_inverter_input_registers(client)
        input_registers['serialNumber'] = serial_number

        mqtt_cfg = config["mqtt"]
        retain = mqtt_cfg["retain"]

        # New per-device topic: merged state, retained.
        state = {**holding_registers, **input_registers}
        state_topic = f"{mqtt_cfg['topic_prefix']}/{serial_number}/state"
        mqtt_client.publish(state_topic, json.dumps(state), retain=retain)

        # Legacy flat topic: keep the original two-message behaviour intact.
        legacy = mqtt_cfg["legacy_topic"]
        mqtt_client.publish(legacy, json.dumps(input_registers))
        mqtt_client.publish(legacy, json.dumps(holding_registers))

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

    log.info("Polling %d device(s) every %ss",
             len(config["devices"]), config["poll_interval"])
    try:
        while not _shutdown:
            for dev in config["devices"]:
                try:
                    poll_device(dev, config, mqtt_client, discovered)
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
