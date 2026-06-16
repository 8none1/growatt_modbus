# Growatt Modbus tools (monitor + control)

Talk to one or more **Growatt SPH** hybrid inverters over Modbus. A single process
(`growatt_modbus.py`), built on the `growatt/` Python library, does two jobs:

- **Monitor** (the poll loop): polls the inverters, decodes the registers into friendly
  metrics, publishes them to MQTT, auto-corrects the inverter clock, and publishes
  [Home Assistant MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
  configs so every sensor appears in Home Assistant with no manual YAML.
- **Control** (`growatt/http_api.py`, an in-process HTTP server on :8085): switch the battery
  inverter between Battery / Grid / Load First and manage the AC-charge time slots. Driven by
  Home Assistant `rest_command`s and an Octopus Agile cheap-rate charging scheduler.

Both run in one process so a single `threading.Lock` serialises all Modbus access to the
shared dongle (which has no on-device request/response framing), and `/health` can report
from an in-memory "last good read" timestamp without touching the inverter.

```
  Growatt SPH inverter(s)
        │  RS485 (SYS / COM port)
        ▼
  Bridge: Elfin EW11  or  reflashed ShineWiFi-X dongle   (TCP server on :502)
        │  Modbus over your LAN  (TCP/MBAP for the EW11, RTU-over-TCP for a dongle)
        ▼
  growatt_modbus.py  (one process, MODBUS_LOCK serialises every session)
        ├──► MQTT: growatt/<serial>/state, homeassistant/sensor/... (HA discovery)
        └──► HTTP :8085  GET /health · GET /slots · POST /mode   (HA rest_commands + Agile scheduler)
```

## Why

Growatt's own cloud is slow and laggy. The inverter exposes everything over Modbus on
its RS485 port, so an [Elfin EW11](https://www.hi-flying.com/elfin-ew10-elfin-ew11)
WiFi to RS485 bridge lets you read it locally and in near real time, while leaving the
original WiFi dongle in place. Background:
<https://www.whizzy.org/2023-02-18-using-influx-to-gain-power-insights/>.

## Hardware: the EW11 bridge

The Growatt SPH has an RS485 interface on its communication port (the same bus the WiFi
/ ShineLink dongle uses). Wire the EW11 to the RS485 A/B lines and power it, then
configure it as a **TCP server**:

| EW11 setting   | Value                                  |
| -------------- | -------------------------------------- |
| Protocol       | Modbus TCP (raw TCP passthrough)       |
| Role           | TCP Server                             |
| Port           | 502                                    |
| Baud rate      | 9600                                   |
| Data / parity  | 8 / None / 1 (8N1)                      |

Give the EW11 a stable address (DHCP reservation or static IP) and ideally a hostname,
then point this service at it. You can wire two inverters to two EW11s, or chain them on
one RS485 bus with different Modbus unit IDs.

> **Note on the battery.** The battery BMS data you see here is proxied through the
> inverter's Modbus registers. The battery pack itself is believed to talk **CAN bus**
> to the inverter, not Modbus, so it cannot be read directly over the EW11. See
> `CLAUDE.md` for the open investigation.

## Alternative bridge: a reflashed ShineWiFi-X dongle

You do not have to use an EW11. The Growatt **ShineWiFi-X** WiFi dongle (the one that
plugs into the inverter's "USB" port, which is really TTL serial, not USB) contains an
ESP8266, and it can be reflashed with [ESPHome](https://esphome.io/) to act as the same
kind of dumb serial-to-TCP Modbus bridge, no external box, no RS485 wiring. The firmware
and full instructions are in [`shinewifi-bridge/`](shinewifi-bridge/).

The one difference for this service: a reflashed dongle presents **raw Modbus
RTU-over-TCP** (not Modbus TCP / MBAP like the EW11), so set **`framer: rtu`** on that
device in `config.yaml`. Everything else, decode, MQTT, HA discovery, clock sync and
control writes, is identical. You can mix EW11s and dongles across inverters freely.

## Configuration

All settings live in `config.yaml`. Copy the example and edit it:

```bash
cp config.yaml.example config.yaml
$EDITOR config.yaml
```

Key options (see `config.yaml.example` for the full annotated file):

- `poll_interval` - seconds between polls.
- `read_retries` - re-read attempts within a poll cycle before giving up, to ride out the
  occasional garbled/short frame from a raw RTU-over-TCP dongle (default 3).
- `devices` - list of inverters: `name`, `host` (the EW11 or dongle), `port`, `unit`, and
  optional `framer: rtu` (set this for a reflashed ShineWiFi-X dongle; omit it for an EW11).
- `mqtt.broker` / `mqtt.port` / `mqtt.username` / `mqtt.password`.
- `mqtt.topic_prefix` - per-device data goes to `<prefix>/<serial>/state`.
- `mqtt.discovery` - publish Home Assistant discovery configs (true/false).
- `time_sync.enabled` / `time_sync.max_drift_seconds` - correct the inverter RTC if it
  drifts (useful when scheduling charge windows, e.g. with Octopus Agile).
- `http.port` - port for the in-process control + health endpoint (default 8085).
- `health.stale_after_seconds` - `/health` reports stale if the control inverter has not
  been read within this long (default 600).

The config file path is resolved in this order: `$GROWATT_CONFIG`, `/config/config.yaml`,
then `./config.yaml`. A few values can be overridden by environment variables
(`MQTT_BROKER`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `LOG_LEVEL`), which is handy in
containers.

## Running

### With Docker (recommended)

A multi-arch image (amd64 and arm64) is published to the GitHub Container Registry, so
you do not need to build anything. The compose file mounts `./config` into the container
at `/config`:

```bash
mkdir -p config
cp config.yaml.example config/config.yaml   # then edit config/config.yaml
docker compose pull
docker compose up -d
docker compose logs -f
```

To update later, `docker compose pull && docker compose up -d`.

### Build it yourself

If you would rather build the image locally (e.g. you are hacking on the code):

```bash
docker build -t ghcr.io/8none1/growatt_modbus:latest .
docker compose up -d        # uses the image you just built
```

### Directly with Python

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml           # then edit it
python growatt_modbus.py
```

## MQTT output

For each inverter, every successful poll publishes:

- **`growatt/<serial>/state`** - a single retained JSON document with all decoded
  holding and input registers merged together. This is what Home Assistant and telegraf read.
- **`growatt/<serial>/diagnostics`** - retained read-health counters for the device
  (`readErrorsTotal`, `pollSkippedTotal`, `pollOkTotal`, `lastCycleRetries`), so dongle
  flakiness can be tracked over time. Published every cycle, including ones that failed.

Reads are all-or-nothing: a garbled or short frame from a flaky dongle is rejected (rather
than decoded into out-of-range nonsense) and re-tried up to `read_retries` times within the
cycle; if it still fails the whole cycle is skipped rather than publishing partial data.

On startup the service also publishes retained discovery configs under
`homeassistant/sensor/<serial>_<field>/config` for a curated set of useful sensors
(PV power, battery SOC, temperatures, grid import/export, etc) plus the read-health
diagnostic sensors above.

## Home Assistant

With `mqtt.discovery: true` and the
[MQTT integration](https://www.home-assistant.io/integrations/mqtt/) configured against
the same broker, a **Growatt** device appears automatically with all the sensors
populated and correct units / device classes. No `configuration.yaml` editing required.

## Controlling the inverter

Besides reading, the process serves a control + health HTTP endpoint on port 8085
(`growatt/http_api.py`), sharing the same `growatt/` library and `config.yaml` as the poll loop
(the `control:` section picks which inverter to command). All control Modbus access runs under
the same `MODBUS_LOCK` as the poll loop, so it can never collide with a poll on the dongle.

- **GET** `/health` returns `200 {"status":"ok",...}` if the control inverter was read
  successfully within `health.stale_after_seconds` (default 600), else `503 {"status":"stale"}`.
  In-memory only - it never touches the inverter, so it is cheap to poll (e.g. as a Docker healthcheck).
- **GET** `/slots` returns the current Battery-First / Grid-First time slots as JSON.
- **POST** `/mode` `{"action": ...}` switches mode or edits the AC-charge slots:
  - `switch_inverter_to_batt_first_mode` (`duration`, `slot_num`) - charge from the grid for a window
  - `switch_inverter_to_grid_first_mode` (`duration`) - force-discharge to the grid
  - `switch_inverter_to_load_first_mode` - return to normal (self-use)
  - `disable_batt_first_slot` (`slot_num`), `clear_all_slots`

Home Assistant drives these via `rest_command`s, and an Octopus Agile scheduler charges the
battery during cheap half-hours. The AC-charge tuning values (charge rate, stop-SOC) are
intentionally hard-coded in `growatt/control.py`; only the inverter host comes from config.

## Register notes

The decoding lives in the shared `growatt/` package (`growatt/monitor.py`); see also
[`REGISTERS.md`](REGISTERS.md) for the cross-referenced register map and the known PDF-vs-code
discrepancies. A few conventions worth knowing:

- 32-bit values span two 16-bit registers and are combined with `read_double_reg()`
  (`high << 16 | low`), then scaled by a multiplier (commonly `0.1`).
- The RTC (register 45) is asymmetric: the inverter **reports** a full 4-digit year
  on read, but **expects** a 2-digit year (`year - 2000`) on write. Time is held in
  **UTC**, and the clock is set with a six-register FC16 write (year, month, day, hour,
  minute, second). Writing the full 4-digit year is rejected with `IllegalDataValue`.
- Full register definitions are in the Growatt Modbus protocol PDFs in this repo. The
  field map here is a curated subset that has been verified against a real SPH; it is not
  exhaustive.

## Credits

- Original Node-RED implementation and EW11 write-up:
  <https://github.com/8none1/growatt_sph_nodered>
- Background article: <https://www.whizzy.org/2023-02-18-using-influx-to-gain-power-insights/>

## Licence

See [LICENSE](LICENSE).
