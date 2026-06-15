# Register notes

Findings from cross-referencing the Growatt SPH protocol PDFs against what
`growatt_modbus.py` actually reads. **The running code is treated as ground truth**:
the PDFs are translated from Chinese, are internally inconsistent in places, and have
been observed to be wrong against real hardware (see the battery temperature example
below). Live values quoted here were read from a real SPH (serial WCK0CDE013).

Addresses are Modbus register numbers. "32-bit" means a high/low pair combined as
`high << 16 | low`. Input = function code 04, Holding = function code 03.

## Verified (code matches the PDF and live data)

Most of what the code reads checks out: PV strings (input 1-10), grid (37-41),
temperatures (93-95), the storage power flows (input 1009-1038), battery SOC/voltage
(1013-1014), the BMS status/error/SOC/SOH/cycle/delta block (1083-1096), the inverter
identity/firmware/serial holding registers, and the RTC. No action needed on these.

## Discrepancies to discuss (code is correct; the PDF says something different)

These are *not* applied in code yet. Naming and scaling changes are deliberately left
for case-by-case review because the PDF is unreliable.

### Naming (register is read correctly, but the code's name misleads)
| Register | Code name | Reality (PDF + live) |
|---|---|---|
| input 35-36 | `pvBattPower` | Total **AC output power** (`Pac`). Live 2722 W = the inverter's AC output, nothing battery-specific. |
| input 39 | `pvOutputCurrent` | **Grid/AC output current** (`Iac1`). Live 11.4 A ≈ 2722 W / 237 V. The code comment ("PV output current, not grid") is backwards. |
| input 116-117 | `ACChargePower` | PDF labels this **energy (kWh)** ("grid power to local load"), not instantaneous power. The real AC-charge *energy* is at 1124-1127 (now read). Probably mislabelled; live 0 so unconfirmed. |
| holding 1006 | `vbatStopDischarge` | PDF calls it "Vbat **start** for discharge" (a lower limit). Possible semantic inversion vs the "stop" concept. |

### Scaling the PDF claims but the code does not apply (decide per case)
| Register | Code field | Note |
|---|---|---|
| holding 8 | `inverterNormalVoltage` | Live 3600. PDF ×0.1 → 360.0 V (sensible). Likely needs ×0.1. |
| holding 5 | `inverterPowerFactor` | Live 10000 = PF 1.0 (PDF: value is PF×10000). Needs ÷10000 for a real PF. |
| holding 123 | `exportLimitRate` | PDF ×0.1 (%). Live 0, inconclusive. |
| input 1067 | `epsFreq` | PDF ×0.01 Hz. Live 0 (EPS idle), inconclusive. |
| input 1069 | `epsCurrent` | PDF ×0.1 A. Live 0 (EPS idle), inconclusive. |
| input 1081 | `epsPowerFactor` | PDF: stored value = PF + 1 (needs a transform). Live 1000 (EPS idle). |
| input 1040 | `battTemperature` | **PDF says ×0.1 but the PDF is WRONG here.** Live 19 = 19 °C; ×0.1 would give 1.9 °C. The code (raw) is correct. Do not change. |
| holding 1005/1006 | `vbatStopCharge` / `vbatStopDischarge` | Live 575 and 4800 are mutually inconsistent in scale, and the battery is lithium so these lead-acid voltage thresholds look like unused defaults. Low priority. |

### Other flags
- **`battType` (holding 1048):** live = 1, and the pack is clearly lithium. The *input*-table
  enum (1 = Lithium) is therefore right; the *holding*-table enum (1 = lead-acid) is wrong.
- **`FaultBitCode` (input 106-107)** and **`WarningBitCode` (input 110-111):** the code treats
  each as a 32-bit pair, but the PDF shows 106 reserved / 107 a 16-bit subcode, and the 110/111/112
  rows are messy. The 32-bit pairing is unconfirmed.
- **`svgFunctionEnabled` (holding 141)** and **`numBatteryModules` (holding 185):** fall in a PDF
  section not yet extracted, so unverified. Live input reg 1110 = 2 is consistent with "2 modules".
- **`inverterStatus` (input 0):** the code comment "seems to be 6 at night" more likely refers to
  `systemWorkMode` (input 1000), which uses higher mode codes.

## The cell-voltage block (input 1108-1123): fixed

The old code labelled input 1108-1123 as `cellVoltage1..16`. This was wrong. Per the PDF
and live data:

- **1108 = max cell voltage** (×0.001 V), **1109 = min cell voltage** (×0.001 V),
  **1110 = battery module/parallel count.** These are now read as `maxCellVoltage`,
  `minCellVoltage`, `batteryModuleCount`.
- **1111** is an outlier (live 3600, above the max cell voltage, so not a cell). PDF guesses
  "number of batteries"; unconfirmed.
- **1112-1123** read as 12 values that all sit inside the min/max cell envelope and rise/fall
  together with charge state, so they are *probably* 12 individual cell voltages in mV, which
  contradicts the PDF (which lists indices/temps/SOC/error words there). Kept as raw `bmsReg1111`
  .. `bmsReg1123` with meaning UNCONFIRMED pending observation over a full charge/discharge cycle.

