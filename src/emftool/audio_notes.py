"""Audionotizen (``REC*.WAV``) des NFA1000: Discovery, Session-Zuordnung, Browser-Audio.

Die WAVs enthalten keinen Zeitstempel (nur RIFF/fmt/data; 8-bit mono PCM, 17875 Hz).
Einzige Zeitquelle ist die Datei-mtime = Zeitpunkt des Datei-Schließens = Ende der
Aufnahme. Der Aufnahme-Start ergibt sich aus mtime minus Dauer (Dauer aus dem
fmt-Chunk / der Datenlänge). Zugeordnet wird eine Notiz der Session, in deren
Zeitfenster (± Toleranz) ihr Startzeitpunkt fällt – Notizen werden laut Manual auch
kurz vor/nach dem Logging diktiert (z. B. um HF-Settings festzuhalten).
"""

from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .log_txt import Session

# Notizen bis zu dieser Toleranz vor Log-Start / nach Log-Ende zählen zur Session.
ASSIGN_TOLERANCE_S = 120.0
# Länger ist keine plausible Sprachnotiz mehr → Datei gilt als defekt (kaputter Header).
MAX_NOTE_DURATION_S = 600.0


@dataclass
class Note:
    path: Path
    start: pd.Timestamp      # Aufnahme-Beginn (mtime - Dauer)
    end: pd.Timestamp        # Aufnahme-Ende (Datei-mtime)
    duration_s: float

    @property
    def name(self) -> str:
        return self.path.name


def _wav_duration_s(path: Path) -> float | None:
    """Dauer über das wave-Modul; ``None`` = Datei ist kein abspielbares WAV.

    Bewusst kein Größen-Fallback: eine Datei, die ``wave`` nicht lesen kann, kann
    auch ``wav_bytes_16bit`` nicht ausliefern – sie als Notiz zu listen erzeugte
    nur tote Player und falsche Marker.
    """
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            if rate <= 0:
                return None
            dur = w.getnframes() / rate
    except (wave.Error, EOFError, struct.error, OSError):
        return None
    # Kaputte Header können absurde Frame-Zahlen behaupten (tagelange "Notizen").
    if not (0.0 < dur <= MAX_NOTE_DURATION_S):
        return None
    return dur


def scan(folder: str | Path, device_tz: str | None = None) -> list[Note]:
    """Alle ``REC*.WAV`` eines Ordners als zeitlich sortierte Notizen.

    ``device_tz`` (IANA-Name, z. B. ``"Europe/Zurich"``): Zeitzone, in der das
    NFA1000 lief. Default ``None`` = lokale Zeitzone dieser Maschine – korrekt,
    solange Auswertung und Messung in derselben Zone stattfinden. Auf Maschinen
    mit anderer TZ (Server, TZ=UTC) MUSS die Gerätezone gesetzt werden, sonst
    werden Notizen still falschen Sessions zugeordnet.
    """
    folder = Path(folder)
    tz = ZoneInfo(device_tz) if device_tz else None
    notes: list[Note] = []
    for p in sorted(folder.glob("REC*.WAV")):
        try:
            dur = _wav_duration_s(p)
            if dur is None:
                continue
            # mtime als Wanduhrzeit der GERÄTE-Zone rendern (LOG-Zeitstempel sind
            # naive Gerätezeit) – nicht blind die Maschinen-TZ nehmen.
            local = datetime.fromtimestamp(p.stat().st_mtime, tz).replace(tzinfo=None)
            end = pd.Timestamp(local).round("s")
            note = Note(p, (end - pd.Timedelta(seconds=dur)).round("s"), end, dur)
        except Exception:  # defekte Datei/mtime soll den Scan nicht abbrechen
            continue
        notes.append(note)
    notes.sort(key=lambda n: n.start)
    return notes


def assign(notes: list[Note], sessions: list[Session],
           tolerance_s: float = ASSIGN_TOLERANCE_S) -> dict[str, list[Note]]:
    """Ordnet jede Notiz höchstens einer Session zu (kleinste zeitliche Distanz).

    Distanz 0 = Notiz liegt im Aufzeichnungsfenster; sonst Abstand zum Fensterrand.
    Notizen ohne Session innerhalb der Toleranz bleiben unzugeordnet.
    """
    out: dict[str, list[Note]] = {s.name: [] for s in sessions}
    windows = [s for s in sessions if s.n >= 2]
    for note in notes:
        best: tuple[float, Session] | None = None
        for s in windows:
            t0, t1 = s.start, s.end
            if t0 <= note.start <= t1:
                dist = 0.0
            else:
                dist = min(abs((note.start - t1).total_seconds()),
                           abs((t0 - note.start).total_seconds()))
            if dist <= tolerance_s and (best is None or dist < best[0]):
                best = (dist, s)
        if best is not None:
            out[best[1].name].append(note)
    return out


def wav_bytes_16bit(path: str | Path) -> bytes:
    """WAV als 16-bit PCM für maximale Browser-Kompatibilität re-kodieren.

    Die Geräte-WAVs sind 8-bit unsigned PCM; einige Player/Browser sind damit
    wählerisch. 8-bit → 16-bit ist verlustfrei ((x-128) << 8).
    """
    with wave.open(str(path), "rb") as w:
        rate, nch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 1:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
        pcm16 = ((samples - 128) << 8).astype("<i2").tobytes()
    elif width == 2:
        pcm16 = raw
    else:  # exotische Breiten unverändert ausliefern
        return Path(path).read_bytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(nch)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm16)
    return buf.getvalue()
