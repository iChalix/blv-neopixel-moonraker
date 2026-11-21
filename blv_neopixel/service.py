"""Zusammenspiel von Moonraker, Zustandsabbildung und serieller Ausgabe."""

from __future__ import annotations

import asyncio
import logging
import time

from . import describe
from .config import Config
from .mapper import KlipperMapper
from .moonraker import MoonrakerClient
from .serial_link import SerialLink
from .state import PrinterStatus

LOGGER = logging.getLogger(__name__)


class FrameSender:
    """Schickt Rahmen an den Arduino - auf Anfrage, bei Aenderung, als Heartbeat.

    V3.0 fragt nichts ab, sondern hoert nur zu. V4.x fragt mit
    ``M409 F"d99f"`` selbst nach (alle 1 s im Betrieb, alle 5 s im Leerlauf).
    Beides wird bedient:

    * **Anfrage** - sofort antworten, das ist bei V4.x der natuerliche Takt
    * **Zustandsaenderung** - sofort senden, gebremst durch ``min_interval``,
      damit der 64 Byte grosse RX-Puffer des AVR nicht ueberlaeuft
    * **Heartbeat** - regelmaessig, weil der Sketch erst nach der ersten
      vollstaendigen Nachricht etwas anzeigt und ``UpdatePending`` fuer das
      Neuzeichnen braucht (und weil bei V4.x die Rueckleitung fehlen kann)
    """

    def __init__(self, config: Config, provider, link: SerialLink, on_frame=None) -> None:
        self._config = config
        self._protocol = config.protocol
        self._provider = provider
        self._link = link
        self._on_frame = on_frame
        self._wake = asyncio.Event()
        self._last_fingerprint = None
        self._last_sent = 0.0
        self._last_summary = ""
        self._request_pending = False
        self._frames = 0
        self._requests = 0
        self._errors = 0

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def requests(self) -> int:
        return self._requests

    @property
    def protocol(self):
        return self._protocol

    def wake(self) -> None:
        """Aus dem Moonraker-Client aufgerufen, sobald sich etwas aendert."""
        self._wake.set()

    def on_request(self) -> None:
        """Der Arduino hat nach dem Zustand gefragt (nur V4.x)."""
        self._requests += 1
        self._request_pending = True
        self._wake.set()

    def _interval(self, state) -> float:
        printer = self._config.printer
        busy = state.status is not PrinterStatus.IDLE or any(
            heater.state.code != 0 for heater in state.heater_array(min_entries=1)
        )
        return printer.interval_active if busy else printer.interval_idle

    async def run(self) -> None:
        while True:
            now = time.monotonic()
            state = self._provider.build_state()
            fingerprint = state.fingerprint()
            changed = fingerprint != self._last_fingerprint
            asked = self._request_pending
            since = now - self._last_sent

            if changed or asked:
                wait = self._config.printer.min_interval - since
            else:
                wait = self._interval(state) - since

            if wait > 0:
                try:
                    await asyncio.wait_for(self._wake.wait(), wait)
                except asyncio.TimeoutError:
                    pass
                else:
                    self._wake.clear()
                continue

            self._request_pending = False
            await self._send(state, changed, asked)
            self._last_fingerprint = fingerprint
            self._last_sent = time.monotonic()

    async def _send(self, state, changed: bool, asked: bool = False) -> None:
        try:
            frame = self._protocol.build_frame(
                state, full_ring=self._config.printer.full_ring_at_100, validate=True
            )
        except self._protocol.frame_error as exc:
            self._errors += 1
            LOGGER.error("Rahmen verworfen: %s", exc)
            return

        ok = await self._link.write_line(frame)
        if ok:
            self._frames += 1
        summary = state.summary()
        if changed and summary != self._last_summary:
            LOGGER.info("%s", summary)
            self._last_summary = summary
        else:
            LOGGER.debug(
                "%s (%d Byte%s)", summary, len(frame), ", auf Anfrage" if asked else ""
            )
        if self._on_frame is not None:
            self._on_frame(state, frame, ok)


class SerialReader:
    """Liest mit, was der Arduino sendet, und erkennt Statusabfragen."""

    def __init__(self, config: Config, link: SerialLink, sender: FrameSender = None) -> None:
        self._config = config
        self._link = link
        self._sender = sender
        self._protocol = config.protocol
        self._seen_request = False

    async def run(self) -> None:
        while True:
            for line in await self._link.read_lines():
                if self._protocol.is_request(line):
                    if not self._seen_request:
                        LOGGER.info("Arduino fragt den Zustand ab (%s)", line.strip())
                        self._seen_request = True
                    if self._sender is not None:
                        self._sender.on_request()
                    continue
                if self._config.serial.log_incoming:
                    LOGGER.info("Arduino: %s", line)
                else:
                    LOGGER.debug("Arduino: %s", line)
            await asyncio.sleep(0.25)


async def _run_tasks(tasks) -> None:
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class Service:
    """Der Dienst: Moonraker lesen, uebersetzen, an den Arduino senden."""

    def __init__(self, config: Config, dry_run: bool = False, on_frame=None) -> None:
        self.config = config
        self.mapper = KlipperMapper(config)
        self.link = SerialLink(config.serial, dry_run=dry_run)
        self.sender = FrameSender(config, self.mapper, self.link, on_frame=on_frame)
        self.reader = SerialReader(config, self.link, self.sender)
        self.client = MoonrakerClient(config.moonraker, self.mapper, on_change=self.sender.wake)

    async def run(self) -> None:
        LOGGER.info("BLV-NeoPixel-Middleware %s startet", describe())
        LOGGER.info("%s", self.config.describe())
        if self.config.protocol.polls:
            LOGGER.info(
                "Sketch fragt selbst ab (%s) - Antworten plus Heartbeat",
                self.config.protocol.request_command,
            )
        await self.link.ensure_open()
        try:
            await _run_tasks(
                [
                    asyncio.ensure_future(self.client.run()),
                    asyncio.ensure_future(self.sender.run()),
                    asyncio.ensure_future(self.reader.run()),
                ]
            )
        finally:
            await self.link.close()
            LOGGER.info(
                "Middleware beendet (%d Rahmen gesendet, %d Abfragen beantwortet)",
                self.sender.frames,
                self.sender.requests,
            )


async def run_provider(config: Config, provider, dry_run: bool = False, on_frame=None) -> None:
    """Sender mit beliebiger Zustandsquelle laufen lassen (z. B. Simulator)."""
    link = SerialLink(config.serial, dry_run=dry_run)
    sender = FrameSender(config, provider, link, on_frame=on_frame)
    reader = SerialReader(config, link, sender)
    await link.ensure_open()
    try:
        await _run_tasks(
            [asyncio.ensure_future(sender.run()), asyncio.ensure_future(reader.run())]
        )
    finally:
        await link.close()
