# Flashing a ShineWiFi-X with this firmware

One-time serial flash. After that, updates are OTA (`esphome run` over the network).

## 0. Back up the stock firmware first (do this!)

This makes the reflash reversible. If anything goes wrong, or you ever want ShinePhone
back, you can restore the original image. Read the full flash before you write anything.
`ALL` lets esptool auto-detect the size so you do not have to guess 1MB vs 4MB:

```bash
# esptool v5 (hyphenated commands). CP2102 adapters have no auto-reset, so --before no-reset.
esptool --port /dev/ttyUSB0 --baud 115200 --before no-reset read-flash 0x0 ALL \
  firmware-backups/shinewifi-x-stock.bin
```

Store these `.bin` files somewhere safe (the `firmware-backups/` dir here is git-ignored).
Restore with `write-flash 0x0 <file>`.

> **Hard-won lessons (a real ESP-07 / 4MB unit on a CP2102):**
> - **Pick one baud and keep it for the whole command.** Bumping mid-session (e.g. to
>   460800 after a slower connect) drops sync and you get `No serial data received`.
>   115200 is reliable; 230400 usually works and is ~2x faster. 460800 was flaky here.
> - `flash-id` succeeding (it prints chip type, MAC, flash size) **proves the wiring,
>   power and ground are all correct** - if that works, a failed read is a baud or
>   bootloader-state issue, not wiring.

## 1. Open the dongle and identify the module

Crack the case and find the ESP8266 module:

- **ESP07 / ESP07S** (1MB) -> set `board: esp07s` in the YAML substitutions.
- **ESP12E** (4MB) -> `board: esp12e`.

The first unit we backed up reported **ESP8266EX with 4MB flash** via `flash-id`, so the
YAML defaults to `esp12e`. Confirm yours with `flash-id` (it prints the detected size)
rather than guessing from the module label.

## 2. Reach the ESP for flashing

The ESP's programming UART is GPIO1 (TX) / GPIO3 (RX), 3.3V TTL.

**The proven route here: an external 3.3V USB-UART adapter on the ESP's header pads.**
Clip a 3.3V adapter (we used a CP2102) onto the ESP's TX / RX / GND / 3V3 pads directly.
Cross TX/RX (adapter TX -> ESP RX/GPIO3, adapter RX -> ESP TX/GPIO1) and share grounds.
**Never use a 5V adapter**, it will damage the ESP. The CP2102 also powers the board over
those pads, so "power-cycle" = unplug/replug the adapter (see step 3).

> The onboard CH340/XR21V1410 *might* let you flash straight through the USB-A connector
> on some board revisions, but that was not needed or tested here. The header pads are the
> reliable route; use them unless you have a reason not to.

## 3. Enter bootloader (flash) mode

Hold **GPIO0 to GND while powering on** the board, GPIO0 is only sampled at power-up.
When the CP2102 supplies the board's power (common), the USB plug-in *is* the power-up,
so bridge GPIO0 to GND **before** plugging the CP2102 in. You can leave the jumper on for
the whole esptool session (reads/writes work fine with GPIO0 still grounded); you only
**must remove it after flashing**, before the final power-cycle, so it boots the new
firmware instead of back into the bootloader.

Because CP2102 boards usually do not wire DTR/RTS, esptool cannot reset the chip itself,
hence `--before no-reset` and the manual power-cycle. If a command says `No serial data
received`, the chip booted to firmware before esptool connected: re-plug with GPIO0 held
and run it again.

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
