"""Auswahl des Sketch-Protokolls: V3.0 (M408) oder V4.x (Objektmodell).

Beide Sketch-Generationen sprechen unterschiedliche Dialekte:

* **V3.0** - flaches M408-JSON der RepRapFirmware 2.x, Statusbuchstaben,
  Heater als Zahlenlisten. Der Sketch hoert nur zu und fragt nichts ab.
* **V4.0/V4.1** - Objektmodell der RepRapFirmware 3, Statusworte, Heater als
  Objekte, Fortschritt ueber Zeiten. Der Sketch fragt den Zustand selbst mit
  ``M409 F"d99f"`` ab (alle 1 s im Betrieb, alle 5 s im Leerlauf).

Die Anzeigelogik auf den Ringen ist in beiden Versionen gleich - nur der Weg
der Daten unterscheidet sich.
"""

from __future__ import annotations

from . import duet, duet_om, sketchmodel

#: Kanonische Versionsnamen
V30 = "3.0"
V41 = "4.1"

#: Schreibweisen, die in der Konfiguration erlaubt sind
ALIASES = {
    "3": V30,
    "3.0": V30,
    "v3": V30,
    "v3.0": V30,
    "alt": V30,
    "old": V30,
    "4": V41,
    "4.0": V41,
    "4.1": V41,
    "v4": V41,
    "v4.0": V41,
    "v4.1": V41,
    "neu": V41,
    "new": V41,
}


class UnknownVersion(ValueError):
    """Unbekannte Sketch-Version."""


def normalize(raw: str) -> str:
    """``"V4.1"``, ``"neu"``, ``"4"`` ... auf einen kanonischen Namen bringen."""
    key = str(raw or "").strip().lower()
    if key in ALIASES:
        return ALIASES[key]
    raise UnknownVersion(
        "unbekannte Sketch-Version %r (erlaubt: 3.0, 4.1 - oder alt/neu)" % raw
    )


class Protocol:
    """Gemeinsame Schnittstelle fuer beide Sketch-Generationen."""

    version = ""
    label = ""
    #: Fragt der Sketch selbst nach Daten?
    polls = False
    #: Kommando, mit dem er fragt (nur zur Information)
    request_command = ""
    #: Laesst sich der Fortschrittsring bei 100 % komplett fuellen?
    supports_full_ring = False
    max_frame_bytes = 0
    frame_error = ValueError

    def build_frame(self, state, full_ring: bool = True, validate: bool = True) -> str:
        raise NotImplementedError

    def validate_frame(self, frame: str) -> None:
        raise NotImplementedError

    def parse(self, frame: str, printer=None):
        """Rahmen so auswerten, wie der Sketch es tun wuerde."""
        raise NotImplementedError

    def is_request(self, line: str) -> bool:
        """Ist diese Zeile vom Arduino eine Statusabfrage?"""
        return False

    def __str__(self) -> str:
        return self.label


class ProtocolV30(Protocol):
    """NeoPixelBLVmgn V3.0 - M408, reiner Zuhoerer."""

    version = V30
    label = "V3.0 (M408, der Sketch hoert nur zu)"
    polls = False
    supports_full_ring = True
    max_frame_bytes = duet.MAX_FRAME_BYTES
    frame_error = duet.FrameError

    def build_frame(self, state, full_ring: bool = True, validate: bool = True) -> str:
        return duet.build_frame(state, full_ring=full_ring, validate=validate)

    def validate_frame(self, frame: str) -> None:
        duet.validate_frame(frame)

    def parse(self, frame: str, printer=None):
        return sketchmodel.feed(frame, printer)


class ProtocolV41(Protocol):
    """NeoPixelBLVmgn V4.0/V4.1 - Objektmodell, fragt selbst ab."""

    version = V41
    label = 'V4.x (Objektmodell, fragt mit M409 F"d99f" selbst ab)'
    polls = True
    request_command = 'M409 F"d99f"'
    #: Der Fortschritt wird aus Zeiten errechnet und bleibt immer < 1.0
    supports_full_ring = False
    max_frame_bytes = duet_om.MAX_FRAME_BYTES
    frame_error = duet_om.FrameError

    def build_frame(self, state, full_ring: bool = True, validate: bool = True) -> str:
        return duet_om.build_frame(state, full_ring=full_ring, validate=validate)

    def validate_frame(self, frame: str) -> None:
        duet_om.validate_frame(frame)

    def parse(self, frame: str, printer=None):
        return sketchmodel.feed_v4(frame, printer)

    def is_request(self, line: str) -> bool:
        return line.strip().upper().startswith("M409")


_PROTOCOLS = {V30: ProtocolV30(), V41: ProtocolV41()}


def get(version: str) -> Protocol:
    """Protokoll zu einer Versionsangabe holen."""
    return _PROTOCOLS[normalize(version)]


def all_protocols() -> list:
    return [_PROTOCOLS[V30], _PROTOCOLS[V41]]


def versions() -> list:
    return [V30, V41]
