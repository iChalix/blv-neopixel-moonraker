"""Serialisierung in das Objektmodell-JSON von NeoPixelBLVmgn **V4.0/V4.1**.

V4.x benutzt einen echten Streaming-Parser (``src/json``) und erwartet den
Dialekt der RepRapFirmware 3 - also Woerter statt Buchstaben beim Status und
Objekte statt Zahlenlisten bei den Heatern:

    {"state":{"status":"processing"},
     "heaters":[{"current":60.1,"active":60.0,"standby":0.0,"state":"active"}],
     "timesLeft":{"slicer":500},"job":{"duration":500,"warmUpDuration":0}}

Die Eigenheiten, die :func:`validate_frame` erzwingt:

1. Genau **ein** JSON-Objekt pro Zeile, abgeschlossen mit ``\\n``
   (``GetSerialMessage()`` beendet die Nachricht beim Zeilenumbruch).
2. Nur **ein** Array (``heaters``), und darin ausschliesslich Objekte. Der
   ``StreamingHandler`` setzt ``inArray`` bei ``endArray`` global zurueck und
   zaehlt den Heater-Index in ``startObject`` - verschachtelte Arrays bringen
   die Zuordnung durcheinander.
3. ``heaters`` wird ueber die Array-Position adressiert, Luecken brauchen
   also Platzhalter.
4. ``timesLeft`` muss **vor** ``job`` stehen: ``job.duration`` und
   ``job.warmUpDuration`` werden nur uebernommen, solange
   ``printRemaining > 0`` ist (StreamingHandler.cpp).
5. Schluessel und Werte duerfen maximal 31 Zeichen haben
   (``BUFFER_MAX_LENGTH 32`` im Parser) - laengere werden abgeschnitten.
6. ``printDuration``, ``printWarmUpDuration`` und ``printRemaining`` sind
   ``uint16_t``.

Den Fortschritt rechnet der Sketch selbst aus::

    FractionPrinted = (duration - warmUpDuration)
                      / max(1, (duration - warmUpDuration) + printRemaining)

Es werden deshalb kuenstliche Zeiten gesendet, deren Verhaeltnis genau dem
Klipper-Fortschritt entspricht. Da ``printRemaining`` nie negativ werden
kann, ist das Ergebnis immer < 1.0 - die letzte LED des Fortschrittsrings
bleibt bei V4.x grundsaetzlich auf der Druckfarbe (bei V3.0 laesst sich das
mit einem Wert > 1.0 umgehen, hier nicht).
"""

from __future__ import annotations

import json

from .state import MAX_HEATERS, Heater, HeaterState, PrinterState, PrinterStatus

#: Virtuelle Gesamtdauer fuer die Fortschrittskodierung (uint16-sicher)
PROGRESS_TICKS = 1000

#: ``uint16_t`` in StructPanelDueMessage
UINT16_MAX = 65535

#: ``BUFFER_MAX_LENGTH 32`` in JsonStreamingParser.h - nutzbar sind 31 Zeichen
MAX_TOKEN_CHARS = 31

#: Selbst gesetzte Obergrenze: schuetzt den 64 Byte grossen RX-Puffer des AVR
#: vor zu langen Nachrichten (der Sketch selbst hat hier kein Limit).
MAX_FRAME_BYTES = 512

#: ``state.status``-Woerter, die StreamingHandler.cpp kennt
STATUS_WORDS = {
    PrinterStatus.IDLE: "idle",
    PrinterStatus.PRINTING: "processing",
    PrinterStatus.PAUSED: "paused",
    PrinterStatus.PAUSING: "pausing",
    PrinterStatus.RESUMING: "resuming",
    PrinterStatus.BUSY: "busy",
    PrinterStatus.STOPPED: "halted",
    PrinterStatus.CONFIGURING: "updating",
    PrinterStatus.FLASHING: "starting",
}

#: Umkehrung inklusive der Woerter, die auf denselben Buchstaben fuehren
KNOWN_STATUS_WORDS = set(STATUS_WORDS.values()) | {"simulating", "off", "starting", "updating"}

#: ``heaters[].state``-Woerter
HEATER_WORDS = {
    HeaterState.OFF: "off",
    HeaterState.STANDBY: "standby",
    HeaterState.ACTIVE: "active",
    HeaterState.FAULT: "fault",
    HeaterState.TUNING: "tuning",
}

#: Reihenfolge der Schluessel auf oberster Ebene
FRAME_KEYS = ("state", "heaters", "timesLeft", "job")


class FrameError(ValueError):
    """Der erzeugte Rahmen verletzt eine Annahme des V4.x-Parsers."""


def encode_progress(progress: float) -> tuple:
    """Fortschritt 0.0..1.0 in ``(duration, warm_up, remaining)`` uebersetzen."""
    if progress != progress:  # NaN
        progress = 0.0
    progress = max(0.0, min(1.0, float(progress)))
    duration = int(round(progress * PROGRESS_TICKS))
    # remaining muss >= 1 bleiben, sonst verwirft der Sketch job.duration
    duration = max(0, min(PROGRESS_TICKS - 1, duration))
    remaining = max(1, PROGRESS_TICKS - duration)
    return duration, 0, remaining


def decoded_fraction(duration: int, warm_up: int, remaining: int) -> float:
    """Dieselbe Rechnung wie ``StructPanelDueMessage::FractionPrinted()``."""
    net = int(duration) - int(warm_up)
    return net / float(max(1, net + int(remaining)))


