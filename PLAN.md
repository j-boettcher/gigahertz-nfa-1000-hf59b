# Plan: EMF-Erfassungs- & Auswertungstool (Gigahertz Solutions NFA1000 + HF59B/HFW59D)

> Stand: 2026-07-04 · Recherche-Basis: offizielles Operating Manual „NFA 1000 / NFA 400 – NFAsoft", Version 9.1 (November 2024, Firmware 91 / NFAsoft 176). Quellen siehe Abschnitt 11.

---

## 1. Ziel

Ein Tool, das Messdaten der Geräte **NFA1000** (3D-Niederfrequenz-Analyser) und **HF59B / HFW59D** (Hochfrequenz-Analyser, über das NFA1000 mitgeloggt) einliest, aufbereitet und **automatisch Auffälligkeiten** herausfiltert. Zwei fachliche Kernfragen:

1. **Crest-Faktor (Peak/Average ≥ 4):** Zeitfenster/Kanäle finden, in denen der **Peak mindestens 4× so hoch** ist wie der **Average (RMS)** → Indikator für gepulste/impulshaltige Felder (typisch für „dirty power" und gepulste HF wie Mobilfunk/DECT/WLAN).
2. **Hohe V/m-Werte:** Ungewöhnlich hohe, **potentialfrei** gemessene 3D-**E-Felder (V/m)** in einem Raum identifizieren und klassifizieren.

---

## 2. TL;DR – Machbarkeit & Empfehlung

| Frage | Antwort |
|---|---|
| HF59B/HFW59D über NFA1000 auslesbar? | **Ja, bestätigt.** Analog-Ausgang des HF-Geräts → Kabel **KAB0053** → **AC/DC-Buchse** des NFA1000 → aufgezeichnet als **Kanal 4 (CH4)** in mV. |
| SD-Daten am Mac auslesen? | **Ja, bestätigt (am Gerät getestet).** Das NFA1000 meldet sich per USB als **Massenspeicher/Laufwerk** am Mac; die SD-Daten sind direkt lesbar. Die Manual-Einschränkung „windows and Linux only" betrifft **nur die NFAsoft-Konfiguration** über USB, nicht das Lesen der Karte (USB Mass Storage ist plattformunabhängig). |
| Auswertung live oder post-hoc? | **Post-hoc – entschieden.** Kein USB-Messwert-Stream; das Laufwerk ist am Mac erst **nach dem Stoppen** der Aufzeichnung lesbar. Auswertung erfolgt nach jedem Langzeit-Logging (Abschnitt 4). |
| Nachträgliche Python-Auswertung möglich? | **Ja, sehr gut – Format an echten Samples verifiziert.** `LOG*.TXT`, `;`-getrennt, ~10 Hz, Feldtyp & Signalmodus im Header kodiert. Parser-Spec in 3.3, Stack in 7. |
| Empfohlener Workflow | Auf **SD-Karte loggen → am Mac auslesen (Geräte-USB oder Kartenleser) → Python-Pipeline** für Analyse & Report. |

**Stand der Daten:** Echte Samples liegen in `samples/` vor; das `LOG*.TXT`-Format ist damit **verifiziert** (siehe 3.3). Offen bleiben nur eine **nicht-leere `.9PM`/`.6PM`** (Punktmessung) und die finale Bestätigung der `r`/`p`-Modus-Semantik (Abschnitt 10).

---

## 3. Verifizierte Gerätefakten (Grundlage der Architektur)

### 3.1 NFA1000 – Messung & Aufzeichnung
- Misst **3D-AC-Magnetfeld** (nT / mG) und **3D potentialfreies AC-E-Feld** (V/m), zusätzlich erdbezogenes E-Feld. Frequenzbereich **5 Hz – 1000 kHz**.
- **E3D-Modus = potentialfreies 3D-E-Feld** (genau der Modus für Kernfrage 2). Wichtig: E-Feld **nur im Akkubetrieb** messen – externe Stromversorgung verfälscht das E-Feld (Manual 5.4 / 6.3).
- **4 Aufzeichnungskanäle:** 3 Feldachsen (X/Y/Z) + **CH4** (extern/HF oder E-Feld-Zusatz).
- **Abtastrate: 10 vollständige Datensätze pro Sekunde (10 Hz)** in den SD-Log (Manual 5.4).
- **Signal-Auswertung per Schalter: tRMS / Hold / Peak** – das Gerät zeichnet **eine** Größe pro Aufzeichnung auf (bestätigt: die Samples haben **einen Wert je Kanal**, kein paralleles RMS+Peak). Der Modus steht im Header (`r`=tRMS / `p`=Peak, siehe 3.3). ⇒ Crest-Faktor wird **temporal** über die Zeitreihe gebildet – optional durch **Paaren** einer `r`- und einer `p`-Aufzeichnung derselben Szene verfeinert (Abschnitt 6.1).
- **Frequenzbänder (TCO):** 16,7 Hz · 50/60 Hz · 100/120 Hz · 150/180 Hz · R<2 kHz · >2 kHz. NFAsoft zeigt daraus abgeleitete Kanäle („All3D", „AllX/Y/Z", Bandsplits) – Quadratsumme der Bänder = Kennzahl für „dirty electricity".

### 3.2 HF-Anbindung (HF59B / HFW59D) – bestätigt
- **Anschluss:** HF-Analyser Analog-DC-Ausgang → **Verbindungskabel KAB0053** → **AC/DC-Kombi-Eingang** des NFA1000. Optional **Entstör-Kit EDY** gegen LF/RF-Einkopplung.
- **Aufzeichnung als CH4** in **mV**.
- **Skalierung (Beispiel HF59B):** HF-Gerät auf **coarse/grob, auf 1 V justiert** → NFA loggt **1 µW/m² … ~30.000 µW/m²**. (Anzeigewert 2000 = 1 V RMS AC am Eingang.)
- **Limitierung:** Das NFA1000 kennt die Einstellungen des HF-Geräts **nicht** und speichert sie nicht mit („make sure to remember the settings … they will not be registered by the NFA"). Der gewählte **Messbereich + DC-Out-Spannung müssen manuell dokumentiert** werden (per Audionotiz oder Session-Metadaten). NFAsoft hat dafür den Dialog **„Select HF-Unit"** (Messbereich z. B. 20 µW/m², DC-Out z. B. 2 V) zur Umrechnung **mV → µW/m² → mV/m**.
- **RMS vs. Peak bei HF:** Das HF-Gerät hat einen **eigenen AVG/Peak-Schalter**; der Analog-Ausgang gibt das aus, was am HF-Gerät eingestellt ist. Für die Crest-Analyse relevant (Abschnitt 6.1).

### 3.3 Dateiformate auf der SD-Karte (an echten Samples in `samples/` verifiziert)
| Datei | Inhalt | Auswertung |
|---|---|---|
| `LOG*.TXT` | **Langzeit-Aufzeichnung** (~10 Hz, alle Kanäle) | **Text, `;`-getrennt → direkt parsebar (Hauptdatei)** |
| `LOG*.9PM` | Geführte **9-Punkt-Messung** (Schlafplatz) | Heatmap (nicht-leeres Sample noch nötig) |
| `LOG*.6PM` | Geführte **6-Punkt-Messung** (Arbeitsplatz) | Heatmap |
| `REC*.WAV` | **Audionotizen** (RIFF PCM, 8 bit, mono, 17875 Hz) | Zeitachsen-Marker + Playback |
| `CONFIG.NFA` | Geräte-Konfiguration (binär, nicht lesbar) | – |

**`LOG*.TXT` – verifizierte Struktur:**
- **Trennzeichen `;`**, **Dezimal-Komma** (`2,1` = 2.1); jede Zeile endet mit `;`.
- **Zeile 1 = Header:** `<12-Zeichen-Code> Date;"All 3D";"16,7Hz 3D";"50/60Hz 3D";"100/120Hz 3D";"150/180Hz 3D";"R<2kHz 3D";">2kHz 3D";"All CH4";"All X";"All Y";"All Z";"User"` + Geräte-Metadaten (`NFA1000 SN … f091 <cal-datum> hp <n> sdc <n>`).
- **Datenzeilen:** `DD.MM.YYYY HH:MM:SS,z` (z = **Zehntelsekunde** ⇒ 10 Hz) + 11 Werte (All 3D · 6 Bänder · All CH4 · All X/Y/Z) + leere `User`-Spalte.
- **`All 3D` = √(X²+Y²+Z²)** (verifiziert); die 6 Bänder = Frequenzzerlegung des 3D-Werts.
- **12-Zeichen-Code = Feldtyp je Spalte** – so erkennt der Parser Messart & Einheit **automatisch**:
  - **`E`** = E-Feld (**V/m**) · **`B`** = Magnetfeld (**nT**) · **`U`** = externer/Spannungs-Kanal (HF-Analyser bzw. Körperspannung).
  - Zeichen 1–7 & 9–11 (die 3D-Feldspalten) sind einheitlich `E…E` **oder** `B…B` ⇒ E-Feld- vs. Magnetfeld-Aufzeichnung.
  - Zeichen 8 = Typ von **CH4** (unabhängig, z. B. `B`/`E`/`U`) – hier landet der HF-Analyser.
  - **Zeichen 12 (`r`/`p`)** = sehr wahrscheinlich **Signalmodus `r`=tRMS / `p`=Peak** (beide in den Samples vorhanden – final zu bestätigen).
- **Zeitstempel nicht als starr 10 Hz annehmen** – die Samples enthalten Lücken; stets die realen Zeitstempel verwenden.
- **Dateigrößen** reichen von wenigen KB bis ~18 MB (~266 000 Zeilen ≈ 7 h) ⇒ effizientes Parsen + Parquet-Cache sinnvoll.

### 3.4 NFAsoft (Referenz für Features, die wir in Python nachbauen/übertreffen)
- Statistik je Kanal (u. a. **95. Perzentil**, „Edges per hour").
- **„Set Edge Criteria"** (experimentell): *Absolute increment*, ***Relative increment X[t+a]/X[t]*** (≈ Verhältnis-/Crest-Detektor!), *Slope*, *Absolute threshold*. → Bestätigt: die Peak/Average-Idee ist gerätenah relevant; wir bauen sie robuster.
- **SBM2015-Farbklassifikation:** grün (unauffällig) · gelb (schwach) · rot (stark) · violett (extrem).
- Umrechnungen µW/m² → mV/m, nT ↔ mG; Bänder-Quadratsumme; KML-Export (GPS).
- **Plattform:** primär Windows (98+); Linux/Mac „as is". **USB-Konfiguration nur Windows/Linux.**

---

## 4. Datenübertragung & Workflow (entschieden: post-hoc)

**Festgelegt (mit Nutzer bestätigt):** Ausgewertet wird **nach Abschluss des Langzeit-Loggings**. Ein kontinuierliches Live-Streaming der Messwerte über USB gibt es nicht (das NFA1000 schreibt auf die SD-Karte); das Laufwerk ist am Mac **erst nach dem Stoppen** der Aufzeichnung lesbar – für diesen Anwendungsfall völlig ausreichend.

**Workflow:**
1. NFA1000 misst/loggt auf SD (optional HF59B/HFW59D an AC/DC → CH4; Audionotizen per „Rec"-Taste).
2. Aufzeichnung stoppen; Gerät per USB an den Mac (erscheint als Laufwerk) **oder** SD in den Kartenleser.
3. `LOG*.TXT` (+ zugehörige `REC*.WAV`) in die Python-Pipeline → Analyse → Report/Dashboard.

*Konfiguration (separat):* Die NFAsoft-Geräte-Konfiguration über USB ist laut Manual „windows and Linux only" – betrifft nur das **Einstellen** des Geräts (Filter/Presets/Zeit-Sync), nicht das Auslesen der Daten am Mac. Falls nötig einmalig über Windows/Linux(-VM).

*Optional (bewusst außerhalb des Scopes):* Echtes Sekunden-Live wäre nur mit einem separaten USB-DAQ-Modul am Analog-Ausgang des HF-Analysers machbar – eigenes Hardware-Projekt.

---

## 5. Datenfluss / Architektur

```
[NFA1000]  ──(loggt @10 Hz)──▶  [SD-Karte: LOG*.TXT / *.9PM / *.6PM / *.WAV]
   ▲                                        │
   │ (HF59B/HFW59D → KAB0053 → AC/DC → CH4) │  Kartenleser
   │                                        ▼
                                   [Mac: Python-Pipeline]
   Ingest → Normalisieren/Einheiten → Analyse → Events → Report/Dashboard
```

Stufen:
1. **Ingest:** `LOG*.TXT` parsen (`;`-getrennt, Dezimal-Komma; **Header-Code → Feldtyp/Einheit/Modus je Kanal**), Zeitachse aus den realen Zeitstempeln, `.9PM/.6PM` und `REC*.WAV`-Marker (per Datei-mtime) einlesen.
2. **Normalisieren:** Einheiten je Kanal (nT/mG, V/m, mV→µW/m²→mV/m für CH4 via HF-Range), Frequenzbänder, Session-Metadaten (Raum, Modus, HF-Einstellungen).
3. **Analyse:** Crest-Faktor, Schwellwert/SBM2015, Kantendetektion, Band-Quadratsumme.
4. **Events:** zusammenhängende Auffälligkeits-Segmente (Start/Ende, Peak, Dauer, Kanal, Raum) extrahieren & ranken.
5. **Ausgabe:** interaktives Dashboard + statischer Report (HTML/PDF), Heatmaps für 9-/6-Punkt.

---

## 6. Auswerte-Logik (Kern)

### 6.1 Analyse 1 – Crest-Faktor (Peak/Average ≥ 4)
Zwei Betriebsarten, je nach Datenlage:

- **(a) Gepaarter Crest (r/p)** – die Aufzeichnungen enthalten **einen** Wert je Kanal, der Header markiert aber den Modus (`r`=tRMS / `p`=Peak). Werden dieselbe Szene einmal als `r` und einmal als `p` aufgezeichnet, ergibt sich der Crest je Kanal statistisch aus `p`-Datei ÷ `r`-Datei (nicht sample-genau, da nicht simultan).
- **(b) Temporaler Crest** *(empfohlen, funktioniert mit EINER tRMS-Aufzeichnung @10 Hz)*: gleitendes Fenster (konfigurierbar, z. B. 1 s / 10 s / 60 s):
  - `Average = Mittel der tRMS-Samples im Fenster`
  - `Peak = Max (oder robustes 99. Perzentil) der Samples im Fenster`
  - **Flag, wenn `Peak/Average ≥ 4` UND `Peak ≥ absoluter Rausch-Floor`** (der Floor verhindert Fehlalarme durch Grundrauschen bei sehr kleinen Werten; Manual nennt ~0,1 µW/m² als sinnvolle Untergrenze für HF).
- **HF-Sonderfall (CH4):** ✅ gebaut — temporaler Crest läuft auch auf dem nach µW/m² umgerechneten CH4 (skaleninvariant, Floor 0,1 µW/m²); CH4-Crest-Events erscheinen als „CH4 (HF)" in der Auffälligkeiten-Liste. Crest ist **der** Indikator für gepulste Digitalsignale (für den *instantanen* RF-Crest HF-Gerät im Peak-Modus loggen).
- **Output:** Event-Liste `(t_start, t_end, Kanal, Average, Peak, Ratio, Dauer)`, Ranking nach Ratio×Dauer×Absolutwert, Overlay im Zeitplot.

### 6.2 Analyse 2 – Hohe V/m (potentialfreies E-Feld)
- Quelle: **E3D**, tRMS, Akkubetrieb. Kanäle E-Feld X/Y/Z + All3D in **V/m**.
- **Klassifikation nach SBM-2015** (unauffällig/schwach/stark/extrem) — echte Richtwerte gesetzt (`config.py`, verifiziert an der offiziellen IBN/Maes-Tabelle): E-Feld **potentialfrei** V/m (0,3 / 1,5 / 10), E-Feld erdbezogen (1 / 5 / 50), Magnetfeld nT (20 / 100 / 500), HF µW/m² (0,1 / 10 / 1000). Der E-Feld-Bezug (potentialfrei = NFA1000 E3D) ist per `efield_reference` umschaltbar.
- **Pro Raum aggregieren:** max, 95. Perzentil, %-Zeit in rot/violett. Raum-Zuordnung über Audionotizen/Kommentare/Session-Metadaten.
- **9-/6-Punkt:** Heatmap-Raster wie in NFAsoft reproduzieren.
- **Output:** Raum-für-Raum-Tabelle + markierte Zeitfenster + Heatmaps.

### 6.3 Zusatz (optional, „gratis" mitnehmbar)
- **Kantendetektion** (Rate of Change) analog „Edges per hour", aber besser.
- **„Dirty electricity":** Quadratsumme der Bänder 100/120 + 150/180 + R<2k + >2k.
- ✅ **Einheiten-Konverter** mV → µW/m² → mV/m gebaut (`hf.py`): Messbereich (grob/mittel/fein) + DC-Out (1/2 V) pro Messung wählbar & persistent; CH4-Kachel zeigt µW/m² + SBM-Stufe + mV/m; Crest-Faktor läuft auch auf CH4; eigenes CH4-Diagramm (µW/m², Log) per Feld/CH4-Umschalter.

### 6.4 Frequenz-Ansichten (alle Bänder)
Der Log liefert je Zeitpunkt **6 Frequenzbänder** (16,7 Hz · 50/60 Hz · 100/120 Hz · 150/180 Hz · R<2 kHz · >2 kHz) plus **All 3D** – je nach Aufzeichnung für Magnetfeld (nT) oder E-Feld (V/m). Das ist eine **feste 6-Band-Zerlegung (keine FFT / kein Feinspektrum)** – feiner ist aus dem Log nicht möglich.

Vorgesehene Diagramme (interaktiv mit **Plotly (WebGL)** im **Dash**-Dashboard, **matplotlib** für den statischen Report):
- **Multi-Linien-Zeitverlauf:** alle 6 Bänder + All 3D überlagert, farbcodiert, gemeinsame Zeitachse (Zoom/Pan).
- **Band-Spektrogramm (Heatmap):** x = Zeit, y = Band, Farbe = Intensität → zeigt sofort, *welches Band wann* dominiert.
- **Gestapelte Fläche:** Beitrag jedes Bands zum Gesamtwert über die Zeit.
- **Spektrum-Snapshot (Balken):** die 6 Bänder zu einem gewählten Zeitpunkt bzw. gemittelt über ein Fenster – der eigentliche „Frequenzbereichs-Diagramm"-Blick.

Alle Ansichten mit SBM2015-Farbskala und Verknüpfung zu den Crest-/Schwellwert-Events. Kein zusätzlicher Stack nötig – Plotly/matplotlib decken das ab.

---

## 7. Vorgeschlagener Tech-Stack (Python)

| Zweck | Wahl | Warum |
|---|---|---|
| Sprache/Umgebung | **Python 3.12 + `uv`** (Lockfile) – entschieden | schnell, reproduzierbar, Mac-nativ |
| Datenframes | **pandas + numpy** | Standard für Zeitreihen |
| Signal-/Statistik | **scipy** | Fenster, Perzentile, Kantendetektion |
| Speicherung große Logs | **Parquet (pyarrow)**, optional **DuckDB** | 16-GB-Karte ≈ bis ~16 Tage @10 Hz → viele Mio. Zeilen; DuckDB/Parquet skaliert |
| Audio | **soundfile** | `.WAV`-Notizen |
| Interaktiv (GUI) | **Plotly Dash + `dash-mantine-components`** | app-artig/poliert, volle Layout-Kontrolle, Klick-Inspektion, Audio-Playback |
| Rendering großer Zeitreihen | **Plotly WebGL (`scattergl`)** + min/max-Dezimation (Level-of-Detail) | flüssiges Zoomen auch bei >100k Punkten |
| Statischer Report | **Jinja2 + matplotlib → HTML**, optional PDF (WeasyPrint) | teilbare Mess-Protokolle |
| CLI | **Typer** | `emftool ingest|analyze|report` |
| Konfiguration | **Pydantic + YAML** | Schwellen (4×, SBM2015, HF-Ranges) sauber & validiert |
| Tests | **pytest** | Parser & Analyse gegen Referenzdaten |

*GUI-Entscheid:* **Plotly Dash + Design-Komponenten** (statt Streamlit) für einen polierten, app-artigen Look mit voller Layout-Kontrolle; läuft lokal im Browser. **matplotlib** erzeugt zusätzlich die statischen Report-Grafiken. (Streamlit bliebe nur als schneller MVP-Fallback.)

*Portabilität:* Bewusst **kein ddev/Docker** (Mac-lokales Tool, `uv` reicht). Ein Docker-Image oder devcontainer lässt sich später ohne Umbau ergänzen, falls das Dashboard auf einem Server / anderen Rechnern laufen soll.

---

## 8. Projektstruktur (Vorschlag)

```
EMF/
├─ PLAN.md
├─ pyproject.toml
├─ config/
│  ├─ thresholds.yaml        # 4x-Ratio, SBM2015-Grenzen, Rausch-Floors
│  └─ hf_ranges.yaml         # HF59B/HFW59D Messbereich↔DC-Out Mapping
├─ src/emftool/
│  ├─ io/
│  │  ├─ log_txt.py          # Parser LOG*.TXT: Header-Code→Feldtyp/Modus, ;-Split, Komma-Dezimal
│  │  ├─ point_meas.py       # .9PM / .6PM (Heatmap-Punktmessungen)
│  │  └─ audio_notes.py      # REC*.WAV: Marker per mtime auf Log-Zeitachse + Playback
│  ├─ model.py               # Session-/Kanal-/Einheiten-Datenmodell (pydantic)
│  ├─ units.py               # mV↔µW/m²↔mV/m, nT↔mG
│  ├─ analysis/
│  │  ├─ crest.py            # Analyse 1
│  │  ├─ thresholds.py       # Analyse 2 (SBM2015)
│  │  ├─ edges.py            # Kantendetektion
│  │  └─ events.py           # Segmentierung + Ranking
│  ├─ report/
│  │  ├─ app_dash.py         # Plotly Dash + dash-mantine (interaktive GUI)
│  │  └─ static_report.py    # HTML/PDF (matplotlib + Jinja2)
│  └─ cli.py                 # Typer
├─ tests/
│  └─ data/                  # anonymisierte Referenz-Logs
└─ samples/                  # echte Geräte-Logs (LOG*.TXT, REC*.WAV) – bereits vorhanden
```

---

## 9. Implementierungs-Roadmap (Phasen)

> **Stand: Grundgerüst gebaut & lauffähig** (`app.py`, `src/emftool/`). Parser für `LOG*.TXT`
> (an 31 echten Samples verifiziert), beide Kern-Analysen (Crest ≥ 4×, hohe Feldwerte) und das
> Dash-Dashboard (Kennzahlen · Frequenzband-Zeitverlauf mit SBM-Zonen · Band-Spektrogramm ·
> Auffälligkeiten) laufen. Damit sind Teile von Phase 1 und Phase 3 bereits erledigt.

**Phase 0 – Datengrundlage:** ✅ weitgehend erledigt – `LOG*.TXT`-Format an echten Samples verifiziert (Delimiter, Zeitstempel, Header-Code für Feldtyp/Einheit/Modus, ein Wert je Kanal). Rest-offen: nicht-leere `.9PM`/`.6PM`, `r`/`p`-Bestätigung.

**Phase 1 – MVP (CLI + Parser + Kern-Analysen):**
- `log_txt.py` robust parsen (→ pandas, Parquet-Cache).
- Analyse 1 (temporaler Crest ≥ 4) + Analyse 2 (V/m-Schwellen).
- Event-Liste als CSV/Tabelle; einfache matplotlib-Plots.
- *Ergebnis:* „Wirf eine LOG-Datei rein, bekomme eine Liste der Auffälligkeiten."

**Phase 2 – Auswertung & Einheiten:**
- HF-Umrechnung mV→µW/m²→mV/m; Band-Quadratsumme; SBM2015-Klassifikation; Raum-Aggregation über Audionotizen.
- Kantendetektion.

**Phase 3 – Interaktives Dashboard (Plotly Dash + Design):**
- Zoombare WebGL-Zeitverläufe mit SBM-Farbbändern, Anomalie-Overlays, Klick-Inspektion, Band-Spektrogramm, Audio-Playback, 9-/6-Punkt-Heatmaps – in einem app-artigen Layout (dash-mantine).

**Phase 4 – Report & Politur:**
- ✅ **PDF-Report gebaut** (`report.py`, matplotlib PdfPages): Messprotokoll je Aufzeichnung mit Kopf/SBM-Bewertung, Kennzahlen, Auffälligkeiten, Audionotizen, Feld-Zeitverlauf, Band-Spektrogramm und (bei HF) CH4-µW/m²-Diagramm. Download-Button im Dashboard.

*Fable-5-Hinweis:* Phase 1 ist der ideale erste Fable-5-Auftrag – klar abgegrenzt, testbar, liefert sofort Nutzen.

---

## 10. Offene Punkte / was gebraucht wird

1. ✅ **Sample-Logs vorhanden** (`samples/`) – `LOG*.TXT`-Format verifiziert, Parser-Spec steht.
2. **Nicht-leere `.9PM`/`.6PM`-Datei** für die Punktmessungs-Heatmaps (das vorliegende `.9PM` ist 0 Byte).
3. **`r`/`p`-Semantik final bestätigen** (tRMS vs. Peak) – z. B. dieselbe Szene bewusst einmal als tRMS und einmal als Peak aufzeichnen und vergleichen; ebenso `U` vs. `B` als exakte CH4-Einheit klären.
4. ✅ **SBM-2015-Grenzwerte gesetzt** (E-Feld potentialfrei/erdbezogen, Magnetfeld, HF) — verifiziert an der offiziellen IBN/Maes-Tabelle. ✅ Magnetfeld wird zudem über das **95. Perzentil** (Langzeit) bewertet statt über den Max (mit Mindest-Sample-Guard, Max als Zusatzinfo).
5. ✅ **HF-Setup im Tool eingebbar** (Messbereich + DC-Out pro Messung, `hf.py`/Dashboard). Offen bleibt nur: für die konkreten HF-Aufzeichnungen die tatsächlich verwendeten Einstellungen wählen.
6. **Scope-Bestätigung:** reicht das interaktive Streamlit-Dashboard, oder wird zusätzlich ein PDF-Protokoll für Kunden gebraucht?

---

## 11. Quellen
- Operating Manual NFA 1000 / NFA 400 – NFAsoft, Version 9.1 (Nov 2024): Abschnitte 3.1 (Display/LEDs), 4.3 (Sockets/AC-DC/HF), 4.5 (tRMS/Peak), 4.6 (SD), 4.7 (USB), 5.1/5.4 (Live-Messung/Langzeit-Logging), 6.3 (E-Feld/Stromversorgung), 7.1–7.4 (NFAsoft/Dateitypen/Edge-Kriterien/HF-Unit) — https://safelivingtechnologies.com/content/Products/EMFMeterNFA1000NFA400UserManual.pdf
- Gigahertz Solutions Produktseite NFA1000 — https://gigahertz-solutions.com/Measurement/Low-Frequency/NFA1000
- Datenblatt NFA30M/NFA400/NFA1000 — https://gigahertz-solutions.com/mediafiles/manuals/NF/NFA/NFA1000_400/DataSheet_GigahertzSolutions_NFA30M-NFA400-NFA1000_EN.pdf
- Safe Living Technologies – NFA1000 (HF59B/HFW59D-Logging bestätigt) — https://safelivingtechnologies.com/NFA1000/
