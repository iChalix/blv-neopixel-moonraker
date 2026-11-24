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

Die serielle Schnittstelle traegt das Skript nur ein, wenn **genau ein**
USB-Seriell-Adapter (CH340, FTDI, CP210x, Prolific) angeschlossen ist.
Gezaehlt werden die Schnittstellen selbst, nicht die Namen unter
`/dev/serial/by-id/` - zwei CH340 teilen sich dort einen. Arduino-, Klipper- und Probe-Eintraege (z. B.
`usb-Arduino__www.arduino.cc__0042_...`, `usb-Cartographer_...`) zaehlen
bewusst nicht mit: An einem Drucker sind das meist die MCUs, und dorthin
darf die Middleware nichts senden. Gibt es null oder mehrere Treffer, bleibt
`port` leer, das Skript listet die vorhandenen Geraete auf, und der Dienst
wird installiert, aber **nicht gestartet**, bis `port` auf ein vorhandenes
Geraet zeigt. Wer den Dienst erst nach einer Pruefung der Konfiguration
installieren will, startet mit `./install.sh --no-service`.

**Zwei CH340 am selben Pi.** CH340-Nachbauten haben keine Seriennummer und
bekommen unter `/dev/serial/by-id/` alle denselben Namen; sichtbar bleibt nur
einer, und das kann nach jedem Neustart ein anderer sein. Dann fuer **beide**
Geraete den Pfad unter `/dev/serial/by-path/` eintragen und jedes an seiner
USB-Buchse lassen:

```bash
ls -l /dev/serial/by-path/
```

Einen schon laufenden Dienst umstellen, **bevor** der zweite Adapter
angesteckt wird - sonst schreibt die Middleware womoeglich in das fremde
Geraet. Gibt es zwei Eintraege fuer dieselbe Buchse (`...-usb-...` und
`...-usbv2-...`), den mit `-usb-` nehmen.

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

Alle Befehle mit dem venv-Python, **im Projektordner** ausgefuehrt - das Paket
`blv_neopixel` ist nicht ins venv installiert und wird nur dort gefunden
(sonst: `No module named blv_neopixel`):

```bash
cd ~/blv-neopixel-moonraker
~/blv-neopixel-env/bin/python -m blv_neopixel <befehl>
```

Die Konfiguration findet das Programm selbst, zuerst unter
`~/printer_data/config/blv-neopixel.conf`; mit `--config` laesst sie sich
ueberschreiben.

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
cd ~/blv-neopixel-moonraker
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
| Nach Neustart falscher Port | Pfad unter `/dev/serial/by-id/` verwenden statt `/dev/ttyUSB0`, bei zwei CH340 den unter `/dev/serial/by-path/`. |
| Zwei CH340 angeschlossen, unter `/dev/serial/by-id/` steht nur einer | CH340 haben keine Seriennummer und teilen sich den Namen. Fuer beide `/dev/serial/by-path/` verwenden (siehe Installation). |
| `install.sh` meldet "Port nicht automatisch gesetzt" | Kein oder kein eindeutiger USB-Seriell-Adapter gefunden. Pfad des NeoPixel-Arduino selbst unter `[serial] port` eintragen, nicht den eines Drucker-MCUs. |
| Arduino taucht am Pi nicht auf (`lsusb`, `ls /dev/serial/by-id/`) | Kabel ohne Datenleitungen, kein freier USB-Port oder der Hub hat keine Datenverbindung zum Pi. Bei `sudo dmesg -w` beim Anstecken muss ein neues Geraet erscheinen. |
| Drucker geht beim Herunterfahren des Pi nicht aus (SSR bleibt an) | Siehe "Strom und USB-Hub am Pi" unten - empfohlen: SSR ueber GPIO27 schalten. |

### Strom und USB-Hub am Pi

**Das Problem.** An einem Pi 3B+ waren die 5 V der USB-Ports nach dem
Herunterfahren abgeschaltet (so beobachtet). Haengt der Drucker-Strom an
einem SSR, das ueber diese Spannung gehalten wird (etwa ueber die Arduinos an
den USB-Ports), nutzt man genau dieses Verhalten. Das geht kaputt, sobald ein
**USB-Hub an der GPIO-Leiste mit Strom versorgt wird** und zugleich per
USB-Kabel am Pi haengt: Die GPIO-5-V liegen immer an, und die 5-V-Leitung im
Upstream-Kabel verbindet sie mit der geschalteten Schiene der USB-Ports. Alle
Ports bleiben dann nach dem Herunterfahren versorgt, auch die Geraete, die
das SSR halten. Beobachtet an einem Pi 3B+ mit einem USB-HAT
(`USB_HUB_HAT_V1.4`); nach dem Trennen der GPIO-Versorgung des HAT war das
Abschalten wieder in Ordnung.

#### Empfohlen: das Drucker-SSR ueber einen GPIO schalten

Das SSR haengt dann nicht mehr an der USB-Spannung, und der Hub darf seinen
Strom direkt von der GPIO-Leiste bekommen. Die Firmware setzt den Pin beim
Booten auf HIGH, ein Hook am Ende des Herunterfahrens auf LOW:

