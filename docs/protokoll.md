# Die Protokolle von NeoPixelBLVmgn

Die Middleware kann beide Sketch-Generationen bedienen, umgeschaltet mit
`[printer] sketch_version`:

| Einstellung | Sketch | Protokoll |
|-------------|--------|-----------|
| `3.0` | Release V3.0 | flaches M408-JSON der RepRapFirmware 2.x |
| `4.1` | Release V4.0/V4.1 | Objektmodell der RepRapFirmware 3 |

Die **Anzeigelogik** ist in beiden Versionen identisch (Heizungsgrafik,
Fortschrittsbalken, LED-Reihenfolge, Farbnamen) - sie ist weiter unten nur
einmal beschrieben. Unterschiedlich sind Transport und Format.

---

# Teil 1: V3.0 (M408)

Grundlage: `NeoPixel_BLV.ino` aus Release V3.0 (ein einzelnes Sketch-File,
ohne `src/json`).

## Transport

* 57600 Baud, 8N1 (`SerialObject.begin(57600)`)
* Auf einem Uno/Nano/Pro Mini (ATmega328P) ist `SerialObject` die
  Hardware-UART - also genau die USB-Schnittstelle.
* V3.0 **sendet nichts**. Es gibt keine Statusabfrage wie das `M409` der
  Version V4.x. Wer Daten will, muss sie unaufgefordert hinschicken.
* `GetSerialMessage()` wartet bis zu 150 ms (`SerialTimeout`) auf Daten und
  wird in jedem `loop()`-Durchlauf aufgerufen. Die Ringe werden alle 200 ms
  neu gezeichnet (`NeopixelRefreshSpeed`), aber nur wenn `UpdatePending`
  gesetzt ist - also nur, wenn vorher eine Nachricht ankam.
* Vor der ersten vollstaendigen Nachricht bleibt die Anzeige nach der
  Startup-Animation stehen (`InitialSerialMessageSuccess`).

Folge fuer die Middleware: regelmaessig senden (Heartbeat), aber nicht zu
schnell - der AVR hat nur 64 Byte RX-Puffer.

## Rahmenerkennung

`GetSerialMessage()` arbeitet zeichenweise:

1. `{` setzt den Puffer zurueck und beginnt eine neue Nachricht.
2. `}` beendet sie **sofort** und ruft `AnalyzeSerialMessage()` auf.
3. `"` wird verworfen - im Puffer landet `{status:I,...}`.
4. Der Puffer ist 550 Byte gross, nutzbar bis `SerialMessageBuffer - 3`.
   Laengere Nachrichten werden stillschweigend abgeschnitten.

Daraus folgt:

* **Genau ein flaches Objekt pro Nachricht.** Ein verschachteltes Objekt
  wuerde die Nachricht beim ersten inneren `}` abbrechen.
* Ein Zeilenende ist nicht noetig, wird aber als Trenner mitgesendet.
* Ein abgeschnittener Rahmen ohne `}` wird komplett ignoriert - der Sketch
  behaelt dann einfach die alten Werte. Verlorene Rahmen sind damit
  unkritisch.

## Der "Parser"

`JsonParseRoot(Result, Message, Key, Index)`:

* sucht den Schluessel mit `strstr()` **irgendwo** im Puffer,
* liest den Wert ab `Position + strlen(Key) + 1` - also genau ein Zeichen
  hinter dem Schluesselnamen. Ein Leerzeichen hinter dem Doppelpunkt
  verschiebt alles und liefert Unsinn,
* `Index == 0`: Einzelwert bis zum naechsten `,` oder `}`,
* `Index >= 1`: n-ter Array-Eintrag, **1-basiert**,
* der Array-Inhalt wird nach `char JsonValue[100]` kopiert,
* **einelementige Arrays sind per Index nicht lesbar**: ohne Komma gibt die
  Funktion `false` zurueck ("MultiValue expected, but Singlevalue found").

Konsequenzen, die `duet.validate_frame()` erzwingt:

| Regel | Grund |
|-------|-------|
| kein Leerzeichen hinter `:` oder `,` | fester Offset beim Wertlesen |
| kein Schluessel ist Teil eines anderen | `strstr()` nimmt den ersten Treffer |
| keine unbeteiligten Felder | dito |
| jedes Array mit mindestens zwei Werten | einelementige Arrays sind unlesbar |
| Array-Text unter 100 Zeichen | `char JsonValue[100]` |
| Rahmen unter 540 Byte | 550-Byte-Puffer |
| genau ein `{` und ein `}` | Rahmenerkennung |

## Gelesene Felder

`AnalyzeSerialMessage()` liest ausschliesslich:

