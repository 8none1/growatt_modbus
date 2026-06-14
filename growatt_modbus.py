#!/usr/bin/env python3
"""Poll Growatt SPH inverters over Modbus TCP (via an EW11 RS485 bridge) and
publish the decoded registers to MQTT, with optional Home Assistant discovery.

Configuration lives in config.yaml (see config.yaml.example). Hostnames, the MQTT
broker and poll interval are no longer hard coded.
"""

import os
import sys
import time
import json
import signal
import logging
import datetime

import yaml
from pymodbus.client import ModbusTcpClient
import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("growatt_modbus")

# Set by the signal handler so the main loop can exit cleanly on docker stop.
_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    log.info("Received signal %s, shutting down after this cycle", signum)
    _shutdown = True


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "poll_interval": 10,
    "devices": [],
    "mqtt": {
        "broker": "localhost",
        "port": 1883,
        "username": None,
        "password": None,
        "legacy_topic": "growatt",
        "topic_prefix": "growatt",
        "retain": True,
        "discovery_prefix": "homeassistant",
        "discovery": True,
    },
    "time_sync": {
        "enabled": True,
        "max_drift_seconds": 60,
    },
}


def _deep_merge(base, override):
    """Recursively merge override into a copy of base."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config():
    """Load config.yaml, layering it over defaults, then env var overrides."""
    candidates = [
        os.environ.get("GROWATT_CONFIG"),
        "/config/config.yaml",
        os.path.join(os.path.dirname(__file__), "config.yaml"),
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

    if not config["devices"]:
        log.error("No devices configured. Add at least one device to config.yaml.")
        sys.exit(1)
    return config


# ---------------------------------------------------------------------------
# Home Assistant discovery metadata
# ---------------------------------------------------------------------------
# Curated map of register field -> sensor metadata. Fields not listed here are
# still published in the state payload, they just do not get a HA entity.
# Tuple: (friendly name, device_class, unit, state_class)
SENSOR_META = {
    "pvPowerTotal":         ("PV power total",        "power",       "W",  "measurement"),
    "pv1Voltage":           ("PV1 voltage",           "voltage",     "V",  "measurement"),
    "pv1Current":           ("PV1 current",           "current",     "A",  "measurement"),
    "pv1Power":             ("PV1 power",             "power",       "W",  "measurement"),
    "pv2Voltage":           ("PV2 voltage",           "voltage",     "V",  "measurement"),
    "pv2Current":           ("PV2 current",           "current",     "A",  "measurement"),
    "pv2Power":             ("PV2 power",             "power",       "W",  "measurement"),
    "pvBattPower":          ("Battery PV power",      "power",       "W",  "measurement"),
    "gridFreq":             ("Grid frequency",        "frequency",   "Hz", "measurement"),
    "gridVolt":             ("Grid voltage",          "voltage",     "V",  "measurement"),
    "pvOutputCurrent":      ("Inverter output current", "current",   "A",  "measurement"),
    "pvOutputWattsVA":      ("Inverter apparent power", "apparent_power", "VA", "measurement"),
    "inverterTemperature":  ("Inverter temperature",  "temperature", "°C", "measurement"),
    "IPMTemperature":       ("IPM temperature",       "temperature", "°C", "measurement"),
    "boostTemperature":     ("Boost temperature",     "temperature", "°C", "measurement"),
    "realOutputPowerPercent": ("Real output power",   "power_factor", "%", "measurement"),
    "dischargePower":       ("Battery discharge power", "power",     "W",  "measurement"),
    "chargePower":          ("Battery charge power",  "power",       "W",  "measurement"),
    "battVoltage":          ("Battery voltage",       "voltage",     "V",  "measurement"),
    "battSOC":              ("Battery SOC",           "battery",     "%",  "measurement"),
    "gridImportPowerTotal": ("Grid import power",     "power",       "W",  "measurement"),
    "gridExportPowerTotal": ("Grid export power",     "power",       "W",  "measurement"),
    "pLocalLoadTotal":      ("Local load power",      "power",       "W",  "measurement"),
    "battTemperature":      ("Battery temperature",   "temperature", "°C", "measurement"),
    "epsFreq":              ("EPS frequency",         "frequency",   "Hz", "measurement"),
    "epsVolt":              ("EPS voltage",           "voltage",     "V",  "measurement"),
    "epsPower":             ("EPS power",             "power",       "W",  "measurement"),
    "epsLoadPercent":       ("EPS load",              None,          "%",  "measurement"),
    "bmsSOC":               ("BMS SOC",               "battery",     "%",  "measurement"),
    "bmsSOH":               ("BMS state of health",   None,          "%",  "measurement"),
    "bmsCycleCount":        ("BMS cycle count",       None,          None, "total_increasing"),
}


# ---------------------------------------------------------------------------
# Modbus helpers
# ---------------------------------------------------------------------------
def read_double_reg(r1, r2, multiplier=1):
    value = (r1 << 16 | r2)
    value = value * multiplier
    return value


def read_holding_registers(client, start_address, count):
    """Read holding registers from Modbus. Returns None on error."""
    try:
        response = client.read_holding_registers(address=start_address, count=count)
        if response.isError():
            log.warning("Error reading holding registers %s-%s",
                        start_address, start_address + count)
            return None
        return response.registers
    except Exception as e:
        log.warning("Modbus error reading holding registers: %s", e)
        return None


def read_input_registers(client, start_address, count):
    """Read input registers from Modbus. Returns None on error."""
    try:
        response = client.read_input_registers(address=start_address, count=count)
        if response.isError():
            log.warning("Error reading input registers %s-%s",
                        start_address, start_address + count)
            return None
        return response.registers
    except Exception as e:
        log.warning("Modbus error reading input registers: %s", e)
        return None


def write_registers(client, start_address, values):
    """Write holding registers to Modbus. Returns True on success."""
    try:
        response = client.write_registers(address=start_address, values=values)
        if response.isError():
            log.warning("Error writing registers %s-%s",
                        start_address, start_address + len(values) - 1)
            return False
        return True
    except Exception as e:
        log.warning("Modbus write error: %s", e)
        return False


def get_inverter_serial_number(client):
    """Read inverter serial number from Modbus."""
    registers = read_holding_registers(client, 23, 5)
    if registers:
        try:
            return ''.join(chr((i >> 8) & 0xFF) + chr(i & 0xFF) for i in registers)
        except (ValueError, TypeError):
            return "unknown_serial"
    return "unknown_serial"


def get_inverter_time(client):
    """Read the inverter's RTC. Returns a tz-aware UTC datetime, or None."""
    registers = read_holding_registers(client, 45, 7)
    if not registers:
        return None
    year, month, day, hour, minute, second, _dow = registers
    # This inverter firmware reports the full 4-digit year in register 45.
    try:
        return datetime.datetime(
            year, month, day, hour, minute, second,
            tzinfo=datetime.timezone.utc,
        )
    except ValueError as e:
        log.warning("Inverter returned an invalid time %s: %s", registers, e)
        return None


