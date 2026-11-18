"""Simulierter Druckerzustand - zum Pruefen der Ringe ohne laufenden Druck."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Config
from .state import Heater, HeaterState, PrinterState, PrinterStatus


@dataclass
class Phase:
    name: str
    duration: float
    status: PrinterStatus


#: Ablauf, der jeden Anzeigefall des Sketches einmal zeigt
PHASES = (
    Phase("Klipper startet", 3.0, PrinterStatus.CONFIGURING),
    Phase("Leerlauf", 4.0, PrinterStatus.IDLE),
    Phase("Aufheizen", 12.0, PrinterStatus.BUSY),
    Phase("Druck laeuft", 25.0, PrinterStatus.PRINTING),
    Phase("Druck fertig", 5.0, PrinterStatus.PRINTING),
    Phase("Pausiert", 4.0, PrinterStatus.PAUSED),
    Phase("Abgebrochen", 4.0, PrinterStatus.STOPPED),
    Phase("Abkuehlen", 10.0, PrinterStatus.IDLE),
)

#: Zieltemperaturen der Simulation: Index -> (Raumtemperatur, Solltemperatur)
DEFAULT_TEMPS = {
    0: (22.0, 60.0),
    1: (24.0, 235.0),
    2: (22.0, 80.0),
    3: (22.0, 80.0),
    4: (22.0, 80.0),
}


def _lerp(start: float, end: float, fraction: float) -> float:
    return start + (end - start) * max(0.0, min(1.0, fraction))


class Simulator:
    """Liefert :class:`PrinterState` wie der Mapper, nur erfunden."""

    def __init__(self, config: Config, speed: float = 1.0, loop: bool = True) -> None:
        self._config = config
        self._speed = max(0.05, float(speed))
        self._loop = loop
        self._start = time.monotonic()
        self.phase_name = PHASES[0].name
        self.finished = False

    @property
    def total(self) -> float:
        return sum(phase.duration for phase in PHASES)

    def elapsed(self) -> float:
        return (time.monotonic() - self._start) * self._speed

    def _locate(self, elapsed: float):
        total = self.total
        if elapsed >= total:
            if not self._loop:
                self.finished = True
                return PHASES[-1], 1.0
            elapsed = elapsed % total
        for phase in PHASES:
            if elapsed < phase.duration:
                return phase, elapsed / phase.duration
            elapsed -= phase.duration
        return PHASES[-1], 1.0

    def build_state(self) -> PrinterState:
        phase, fraction = self._locate(self.elapsed())
        self.phase_name = phase.name

        progress = 0.0
        heaters = {}
        for mapping in self._config.heaters:
            room, target = DEFAULT_TEMPS.get(mapping.index, (22.0, 100.0))
            current = room
            setpoint = 0.0
            state = HeaterState.OFF

            if phase.name == "Aufheizen":
                # Bett zuerst, Hotend zieht etwas verzoegert nach
                delay = 0.0 if mapping.index == 0 else 0.25
                ramp = max(0.0, (fraction - delay) / (1.0 - delay or 1.0))
                current = _lerp(room, target, ramp)
                setpoint = target
                state = HeaterState.ACTIVE
            elif phase.status in (PrinterStatus.PRINTING, PrinterStatus.PAUSED):
                current = target - 1.0 + (2.0 * fraction)
                setpoint = target
                state = HeaterState.ACTIVE
            elif phase.name == "Abgebrochen":
                current = _lerp(target, target * 0.8, fraction)
            elif phase.name == "Abkuehlen":
                current = _lerp(target * 0.8, room, fraction)

            heaters[mapping.index] = Heater(
                index=mapping.index,
                current=round(current, 1),
                active=setpoint,
                standby=0.0,
                state=state,
                name=mapping.label,
            )

        if phase.name == "Druck laeuft":
            progress = fraction
        elif phase.name == "Druck fertig":
            progress = 1.0

        return PrinterState(
            status=phase.status,
            heaters=heaters,
            progress=progress,
            note="Simulation: %s" % phase.name,
        )