| Feld | Form | Bedeutung |
|------|------|-----------|
| `status` | ein Zeichen | Druckerstatus, siehe unten |
| `heaters` | Array Float | Ist-Temperaturen, Index 0 = Heizbett |
| `active` | Array Float | Soll-Temperatur im Zustand "active" |
| `standby` | Array Float | Soll-Temperatur im Zustand "standby" |
| `hstat` | Array Int | 0 = off, 1 = standby, 2 = active, 3 = fault, 4 = tuning |
| `fraction_printed` | Float | Druckfortschritt 0.0 .. 1.0 |

Alles andere aus einer echten RepRapFirmware-Nachricht (`pos`, `fanPercent`,
`myName` ...) wird nicht ausgewertet und deshalb auch nicht gesendet.

Fehlt ein Feld, bleibt der alte Wert stehen. Die Middleware sendet daher
immer alle sechs Felder.

---

# Gemeinsam: Anzeige auf den Ringen

## Statusbuchstaben

Die Zuordnung steht im `switch (Printer.Status)` der Anzeigeschleife - in
beiden Versionen gleich. Sie weicht von der echten RepRapFirmware ab (dort
ist `S` = paused), ist hier aber verbindlich. Bei V3.0 kommt der Buchstabe
direkt aus dem JSON, bei V4.x macht `StreamingHandler.cpp` ihn aus einem
Statuswort:

| Buchstabe | Farbe im Sketch | V3.0 sendet | V4.x sendet | Verwendung |
|-----------|-----------------|-------------|-------------|------------|
| `I` | `ColorIdle` | `"I"` | `"idle"` | Leerlauf |
| `P` | Fortschrittsbalken | `"P"` | `"processing"` | Druck laeuft, Druck fertig |
| `A` | `ColorPaused` | `"A"` | `"paused"` | pausiert |
| `R` | `ColorPausing` | `"R"` | `"pausing"` | PAUSE laeuft |
| `B` | `ColorResuming` | `"B"` | `"resuming"` | RESUME laeuft |
| `D` | `ColorBusy` | `"D"` | `"busy"` | Makro, Homing, manuelles Heizen |
| `S` | `ColorStopped` | `"S"` | `"halted"` | Klipper-Shutdown, Abbruch, offline |
| `C` | `ColorConfiguring` | `"C"` | `"updating"` | Klipper startet |
| `F` | `ColorFlashing` | `"F"` | `"starting"` | frei (Firmware-Update) |
| sonst | `ColorDefault` | - | - | - |

V4.x kennt zusaetzlich `simulating` (wie `processing`) und `off` (wie
`halted`); `idle`, `starting`, `updating`, `halted` und `off` loesen dort
``resetPrinting()`` aus.

## Heizungsgrafik

Pro LED (1 .. `LEDs`) mit `a = (Ist - TempOffset) / Scale` und
`s = (Soll - TempOffset) / Scale`:

| Bedingung | Farbe |
|-----------|-------|
| `a < LED` und `s >= LED` | `ColorHeatUp` (noch zu heizen) |
| `a >= LED` und `s >= LED` | `ColorHeatUpDone` (erreicht) |
| `a >= LED` und `s < LED` | `ColorCoolDown` (Nachwaerme) |
| sonst | `ColorIdle` |

Der Sollwert kommt aus `hstat`: bei `2` aus `active`, bei `1` aus `standby`,
sonst ist er 0. Klipper kennt kein Standby - die Middleware meldet deshalb
`2`, sobald ein `target > 0` gesetzt ist, und sonst `0`.

## Fortschrittsbalken

```c
if (NeoPixelLEDID < (Printer.FractionPrinted * NeoPixelConfig[i].LEDs))
```

Der Vergleich ist strikt, LED-Nummern sind 1-basiert: bei `fraction = 1.0`
und 16 LEDs ist `16 < 16` falsch, die letzte LED bleibt auf der
Druckfarbe. Ein Wert knapp **ueber** 1.0 fuellt den Ring vollstaendig -
unabhaengig von der LED-Anzahl, denn die Bedingung laeuft auf
`fraction > 1` hinaus.

Bei **V3.0** sendet die Middleware deshalb bei 100 % den Wert `1.01`
(abschaltbar mit `full_ring_at_100 = false`). Bei **V4.x** geht das nicht:
dort wird `fraction` aus Zeiten errechnet, und weil `printRemaining` ein
`uint16_t` ist, kann das Ergebnis nie groesser als 1.0 werden. Die letzte
LED bleibt bei V4.x also immer auf der Druckfarbe.

## LED-Reihenfolge

`ConvertPosition2PixelIndex(PixelCount, PixelOffset, Position, Reverse)`
rechnet Grafikposition in Pixelindex um. Ohne Reverse gilt
`Index = PixelCount - Position`, LED 1 der Grafik ist also das **letzte**
Pixel im Streifen. `PixelOffset` dreht den Startpunkt (positiv im
Uhrzeigersinn), `Reverse` spiegelt die Richtung. `sketchmodel.py` baut das
nach, damit `--preview` dieselbe Reihenfolge zeigt wie die Hardware.

