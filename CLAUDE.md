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
- Inverter RTC year is two digits: reads add 2000, writes subtract 2000.
- `SENSOR_META` is the curated field -> (name, device_class, unit, state_class) map that
  drives HA discovery. Fields not listed are still published in the state payload, they
  just do not get an HA entity. Add to this map to expose more sensors.
- The map is verified against a real SPH but is not exhaustive; the PDFs are the source
  of truth for anything not yet decoded.

## Battery / CAN bus

The BMS values (`bms*`, `cellVoltage*`) are read from the inverter's Modbus registers,
which the inverter populates from the battery. The battery pack itself is believed to
talk **CAN bus** to the inverter, not Modbus, so it cannot be queried directly over the
EW11. Reading the pack directly would need a CAN interface, not this tool.

## Deployment (perceptron)

- Git checkout lives at `/home/will/source/growatt_modbus`.
- `docker-compose.yaml` is symlinked into `/home/will/docker/growatt_modbus/`.
- Deploy flow: pull, ensure `config/config.yaml` exists, `docker compose build`,
  `docker compose up -d`. The image is built locally (no CI / registry yet).
- The MQTT broker and Home Assistant both run on perceptron.

## Open TODOs

- Investigate the two protocol PDFs to validate and extend the register map (good
  candidate for a fan-out review). Many registers are still undecoded or guessed.
- Confirm the CAN-bus battery theory and decide whether a separate CAN reader is worth it.
- Possible future: a proper Home Assistant custom component / HACS integration instead of
  the MQTT-discovery approach.

## Conventions for changes

- British English in prose, no em dashes (Will's preference).
- Keep the register decode readable; the aligned-assignment style is intentional.
- Do not commit a real `config.yaml`; only update `config.yaml.example`.
