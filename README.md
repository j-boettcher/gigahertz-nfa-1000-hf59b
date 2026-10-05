# EMF Analyzer

Auswertungstool für EMF-Langzeit-Logs des **Gigahertz Solutions NFA1000** (inkl. der
über CH4 mitgeloggten HF-Analyser **HF59B / HFW59D**). Liest die `LOG*.TXT`-Dateien der
SD-Karte ein und filtert automatisch Auffälligkeiten heraus:

- **Crest-Faktor ≥ 4×** – Zeitfenster, in denen der Peak ≥ 4× so hoch ist wie der Average
  (temporal über ein gleitendes Zeitfenster, mit Rausch-Floor gegen Fehlalarme).
- **Hohe Feldwerte** – Segmente über den SBM2015-Zonen (V/m für E-Feld, nT für Magnetfeld).

Konzept & Hintergrund: siehe [PLAN.md](PLAN.md).

## Setup

```bash
uv sync            # Python 3.12 venv + Abhängigkeiten
```

## Dashboard starten

```bash
uv run python app.py     # → http://127.0.0.1:8050
```

Oben rechts schaltet ein Menü zwischen den Ansichten **Langzeit-Log** (LOG*.TXT) und
**Punktmessung** (.9PM/.6PM) um; die Wahl bleibt im Browser gespeichert. In der Log-Ansicht
oben die Aufzeichnung wählen. Unter dem Dropdown lässt sich ein
**Messort** eintragen (z. B. „Schlafzimmer, Bett Kopfende"); er wird pro Messung in
`locations.json` gespeichert und erscheint im PDF-Protokoll. Angezeigt werden: Kennzahlen,
Frequenzband-Zeitverlauf mit SBM-Zonen, Crest-Markern und Audionotiz-Markern,
Band-Spektrogramm (welches Band wann dominiert), die Audionotizen der Aufzeichnung
(direkt abspielbar) und die Liste der Auffälligkeiten.

**Zeitausschnitt / Zoom:** Im Zeitverlauf einen Bereich ziehen, um hineinzuzoomen —
Kennzahlen, Auffälligkeiten und Audionotizen beschränken sich dann auf diesen Ausschnitt,
das Spektrogramm zoomt mit. Doppelklick im Graph oder der Button „Ganze Aufzeichnung"
setzt zurück.

**HF-Analyser (CH4):** Standardmäßig ist CH4 der 4. NFA-Kanal (das automatisch mitgeloggte
andere Feld). War für eine Messung ein HF59B/HFW59D angeschlossen, in der Karte
„HF-Analyser (CH4)" den **Messbereich** (grob/mittel/fein) und **DC-Out** (1/2 V) einstellen,
die am HF-Gerät verwendet wurden. Optional lässt sich **Zubehör** wählen: der Verstärker
**HV10** (×0,1 auf den Bereich, misst schwächere Felder) oder der Dämpfer **DG20** (×100,
misst stärkere Felder bis ~2.000.000 µW/m²) — der Multiplikator geht in die Umrechnung ein.
CH4 wird dann in **µW/m²** umgerechnet (plus SBM-Stufe und
mV/m), und der **Crest-Faktor läuft auch auf CH4** — Peak ≫ Average deutet auf gepulste
Digitalsignale (Mobilfunk/DECT/WLAN) hin; solche CH4-Crest-Events erscheinen in der
Auffälligkeiten-Liste (als „CH4 (HF)", in µW/m²). Die Einstellung wird **pro Messung** in
`hf_settings.json` gespeichert und beim nächsten Start wieder geladen. Sobald HF gesetzt ist,
erscheint über dem Zeitverlauf ein Umschalter **Feld (CH1–3) ↔ HF (CH4)**: das CH4-Diagramm
zeigt das HF-Signal in µW/m² auf Log-Skala mit eigenen SBM-Zonen und Crest-Markern (Feld und
HF haben verschiedene Einheiten und lassen sich daher nicht sinnvoll in *ein* Diagramm legen).

**CH4 ohne HF:** Auch ohne HF59x ist der Umschalter da, sobald CH4 Daten hat — dann zeigt
er den 4. NFA-Kanal in seiner nativen Einheit. Bei einer Magnetfeldmessung loggt das NFA1000
dort automatisch das **E-Feld (V/m)**, bei einer E-Feld-Messung das Magnetfeld (nT) — jeweils
linear mit den SBM-Zonen der Einheit. Der Umschalter heißt dann z. B. „CH4 (V/m)".

**Audionotizen:** `REC*.WAV` enthalten keinen Zeitstempel — die Zuordnung zur Aufzeichnung
läuft über die Datei-mtime (= Ende der Aufnahme; Start = mtime − Dauer).
Beim Kopieren von der SD-Karte müssen die mtimes erhalten bleiben (Finder/`cp -p` tun das)
und die Dateien dürfen nicht umbenannt werden. Läuft die Auswertung in einer anderen
Zeitzone als die Messung (z. B. Server mit TZ=UTC), muss `device_tz` in
`config.py` auf die Gerätezone gesetzt werden (z. B. `"Europe/Zurich"`), sonst
werden Notizen falschen Aufzeichnungen zugeordnet.

Logs zum Auswerten liegen in `samples/` (LOG*.TXT). Eigene Karten dorthin kopieren.
**Neue Dateien werden bei einem Seiten-Reload automatisch geladen** — kein Server-Neustart
nötig. Der Ordner wird bei jedem Reload eingelesen; da der Vollscan aller Aufzeichnungen teuer
ist (volle DataFrames), werden nur **neue oder geänderte** LOG-Dateien tatsächlich neu geparst
(Vergleich per Datei-mtime), bereits geladene wiederverwendet — der Reload bleibt so schnell.
Die aktuelle Auswahl bleibt erhalten, sofern die Datei noch existiert.

**Parse-Cache:** Geparste LOG-Dateien werden in `.cache/logs/` zwischengespeichert (Pickle,
etwa so groß wie die Rohdaten). Der erste Start parst alles (~8 s für 64 Logs / 6 Mio. Zeilen),
danach startet die App in unter 1 s. Der Cache wird pro Datei automatisch verworfen, wenn sich
mtime/Größe oder `PARSER_VERSION` in `log_txt.py` ändern; `.cache/` kann jederzeit gelöscht werden.

## Projektstruktur

```
app.py                  Dash-Dashboard (Plotly) + /audio/-Route für Notiz-Playback
src/emftool/
  log_txt.py            Parser für LOG*.TXT (Header-Code → Feldtyp/Einheit/Modus)
  point_meas.py         Parser für 9-/6-Punkt-Messungen (.9PM/.6PM → Messpunkt-Raster)
  analysis.py           Crest-Faktor, Schwellwert-Events, Spektrogramm, Kennzahlen
  audio_notes.py        REC*.WAV: Discovery, Session-Zuordnung (mtime), 16-bit-Re-Encoding
  hf.py                 HF59B/D an CH4: mV→µW/m²→mV/m + Einstellungs-Persistenz
  report.py             PDF-Messprotokoll (matplotlib PdfPages)
  config.py             SBM-2015-Richtwerte + Crest-Floor, einstellbar
samples/                echte NFA1000-Aufzeichnungen (LOG*.TXT, REC*.WAV)
hf_settings.json        HF-Einstellungen je Messung (wird vom Tool angelegt)
locations.json          Messort je Messung (wird vom Tool angelegt)
.cache/logs/            Parse-Cache der LOG-Dateien (wird vom Tool angelegt)
```

## Status / offen

- **SBM-2015-Richtwerte** sind in `config.py` gesetzt (verifiziert an der offiziellen
  IBN/Maes-Tabelle). E-Feld wird standardmäßig **potentialfrei** bewertet (NFA1000 E3D);
  für Messungen mit Erdungskabel `efield_reference = "ground"` setzen. Die Crest-Floors
  sind reine Störunterdrückung (keine SBM-Werte) und frei einstellbar.
- **Magnetfeld-Bewertung nach SBM-2015 über das 95. Perzentil** (Langzeit): die Feld-Kachel
  stuft Magnetfeld-Aufzeichnungen über das 95. Perzentil ein (kurze Spitzen dominieren nicht),
  der Max steht als Zusatz daneben. E-Feld wird weiter über den Max bewertet.
- **PDF-Report:** Button „PDF-Report" oben rechts erzeugt ein Messprotokoll der aktuellen
  Aufzeichnung (Kopf + SBM-Bewertung, Kennzahlen, Auffälligkeiten, Audionotizen, Feld-Zeitverlauf,
  Band-Spektrogramm und immer auch das CH4-Diagramm — als HF µW/m² bei konfiguriertem HF, sonst
  in nativer Einheit V/m/nT wie im Feld/CH4-Umschalter).
- **9-/6-Punkt-Heatmaps:** Die eigene Ansicht „Punktmessung" (Menü oben rechts) zeigt die räumliche
  Feldverteilung geführter Messungen (`.9PM` Schlafplatz = 3×3 Kopf/Rumpf/Füße × links/Mitte/
  rechts; `.6PM` Arbeitsplatz = Kopf/Ellbogen/Gesäß/Hände/Knie/Füße) als SBM-eingefärbte
  Heatmap, **frequenzspezifisch** (Band-Auswahl All 3D / einzelne Bänder). Das Format ist aus
  dem offiziellen Gigahertz-/NFAsoft-Handbuch abgeleitet (wie `LOG*.TXT`, aber Zeilen =
  Messpunkte). Zum Testen liegen zwei **klar benannte Beispiel­dateien** in `samples/`
  (`BEISPIEL-Schlafplatz.9PM`, `BEISPIEL-Arbeitsplatz.6PM`) — durch echte Gerätedateien
  ersetzen (die reale Struktur ist gegen eine echte `.9PM`/`.6PM` zu verifizieren).
