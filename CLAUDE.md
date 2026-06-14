# CLAUDE.md

Context for Claude (and humans) working on this repo.

## What this is

A single-purpose service (`growatt_modbus.py`) that polls Growatt SPH inverters over
Modbus TCP and publishes the decoded registers to MQTT, with optional Home Assistant
MQTT Discovery and inverter clock syncing. It runs as a Docker container.

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

- `growatt_modbus.py` - everything: config loading, Modbus decode, MQTT, HA discovery,
  the poll loop.
- `config.yaml.example` - annotated config template. Real `config.yaml` is git-ignored.
- `docker-compose.yaml` / `Dockerfile` - container build and run. Compose mounts
  `./config` at `/config`.
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
- The MQTT broker and Home Assistant both run on perceptron.

## Open TODOs

- Decide on the PDF-vs-code discrepancies in `REGISTERS.md` (naming/scaling), case by case.
- Confirm the input 1112-1123 per-cell-voltage hypothesis (observe over a charge/discharge cycle).
- Optionally decode the system fault words (input 1001-1008) and BMS warning bitfield
  (1098/1099) into human-readable sensors.
- Possible future: a proper Home Assistant custom component / HACS integration instead of
  the MQTT-discovery approach; and a CAN reader for full per-cell battery detail.

## Conventions for changes

- British English in prose, no em dashes (Will's preference).
- Keep the register decode readable; the aligned-assignment style is intentional.
- Do not commit a real `config.yaml`; only update `config.yaml.example`.
