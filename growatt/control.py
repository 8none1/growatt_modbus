"""Control a Growatt SPH inverter: switch Battery/Grid/Load First and manage the
AC-charge time-of-use slots.

Faithful port of the original switch_inverter_mode.py CGI logic. Register addresses,
the slot map (with the 1018 off-by-one vs the PDF) and the AC-charge values are kept
exactly as the proven script; only the inverter host/unit now come from config.
Times are UTC (Octopus Agile prices are UTC) and encoded as hour << 8 | minute.
"""

import datetime
import logging

from pymodbus.client import ModbusTcpClient

from .client import read_holding_registers, write_registers, set_inverter_time
from ._modbus_lock import MODBUS_LOCK

log = logging.getLogger("growatt")

UTC = datetime.timezone.utc


def with_control_session(config, fn):
    """Run fn(inv) against the control inverter inside the global Modbus lock.

    Opens a short-lived InverterControl (one connection), calls fn, and always closes
    it. Holding MODBUS_LOCK for the whole session means it can never overlap a poll
    cycle's session, so the dongle never sees two concurrent connections.
    """
    from .config import control_target
    host, port, device_id, framer = control_target(config)
    with MODBUS_LOCK:
        inv = InverterControl(host, port, device_id, framer=framer)
        try:
            return fn(inv)
        finally:
            inv.close()

# Battery First / AC-charge slots: [start, end, enable] registers per slot.
# NB slots 4-6 start at 1018, not 1017 as the Growatt PDF says.
BATT_FIRST_SLOTS = [
    [1100, 1101, 1102],
    [1103, 1104, 1105],
    [1106, 1107, 1108],
    [1018, 1019, 1020],
    [1021, 1022, 1023],
    [1024, 1025, 1026],
]
GRID_FIRST_SLOT_1 = [1080, 1081, 1082]


def decode_time(encoded_time):
    """Decode the inverter time format (hour << 8 | minute) to 'HH:MM'."""
    return "%02d:%02d" % (encoded_time >> 8, encoded_time & 255)


