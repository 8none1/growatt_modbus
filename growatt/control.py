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
# Grid First (forced discharge to grid) slots: [start, end, enable] registers per slot.
# Mirrors the batt-first layout: a primary block (slots 1-3) plus an extended block
# (slots 4-6) sharing the 1017-1035 table.
#   Slots 1-3 (1080-1088): three consecutive regs each, verified for slot 1 / from PDF
#     for 2-3.
#   Slots 4-6 (1027-1035): derived from the PDF's extended-slot table via the SAME +1
#     off-by-one proven for batt-first 4-6 (real 1026 is the batt-first slot-6 enable,
#     so the next entry "GF start 4" is real 1027). NOT yet live-verified - confirm with
#     GET /slots on the real inverter before relying on them.
GRID_FIRST_SLOTS = [
    [1080, 1081, 1082],   # slot 1 (verified)
    [1083, 1084, 1085],   # slot 2
    [1086, 1087, 1088],   # slot 3
    [1027, 1028, 1029],   # slot 4 (PDF-derived, unverified)
    [1030, 1031, 1032],   # slot 5 (PDF-derived, unverified)
    [1033, 1034, 1035],   # slot 6 (PDF-derived, unverified)
]


def decode_time(encoded_time):
    """Decode the inverter time format (hour << 8 | minute) to 'HH:MM'."""
    return "%02d:%02d" % (encoded_time >> 8, encoded_time & 255)


def encode_time(value):
    """Encode 'HH:MM' (UTC) to the inverter time format (hour << 8 | minute).

    Already-encoded integers are passed through, so callers can hand over either.
    """
    if isinstance(value, int):
        return value
    hh, mm = str(value).split(":")
    hh, mm = int(hh), int(mm)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("time out of range: %r" % (value,))
    return hh << 8 | mm


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
        gf_1_3 = self._read(1080, 9)
        if gf_1_3:
            for i in range(3):
                slots["grid_first_slot_%d" % (i + 1)] = {
                    "start": decode_time(gf_1_3[i * 3]),
                    "end": decode_time(gf_1_3[i * 3 + 1]),
                    "enabled": bool(gf_1_3[i * 3 + 2]),
                }
        gf_4_6 = self._read(1027, 9)  # extended block, PDF-derived (see GRID_FIRST_SLOTS)
        if gf_4_6:
            for i in range(3):
                slots["grid_first_slot_%d" % (i + 4)] = {
                    "start": decode_time(gf_4_6[i * 3]),
                    "end": decode_time(gf_4_6[i * 3 + 1]),
                    "enabled": bool(gf_4_6[i * 3 + 2]),
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
        """Load First: clear the Battery-First slot 6 enable and all Grid-First enables."""
        slot_6_enable_reg = BATT_FIRST_SLOTS[5][2]  # 1026
        r = self._read(slot_6_enable_reg, 1)
        if r and r[0] == 1:
            log.info("Clearing batt first slot 6 enable")
            self._write(slot_6_enable_reg, [0])
        for i, slot in enumerate(GRID_FIRST_SLOTS, 1):
            r = self._read(slot[2], 1)
            if r and r[0] == 1:
                log.info("Clearing grid first slot %d enable", i)
                self._write(slot[2], [0])

    def grid_first(self, duration=30, start=None, end=None, slot_num=1,
                   export_watts=None, rate_percent=None, stop_soc=None, rated_power_w=None):
        """Grid First (forced discharge to grid): program a grid-first slot to export.

        Time window: pass absolute start/end as 'HH:MM' (UTC) for a fixed window
        (e.g. a saving-session hour), or omit both to fall back to now..now+duration.
        slot_num (1-3) selects which of the three grid-first slots to program.

        The export rate and battery floor are controllable; the defaults preserve
        the original behaviour (full-power discharge, stop at 25% SOC):

        - rate (register 1070): the % of rated inverter power the battery discharges
          at. Set it by wattage with export_watts (needs rated_power_w to convert),
          or pass rate_percent to set the % directly. NB this caps battery *discharge*
          power, so net grid export is roughly discharge - house load + PV, not an
          exact export meter.
        - stop_soc (register 1071): battery SOC floor; discharge stops here, so a
          saving-session export does not drain the whole pack.

        1070/1071 are global to grid-first mode (not per-slot), so they apply to
        whichever slot is active.

        Returns the resolved {slot_num, start, end, rate_percent, stop_soc}.
        """
        slot_num = int(slot_num)
        if not (1 <= slot_num <= len(GRID_FIRST_SLOTS)):
            raise ValueError("invalid grid-first slot number %s (1-%d)"
                             % (slot_num, len(GRID_FIRST_SLOTS)))

        # Resolve the discharge/export rate as a percent of rated power.
        if export_watts is not None:
            if not rated_power_w:
                raise ValueError(
                    "export_watts needs rated_power_w to convert to a percent "
                    "(set control.rated_power_w in config)"
                )
            rate = round(100 * float(export_watts) / float(rated_power_w))
        elif rate_percent is not None:
            rate = int(rate_percent)
        else:
            rate = 100  # original default: discharge at full power
        rate = max(1, min(100, rate))

        floor = 25 if stop_soc is None else int(stop_soc)
        floor = max(0, min(100, floor))

        # Resolve the time window: absolute start/end, else now..now+duration.
        if start is not None or end is not None:
            if start is None or end is None:
                raise ValueError("start and end must be given together")
            encoded_start = encode_time(start)
            encoded_end = encode_time(end)
        else:
            if duration is None:
                duration = 30
            system_now = datetime.datetime.now(UTC)
            end_time = system_now + datetime.timedelta(minutes=int(duration) + 1)
            encoded_start = system_now.hour << 8 | system_now.minute
            encoded_end = end_time.hour << 8 | end_time.minute

        slot = GRID_FIRST_SLOTS[slot_num - 1]
        log.info("[GF] slot=%s start=%s end=%s rate=%s%% stop_soc=%s%%",
                 slot_num, decode_time(encoded_start), decode_time(encoded_end), rate, floor)
        # Clear batt-first slot 6 enable, set discharge rate + stop SOC, program the slot.
        self._write(BATT_FIRST_SLOTS[5][2], [0])  # 1026
        self._write(1071, [floor])
        self._write(1070, [rate])
        self._write(slot[0], [encoded_start, encoded_end, 1])
        return {"slot_num": slot_num, "start": decode_time(encoded_start),
                "end": decode_time(encoded_end), "rate_percent": rate, "stop_soc": floor}

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

    def disable_grid_first_slot(self, slot_num):
        slot_num = int(slot_num)
        if not (1 <= slot_num <= len(GRID_FIRST_SLOTS)):
            log.warning("disable grid-first: invalid slot number %s", slot_num)
            return
        enable_reg = GRID_FIRST_SLOTS[slot_num - 1][2]
        r = self._read(enable_reg, 1)
        if r and r[0] == 0:
            log.info("Grid-first slot %d already disabled", slot_num)
            return
        log.info("Disabling grid-first slot %d (register %d = 0)", slot_num, enable_reg)
        self._write(enable_reg, [0])

    def clear_all_slots(self):
        log.info("Clearing all battery first slots (1100-1108)")
        self._write(1100, [0] * 9)
        log.info("Clearing all battery first slots (1018-1026)")
        self._write(1018, [0] * 9)
        log.info("Clearing grid first slots 1-3 (1080-1088)")
        self._write(1080, [0] * 9)
        log.info("Clearing grid first slots 4-6 (1027-1035)")
        self._write(1027, [0] * 9)
