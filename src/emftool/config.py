"""Konfigurierbare Schwellen & Analyse-Parameter.

Die Feld-Schwellen sind die offiziellen baubiologischen Richtwerte **SBM-2015** für
Schlafbereiche (© Baubiologie Maes / Institut für Baubiologie + Nachhaltigkeit IBN,
www.baubiologie.de), vierstufig:
unauffällig < schwach auffällig < stark auffällig < extrem auffällig.
Ein Tupel ``(schwach_ab, stark_ab, extrem_ab)`` gibt die jeweils untere Stufengrenze an.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Elektrische Wechselfelder (Niederfrequenz) haben ZWEI Messarten mit unterschiedlichen
# Richtwerten. Das NFA1000 misst im E3D-Modus potentialfrei (Standardfall dieses Tools);
# mit gestecktem Erdungskabel misst es erdbezogen.
SBM_EFIELD = {
    "potential_free": (0.3, 1.5, 10.0),   # potentialfrei, V/m TRMS (NFA1000 E3D)
    "ground": (1.0, 5.0, 50.0),           # erdbezogen, V/m TRMS
}

# Übrige Feldarten nach Einheit.
SBM_ZONES = {
    "nT": (20.0, 100.0, 500.0),           # magnetische Wechselfelder, Flussdichte
    "mG": (0.2, 1.0, 5.0),                # dieselben Werte in Milligauss
    "µW/m²": (0.1, 10.0, 1000.0),         # elektromagn. Wellen HF, Leistungsflussdichte (Spitzenwert)
    "mV_body": (10.0, 100.0, 1000.0),     # Körperspannung erdbezogen (Hand-Sonde), mV RMS
}

# Rausch-Floor je Einheit – KEINE SBM-Werte, sondern reine Störunterdrückung: ein
# Crest-Event wird nur gemeldet, wenn der Peak diesen Wert erreicht (verhindert
# Fehlalarme durch winzige Ausschläge knapp über dem Grundrauschen). Frei einstellbar.
CREST_FLOOR = {"V/m": 1.0, "nT": 20.0, "µW/m²": 0.1}


@dataclass
class AnalysisConfig:
    crest_ratio: float = 4.0          # Peak/Average-Schwelle
    crest_window: str = "10s"         # gleitendes Zeitfenster
    # Zeitzone, in der das NFA1000 lief (IANA-Name). None = lokale Maschinen-TZ.
    # Auf Maschinen mit anderer TZ (Server, TZ=UTC) zwingend setzen, sonst werden
    # Audionotizen still falschen Sessions zugeordnet.
    device_tz: str | None = None
    min_event_s: float = 0.5          # Mindestdauer eines Events
    spectrogram_bins: int = 260       # Zeitauflösung des Band-Spektrogramms
    plot_max_points: int = 3000       # Downsampling der Zeitreihen fürs Rendering
    # E-Feld-Bezug für die V/m-Bewertung: "potential_free" (NFA1000 E3D, Default)
    # oder "ground" (Messung mit Erdungskabel gegen Erdpotential).
    efield_reference: str = "potential_free"
    sbm_efield: dict = field(default_factory=lambda: dict(SBM_EFIELD))
    sbm_zones: dict = field(default_factory=lambda: dict(SBM_ZONES))
    crest_floor: dict = field(default_factory=lambda: dict(CREST_FLOOR))

    def zones(self, unit: str) -> tuple[float, float, float]:
        """SBM-Stufengrenzen (schwach_ab, stark_ab, extrem_ab) für eine Einheit."""
        if unit == "V/m":
            return self.sbm_efield.get(self.efield_reference, self.sbm_efield["potential_free"])
        return self.sbm_zones.get(unit, (float("inf"),) * 3)

    def floor(self, unit: str) -> float:
        return self.crest_floor.get(unit, 0.0)
