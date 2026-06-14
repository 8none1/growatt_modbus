# ShineWiFi-X bridge firmware

ESPHome firmware that turns a reflashed **Growatt ShineWiFi-X** USB dongle into a
**dumb serial-to-TCP Modbus bridge**, i.e. a drop-in replacement for an Elfin EW11.

It does **not** decode anything. It exposes the inverter's serial Modbus line as a raw
TCP socket on port 502 so the `growatt_modbus.py` poller in the parent repo can keep
doing all the decoding, MQTT publishing, HA discovery and clock syncing. The dongle
"sits there doing nothing until asked to poll".

This lives as a subdirectory of the [`growatt_modbus`](../README.md) project: the
firmware exists only to feed that poller, so they share one repo and one source of truth.

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

- **MCU:** ESP8266.
  - Older ShineWiFi-X: ESP07 / ESP07S module (1MB flash) -> board `esp07s`.
  - Newer (4MB flash): ESP12E -> board `esp12e`. Set this in `substitutions.board`.
- **Inverter UART:** the ESP hardware UART0 (TXD0 = GPIO1, RXD0 = GPIO3), 115200 8N1.
- A USB-serial chip (CH340 or XR21V1410) is also on the board, see `FLASHING.md`.

## Raw RTU-over-TCP, not Modbus TCP (important)

`stream_server` is a **raw** byte passthrough. The TCP port therefore carries raw Modbus
**RTU** frames (with CRC), not Modbus **TCP** (MBAP header). The EW11, as currently
configured, presents Modbus TCP, so the poller talks MBAP to it. To talk to this bridge
the poller needs the RTU framer for that one device.

This is a tiny, additive change in the `growatt_modbus` repo (separate PR). EW11 entries
are untouched; only the dongle entry opts in:

```yaml
# config.yaml
devices:
  - name: inverter1            # existing EW11, unchanged (Modbus TCP / MBAP)
    host: ew11-1.example.com
    port: 502
    unit: 1
  - name: inverter2            # reflashed ShineWiFi-X dongle
    host: shinewifi-x-2.example.com
    port: 502
    unit: 1
    framer: rtu                # <-- raw RTU-over-TCP
```

```python
# poller side (sketch)
from pymodbus.client import ModbusTcpClient
from pymodbus import FramerType

framer = FramerType.RTU if dev.get("framer") == "rtu" else FramerType.SOCKET
client = ModbusTcpClient(host=dev["host"], port=dev.get("port", 502), framer=framer)
```

## Files

- `shinewifi-x-bridge.yaml` - the ESPHome config. Copy/duplicate per dongle and set
  `name` / `board` in the substitutions.
- `secrets.yaml.example` - copy to `secrets.yaml` and fill in. `secrets.yaml` is
  git-ignored.
- `FLASHING.md` - how to open the dongle, back up the stock firmware (do this first!),
  enter flash mode (GPIO0 -> GND), and flash.

## Build / flash / deploy

```bash
pip install esphome
cp secrets.yaml.example secrets.yaml   # then edit
esphome run shinewifi-x-bridge.yaml    # first flash over serial; later updates via OTA
```

See `FLASHING.md` for the one-time serial flash procedure.
