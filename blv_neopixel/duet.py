"""Serialisierung in das M408-JSON, das NeoPixelBLVmgn **V3.0** parst.

V3.0 benutzt keinen echten JSON-Parser, sondern ``JsonParseRoot()`` - eine
Handvoll ``strstr()``-Aufrufe auf einem 550-Byte-Puffer. Daraus folgen harte
Regeln fuer jeden Rahmen, die hier zentral durchgesetzt und von
:func:`validate_frame` geprueft werden:

1. ``GetSerialMessage()`` beginnt bei ``{`` und beendet die Nachricht beim
   **ersten** ``}``. Also: genau ein flaches Objekt, keine verschachtelten
   Objekte.
2. Anfuehrungszeichen werden beim Einlesen verworfen; der Wert beginnt immer
   genau ein Zeichen nach dem Schluesselnamen. Deshalb darf hinter dem
   Doppelpunkt **kein Leerzeichen** stehen.
3. Schluessel werden mit ``strstr()`` irgendwo im Puffer gesucht - kein
   Schluessel darf Teilzeichenkette eines anderen sein, und es darf keine
   unbeteiligten Felder geben.
4. Indizierter Array-Zugriff (``heaters``, ``active``, ``standby``, ``hstat``)
   verlangt **mindestens zwei Werte**: bei nur einem Wert liefert der Parser
   ``false`` ("MultiValue expected, but Singlevalue found").
5. Der Array-Inhalt wird in ein ``char JsonValue[100]`` kopiert - der Text
   zwischen ``[`` und ``]`` muss also klar unter 100 Zeichen bleiben.
6. Der Empfangspuffer ist 550 Byte (``SerialMessageBuffer``), nutzbar bis
   ``SerialMessageBuffer - 3``.

Fortschritt: V3.0 liest ``fraction_printed`` direkt als Float. Die Anzeige
vergleicht ``NeoPixelLEDID < FractionPrinted * LEDs``, d. h. bei genau 1.0
bleibt die letzte LED auf der "Printing"-Farbe. Ein minimaler Ueberschwinger
> 1.0 fuellt den Ring unabhaengig von der LED-Anzahl komplett - siehe
:data:`FULL_RING_FRACTION`.
"""

from __future__ import annotations

import json

from .state import MIN_ARRAY_ENTRIES, Heater, HeaterState, PrinterState, PrinterStatus

#: Groesse von ``char SerialMessage[SerialMessageBuffer]`` im Sketch
SERIAL_MESSAGE_BUFFER = 550

#: Nutzbare Laenge inkl. Sicherheitsabstand (Sketch: ``Position < Buffer - 3``)
MAX_FRAME_BYTES = SERIAL_MESSAGE_BUFFER - 10

#: ``char JsonValue[100]`` in ``JsonParseRoot()``
MAX_ARRAY_CHARS = 90

#: Wert, der bei 100 % gesendet wird, damit auch die letzte LED umschaltet
FULL_RING_FRACTION = 1.01

#: Ab diesem Fortschritt gilt der Druck als fertig
FULL_RING_THRESHOLD = 0.9995

#: Schluessel, die der Sketch sucht (AnalyzeSerialMessage)
FRAME_KEYS = ("status", "heaters", "active", "standby", "hstat", "fraction_printed")


class FrameError(ValueError):
    """Der erzeugte Rahmen verletzt eine Annahme des V3.0-Parsers."""


def encode_fraction(progress: float, full_ring: bool = True) -> float:
    """Fortschritt 0.0..1.0 fuer ``fraction_printed`` aufbereiten."""
    if progress != progress:  # NaN
        progress = 0.0
    progress = max(0.0, min(1.0, float(progress)))
    if full_ring and progress >= FULL_RING_THRESHOLD:
        return FULL_RING_FRACTION
    return round(progress, 3)


