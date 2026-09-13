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
the Modbus bridge (dongle or EW11); the inverter only proxies a summary (max/min/count) into Modbus.

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

**The authoritative reference is `growatt/control.py`** (the `InverterControl` class). It reads
the slots (`GET /slots`) and applies the writes (`POST /mode`) that the
`octopus_agile_battery_scheduler` drives, via Home Assistant, to charge during cheap Octopus
Agile windows. (Originally a standalone CGI on perceptron; ported into the repo and then merged
into the poller process.)

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

**Grid First (forced discharge to grid) slots** mirror the batt-first split: a primary
block (slots 1-3) plus an extended block (slots 4-6) sharing the 1017-1035 table.

| Slot | start / end / enable registers | source |
| ---- | ------------------------------ | ------ |
| 1 | 1080 / 1081 / 1082 | verified |
| 2 | 1083 / 1084 / 1085 | PDF (consecutive) |
| 3 | 1086 / 1087 / 1088 | PDF (consecutive) |
| 4 | 1027 / 1028 / 1029 | PDF + the +1 off-by-one |
| 5 | 1030 / 1031 / 1032 | PDF + the +1 off-by-one |
| 6 | 1033 / 1034 / 1035 | PDF + the +1 off-by-one |

> Slots 4-6 sit in the **same extended-slot table as batt-first 4-6** and carry the same
> **+1 off-by-one** the PDF has for that table. The PDF lists "Grid First Start Time 4" at
> 1026, but real 1026 is the proven batt-first slot-6 enable, so grid-first slot 4 starts at
> **1027** (real = PDF + 1), landing a clean gapless block 1018-1035. Slots 4-6 are
> **PDF-derived and not yet live-verified** - read them back with `GET /slots` on the real
> inverter before relying on them. The discharge rate (`1070`) and stop-discharge SOC
> (`1071`) are global to grid-first mode, not per-slot.

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

## Grid-first force discharge: LIVE-VERIFIED 2026-09-13 (WCK0CDE013)

First real force-discharge test on the hardware. This **settles the register 1070
question** that `POWER_DOWN_EXPORT_PLAN.md` lists as its blocking unknown, and it
found two things the PDF does not mention.

**Method.** Grid-first slot 1 (`1080`/`1081`/`1082`) programmed for a 2-minute UTC
window, `1070 = 40`, `1071 = 25` (SOC was 92-93 %, so the floor was never near).
Evening, PV ~350 W, house ~2130 W, so the battery was carrying the house with zero
import and zero export beforehand. MQTT sampled every ~8-30 s throughout.

### 1070 is a DISCHARGE CAP, not an export setpoint

Reading (A) confirmed. During the window battery discharge **pinned flat at 1600 W**
for the whole slot, and because 1600 W of battery + 350 W of PV was less than the
~2130 W the house wanted, the house **began importing 60-120 W having imported
nothing beforehand**. Two samples caught 1120-1130 W of *export* when the house load
momentarily dropped away, which is the clincher: the battery holds the commanded rate
regardless of load, and grid flow is simply the remainder.

    export = discharge(1070) + PV - house load        (signed; negative = import)

**Consequence: a low rate does NOT give you a small export, it gives you import.**
Any "frugal" small-export scheme built on 1070 alone is wrong. To get a small
controlled export, run 1070 generously (battery comfortably ahead of the house) and
cap the surplus with the export limiter at `122`/`123` instead.

### The percentage base is ~4000 W, NOT the 5000 W nameplate

`1070 = 40` produced exactly 1600 W, i.e. 40 % of **4000 W**. 4000 W is also the
maximum discharge observed over 14 days of recorder history (p95 3580 W). So do not
size a rate against the SPH-5000 nameplate: 1 % is ~40 W, not 50 W, and
`control.rated_power_w = 5000` would make `export_watts` conversions ~20 % optimistic.

### Grid-first mode LINGERS past the slot end time, by a consistent ~80 seconds

Window ended 16:37; the inverter was still in `priorityMode = 2` at **16:38:24** and
only dropped to Load First after an explicit `disable_grid_first_slot`. **Never rely
on the window expiring** - always write the enable register to 0 to end a burst.

**Quantified 2026-09-13** by programming a one-minute window (18:43 -> 18:44, explicit
start/end so no `+1` minute is added) and polling holding `1044` every 4 s:

