"""Middleware zwischen Moonraker/Klipper und NeoPixelBLVmgn.

Der Arduino-Sketch erwartet das JSON, das eine RepRapFirmware am PanelDue-Port
ausgibt - je nach Sketch-Generation in zwei Dialekten:

* **V3.0** - flaches M408-JSON der RepRapFirmware 2.x
* **V4.0/V4.1** - Objektmodell der RepRapFirmware 3

Dieses Paket uebersetzt den Klipper-Zustand in das jeweilige Format
(umschaltbar ueber ``[printer] sketch_version``) und schickt es ueber die
serielle Schnittstelle an den Arduino.
"""

__version__ = "1.1.0"

#: Sketch-Versionen, die diese Middleware bedienen kann
SKETCH_VERSIONS = ("V3.0", "V4.0/V4.1")

__all__ = ["__version__", "SKETCH_VERSIONS"]
