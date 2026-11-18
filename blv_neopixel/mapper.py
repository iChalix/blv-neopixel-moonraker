"""Klipper/Moonraker-Daten in den Sketch-Zustand uebersetzen."""

from __future__ import annotations

import logging
import time

from .config import Config
from .state import Heater, HeaterState, PrinterState, PrinterStatus

LOGGER = logging.getLogger(__name__)

#: Objekte, die bei Moonraker abonniert werden (ausser den Heatern)
BASE_OBJECTS = {
    "print_stats": None,
    "display_status": None,
    "virtual_sdcard": None,
    "idle_timeout": None,
    "pause_resume": None,
}

#: print_stats.state -> Sketch-Status
PRINT_STATE_MAP = {
    "standby": PrinterStatus.IDLE,
    "printing": PrinterStatus.PRINTING,
    "paused": PrinterStatus.PAUSED,
    "complete": PrinterStatus.IDLE,
    "cancelled": PrinterStatus.IDLE,
    "error": PrinterStatus.STOPPED,
}

#: Klippy-Zustaende, in denen keine echten Druckerdaten vorliegen
KLIPPY_READY = "ready"


class KlipperMapper:
    """Haelt den letzten Moonraker-Stand und erzeugt daraus den Sketch-Zustand."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._objects: dict = {}
        self._klippy_state = "disconnected"
        self._klippy_message = ""
        self._online = False
        #: Erst nach der ersten Verbindung gilt "offline" als Stoerung - vorher
        #: ist es der normale Startzustand (sonst blitzen die Ringe kurz in der
        #: Fehlerfarbe auf, bevor Moonraker antwortet).
        self._ever_online = False
        self._print_state = ""
        self._print_state_since = time.monotonic()

    # ------------------------------------------------------------------ Eingang

    def subscription(self) -> dict:
        """Objektliste fuer ``printer.objects.subscribe``."""
        objects = dict(BASE_OBJECTS)
        for heater in self._config.heaters:
            objects[heater.object] = None
        return objects

    def set_online(self, online: bool) -> None:
        if online != self._online:
            LOGGER.info("Moonraker-Verbindung: %s", "aktiv" if online else "getrennt")
        self._online = online
        if online:
            self._ever_online = True
        else:
            self._klippy_state = "disconnected"

    def set_klippy_state(self, state: str, message: str = "") -> None:
        state = (state or "").strip().lower()
        if state != self._klippy_state:
            LOGGER.info("Klipper-Status: %s%s", state, " (%s)" % message if message else "")
        self._klippy_state = state
        self._klippy_message = message

    def update_objects(self, objects: dict) -> None:
        """Teilaktualisierung aus ``notify_status_update`` einmischen."""
        for name, values in (objects or {}).items():
            if not isinstance(values, dict):
                continue
            target = self._objects.setdefault(name, {})
            target.update(values)
            if name == "print_stats" and "state" in values:
                new_state = str(values["state"] or "").lower()
                if new_state != self._print_state:
                    LOGGER.debug("print_stats.state: %s -> %s", self._print_state, new_state)
                    self._print_state = new_state
                    self._print_state_since = time.monotonic()

    def reset_objects(self) -> None:
        self._objects = {}
        self._print_state = ""
        self._print_state_since = time.monotonic()

    # ------------------------------------------------------------------ Ausgang

    def build_state(self) -> PrinterState:
        status, note = self._status()
        state = PrinterState(
            status=status,
            heaters=self._heaters(),
            progress=self._progress(),
            note=note,
        )
        if status is PrinterStatus.PRINTING and self._print_complete_hold():
            state.progress = 1.0
        return state

    # ------------------------------------------------------------------ intern

    def _obj(self, name: str) -> dict:
        value = self._objects.get(name)
        return value if isinstance(value, dict) else {}

    def _status(self) -> tuple:
        printer = self._config.printer
        if not self._online:
            if not self._ever_online:
                return printer.startup_status, "warte auf Moonraker"
            return printer.offline_status, "Moonraker offline"
        if self._klippy_state in ("startup", "starting", ""):
            return printer.startup_status, "Klipper startet"
        if self._klippy_state in ("shutdown", "error"):
            return printer.error_status, self._klippy_message or "Klipper-Shutdown"
        if self._klippy_state == "disconnected":
            return printer.offline_status, "Klipper getrennt"

        print_state = str(self._obj("print_stats").get("state", "") or "").lower()
        status = PRINT_STATE_MAP.get(print_state, PrinterStatus.IDLE)
        note = ""

        if print_state == "complete" and self._print_complete_hold():
            return PrinterStatus.PRINTING, "Druck fertig"
        if print_state == "cancelled" and self._hold_active(printer.cancel_hold):
            return PrinterStatus.STOPPED, "Druck abgebrochen"

        # pause_resume liefert den Zwischenzustand: Klipper setzt is_paused
        # sofort, print_stats.state folgt erst danach. Ohne diese Information
        # wird nichts geraten.
        pause_info = self._obj("pause_resume")
        if "is_paused" in pause_info:
            is_paused = bool(pause_info["is_paused"])
            if print_state == "printing" and is_paused:
                return PrinterStatus.PAUSING, "pausiert gerade"
            if print_state == "paused" and not is_paused:
                return PrinterStatus.RESUMING, "setzt fort"

        if (
            printer.busy_from_idle_timeout
            and status is PrinterStatus.IDLE
            and str(self._obj("idle_timeout").get("state", "")).lower() == "printing"
        ):
            return PrinterStatus.BUSY, "Makro/manuelle Aktion"

        return status, note

    def _hold_active(self, hold: float) -> bool:
        return hold > 0 and (time.monotonic() - self._print_state_since) < hold

    def _print_complete_hold(self) -> bool:
        return (
            str(self._obj("print_stats").get("state", "")).lower() == "complete"
            and self._hold_active(self._config.printer.complete_hold)
        )

    def _heaters(self) -> dict:
        heaters = {}
        for mapping in self._config.heaters:
            values = self._obj(mapping.object)
            current = _as_float(values.get("temperature"))
            has_target = "target" in values
            target = _as_float(values.get("target"))
            state = HeaterState.OFF
            if has_target and target > 0.0:
                state = HeaterState.ACTIVE
            heaters[mapping.index] = Heater(
                index=mapping.index,
                current=current,
                active=target if state is HeaterState.ACTIVE else 0.0,
                standby=0.0,
                state=state,
                name=mapping.label,
            )
        return heaters

    def _progress(self) -> float:
        source = self._config.printer.progress_source
        if source == "sdcard":
            return _clamp01(_as_float(self._obj("virtual_sdcard").get("progress")))
        if source == "layer":
            info = self._obj("print_stats").get("info") or {}
            if isinstance(info, dict):
                total = _as_float(info.get("total_layer"))
                current = _as_float(info.get("current_layer"))
                if total > 0:
                    return _clamp01(current / total)
            # Fallback, solange der Slicer keine Layer-Infos setzt
            return _clamp01(_as_float(self._obj("display_status").get("progress")))
        return _clamp01(_as_float(self._obj("display_status").get("progress")))

    # ------------------------------------------------------------------ Infos

    @property
    def klippy_state(self) -> str:
        return self._klippy_state

    @property
    def online(self) -> bool:
        return self._online


def _as_float(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return number


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
