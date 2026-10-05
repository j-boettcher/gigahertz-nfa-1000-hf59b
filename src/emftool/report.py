"""PDF-Messprotokoll für eine NFA1000-Aufzeichnung.

Erzeugt ein mehrseitiges PDF (Kopf + Bewertung, Diagramme, Auffälligkeiten, Audionotizen)
mit matplotlib ``PdfPages`` – rein Python (keine System-Abhängigkeiten wie WeasyPrint) und
über die objektorientierte ``Figure``-API thread-sicher (kein globaler pyplot-Zustand).
"""

from __future__ import annotations

import io

import matplotlib.dates as mdates
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from . import analysis
from .log_txt import BAND_COLS, Session

A4 = (8.27, 11.69)  # Zoll, Hochformat
COL = {"text": "#20201e", "muted": "#77756e", "danger": "#a32d2d",
       "warning": "#854f0b", "ok": "#3b6d11", "accent": "#185fa5"}
BAND_STYLE = {"All 3D": ("#5f5e5a", 2.0), "50/60Hz": ("#378add", 1.1),
              ">2kHz": ("#7f77dd", 1.1), "16.7Hz": ("#1d9e75", 1.1)}
# grün / gelb / rot / violett (Füllfarben der SBM-Zonen)
SBM_FILL = ["#639922", "#ba7517", "#a32d2d", "#7f77dd"]


def _de(x, nd=1) -> str:
    if x != x:  # NaN
        return "–"
    return f"{x:.{nd}f}".replace(".", ",")


def _fmt_dur(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} m" if h else f"{m} m {s:02d} s"


def _sbm_cat(value, zones):
    if value != value:
        return None, COL["muted"]
    gelb, rot, viol = zones
    if value >= viol:
        return "extrem auffällig", COL["danger"]
    if value >= rot:
        return "stark auffällig", COL["danger"]
    if value >= gelb:
        return "schwach auffällig", COL["warning"]
    return "unauffällig", COL["ok"]


def _sbm_zones(ax, zones, top, bottom=0.0):
    lo = bottom
    for i, hi in enumerate([*zones, top]):
        hi = min(hi, top)
        if lo < hi:
            ax.axhspan(lo, hi, color=SBM_FILL[i], alpha=0.10, lw=0, zorder=0)
        lo = hi


