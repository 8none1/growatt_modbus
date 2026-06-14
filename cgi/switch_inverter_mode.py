#!/usr/bin/env python3
"""CGI endpoint to read and control the Growatt SPH inverter.

Thin wrapper over growatt.control. Preserves the original request/response
contract so the Home Assistant rest_commands keep working unchanged:

  GET  ?action=get_all_slots            -> {"status":"success","slots":{...}}
  POST {"action":"switch_inverter_to_batt_first_mode", "duration":N, "slot_num":N}
  POST {"action":"switch_inverter_to_grid_first_mode", "duration":N}
  POST {"action":"switch_inverter_to_load_first_mode"}
  POST {"action":"disable_batt_first_slot", "slot_num":N}
  POST {"action":"clear_all_slots"}

The inverter host/unit come from config.yaml (see config.yaml.example); the
AC-charge tunables stay hard-coded in growatt/control.py.
"""

import os
import sys
import json
import logging

# Make the growatt package importable whether run from the repo or the container.
for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)

from growatt.config import load_config, control_target
from growatt.control import InverterControl


def main():
    method = os.environ.get("REQUEST_METHOD", "GET")
    query_string = os.environ.get("QUERY_STRING", "")
    response = {}

    config = load_config(require_devices=True)
    host, port, device_id = control_target(config)
    inv = InverterControl(host, port, device_id)
    try:
        if method == "GET":
            params = dict(p.split("=", 1) for p in query_string.split("&") if "=" in p)
            action = params.get("action", "get_all_slots")
            if action == "get_all_slots":
                response["status"] = "success"
                response["slots"] = inv.get_all_slots()
            else:
                response["status"] = "error"
                response["message"] = "Unknown GET action: %s" % action
            print("Content-Type: application/json\r\n\r\n")
            print(json.dumps(response, indent=2))
            return

        if method == "POST":
            data = sys.stdin.read()
            json_data = json.loads(data)
            print("Content-Type: application/json\r\n\r\n")

            inv.set_time()  # keep the clock aligned to UTC before scheduling
            action = json_data.get("action")
            if action == "switch_inverter_to_batt_first_mode":
                duration = json_data.get("duration", 30)
                slot_num = json_data.get("slot_num", 6)
                inv.battery_first(duration, slot_num)
                response["status"] = "success"
                response["duration"] = duration
            elif action == "switch_inverter_to_load_first_mode":
                inv.load_first()
                response["status"] = "success"
            elif action == "switch_inverter_to_grid_first_mode":
                duration = json_data.get("duration")
                inv.grid_first(duration)
                response["status"] = "success"
                response["duration"] = duration
            elif action == "disable_batt_first_slot":
                inv.disable_batt_first_slot(json_data.get("slot_num"))
                response["status"] = "success"
            elif action == "clear_all_slots":
                inv.clear_all_slots()
                response["status"] = "success"
            else:
                response["status"] = "unknown action"
            print(json.dumps(response))
    finally:
        inv.close()


if __name__ == "__main__":
    main()