def _temp(value: float) -> float:
    """Temperaturen kurz halten - eine Dezimalstelle wie bei der echten RRF."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN
        return 0.0
    # Der Sketch rechnet mit float; -999..999 deckt jeden realen Sensor ab.
    value = max(-999.0, min(999.0, value))
    return round(value, 1)


def build_payload(state: PrinterState, full_ring: bool = True) -> dict:
    """Flaches Dict in genau der Form, die V3.0 lesen kann."""
    heaters = state.heater_array()
    fraction = encode_fraction(
        state.progress if state.status.shows_progress else 0.0, full_ring=full_ring
    )
    return {
        "status": PrinterStatus(state.status).letter,
        "heaters": [_temp(h.current) for h in heaters],
        "active": [_temp(h.active) for h in heaters],
        "standby": [_temp(h.standby) for h in heaters],
        "hstat": [HeaterState(h.state).code for h in heaters],
        "fraction_printed": fraction,
    }


def build_frame(state: PrinterState, full_ring: bool = True, validate: bool = True) -> str:
    """Fertige Zeile inkl. ``\\n`` fuer die serielle Schnittstelle."""
    payload = build_payload(state, full_ring=full_ring)
    frame = json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n"
    if validate:
        validate_frame(frame)
    return frame


def strip_quotes(frame: str) -> str:
    """Nachbildung von ``GetSerialMessage()``: Anfuehrungszeichen verwerfen."""
    body = frame.strip()
    if not body.startswith("{"):
        raise FrameError("Rahmen beginnt nicht mit '{'")
    end = body.find("}")
    if end < 0:
        raise FrameError("Rahmen enthaelt kein '}'")
    return body[: end + 1].replace('"', "")


def validate_frame(frame: str) -> None:
    """Alle Annahmen des V3.0-Parsers pruefen; wirft :class:`FrameError`."""
    if not frame.endswith("\n"):
        raise FrameError("Rahmen muss mit \\n enden")
    body = frame[:-1]
    encoded = len(body.encode("ascii", "strict"))
    if encoded > MAX_FRAME_BYTES:
        raise FrameError("Rahmen zu lang: %d > %d Byte" % (encoded, MAX_FRAME_BYTES))
    if body.count("{") != 1 or body.count("}") != 1:
        raise FrameError("genau ein flaches Objekt erwartet (keine verschachtelten Objekte)")
    if not body.startswith("{") or not body.endswith("}"):
        raise FrameError("Rahmen muss genau ein Objekt sein")
    if ": " in body or ", " in body:
        raise FrameError("keine Leerzeichen hinter ':' oder ',' erlaubt")

    stripped = strip_quotes(frame)

    for key in FRAME_KEYS:
        if stripped.count(key) != 1:
            raise FrameError("Schluessel %r muss genau einmal vorkommen" % key)
        pos = stripped.find(key)
        sep = stripped[pos + len(key)]
        if sep != ":":
            raise FrameError("hinter %r muss direkt ':' folgen, gefunden %r" % (key, sep))

    # Teilzeichenketten-Kollisionen zwischen den Schluesseln ausschliessen
    for key in FRAME_KEYS:
        for other in FRAME_KEYS:
            if key != other and key in other:
                raise FrameError("Schluessel %r ist Teil von %r" % (key, other))

    for key in ("heaters", "active", "standby", "hstat"):
        start = stripped.find(key) + len(key) + 1
        if stripped[start] != "[":
            raise FrameError("%r muss ein Array sein" % key)
        end = stripped.find("]", start)
        if end < 0:
            raise FrameError("%r ohne ']'" % key)
        inner = stripped[start + 1 : end]
        if len(inner) >= MAX_ARRAY_CHARS:
            raise FrameError(
                "Array %r zu lang fuer char[100]: %d Zeichen" % (key, len(inner))
            )
        if inner.count(",") + 1 < MIN_ARRAY_ENTRIES:
            raise FrameError(
                "Array %r braucht mindestens %d Werte (V3.0-Parser)"
                % (key, MIN_ARRAY_ENTRIES)
            )

    status_pos = stripped.find("status:") + len("status:")
    if stripped[status_pos + 1] != ",":
        raise FrameError("status muss genau ein Zeichen sein")
