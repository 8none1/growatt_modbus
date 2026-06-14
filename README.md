# Growatt Modbus to MQTT bridge

A small Python service that polls one or more **Growatt SPH** hybrid inverters over
Modbus TCP, decodes the registers into friendly metrics, and publishes them to MQTT.
It can also auto-correct the inverter's clock and publish
[Home Assistant MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
configs so every sensor appears in Home Assistant with no manual YAML.

```
  Growatt SPH inverter
        │  RS485 (SYS / COM port)
        ▼
  Elfin EW11 (WiFi ↔ RS485 bridge, TCP server on :502)
        │  Modbus TCP over your LAN
        ▼
  growatt_modbus.py  (this service)
        │  MQTT
        ├──► growatt/<serial>/state        (retained JSON, one per inverter)
        ├──► growatt                        (legacy flat topic, back-compat)
        └──► homeassistant/sensor/...       (HA discovery, retained)
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

## Configuration

All settings live in `config.yaml`. Copy the example and edit it:

```bash
cp config.yaml.example config.yaml
$EDITOR config.yaml
```

Key options (see `config.yaml.example` for the full annotated file):

- `poll_interval` - seconds between polls.
- `devices` - list of inverters: `name`, `host` (the EW11), `port`, `unit`.
- `mqtt.broker` / `mqtt.port` / `mqtt.username` / `mqtt.password`.
- `mqtt.legacy_topic` - the original flat topic, kept for existing consumers.
- `mqtt.topic_prefix` - per-device data goes to `<prefix>/<serial>/state`.
- `mqtt.discovery` - publish Home Assistant discovery configs (true/false).
- `time_sync.enabled` / `time_sync.max_drift_seconds` - correct the inverter RTC if it
  drifts (useful when scheduling charge windows, e.g. with Octopus Agile).

The config file path is resolved in this order: `$GROWATT_CONFIG`, `/config/config.yaml`,
then `./config.yaml`. A few values can be overridden by environment variables
(`MQTT_BROKER`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `LOG_LEVEL`), which is handy in
containers.

## Running

### With Docker (recommended)

The compose file mounts `./config` into the container at `/config`:

```bash
mkdir -p config
cp config.yaml.example config/config.yaml   # then edit config/config.yaml
docker compose build
docker compose up -d
docker compose logs -f
```

### Directly with Python

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml           # then edit it
python growatt_modbus.py
```

## MQTT output

For each inverter, every poll publishes:

- **`growatt/<serial>/state`** - a single retained JSON document with all decoded
  holding and input registers merged together. This is the topic Home Assistant reads.
- **`growatt`** (legacy) - the original behaviour: two separate JSON messages (input
  registers, then holding registers). Kept so existing Grafana / Node-RED pipelines do
  not break.

On startup the service also publishes retained discovery configs under
`homeassistant/sensor/<serial>_<field>/config` for a curated set of useful sensors
(PV power, battery SOC, temperatures, grid import/export, etc).

## Home Assistant

With `mqtt.discovery: true` and the
[MQTT integration](https://www.home-assistant.io/integrations/mqtt/) configured against
the same broker, a **Growatt** device appears automatically with all the sensors
populated and correct units / device classes. No `configuration.yaml` editing required.

## Register notes

The decoding logic lives in `growatt_modbus.py`. A few conventions worth knowing:

- 32-bit values span two 16-bit registers and are combined with `read_double_reg()`
  (`high << 16 | low`), then scaled by a multiplier (commonly `0.1`).
- The inverter stores the RTC year as two digits, so reads add `2000` and writes
  subtract it.
- Full register definitions are in the Growatt Modbus protocol PDFs in this repo. The
  field map here is a curated subset that has been verified against a real SPH; it is not
  exhaustive.

## Credits

- Original Node-RED implementation and EW11 write-up:
  <https://github.com/8none1/growatt_sph_nodered>
- Background article: <https://www.whizzy.org/2023-02-18-using-influx-to-gain-power-insights/>

## Licence

See [LICENSE](LICENSE).
