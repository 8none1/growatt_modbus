# ShineWiFi-X bridge firmware

ESPHome firmware that turns a reflashed **Growatt ShineWiFi-X** USB dongle into a
**dumb serial-to-TCP Modbus bridge**, i.e. a drop-in replacement for an Elfin EW11.

It does **not** decode anything. It exposes the inverter's serial Modbus line as a raw
TCP socket on port 502 so the `growatt_modbus.py` poller in the parent repo can keep
doing all the decoding, MQTT publishing, HA discovery and clock syncing. The dongle
"sits there doing nothing until asked to poll".

This lives as a subdirectory of the [`growatt_modbus`](../README.md) project: the
firmware exists only to feed that poller, so they share one repo and one source of truth.

> **Status:** in production. Two units are deployed, one per inverter, replacing the
> EW11s. See the parent `CLAUDE.md` for the (config-only) procedure to revert to EW11s.

## Why a dumb bridge (and not native ESPHome decode)

All the hard-won logic, the register map, the 32-bit pairings, the UTC RTC quirk, the
Agile-driven charge scheduling with its subtle register writes, lives in one tested
Python codebase. Reimplementing that on a constrained ESP8266 would mean abandoning the
single source of truth and porting control logic into fiddly YAML lambdas. Instead we
keep transport (get bytes on/off the bus) separate from logic (decode/decide):

```
Inverter --TTL UART (115200 8N1)--> ESP8266 in dongle --WiFi/raw TCP :502--> growatt_modbus.py --MQTT--> HA / Grafana
```

If the dongle ever misbehaves you drop the EW11 back in and nothing else changes.

## The "USB" port is not USB

The Growatt inverter's USB-A port is a USB connector used only as a physical form
factor. The pins carry **TTL-level UART serial** (115200 8N1) plus power, there is no USB
data protocol. The ESP8266 inside the dongle is the Modbus **master**; the inverter is
slave address 1. This is the same Modbus the EW11 speaks over RS485, just over a
different physical wire.

## Hardware

- **MCU:** ESP8266. The units here are **ESP8266EX with 4MB flash** (confirmed with
  `esptool flash-id`), so the ESPHome board profile is **`esp12e`** (a generic 4MB
  ESP8266 target, not a claim about the physical module). Older 1MB dongles would use
  `esp01_1m`.
- **Inverter UART:** the ESP hardware UART0 (TXD0 = GPIO1, RXD0 = GPIO3), 115200 8N1.
- **LEDs:** green = GPIO0, red = GPIO2, blue = GPIO16 (active-high).
- **Button:** front-panel button on **A0** (analog), active-low.
- A USB-serial chip (CH340 / XR21V1410) is also on the board, but we flash via the ESP's
  header pads with an external 3.3V USB-UART adapter (see `FLASHING.md`).

## What the firmware exposes

Once flashed and adopted into Home Assistant (the API key matches), each dongle gives you:

- **The bridge** itself: raw Modbus on TCP `:502`.
- **Status LEDs:**
  - **green** = 1Hz heartbeat. Movement means the firmware loop is alive and healthy; if
    it freezes (solid or off) the device has wedged. This is the LED visible through the
    case window, so it carries the health signal.
  - **red** = ESPHome status: off when healthy, blinks on warning/error.
  - **blue** = lit while a poller is connected to `:502`. Because the poller opens and
    closes the connection each poll, this naturally pulses once per cycle, a free
    activity light.
- **Front-panel button** (A0): a press fires a Home Assistant event
  `esphome.shinewifi_button` with `data.device` set to the node name, hang automations
  off it (e.g. "locate this dongle", trigger something, etc.).
- **Diagnostics in HA:** WiFi signal, uptime, IP address, ESPHome version, active
  connection count, and a raw "Button level" sensor (handy if you ever need to re-tune
  the button threshold).
- **Management buttons:** Restart and Safe-mode-boot (in HA / the ESPHome dashboard).

## Raw RTU-over-TCP, not Modbus TCP (important)

`stream_server` is a **raw** byte passthrough. The TCP port therefore carries raw Modbus
**RTU** frames (with CRC), not Modbus **TCP** (MBAP header). An EW11, as configured for
this project, presents Modbus TCP, so for a dongle the poller must use the RTU framer for
that one device. This is already implemented in the parent project: just set
`framer: rtu` on the device in `config.yaml` (see `../config.yaml.example`). EW11 entries,
with no `framer` key, are unaffected. Implementation detail and the control-path notes
live in [`POLLER-INTEGRATION.md`](POLLER-INTEGRATION.md).

```yaml
# ../config.yaml
devices:
  - name: inverter1
    host: 192.168.x.y          # the dongle's (reserved) IP
    port: 502
    unit: 1
    framer: rtu                # <-- raw RTU-over-TCP
```

## Using it: operational notes

- **First flash is over serial; everything after is OTA** over WiFi (no need to open the
  case again). See `FLASHING.md`.
- **Give each dongle a DHCP reservation.** mDNS (`shinewifi-x-1.local`) works fine for you
  and the ESPHome tooling, but it does **not** resolve from inside slim Docker containers
  (no Avahi), so the poller container must reach the dongle by **IP**. A
  reservation keeps that IP stable.
- **One master per bus.** The inverter's USB/serial and its RS485 port are the same Modbus
  bus, and Modbus is single-master. Never have both a dongle and an EW11 polling the same
  inverter at once, the dongle *replaces* the EW11.
- **Secrets are baked into the build.** ESPHome compiles your WiFi credentials, API key
  and OTA password into the `.bin`. So neither the stock dump nor the built firmware is
  safe to commit to a public repo; `firmware-backups/` is git-ignored for exactly this
  reason. Keep the stock dumps somewhere safe, they are your only restore path.

## Files

- `shinewifi-x-bridge.yaml` - the ESPHome config (this is the canonical, full-featured
  example). Copy/duplicate per dongle and set `name` in the substitutions.
- `secrets.yaml.example` - copy to `secrets.yaml` (git-ignored) and fill in. The secret
  names match ESPHome's defaults, so a shared ESPHome `secrets.yaml` works as-is.
- `FLASHING.md` - one-time serial flash: back up the stock firmware first, identify the
  board, enter the bootloader (GPIO0 to GND), and flash. Includes the hard-won gotchas.
- `POLLER-INTEGRATION.md` - how the poller/control side talks to a dongle (`framer: rtu`),
  and post-cutover hardening notes.
- `firmware-backups/` - git-ignored. Stock dumps and built binaries live here; they
  contain secrets, do not commit them.

## Build / flash / deploy

First flash (serial), see `FLASHING.md` for the bootloader dance:

```bash
pip install esphome
cp secrets.yaml.example secrets.yaml      # then edit
esphome run shinewifi-x-bridge.yaml       # compile + serial upload
```

Subsequent updates (OTA, over the network). If you manage ESPHome from a container/host
elsewhere (as here, an ESPHome container on the HA box), the pattern is:

```bash
esphome compile shinewifi-x-1.yaml                          # compile first...
esphome upload  shinewifi-x-1.yaml --device shinewifi-x-1.local   # ...then OTA. `upload` does NOT recompile.
```

Verify with `esphome logs shinewifi-x-1.yaml` (over the network), or check the dongle's
diagnostics in Home Assistant.
