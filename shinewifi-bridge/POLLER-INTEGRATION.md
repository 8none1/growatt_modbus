# Backend integration: the `framer: rtu` knob (hand-off note)

For whoever maintains the Python side (`growatt_modbus.py` + the `growatt/` package).
This describes a small change that is **coming later**, not yet in the tree, so you can
plan around it. It will land as its own PR once a reflashed dongle is tested end-to-end.

## TL;DR

A reflashed ShineWiFi-X dongle (see this directory) is a **raw** serial-to-TCP bridge.
Raw passthrough means the TCP socket carries **Modbus RTU frames** (with CRC), i.e.
"RTU-over-TCP", **not** Modbus TCP with the MBAP header. The EW11, as configured today,
presents Modbus TCP (MBAP), which is why the current code uses the default framer.

So talking to a dongle needs the RTU framer for that one device. Everything else
(register map, scaling, MQTT, HA discovery, time sync, control writes) is identical.

## The change

Add an **optional, per-device** `framer` key to the device config. Default keeps today's
behaviour (Modbus TCP / MBAP), so **all existing EW11 entries are untouched**.

```yaml
# config.yaml
devices:
  - name: inverter1            # EW11, unchanged -> default MBAP framer
    host: ew11-1.example.com
    port: 502
    unit: 1
  - name: inverter2            # reflashed ShineWiFi-X dongle
    host: shinewifi-x-2.example.com
    port: 502
    unit: 1
    framer: rtu                # <-- raw RTU-over-TCP
```

`unit` / slave id is unchanged (still 1). Only the framing differs.

## Call sites that must honour it (there are THREE, not two)

A dongle replacing the EW11 on the battery inverter means the **control** path uses the
framer too, or writes (RTC sync, Battery-First slot programming) fail. Three edit points:

1. **Monitor** - `growatt_modbus.py:89`, in `poll_device(dev, ...)`. It has `dev`, so it's
   a one-liner:
   ```python
   from growatt.config import device_framer   # helper below
   client = ModbusTcpClient(host=dev["host"], port=dev.get("port", 502),
                            framer=device_framer(dev))
   ```

2. **Control client** - `growatt/control.py`, `InverterControl.__init__`
   (currently `def __init__(self, host, port=502, device_id=1, timeout=5)`, line ~42).
   Add a `framer` parameter and thread it into the client:
   ```python
   def __init__(self, host, port=502, device_id=1, timeout=5, framer=None):
       ...
       kwargs = {"port": port, "timeout": timeout}
       if framer is not None:
           kwargs["framer"] = framer
       self.client = ModbusTcpClient(host, **kwargs)
   ```

3. **Control entry point** - `cgi/switch_inverter_mode.py:41` + `control_target()` in
   `growatt/config.py:43`. **This is the easy one to miss and it 500s in production if
   you do.** The CGI unpacks a 3-tuple positionally:
   ```python
   host, port, device_id = control_target(config)      # cgi/switch_inverter_mode.py:41
   inv = InverterControl(host, port, device_id)
   ```
   **Do not silently widen `control_target` to a 4-tuple** - that unpack breaks and the
   control container errors on every request. Two safe options:
   - **Preferred:** change `control_target` to return a small dict/dataclass
     (`{host, port, device_id, framer}`) and update the single caller (cgi:41) to match.
     Named fields stop future additions from rippling out.
   - Or have `control_target` also return the selected `dev` so the CGI can call
     `device_framer(dev)`. Note the CGI does **not** currently hold the `dev` object, so a
     bare `device_framer` helper alone is not enough at this site - it needs the device.

   Then pass it through: `inv = InverterControl(host, port, device_id, framer=...)`.

Shared helper to avoid duplicating the map, in `growatt/config.py`:

```python
from pymodbus import FramerType   # see version note below

def device_framer(dev):
    """Map a device's optional 'framer' key to a pymodbus framer (default MBAP)."""
    return FramerType.RTU if (dev or {}).get("framer") == "rtu" else FramerType.SOCKET
```

## pymodbus version note (verify on BOTH images)

The monitor and control containers install pymodbus from different sources, so the framer
API must be confirmed in each, they can resolve to different versions:

- **poller image:** `python:3.12-slim` + `pip install pymodbus` (latest).
- **control image:** the wolfi `lighttpd-chainguard` base + `py3-pip` pymodbus.

API split to watch:
- pymodbus **3.5+**: `from pymodbus import FramerType; ModbusTcpClient(..., framer=FramerType.RTU)`.
- pymodbus **3.0-3.4**: `from pymodbus.framer import ModbusRtuFramer / ModbusSocketFramer`
  (pass the class, not an enum).

If a dongle ever replaces the **battery** inverter (`ew11-1`, the control target), the
control image's pymodbus must support the framer too, not just the poller's. Easiest
insurance: **pin `pymodbus` in `requirements.txt`** and confirm the wolfi build resolves
the same major, or explicitly import/exercise the framer in both containers before
relying on it.

## Behaviour / gotchas to expect

- **Decode is unchanged.** Same input/holding register reads, same `SENSOR_META`, same
  MQTT topics and HA discovery. Only the wire framing changes.
- **Control writes work the same.** Reads and writes go through the same client, so the
  FC16 RTC sync and the Battery-First slot writes work over RTU-over-TCP once the control
  path uses the framer.
- **One master per bus.** A dongle and an EW11 must not poll the same inverter at once
  (Modbus is single-master). The dongle *replaces* the EW11 on that inverter.
- **Timing.** RTU normally frames by inter-character gaps on the serial line; over TCP,
  pymodbus frames by length + CRC. This is generally robust, but if you see occasional
  truncated/short reads, bump the client `timeout` a little. The raw bridge does no
  buffering or reassembly beyond the UART buffer.
- **Port stays 502** so the only config delta for a dongle is adding `framer: rtu`.

## Status

Deferred until a dongle is flashed and verified against a real SPH. Tracked alongside the
firmware in this directory; firmware merged in PR #10.
