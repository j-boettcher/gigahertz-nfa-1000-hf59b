"""Parser für NFA1000 9-Punkt-/6-Punkt-Messungen (.9PM / .6PM).

Diese geführten Messungen erfassen die räumliche Feldverteilung an festen Körperpositionen:
- 9-Punkt (Schlafplatz): Kopf/Rumpf/Füße jeweils links/Mitte/rechts.
- 6-Punkt (Arbeitsplatz): Kopf/Ellbogen/Gesäß/Hände/Knie/Füße.
Das Ergebnis wird frequenzspezifisch als Heatmap dargestellt (pro Band ein Wert je Punkt).

Dateiformat = wie LOG*.TXT (Header-Code + Spalten, ``;``-getrennt, Dezimal-Komma), aber die
Datenzeilen sind ortsfeste Messpunkte statt einer Zeitreihe (Reihenfolge wie oben, laut
Gigahertz-Solutions-/NFAsoft-Handbuch). Laut Handbuch (Rev. Nov. 2024, Kap. 5.2/5.3/7.3 +
FAQ) ist die .9PM eine mit dem Texteditor bearbeitbare Datei mit einem Wert je Punkt und
denselben Kanälen wie die LOG-Datei (All 3D, Bänder, CH4, X/Y/Z). Herunterladbare echte
.9PM/.6PM-Dateien gibt es nicht öffentlich (Web/GitHub 10/2026 geprüft); die einzige echte
Datei in samples/ ist leer. Mit einer echten, nicht-leeren Gerätedatei gegenprüfen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .log_txt import VALUE_COLS, _parse_header

# Messpunkt-Label + Rasterposition (Zeile, Spalte) für die Heatmap.
POINTS_9 = [
    ("Kopf links", 0, 0), ("Kopf Mitte", 0, 1), ("Kopf rechts", 0, 2),
    ("Rumpf links", 1, 0), ("Rumpf Mitte", 1, 1), ("Rumpf rechts", 1, 2),
    ("Füße links", 2, 0), ("Füße Mitte", 2, 1), ("Füße rechts", 2, 2),
]
POINTS_6 = [
    ("Kopf", 0, 0), ("Ellbogen", 1, 0), ("Gesäß", 2, 0),
    ("Hände", 3, 0), ("Knie", 4, 0), ("Füße", 5, 0),
]
# Achsenbeschriftung (x, y) je Messart.
AXES = {
    "9-Punkt": (["links", "Mitte", "rechts"], ["Kopf", "Rumpf", "Füße"]),
    "6-Punkt": ([""], ["Kopf", "Ellbogen", "Gesäß", "Hände", "Knie", "Füße"]),
}


@dataclass
class PointMeasurement:
    path: Path
    df: pd.DataFrame        # index = Punkt-Label, Spalten = VALUE_COLS
    kind: str               # "9-Punkt" | "6-Punkt"
    points: list            # [(label, row, col), ...] – nur die tatsächlich vorhandenen
    main_unit: str
    mode: str
    meta: str

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def n_rows(self) -> int:
        return max((p[1] for p in self.points), default=0) + 1

    @property
    def n_cols(self) -> int:
        return max((p[2] for p in self.points), default=0) + 1

    @property
    def uid(self) -> str:
        """Eindeutige ID inkl. Endung – der Stem allein kollidiert bei gleichnamiger
        .9PM/.6PM (z. B. Schlafzimmer.9PM + Schlafzimmer.6PM)."""
        return self.path.name

    @property
    def axes(self):
        return AXES.get(self.kind, ([""], []))

    def label(self) -> str:
        wo = "Schlafplatz" if self.kind == "9-Punkt" else "Arbeitsplatz"
        return f"{self.name} · {self.kind} ({wo}) · {self.main_unit}"


def load(path: str | Path) -> PointMeasurement:
    path = Path(path)
    kind = "9-Punkt" if path.suffix.lower() == ".9pm" else "6-Punkt"
    all_points = POINTS_9 if kind == "9-Punkt" else POINTS_6

    lines = path.read_text(encoding="latin-1", errors="replace").splitlines()
    header = lines[0] if lines else ""
    code, main_unit, ch4_unit, mode, meta = _parse_header(header)

    data_lines = lines[1:]
    while data_lines and data_lines[-1].strip() == "":   # leere EOF-Zeilen ignorieren
        data_lines.pop()

    # Die i-te Datenzeile IST der i-te Messpunkt (feste Reihenfolge laut Handbuch).
    # Bewusst manuell und positionsfest geparst statt read_csv+Filter: eine defekte oder
    # ts-leere Zwischenzeile darf die folgenden Punkte NICHT auf falsche Körperpositionen
    # verschieben (der Schaden einer kaputten Zeile bleibt auf genau diese Zeile begrenzt).
    nval = len(VALUE_COLS)
    rows, points = [], []
    for i, (label, r, c) in enumerate(all_points):
        if i >= len(data_lines):
            break                       # weniger Zeilen als Punkte → fehlende Punkte weglassen
        fields = data_lines[i].split(";")   # fields[0] = ts, fields[1:] = Werte je VALUE_COLS
        vals = []
        for j in range(nval):
            txt = fields[j + 1].strip().replace(",", ".") if j + 1 < len(fields) else ""
            try:
                vals.append(float(txt) if txt else float("nan"))
            except ValueError:
                vals.append(float("nan"))
        rows.append(vals)
        points.append((label, r, c))

    df = (pd.DataFrame(rows, columns=VALUE_COLS, index=[p[0] for p in points])
          if rows else pd.DataFrame(columns=VALUE_COLS))
    return PointMeasurement(path, df, kind, points, main_unit, mode, meta)


def scan(folder: str | Path) -> list[PointMeasurement]:
    """Alle nicht-leeren .9PM/.6PM eines Ordners."""
    folder = Path(folder)
    out: list[PointMeasurement] = []
    for p in sorted(list(folder.glob("*.9PM")) + list(folder.glob("*.6PM"))):
        try:
            pm = load(p)
        except Exception:  # noqa: BLE001 - defekte Datei soll den Scan nicht abbrechen
            continue
        if not pm.df.empty and bool(pm.df.notna().any().any()):  # leere/all-NaN überspringen
            out.append(pm)
    return out
