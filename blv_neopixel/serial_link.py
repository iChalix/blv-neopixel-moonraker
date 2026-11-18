"""Serielle Verbindung zum Arduino mit automatischem Wiederaufbau."""

from __future__ import annotations

import asyncio
import logging

from .config import SerialConfig

LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover
    import serial as pyserial
except ImportError:  # pragma: no cover
    pyserial = None


class SerialUnavailable(RuntimeError):
    """Das Paket ``pyserial`` fehlt."""


class SerialLink:
    """Duenne Huelle um pyserial, die blockierende Aufrufe auslagert.

    Besonderheit: Beim Oeffnen eines USB-Arduino loest die DTR-Flanke einen
    Reset aus (der Sketch spielt dann seine Startup-Animation). Mit
    ``avoid_reset`` wird DTR vor dem Oeffnen deaktiviert, sodass ein
    Neustart der Middleware die Ringe nicht zuruecksetzt.
    """

    def __init__(self, config: SerialConfig, dry_run: bool = False) -> None:
        self._config = config
        self._dry_run = dry_run
        self._serial = None
        self._buffer = b""
        self._last_error = ""
        self._reopen_at = 0.0

    # ------------------------------------------------------------------ Status

    @property
    def connected(self) -> bool:
        return self._dry_run or (self._serial is not None and self._serial.is_open)

    @property
    def last_error(self) -> str:
        return self._last_error

    # ------------------------------------------------------------------ oeffnen

    def _open_blocking(self):
        port = pyserial.Serial()
        port.port = self._config.port
        port.baudrate = self._config.baudrate
        port.timeout = 0  # nicht blockierend lesen
        port.write_timeout = self._config.write_timeout
        if self._config.avoid_reset:
            # Vor dem Oeffnen gesetzt -> pyserial uebernimmt es direkt beim
            # Oeffnen, die Reset-Flanke am Arduino bleibt damit aus.
            port.dtr = False
            port.rts = False
        port.open()
        return port

    async def ensure_open(self) -> bool:
        if self._dry_run:
            return True
        if self._serial is not None and self._serial.is_open:
            return True
        if pyserial is None:  # pragma: no cover
            raise SerialUnavailable(
                "Python-Paket 'pyserial' fehlt - bitte 'pip install pyserial'"
            )
        if not self._config.port:
            self._note_error("keine serielle Schnittstelle konfiguriert ([serial] port)")
            return False

        loop = asyncio.get_running_loop()
        try:
            self._serial = await loop.run_in_executor(None, self._open_blocking)
        except Exception as exc:  # noqa: BLE001 - pyserial wirft diverse Fehler
            self._serial = None
            self._note_error("%s: %s" % (self._config.port, exc))
            return False

        self._buffer = b""
        self._last_error = ""
        LOGGER.info(
            "serielle Schnittstelle offen: %s @ %d Baud (avoid_reset=%s)",
            self._config.port,
            self._config.baudrate,
            self._config.avoid_reset,
        )
        if self._config.reset_delay > 0:
            await asyncio.sleep(self._config.reset_delay)
        return True

    def _note_error(self, message: str) -> None:
        if message != self._last_error:
            LOGGER.warning("serielle Schnittstelle: %s", message)
        self._last_error = message

    async def close(self) -> None:
        port, self._serial = self._serial, None
        if port is None:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, port.close)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ schreiben

    async def write_line(self, line: str) -> bool:
        """Eine Zeile senden. Gibt ``False`` zurueck, wenn es nicht klappte."""
        if self._dry_run:
            return True
        if not await self.ensure_open():
            return False
        data = line.encode("ascii", "ignore")
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._serial.write, data)
            await loop.run_in_executor(None, self._serial.flush)
        except Exception as exc:  # noqa: BLE001
            self._note_error("Schreibfehler: %s" % exc)
            await self.close()
            return False
        return True

    # ------------------------------------------------------------------ lesen

    async def read_lines(self) -> list:
        """Alles lesen, was der Arduino geschickt hat (Debug-Builds)."""
        if self._dry_run or self._serial is None or not self._serial.is_open:
            return []
        loop = asyncio.get_running_loop()
        try:
            waiting = await loop.run_in_executor(None, lambda: self._serial.in_waiting)
            if not waiting:
                return []
            chunk = await loop.run_in_executor(None, self._serial.read, waiting)
        except Exception as exc:  # noqa: BLE001
            self._note_error("Lesefehler: %s" % exc)
            await self.close()
            return []

        self._buffer += chunk or b""
        if len(self._buffer) > 4096:
            self._buffer = self._buffer[-4096:]
        lines = []
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            text = raw.decode("ascii", "replace").strip()
            if text:
                lines.append(text)
        return lines


def list_ports() -> list:
    """Verfuegbare serielle Schnittstellen (fuer die CLI)."""
    if pyserial is None:  # pragma: no cover
        raise SerialUnavailable("Python-Paket 'pyserial' fehlt")
    from serial.tools import list_ports as tools  # noqa: PLC0415

    return [(p.device, p.description or "", p.hwid or "") for p in tools.comports()]
