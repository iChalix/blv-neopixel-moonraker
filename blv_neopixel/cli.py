"""Kommandozeile der Middleware."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__, config as config_module, protocol as protocol_module, sketchmodel
from .config import Config, ConfigError
from .service import Service, run_provider
from .simulator import Simulator
from .state import STATUS_DESCRIPTION, Heater, HeaterState, PrinterState, PrinterStatus

LOGGER = logging.getLogger("blv_neopixel")

#: Standardverdrahtung des BLV: Ring 1 = Pin 7, Ring 2 = Pin 6, Ring 3 = Pin 8
PREVIEW_RINGS = (
    ("Ring 1 (Pin 7) Status", sketchmodel.RingConfig(display=99)),
    ("Ring 2 (Pin 6) Bett  ", sketchmodel.RingConfig(display=0)),
    ("Ring 3 (Pin 8) Hotend", sketchmodel.RingConfig(display=1)),
)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, (level or "info").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_status(raw: str) -> PrinterStatus:
    upper = (raw or "").strip().upper()
    for status in PrinterStatus:
        if upper in (status.letter, status.name):
            return status
    raise argparse.ArgumentTypeError(
        "unbekannter Status %r (erlaubt: %s)"
        % (raw, ", ".join("%s/%s" % (s.letter, s.name.lower()) for s in PrinterStatus))
    )


def parse_heater(raw: str):
    """``IDX:CURRENT[/TARGET]`` zerlegen."""
    try:
        index_text, rest = raw.split(":", 1)
        index = int(index_text)
        if "/" in rest:
            current_text, target_text = rest.split("/", 1)
        else:
            current_text, target_text = rest, "0"
        return index, float(current_text), float(target_text)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "Heater erwartet die Form IDX:IST[/SOLL], z. B. 1:200/235"
        ) from exc


def state_from_args(args, cfg: Config) -> PrinterState:
    heaters = {}
    if args.heater:
        for index, current, target in args.heater:
            heaters[index] = Heater(
                index=index,
                current=current,
                active=target,
                state=HeaterState.ACTIVE if target > 0 else HeaterState.OFF,
            )
    else:
        for mapping in cfg.heaters:
            heaters[mapping.index] = Heater(index=mapping.index, name=mapping.label)
    return PrinterState(status=args.status, heaters=heaters, progress=args.progress)


def print_preview(protocol, frame: str, label: str = "") -> None:
    printer = protocol.parse(frame)
    if label:
        print(label)
    print("  Sketch liest: status=%r fraction=%.3f" % (printer.status, printer.fraction_printed))
    for heater in range(2):
        print(
            "                Heater %d: ist %.1f soll %.1f hstat %d"
            % (
                heater,
                printer.heater_act_temp[heater],
                printer.setpoint(heater),
                printer.heater_status[heater],
            )
        )
    for name, ring in PREVIEW_RINGS:
        pixels = sketchmodel.render_ring(printer, ring)
        print("  %s %s" % (name, sketchmodel.ring_to_ansi(pixels)))


# ---------------------------------------------------------------- Unterbefehle


def _start_service(cfg: Config, dry_run: bool, on_frame=None) -> int:
    """Dienst starten und fehlende Abhaengigkeiten verstaendlich melden."""
    from .moonraker import MoonrakerUnavailable
    from .serial_link import SerialUnavailable

    try:
        service = Service(cfg, dry_run=dry_run, on_frame=on_frame)
    except (MoonrakerUnavailable, SerialUnavailable) as exc:
        print("Fehler: %s" % exc, file=sys.stderr)
        print("Abhilfe: pip install -r requirements.txt", file=sys.stderr)
        return 1
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        print()
        LOGGER.info("Abbruch durch Benutzer")
    return 0


def cmd_run(args, cfg: Config) -> int:
    return _start_service(cfg, dry_run=args.dry_run)


def cmd_dump(args, cfg: Config) -> int:
    def on_frame(state, frame, ok):
        print(frame.rstrip("\n"))
        if args.preview:
            print_preview(cfg.protocol, frame)
        sys.stdout.flush()

    return _start_service(cfg, dry_run=True, on_frame=on_frame)


def cmd_simulate(args, cfg: Config) -> int:
    simulator = Simulator(cfg, speed=args.speed, loop=not args.once)
    if args.preview and not args.log_level:
        # Logzeilen wuerden die Ringgrafik zerreissen
        logging.getLogger().setLevel(logging.WARNING)

    def on_frame(state, frame, ok):
        if args.preview:
            print_preview(
                cfg.protocol, frame, label="[%s] %s" % (simulator.phase_name, state.summary())
            )
            sys.stdout.flush()

    async def main():
        task = asyncio.ensure_future(
            run_provider(cfg, simulator, dry_run=args.dry_run, on_frame=on_frame)
        )
        if args.once:
            while not simulator.finished:
                await asyncio.sleep(0.2)
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
    return 0


def cmd_frame(args, cfg: Config) -> int:
    state = state_from_args(args, cfg)
    protocol = cfg.protocol
    try:
        frame = protocol.build_frame(state, full_ring=cfg.printer.full_ring_at_100)
    except protocol.frame_error as exc:
        print("Fehler: %s" % exc, file=sys.stderr)
        return 1
    print(frame.rstrip("\n"))
    print(
        "  Sketch %s, %d Byte, Grenze %d"
        % (protocol.version, len(frame) - 1, protocol.max_frame_bytes)
    )
    if args.preview:
        print_preview(protocol, frame)
    return 0


def cmd_ports(args, cfg: Config) -> int:
    from .serial_link import SerialUnavailable, list_ports

    try:
        ports = list_ports()
    except SerialUnavailable as exc:
        print("Fehler: %s" % exc, file=sys.stderr)
        return 1
    if not ports:
        print("keine seriellen Schnittstellen gefunden")
        return 1
    for device, description, hwid in ports:
        print("%-24s %s" % (device, description))
        print("%-24s %s" % ("", hwid))
    print()
    print("Tipp: stabile Pfade liegen unter /dev/serial/by-id/ - die bitte in die Konfiguration eintragen.")
    return 0


def cmd_config(args, cfg: Config) -> int:
    print(cfg.describe())
    return 0


def _selftest_protocol(protocol, full_ring: bool, failures: list) -> None:
    """Rahmen erzeugen und gegen das nachgebaute Sketch-Verhalten pruefen."""

    def check(name, condition, detail=""):
        print("  [%s] %s%s" % ("ok" if condition else "FEHLER", name, (" - " + detail) if detail else ""))
        if not condition:
            failures.append("%s: %s" % (protocol.version, name))

    print("\n=== Sketch %s - %s" % (protocol.version, protocol.label))

    print("Statuszustaende:")
    for status in PrinterStatus:
        state = PrinterState(
            status=status,
            heaters={
                0: Heater(index=0, current=61.4, active=60.0, state=HeaterState.ACTIVE),
                1: Heater(index=1, current=234.9, active=235.0, state=HeaterState.ACTIVE),
            },
            progress=0.5,
        )
        frame = protocol.build_frame(state, full_ring=full_ring)
        printer = protocol.parse(frame)
        ok = (
            printer.status == status.letter
            and abs(printer.heater_act_temp[0] - 61.4) < 0.05
            and abs(printer.heater_act_temp[1] - 234.9) < 0.05
            and printer.setpoint(0) == 60.0
            and printer.setpoint(1) == 235.0
            and printer.heater_status[0] == 2
        )
        check(
            "%s -> %s" % (status.letter, STATUS_DESCRIPTION[status]),
            ok,
            "%d Byte" % (len(frame) - 1),
        )

    print("Fortschritt:")
    for progress in (0.0, 0.125, 0.5, 0.999, 1.0):
        state = PrinterState(
            status=PrinterStatus.PRINTING,
            heaters={0: Heater(index=0), 1: Heater(index=1)},
            progress=progress,
        )
        frame = protocol.build_frame(state, full_ring=full_ring)
        printer = protocol.parse(frame)
        ring = sketchmodel.render_ring(printer, sketchmodel.RingConfig(display=99))
        done = sum(1 for pixel in ring if pixel == sketchmodel.STATUS_COLORS["printing_done"])
        if progress >= 0.9995:
            expected = 16 if (full_ring and protocol.supports_full_ring) else 15
        else:
            expected = int(progress * 16)
        check(
            "%5.1f%% -> %2d/16 LEDs fertig" % (progress * 100.0, done),
            abs(done - expected) <= 1,
            "fraction=%.3f" % printer.fraction_printed,
        )

    print("Grenzfaelle:")
    single = PrinterState(status=PrinterStatus.IDLE, heaters={0: Heater(index=0, current=42.0)})
    frame = protocol.build_frame(single, full_ring=full_ring)
    printer = protocol.parse(frame)
    check(
        "einzelner Heater wird korrekt gelesen",
        abs(printer.heater_act_temp[0] - 42.0) < 0.05,
        "V3.0 braucht dafuer zwei Array-Werte",
    )

    full = PrinterState(
        status=PrinterStatus.PRINTING,
        heaters={
            i: Heater(index=i, current=199.9, active=250.0, state=HeaterState.ACTIVE)
            for i in range(5)
        },
        progress=0.5,
    )
    frame = protocol.build_frame(full, full_ring=full_ring)
    printer = protocol.parse(frame)
    check(
        "fuenf Heater passen in die Puffer",
        all(abs(printer.heater_act_temp[i] - 199.9) < 0.05 for i in range(5)),
        "%d Byte, Grenze %d" % (len(frame) - 1, protocol.max_frame_bytes),
    )

    gaps = PrinterState(
        status=PrinterStatus.IDLE,
        heaters={
            0: Heater(index=0, current=55.0, active=55.0, state=HeaterState.ACTIVE),
            2: Heater(index=2, current=31.0),
        },
    )
    frame = protocol.build_frame(gaps, full_ring=full_ring)
    printer = protocol.parse(frame)
    check(
        "Luecke zwischen Heater 0 und 2 wird aufgefuellt",
        abs(printer.heater_act_temp[2] - 31.0) < 0.05 and printer.heater_status[1] == 0,
    )

    if protocol.polls:
        check(
            "Statusabfrage des Sketches wird erkannt",
            protocol.is_request('M409 F"d99f"') and not protocol.is_request("irgendwas"),
            protocol.request_command,
        )


def cmd_selftest(args, cfg: Config) -> int:
    protocols = (
        protocol_module.all_protocols() if args.all else [cfg.protocol]
    )
    failures = []
    for protocol in protocols:
        _selftest_protocol(protocol, cfg.printer.full_ring_at_100, failures)

    print()
    if failures:
        print("%d Pruefung(en) fehlgeschlagen:" % len(failures))
        for name in failures:
            print("  - %s" % name)
        return 1
    print("alle Pruefungen bestanden")
    return 0


# ---------------------------------------------------------------- Einstiegspunkt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blv-neopixel",
        description="Middleware zwischen Moonraker/Klipper und dem "
        "NeoPixelBLVmgn-Sketch (V3.0 oder V4.x) auf dem Arduino.",
    )
    parser.add_argument("--config", default="", help="Pfad zur Konfigurationsdatei")
    parser.add_argument(
        "--sketch-version",
        default="",
        metavar="VERSION",
        help="Sketch-Version fuer diesen Aufruf: 3.0 oder 4.1 (alt/neu erlaubt)",
    )
    parser.add_argument("--log-level", default="", help="debug, info, warning, error")
    parser.add_argument("--version", action="version", version="blv-neopixel %s" % __version__)
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Dienst starten (Standard)")
    run.add_argument("--dry-run", action="store_true", help="nichts auf die serielle Leitung schreiben")
    run.set_defaults(func=cmd_run)

    dump = subparsers.add_parser("dump", help="Moonraker lesen und Rahmen nur anzeigen")
    dump.add_argument("--preview", action="store_true", help="Ringfarben im Terminal zeigen")
    dump.set_defaults(func=cmd_dump)

    simulate = subparsers.add_parser(
        "simulate", help="alle Anzeigezustaende durchspielen (ohne Klipper)"
    )
    simulate.add_argument("--speed", type=float, default=1.0, help="Zeitraffer, Standard 1.0")
    simulate.add_argument("--once", action="store_true", help="nur ein Durchlauf")
    simulate.add_argument("--preview", action="store_true", help="Ringfarben im Terminal zeigen")
    simulate.add_argument("--dry-run", action="store_true", help="nicht auf die serielle Leitung schreiben")
    simulate.set_defaults(func=cmd_simulate)

    frame = subparsers.add_parser("frame", help="einen einzelnen Rahmen erzeugen")
    frame.add_argument("--status", type=parse_status, default=PrinterStatus.IDLE)
    frame.add_argument("--progress", type=float, default=0.0)
    frame.add_argument(
        "--heater",
        type=parse_heater,
        action="append",
        default=[],
        metavar="IDX:IST[/SOLL]",
        help="z. B. --heater 0:60.1/60 --heater 1:234/235",
    )
    frame.add_argument("--preview", action="store_true", help="Ringfarben im Terminal zeigen")
    frame.set_defaults(func=cmd_frame)

    ports = subparsers.add_parser("ports", help="serielle Schnittstellen auflisten")
    ports.set_defaults(func=cmd_ports)

    show = subparsers.add_parser("config", help="wirksame Konfiguration anzeigen")
    show.set_defaults(func=cmd_config)

    selftest = subparsers.add_parser(
        "selftest", help="Rahmen gegen das nachgebaute Sketch-Verhalten pruefen"
    )
    selftest.add_argument(
        "--all", action="store_true", help="beide Sketch-Versionen pruefen"
    )
    selftest.set_defaults(func=cmd_selftest)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(list(argv or sys.argv[1:]) + ["run"])

    try:
        cfg = config_module.load(args.config)
        if args.sketch_version:
            cfg.printer.sketch_version = protocol_module.normalize(args.sketch_version)
    except (ConfigError, protocol_module.UnknownVersion) as exc:
        print("Konfigurationsfehler: %s" % exc, file=sys.stderr)
        return 2

    setup_logging(args.log_level or cfg.log_level)
    return args.func(args, cfg)
