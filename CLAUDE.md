# CLAUDE.md

Context for Claude (and humans) working on this repo.

## What this is

ONE process (`growatt_modbus.py`) sharing the `growatt/` library, doing two jobs:
- **Monitor** (the poll loop): polls Growatt SPH inverters over Modbus TCP and publishes the
  decoded registers to MQTT, with Home Assistant MQTT Discovery and clock sync.
- **Control** (`growatt/http_api.py`): an in-process HTTP endpoint to switch the battery
  inverter between Battery/Grid/Load First and manage the AC-charge time slots. Driven by
  Home Assistant rest_commands and the Octopus Agile scheduler.

These used to be two containers (the poller, and a lighttpd/CGI control service). They were
merged 2026-06-16: both opened Modbus connections to the same dongle, which bridges every TCP
connection onto one serial line with no framing, so concurrent access garbled frames. In one
process a single `threading.Lock` (`growatt/_modbus_lock.py`) serialises every Modbus session,
and health is an in-memory "last good read" timestamp instead of a live probe.

## Architecture

```
Inverter --RS485--> EW11 / reflashed ShineWiFi-X dongle (:502) --Modbus TCP-->  growatt_modbus.py
                                                                                  |--MQTT--> HA / Grafana / Node-RED
                                                                                  '--HTTP :8085 (/health /slots /mode)--> HA rest_commands
```

- One `ModbusTcpClient` per Modbus session (opened/closed each cycle, or per control op),
  always taken under `MODBUS_LOCK` so the dongle only ever sees one connection.
- A single persistent `paho-mqtt` client and a daemon-thread `http.server` for the process lifetime.
- Config is external: `config.yaml` (see `config.yaml.example`), resolved from
  `$GROWATT_CONFIG`, `/config/config.yaml`, then `./config.yaml`, layered over
  `DEFAULT_CONFIG`, with a few env-var overrides.

## Key files

- `growatt/` - shared library: `client.py` (Modbus read/write, time-sync; read/write helpers
  take an optional `device_id`), `monitor.py` (register decode), `registers.py` (`SENSOR_META`),
  `config.py` (config loading + `control_target()`), `control.py` (`InverterControl`: mode
  switches + slot management; `with_control_session()` runs a control op under the lock),
  `http_api.py` (the control + health HTTP server), `_modbus_lock.py` (the global `MODBUS_LOCK`).
- `growatt_modbus.py` - the entrypoint: wires config + MQTT + the HTTP server thread + the poll loop.
- `config.yaml.example` - annotated config template. Real `config.yaml` is git-ignored.
  Holds the only host-specific values; the AC-charge tunables stay hard-coded in `control.py`.
- `Dockerfile` / `docker-compose.yaml` - the single image (mounts `./config` at `/config`, serves :8085).
- `shinewifi-bridge/` - ESPHome firmware to reflash a Growatt ShineWiFi-X dongle into a
  dumb serial-to-TCP Modbus bridge (an EW11 alternative; the dongles are what's deployed
  now). Self-contained: see its `README.md`, `FLASHING.md`, and `POLLER-INTEGRATION.md`.
- `*.pdf` - Growatt and ESS Modbus protocol manuals (reference for the register maps).
- `find_fields.py` / `old_fields.json` - throwaway helpers used while reverse
  engineering the register set. Not part of the runtime.

## Register conventions

- `read_double_reg(high, low, mult)` combines two 16-bit registers into a 32-bit value
  (`high << 16 | low`) and scales it. Most powers/energies use a `0.1` multiplier.
- Holding registers: config/settings. Input registers: live telemetry.
- Inverter RTC (register 45) is asymmetric and verified against a real SPH:
  - Read returns a full 4-digit year (2026); write expects a 2-digit year (year - 2000).
    Writing the 4-digit year is rejected with Modbus `IllegalDataValue`.
  - Time base is UTC. Set with a six-register FC16 write (Y, M, D, h, m, s); the weekday
    register is left for the inverter to derive. This matches the known-good
    octopus_agile_battery_scheduler (control_inverter.py).
  - Earlier "writes fail" symptoms were caused by sending the 4-digit year, not by the
    register being read-only or by EW11 contention.
