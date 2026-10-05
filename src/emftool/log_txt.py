"""Parser für NFA1000 ``LOG*.TXT`` Langzeit-Aufzeichnungen.

Format (an echten Samples verifiziert):
- Trennzeichen ``;``, Dezimal-Komma (``2,1`` = 2.1), jede Zeile endet mit ``;``.
- Zeile 1 = Header: ``<12-Zeichen-Code> Date;"All 3D";...;"User" <Geräte-Metadaten>``.
- Datenzeilen: ``DD.MM.YYYY HH:MM:SS,z`` (z = Zehntelsekunde) + 11 Werte + leere ``User``-Spalte.

Der 12-Zeichen-Code kodiert je Spalte den Feldtyp:
``E`` = E-Feld (V/m), ``B`` = Magnetfeld (nT), ``U`` = externer/Spannungs-Kanal (mV).
Zeichen 1-7 & 9-11 (die 3D-Feldspalten) sind einheitlich; Zeichen 8 = Typ von CH4;
Zeichen 12 (``r``/``p``) = Signalmodus tRMS/Peak.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Spalten der Datenzeilen (nach dem Zeitstempel), Reihenfolge wie im Header.
VALUE_COLS = [
    "All 3D",
    "16.7Hz",
    "50/60Hz",
    "100/120Hz",
    "150/180Hz",
    "R<2kHz",
    ">2kHz",
    "All CH4",
    "All X",
    "All Y",
    "All Z",
]
COLS = ["ts"] + VALUE_COLS + ["User"]

BAND_COLS = ["16.7Hz", "50/60Hz", "100/120Hz", "150/180Hz", "R<2kHz", ">2kHz"]
AXIS_COLS = ["All X", "All Y", "All Z"]

FIELD_UNIT = {"E": "V/m", "B": "nT", "U": "mV"}
MODE = {"r": "tRMS", "p": "Peak"}


@dataclass
class Session:
    """Eine geladene Aufzeichnung: Zeitreihe + Metadaten aus dem Header."""

    path: Path
    df: pd.DataFrame          # DatetimeIndex, Spalten = VALUE_COLS (Einheiten je main_unit/ch4_unit)
    code: str                 # 12-Zeichen-Header-Code
    main_unit: str            # Einheit der 3D-Feldkanäle ("V/m" / "nT")
    ch4_unit: str             # Einheit von CH4 ("mV" HF / "V/m" / "nT")
    mode: str                 # "tRMS" | "Peak"
    meta: str                 # roher Geräte-Metadaten-Rest des Headers

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def n(self) -> int:
        return len(self.df)

    @property
    def start(self):
        return self.df.index[0] if self.n else None

    @property
    def end(self):
        return self.df.index[-1] if self.n else None

    @property
    def duration_s(self) -> float:
        if self.n < 2:
            return 0.0
        return (self.end - self.start).total_seconds()

    @property
    def kind(self) -> str:
        return "E-Feld" if self.main_unit == "V/m" else "Magnetfeld" if self.main_unit == "nT" else "?"

    @property
    def dur_str(self) -> str:
        """Messdauer als '7 h 24 m' bzw. '46 m 05 s'."""
        m, s = divmod(int(self.duration_s), 60)
        h, m = divmod(m, 60)
        return f"{h} h {m:02d} m" if h else f"{m} m {s:02d} s"

    def label(self) -> str:
        d = self.start.strftime("%d.%m.%Y %H:%M") if self.start is not None else "leer"
        return f"{self.name} · {d} · {self.kind} · {self.mode} · {self.dur_str}"


def _parse_header(line: str) -> tuple[str, str, str, str, str]:
    before = line.split("Date", 1)[0].strip()
    code = before[-12:] if len(before) >= 12 else before
    meta = ""
    if "User" in line:
        meta = line.split("User", 1)[1].strip().strip('"').strip()
    main = FIELD_UNIT.get(code[0], "?") if code else "?"
    ch4 = FIELD_UNIT.get(code[7], "?") if len(code) > 7 else "?"
    mode = MODE.get(code[11], "?") if len(code) > 11 else "?"
    return code, main, ch4, mode, meta


def load(path: str | Path) -> Session:
    """Lädt eine ``LOG*.TXT`` in eine :class:`Session`. Leere Dateien → leere Session."""
    path = Path(path)
    with open(path, "r", encoding="latin-1", errors="replace") as f:
        header = f.readline()
    code, main_unit, ch4_unit, mode, meta = _parse_header(header)

    empty = Session(path, pd.DataFrame(columns=VALUE_COLS), code, main_unit, ch4_unit, mode, meta)
    # Alles als String lesen und Dezimal-Komma manuell umwandeln – read_csv(decimal=",")
    # arbeitet hier nicht zuverlässig (Werte wie "2,1" landen sonst als NaN).
    try:
        raw = pd.read_csv(
            path, sep=";", header=None, skiprows=1, names=COLS,
            dtype=str, engine="c", index_col=False, on_bad_lines="skip",
            encoding="latin-1",  # wie der Header-Read; utf-8 würfe bei jedem Sonderbyte
        )
    except (pd.errors.EmptyDataError, ValueError):
        return empty
    if raw.empty:
        return empty

    raw = raw[raw["ts"].notna() & (raw["ts"].str.strip() != "")]
    ts = pd.to_datetime(
        raw["ts"].str.strip().str.replace(",", ".", regex=False),
        format="%d.%m.%Y %H:%M:%S.%f", errors="coerce",
    )
    df = raw[VALUE_COLS].apply(
        lambda c: pd.to_numeric(c.str.replace(",", ".", regex=False), errors="coerce")
    )
    df.index = ts
    df = df[df.index.notna()].sort_index()
    return Session(path, df, code, main_unit, ch4_unit, mode, meta)


def scan(folder: str | Path) -> list[Session]:
    """Lädt alle ``LOG*.TXT`` eines Ordners, die mindestens 2 Datenzeilen haben."""
    folder = Path(folder)
    out: list[Session] = []
    for p in sorted(folder.glob("LOG*.TXT")):
        try:
            s = load(p)
        except Exception:  # noqa: BLE001 - defektes File soll die Liste nicht killen
            continue
        if s.n >= 2:
            out.append(s)
    return out
