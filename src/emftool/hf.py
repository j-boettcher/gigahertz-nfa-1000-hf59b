"""HF-Analyser (HF59B / HFW59D) an CH4: Umrechnung mV→µW/m²→mV/m + Einstellungs-Persistenz.

Der HF-Analyser gibt eine DC-Spannung proportional zur Leistungsflussdichte aus, die das
NFA1000 auf CH4 in mV loggt. Ohne die am HF-Gerät eingestellten Werte (Messbereich +
DC-Out-Vollausschlag) ist der mV-Wert nicht interpretierbar – das NFA1000 speichert sie
nicht mit. Diese Werte werden daher pro Messung separat gespeichert.

Umrechnung (linear, deckungsgleich mit dem Manual-Beispiel „grob + 1 V → bis ~30.000 µW/m²",
da das NFA1000 bis 1500 mV = 1,5× Vollausschlag loggt):

    power[µW/m²] = CH4[mV] / (DC_out[V] · 1000) · Vollausschlag[µW/m²]
    E[mV/m]      = sqrt(power · 1e-6 · Z0) · 1000     (Fernfeld, Z0 = 376,73 Ω)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

# Messbereiche des HF59B/D: Schlüssel -> (Vollausschlag in µW/m², Anzeige-Label).
RANGES: dict[str, tuple[float, str]] = {
    "coarse": (19990.0, "grob (max): bis 19.990 µW/m²"),
    "medium": (199.9, "mittel (med): bis 199,9 µW/m²"),
    "fine": (19.99, "fein (min): bis 19,99 µW/m²"),
}
DC_OUT_OPTIONS = ("1", "2")          # Vollausschlag-Spannung in V (als String für Dropdowns)
FREE_SPACE_IMPEDANCE = 376.730313    # Ω, Wellenwiderstand des freien Raums

# Zubehör am HF-Gerät: fester Multiplikator auf den eingestellten Messbereich.
# HV10 Verstärker: +10 dB = 10-fach empfindlicher → Bereich ×0,1 (misst schwächere Felder).
# DG20 Dämpfer: 20 dB = Faktor 100 → Bereich ×100 (misst stärkere Felder, bis ~2.000.000 µW/m²).
# (Quelle: Gigahertz Solutions / Safe Living Technologies. DG20 = ×100, NICHT ×20!)
ACCESSORIES: dict[str, tuple[float, str]] = {
    "none": (1.0, "Ohne Zubehör"),
    "hv10": (0.1, "Verstärker HV10 (×0,1)"),
    "dg20": (100.0, "Dämpfer DG20 (×100)"),
}


def accessory_mult(accessory: str) -> float:
    return ACCESSORIES.get(accessory, ACCESSORIES["none"])[0]


def power_factor(range_key: str, dc_out_v: float, accessory: str = "none") -> float:
    """Linearer Faktor k mit power[µW/m²] = ch4_mv · k (inkl. Zubehör-Multiplikator)."""
    return RANGES[range_key][0] * accessory_mult(accessory) / (dc_out_v * 1000.0)


def to_power_uwm2(ch4_mv: float, range_key: str, dc_out_v: float, accessory: str = "none") -> float:
    """CH4-Spannung (mV) → Leistungsflussdichte (µW/m²), inkl. Zubehör (HV10/DG20)."""
    return ch4_mv * power_factor(range_key, dc_out_v, accessory)


def power_to_field_mvm(power_uwm2):
    """Leistungsflussdichte (µW/m²) → E-Feldstärke (mV/m), Fernfeld E = √(S·Z0)."""
    s = np.maximum(np.asarray(power_uwm2, dtype=float), 0.0) * 1e-6  # W/m²
    return np.sqrt(s * FREE_SPACE_IMPEDANCE) * 1000.0


# ---- Persistenz (eine JSON-Datei, je Messung/Session ein Eintrag) ---------
def _valid_entry(v) -> bool:
    """Ein Eintrag ist gültig, wenn er ein dict mit bekanntem range + gültigem dc_out ist."""
    return (isinstance(v, dict)
            and v.get("range") in {*RANGES, "none"}
            and str(v.get("dc_out")) in DC_OUT_OPTIONS)


def load_settings(path: str | Path) -> dict:
    """HF-Einstellungen laden; defekte/teilweise Einträge (z. B. Hand-Edit) werden verworfen."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: {"range": v["range"], "dc_out": str(v["dc_out"]),
                # accessory optional (Rückwärtskompatibilität: alte Einträge → "none")
                "accessory": v["accessory"] if v.get("accessory") in ACCESSORIES else "none"}
            for k, v in data.items() if _valid_entry(v)}


def save_settings(path: str | Path, settings: dict) -> None:
    tmp = Path(path).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomarer Ersatz
