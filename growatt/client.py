"""Low-level Modbus helpers for a Growatt SPH inverter.

These wrap a ``pymodbus`` ``ModbusTcpClient`` and are shared by the poller and
the control side. They log warnings and return ``None``/``False`` on error
rather than raising, so callers can degrade gracefully.
"""

import datetime
import logging

log = logging.getLogger("growatt")


def read_double_reg(r1, r2, multiplier=1):
    """Combine two 16-bit registers into a 32-bit value (high << 16 | low)."""
    value = (r1 << 16 | r2)
    value = value * multiplier
    return value


def read_holding_registers(client, start_address, count, device_id=None):
    """Read holding registers from Modbus. Returns None on error."""
    kwargs = {} if device_id is None else {"device_id": device_id}
    try:
        response = client.read_holding_registers(address=start_address, count=count, **kwargs)
        if response.isError():
            log.warning("Error reading holding registers %s-%s",
                        start_address, start_address + count)
            return None
        return response.registers
    except Exception as e:
        log.warning("Modbus error reading holding registers: %s", e)
        return None


def read_input_registers(client, start_address, count, device_id=None):
    """Read input registers from Modbus. Returns None on error."""
    kwargs = {} if device_id is None else {"device_id": device_id}
    try:
        response = client.read_input_registers(address=start_address, count=count, **kwargs)
        if response.isError():
            log.warning("Error reading input registers %s-%s",
                        start_address, start_address + count)
            return None
        return response.registers
    except Exception as e:
        log.warning("Modbus error reading input registers: %s", e)
        return None


def write_registers(client, start_address, values, device_id=None):
    """Write holding registers to Modbus. Returns True on success."""
    kwargs = {} if device_id is None else {"device_id": device_id}
    try:
        response = client.write_registers(address=start_address, values=values, **kwargs)
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
        # Asymmetric year: the inverter reports a 4-digit year on read but
        # expects a 2-digit year (year - 2000) on write. Write the six time
        # fields via FC16; the weekday register is left for the inverter to
        # derive (matches the known-good octopus_agile_battery_scheduler).
        time_list = [now_utc.year - 2000, now_utc.month, now_utc.day,
                     now_utc.hour, now_utc.minute, now_utc.second]
        if not write_registers(client, 45, time_list):
            log.warning("Failed to update inverter time")
        else:
            log.info("Inverter time updated to %s", now_utc.isoformat())


def set_inverter_time(client, device_id=None):
    """Unconditionally set the inverter RTC to the current UTC time.

    Used by the control side before scheduling, so charge/discharge slots line up
    with UTC (Octopus Agile prices are UTC). Same 2-digit-year, six-register FC16
    write as sync_inverter_time.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    time_list = [now_utc.year - 2000, now_utc.month, now_utc.day,
                 now_utc.hour, now_utc.minute, now_utc.second]
    if write_registers(client, 45, time_list, device_id=device_id):
        log.info("Inverter time set to %s", now_utc.isoformat())
    else:
        log.warning("Failed to set inverter time")