| Zustand | GPIO27 | Drucker |
|---|---|---|
| Pi bootet (vor der Firmware-Zeile) | LOW | aus |
| Pi laeuft | HIGH | an |
| nach `shutdown -h now` | LOW (Hook) | aus |
| Neustart | kurz LOW (Reset) | kurz aus |

Getestet mit einem Pi 3B+ und einem Fotek SSR-25 DA (Steuereingang 3-32 VDC).

**Pin waehlen.** GPIO 9 bis 27 starten nach einem Reset als Eingang mit
Pull-down, also LOW - geeignet. GPIO 0 bis 8 starten mit Pull-up und sind
ungeeignet. Hier: **GPIO27 (Pin 13)**, GND auf Pin 14. Vorher pruefen, dass
nichts anderes den Pin nutzt: `pinctrl get 27` soll `ip pd` zeigen.

**Verdrahtung** (Drucker vorher vom Netz trennen, nur am Steuereingang
arbeiten):

* direkt: GPIO27 an SSR `3 (+)`, SSR `4 (-)` an GND. 3,3 V liegen im
  Steuerbereich solcher SSRs; ob es zuverlaessig schaltet, zeigt die LED am
  SSR. Nachbauten schalten an der Untergrenze nicht immer sicher.
* mit Transistor, wenn das SSR bei 3,3 V nicht sicher schaltet: SSR `3 (+)`
  an 5 V (Pin 2), SSR `4 (-)` an den Kollektor eines NPN (z. B. BC337),
  Emitter an GND, Basis ueber 1 kOhm an GPIO27, 10 kOhm von der Basis nach
  GND. Nur moeglich, wenn `4 (-)` nicht mit anderen SSRs verbunden ist.

Die bisherigen Steuerleitungen am SSR abklemmen - es darf nur eine Quelle
geben.

**Einschalten beim Booten** - in `/boot/firmware/config.txt`, unter `[all]`:

```ini
gpio=27=op,dh
```

**Ausschalten am Ende des Herunterfahrens** - systemd fuehrt alles in
`/usr/lib/systemd/system-shutdown/` erst aus, wenn alle Dienste (auch Klipper
und Moonraker) beendet sind, und uebergibt `poweroff`, `halt`, `reboot` oder
`kexec`. Der Ordner existiert nicht immer:

```bash
sudo mkdir -p /usr/lib/systemd/system-shutdown
sudo tee /usr/lib/systemd/system-shutdown/printer-power-off >/dev/null <<'EOF'
#!/bin/sh
# Drucker-SSR an GPIO27 am Ende des Herunterfahrens abschalten.
# systemd-shutdown uebergibt: poweroff, halt, reboot oder kexec.
case "$1" in
    poweroff|halt) /usr/bin/pinctrl set 27 op dl ;;
esac
EOF
sudo chmod +x /usr/lib/systemd/system-shutdown/printer-power-off
```

`pinctrl` muss unter `/usr/bin` liegen (`command -v pinctrl`), sonst den Pfad
im Hook anpassen; es kommt aus dem Paket `raspi-utils`.

**Testen**, am besten zuerst mit dem Multimeter an Pin 13/14 statt mit dem
SSR:

```bash
pinctrl get 27                                                     # nach dem Neustart: op ... | hi
sudo /usr/lib/systemd/system-shutdown/printer-power-off reboot && pinctrl get 27   # bleibt hi
sudo /usr/lib/systemd/system-shutdown/printer-power-off halt && pinctrl get 27     # jetzt lo
pinctrl set 27 op dh                                               # wieder einschalten
sudo shutdown -h now                                               # am Ende: 0 V bzw. LED aus
```

Hinweise:

* **Nicht** `gpioget 27` zum Pruefen verwenden - das stellt den Pin auf
  Eingang um und schaltet den Drucker ab.
* Das Firmware-Overlay `gpio-poweroff` ist dafuer nicht gedacht: Es soll die
  Stromversorgung des Pi selbst abschalten, pulst den Pin beim Ausschalten
  (an-aus-an) und warnt vor undefiniertem Verhalten, wenn der Pi danach weiter
  Strom hat.
* Wer den Drucker zusaetzlich in Mainsail schalten will, kann in
  `moonraker.conf` einen `[power printer]`-Block mit `type: gpio`,
  `pin: gpiochip0/gpio27` und **`initial_state: on`** anlegen. Ohne
  `initial_state: on` uebernimmt Moonraker den Pin beim Start vermutlich mit
  LOW. Das endgueltige Abschalten nach dem Herunterfahren bleibt Sache des
  Hooks.
* `config.txt` und der Hook liegen ausserhalb dieses Repos und muessen nach
  einer Neuinstallation des Pi neu eingerichtet werden.

#### Den Hub mit Strom versorgen