| Event | Time | Offset |
|---|---|---|
| window opens | 18:43:00 | |
| `priorityMode` 0 -> 2, export ramps to 2800 W | 18:43:15 | **+15 s** |
| window closes | 18:44:00 | |
| `priorityMode` 2 -> 0, export stops | 18:45:18 | **+78 s** |

So a **one-minute slot produces a ~123 second burst**, roughly double. The ~80 s
overrun matches the 16:37 observation, so the inverter appears to re-evaluate its slot
boundaries on an 80-90 s cycle rather than continuously. Practical consequences:

- One minute is the shortest *programmable* window (slots are `HH:MM`), and it costs
  about **96 Wh** of export at a summer-evening load (2800 W for 123 s). Writing the
  enable bit to 0 directly would end it ~80 s sooner and roughly halve that, if the
  extra energy ever matters.
- **Place a burst in the middle of the half hour it is meant to count for**, e.g.
  minute 10 and minute 40. A window in the last two minutes of a half hour will bleed
  into the next one.
- Entry is prompt enough (+15 s) that a burst will not be missed entirely.

Polling `/registers` every 4 s alongside the 20 s poll loop caused **no** inverter read
errors (`readErrorsTotal` and `pollSkippedTotal` both stayed 0); the few failures in the
trace were the client's own 4 s curl timeout waiting on `MODBUS_LOCK`.

**Caveat on measuring the energy:** `sensor.grid_export_power_kwh` in HA moved 200 Wh
for this burst, about double the 96 Wh the power samples support (five consecutive
samples at ~2800 W across 105 s). The power-derived figure is the trustworthy one; that
HA sensor's provenance needs checking before anything relies on it.

### CONFIRMED: grid-first slot 1 and "batt-first slot 6" share registers

First seen as an oddity, then reproduced deliberately on 2026-09-13 with a controlled
probe (no energy cost: a 03:07 window that could not fire, disabled immediately after).

    BEFORE:  grid_first_slot_1 = 18:43-18:44      battery_first_slot_6 = 00:00-00:00
    write:   grid-first slot 1 := 03:07-03:09     (registers 1080/1081/1082)
    AFTER:   grid_first_slot_1 = 03:07-03:09      battery_first_slot_6 = 18:43-18:44

So writing `1080`/`1081` pushes their **previous** value into `1024`/`1025`, the pair the
code maps as batt-first slot 6 start/end. It is a shadow, one write behind, and it is
reproducible.

**The likely explanation is that the map for this block is wrong, not that the firmware
helpfully mirrors things.** Will's call, and it fits the pattern: the PDF's extended-slot
table has already been "corrected" by +1 in this code
(`# NB slots 4-6 start at 1018, not 1017 as the Growatt PDF says`), the PDF's own
`122` row contradicts itself, and `533`/`180` turned out to be outside the SPH map
entirely. A firmware mirror was a hand-wave; an off-by-something in a block the PDF
describes badly is the ordinary explanation.

Note the block really is written by both functions: `1024`/`1025` read `06:30`/`07:01`
before any grid-first write happened tonight, which is a morning charge window, so the
batt-first automation does write there too.

**Untested: the reverse direction.** Whether writing batt-first slot 6 corrupts
grid-first slot 1's window is unknown, and probing it is not free (it enables a
grid-charge slot and also writes `1090`-`1092`), so it was left alone.

**Practical exposure is nil today** but know the shape of it: the Power Down export
bursts programme grid-first slots 1-3 minutes before a session and tear them down
minutes after, while the batt-first slot 6 window is around 06:30, so they never coexist.
Do not trust `/slots`' `battery_first_slot_6` reading, and treat `grid_first()`'s
`_write(1026, [0])` as writing something in this murky block rather than as a reliable
"clear the batt-first slot 6 enable".

### Known-safe registers for force discharge (use these only)

| Register | Purpose | Status |
|---|---|---|
| `1080`/`1081`/`1082` | grid-first slot 1 start/end/enable | live-verified, this test |
| `1083`-`1085`, `1086`-`1088` | grid-first slots 2/3 | PDF-derived, adjacent to the verified block, read back after writing |
| `1070` | discharge rate %, base ~4000 W | live-verified as a cap |
| `1071` | stop-discharge SOC % | written and read back OK |
| `1044` | priorityMode, read-only in practice | observed going 0 -> 2 -> 0 |
| `1027`-`1035` | grid-first slots 4-6 | **DO NOT USE** - see anomaly above |

### Measuring how much actually went out

