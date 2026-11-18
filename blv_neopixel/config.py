"""Konfiguration der Middleware (INI-Format, ohne Fremdabhaengigkeiten)."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field

from . import protocol as protocol_module
from .state import MAX_HEATERS, PrinterStatus

#: Suchreihenfolge, wenn kein --config angegeben wird
DEFAULT_CONFIG_PATHS = (
    "~/printer_data/config/blv-neopixel.conf",
    "~/klipper_config/blv-neopixel.conf",
    "/etc/blv-neopixel/blv-neopixel.conf",
    "./blv-neopixel.conf",
)

PROGRESS_SOURCES = ("file", "sdcard", "layer")


class ConfigError(ValueError):
    """Fehlerhafte oder unvollstaendige Konfiguration."""


@dataclass
class MoonrakerConfig:
    url: str = "ws://127.0.0.1:7125/websocket"
    api_key: str = ""
    connect_timeout: float = 10.0
    reconnect_min: float = 1.0
    reconnect_max: float = 15.0


@dataclass
class SerialConfig:
    port: str = ""
    baudrate: int = 57600
    #: DTR vor dem Oeffnen abschalten, damit der Arduino nicht neu startet
    avoid_reset: bool = True
    #: Wartezeit nach dem Oeffnen (Bootloader + Startup-Animation)
    reset_delay: float = 2.0
    write_timeout: float = 2.0
    #: Ausgaben des Arduino (Debug-Build) mitloggen
    log_incoming: bool = False


@dataclass
class HeaterMap:
    """Zuordnung Klipper-Objekt -> Heater-Index des Sketches."""

    index: int
    object: str
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.object


@dataclass
class PrinterConfig:
    #: Version des Arduino-Sketches: "4.1" (Objektmodell) oder "3.0" (M408)
    sketch_version: str = protocol_module.V41
    progress_source: str = "file"
    full_ring_at_100: bool = True
    #: True, wenn full_ring_at_100 in der Datei steht - nur dann wird auf die
    #: Wirkungslosigkeit bei V4.x hingewiesen
    full_ring_explicit: bool = False
    #: Sendeintervall waehrend Druck/Aktivitaet
    interval_active: float = 1.0
    #: Sendeintervall im Leerlauf
    interval_idle: float = 5.0
    #: Mindestabstand zwischen zwei Rahmen (Schutz vor RX-Overflow am Arduino)
    min_interval: float = 0.35
    #: Nach Druckende so lange 100 % anzeigen (Sekunden, 0 = aus)
    complete_hold: float = 30.0
    #: Nach Abbruch so lange "Stopped" anzeigen (Sekunden, 0 = aus)
    cancel_hold: float = 10.0
    #: Manuelles Heizen/Homen als "busy" anzeigen
    busy_from_idle_timeout: bool = True
    #: Status, wenn Moonraker/Klipper nicht erreichbar ist
    offline_status: PrinterStatus = PrinterStatus.STOPPED
    #: Status, waehrend Klipper noch startet
    startup_status: PrinterStatus = PrinterStatus.CONFIGURING
    #: Status bei Klipper-Shutdown/Fehler
    error_status: PrinterStatus = PrinterStatus.STOPPED


@dataclass
class Config:
    moonraker: MoonrakerConfig = field(default_factory=MoonrakerConfig)
    serial: SerialConfig = field(default_factory=SerialConfig)
    printer: PrinterConfig = field(default_factory=PrinterConfig)
    heaters: list = field(default_factory=list)
    log_level: str = "info"
    source: str = "<defaults>"

    @property
    def heater_objects(self) -> dict:
        """Klipper-Objektname -> :class:`HeaterMap`."""
        return {h.object: h for h in self.heaters}

    @property
    def protocol(self):
        """Das zur eingestellten Sketch-Version passende Protokoll."""
        return protocol_module.get(self.printer.sketch_version)

    def warnings(self) -> list:
        """Hinweise auf Einstellungen, die so nicht wirken koennen."""
        notes = []
        if (
            self.printer.full_ring_at_100
            and self.printer.full_ring_explicit
            and not self.protocol.supports_full_ring
        ):
            notes.append(
                "full_ring_at_100 wirkt nur mit Sketch V3.0 - bei V4.x rechnet "
                "der Sketch den Fortschritt selbst aus, die letzte LED bleibt "
                "auf der Druckfarbe."
            )
        if not self.serial.port:
            notes.append("[serial] port ist nicht gesetzt - es wird nichts gesendet.")
        return notes

    def describe(self) -> str:
        lines = [
            "Konfiguration: %s" % self.source,
            "  Sketch    : %s" % self.protocol.label,
            "  Moonraker : %s" % self.moonraker.url,
            "  Serial    : %s @ %d Baud" % (self.serial.port or "<nicht gesetzt>", self.serial.baudrate),
            "  Intervalle: aktiv %.2fs / idle %.2fs / min %.2fs"
            % (self.printer.interval_active, self.printer.interval_idle, self.printer.min_interval),
            "  Fortschritt: %s" % self.printer.progress_source,
        ]
        for heater in sorted(self.heaters, key=lambda h: h.index):
            lines.append(
                "  Heater %d  : %s (%s)" % (heater.index, heater.object, heater.label)
            )
        for note in self.warnings():
            lines.append("  Hinweis   : %s" % note)
        return "\n".join(lines)


def default_heaters() -> list:
    """Standardbelegung eines BLV mgn mit Klipper: Bett + ein Hotend."""
    return [
        HeaterMap(index=0, object="heater_bed", name="Heizbett"),
        HeaterMap(index=1, object="extruder", name="Hotend"),
    ]


def find_config(explicit: str = "") -> str:
    if explicit:
        path = os.path.abspath(os.path.expanduser(explicit))
        if not os.path.isfile(path):
            raise ConfigError("Konfigurationsdatei nicht gefunden: %s" % path)
        return path
    for candidate in DEFAULT_CONFIG_PATHS:
        path = os.path.abspath(os.path.expanduser(candidate))
        if os.path.isfile(path):
            return path
    return ""


def _status(value: str, where: str) -> PrinterStatus:
    raw = (value or "").strip()
    if not raw:
        raise ConfigError("%s: leerer Status" % where)
    upper = raw.upper()
    for status in PrinterStatus:
        if upper == status.letter or upper == status.name:
            return status
    valid = ", ".join("%s=%s" % (s.letter, s.name.lower()) for s in PrinterStatus)
    raise ConfigError("%s: unbekannter Status %r (erlaubt: %s)" % (where, raw, valid))


def _normalize_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        raise ConfigError("[moonraker] url darf nicht leer sein")
    if url.startswith("http://"):
        url = "ws://" + url[len("http://") :]
    elif url.startswith("https://"):
        url = "wss://" + url[len("https://") :]
    elif not url.startswith(("ws://", "wss://")):
        url = "ws://" + url
    if "/websocket" not in url:
        url = url.rstrip("/") + "/websocket"
    return url


def load(path: str = "") -> Config:
    """Konfiguration laden; ohne Datei gelten die Standardwerte."""
    cfg = Config(heaters=default_heaters())
    resolved = find_config(path)
    if not resolved:
        return cfg

    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise ConfigError("Konfiguration %s nicht lesbar: %s" % (resolved, exc)) from exc

    cfg.source = resolved

    if parser.has_section("moonraker"):
        sec = parser["moonraker"]
        cfg.moonraker.url = _normalize_url(sec.get("url", cfg.moonraker.url))
        cfg.moonraker.api_key = sec.get("api_key", "").strip()
        key_file = sec.get("api_key_file", "").strip()
        if key_file and not cfg.moonraker.api_key:
            expanded = os.path.expanduser(key_file)
            try:
                with open(expanded, "r", encoding="utf-8") as handle:
                    cfg.moonraker.api_key = handle.read().strip()
            except OSError as exc:
                raise ConfigError("api_key_file %s nicht lesbar: %s" % (expanded, exc)) from exc
        cfg.moonraker.connect_timeout = sec.getfloat("connect_timeout", cfg.moonraker.connect_timeout)
        cfg.moonraker.reconnect_min = sec.getfloat("reconnect_min", cfg.moonraker.reconnect_min)
        cfg.moonraker.reconnect_max = sec.getfloat("reconnect_max", cfg.moonraker.reconnect_max)

    if parser.has_section("serial"):
        sec = parser["serial"]
        cfg.serial.port = os.path.expanduser(sec.get("port", cfg.serial.port).strip())
        cfg.serial.baudrate = sec.getint("baudrate", cfg.serial.baudrate)
        cfg.serial.avoid_reset = sec.getboolean("avoid_reset", cfg.serial.avoid_reset)
        cfg.serial.reset_delay = sec.getfloat("reset_delay", cfg.serial.reset_delay)
        cfg.serial.write_timeout = sec.getfloat("write_timeout", cfg.serial.write_timeout)
        cfg.serial.log_incoming = sec.getboolean("log_incoming", cfg.serial.log_incoming)

    if parser.has_section("printer"):
        sec = parser["printer"]
        if "sketch_version" in sec:
            try:
                cfg.printer.sketch_version = protocol_module.normalize(sec["sketch_version"])
            except protocol_module.UnknownVersion as exc:
                raise ConfigError("[printer] sketch_version: %s" % exc) from exc
        source = sec.get("progress_source", cfg.printer.progress_source).strip().lower()
        if source not in PROGRESS_SOURCES:
            raise ConfigError(
                "[printer] progress_source %r unbekannt (erlaubt: %s)"
                % (source, ", ".join(PROGRESS_SOURCES))
            )
        cfg.printer.progress_source = source
        if "full_ring_at_100" in sec:
            cfg.printer.full_ring_at_100 = sec.getboolean("full_ring_at_100")
            cfg.printer.full_ring_explicit = True
        cfg.printer.interval_active = sec.getfloat("interval_active", cfg.printer.interval_active)
        cfg.printer.interval_idle = sec.getfloat("interval_idle", cfg.printer.interval_idle)
        cfg.printer.min_interval = sec.getfloat("min_interval", cfg.printer.min_interval)
        cfg.printer.complete_hold = sec.getfloat("complete_hold", cfg.printer.complete_hold)
        cfg.printer.cancel_hold = sec.getfloat("cancel_hold", cfg.printer.cancel_hold)
        cfg.printer.busy_from_idle_timeout = sec.getboolean(
            "busy_from_idle_timeout", cfg.printer.busy_from_idle_timeout
        )
        if "offline_status" in sec:
            cfg.printer.offline_status = _status(sec["offline_status"], "[printer] offline_status")
        if "startup_status" in sec:
            cfg.printer.startup_status = _status(sec["startup_status"], "[printer] startup_status")
        if "error_status" in sec:
            cfg.printer.error_status = _status(sec["error_status"], "[printer] error_status")

    if parser.has_section("logging"):
        cfg.log_level = parser["logging"].get("level", cfg.log_level).strip().lower()

    heaters = []
    for section in parser.sections():
        if not section.startswith("heater"):
            continue
        parts = section.split()
        if len(parts) != 2 or parts[0] != "heater":
            raise ConfigError("Abschnitt [%s] erwartet die Form [heater N]" % section)
        try:
            index = int(parts[1])
        except ValueError as exc:
            raise ConfigError("Abschnitt [%s]: N muss eine Zahl sein" % section) from exc
        if not 0 <= index < MAX_HEATERS:
            raise ConfigError(
                "Abschnitt [%s]: Index muss 0..%d sein (Sketch: NumberHeaters)"
                % (section, MAX_HEATERS - 1)
            )
        sec = parser[section]
        obj = sec.get("object", "").strip()
        if not obj:
            raise ConfigError("Abschnitt [%s]: 'object' fehlt" % section)
        heaters.append(HeaterMap(index=index, object=obj, name=sec.get("name", "").strip()))

    if heaters:
        seen = {}
        for heater in heaters:
            if heater.index in seen:
                raise ConfigError("Heater-Index %d doppelt belegt" % heater.index)
            seen[heater.index] = heater
        cfg.heaters = sorted(heaters, key=lambda h: h.index)

    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if not cfg.heaters:
        raise ConfigError("mindestens ein [heater N]-Abschnitt wird benoetigt")
    if cfg.printer.min_interval <= 0:
        raise ConfigError("[printer] min_interval muss > 0 sein")
    if cfg.printer.interval_active < cfg.printer.min_interval:
        raise ConfigError("[printer] interval_active darf nicht kleiner als min_interval sein")
    if cfg.printer.interval_idle < cfg.printer.min_interval:
        raise ConfigError("[printer] interval_idle darf nicht kleiner als min_interval sein")
    if cfg.serial.baudrate <= 0:
        raise ConfigError("[serial] baudrate muss > 0 sein")