* **Ueber die GPIO-Leiste** (so ist ein USB-HAT gebaut): Dann zusaetzlich im
  Upstream-Kabel zum Pi die 5-V-Leitung (VBUS) unterbrechen, etwa den
  VBUS-Kontakt am USB-A-Stecker abkleben (mit dem Multimeter pruefen: Er hat
  Durchgang zu Pin 2 des Pi). Sonst umgeht der Hub die Strombegrenzung der
  USB-Ports des Pi. Danach mit `lsusb` pruefen, dass der Hub noch erscheint.
* **Nur ueber das USB-Kabel**: Wer das SSR weiter ueber die USB-Spannung
  haelt, darf den Hub nicht ueber die GPIO-Leiste versorgen - z. B. eine
  2x20-GPIO-Verlaengerung mit abgeknipsten Pins 2 und 4 zwischen Pi und HAT
  setzen. Der Hub geht dann beim Herunterfahren mit aus.

Pi und Hub haengen in beiden Faellen am selben Netzteil. Ein Pi 3B+ zieht
selbst 0,5-1 A, das offizielle Netzteil liefert 2,5 A. Bei vielen Geraeten am
Hub ein staerkeres 5-V-Netzteil verwenden und dann Pi und HAT gemeinsam ueber
die GPIO-Leiste speisen - nie zwei Netzteile an derselben 5-V-Schiene. Im
Betrieb `vcgencmd get_throttled` kontrollieren: `0x0` ist gut, alles andere
heisst Unterspannung. NeoPixel-Ringe gehoeren an ein eigenes 5-V-Netzteil.

Ein Probe wie der Cartographer hinter einem einfachen Hub kann zu Aussetzern
fuehren - nach dem Umstecken einen kompletten Mesh-/Probe-Durchlauf testen.

`selftest --all` laeuft ohne Drucker und Arduino und prueft die Rahmen beider
Versionen gegen das nachgebaute Sketch-Verhalten - der erste Schritt, wenn
unklar ist, ob die Middleware oder die Hardware das Problem ist.

## Entwicklung

```bash
python3 -m unittest discover -s tests
```

159 Tests, keine Abhaengigkeiten ausser der Standardbibliothek: Rahmenformat
und Parserregeln beider Versionen, die nachgebauten Sketch-Parser samt
LED-Ausgabe, die Abbildung der Klipper-Zustaende, die Moonraker-Sitzung, das
Laden der Konfiguration, die Adaptererkennung in `install.sh` und der Abgleich
von Version und CHANGELOG.

Dazu ein Integrationstest, der einen Fake-Moonraker startet, ueber ein pty
mitliest, was der Arduino bekommen haette, bei V4.x auch den fragenden
Arduino spielt und einen Klipper-Neustart durchlaeuft - ohne Drucker und ohne
Arduino:

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

## Releases

Versionen folgen [Semantic Versioning](https://semver.org/lang/de/) und sind
als Git-Tags `vX.Y.Z` und als GitHub-Releases veroeffentlicht. Die einzige
Versionsquelle ist `__version__` in `blv_neopixel/__init__.py`; was sich
geaendert hat, steht im [CHANGELOG](CHANGELOG.md).

**Welcher Stand laeuft?** `--version` und die erste Zeile im Dienst-Log
nennen die Version und den genauen Git-Stand:

```bash
cd ~/blv-neopixel-moonraker && ~/blv-neopixel-env/bin/python -m blv_neopixel --version
```

```
blv-neopixel 1.2.0 (v1.2.0)            # genau der Release
blv-neopixel 1.2.0 (v1.2.0-3-g1983b41) # drei Commits nach dem Release
```

**Einen Release schneiden** (im Entwicklungsklon, auf `main`):

1. Waehrend der Arbeit Aenderungen unter `## [Unreleased]` im CHANGELOG
   eintragen (Hinzugefuegt, Geaendert, Behoben, Doku).
2. Vorschau: `python3 scripts/release.py minor --dry-run` (oder `patch`,
   `major`, oder eine feste Version wie `1.3.0`). Das zeigt die Release-Notizen
   und aendert nichts.
3. Release: `python3 scripts/release.py minor`.

Das Skript prueft vorher, dass der Arbeitsordner sauber ist, der Branch `main`
heisst, `origin` nicht weiter ist als lokal, der Tag noch nicht existiert,
`__version__` und CHANGELOG zusammenpassen und alle Tests durchlaufen. Dann
macht es aus `[Unreleased]` einen datierten Abschnitt, setzt `__version__`,
committet `Release vX.Y.Z`, legt einen annotierten Tag an, pusht `main` und
den Tag und erstellt mit `gh` das GitHub-Release mit den CHANGELOG-Eintraegen.
Vor dem Veroeffentlichen fragt es nach. Mit `--no-push` bleibt alles lokal,
mit `--no-github` entfaellt das GitHub-Release.

Ein leeres `[Unreleased]` verhindert den Release: Ohne Eintraege gibt es
nichts zu veroeffentlichen.

## Lizenz

Dieses Projekt: MIT. Der Arduino-Sketch NeoPixelBLVmgn hat seine eigene
Lizenz (GPL-3.0) und ist hier nicht enthalten.