---

# Teil 2: V4.0/V4.1 (Objektmodell)

Grundlage: `NeoPixel_BLV.ino`, `StreamingHandler.cpp/.h` und `src/json/`
aus Release V4.1.

## Transport

* 57600 Baud, 8N1 - wie V3.0.
* V4.x **fragt selbst**: `Serial.println("M409 F\"d99f\"")`, alle 1000 ms
  solange der Status nicht `I` ist, sonst alle 5000 ms
  (`ACTIVE_UPDATE_THRESHOLD` / `UPDATE_THRESHOLD`).
  Die Middleware beantwortet jede Abfrage sofort und sendet zusaetzlich
  ihren Heartbeat - so funktioniert auch eine Verdrahtung, bei der der
  Arduino nicht zu Wort kommt.
* `GetSerialMessage()` sammelt ab `{` und beendet die Nachricht beim
  **Zeilenumbruch** (nicht beim `}`); Timeout 1500 ms. Jeder Rahmen muss
  also mit `\n` enden.
* Anfuehrungszeichen bleiben erhalten - es wird echtes JSON gelesen.

## Format

```json
{"state":{"status":"processing"},
 "heaters":[{"current":60.1,"active":60.0,"standby":0.0,"state":"active"}],
 "timesLeft":{"slicer":500},"job":{"duration":500,"warmUpDuration":0}}
```

Gelesen wird (`StreamingHandler::handleValue`, Schluessel + Elternschluessel):

| Schluessel | Eltern | Bedeutung |
|------------|--------|-----------|
| `status` | `state` | Statuswort |
| `current` | `heaters` | Ist-Temperatur |
| `active` | `heaters` | Soll-Temperatur aktiv |
| `standby` | `heaters` | Soll-Temperatur Standby |
| `state` | `heaters` | `off`/`offline`, `standby`, `active`, `fault`, `tuning` |
| `slicer` | `timesLeft` | Restzeit in Sekunden |
| `duration` | `job` | bisherige Druckdauer |
| `warmUpDuration` | `job` | Aufheizzeit |

## Der Parser und seine Grenzen

V4.x benutzt einen echten Streaming-Parser (`src/json`, MIT, von squix78)
mit einem eigenen Listener. Die Fallen liegen im Listener, nicht im Parser:

| Regel | Grund |
|-------|-------|
| nur **ein** Array (`heaters`), darin nur Objekte | `endArray()` setzt `inArray` global zurueck, der Heater-Index wird in `startObject()` gezaehlt |
| `heaters` ueber die Array-Position, Luecken auffuellen | derselbe Zaehler |
| hoechstens 5 Heater | `Heater_ActTemp[index]` wird ungeprueft beschrieben (`NumberHeaters 5`) |
| `timesLeft` **vor** `job` | `job.duration` und `job.warmUpDuration` werden nur uebernommen, solange `printRemaining > 0` |
| `timesLeft.slicer` >= 1, solange `duration > 0` | dieselbe Bedingung |
| Schluessel und Werte <= 31 Zeichen | `BUFFER_MAX_LENGTH 32` in `JsonStreamingParser.h` |
| Werte als `uint16_t` | `printDuration`, `printRemaining`, `printWarmUpDuration` |
| Rahmen mit `\n` abschliessen | Ende der Nachricht |

Der Key-Stack des Listeners ist nur drei Ebenen tief (`String stack[3]`) -
fuer dieses flache Format reicht das genau.

Ein Unterschied zu V3.0, der in der Praxis zaehlt: Der Listener schreibt
die Werte **waehrend** des Parsens direkt in den Druckerzustand. Eine
abgebrochene Nachricht hinterlaesst also Teilwerte, waehrend V3.0 eine
unvollstaendige Nachricht komplett verwirft.

## Fortschritt

```cpp
FractionPrinted = (printDuration - printWarmUpDuration)
                / max(1, (printDuration - printWarmUpDuration) + printRemaining)
```

Klipper liefert einen Prozentwert, keine brauchbaren Duet-Zeiten. Die
Middleware erzeugt deshalb kuenstliche Zeiten mit einer festen Gesamtskala
von 1000 "Sekunden": `duration = Prozent * 1000`,
`slicer = 1000 - duration` (mindestens 1). Das Verhaeltnis ist damit exakt
der Klipper-Fortschritt, und alle Werte bleiben im `uint16_t`.

## Statusabfrage

Die Abfrage wird mit `Serial.println(...)` gesendet - hart auf `Serial`,
nicht auf dem ansonsten verwendeten `SerialObject`. Auf einem Arduino Mega
(`SerialObject` = `Serial2`) kommt sie deshalb am USB-Port heraus. Die
Middleware erkennt jede Zeile, die mit `M409` beginnt, als Abfrage; bleibt
sie aus, uebernimmt der Heartbeat.
