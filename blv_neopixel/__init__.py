"""Middleware zwischen Moonraker/Klipper und NeoPixelBLVmgn.

Der Arduino-Sketch erwartet das JSON, das eine RepRapFirmware am PanelDue-Port
ausgibt - je nach Sketch-Generation in zwei Dialekten:

* **V3.0** - flaches M408-JSON der RepRapFirmware 2.x
* **V4.0/V4.1** - Objektmodell der RepRapFirmware 3

Dieses Paket uebersetzt den Klipper-Zustand in das jeweilige Format
(umschaltbar ueber ``[printer] sketch_version``) und schickt es ueber die
serielle Schnittstelle an den Arduino.
"""

__version__ = "1.2.2"

#: Sketch-Versionen, die diese Middleware bedienen kann
SKETCH_VERSIONS = ("V3.0", "V4.0/V4.1")

import os
import subprocess


def describe() -> str:
    """Version, in einem Git-Klon zusaetzlich der genaue Stand.

    Beispiel: ``1.2.0 (v1.2.0)`` auf einem getaggten Release,
    ``1.2.0 (v1.2.0-3-g1983b41)`` drei Commits danach, ``-dirty`` bei
    ungespeicherten Aenderungen. Ohne Git (oder ausserhalb eines Klons) kommt
    nur die Versionsnummer zurueck.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=os.path.dirname(here),
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return __version__
    stand = result.stdout.strip()
    if result.returncode != 0 or not stand:
        return __version__
    return "%s (%s)" % (__version__, stand)


__all__ = ["__version__", "SKETCH_VERSIONS", "describe"]
