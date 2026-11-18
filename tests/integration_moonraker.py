"""Integrationstest mit echtem Websocket-Server und echter serieller Schnittstelle.

Startet einen Fake-Moonraker, laesst die Middleware darauf laufen und liest am
anderen Ende eines pty mit, was der Arduino bekommen haette. Bei Sketch V4.x
spielt der Test zusaetzlich den fragenden Arduino und schickt
``M409 F"d99f"`` in die Leitung.

Braucht ``websockets`` und ``pyserial``, laeuft aber ohne Drucker und ohne
Arduino:

    ~/blv-neopixel-env/bin/python tests/integration_moonraker.py          # beide Versionen
    ~/blv-neopixel-env/bin/python tests/integration_moonraker.py 4.1      # nur V4.x

Wird von ``python3 -m unittest discover`` absichtlich nicht gefunden (der
Dateiname beginnt nicht mit ``test_``), damit die Unit-Tests ohne
Fremdpakete laufen.
"""

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets

from blv_neopixel import config as config_module, protocol as protocol_module
from blv_neopixel.service import Service

#: Ausgangszustand, den ``printer.objects.subscribe`` zurueckgibt
INITIAL = {
    "print_stats": {"state": "standby"},
    "display_status": {"progress": 0.0},
    "idle_timeout": {"state": "Idle"},
    "pause_resume": {"is_paused": False},
    "virtual_sdcard": {"progress": 0.0},
    "heater_bed": {"temperature": 22.4, "target": 0.0},
    "extruder": {"temperature": 24.1, "target": 0.0},
}

#: (Wartezeit, Objektaktualisierung) - wie ``notify_status_update``
SCRIPT = [
    (0.8, {"heater_bed": {"temperature": 40.2, "target": 60.0}, "idle_timeout": {"state": "Printing"}}),
    (0.8, {"heater_bed": {"temperature": 59.9}, "extruder": {"temperature": 180.0, "target": 235.0}}),
    (0.8, {"print_stats": {"state": "printing"}, "display_status": {"progress": 0.0}}),
    (0.8, {"display_status": {"progress": 0.5}, "extruder": {"temperature": 234.8}}),
    (0.8, {"display_status": {"progress": 1.0}}),
    (0.8, {"print_stats": {"state": "paused"}, "pause_resume": {"is_paused": True}}),
    (0.8, {"print_stats": {"state": "complete"}, "pause_resume": {"is_paused": False}}),
]


class Fake:
    """Sammelt, was der Fake-Moonraker abonniert und verschickt."""

    def __init__(self):
        self.subscribed = {}
        self.notifications = 0


async def drive(connection, fake):
    for delay, update in SCRIPT:
        await asyncio.sleep(delay)
        await connection.send(
            json.dumps({"jsonrpc": "2.0", "method": "notify_status_update", "params": [update, 1234.5]})
        )
        fake.notifications += 1
    await asyncio.sleep(0.8)
    await connection.send(
        json.dumps({"jsonrpc": "2.0", "method": "notify_klippy_shutdown", "params": []})
    )
    fake.notifications += 1


def make_handler(fake):
    async def handler(connection):
        driver = None
        try:
            async for raw in connection:
                message = json.loads(raw)
                method = message.get("method")
                if method == "server.info":
                    result = {"klippy_state": "ready", "klippy_connected": True}
                elif method == "printer.objects.subscribe":
                    fake.subscribed.update(message["params"]["objects"])
                    result = {"eventtime": 1.0, "status": INITIAL}
                    driver = asyncio.create_task(drive(connection, fake))
                else:
                    result = {}
                await connection.send(
                    json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result})
                )
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if driver is not None:
                driver.cancel()

    return handler


def write_config(port, url, version):
    text = (
        "[moonraker]\nurl = %s\n\n"
        "[serial]\nport = %s\navoid_reset = false\nreset_delay = 0.1\n\n"
        "[printer]\nsketch_version = %s\ninterval_active = 1.0\ninterval_idle = 2.0\n"
        "min_interval = 0.35\ncomplete_hold = 30\n\n"
        "[heater 0]\nobject = heater_bed\nname = Heizbett\n\n"
        "[heater 1]\nobject = extruder\nname = Hotend\n\n"
        "[logging]\nlevel = warning\n" % (url, port, version)
    )
    handle = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


async def fake_arduino_requests(master_fd, command, interval=1.0):
    """Den fragenden Arduino von V4.x spielen."""
    while True:
        await asyncio.sleep(interval)
        os.write(master_fd, (command + "\r\n").encode("ascii"))