class InverterControl:
    """Connect to one inverter and issue control commands."""

    def __init__(self, host, port=502, device_id=1, framer=None, timeout=5):
        self.device_id = device_id
        # retries: raw RTU-over-TCP dongles occasionally drop/garble a frame.
        kwargs = {"framer": framer} if framer is not None else {}
        self.client = ModbusTcpClient(host, port=port, timeout=timeout, retries=3, **kwargs)
        self.connected = self.client.connect()

    def close(self):
        self.client.close()

    # -- low-level (via the shared wrappers, scoped to this device) --
    def _read(self, address, count):
        return read_holding_registers(self.client, address, count, device_id=self.device_id)

    def _write(self, address, values):
        return write_registers(self.client, address, values, device_id=self.device_id)

    # -- time --
    def set_time(self):
        set_inverter_time(self.client, device_id=self.device_id)

    # -- read all slots --
    def get_all_slots(self):
        slots = {}
        bf_1_3 = self._read(1100, 9)
        if bf_1_3:
            for i in range(3):
                slots["battery_first_slot_%d" % (i + 1)] = {
                    "start": decode_time(bf_1_3[i * 3]),
                    "end": decode_time(bf_1_3[i * 3 + 1]),
                    "enabled": bool(bf_1_3[i * 3 + 2]),
                }
        bf_4_6 = self._read(1018, 9)
        if bf_4_6:
            for i in range(3):
                slots["battery_first_slot_%d" % (i + 4)] = {
                    "start": decode_time(bf_4_6[i * 3]),
                    "end": decode_time(bf_4_6[i * 3 + 1]),
                    "enabled": bool(bf_4_6[i * 3 + 2]),
                }
        gf = self._read(1080, 3)
        if gf:
            slots["grid_first_slot_1"] = {
                "start": decode_time(gf[0]),
                "end": decode_time(gf[1]),
                "enabled": bool(gf[2]),
            }
        return slots

    # -- mode switches --
    def battery_first(self, duration=30, slot_num=6):
        """Battery First (charge): program the given slot to start=now, end=now+duration."""
        duration = int(duration)
        slot_num = int(slot_num)
        if not (1 <= slot_num <= len(BATT_FIRST_SLOTS)):
            log.warning("[BF] invalid slot number %s", slot_num)
            return
        system_now = datetime.datetime.now(UTC)
        new_end_time = (system_now + datetime.timedelta(minutes=duration + 1)).replace(second=0, microsecond=0)
        log.info("[BF] now=%s duration=%s end=%s slot=%s", system_now, duration, new_end_time, slot_num)

        slot = BATT_FIRST_SLOTS[slot_num - 1]
        slot_start_reg = slot[0]
        current = self._read(slot_start_reg, 3)
        if current and current[2] == 1:
            # A slot is already enabled; do not shorten an existing charge window.
            start_h, start_m = current[0] >> 8, current[0] & 255
            end_h, end_m = current[1] >> 8, current[1] & 255
            cur_start = datetime.datetime(system_now.year, system_now.month, system_now.day,
                                          start_h, start_m, 0, 0, tzinfo=UTC)
            cur_end = datetime.datetime(system_now.year, system_now.month, system_now.day,
                                        end_h, end_m, 0, 0, tzinfo=UTC)
            if cur_start.time() > system_now.time():
                # start is in the future -> the window began the previous day
                cur_end = cur_end - datetime.timedelta(days=1)
            if cur_end >= new_end_time:
                log.info("[BF] existing slot end %s >= requested %s, leaving alone", cur_end, new_end_time)
                return
            log.info("[BF] existing slot ends before requested, reprogramming")

        # Max charge level + enable AC charging (values intentionally hard-coded).
        self._write(1090, [100])
        self._write(1091, [100])
        self._write(1092, [1])
        encoded_start = system_now.hour << 8 | system_now.minute
        encoded_end = new_end_time.hour << 8 | new_end_time.minute
        log.info("[BF] writing slot %s start=%s end=%s", slot_num, encoded_start, encoded_end)
        self._write(slot_start_reg, [encoded_start, encoded_end, 1])

    def load_first(self):
        """Load First: clear the Battery-First slot 6 enable and the Grid-First enable."""
        slot_6_enable_reg = BATT_FIRST_SLOTS[5][2]  # 1026
        grid_first_enable_reg = GRID_FIRST_SLOT_1[2]  # 1082
        r = self._read(slot_6_enable_reg, 1)
        if r and r[0] == 1:
            log.info("Clearing batt first slot 6 enable")
            self._write(slot_6_enable_reg, [0])
        r = self._read(grid_first_enable_reg, 1)
        if r and r[0] == 1:
            log.info("Clearing grid first slot 1 enable")
            self._write(grid_first_enable_reg, [0])

    def grid_first(self, duration=30):
        """Grid First (forced discharge): program grid-first slot 1 for now..now+duration."""
        if duration is None:
            duration = 30
        duration = int(duration)
        system_now = datetime.datetime.now(UTC)
        end_time = system_now + datetime.timedelta(minutes=duration + 1)
        log.info("[GF] now=%s duration=%s end=%s", system_now, duration, end_time)
        # Clear batt-first slot 6 enable, set discharge rate + stop SOC (hard-coded), program slot 1.
        self._write(BATT_FIRST_SLOTS[5][2], [0])  # 1026
        self._write(1071, [25])
        self._write(1070, [100])
        encoded_start = system_now.hour << 8 | system_now.minute
        encoded_end = end_time.hour << 8 | end_time.minute
        log.info("[GF] writing grid-first slot 1 start=%s end=%s", encoded_start, encoded_end)
        self._write(GRID_FIRST_SLOT_1[0], [encoded_start, encoded_end, 1])

    def disable_batt_first_slot(self, slot_num):
        slot_num = int(slot_num)
        if not (1 <= slot_num <= len(BATT_FIRST_SLOTS)):
            log.warning("disable: invalid slot number %s", slot_num)
            return
        enable_reg = BATT_FIRST_SLOTS[slot_num - 1][2]
        r = self._read(enable_reg, 1)
        if r and r[0] == 0:
            log.info("Slot %d already disabled", slot_num)
            return
        log.info("Disabling slot %d (register %d = 0)", slot_num, enable_reg)
        self._write(enable_reg, [0])

    def clear_all_slots(self):
        log.info("Clearing all battery first slots (1100-1108)")
        self._write(1100, [0] * 9)
        log.info("Clearing all battery first slots (1018-1026)")
        self._write(1018, [0] * 9)
        log.info("Clearing grid first slot (1080-1082)")
        self._write(1080, [0] * 3)
