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

import io
import re
import os
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
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

# Bei jeder Änderung am Parse-Ergebnis erhöhen → alte Cache-Dateien werden ignoriert.
PARSER_VERSION = 3

_HEADER_MARK = b'"All 3D"'
_TS_IN_META = re.compile(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}")
_TS_FORMAT = "%d.%m.%Y %H:%M:%S.%f"
_TS_LEN = 21  # "DD.MM.YYYY HH:MM:SS.z" (nach Komma→Punkt)
_TS_SEP = {2: ord("."), 5: ord("."), 10: ord(" "), 13: ord(":"), 16: ord(":"), 19: ord(".")}
_TS_DIGITS = [i for i in range(_TS_LEN) if i not in _TS_SEP]


def _drop_header_lines(data: bytes) -> bytes:
    """Entfernt alle Zeilen, die die Header-Spaltennamen enthalten (auch die erste)."""
    parts: list[bytes] = []
    pos = 0
    while (i := data.find(_HEADER_MARK, pos)) != -1:
        a = data.rfind(b"\n", 0, i) + 1
        b = data.find(b"\n", i)
        b = len(data) if b == -1 else b + 1
        parts.append(data[pos:a])
        pos = b
    parts.append(data[pos:])
    return b"".join(parts)


def _parse_ts(ts: pd.Series) -> pd.DatetimeIndex:
    """Vektorisiertes Parsen von ``DD.MM.YYYY HH:MM:SS.z``.

    ``strptime`` je Zeile war bei Millionen Zeilen der größte Einzelposten der Ladezeit.
    Die Zeitstempel sind festbreit; Zeilen, die nicht exakt ins Raster passen, laufen
    über den bisherigen ``strptime``-Pfad (inkl. Strip, ungültig → NaT).
    """
    vals = ts.fillna("").to_numpy(dtype=object)
    # U(len+1): längere Werte werden abgeschnitten und fallen über die Längenprüfung raus.
    u = np.array(vals.tolist(), dtype=f"U{_TS_LEN + 1}")
    fixed = np.char.str_len(u) == _TS_LEN
    out = np.full(len(vals), np.datetime64("NaT"), dtype="datetime64[us]")
    if fixed.any():
        b = u[fixed].view(np.uint32).reshape(-1, _TS_LEN + 1)
        ok = np.ones(len(b), dtype=bool)
        for i, ch in _TS_SEP.items():
            ok &= b[:, i] == ch
        d = b[:, _TS_DIGITS].astype(np.int64) - ord("0")
        ok &= ((d >= 0) & (d <= 9)).all(axis=1)
        # Spalten in d: DD MM YYYY hh mm ss z
        num = lambda *cols: sum(d[:, c] * 10 ** (len(cols) - 1 - k) for k, c in enumerate(cols))
        parts = pd.DataFrame({
            "year": num(4, 5, 6, 7), "month": num(2, 3), "day": num(0, 1),
            "hour": num(8, 9), "minute": num(10, 11), "second": num(12, 13),
            "ms": d[:, 14] * 100,
        })
        parsed = pd.to_datetime(parts[ok], errors="coerce").to_numpy(dtype="datetime64[us]")
        idx = np.flatnonzero(fixed)
        out[idx[ok]] = parsed
        fixed[idx[~ok]] = False  # Rasterfehler → langsamer Pfad
    rest = ~fixed
    if rest.any():
        slow = pd.Series(vals[rest]).astype(str).str.strip()
        out[rest] = pd.to_datetime(slow, format=_TS_FORMAT, errors="coerce").to_numpy(dtype="datetime64[us]")
    return pd.DatetimeIndex(out, name="ts")


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
        # Bei manchen Dateien ist die erste Datenzeile an den Header angehängt
        # ("… sdc 003.07.2026 22:15:33,8;…") – Metadaten vor dem Zeitstempel abschneiden.
        meta = _TS_IN_META.split(meta, 1)[0].strip()
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
    # Dezimal-Komma einmal über den ganzen Dateiinhalt in einen Punkt wandeln (Trenner ist
    # ``;``, Kommas kommen nur als Dezimalzeichen vor) – read_csv(decimal=",") arbeitet hier
    # nicht zuverlässig, und ein spaltenweises str.replace kostete ~2/3 der Ladezeit.
    data = path.read_bytes().replace(b",", b".")
    # Fortgesetzte Aufzeichnungen wiederholen die Header-Zeile mitten in der Datei. Diese
    # Zeilen (inkl. der ersten) vorab entfernen – sonst kippen alle Spalten auf object und
    # der Float-Parser fällt auf den langsamen Pfad. Ergebnis wie vorher: ihr Zeitstempel
    # war ohnehin ungültig und die Zeile wurde verworfen.
    data = _drop_header_lines(data)
    try:
        raw = pd.read_csv(
            io.BytesIO(data), sep=";", header=None, names=COLS,
            dtype={"ts": str, "User": str}, engine="c",
            index_col=False, on_bad_lines="skip",
            encoding="latin-1",  # wie der Header-Read; utf-8 würfe bei jedem Sonderbyte
        )
    except (pd.errors.EmptyDataError, ValueError):
        return empty
    if raw.empty:
        return empty

    ts = _parse_ts(raw["ts"])
    df = raw[VALUE_COLS]
    # Spalten mit Störzeichen landen als object – nur diese einzeln erzwingen.
    bad = [c for c in VALUE_COLS if df[c].dtype.kind != "f"]
    if bad:
        df = df.copy()
        df[bad] = df[bad].apply(pd.to_numeric, errors="coerce").astype(float)
    df.index = ts
    df = df[df.index.notna()].sort_index()
    return Session(path, df, code, main_unit, ch4_unit, mode, meta)


def load_cached(path: str | Path, cache_dir: str | Path) -> Session:
    """Wie :func:`load`, aber mit Platten-Cache des Parse-Ergebnisses (Pickle).

    Ein Treffer setzt gleiche Datei-mtime, -Größe und :data:`PARSER_VERSION` voraus;
    sonst wird neu geparst und der Cache überschrieben. Cache-Fehler (defekt, nicht
    beschreibbar) fallen still auf :func:`load` zurück – der Cache ist nur Beschleuniger.
    """
    path = Path(path)
    st = path.stat()
    key = (PARSER_VERSION, st.st_mtime_ns, st.st_size)
    cache = Path(cache_dir) / f"{path.name}.pkl"
    try:
        with open(cache, "rb") as f:
            cached_key, s = pickle.load(f)
        if cached_key == key and isinstance(s, Session):
            s.path = path
            return s
    except Exception:  # noqa: BLE001 - fehlend/defekt/veraltet → neu parsen
        pass
    s = load(path)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.tmp")
        with open(tmp, "wb") as f:
            pickle.dump((key, s), f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, cache)  # atomar: nie halb geschriebene Cache-Datei lesen
    except OSError:
        pass
    return s


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