def sync_inverter_time(client, max_drift_seconds):
    """Compare the inverter clock to UTC and correct it if it has drifted."""
    inverter_time = get_inverter_time(client)
    if inverter_time is None:
        return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    delta = abs((now_utc - inverter_time).total_seconds())
    log.info("Inverter time %s, drift %.0fs from UTC", inverter_time.isoformat(), delta)
    if delta > max_drift_seconds:
        log.warning("Inverter clock drifted %.0fs, correcting to %s",
                    delta, now_utc.isoformat())
        # Match the 4-digit year reported on read. NB: writing register 45 is
        # rejected by some setups (read-only over the EW11), so this may fail.
        time_list = [now_utc.year, now_utc.month, now_utc.day,
                     now_utc.hour, now_utc.minute, now_utc.second, now_utc.isoweekday()]
        if not write_registers(client, 45, time_list):
            log.warning("Failed to update inverter time")
        else:
            log.info("Inverter time updated to %s", now_utc.isoformat())


def read_inverter_holding_registers(client):
    holding_registers = {}
    registers = read_holding_registers(client, 0, 16)
    if registers:
        holding_registers['safetyFunctionsBitMap']      = registers[1]
        holding_registers['maxOutputActivePower']        = registers[3]
        holding_registers['maxOutputReactivePower']      = registers[4]
        holding_registers['inverterPowerFactor']         = registers[5]
        normal_power                                     = read_double_reg(registers[6], registers[7], 0.1)
        holding_registers['NormalPower']                 = round(normal_power, 2)
        holding_registers['inverterNormalVoltage']       = registers[8]
        holding_registers['firmwareVersionH']            = registers[9]
        holding_registers['firmwareVersionM']            = registers[10]
        holding_registers['firmwareVersionL']            = registers[11]
        holding_registers['controllerVersionH']          = registers[12]
        holding_registers['controllerVersionM']          = registers[13]
        holding_registers['controllerVersionL']          = registers[14]
        holding_registers['lcdLanguage']                 = registers[15]
    next_chunk_start = 122
    registers = read_holding_registers(client, next_chunk_start, 64)
    if registers:
        holding_registers['exportLimitState']            = registers[122 - next_chunk_start]
        holding_registers['exportLimitRate']             = registers[123 - next_chunk_start]
        holding_registers['svgFunctionEnabled']          = registers[141 - next_chunk_start]
        holding_registers['numBatteryModules']           = registers[185 - next_chunk_start]
    registers = read_holding_registers(client, 1000, 93)
    if registers:
        holding_registers['vbatStopCharge']              = registers[5]
        holding_registers['vbatStopDischarge']           = registers[6]
        # Priority Mode - 0 = load, 1 = Batt, 2 = Grid
        holding_registers['priorityMode']                = registers[44]
        holding_registers['battType']                    = registers[48]
        holding_registers['exportToGridRatePercent']     = registers[70]
        holding_registers['exportToGridStopDischargePercent'] = registers[71]
        holding_registers['batFirstChargeRate']          = registers[90]
        holding_registers['batFirstStopChargeSOC']       = registers[91]
        holding_registers['acChargeEnabled']             = registers[92]
    return holding_registers


