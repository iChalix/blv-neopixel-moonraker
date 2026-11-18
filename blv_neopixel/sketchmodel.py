"""Nachbau des V3.0-Sketches in Python - zum Pruefen ohne Hardware.

Hier liegt eine zeilentreue Uebersetzung der relevanten Teile von
``NeoPixel_BLV.ino`` (Release V3.0):

* ``GetSerialMessage()``  - Rahmenerkennung, Entfernen der Anfuehrungszeichen
* ``JsonParseRoot()``     - der ``strstr``-basierte "Parser"
* ``AnalyzeSerialMessage()`` - Zuordnung auf Status/Heater/Fortschritt
* ``ConvertPosition2PixelIndex()`` und die Anzeigelogik der Ringe

Damit laesst sich jeder gesendete Rahmen gegen das echte Sketch-Verhalten
pruefen - inklusive der tatsaechlichen LED-Farben.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

#: ``#define SerialMessageBuffer 550``
SERIAL_MESSAGE_BUFFER = 550

#: ``#define NumberHeaters 5``
NUMBER_HEATERS = 5

WHITE = (255, 255, 255)

#: Farbvorgaben aus dem User-Config-Block von V3.0
DEFAULT_HEATER_COLORS = {
    "idle": WHITE,
    "heat_up": (255, 64, 64),
    "heat_up_done": (255, 0, 0),
    "cool_down": (0, 0, 255),
}

#: ``Heater0_Scale`` .. ``Heater4_Scale`` in der Werkseinstellung
DEFAULT_HEATER_SCALES = (10, 20, 20, 20, 20)

#: ``PrinterStatus_Color*`` in der Werkseinstellung
STATUS_COLORS = {
    "I": WHITE,
    "P": (64, 255, 64),
    "printing_done": (0, 255, 0),
    "S": (0, 0, 255),
    "C": (255, 255, 0),
    "A": (160, 32, 240),
    "D": (255, 255, 0),
    "R": (160, 32, 240),
    "B": (255, 255, 0),
    "F": (255, 255, 0),
    "default": WHITE,
}


def atof(text: str) -> float:
    """C-``atof``: fuehrende Zahl lesen, sonst 0.0."""
    if text is None:
        return 0.0
    text = text.strip()
    end = 0
    seen_digit = False
    seen_dot = False
    seen_exp = False
    while end < len(text):
        char = text[end]
        if char in "+-" and (end == 0 or text[end - 1] in "eE"):
            end += 1
            continue
        if char.isdigit():
            seen_digit = True
            end += 1
            continue
        if char == "." and not seen_dot and not seen_exp:
            seen_dot = True
            end += 1
            continue
        if char in "eE" and seen_digit and not seen_exp:
            seen_exp = True
            end += 1
            continue
        break
    if not seen_digit:
        return 0.0
    try:
        return float(text[:end])
    except ValueError:
        return 0.0


def atoi(text: str) -> int:
    return int(atof(text))


@dataclass
class SketchPrinter:
    """``struct StructPrinter`` (V3.0) bzw. ``StructPanelDueMessage`` (V4.x)."""

    status: str = " "
    fraction_printed: float = 0.0
    heater_act_temp: list = field(default_factory=lambda: [0.0] * NUMBER_HEATERS)
    heater_active_temp: list = field(default_factory=lambda: [0.0] * NUMBER_HEATERS)
    heater_standby_temp: list = field(default_factory=lambda: [0.0] * NUMBER_HEATERS)
    heater_status: list = field(default_factory=lambda: [0] * NUMBER_HEATERS)
    update_pending: bool = False
    #: nur V4.x: der Fortschritt wird aus diesen Zeiten errechnet
    print_duration: int = 0
    print_remaining: int = 0
    print_warm_up: int = 0

    def setpoint(self, heater: int) -> float:
        """Entspricht der SetTempHeater-Ermittlung in der Anzeigeschleife."""
        if self.heater_status[heater] == 2:
            return self.heater_active_temp[heater]
        if self.heater_status[heater] == 1:
            return self.heater_standby_temp[heater]
        return 0.0

    def reset_printing(self) -> None:
        """``StructPanelDueMessage::resetPrinting()`` (nur V4.x)."""
        self.print_duration = 0
        self.print_remaining = 0
        self.print_warm_up = 0

    def fraction_from_times(self) -> float:
        """``FractionPrinted()`` aus StreamingHandler.h (nur V4.x)."""
        net = self.print_duration - self.print_warm_up
        return net / float(max(1, net + self.print_remaining))


@dataclass
class RingConfig:
    """``NeoPixelN_*`` aus dem User-Config-Block."""

    display: int = 99
    leds: int = 16
    pixel_offset: int = 0
    reverse: bool = False
    temp_offset: float = 0.0
    scale: int = 0  # 0 -> DEFAULT_HEATER_SCALES[display]


def get_serial_message(data: str) -> str:
    """``GetSerialMessage()``: ab ``{`` sammeln, beim ersten ``}`` fertig.

    Anfuehrungszeichen werden verworfen, der Puffer ist begrenzt.
    """
    message = []
    begin = False
    position = 0
    for char in data:
        if char == "{":
            begin = True
            position = 1
            message = ["{"]
        elif begin and position < (SERIAL_MESSAGE_BUFFER - 3):
            if char == "}":
                message.append("}")
                return "".join(message)
            if char != '"':
                message.append(char)
                position += 1
    return ""


def json_parse_root(message: str, key: str, index: int):
    """``JsonParseRoot()`` - inklusive aller Eigenheiten.

    ``index == 0`` liest einen Einzelwert, ``index >= 1`` den n-ten
    Array-Eintrag (1-basiert). Einelementige Arrays liefern bei ``index >= 1``
    bewusst ``None``, genau wie im Original.
    """
    position = message.find(key)
    if position < 0:
        return None
    start = position + len(key) + 1
    if start >= len(message):
        return None

    if message[start] == "[":
        end = message.find("]", start)
        if end < 0:
            return None
        inner = message[start + 1 : end]
        if index == 0:
            return inner if "," not in inner else None
        if "," not in inner:
            # "MultiValue expected, but Singlevalue found"
            return None
        rest = inner
        for _ in range(1, index):
            if "," not in rest:
                return None
            rest = rest[rest.find(",") + 1 :]
        if "," in rest:
            return rest[: rest.find(",")]
        return rest

    if index != 0:
        return None
    rest = message[start:]
    if "," in rest:
        return rest[: rest.find(",")]
    if "}" in rest:
        return rest[: rest.find("}")]
    return None


def analyze_serial_message(message: str, printer: SketchPrinter = None) -> SketchPrinter:
    """``AnalyzeSerialMessage()``: Werte uebernehmen, sonst Altwert behalten."""
    printer = printer or SketchPrinter()
    printer.update_pending = False

    value = json_parse_root(message, "status", 0)
    if value is not None and value != "":
        printer.status = value[0]
        printer.update_pending = True

    for heater in range(NUMBER_HEATERS):
        value = json_parse_root(message, "heaters", heater + 1)
        if value is not None:
            printer.heater_act_temp[heater] = atof(value)
            printer.update_pending = True
        value = json_parse_root(message, "active", heater + 1)
        if value is not None:
            printer.heater_active_temp[heater] = atof(value)
            printer.update_pending = True
        value = json_parse_root(message, "standby", heater + 1)
        if value is not None:
            printer.heater_standby_temp[heater] = atof(value)
            printer.update_pending = True
        value = json_parse_root(message, "hstat", heater + 1)
        if value is not None:
            printer.heater_status[heater] = atoi(value)
            printer.update_pending = True

    value = json_parse_root(message, "fraction_printed", 0)
    if value is not None:
        printer.fraction_printed = atof(value)
        printer.update_pending = True

    return printer


def feed(frame: str, printer: SketchPrinter = None) -> SketchPrinter:
    """Kompletter Weg: serielle Daten -> Rahmen -> Druckerzustand."""
    message = get_serial_message(frame)
    if not message:
        return printer or SketchPrinter()
    return analyze_serial_message(message, printer)


def convert_position_to_pixel_index(
    pixel_count: int, pixel_offset: int, position: int, reverse: bool
) -> int:
    """``ConvertPosition2PixelIndex()``."""
    offset = -pixel_offset if reverse else pixel_offset
    if (position + offset) > pixel_count:
        new_position = (position + offset) - pixel_count
    elif (position + offset) < 1:
        new_position = pixel_count + (position + offset)
    else:
        new_position = position + offset
    if not reverse:
        return pixel_count - new_position
    return new_position - 1


def render_ring(printer: SketchPrinter, ring: RingConfig) -> list:
    """Die LED-Farben, die der Sketch fuer diesen Ring setzen wuerde."""
    pixels = [(0, 0, 0)] * ring.leds

    def put(position: int, color) -> None:
        index = convert_position_to_pixel_index(
            ring.leds, ring.pixel_offset, position, ring.reverse
        )
        if 0 <= index < ring.leds:
            pixels[index] = color

    if 0 <= ring.display < NUMBER_HEATERS:
        scale = ring.scale or DEFAULT_HEATER_SCALES[ring.display]
        actual = (printer.heater_act_temp[ring.display] - ring.temp_offset) / float(scale)
        target = (printer.setpoint(ring.display) - ring.temp_offset) / float(scale)
        for led in range(1, ring.leds + 1):
            if actual < led and target >= led:
                put(led, DEFAULT_HEATER_COLORS["heat_up"])
            elif actual >= led and target >= led:
                put(led, DEFAULT_HEATER_COLORS["heat_up_done"])
            elif actual >= led and target < led:
                put(led, DEFAULT_HEATER_COLORS["cool_down"])
            else:
                put(led, DEFAULT_HEATER_COLORS["idle"])
        return pixels

    if ring.display == 99:
        if printer.status == "P":
            for led in range(1, ring.leds + 1):
                if led < (printer.fraction_printed * ring.leds):
                    put(led, STATUS_COLORS["printing_done"])
                else:
                    put(led, STATUS_COLORS["P"])
        else:
            color = STATUS_COLORS.get(printer.status, STATUS_COLORS["default"])
            pixels = [color] * ring.leds
        return pixels

    return pixels


def ring_to_ansi(pixels: list, char: str = "██") -> str:
    """Ringfarben als ANSI-Blockgrafik fuer das Terminal."""
    out = []
    for red, green, blue in pixels:
        out.append("\033[38;2;%d;%d;%dm%s" % (red, green, blue, char))
    out.append("\033[0m")
    return "".join(out)


# --------------------------------------------------------------------------
# V4.x: Objektmodell-Protokoll mit Streaming-Parser
#
# Die Anzeigelogik (render_ring, ConvertPosition2PixelIndex, Farben) ist in
# V4.1 identisch mit V3.0 - nur das Einlesen unterscheidet sich. Hier liegt
# deshalb nur die Nachbildung von StreamingHandler.cpp.
# --------------------------------------------------------------------------

#: ``state.status``-Wort -> interner Statusbuchstabe (StreamingHandler.cpp)
V4_STATUS_LETTERS = {
    "idle": "I",
    "simulating": "P",
    "processing": "P",
    "resuming": "B",
    "busy": "D",
    "starting": "F",
    "updating": "C",
    "pausing": "R",
    "paused": "A",
    "halted": "S",
    "off": "S",
}

#: Statusworte, bei denen ``resetPrinting()`` gerufen wird
V4_RESET_PRINTING = ("idle", "starting", "updating", "halted", "off")

#: ``heaters[].state`` -> ``Heater_Status``
V4_HEATER_CODES = {"off": 0, "offline": 0, "active": 2, "standby": 1, "fault": 3, "tuning": 4}

#: ``BUFFER_MAX_LENGTH 32`` - laengere Schluessel/Werte schneidet der Parser ab
V4_BUFFER_MAX_LENGTH = 32


def _u16(value: int) -> int:
    """Zuweisung an ein ``uint16_t``."""
    return int(value) & 0xFFFF


class _Obj:
    """Traeger fuer ein JSON-Objekt mit erhaltener Schluesselreihenfolge."""

    __slots__ = ("pairs",)

    def __init__(self, pairs):
        self.pairs = pairs


def _token(value) -> str:
    """Wert so darstellen, wie der C-Parser ihn an ``value(String)`` gibt."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _emit(node, out) -> None:
    if isinstance(node, _Obj):
        out.append(("startObject", None))
        for key, value in node.pairs:
            out.append(("key", key))
            _emit(value, out)
        out.append(("endObject", None))
    elif isinstance(node, list):
        out.append(("startArray", None))
        for item in node:
            _emit(item, out)
        out.append(("endArray", None))
    else:
        out.append(("value", _token(node)))


