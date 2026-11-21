# Changelog

Alle nennenswerten Aenderungen stehen hier. Das Format folgt
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/), die Versionen
[Semantic Versioning](https://semver.org/lang/de/):

* **Major** - inkompatible Aenderung der Konfiguration oder des Verhaltens
* **Minor** - neue Funktion, rueckwaertskompatibel
* **Patch** - Fehlerbehebung oder reine Doku-Aenderung

Neue Eintraege kommen unter `[Unreleased]`. `scripts/release.py` macht daraus
beim Release einen datierten Abschnitt (siehe README, Abschnitt "Releases").

## [Unreleased]

### Doku
- README: Drucker-SSR ueber einen GPIO (GPIO27) schalten. Die Firmware setzt
  den Pin beim Booten auf HIGH (`gpio=27=op,dh`), ein
  systemd-shutdown-Hook setzt ihn am Ende des Herunterfahrens auf LOW. Damit
  darf ein USB-HAT seinen Strom direkt von der GPIO-Leiste bekommen, ohne das
  Abschalten des Druckers zu verhindern. Dazu Verdrahtung, Pin-Wahl,
  Testablauf und Hinweise zur Stromversorgung des Hubs.

## [1.2.0] - 2026-11-21

### Hinzugefuegt
- Releaseverwaltung: dieses CHANGELOG, `scripts/release.py` (Version, Tag und
  GitHub-Release in einem Schritt) und Tests, die Version und CHANGELOG
  abgleichen.
- `--version` und die erste Zeile im Dienst-Log zeigen neben der Version auch
  den genauen Git-Stand (z. B. `1.1.0 (v1.1.0-3-g1983b41)`), damit auf dem Pi
  erkennbar ist, welcher Stand laeuft.
- `install.sh` setzt die `origin`-URL des Klons automatisch in den
  vorgeschlagenen `[update_manager]`-Block ein.

### Geaendert
- `install.sh` traegt die serielle Schnittstelle nur noch ein, wenn unter
  `/dev/serial/by-id/` genau ein USB-Seriell-Adapter (CH340, FTDI, CP210x,
  Prolific) steht. Arduino-, Klipper- und Probe-Geraete zaehlen nicht mit,
  weil das an einem Drucker meist die MCUs sind. Bei null oder mehreren
  Treffern bleibt `port` leer, die vorhandenen Geraete werden aufgelistet.
- `install.sh` startet den Dienst nicht, solange `[serial] port` auf kein
  vorhandenes Geraet zeigt.
- Beispielkonfiguration: realer Beispielpfad fuer den Port.

### Behoben
- Der von `install.sh` ausgegebene `[update_manager]`-Block enthielt kein
  `primary_branch: main`; Moonraker nimmt sonst `master` an.
- Befehle in README, `install.sh`-Ausgabe und Konfiguration nannten
  `python -m blv_neopixel` ohne Wechsel in den Projektordner und fuehrten zu
  `No module named blv_neopixel`. `install.sh` wechselt jetzt selbst dorthin.

### Doku
- README: Abschnitt "Strom und USB-Hub am Pi" (ein ueber die GPIO-Leiste
  versorgter Hub hebelt das Abschalten der USB-Ports beim Herunterfahren aus),
  neue Zeilen in der Fehlersuche, Beschreibung der Port-Erkennung.

## [1.1.0] - 2026-11-18

Erste Version.

### Hinzugefuegt
- Middleware zwischen Moonraker/Klipper und dem Arduino-Sketch
  [NeoPixelBLVmgn](https://github.com/mule1972/NeoPixelBLVmgn): liest den
  Druckerzustand ueber die Moonraker-Websocket-API und schickt ihn im
  Duet-Protokoll an den Arduino.
- Beide Sketch-Generationen, umschaltbar ueber `[printer] sketch_version`:
  V4.0/V4.1 (Objektmodell, Standard; beantwortet die `M409`-Abfragen des
  Sketches) und V3.0 (flaches M408-JSON).
- Rahmenpruefung vor jedem Senden gegen die Parser-Eigenheiten beider Sketches
  (`duet.py`, `duet_om.py`).
- Nachbau beider Sketch-Parser samt LED-Logik (`sketchmodel.py`) fuer
  `selftest` und `--preview`.
- Serielle Ausgabe ohne Arduino-Reset (DTR), Wiederverbindung bei Moonraker-
  und Port-Verlust, Sendetakt aus Abfrage, Zustandsaenderung und Heartbeat.
- Kommandozeile: `run`, `config`, `ports`, `selftest`, `simulate`, `frame`,
  `dump`.
- systemd-Dienst, `install.sh`, kommentierte Beispielkonfiguration,
  Dokumentation zu beiden Protokollen und den Arduino-Einstellungen.
- 122 Unit-Tests ohne Fremdpakete und ein Integrationstest mit
  Fake-Moonraker.