def read_inverter_input_registers(client):
    input_registers = {}
    registers = read_input_registers(client, 0, 118)
    if registers:
        input_registers['inverterStatus']               = registers[0]  # Seems to be 6 at night
        input_registers['pvPowerTotal']                 = read_double_reg(registers[1], registers[2], 0.1)
        input_registers['pv1Voltage']                   = round(registers[3] * 0.1, 1)
        input_registers['pv1Current']                   = round(registers[4] * 0.1, 1)
        input_registers['pv1Power']                     = round(read_double_reg(registers[5], registers[6], 0.1), 1)
        input_registers['pv2Voltage']                   = round(registers[7] * 0.1, 1)
        input_registers['pv2Current']                   = round(registers[8] * 0.1, 1)
        input_registers['pv2Power']                     = round(read_double_reg(registers[9], registers[10], 0.1), 1)
        input_registers['pvBattPower']                  = round(read_double_reg(registers[35], registers[36], 0.1), 1)
        input_registers['gridFreq']                     = round(registers[37] * 0.01, 3)
        input_registers['gridVolt']                     = round(registers[38] * 0.1, 2)
        input_registers['pvOutputCurrent']              = round(registers[39] * 0.1, 1)  # PV output current, not grid
        input_registers['pvOutputWattsVA']              = round(read_double_reg(registers[40], registers[41], 0.1), 1)
        input_registers['inverterTemperature']          = round(registers[93] * 0.1, 1)
        input_registers['IPMTemperature']               = round(registers[94] * 0.1, 1)
        input_registers['boostTemperature']             = round(registers[95] * 0.1, 1)
        input_registers['inverterPowerFactorNow']       = registers[100]  # 0 -> 20000 range
        input_registers['realOutputPowerPercent']       = registers[101]
        input_registers['OPFullWatt']                   = read_double_reg(registers[102], registers[103], 0.1)
        input_registers['InverterFaultCode']            = registers[105]
        input_registers['FaultBitCode']                 = read_double_reg(registers[106], registers[107])
        input_registers['WarningBitCode']               = read_double_reg(registers[110], registers[111])
        input_registers['ACChargePower']                = read_double_reg(registers[116], registers[117], 0.1)
    next_chunk_start = 1000
    registers = read_input_registers(client, next_chunk_start, 124)
    if registers:
        input_registers['systemWorkMode']               = registers[0]
        input_registers['dischargePower']               = read_double_reg(registers[9], registers[10], 0.1)
        input_registers['chargePower']                  = read_double_reg(registers[11], registers[12], 0.1)
        input_registers['battVoltage']                  = round(registers[13] * 0.1, 3)
        input_registers['battSOC']                      = registers[14]
        input_registers['gridImportPowerTotal']         = read_double_reg(registers[21], registers[22], 0.1)
        input_registers['gridExportPowerTotal']         = read_double_reg(registers[29], registers[30], 0.1)
        input_registers['pLocalLoadTotal']              = read_double_reg(registers[37], registers[38], 0.1)
        input_registers['battTemperature']              = registers[40]
        input_registers['epsFreq']                      = registers[67]
        input_registers['epsVolt']                      = round(registers[68] / 10, 2)
        input_registers['epsCurrent']                   = registers[69]
        input_registers['epsPower']                     = read_double_reg(registers[70], registers[71], 0.1)
        input_registers['epsLoadPercent']               = registers[80]
        input_registers['epsPowerFactor']               = registers[81]
        input_registers['bmsStatus']                    = registers[83]
        input_registers['bmsStatusBitmap']              = format(registers[83], '016b')
        #  Bit map for bmsStatusBitmap:
        #  0 & 1 - 00 soft start, 01 stand by, 10 charge, 11 discharge
        #  2 - errors?
        #  3 - cell balance 0 = unbalance, 1 = balance
        #  4 - sleep status 0 disable 1 enable
        #  5 output discharge - 0 disable 1 enable
        #  6 output charge
        #  7 battery terminal - 0 connected, 1 disconnected
        #  8 & 9 operation mode, 00 - stand alone, 01 - parallel, 10 - parallel preparation
        #  10 & 11 SP status - 00 none, 01 standby, 10 charge, 11 discharge
        input_registers['bmsError']                     = registers[85]
        input_registers['bmsSOC']                       = registers[86]
        input_registers['bmsDeltaV']                    = registers[94]
        input_registers['bmsCycleCount']                = registers[95]
        input_registers['bmsSOH']                       = registers[96]
        # BMS cell voltages (mV)
        for i in range(108, 124):
            input_registers["cellVoltage" + str(i - 107)] = registers[i]
    return input_registers


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
