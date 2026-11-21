# BLV NeoPixel Middleware (Moonraker -> NeoPixelBLVmgn)

Middleware fuer den BLV mgn Cube mit Klipper: sie liest den Druckerzustand von
Moonraker, uebersetzt ihn in das Duet-Protokoll, das der Arduino-Sketch
[NeoPixelBLVmgn](https://github.com/mule1972/NeoPixelBLVmgn) erwartet, und
schickt ihn ueber die serielle Schnittstelle an den Arduino. Die drei
NeoPixel-Ringe zeigen damit Bett, Hotend und Druckerstatus/-fortschritt, ohne
dass am Sketch etwas geaendert werden muss.

**Beide Sketch-Generationen werden unterstuetzt** und sind in der
Konfiguration umschaltbar:

| `sketch_version` | Sketch | Protokoll | Besonderheit |
|------------------|--------|-----------|--------------|
| `4.1` (Standard) | Release V4.0/V4.1 | Objektmodell (RRF 3) | Sketch fragt mit `M409 F"d99f"` selbst ab |
| `3.0` | Release V3.0 | flaches M408-JSON (RRF 2.x) | Sketch hoert nur zu |

```
Klipper ── Moonraker ──websocket──> blv-neopixel ──serial 57600──> Arduino ──> 3x16 NeoPixel
                                   (dieses Projekt)              (V3.0 oder V4.x)
```

## Warum ueberhaupt eine Middleware?

Beide Sketch-Versionen sind fuer einen **Duet3D mit PanelDue** gebaut: Der
Arduino hoert am PanelDue-Port mit und wertet das JSON aus, das die
RepRapFirmware dort ausgibt. Klipper spricht dieses Protokoll nicht - es gibt
keinen PanelDue-Port, kein M408 und kein M409. Die Middleware nimmt die Rolle
der RepRapFirmware ein und erzeugt genau die Nachrichten, die der jeweilige
Sketch lesen kann:

```json
V3.0: {"status":"P","heaters":[60.1,234.0],"active":[60.0,235.0],"standby":[0.0,0.0],"hstat":[2,2],"fraction_printed":0.42}
V4.x: {"state":{"status":"processing"},"heaters":[{"current":60.1,"active":60.0,"standby":0.0,"state":"active"}],"timesLeft":{"slicer":580},"job":{"duration":420,"warmUpDuration":0}}
```

Beide Parser haben Eigenheiten, an denen man sich sonst die Zaehne ausbeisst -
V3.0 sucht Schluessel mit `strstr()` und kann einelementige Arrays nicht
indiziert lesen, V4.x verwirft `job.duration`, wenn `timesLeft` dahinter
steht, und schneidet Werte ueber 31 Zeichen ab. Diese Regeln setzen
[`duet.py`](blv_neopixel/duet.py) und [`duet_om.py`](blv_neopixel/duet_om.py)
durch und pruefen sie vor jedem Senden. Details:
[docs/protokoll.md](docs/protokoll.md).

## Installation auf dem Raspberry Pi

```bash
git clone <diese-repo-url> ~/BLV-NeoPixel-Moonraker-Plugin
cd ~/BLV-NeoPixel-Moonraker-Plugin
./install.sh
```

Das Skript legt ein venv unter `~/blv-neopixel-env` an, installiert
`websockets` und `pyserial`, kopiert die Konfiguration nach
`~/printer_data/config/blv-neopixel.conf`, richtet den systemd-Dienst
`blv-neopixel` ein und startet ihn. Benoetigt werden Python 3.8 oder neuer und
die Gruppe `dialout`.

Die serielle Schnittstelle traegt das Skript nur ein, wenn unter
`/dev/serial/by-id/` **genau ein** USB-Seriell-Adapter (CH340, FTDI, CP210x,
Prolific) steht. Arduino-, Klipper- und Probe-Eintraege (z. B.
`usb-Arduino__www.arduino.cc__0042_...`, `usb-Cartographer_...`) zaehlen
bewusst nicht mit: An einem Drucker sind das meist die MCUs, und dorthin
darf die Middleware nichts senden. Gibt es null oder mehrere Treffer, bleibt
`port` leer, das Skript listet die vorhandenen Geraete auf, und der Dienst
wird installiert, aber **nicht gestartet**, bis `port` auf ein vorhandenes
Geraet zeigt. Wer den Dienst erst nach einer Pruefung der Konfiguration
installieren will, startet mit `./install.sh --no-service`.

Die Arduino-Seite - Sketch-Einstellungen, Verdrahtung, Pegel - steht je
Version in [arduino/V3.0-Einstellungen.md](arduino/V3.0-Einstellungen.md) und
[arduino/V4.x-Einstellungen.md](arduino/V4.x-Einstellungen.md). Kurz: Arduino
per USB an den Pi, Sketch unveraendert flashen - die Werkseinstellungen passen
in beiden Versionen zu drei Ringen mit je 16 LEDs an den Pins 7, 6 und 8.

## Konfiguration

Vollstaendig kommentiert in [config/blv-neopixel.conf](config/blv-neopixel.conf).
Das Wichtigste:

```ini
[serial]
port = /dev/serial/by-id/usb-1a86_USB2.0-Serial-if00-port0

[printer]
sketch_version = 4.1      # oder 3.0 - "alt"/"neu" geht auch

[heater 0]
object = heater_bed

[heater 1]
object = extruder
```

`[heater N]` ordnet Klipper-Objekte den PrinterObjects des Sketches zu:
`0` ist das Heizbett, `1` das erste Hotend, bis maximal `4`. Erlaubt ist
jedes Klipper-Objekt mit `temperature` (und optional `target`), also auch
`heater_generic chamber` oder `temperature_sensor raspberry_pi`.

Welche Version laeuft, zeigt `config`:

```
  Sketch    : V4.x (Objektmodell, fragt mit M409 F"d99f" selbst ab)
```

Zum Ausprobieren ohne Aenderung der Datei: `--sketch-version 3.0` vor dem
Unterbefehl.

## Bedienung

Alle Befehle mit dem venv-Python, z. B.
`~/blv-neopixel-env/bin/python -m blv_neopixel <befehl>`:

| Befehl | Zweck |
|--------|-------|
| `run` | Dienst starten (Standard, macht sonst systemd) |
| `config` | wirksame Konfiguration und Hinweise anzeigen |
| `ports` | serielle Schnittstellen auflisten |
| `selftest` | Rahmen gegen das nachgebaute Sketch pruefen, ohne Hardware |
| `selftest --all` | dasselbe fuer **beide** Sketch-Versionen |
| `simulate --preview` | alle Anzeigezustaende durchspielen, Ringe im Terminal |
| `frame --status P --progress 0.5 --heater 0:60/60 --preview` | einen einzelnen Rahmen erzeugen und anzeigen |
| `dump --preview` | echten Moonraker-Zustand lesen, nichts senden |

Globale Optionen: `--config PATH`, `--sketch-version 3.0|4.1`,
`--log-level debug`.

`--preview` rendert, welche Farbe der Sketch jeder LED geben wuerde -
nachgebaut aus `ConvertPosition2PixelIndex()` und der Anzeigelogik, die in
beiden Versionen gleich ist (siehe
[blv_neopixel/sketchmodel.py](blv_neopixel/sketchmodel.py)).

Zum Ausprobieren der Ringe den Dienst kurz anhalten, damit die Schnittstelle
frei ist:

```bash
sudo systemctl stop blv-neopixel
~/blv-neopixel-env/bin/python -m blv_neopixel simulate --preview
sudo systemctl start blv-neopixel
```

## Was angezeigt wird

| Klipper | Sketch-Status | Ring 99 |
|---------|---------------|---------|
| `print_stats.state = printing` | `P` | Fortschrittsbalken |
| Druck fertig (`complete_hold`) | `P` mit 100 % | ganzer Ring gruen (nur V3.0, siehe unten) |
| `paused` | `A` | violett |
| PAUSE laeuft / RESUME laeuft | `R` / `B` | violett / gelb |
| Makro, Homing, manuelles Heizen | `D` | gelb |
| `standby`, `cancelled` (nach Haltezeit) | `I` | weiss |
| Klipper-Shutdown oder -Fehler | `S` | blau |
| Klipper startet, Middleware wartet auf Moonraker | `C` | gelb |
| Moonraker nicht erreichbar (nach erster Verbindung) | `S` (einstellbar) | blau |

Heizungsringe: Ist-Temperatur in `ColorHeatUpDone`, der Weg zum Sollwert in
`ColorHeatUp`, Nachwaerme bei abgeschalteter Heizung in `ColorCoolDown`, der
Rest in `ColorIdle` - die Farben kommen aus dem Sketch, nicht von hier.

Ein Detail zum Fortschrittsring: Der Sketch vergleicht
`LED-Nummer < fraction * LEDs`, die letzte LED schaltet also erst bei
`fraction > 1` um. Bei V3.0 sendet die Middleware dafuer `1.01`
(`full_ring_at_100`). Bei V4.x rechnet der Sketch den Wert selbst aus Zeiten
aus und kommt nie ueber 1.0 - dort bleibt bei 100 % eine LED auf der
Druckfarbe. `config` weist darauf hin.

## Fehlersuche

```bash
journalctl -u blv-neopixel -f
```

| Symptom | Ursache |
|---------|---------|
| Ringe bleiben dunkel | Der Sketch zeigt erst nach der **ersten** vollstaendigen Nachricht etwas an. Log pruefen: steht dort `serielle Schnittstelle offen`? |
| Ringe bleiben dunkel, Log sieht gut aus | Falsche `sketch_version` - der Sketch versteht das Format nicht. Beide Varianten durchprobieren. |
| Ringe spielen dauernd die Startup-Animation | Der Arduino wird beim Oeffnen der Schnittstelle resettet - `avoid_reset = true` setzen. |
| Temperaturen bleiben auf 0 | Falscher Objektname in `[heater N]`. `dump` zeigt, was ankommt. |
| Status wechselt, Temperaturen nicht | Zu viele Rahmen: `min_interval` erhoehen (der AVR hat nur 64 Byte RX-Puffer). |
| V4.x: keine Zeile `Arduino fragt den Zustand ab` | Die Rueckleitung fehlt (oder Mega-Eigenheit, siehe Arduino-Doku). Unkritisch, der Heartbeat liefert die Daten. |
| `Permission denied` auf dem Port | Benutzer nicht in `dialout`, oder ein anderer Prozess haelt den Port. |
| Nach Neustart falscher Port | Pfad unter `/dev/serial/by-id/` verwenden statt `/dev/ttyUSB0`. |
| `install.sh` meldet "Port nicht automatisch gesetzt" | Kein oder kein eindeutiger USB-Seriell-Adapter gefunden. Pfad des NeoPixel-Arduino selbst unter `[serial] port` eintragen, nicht den eines Drucker-MCUs. |
| Arduino taucht am Pi nicht auf (`lsusb`, `ls /dev/serial/by-id/`) | Kabel ohne Datenleitungen, kein freier USB-Port oder der Hub hat keine Datenverbindung zum Pi. Bei `sudo dmesg -w` beim Anstecken muss ein neues Geraet erscheinen. |
| Pi bleibt nach dem Herunterfahren unter Spannung (z. B. haelt er ein SSR) | Siehe "Strom und USB-Hub am Pi" unten. |

### Strom und USB-Hub am Pi

An einem Pi 3B+ waren die 5 V der USB-Ports nach dem Herunterfahren
abgeschaltet (so beobachtet). Haengt der Drucker-Strom an einem SSR, das
ueber diese Spannung gehalten wird (etwa ueber die Arduinos), nutzt man genau
dieses Verhalten.

Das geht kaputt, wenn ein **USB-Hub an der GPIO-Leiste mit Strom versorgt
wird** und zugleich per USB-Kabel am Pi haengt: Die GPIO-5-V liegen immer an,
und die 5-V-Leitung im Upstream-Kabel verbindet sie mit der geschalteten
Schiene der USB-Ports. Alle Ports bleiben dann nach dem Herunterfahren
versorgt, auch die Geraete am SSR. Beobachtet an einem Pi 3B+; der Hub wurde
nur ueber die GPIO-Pins mit Strom versorgt und nach dem Trennen dieser
Verbindung war das Abschalten wieder in Ordnung.

Abhilfe: den Hub **nicht** ueber die GPIO-Leiste speisen, sondern nur ueber
das USB-Kabel aus der geschalteten Schiene. Dann geht er beim Herunterfahren
zusammen mit allem anderen aus - auch der Nano und die Ringe. Alternativ die
5-V-Leitung im Upstream-Kabel unterbrechen; dann bleibt der Hub (und alles
dahinter) nach dem Herunterfahren unter Spannung.

Nach jedem Umbau pruefen, dass der Drucker beim Herunterfahren des Pi
tatsaechlich abgeschaltet wird, und im Betrieb `vcgencmd get_throttled`
kontrollieren: `0x0` ist gut, alles andere heisst Unterspannung.

Ausserdem: Die 5-V-Leitung eines Hubs allein liefert nur begrenzt Strom
(Pi 3: etwa 1,2 A fuer alle USB-Ports zusammen). Ein Probe wie der
Cartographer hinter einem einfachen Hub kann zu Aussetzern fuehren - nach dem
Umstecken einen kompletten Mesh-/Probe-Durchlauf testen.

`selftest --all` laeuft ohne Drucker und Arduino und prueft die Rahmen beider
Versionen gegen das nachgebaute Sketch-Verhalten - der erste Schritt, wenn
unklar ist, ob die Middleware oder die Hardware das Problem ist.

## Entwicklung

```bash
python3 -m unittest discover -s tests
```

122 Tests, keine Abhaengigkeiten ausser der Standardbibliothek: Rahmenformat
und Parserregeln beider Versionen, die nachgebauten Sketch-Parser samt
LED-Ausgabe, die Abbildung der Klipper-Zustaende und das Laden der
Konfiguration.

Dazu ein Integrationstest, der einen Fake-Moonraker startet, ueber ein pty
mitliest, was der Arduino bekommen haette, und bei V4.x auch den fragenden
Arduino spielt - ohne Drucker und ohne Arduino:

```bash
~/blv-neopixel-env/bin/python tests/integration_moonraker.py        # beide Versionen
~/blv-neopixel-env/bin/python tests/integration_moonraker.py 4.1    # nur V4.x
```

| Modul | Aufgabe |
|-------|---------|
| `protocol.py` | Auswahl zwischen V3.0 und V4.x |
| `duet.py` | V3.0-Rahmen bauen und gegen die M408-Regeln pruefen |
| `duet_om.py` | V4.x-Rahmen bauen und gegen die Objektmodell-Regeln pruefen |
| `state.py` | internes Zustandsmodell (Status, Heater, Fortschritt) |
| `mapper.py` | Moonraker-Objekte -> Zustand |
| `moonraker.py` | Websocket-Client mit Abo und Wiederverbindung |
| `serial_link.py` | serielle Ausgabe, reset-frei und selbstheilend |
| `service.py` | Sendetakt: auf Anfrage, bei Aenderung, als Heartbeat |
| `sketchmodel.py` | beide Sketch-Parser in Python nachgebaut, plus LED-Farben |
| `simulator.py` | erfundener Druckerzustand fuer Tests ohne Drucker |

## Lizenz

Dieses Projekt: MIT. Der Arduino-Sketch NeoPixelBLVmgn hat seine eigene
Lizenz (GPL-3.0) und ist hier nicht enthalten.