async def collect(version):
    fake = Fake()
    master_fd, slave_fd = os.openpty()
    slave_path = os.ttyname(slave_fd)
    os.set_blocking(master_fd, False)

    received = bytearray()
    loop = asyncio.get_running_loop()
    loop.add_reader(master_fd, lambda: received.extend(os.read(master_fd, 4096) or b""))

    async with websockets.serve(make_handler(fake), "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        config_path = write_config(slave_path, "ws://127.0.0.1:%d/websocket" % port, version)
        config = config_module.load(config_path)
        service = Service(config)

        tasks = [asyncio.ensure_future(service.run())]
        if config.protocol.polls:
            tasks.append(
                asyncio.ensure_future(
                    fake_arduino_requests(master_fd, config.protocol.request_command)
                )
            )
        await asyncio.sleep(sum(delay for delay, _ in SCRIPT) + 2.5)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    loop.remove_reader(master_fd)
    os.close(master_fd)
    os.close(slave_fd)
    os.unlink(config_path)
    return bytes(received), fake, service


def analyse(version, raw, fake, service):
    protocol = service.config.protocol
    failures = []

    def check(name, condition, detail=""):
        print("  [%s] %s%s" % ("ok" if condition else "FEHLER", name, (" - " + detail) if detail else ""))
        if not condition:
            failures.append("%s: %s" % (version, name))

    lines = [line for line in raw.decode("ascii", "replace").split("\n") if line.strip().startswith("{")]
    states = []
    printer = None
    for line in lines:
        printer = protocol.parse(line + "\n", printer)
        states.append(
            (
                printer.status,
                round(printer.heater_act_temp[0], 1),
                round(printer.setpoint(0), 1),
                round(printer.heater_act_temp[1], 1),
                printer.heater_status[1],
                round(printer.fraction_printed, 3),
            )
        )

    print("\n=== Sketch %s - %s" % (protocol.version, protocol.label))
    print("Rahmen: %d | Abfragen beantwortet: %d | Benachrichtigungen: %d"
          % (len(lines), service.sender.requests, fake.notifications))
    print("Erster : %s" % (lines[0] if lines else "<keiner>"))
    print("Letzter: %s" % (lines[-1] if lines else "<keiner>"))

    check("Rahmen wurden geschrieben", len(lines) >= 8, "%d Rahmen" % len(lines))
    check("jeder Rahmen ist gueltig", all(_valid(protocol, line) for line in lines))
    check(
        "benoetigte Objekte abonniert",
        {"heater_bed", "extruder", "print_stats", "display_status", "pause_resume"}
        <= set(fake.subscribed),
    )

    letters = [state[0] for state in states]
    print("Statusverlauf: %s" % " ".join(letters))
    check("Startzustand 'configuring', nicht Fehlerfarbe", letters[0] == "C", "status=%s" % letters[0])
    check("danach Leerlauf", "I" in letters[:3], " ".join(letters[:3]))
    check("manuelles Heizen wird 'busy'", "D" in letters)
    check("Druck wird 'printing'", "P" in letters)
    check("Pause wird 'paused'", "A" in letters)
    check("Klipper-Shutdown wird 'stopped'", letters[-1] == "S", "status=%s" % letters[-1])

    check("Bett-Temperaturen kommen an", {22.4, 40.2, 59.9} <= {s[1] for s in states})
    check("Bett-Sollwert kommt an", 60.0 in {s[2] for s in states})
    check("Hotend-Temperaturen kommen an", {24.1, 180.0, 234.8} <= {s[3] for s in states})
    check("Hotend wird aktiv gemeldet", 2 in {s[4] for s in states})

    printing = [s[5] for s in states if s[0] == "P"]
    check("Fortschritt 50 %", any(abs(value - 0.5) < 0.01 for value in printing), str(printing))
    if protocol.supports_full_ring:
        check("Fortschritt 100 % mit Ueberschwinger", any(value > 1.0 for value in printing), str(printing))
    else:
        check(
            "Fortschritt geht bis knapp unter 100 %",
            any(0.99 <= value < 1.0 for value in printing),
            str(printing),
        )

    if protocol.polls:
        check(
            "Statusabfragen des Arduino wurden beantwortet",
            service.sender.requests >= 3,
            "%d Abfragen" % service.sender.requests,
        )
    else:
        check("keine Abfragen erwartet", service.sender.requests == 0)

    return failures


def _valid(protocol, line):
    try:
        protocol.validate_frame(line + "\n")
        return True
    except protocol.frame_error:
        return False


def main(argv):
    versions = [protocol_module.normalize(arg) for arg in argv] or protocol_module.versions()
    failures = []
    for version in versions:
        raw, fake, service = asyncio.run(collect(version))
        failures.extend(analyse(version, raw, fake, service))

    print()
    if failures:
        print("%d Pruefung(en) fehlgeschlagen:" % len(failures))
        for name in failures:
            print("  - %s" % name)
        return 1
    print("Integrationstest bestanden (%s)" % ", ".join(versions))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
