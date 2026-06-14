# CLAUDE.md

Context for Claude (and humans) working on this repo.

## What this is

Two services that share one `growatt/` library:
- **Monitor** (`growatt_modbus.py`): polls Growatt SPH inverters over Modbus TCP and
  publishes the decoded registers to MQTT, with Home Assistant MQTT Discovery and clock sync.
- **Control** (`cgi/switch_inverter_mode.py`): an HTTP/CGI endpoint to switch the battery
  inverter between Battery/Grid/Load First and manage the AC-charge time slots. Driven by
  Home Assistant rest_commands and the Octopus Agile scheduler. (Formerly a standalone,
  unversioned CGI on perceptron; consolidated into this repo.)

## Architecture

```
Inverter --RS485--> Elfin EW11 (TCP server :502) --Modbus TCP--> growatt_modbus.py --MQTT--> HA / Grafana / Node-RED
```

- One `ModbusTcpClient` per inverter per poll (opened and closed each cycle).
- A single persistent `paho-mqtt` client for the lifetime of the process.
- Config is external: `config.yaml` (see `config.yaml.example`), resolved from
  `$GROWATT_CONFIG`, `/config/config.yaml`, then `./config.yaml`, layered over
  `DEFAULT_CONFIG`, with a few env-var overrides.

## Key files

- `growatt/` - shared library: `client.py` (Modbus read/write, time-sync; read/write helpers
  take an optional `device_id`), `monitor.py` (register decode), `registers.py` (`SENSOR_META`),
  `config.py` (config loading + `control_target()`), `control.py` (`InverterControl`: mode
  switches + slot management).
- `growatt_modbus.py` - the poller: wires config + MQTT + the poll loop around `growatt/`.
- `cgi/switch_inverter_mode.py` - the control CGI (thin wrapper over `growatt.control`).
- `config.yaml.example` - annotated config template. Real `config.yaml` is git-ignored.
  Holds the only host-specific values; the AC-charge tunables stay hard-coded in `control.py`.
- `Dockerfile` / `docker-compose.yaml` - the poller image (mounts `./config` at `/config`).
- `Dockerfile.control` / `deploy/control/` - the control image (FROM `lighttpd-chainguard`,
  bakes the CGI + lib + lighttpd.conf) and its deploy compose.
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
  `~/docker/growatt_modbus/` holds a standalone `docker-compose.yaml` (referencing the GHCR
  image) and the real `config/config.yaml`. Deploy/update is just:
  `cd ~/docker/growatt_modbus && docker compose pull && docker compose up -d`.
- **Control container**: `~/docker/growatt_control/` runs `ghcr.io/8none1/growatt_modbus-control`
  (the second CI image) on port 8085, mounting the *same* `~/docker/growatt_modbus/config`. HA
  rest_commands POST to `http://192.168.42.241:8085/cgi-bin/switch_inverter_mode.py`. The CGI is
  now baked into the image, so it is no longer edited in place; change it in this repo and
  redeploy via `docker compose pull`. The old `~/docker/lighttpd/` deploy is kept (stopped) for
  rollback, along with `~/docker/_backups/control-cutover-*`.
- The MQTT broker and Home Assistant both run on perceptron.

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