def _clamp_u16(value) -> int:
    return max(0, min(UINT16_MAX, int(value)))


def _temp(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return round(max(-999.0, min(999.0, number)), 1)


def _heater_payload(heater: Heater) -> dict:
    return {
        "current": _temp(heater.current),
        "active": _temp(heater.active),
        "standby": _temp(heater.standby),
        "state": HEATER_WORDS[HeaterState(heater.state)],
    }


def build_payload(state: PrinterState, full_ring: bool = True) -> dict:
    """Dict in genau der Reihenfolge, die der V4.x-Handler erwartet.

    ``full_ring`` wird nur der Einheitlichkeit wegen angenommen - bei V4.x
    laesst sich der Ring rechnerisch nicht komplett fuellen.
    """
    duration, warm_up, remaining = encode_progress(
        state.progress if state.status.shows_progress else 0.0
    )
    return {
        "state": {"status": STATUS_WORDS[PrinterStatus(state.status)]},
        # Der Streaming-Parser kommt mit einem einzigen Eintrag zurecht,
        # anders als der V3.0-Parser - hier wird nichts aufgefuellt.
        "heaters": [_heater_payload(h) for h in state.heater_array(min_entries=1)],
        # timesLeft MUSS vor job stehen (siehe Modul-Docstring)
        "timesLeft": {"slicer": _clamp_u16(remaining)},
        "job": {
            "duration": _clamp_u16(duration),
            "warmUpDuration": _clamp_u16(warm_up),
        },
    }


def build_frame(state: PrinterState, full_ring: bool = True, validate: bool = True) -> str:
    """Fertige Zeile inklusive ``\\n`` fuer die serielle Schnittstelle."""
    payload = build_payload(state, full_ring=full_ring)
    frame = json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n"
    if validate:
        validate_frame(frame)
    return frame


def _walk_tokens(node, keys, strings, arrays, depth=0):
    if isinstance(node, dict):
        for key, value in node.items():
            keys.append(key)
            _walk_tokens(value, keys, strings, arrays, depth + 1)
    elif isinstance(node, list):
        arrays.append(node)
        for item in node:
            _walk_tokens(item, keys, strings, arrays, depth + 1)
    elif isinstance(node, str):
        strings.append(node)


def validate_frame(frame: str) -> None:
    """Alle Annahmen des V4.x-Parsers pruefen; wirft :class:`FrameError`."""
    if not frame.endswith("\n"):
        raise FrameError("Rahmen muss mit \\n enden")
    body = frame[:-1]
    if "\n" in body:
        raise FrameError("nur ein Rahmen pro Zeile erlaubt")
    encoded = len(body.encode("ascii", "strict"))
    if encoded > MAX_FRAME_BYTES:
        raise FrameError("Rahmen zu lang: %d > %d Byte" % (encoded, MAX_FRAME_BYTES))

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise FrameError("kein gueltiges JSON: %s" % exc) from exc
    if not isinstance(payload, dict):
        raise FrameError("oberste Ebene muss ein Objekt sein")

    order = list(payload)
    if order != list(FRAME_KEYS):
        raise FrameError(
            "Schluesselreihenfolge muss %s sein, ist %s"
            % (", ".join(FRAME_KEYS), ", ".join(order))
        )

    keys, strings, arrays = [], [], []
    _walk_tokens(payload, keys, strings, arrays)
    if len(arrays) != 1:
        raise FrameError("genau ein Array (heaters) erlaubt, gefunden: %d" % len(arrays))
    if any(not isinstance(item, dict) for item in arrays[0]):
        raise FrameError("heaters darf nur Objekte enthalten")
    if len(arrays[0]) > MAX_HEATERS:
        # Der Handler indiziert Heater_ActTemp[index] ungeprueft - mehr als
        # NumberHeaters Eintraege schreiben im Sketch ueber den Array hinaus.
        raise FrameError(
            "hoechstens %d Heater erlaubt, gefunden: %d" % (MAX_HEATERS, len(arrays[0]))
        )
    for token in keys + strings:
        if len(token) > MAX_TOKEN_CHARS:
            raise FrameError(
                "%r ist laenger als %d Zeichen (BUFFER_MAX_LENGTH)" % (token, MAX_TOKEN_CHARS)
            )

    status = payload["state"].get("status")
    if status not in KNOWN_STATUS_WORDS:
        raise FrameError("unbekannter Status %r" % status)

    for index, heater in enumerate(payload["heaters"]):
        if list(heater) != ["current", "active", "standby", "state"]:
            raise FrameError("heaters[%d] hat unerwartete Schluessel: %s" % (index, list(heater)))
        if heater["state"] not in HEATER_WORDS.values():
            raise FrameError("heaters[%d]: unbekannter Zustand %r" % (index, heater["state"]))

    remaining = payload["timesLeft"]["slicer"]
    duration = payload["job"]["duration"]
    warm_up = payload["job"]["warmUpDuration"]
    for name, value in (("slicer", remaining), ("duration", duration), ("warmUpDuration", warm_up)):
        if not isinstance(value, int) or not 0 <= value <= UINT16_MAX:
            raise FrameError("%s muss ein uint16 sein, ist %r" % (name, value))
    if remaining < 1 and duration > 0:
        raise FrameError(
            "timesLeft.slicer muss >= 1 sein, sonst verwirft der Sketch job.duration"
        )