**Battery is CAN bus.** The ESS protocol PDF defines the genuine per-cell voltages at
`0x0071`-`0x0080` (1 mV each) in the battery's *own* protocol address space, i.e. over CAN to
the inverter, not in the inverter's Modbus map. So the full per-cell detail is not reachable over
the EW11; the inverter only proxies a summary (max/min/count) into Modbus.

> Note: removing `cellVoltage1..16` also removes those keys from the published
> `growatt/<serial>/state` data. They were mislabelled and not Home Assistant entities, but
> check nothing downstream consumed them.

## New registers added this round (validated live, all useful)

### Energy counters (kWh, 32-bit, ×0.1) - unlock the HA energy dashboard
| Field | Registers | Live total |
|---|---|---|
| `eacToday` / `eacTotal` | 53-56 | 24,145 kWh |
| `epvTotal` (+ `epv1Total`/`epv2Total`) | 91-92 / 59-66 | 19,552 kWh |
| `eToUserToday` / `eToUserTotal` | 1044-1047 | 16,834 kWh |
| `eToGridToday` / `eToGridTotal` | 1048-1051 | 4,827 kWh |
| `eDischargeToday` / `eDischargeTotal` | 1052-1055 | 12,815 kWh |
| `eChargeToday` / `eChargeTotal` | 1056-1059 | 13,483 kWh |
| `eLocalLoadToday` / `eLocalLoadTotal` | 1060-1063 | 30,891 kWh |
| `acChargeEnergyToday` / `acChargeEnergyTotal` | 1124-1127 | 9,005 kWh |
| `eSelfToday` / `eSelfTotal` | 1141-1144 | 21,725 kWh |

Sanity check: lifetime charge 13,483 kWh vs discharge 12,815 kWh ≈ 95% round-trip
(right for LFP); per-string PV totals sum to the PV total; per-string daily sums match
the daily AC figure.

### Diagnostics
- `deratingMode` (input 104): why output is capped (0 none, 1 PV, 3 Vac, 4 Fac, 5 Tboost,
  6 Tinv, 7 control, 9 over-back-by-time).
- `operatingHours` (input 57-58, ×0.5 s per count): lifetime run time (~30,929 h live).
- `maxCellVoltage` / `minCellVoltage` (input 1108/1109): cell spread is the clearest early
  warning of an imbalanced or failing cell.

## Charge / discharge scheduling (time-of-use slots)

The SPH schedules battery charging and grid discharge in time slots, each stored as three
holding registers: **start time, end time, enable**. Times are encoded as `hour << 8 | minute`
(hour in the high byte, minute in the low byte); enable is `0`/`1`.

**The authoritative reference is the working CGI script on perceptron:**
`~/docker/lighttpd/www/cgi-bin/switch_inverter_mode.py` (runs in the `lighttpd-web-1` container).
It both reads the slots and is what the `octopus_agile_battery_scheduler` drives to charge during
cheap Octopus Agile windows.

**Battery First / AC-charge slots** (charge the battery; pull from grid here if AC charge is on
and solar is insufficient):

| Slot | start / end / enable registers |
| ---- | ------------------------------ |
| 1 | 1100 / 1101 / 1102 |
| 2 | 1103 / 1104 / 1105 |
| 3 | 1106 / 1107 / 1108 |
| 4 | 1018 / 1019 / 1020 |
| 5 | 1021 / 1022 / 1023 |
| 6 | 1024 / 1025 / 1026 |

> **The Growatt PDF is off by one for slots 4-6**: it lists them starting at 1017, but the real
> start register is **1018**. (Reading from 1017 makes a slot's end-time look like its enable, which
> is misleading.) Slots 1-3 (the 1100 block) match the PDF.

**Grid First (forced discharge to grid) slot 1**: 1080 / 1081 / 1082.

**AC-charge controls:**
- `1044` priorityMode (0 = Load First, 1 = Battery First, 2 = Grid First)
- `1090` battery charge power rate (%)
- `1091` stop-charge SOC (%) — at 100 it tops the battery right up
- `1092` AC charge enable (1 = grid charging allowed)
- `1070` / `1071` Grid First discharge rate (%) / stop-discharge SOC (%)

To stop grid charging in a slot, set that slot's enable register to 0 (e.g. slot 2 → write
`1105 = 0`), or set `1092 = 0` to disable AC charging entirely. An enabled afternoon Battery-First
slot is usually the Agile scheduler working as intended (it writes `start = now, end = now + duration`
for a cheap window), not a fault.

## Not yet pursued
- Decoding the system fault words (input 1001-1008) and BMS warning bitfield (1098/1099)
  into human-readable fault/warning sensors.
- Confirming the 1112-1123 per-cell-voltage hypothesis.
- The holding 124-1000 gap (covers `svgFunctionEnabled`, `numBatteryModules`).