`eToGridToday` **sat unchanged at 2.5 kWh** through more than a kilowatt of export,
because it only resolves to 0.1 kWh. It is useless as a burst-level stop condition.
Use the instantaneous `gridExportPowerTotal` (W), which tracked live and responsively,
and integrate it (a Riemann sum sensor in HA) to get watt-hour resolution.

### Export limiting (122/123): DO NOT ENABLE. Tested live 2026-09-13, it kills all output

The plan was `122 = 3` (CT clamp) with a small `123`, leaving `1070` at 100, so the
inverter's own feedback loop would hold a small steady export against a varying house
load. **It does not work on this hardware.** Tested on WCK0CDE013:

| Write | Result |
|---|---|
| FC16 block write of `122`-`123` together | rejected, Modbus exception 3 (illegal data value) |
| `123` alone (FC06), value 175 | **accepted**, reads back as 17.5 % |
| `122 = 3` (CT clamp) | rejected, exception 3 (illegal data value) |
| `122 = 2` (RS232) | rejected, exception 3 (illegal data value) |
| `122 = 1` (RS485 meter) | **accepted, and it stops the inverter producing anything** |

So only `0` and `1` are legal values; the PDF's four-way enum is wrong for this
firmware. And enabling it is actively harmful:

    17:53:17  122 = 1 written, 123 = 175 (17.5 %, i.e. a NON-zero limit)
    17:53:38  battery discharge -> 0 W, house switches fully to grid import
    ...        stayed clamped for ~7 minutes

Note the limit was **175, not 0**, so this is not "I asked for zero export". Enabling
the limiter at all zeroes the output, almost certainly because export limitation needs
an external meter this install does not have, and with nothing to measure the grid with
the inverter fails safe by shutting output down. It does not limit to the requested
watts, it limits to nothing.

**Writing `122` back to 0 does NOT recover it.** The registers read healthy again
(`122 = 0`, `123 = 0`, `1070 = 100`, `1071 = 25`, `priorityMode = 0`) while the battery
stayed at 0 W and the house kept importing. A full-payload diff against a known-good
sample from earlier the same evening showed **no** register difference at all, so the
clamp is latched internally and is not visible in the Modbus map.

**Recovery: write a priority mode.** The `load_first` action (which writes the
batt-first slot 6 and grid-first enables) cleared it immediately: discharge resumed at
1030 W with zero import on the very next poll. Worth knowing before anyone panics and
starts pulling isolators.

`control.set_export_limit()` therefore refuses any mode other than 0 unless you pass
`force=True`, and the HTTP API does not expose `force`. Register `123` is safe to write
on its own and is inert while `122` is 0.

### Why it fails, from the PDF (checked 2026-09-13, and it explains everything)

- **The PDF contradicts itself on `122`.** The description lists four values (0 disable,
  1 enable 485, 2 enable 232, 3 enable CT) but the **Range column says `1/0`**. The
  firmware enforces the Range column, which is exactly why 2 and 3 came back as illegal
  data values. So on an SPH the only selectable limiter is **`1`, the RS485-meter one**.
- **This install has no RS485 meter** (confirmed by Will; the only current sensing is a
  CT clamp). So enabling mode 1 starts a feedback loop with no sensor.
- **`3000 ExportLimitFailedPowerRate`, "the power rate when exportLimit failed", is
  TL-X/TL-XH only.** On a TL-X you could set what the inverter falls back to when the
  limiter loses its meter. The SPH has no such register, and the observed fallback is
  **zero output**. That single missing register is the whole story.
- **`533 LimitDevice`** ("Anti-backflow equipment selection", 1 Meter / 3 CT) would be
  the way to point the limiter at a CT, but it is **outside the SPH register map** and
  reads 0. Same for **`180 MeterLink`**: out of range, so its 0 is not a real "no meter"
  reading, just an unimplemented register.
- **The SPH holding-register map is only `0`-`124` and `1000`-`1124`** (PDF: "Storage
  (SPH Type): 03 register range: 0~124, 1000~1124"). Worth remembering before chasing
  any register outside those two windows.
- `bCTMode` in the storage block reads **0 = wired CT**, consistent with the hardware,
  so the inverter does know it has a CT. It just will not use it for export limiting.
- **Nothing couples `122`/`123` to Grid First.** They sit in the general inverter block
  (`0`-`124`), grid-first sits in `1000`-`1124`, and empirically `122 = 1` was *not*
  inert while the inverter was in Load First: it zeroed output within 21 seconds. So
  this is a firmware limitation, not a sequencing mistake.
