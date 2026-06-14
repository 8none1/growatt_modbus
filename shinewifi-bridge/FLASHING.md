# Flashing a ShineWiFi-X with this firmware

One-time serial flash. After that, updates are OTA (`esphome run` over the network).

## 0. Back up the stock firmware first (do this!)

This makes the reflash reversible. If anything goes wrong, or you ever want ShinePhone
back, you can restore the original image. Read the full flash before you write anything:

```bash
# 1MB (esp07s) board:
esptool.py --port /dev/ttyUSB0 --baud 115200 read_flash 0x0 0x100000 shinewifi-x-stock-1MB.bin

# 4MB (esp12e) board:
esptool.py --port /dev/ttyUSB0 --baud 115200 read_flash 0x0 0x400000 shinewifi-x-stock-4MB.bin
```

Store these `.bin` files somewhere safe (they are git-ignored here). Restore with
`write_flash 0x0 <file>`.

## 1. Open the dongle and identify the module

Crack the case and find the ESP8266 module:

- **ESP07 / ESP07S** (1MB) -> keep `board: esp07s` in the YAML substitutions.
- **ESP12E** (4MB) -> set `board: esp12e`.

While you're in there, note the USB-serial chip (CH340 or XR21V1410) and **trace where
the USB-A connector pins go**. This answers the open question below.

## 2. Decide how to reach the ESP for flashing

The ESP's programming UART is GPIO1 (TX) / GPIO3 (RX), 3.3V TTL. Two possible routes:

- **Via the USB-A connector** - *if* the onboard CH340/XR21V1410 is wired between the USB
  connector and the ESP UART, plugging the dongle into this machine's USB will enumerate
  as a serial device (`/dev/ttyUSB0` or `/dev/ttyACM0`) and you can flash straight in.
  This is the convenient path **if the wiring supports it** - confirm by tracing in step 1
  (or just plug in and see whether a serial device appears).
- **Via the header/pads** - otherwise, clip a 3.3V USB-UART adapter (FTDI/CP2102) onto the
  ESP's TX/RX/GND/3V3 pads directly. **Never use a 5V adapter**, it will damage the ESP.

> Open question to settle on the first dongle: which route works on your boards. Update
> this file with the answer once confirmed.

## 3. Enter bootloader (flash) mode

Hold **GPIO0 to GND while powering on** the board. On the ShineWiFi-X you bridge the
header's GPIO0 and GND pins as you plug it in / apply power. Release after power-up. The
ESP is now in serial bootloader mode.

## 4. Flash

```bash
pip install esphome
cp secrets.yaml.example secrets.yaml      # then edit
esphome run shinewifi-x-bridge.yaml       # choose the serial port when prompted
```

ESPHome compiles, then uploads over serial. After the first successful boot it joins
wifi and all later updates are OTA, no need to open the case again.

## 5. Verify

- `esphome logs shinewifi-x-bridge.yaml` (over the network) should show wifi connected and
  the stream_server listening on :502.
- From another machine: `nc <dongle-ip> 502` and the poller (with `framer: rtu`) should
  start getting register reads.

## Recovery

If a flash bricks monitoring: unplug the dongle, drop the EW11 back onto the inverter,
and the rest of the system is unaffected. Re-flash the dongle on the bench at leisure (or
restore the stock backup from step 0).
