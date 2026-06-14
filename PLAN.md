# Plan: consolidate monitoring + control, publish the control script

## Goal
Bring the two halves of the solar setup into this one repo behind a shared library:
- **Monitor** = `growatt_modbus.py` (polls the inverters, publishes MQTT + HA discovery). Already here.
- **Control** = the `switch_inverter_mode.py` CGI currently living only on perceptron
  (`~/docker/lighttpd/www/cgi-bin/`), unversioned and edited in place. It switches the
  battery inverter between Battery/Grid/Load First and manages the AC-charge time slots, and
  is driven by Home Assistant `rest_command`s and the Octopus Agile scheduler.

Outcomes: the control logic gets version control + history; the duplicated Modbus/register
knowledge lives in one place; and the control script becomes publishable.

## Decisions (agreed with Will)
- **Externalise only the inverter host** (the one private value). The AC-charge tunables
  (charge rate 100, stop-SOC 100, AC-charge enable, Grid-First 100/25) **stay hard-coded** as
  they are now, do not touch them.
- **Two images, unified code.** The poller and the CGI have different runtimes (a long-running
  daemon vs an on-demand HTTP endpoint), so they stay two images, but share one repo, one
  library and one config file. The control image is **derived from** `ghcr.io/8none1/lighttpd-chainguard`
  (Will's hardened, signed base) and bakes in the CGI + shared lib. This keeps the CGI's
  behaviour and the HA URL unchanged (lowest risk). A future "one image, embedded HTTP, drop
  lighttpd" step is possible but out of scope here.
- `lighttpd-chainguard` stays generic; only change there is the weekly scheduled rebuild
  (done, its PR #1) and adding `PyYAML` to its requirements.

## Target layout
```
growatt_modbus/
  growatt/                     shared library
    client.py                  connect / read / write / read_double_reg / time-sync
    registers.py               input+holding maps, the charge-slot map (with the 1018 off-by-one), SENSOR_META
    monitor.py                 decode input/holding registers -> dict
    control.py                 battery/grid/load-first, disable/clear slots, get_all_slots
    config.py                  load config.yaml (host etc.)
  growatt_modbus.py            poller entrypoint (refactored to import growatt/)
  cgi/switch_inverter_mode.py  thin CGI over growatt.control (config-driven, publishable)
  config.yaml.example          host + MQTT (+ which device is the battery/control one)
  Dockerfile                   poller image (existing)
  Dockerfile.control           FROM lighttpd-chainguard; COPY cgi/ + growatt/
  .github/workflows/           build BOTH images (poller + control)
```
Both containers on perceptron mount the same private `config.yaml`.

## Phases (each a separate PR = the rollback unit)
0. **lighttpd-chainguard weekly rebuild** + add PyYAML. (rebuild PR already open.)
1. **Extract the shared library** from the existing poller. Pure refactor: `growatt_modbus.py`
   behaviour must be **byte-identical**. Verify by diffing the MQTT payloads before/after.
2. **Port control into `growatt/control.py`** verbatim from the live CGI (same registers, same
   1018 off-by-one, same "don't shorten an existing slot" logic), with the host from config.
   Add the thin `cgi/switch_inverter_mode.py` wrapper.
3. **Control image + CI**: add `Dockerfile.control` and a build/sign job; publish
   `ghcr.io/8none1/growatt_modbus-control` (name TBC).
4. **Cutover on perceptron** (only after verification): point the lighttpd deploy at the baked
   image, mount the shared config. Confirm all five HA `rest_command`s produce identical
   register writes. Then make the source public (it is already config-free by this point).

## Rollback / backup strategy (must always be reversible)
- **All code changes are branches + PRs** -> revert via git at any time.
- **Images are immutable + tagged**: CI tags every build with a short SHA, so any previous
  poller/control image can be re-pulled. Before cutover, note the currently-running image
  digests.
- **Before any perceptron change, back up** (timestamped copies kept on perceptron):
  - `~/docker/lighttpd/www/cgi-bin/switch_inverter_mode.py` (the live control script)
  - `~/docker/lighttpd/docker-compose.yml` and `~/docker/growatt_modbus/docker-compose.yaml`
  - the relevant HA `configuration.yaml` `rest_command` block
- **Keep the old lighttpd CGI container/image in place** during cutover; rollback = repoint the
  compose `image:` back to the old reference (and HA URL is unchanged anyway) and
  `docker compose up -d`.
- **Verify-before-cutover gate**: the new control must return identical `get_all_slots` JSON and
  issue identical Modbus writes to the live CGI; checked against the real inverter (read-only
  comparison where possible) before switching traffic to it.

## Out of scope (noted, not now)
- Changing the AC-charge tunables.
- One-image / embedded-HTTP control (dropping lighttpd).
- Decoding fault/warning bitfields; the other `REGISTERS.md` naming/scaling fixes.