def _timefmt(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    for lbl in ax.get_xticklabels():
        lbl.set_fontsize(7)
    for lbl in ax.get_yticklabels():
        lbl.set_fontsize(7)


# ---- Seite 1: Kopf, Bewertung, Auffälligkeiten, Notizen -------------------
def _page_summary(pdf, s, events, m, notes, cfg, location=""):
    fig = Figure(figsize=A4)
    y = [0.965]

    def line(txt, size=9, color=COL["text"], weight="normal", dy=0.021, x=0.07):
        fig.text(x, y[0], txt, fontsize=size, color=color, weight=weight, va="top")
        y[0] -= dy

    line("EMF-Messprotokoll", size=20, weight="bold", dy=0.036)
    if location:
        line(f"Messort: {location}", size=12, weight="bold", color=COL["accent"], dy=0.026)
    line(f"NFA1000 · {s.label()}", size=11, color=COL["muted"], dy=0.020)
    line(f"Gerät: {s.meta[:70]}", size=7.5, color=COL["muted"], dy=0.015)
    line("Bewertung nach SBM-2015 (Baubiologie Maes / IBN)", size=7.5, color=COL["muted"], dy=0.032)

    # Bewertung Hauptfeld: Magnetfeld = 95. Perzentil, sonst Max
    if m["main_unit"] == "nT" and m["main_p95"] == m["main_p95"]:
        val, basis = m["main_p95"], "95. Perzentil"
        extra = f"  (Max {_de(m['main_max'])} {m['main_unit']})"
    else:
        val, basis = m["main_max"], "Max"
        extra = ""
    cat, ccol = _sbm_cat(val, cfg.zones(m["main_unit"]))
    line("Bewertung", size=12, weight="bold", dy=0.026)
    line(f"{s.kind}  ·  {basis}: {_de(val)} {m['main_unit']}{extra}", size=10, dy=0.020)
    line(f"SBM: {cat}" if cat else "SBM: –", size=11, color=ccol, weight="bold", dy=0.030)

    # Kennzahlen
    line("Kennzahlen", size=12, weight="bold", dy=0.024)
    n_crest = sum(1 for e in events if e.type == "crest")
    for k, v in [("Messdauer", _fmt_dur(m["duration_s"])),
                 ("Crest-Events ≥ 4×", str(n_crest)),
                 (f"CH4 max", f"{_de(m['ch4_max'])} {m['ch4_unit']}")]:
        fig.text(0.09, y[0], k, fontsize=9, color=COL["muted"], va="top")
        fig.text(0.42, y[0], v, fontsize=9, color=COL["text"], va="top")
        y[0] -= 0.019
    y[0] -= 0.012

    # Auffälligkeiten
    line("Auffälligkeiten (Top 15)", size=12, weight="bold", dy=0.024)
    if not events:
        line("Keine Auffälligkeiten.", size=9, color=COL["muted"])
    for e in events[:15]:
        dgr = e.severity == "danger"
        if e.type == "crest":
            desc = f"Crest {_de(e.ratio)}×  ·  {e.channel}  ·  {_de(e.peak)} {e.unit}"
        else:
            desc = f"Feldspitze  ·  {e.channel}  ·  {_de(e.peak)} {e.unit}"
        fig.text(0.09, y[0], e.start.strftime("%H:%M:%S"), fontsize=8, color=COL["muted"], va="top")
        fig.text(0.22, y[0], desc, fontsize=8, color=COL["danger"] if dgr else COL["warning"], va="top")
        fig.text(0.86, y[0], f"{e.duration_s:.1f} s", fontsize=8, color=COL["muted"], va="top")
        y[0] -= 0.0165

    # Audionotizen
    y[0] -= 0.012
    line("Audionotizen", size=12, weight="bold", dy=0.024)
    if not notes:
        line("Keine Audionotizen.", size=9, color=COL["muted"])
    for n in notes[:12]:
        fig.text(0.09, y[0], n.start.strftime("%H:%M:%S"), fontsize=8, color=COL["muted"], va="top")
        fig.text(0.22, y[0], f"{n.path.stem}  ({n.duration_s:.1f} s)", fontsize=8, va="top")
        y[0] -= 0.0165

    pdf.savefig(fig)


# ---- Seite 2: Diagramme ---------------------------------------------------
def _field_ax(ax, s, events, cfg):
    unit = s.main_unit
    ymax = 1.0
    for ch, (color, lw) in BAND_STYLE.items():
        ser = analysis.downsample(s.df[ch], cfg.plot_max_points)
        if ser.empty:
            continue
        ymax = max(ymax, float(ser.max()))
        ax.plot(ser.index, ser.values, color=color, lw=lw, label=ch)
    gelb, rot, viol = cfg.zones(unit)
    if np.isfinite(gelb):                       # SBM-Zonen nur bei bekannter Einheit (nicht bei '?'/'mV')
        top = max(ymax * 1.1, gelb * 1.3)
        _sbm_zones(ax, (gelb, rot, viol), top)
    else:
        top = ymax * 1.1
    cr = [e for e in events if e.type == "crest" and e.unit == unit][:15]
    if cr:
        ax.scatter([e.start for e in cr], [e.peak for e in cr], c=COL["danger"],
                   marker="x", s=26, zorder=5, label="Crest ≥ 4×")
    ax.set_ylim(0, top)
    ax.set_ylabel(unit, fontsize=8)
    ax.set_title(f"Frequenzbänder über Zeit · {unit}", fontsize=10, loc="left")
    if ax.get_legend_handles_labels()[0]:       # Legende nur, wenn es beschriftete Kurven gibt
        ax.legend(loc="upper right", fontsize=6.5, ncol=5, framealpha=0.9)
    ax.grid(True, color="#e6e4dd", lw=0.5)
    _timefmt(ax)


def _spectro_ax(ax, s, cfg, fig):
    z, x, y = analysis.spectrogram(s, cfg)
    ax.set_title("Band-Spektrogramm", fontsize=10, loc="left")
    if len(x) < 2:
        ax.text(0.5, 0.5, "zu wenige Daten", ha="center", transform=ax.transAxes, color=COL["muted"])
        return
    xn = mdates.date2num(list(x))
    im = ax.imshow(z, aspect="auto", cmap="Blues", origin="upper",
                   extent=[xn[0], xn[-1], len(y), 0])
    ax.set_yticks([i + 0.5 for i in range(len(y))])
    ax.set_yticklabels(y, fontsize=7)
    ax.xaxis_date()
    fig.colorbar(im, ax=ax, label=s.main_unit, fraction=0.03, pad=0.01)
    _timefmt(ax)


def _ch4_ax(ax, s, cfg, hf, hf_setting):
    range_key, dcout, acc = hf_setting
    factor = hf.power_factor(range_key, float(dcout), acc)
    power = analysis.downsample(s.df["All CH4"] * factor, cfg.plot_max_points)
    if not power.empty:
        ax.plot(power.index, power.values, color="#7f77dd", lw=1.1)
    gelb, rot, viol = cfg.zones("µW/m²")
    ymax = float(power.max()) if not power.empty else viol
    bottom, top = 0.01, max(ymax * 1.4, viol * 1.5)
    ax.set_yscale("log")
    _sbm_zones(ax, (gelb, rot, viol), top, bottom=bottom)
    ax.set_ylim(bottom, top)
    ax.set_ylabel("µW/m²", fontsize=8)
    ax.set_title("HF-Signal (CH4) über Zeit · µW/m² (log)", fontsize=10, loc="left")
    ax.grid(True, color="#e6e4dd", lw=0.5)
    _timefmt(ax)


def _ch4_raw_ax(ax, s, cfg):
    """CH4 als nativer 4. NFA-Kanal (ohne HF) in seiner Einheit – z. B. das bei einer
    Magnetfeldmessung mitgeloggte E-Feld (V/m). Entspricht dem Umschalter 'CH4 (…)'."""
    unit = s.ch4_unit
    ser = analysis.downsample(s.df["All CH4"], cfg.plot_max_points)
    if not ser.empty:
        ax.plot(ser.index, ser.values, color="#5f5e5a", lw=1.2)
    gelb, rot, viol = cfg.zones(unit)
    if np.isfinite(gelb):                       # SBM-Zonen nur bei bekannter Einheit (nicht mV/'?')
        ymax = float(ser.max()) if not ser.empty else 1.0
        top = max(ymax * 1.1, gelb * 1.3)
        _sbm_zones(ax, (gelb, rot, viol), top)
        ax.set_ylim(0, top)
    ax.set_ylabel(unit, fontsize=8)
    ax.set_title(f"CH4 (4. NFA-Kanal) über Zeit · {unit}", fontsize=10, loc="left")
    ax.grid(True, color="#e6e4dd", lw=0.5)
    _timefmt(ax)


def _page_charts(pdf, s, events, cfg, hf, hf_setting):
    # Immer BEIDE Diagramme aufnehmen: Feld (CH1–3) und CH4 – als HF (µW/m²) falls HF
    # gesetzt, sonst in nativer Einheit (V/m/nT). Das ist genau das per Umschalter
    # wählbare zweite Diagramm; CH4 enthält immer Messergebnisse.
    fig = Figure(figsize=A4)
    fig.subplots_adjust(left=0.10, right=0.93, top=0.95, bottom=0.06, hspace=0.35)
    axes = fig.subplots(3, 1)
    _field_ax(axes[0], s, events, cfg)
    _spectro_ax(axes[1], s, cfg, fig)
    if hf_setting is not None:
        _ch4_ax(axes[2], s, cfg, hf, hf_setting)
    else:
        _ch4_raw_ax(axes[2], s, cfg)
    pdf.savefig(fig)


def build_pdf(s: Session, events: list, m: dict, notes: list, cfg,
              hf=None, hf_setting=None, location="") -> bytes:
    """Baut das PDF-Protokoll und gibt die Bytes zurück."""
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        _page_summary(pdf, s, events, m, notes, cfg, location)
        _page_charts(pdf, s, events, cfg, hf, hf_setting)
    return buf.getvalue()
