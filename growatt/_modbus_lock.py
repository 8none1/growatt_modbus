"""The single process-wide Modbus serialiser.

The poller and the HTTP control endpoint now live in one process and both talk to
the same inverter through a dumb RTU-over-TCP WiFi dongle, which bridges every TCP
connection onto one serial line with no request/response correlation. Two concurrent
sessions therefore garble each other's frames. Acquiring this lock around a *whole*
Modbus session (connect -> reads/writes -> close) guarantees the dongle only ever
sees one connection at a time.

It lives in its own tiny module so both growatt_modbus.py (poll loop) and
growatt/control.py (control ops) can import it without a circular dependency.
"""

import threading

MODBUS_LOCK = threading.Lock()