- `SENSOR_META` is the curated field -> (name, device_class, unit, state_class) map that
  drives HA discovery. Fields not listed are still published in the state payload, they
  just do not get an HA entity. Add to this map to expose more sensors.
- The map is verified against a real SPH but is not exhaustive; the PDFs are the source
  of truth for anything not yet decoded.
- See `REGISTERS.md` for the cross-referenced register findings: verified registers,
  known PDF-vs-code discrepancies (naming/scaling, deliberately not auto-applied), and the
  energy/diagnostic registers added. Treat the running code as ground truth; the PDFs
  contain translation errors and at least one provably-wrong scaling (battery temperature).

## Battery / CAN bus

The BMS values (`bms*`, `maxCellVoltage`/`minCellVoltage`) are read from the inverter's
Modbus registers, which the inverter populates from the battery. The battery pack talks
**CAN bus** to the inverter (confirmed: the ESS protocol PDF defines the genuine per-cell
voltages at `0x0071`+ in the battery's own CAN address space, not in Modbus). The inverter
only proxies a BMS *summary* (max/min cell voltage, module count) into Modbus, so full
per-cell detail is not reachable over the EW11; that would need a CAN interface.

## Deployment (perceptron)

- CI builds and publishes the image to GHCR (`.github/workflows/docker-publish.yml`) on
  push to `main` and on `v*` tags: `ghcr.io/8none1/growatt_modbus:latest` (public, multi-arch).
- perceptron does NOT have a source checkout and does not build. The deploy dir
  `~/docker/growatt_modbus/` holds a standalone `docker-compose.yml` (referencing the GHCR
  image) and the real `config/config.yaml`. Deploy/update is just:
  `cd ~/docker/growatt_modbus && docker compose pull && docker compose up -d`.
- **Control + health are now in this same container** (merged 2026-06-16). It uses
  `network_mode: host` and serves HTTP on :8085. HA rest_commands POST to
  `http://192.168.42.241:8085/mode` (was `/cgi-bin/switch_inverter_mode.py`); the Docker
  healthcheck hits `http://127.0.0.1:8085/health` (in-memory, no Modbus). The old separate
  `~/docker/growatt_control/` container (image `ghcr.io/8none1/growatt_modbus-control`) and
  the `~/docker/lighttpd/` deploy are kept (stopped) for rollback.
- The MQTT broker and Home Assistant both run on perceptron.

## Reverting from the ShineWiFi-X dongles back to the EW11s

On 2026-06-14 both inverters were moved from EW11 bridges (Modbus TCP / MBAP) to reflashed
ShineWiFi-X dongles (raw Modbus-RTU-over-TCP, `framer: rtu`). The `framer` support is
backward-compatible, so **reverting is config + hardware only, no code change/redeploy**.

Quick revert (on perceptron):
1. **Hardware**: power the EW11 back on for each inverter and swap the bridge on the RS485 bus
   (EW11 in, dongle out). Modbus is single-master, never have both on the same inverter at once.
2. **Config**: restore the pre-dongle config (EW11 hosts, no `framer` key) in one step:
   `cp ~/docker/_backups/config-pre-rtu-20260614-203514.yaml ~/docker/growatt_modbus/config/config.yaml`
3. **Restart the process** (it reads config only at startup): `docker restart growatt_modbus`.
   Control + health are in this same process now, so there is nothing else to restart.
4. **Verify**: `docker logs growatt_modbus` shows both serials; `curl http://localhost:8085/health`
   returns `{"status":"ok",...}` within a poll cycle, and `curl http://localhost:8085/slots` returns success.

Reference values:
- **EW11** (revert target): `ew11-1.whizzy.org` (inverter1 / battery / control target),
  `ew11-2.whizzy.org` (inverter2), port 502, Modbus TCP/MBAP = **no `framer` key**, unit 1,
  RS485 9600 8N1.
- **Dongles** (current): `192.168.42.204` (inverter1, MAC 4C:75:25:26:6C:83),
  `192.168.42.205` (inverter2, MAC 58:BF:25:C7:C7:3A), `framer: rtu`, DHCP-reserved.
- Serials are unchanged either way: inverter1 `WCK0CDE013`, inverter2 `WCK0CDE018`.
- **Partial revert** (one inverter only): edit just that device's `host:` back to its EW11 and
  delete its `framer: rtu` line, then `docker restart growatt_modbus`.
- Config backups in `~/docker/_backups/`: `config-pre-rtu-*` (EW11), `config-pre-ipchange-*`
  (dongle pool IPs .188/.18 before reservations), and `control-cutover-*` (the original
  lighttpd CGI + composes, for control-side rollback).

## Open TODOs / remaining work

Captured for the next session. Full discrepancy detail is in `REGISTERS.md`.

**Pending code changes (a "round 2" PR), discussed and agreed in principle:**
- High-confidence fixes to apply:
  - Rename `pvBattPower` (input 35-36) → AC output power, and `pvOutputCurrent` (input 39) →
    AC output current. Confirmed by live maths (V×I = the value); the PDF and the existing
    code names/comment are wrong (they are AC-side, not PV/battery).
  - Apply scaling: `inverterNormalVoltage` (holding 8) ×0.1; `inverterPowerFactor`
    (holding 5) ÷10000 for a real 0-1 PF.
  - Fix the fault reads: use 105 (fault maincode), 107 (fault subcode), 112 (warn maincode),
    111 (warn subcode) as separate values instead of the dubious 32-bit `FaultBitCode`
    (106-107) / `WarningBitCode` (110-111) pairings.
- Hold until verified live: `ACChargePower` (input 116-117), the PDF says energy (kWh), not
  power; confirm during a real grid-charge event before renaming/rescaling.
- `battType` (holding 1048): confirmed Lithium (=1) on this hardware; the input-table enum
  (1=Lithium) is right, the holding-table enum is wrong. Decide whether to map/expose it.
- NB: `battTemperature` (input 1040) must stay raw, the PDF's ×0.1 is wrong (live 19 = 19°C).

**Bake / observe (no code yet):**
- Confirm `bmsReg1112-1123` really are per-cell voltages (watch over a charge/discharge cycle),
  then rename from raw `bmsReg*` to cell voltages.
- Watch the combined HA helper `sensor.growatt_site_load_energy_total` tracks total house load
  sensibly (the "let's try it" one).

**Optional features:**
- Decode faults into readable sensors: map fault/warn maincodes (105/112) to text via the
  published Growatt code tables, and bit-decode the BMS error/warn (1085/1099) using the ESS
  protocol PDF tables (0x0014 protection, 0x0022 warning). The 8 system-fault words
  (input 1001-1008) are NOT decodable from our manual (it defers to a fault list we lack).
- Retire the integral-based Grafana energy panels once the register meters are trusted.

**Long-term ("one day"):**
- A proper Home Assistant custom component / HACS integration instead of MQTT discovery.
- A CAN-bus reader for full per-cell battery detail (the inverter only proxies a BMS summary
  over Modbus; true per-cell voltages live in the battery's CAN/ESS protocol at 0x0071+).

**Context worth knowing (see also the memory files):**
- The charge schedule is owned by Will's `octopus_agile_battery_scheduler` via the CGI
  `~/docker/lighttpd/www/cgi-bin/switch_inverter_mode.py` on perceptron; it reprograms the
  Battery-First slots for cheap Agile windows, so manually disabling a slot is only temporary.
- HA Energy Dashboard now reads the register meters (solar = combined both-inverter site
  helper; grid/battery = inverter1). Grafana "Solar NEW" dashboard has register-meter cells,
  a fixed Cell Voltage panel, and a Derating Mode graph.

## Conventions for changes

- British English in prose, no em dashes (Will's preference).
- Keep the register decode readable; the aligned-assignment style is intentional.
- Do not commit a real `config.yaml`; only update `config.yaml.example`.
