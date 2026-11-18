"""Internes Zustandsmodell der Middleware.

Zielplattform ist **NeoPixelBLVmgn V3.0** (Release V3.0, einzelne
``NeoPixel_BLV.ino``). Diese Version ist ein reiner Zuhoerer am PanelDue-Port
und erwartet das flache M408-JSON der RepRapFirmware 2.x:

    {"status":"I","heaters":[31.5,28.1],"active":[0.0,0.0],
     "standby":[0.0,0.0],"hstat":[0,0],"fraction_printed":0.0}

Dieses Modul haelt die Zwischendarstellung, die aus Klipper/Moonraker-Daten
gefuellt (:mod:`blv_neopixel.mapper`) und von :mod:`blv_neopixel.duet` in das
Duet-JSON serialisiert wird.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

#: Anzahl Heater, die der Sketch kennt (``#define NumberHeaters 5``)
MAX_HEATERS = 5

#: Printer-Object 99 = Status + Druckfortschritt
PRINTER_OBJECT_STATUS = 99

#: Der V3.0-Parser (``JsonParseRoot``) braucht fuer indizierten Array-Zugriff
#: mindestens zwei Werte - bei einem einzigen Wert liefert er ``false``.
MIN_ARRAY_ENTRIES = 2


class PrinterStatus(Enum):
    """Statusbuchstaben, die das ``switch (Printer.Status)`` in V3.0 kennt.

    Achtung: Das sind **nicht** die Buchstaben der echten RepRapFirmware 2.x
    (dort ist ``S`` = paused). Der Sketch hat seine eigene Zuordnung, und die
    Middleware ist hier die Firmware-Seite - wir senden also genau die
    Buchstaben, die der Sketch erwartet.
    """

    IDLE = "I"
    PRINTING = "P"
    PAUSED = "A"
    PAUSING = "R"
    RESUMING = "B"
    BUSY = "D"
    STOPPED = "S"
    CONFIGURING = "C"
    FLASHING = "F"

    @property
    def letter(self) -> str:
        return self.value

    @property
    def shows_progress(self) -> bool:
        """Nur bei ``P`` zeichnet Printer-Object 99 den Fortschrittsbalken."""
        return self is PrinterStatus.PRINTING


#: Farbe/Bedeutung laut Config-Block des Sketches - fuer Logs und CLI
STATUS_DESCRIPTION = {
    PrinterStatus.IDLE: "Leerlauf (ColorIdle)",
    PrinterStatus.PRINTING: "Druck laeuft (Fortschrittsbalken)",
    PrinterStatus.PAUSED: "Pausiert (ColorPaused)",
    PrinterStatus.PAUSING: "Pausiert gerade (ColorPausing)",
    PrinterStatus.RESUMING: "Setzt fort (ColorResuming)",
    PrinterStatus.BUSY: "Beschaeftigt (ColorBusy)",
    PrinterStatus.STOPPED: "Gestoppt/Fehler (ColorStopped)",
    PrinterStatus.CONFIGURING: "Startet/Konfiguriert (ColorConfiguring)",
    PrinterStatus.FLASHING: "Firmware-Update (ColorFlashing)",
}


class HeaterState(Enum):
    """``hstat``-Codes: 0 = Off / 1 = Standby / 2 = Active / 3 = Fault / 4 = Tuning."""

    OFF = 0
    STANDBY = 1
    ACTIVE = 2
    FAULT = 3
    TUNING = 4

    @property
    def code(self) -> int:
        return self.value


@dataclass
class Heater:
    """Ein Heater-Slot, den der Sketch ueber die Array-Position adressiert.

    Index 0 ist im Sketch das Heizbett (PrinterObject 0), Index 1 das erste
    Hotend (PrinterObject 1), usw.
    """

    index: int
    current: float = 0.0
    active: float = 0.0
    standby: float = 0.0
    state: HeaterState = HeaterState.OFF
    name: str = ""

    @property
    def setpoint(self) -> float:
        """Soll-Temperatur, die der Sketch fuer die Balkengrafik benutzt."""
        if self.state is HeaterState.ACTIVE:
            return self.active
        if self.state is HeaterState.STANDBY:
            return self.standby
        return 0.0


@dataclass
class PrinterState:
    """Komplettzustand, der zum Arduino geschickt wird."""

    status: PrinterStatus = PrinterStatus.STOPPED
    heaters: dict = field(default_factory=dict)
    progress: float = 0.0
    updated: float = field(default_factory=time.monotonic)
    note: str = ""

    def heater(self, index: int) -> Heater:
        return self.heaters.get(index, Heater(index=index))

    def heater_array(self, min_entries: int = MIN_ARRAY_ENTRIES) -> list:
        """Luecken auffuellen - der Zugriff erfolgt ueber die Array-Position.

        ``min_entries`` ist standardmaessig :data:`MIN_ARRAY_ENTRIES`, weil der
        V3.0-Parser einelementige Arrays nicht indiziert lesen kann. V4.x
        braucht diese Auffuellung nicht und uebergibt ``1``.
        """
        highest = max(self.heaters) if self.heaters else -1
        length = max(highest + 1, min_entries)
        return [self.heater(i) for i in range(length)]

    def summary(self) -> str:
        parts = ["%s=%s" % (self.status.letter, self.status.name.lower())]
        for heater in self.heater_array(min_entries=1):
            label = heater.name or "H%d" % heater.index
            parts.append(
                "%s %.1f/%.1f %s"
                % (label, heater.current, heater.setpoint, heater.state.name.lower())
            )
        if self.status.shows_progress:
            parts.append("%.1f%%" % (self.progress * 100.0))
        if self.note:
            parts.append(self.note)
        return " | ".join(parts)

    def fingerprint(self) -> tuple:
        """Vergleichswert, um unnoetige Sendungen zu vermeiden."""
        return (
            self.status,
            round(self.progress, 3),
            tuple(
                (h.index, round(h.current, 1), round(h.active, 1), round(h.standby, 1), h.state)
                for h in self.heater_array()
            ),
        )
