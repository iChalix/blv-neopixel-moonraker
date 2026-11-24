"""Verhalten der Moonraker-Sitzung - ohne Netzwerk und ohne ``websockets``."""

import asyncio
import json
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blv_neopixel import moonraker
from blv_neopixel.config import MoonrakerConfig

#: Grosszuegig fuer langsame Rechner, aber deutlich unter ``connect_timeout``
SOON = 1.0


class FakeMapper:
    def __init__(self):
        self.online = False
        self.klippy_state = ""
        self.objects = {}

    def subscription(self):
        return {"extruder": None}

    def set_online(self, online):
        self.online = online

    def set_klippy_state(self, state):
        self.klippy_state = state

    def reset_objects(self):
        self.objects = {}

    def update_objects(self, objects):
        for name, values in objects.items():
            self.objects.setdefault(name, {}).update(values)


class FakeSocket:
    """Beantwortet Aufrufe wie Moonraker: ueber dieselbe Leitung.

    ``answers`` enthaelt die Antworten auf ``printer.objects.subscribe`` der
    Reihe nach; ``None`` bleibt unbeantwortet, eine Ausnahme wird als
    Fehlerantwort geschickt.
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.subscriptions = 0
        self.closed = False
        self._queue = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    def __aiter__(self):
        return self

    async def __anext__(self):
        raw = await self._queue.get()
        if raw is None:
            raise StopAsyncIteration
        return raw

    async def close(self):
        if not self.closed:
            self.closed = True
            self._queue.put_nowait(None)

    async def send(self, raw):
        message = json.loads(raw)
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        if message["method"] == "server.info":
            reply["result"] = {"klippy_state": "ready"}
        else:
            self.subscriptions += 1
            answer = self.answers.pop(0)
            if answer is None:
                return
            if isinstance(answer, Exception):
                reply["error"] = {"code": 503, "message": str(answer)}
            else:
                reply["result"] = {"eventtime": 1.0, "status": answer}
        self._queue.put_nowait(json.dumps(reply))

    def notify(self, method, *params):
        self._queue.put_nowait(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": list(params)})
        )


async def until(condition, timeout=SOON):
    """Warten, bis ``condition`` gilt. Liefert ``False`` nach Ablauf der Zeit."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not condition():
        if loop.time() > end:
            return False
        await asyncio.sleep(0.005)
    return True


class SessionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        patcher = mock.patch.object(moonraker, "websockets", mock.Mock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, *answers):
        self.socket = FakeSocket(answers)
        self.mapper = FakeMapper()
        self.client = moonraker.MoonrakerClient(
            MoonrakerConfig(connect_timeout=30.0), self.mapper
        )
        self.client._connect = lambda: self.socket
        self.session = asyncio.ensure_future(self.client._session())
        return self.session

    async def asyncTearDown(self):
        self.session.cancel()
        await asyncio.gather(self.session, return_exceptions=True)
        # nach dem Ende der Sitzung darf keine Aufgabe uebrig bleiben
        left = asyncio.all_tasks() - {asyncio.current_task()}
        self.assertEqual([t for t in left if "moonraker" in repr(t)], [])

    def temperature(self):
        return self.mapper.objects.get("extruder", {}).get("temperature")

    async def test_klipper_restart_renews_subscription(self):
        self.start({"extruder": {"temperature": 20.0}}, {"extruder": {"temperature": 30.0}})
        self.assertTrue(await until(lambda: self.temperature() == 20.0))

        self.socket.notify("notify_klippy_disconnected")
        self.assertTrue(await until(lambda: self.mapper.klippy_state == "disconnected"))
        self.socket.notify("notify_klippy_ready")
        self.assertTrue(
            await until(lambda: self.temperature() == 30.0),
            "Antwort auf das neue Abonnement kam nicht an",
        )
        self.assertEqual(self.mapper.klippy_state, "ready")

        self.socket.notify("notify_status_update", {"extruder": {"temperature": 31.0}}, 1.0)
        self.assertTrue(await until(lambda: self.temperature() == 31.0))
        self.assertFalse(self.session.done())
        self.assertFalse(self.socket.closed)

    async def test_reads_on_while_subscription_is_pending(self):
        self.start({}, None)
        await until(lambda: self.socket.subscriptions == 1)
        self.socket.notify("notify_klippy_ready")
        self.assertTrue(await until(lambda: self.socket.subscriptions == 2))

        self.socket.notify("notify_klippy_shutdown")
        self.assertTrue(await until(lambda: self.mapper.klippy_state == "shutdown"))

    async def test_session_end_cancels_pending_subscription(self):
        self.start({}, None)
        await until(lambda: self.socket.subscriptions == 1)
        self.socket.notify("notify_klippy_ready")
        self.assertTrue(await until(lambda: self.socket.subscriptions == 2))

        await self.socket.close()
        await asyncio.wait([self.session], timeout=SOON)
        self.assertTrue(self.session.done())
        self.assertEqual(self.client._pending, {})

    async def test_failed_resubscription_ends_session(self):
        # ohne Abonnement kaemen keine Daten mehr - run() verbindet dann neu
        self.start({}, RuntimeError("Klippy Host not connected"))
        await until(lambda: self.socket.subscriptions == 1)
        self.socket.notify("notify_klippy_ready")
        await asyncio.wait([self.session], timeout=SOON)
        self.assertTrue(self.session.done())


if __name__ == "__main__":
    unittest.main()
