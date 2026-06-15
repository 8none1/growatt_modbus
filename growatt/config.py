"""Load configuration from config.yaml, layered over defaults with env overrides.

The config file holds the only host-specific/private values (inverter hosts, MQTT
broker), so the rest of the code can be published. Resolution order:
$GROWATT_CONFIG, then /config/config.yaml (the container mount), then ./config.yaml.
"""

import os
import sys
import logging

import yaml

log = logging.getLogger("growatt")

DEFAULT_CONFIG = {
    "poll_interval": 10,
    # How many times to re-read an inverter within a single poll cycle before giving up,
    # to ride out the occasional garbled/short frame from a raw RTU-over-TCP dongle.
    "read_retries": 3,
    "devices": [],
    "mqtt": {
        "broker": "localhost",
        "port": 1883,
        "username": None,
        "password": None,
        "topic_prefix": "growatt",
        "retain": True,
        "discovery_prefix": "homeassistant",
        "discovery": True,
    },
    "time_sync": {
        "enabled": True,
        "max_drift_seconds": 60,
    },
    # Used by the control CGI to pick which inverter to command (the one with the
    # battery). device is the name from the devices list; defaults to the first.
    "control": {
        "device": None,
        "device_id": 1,
    },
}


def device_framer(dev):
    """Map a device's optional 'framer' key to a pymodbus framer.

    Default (no key, or anything other than 'rtu') is Modbus TCP / MBAP (the EW11).
    'rtu' selects raw Modbus-RTU-over-TCP, used by a reflashed ShineWiFi-X dongle.
    """
    from pymodbus import FramerType
    return FramerType.RTU if (dev or {}).get("framer") == "rtu" else FramerType.SOCKET


def control_target(config):
    """Resolve (host, port, device_id, framer) for the inverter the control side commands."""
    devices = config.get("devices") or []
    if not devices:
        raise ValueError("No devices configured for control")
    name = (config.get("control") or {}).get("device")
    dev = next((d for d in devices if d.get("name") == name), devices[0]) if name else devices[0]
    device_id = (config.get("control") or {}).get("device_id") or dev.get("unit", 1)
    return dev["host"], dev.get("port", 502), device_id, device_framer(dev)


def _deep_merge(base, override):
    """Recursively merge override into a copy of base."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(require_devices=True):
    """Load config.yaml, layering it over defaults, then env var overrides."""
    candidates = [
        os.environ.get("GROWATT_CONFIG"),
        "/config/config.yaml",
        "config.yaml",
    ]
    path = next((p for p in candidates if p and os.path.exists(p)), None)
    if path is None:
        log.error(
            "No config file found. Set GROWATT_CONFIG or create config.yaml "
            "(see config.yaml.example)."
        )
        sys.exit(1)

    log.info("Loading config from %s", path)
    with open(path) as fh:
        file_config = yaml.safe_load(fh) or {}
    config = _deep_merge(DEFAULT_CONFIG, file_config)

    # Environment overrides for the common knobs (handy in containers).
    if os.environ.get("MQTT_BROKER"):
        config["mqtt"]["broker"] = os.environ["MQTT_BROKER"]
    if os.environ.get("MQTT_USERNAME"):
        config["mqtt"]["username"] = os.environ["MQTT_USERNAME"]
    if os.environ.get("MQTT_PASSWORD"):
        config["mqtt"]["password"] = os.environ["MQTT_PASSWORD"]

    if require_devices and not config["devices"]:
        log.error("No devices configured. Add at least one device to config.yaml.")
        sys.exit(1)
    return config
