"""Moonraker-Anbindung ueber die JSON-RPC-Websocket-API."""

from __future__ import annotations

import asyncio
import json
import logging

from .config import MoonrakerConfig

LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover - Abhaengigkeit ist zur Laufzeit erforderlich
    import websockets
except ImportError:  # pragma: no cover
    websockets = None


class MoonrakerUnavailable(RuntimeError):
    """Das Paket ``websockets`` fehlt."""


class MoonrakerClient:
    """Haelt die Websocket-Verbindung und fuettert den Mapper.

    Der Client ist bewusst anspruchslos: Er abonniert die benoetigten
    Klipper-Objekte, mischt Teilaktualisierungen ein und meldet jede
    Aenderung ueber ``on_change`` an den Sender.
    """

    def __init__(self, config: MoonrakerConfig, mapper, on_change=None) -> None:
        if websockets is None:  # pragma: no cover
            raise MoonrakerUnavailable(
                "Python-Paket 'websockets' fehlt - bitte 'pip install websockets'"
            )
        self._config = config
        self._mapper = mapper
        self._on_change = on_change or (lambda: None)
        self._next_id = 0
        self._pending: dict = {}

    # ------------------------------------------------------------------ oeffentlich

    async def run(self) -> None:
        """Endlosschleife mit exponentiellem Backoff."""
        delay = self._config.reconnect_min
        while True:
            try:
                await self._session()
                delay = self._config.reconnect_min
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - jede Stoerung fuehrt zum Retry
                LOGGER.warning("Moonraker-Verbindung fehlgeschlagen: %s", exc)
            finally:
                self._fail_pending()
                self._mapper.set_online(False)
                self._on_change()
            LOGGER.debug("neuer Verbindungsversuch in %.1fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, self._config.reconnect_max)

    # ------------------------------------------------------------------ intern

    def _connect(self):
        headers = {}
        if self._config.api_key:
            headers["X-Api-Key"] = self._config.api_key
        kwargs = {
            "open_timeout": self._config.connect_timeout,
            "ping_interval": 20,
            "ping_timeout": 20,
            "close_timeout": 5,
            "max_size": 16 * 1024 * 1024,
        }
        if not headers:
            return websockets.connect(self._config.url, **kwargs)
        try:  # websockets >= 14
            return websockets.connect(self._config.url, additional_headers=headers, **kwargs)
        except TypeError:  # websockets < 14
            return websockets.connect(self._config.url, extra_headers=headers, **kwargs)

    async def _session(self) -> None:
        LOGGER.info("verbinde mit Moonraker: %s", self._config.url)
        async with self._connect() as socket:
            self._mapper.set_online(True)
            reader = asyncio.create_task(self._read_loop(socket))
            try:
                await self._bootstrap(socket)
                self._on_change()
                await reader
            finally:
                reader.cancel()
                try:
                    await reader
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def _bootstrap(self, socket) -> None:
        info = await self._request(socket, "server.info")
        klippy_state = str(info.get("klippy_state", "")).lower()
        self._mapper.set_klippy_state(klippy_state)
        if klippy_state == "ready":
            await self._subscribe(socket)

    async def _subscribe(self, socket) -> None:
        self._mapper.reset_objects()
        result = await self._request(
            socket,
            "printer.objects.subscribe",
            {"objects": self._mapper.subscription()},
        )
        status = result.get("status", {}) if isinstance(result, dict) else {}
        self._mapper.update_objects(status)
        LOGGER.info("Abonnement aktiv: %s", ", ".join(sorted(self._mapper.subscription())))
        self._on_change()

    async def _read_loop(self, socket) -> None:
        async for raw in socket:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                LOGGER.debug("ungueltige Nachricht verworfen")
                continue
            await self._dispatch(socket, message)

    async def _dispatch(self, socket, message: dict) -> None:
        if not isinstance(message, dict):
            return

        if "id" in message and message.get("method") is None:
            future = self._pending.pop(message["id"], None)
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(RuntimeError(str(message["error"])))
            else:
                future.set_result(message.get("result"))
            return

        method = message.get("method", "")
        params = message.get("params", [])

        if method == "notify_status_update":
            if params and isinstance(params[0], dict):
                self._mapper.update_objects(params[0])
                self._on_change()
        elif method == "notify_klippy_ready":
            self._mapper.set_klippy_state("ready")
            await self._subscribe(socket)
        elif method == "notify_klippy_shutdown":
            self._mapper.set_klippy_state("shutdown")
            self._on_change()
        elif method == "notify_klippy_disconnected":
            self._mapper.set_klippy_state("disconnected")
            self._on_change()

    async def _request(self, socket, method: str, params=None):
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await socket.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(future, timeout=self._config.connect_timeout)
        finally:
            self._pending.pop(request_id, None)

    def _fail_pending(self) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
        self._pending.clear()
