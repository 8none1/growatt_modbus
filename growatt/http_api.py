"""In-process HTTP endpoint for inverter control and health.

Replaces the old standalone lighttpd + CGI control container: the poller now runs
this small stdlib server in a daemon thread, so a single process owns the dongle and
one lock (see growatt._modbus_lock) serialises every Modbus session.

Endpoints (port from config["http"]["port"], default 8085):

  GET  /health  -> 200 {"status":"ok",...} | 503 {"status":"stale",...}
                   In-memory only: reflects how long since the control inverter was
                   last read successfully by the poll loop. Never touches Modbus.
  GET  /slots   -> 200 {"status":"success","slots":{...}}   (reads under the lock)
  POST /mode    -> {"action": "...", "duration": N, "slot_num": N}
                   Action strings are unchanged from the old CGI so Home Assistant
                   payloads only needed their URL repointed.
"""

import json
import time
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import control_target
from .control import with_control_session

log = logging.getLogger("growatt")

# POST /mode actions, mapped to the InverterControl call. set_time() is run before
# each (matching the old CGI) inside the locked session.
_WRITE_ACTIONS = {
    "switch_inverter_to_batt_first_mode",
    "switch_inverter_to_grid_first_mode",
    "switch_inverter_to_load_first_mode",
    "disable_batt_first_slot",
    "clear_all_slots",
}


def _apply_mode(inv, body):
    """Run a control write on an open InverterControl. Returns the response dict."""
    inv.set_time()  # keep the clock aligned to UTC before scheduling
    action = body.get("action")
    if action == "switch_inverter_to_batt_first_mode":
        duration = body.get("duration", 30)
        slot_num = body.get("slot_num", 6)
        inv.battery_first(duration, slot_num)
        return {"status": "success", "duration": duration}
    if action == "switch_inverter_to_grid_first_mode":
        duration = body.get("duration")
        inv.grid_first(duration)
        return {"status": "success", "duration": duration}
    if action == "switch_inverter_to_load_first_mode":
        inv.load_first()
        return {"status": "success"}
    if action == "disable_batt_first_slot":
        inv.disable_batt_first_slot(body.get("slot_num"))
        return {"status": "success"}
    if action == "clear_all_slots":
        inv.clear_all_slots()
        return {"status": "success"}
    return {"status": "unknown action"}  # unreachable; validated before the session


class _Handler(BaseHTTPRequestHandler):
    server_version = "growatt/1.0"

    # Route stdlib access logging through our logger instead of stderr.
    def log_message(self, fmt, *args):
        log.debug("http %s - %s", self.address_string(), fmt % args)

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _health(self):
        config = self.server.gw_config
        stats = self.server.gw_stats
        try:
            host = control_target(config)[0]
        except Exception:
            host = None
        st = stats.get(host) if host else None
        last = st.get("lastGoodReadMonotonic") if st else None
        threshold = (config.get("health") or {}).get("stale_after_seconds", 600)
        if last is None:
            return self._send(503, {"status": "stale", "age_seconds": None})
        age = round(time.monotonic() - last)
        payload = {"serial": st.get("serial"), "age_seconds": age}
        if age <= threshold:
            return self._send(200, {"status": "ok", **payload})
        return self._send(503, {"status": "stale", **payload})

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            return self._health()
        if path == "/slots":
            try:
                slots = with_control_session(self.server.gw_config,
                                             lambda inv: inv.get_all_slots())
                return self._send(200, {"status": "success", "slots": slots})
            except Exception as e:
                log.warning("GET /slots failed: %s", e)
                return self._send(500, {"status": "error", "message": str(e)})
        return self._send(404, {"status": "error", "message": "not found: %s" % path})

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/mode":
            return self._send(404, {"status": "error", "message": "not found: %s" % path})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, TypeError):
            return self._send(400, {"status": "error", "message": "invalid JSON body"})
        action = body.get("action")
        if action not in _WRITE_ACTIONS:
            return self._send(400, {"status": "error",
                                    "message": "unknown action: %s" % action})
        try:
            result = with_control_session(self.server.gw_config,
                                          lambda inv: _apply_mode(inv, body))
            return self._send(200, result)
        except Exception as e:
            log.warning("POST /mode (%s) failed: %s", action, e)
            return self._send(500, {"status": "error", "message": str(e)})


def make_http_server(config, mqtt_client, stats):
    """Build the control/health HTTP server. Caller runs serve_forever() in a thread."""
    port = (config.get("http") or {}).get("port", 8085)
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    # Hand the handler what it needs (read-only access to live poller state).
    server.gw_config = config
    server.gw_mqtt = mqtt_client
    server.gw_stats = stats
    server.daemon_threads = True
    log.info("Control/health HTTP server listening on :%d", port)
    return server