def json_events(text: str) -> list:
    """Ereignisfolge wie der Streaming-Parser sie erzeugen wuerde."""
    decoded = json.loads(text, object_pairs_hook=_Obj)
    events = []
    _emit(decoded, events)
    return events


class StreamingHandlerModel:
    """Port von ``StreamingHandler`` samt Key-Stack, Index und ``aryDepth``."""

    def __init__(self, printer: SketchPrinter) -> None:
        self.printer = printer
        self.stack = ["", "", ""]
        self.current = ""
        self.index = -1
        self.ary_depth = 0
        self.in_array = False
        self.values = 0

    # ---------------------------------------------------------------- Stack

    def push(self) -> None:
        self.stack[2] = self.stack[1]
        self.stack[1] = self.stack[0]
        if self.values == 0 and self.current != "":
            self.stack[0] = self.current

    def pop(self) -> None:
        self.stack[0] = self.stack[1]
        self.stack[1] = self.stack[2]

    # ---------------------------------------------------------------- Events

    def run(self, events: list) -> SketchPrinter:
        for kind, payload in events:
            if kind == "startArray":
                self.index = -1
                self.in_array = True
                self.ary_depth = 0
                self.push()
            elif kind == "startObject":
                if self.ary_depth == 0 and self.in_array:
                    self.index += 1
                self.push()
                self.ary_depth += 1
            elif kind == "endArray":
                self.pop()
                self.current = ""
                self.in_array = False
            elif kind == "endObject":
                self.pop()
                self.current = ""
                self.ary_depth -= 1
            elif kind == "key":
                self.current = payload[: V4_BUFFER_MAX_LENGTH - 1]
                self.values = 0
            elif kind == "value":
                self.values += 1
                if self.ary_depth == 0 and self.in_array:
                    self.index += 1
                self.handle_value(payload[: V4_BUFFER_MAX_LENGTH - 1])
        return self.printer

    # ---------------------------------------------------------------- Werte

    def _heater_slot(self):
        """Der C-Code indiziert ungeprueft - hier wird nur geschuetzt."""
        if 0 <= self.index < NUMBER_HEATERS:
            return self.index
        return None

    def handle_value(self, value: str) -> None:
        printer = self.printer
        current, parent = self.current, self.stack[0]

        if current == "status" and parent == "state":
            if value in V4_RESET_PRINTING:
                printer.reset_printing()
            letter = V4_STATUS_LETTERS.get(value)
            if letter is not None:
                printer.status = letter
            printer.update_pending = True
        elif parent == "heaters" and current in ("active", "standby", "current", "state"):
            slot = self._heater_slot()
            if slot is None:
                return
            if current == "active":
                printer.heater_active_temp[slot] = atof(value)
            elif current == "standby":
                printer.heater_standby_temp[slot] = atof(value)
            elif current == "current":
                printer.heater_act_temp[slot] = atof(value)
            else:
                if value in V4_HEATER_CODES:
                    printer.heater_status[slot] = V4_HEATER_CODES[value]
            printer.update_pending = True
        elif current == "slicer" and parent == "timesLeft":
            printer.print_remaining = _u16(atoi(value))
            printer.update_pending = True
        elif current == "duration" and parent == "job" and printer.print_remaining > 0:
            printer.print_duration = _u16(atoi(value))
            printer.update_pending = True
        elif current == "warmUpDuration" and parent == "job" and printer.print_remaining > 0:
            printer.print_warm_up = _u16(atoi(value))
            printer.update_pending = True


def get_serial_message_v4(data: str) -> str:
    """``GetSerialMessage()`` von V4.x: ab ``{`` sammeln, Ende beim ``\\n``."""
    start = data.find("{")
    if start < 0:
        return ""
    end = data.find("\n", start)
    if end < 0:
        return ""
    return data[start:end]


def feed_v4(frame: str, printer: SketchPrinter = None) -> SketchPrinter:
    """Serielle Daten -> Druckerzustand, wie V4.x sie auswerten wuerde.

    Hinweis: Der echte Sketch schreibt die Werte waehrend des Parsens direkt
    in den Druckerzustand, uebernimmt also auch aus einer abgebrochenen
    Nachricht noch Teilwerte. Dieses Modell verlangt eine vollstaendige Zeile
    und ignoriert alles andere - fuer selbst erzeugte Rahmen reicht das.
    """
    printer = printer or SketchPrinter()
    printer.update_pending = False
    message = get_serial_message_v4(frame)
    if not message:
        return printer
    try:
        events = json_events(message)
    except ValueError:
        return printer
    StreamingHandlerModel(printer).run(events)
    printer.fraction_printed = printer.fraction_from_times()
    return printer
