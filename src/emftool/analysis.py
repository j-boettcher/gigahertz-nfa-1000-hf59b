"""Kern-Analysen: temporaler Crest-Faktor, Schwellwert-Events, Spektrogramm, Kennzahlen."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .log_txt import BAND_COLS, Session

# Mindestzahl Messpunkte, damit das 95. Perzentil aussagekräftig ist (bei 10 Hz ≈ 2 s).
P95_MIN_SAMPLES = 20


@dataclass
class Event:
    type: str          # "crest" | "threshold"
    channel: str
    start: pd.Timestamp
    end: pd.Timestamp
    duration_s: float
    peak: float
    unit: str
    ratio: float | None = None   # nur bei Crest
    severity: str = "warning"    # "warning" | "danger"


def _runs(flag: pd.Series) -> list[tuple[int, int]]:
    """Indizes zusammenhängender True-Läufe (start, end) inklusive."""
    vals = flag.fillna(False).to_numpy(dtype=bool)
    out: list[tuple[int, int]] = []
    i, n = 0, len(vals)
    while i < n:
        if vals[i]:
            j = i
            while j + 1 < n and vals[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def crest_on_series(series, cfg: AnalysisConfig, unit: str, channel: str,
                    floor: float) -> list[Event]:
    """Temporaler Crest-Faktor auf einer beliebigen Zeitreihe: rollierendes Max / Mittel.

    Skaleninvariant im Verhältnis, aber ``floor`` (Störunterdrückung) muss in ``unit``
    angegeben sein – deshalb wird CH4 vor dem Aufruf nach µW/m² umgerechnet.
    """
    series = series.dropna()
    if len(series) < 3:
        return []
    roll = series.rolling(cfg.crest_window)
    mean = roll.mean()
    mx = roll.max()
    ratio = mx / mean.where(mean > 0)
    flag = (ratio >= cfg.crest_ratio) & (mx >= floor)

    events: list[Event] = []
    for i, j in _runs(flag):
        seg = series.iloc[i : j + 1]
        t0, t1 = series.index[i], series.index[j]
        dur = (t1 - t0).total_seconds()
        if dur < cfg.min_event_s:
            continue
        r = float(ratio.iloc[i : j + 1].max())
        events.append(
            Event("crest", channel, t0, t1, dur, float(seg.max()), unit,
                  ratio=r, severity="danger" if r >= 2 * cfg.crest_ratio else "warning")
        )
    return events


def crest_events(s: Session, cfg: AnalysisConfig, channel: str = "All 3D") -> list[Event]:
    """Crest-Faktor auf einem Feldkanal (in der Einheit des Hauptfelds)."""
    return crest_on_series(s.df[channel], cfg, s.main_unit, channel, cfg.floor(s.main_unit))


def sort_events(events: list[Event], cfg: AnalysisConfig) -> list[Event]:
    """Reihenfolge nach Auffälligkeit: danger zuerst, dann Magnitude, dann Crest-Ratio, dann Dauer.

    Die Magnitude ist der **auf die SBM-Grünschwelle normierte Peak** (``peak / gelb_ab``) –
    dadurch bleibt die Feldstärke-Ordnung innerhalb einer Einheit erhalten UND verschiedene
    Einheiten (V/m vs. µW/m²) werden fair vergleichbar (jeweils „x-fach über unauffällig").
    """
    def _key(e: Event):
        gelb = cfg.zones(e.unit)[0]
        norm = e.peak / gelb if gelb and gelb != float("inf") else 0.0
        return (e.severity != "danger", -norm, -(e.ratio or 0.0), -e.duration_s)

    events.sort(key=_key)
    return events


def threshold_events(s: Session, cfg: AnalysisConfig, channel: str = "All 3D") -> list[Event]:
    """Hohe Feldwerte: Segmente über der gelben SBM-Zone (rot/violett = danger)."""
    series = s.df[channel].dropna()
    if series.empty:
        return []
    gelb, rot, _viol = cfg.zones(s.main_unit)
    flag = series >= gelb
    events: list[Event] = []
    for i, j in _runs(flag):
        seg = series.iloc[i : j + 1]
        t0, t1 = series.index[i], series.index[j]
        dur = (t1 - t0).total_seconds()
        if dur < cfg.min_event_s:
            continue
        peak = float(seg.max())
        events.append(
            Event("threshold", channel, t0, t1, dur, peak, s.main_unit,
                  severity="danger" if peak >= rot else "warning")
        )
    return events


def all_events(s: Session, cfg: AnalysisConfig) -> list[Event]:
    """Auffälligkeiten des Hauptfelds (All 3D): Crest + Schwellwert. CH4-HF-Crest kommt
    in der App dazu (braucht die HF-Umrechnung)."""
    return sort_events(crest_events(s, cfg, "All 3D") + threshold_events(s, cfg, "All 3D"), cfg)


def _offset(seconds: float) -> str:
    """Resample-Offset als ganzzahlige Millisekunden.

    pandas 3.0 akzeptiert keine fraktionalen Sekunden-Offsets (z. B. "1.009s")
    → in ganze Millisekunden umwandeln (mindestens 1 ms).
    """
    return f"{max(round(seconds * 1000), 1)}ms"


def spectrogram(s: Session, cfg: AnalysisConfig) -> tuple[np.ndarray, list, list[str]]:
    """Band × Zeit Matrix (Max je Zeitbin) für die Heatmap. Gibt (Z, x_times, y_labels)."""
    df = s.df[BAND_COLS]
    if len(df) < 2:
        return np.zeros((len(BAND_COLS), 1)), [df.index[0] if len(df) else pd.Timestamp.now()], BAND_COLS
    span = (s.end - s.start).total_seconds()
    freq = max(span / cfg.spectrogram_bins, 0.1)
    binned = df.resample(_offset(freq)).max()
    z = binned.to_numpy().T  # Zeilen = Bänder, Spalten = Zeit
    return z, list(binned.index), BAND_COLS


def downsample(series: pd.Series, max_points: int) -> pd.Series:
    """Peak-erhaltendes Downsampling per Resample-Max, damit Spitzen sichtbar bleiben."""
    series = series.dropna()
    if len(series) <= max_points or len(series) < 2:
        return series
    span = (series.index[-1] - series.index[0]).total_seconds()
    freq = max(span / max_points, 0.1)
    return series.resample(_offset(freq)).max().dropna()


def overlaps(a0: pd.Timestamp, a1: pd.Timestamp,
             x0: pd.Timestamp | None, x1: pd.Timestamp | None) -> bool:
    """Überlappt das Intervall [a0, a1] das (offene) Fenster [x0, x1]?"""
    if x0 is not None and a1 < x0:
        return False
    if x1 is not None and a0 > x1:
        return False
    return True


def events_in_window(events: list[Event], x0: pd.Timestamp | None,
                     x1: pd.Timestamp | None) -> list[Event]:
    """Events, die den Zeitausschnitt [x0, x1] berühren (None = unbegrenzt)."""
    if x0 is None and x1 is None:
        return events
    return [e for e in events if overlaps(e.start, e.end, x0, x1)]


def metrics(s: Session, events: list[Event],
            x0: pd.Timestamp | None = None, x1: pd.Timestamp | None = None) -> dict:
    """Kennzahlen für die Karten-Zeile, optional auf ein Zeitfenster [x0, x1] beschränkt.

    ``events`` sollten für Fenster-Kennzahlen bereits gefiltert übergeben werden
    (siehe :func:`events_in_window`); ``crest_count`` zählt schlicht diese Liste.
    """
    windowed = x0 is not None or x1 is not None
    df = s.df.loc[x0:x1] if windowed else s.df  # .loc mit None = offenes Ende
    main = df["All 3D"].dropna()
    ch4 = df["All CH4"].dropna()
    if windowed:
        # Dauer aus den (angezeigten) Fenstergrenzen, NICHT aus den Datenpunkten:
        # ein Fenster in einer Datenlücke/zwischen zwei Samples ist leer und dürfte
        # sonst auf die volle Aufzeichnungsdauer zurückfallen.
        lo = x0 if x0 is not None else s.start
        hi = x1 if x1 is not None else s.end
        duration = (hi - lo).total_seconds() if lo is not None and hi is not None else 0.0
    else:
        duration = s.duration_s
    n_crest = sum(1 for e in events if e.type == "crest")
    n_main = len(main)
    # 95. Perzentil nur bei genügend Punkten sinnvoll – sonst degeneriert es zum Max
    # (z. B. wenn man in eine einzelne Spitze zoomt) und wäre irreführend als „Perzentil".
    p95 = float(main.quantile(0.95)) if n_main >= P95_MIN_SAMPLES else float("nan")
    return {
        "duration_s": duration,
        "main_max": float(main.max()) if n_main else float("nan"),
        # Für die Magnetfeld-Bewertung nach SBM-2015 (Langzeit): kurze Spitzen sollen
        # die Bewertung nicht dominieren. NaN, wenn zu wenige Punkte (siehe P95_MIN_SAMPLES).
        "main_p95": p95,
        "main_n": n_main,
        "main_unit": s.main_unit,
        "crest_count": n_crest,
        "ch4_max": float(ch4.max()) if not ch4.empty else float("nan"),
        "ch4_unit": s.ch4_unit,
        "windowed": windowed,
    }
