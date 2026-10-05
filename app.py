"""EMF Analyzer – Dash-Dashboard für NFA1000 / HF59B / HFW59D Logs.

Start:  uv run python app.py   →  http://127.0.0.1:8050
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import io
import json
import math
import threading

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, Patch, State, dcc, html
from dash.exceptions import PreventUpdate
from flask import abort, send_file

from emftool import analysis, audio_notes, hf, log_txt, point_meas
from emftool.config import AnalysisConfig

SAMPLES = Path(__file__).parent / "samples"
HF_SETTINGS_FILE = Path(__file__).parent / "hf_settings.json"
LOCATIONS_FILE = Path(__file__).parent / "locations.json"   # {session_name: Messort}
PARSE_CACHE = Path(__file__).parent / ".cache" / "logs"      # geparste LOG-Dateien (Pickle)
cfg = AnalysisConfig()

# ---- Farben / Theme -------------------------------------------------------
C = {
    "surface2": "#ffffff", "surface1": "#f6f5f1", "page": "#faf9f6",
    "border": "#e6e4dd", "text": "#20201e", "muted": "#77756e",
    "danger": "#a32d2d", "danger_bg": "#fcebeb",
    "warning": "#854f0b", "warning_bg": "#faeeda",
    "ok": "#3b6d11", "accent": "#185fa5",
}
BAND_STYLE = {  # Kanal -> (Farbe, Breite)
    "All 3D": ("#5f5e5a", 2.4),
    "50/60Hz": ("#378add", 1.6),
    ">2kHz": ("#7f77dd", 1.6),
    "16.7Hz": ("#1d9e75", 1.6),
}
SBM_FILL = ["rgba(99,153,34,0.10)", "rgba(186,117,23,0.13)",
            "rgba(163,45,45,0.13)", "rgba(127,71,221,0.13)"]

# ---- Daten laden (inkrementell bei jedem Reload) --------------------------
# Der Vollscan aller LOG*.TXT kostet ~19 s (volle DataFrames). Deshalb wird bei einem Reload
# nur der Dateibestand geprüft und ausschließlich NEUE/geänderte LOG-Dateien werden geparst;
# Notizen/Punktmessungen/JSON-Einstellungen sind billig und werden komplett neu gescannt.
# So erscheinen neu in samples/ abgelegte Dateien nach einem Seiten-Reload.
SESSIONS: list = []
BY_NAME: dict = {}
NOTES: list = []
NOTES_BY_SESSION: dict = {}
NOTE_FILES: set = set()
CH4_HAS_DATA: dict = {}          # hat CH4 (nicht-null) Daten? → Feld/CH4-Umschalter
POINT_MEAS: list = []            # 9-/6-Punkt-Messungen (.9PM/.6PM)
PM_BY_NAME: dict = {}            # Key = uid (Dateiname inkl. Endung; Stem kollidiert)
HF_SETTINGS: dict = {}
LOCATIONS: dict = {}
PM_BANDS = ["All 3D"] + list(log_txt.BAND_COLS)  # Auswahl für die frequenzspezifische Heatmap
# SBM-Stufenfarben (0..3: unauffällig/schwach/stark/extrem) für die Punktmessungs-Heatmap.
PM_COLORS = ["#a8c97e", "#e6c766", "#d47a7a", "#a98fd6"]
PM_CAT_LABELS = ["unauffällig", "schwach", "stark", "extrem"]
_cache: dict[str, list] = {}          # Events je Session (lazy)
_ch4_cache: dict[tuple, list] = {}    # CH4-Crest je (name, range, dcout, accessory)
_file_mtime: dict[str, float] = {}    # Session-Name → mtime der zuletzt geladenen LOG-Datei
_scan_lock = threading.Lock()


def _load_locations() -> dict:
    try:
        raw = json.loads(LOCATIONS_FILE.read_text(encoding="utf-8")) if LOCATIONS_FILE.exists() else {}
        return {k: str(v) for k, v in raw.items() if isinstance(v, str)} if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _ch4_has_data(s) -> bool:
    return bool(s.df["All CH4"].notna().any() and (s.df["All CH4"].abs().max() or 0) > 0)


def _drop_caches(name: str) -> None:
    _cache.pop(name, None)
    # list() snapshottet die Keys atomar (C-Ebene, kein GIL-Release) – die Comprehension über
    # den dict selbst würde crashen, wenn ch4_events_for (anderer Tab/Thread) parallel einfügt
    # ("dictionary changed size during iteration").
    for k in [k for k in list(_ch4_cache) if k[0] == name]:
        _ch4_cache.pop(k, None)


def refresh_data() -> None:
    """samples/ (neu) einlesen. Nur neue/geänderte LOG-Dateien werden geparst (Vollscan teuer),
    der Rest komplett neu. Läuft beim Start und bei jedem Seiten-Reload (on_page_load).

    Baut NEUE Dicts/Listen und tauscht die Globals atomar aus (statt in-place zu mutieren),
    damit nebenläufige Render-Callbacks immer einen konsistenten Snapshot sehen (kein KeyError,
    wenn parallel eine Datei entfernt wird). `_file_mtime`/Caches werden nur unter Lock berührt."""
    global SESSIONS, BY_NAME, CH4_HAS_DATA, NOTES, NOTES_BY_SESSION, NOTE_FILES
    global POINT_MEAS, PM_BY_NAME, HF_SETTINGS, LOCATIONS
    with _scan_lock:
        current: dict[str, float] = {}
        for p in sorted(SAMPLES.glob("LOG*.TXT")):
            try:
                current[p.stem] = p.stat().st_mtime
            except OSError:
                continue
        old, by_name, ch4 = BY_NAME, {}, {}
        for name, mt in current.items():
            s = old.get(name)
            if not (s is not None and _file_mtime.get(name) == mt):   # neu oder geändert
                _drop_caches(name)                                    # Events/CH4-Cache verwerfen
                try:
                    s = log_txt.load_cached(SAMPLES / f"{name}.TXT", PARSE_CACHE)
                except Exception:  # noqa: BLE001 - defekte Datei überspringen, nicht crashen
                    continue
                if s.n < 2:                                           # zu kurz → nicht aufnehmen
                    continue
                _file_mtime[name] = mt
            by_name[name] = s
            ch4[name] = _ch4_has_data(s)
        for name in list(_file_mtime):                # mtime/Caches für Verschwundenes vergessen
            if name not in by_name:
                _file_mtime.pop(name, None)
                _drop_caches(name)
        BY_NAME, CH4_HAS_DATA = by_name, ch4          # atomarer Swap
        SESSIONS = [by_name[n] for n in sorted(by_name)]
        NOTES = audio_notes.scan(SAMPLES, device_tz=cfg.device_tz)
        NOTES_BY_SESSION = audio_notes.assign(NOTES, SESSIONS)
        NOTE_FILES = {n.name for n in NOTES}
        POINT_MEAS = point_meas.scan(SAMPLES)
        PM_BY_NAME = {pm.uid: pm for pm in POINT_MEAS}
        HF_SETTINGS = hf.load_settings(HF_SETTINGS_FILE)
        LOCATIONS = _load_locations()


refresh_data()   # Erst-Scan beim Start (danach inkrementell bei jedem Reload)


def events_for(name: str) -> list:
    """Auffälligkeiten des Hauptfelds (All 3D) – unabhängig von HF-Einstellungen."""
    if name not in _cache:
        _cache[name] = analysis.all_events(BY_NAME[name], cfg)
    return _cache[name]


def ch4_events_for(name: str, hf_range, hf_dcout, accessory="none") -> list:
    """Crest-Events auf dem nach µW/m² umgerechneten CH4 (nur wenn HF gesetzt).

    Crest ist skaleninvariant, aber der Rausch-Floor gilt in µW/m² – daher CH4 vor der
    Analyse linear umrechnen (Faktor inkl. Zubehör-Multiplikator HV10/DG20)."""
    if not hf_range or hf_range == "none":
        return []
    key = (name, hf_range, hf_dcout, accessory)
    if key not in _ch4_cache:
        s = BY_NAME[name]
        power = s.df["All CH4"] * hf.power_factor(hf_range, float(hf_dcout), accessory)
        _ch4_cache[key] = analysis.crest_on_series(
            power, cfg, "µW/m²", "CH4 (HF)", cfg.floor("µW/m²"))
    return _ch4_cache[key]


def _default() -> str:
    for pref in ("LOG00005", "LOG00003", "LOG00033"):
        if pref in BY_NAME:
            return pref
    return SESSIONS[0].name if SESSIONS else ""


# ---- App ------------------------------------------------------------------
app = Dash(__name__, external_stylesheets=[
    "https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.24.0/dist/tabler-icons.min.css",
])
app.title = "EMF Analyzer"


@app.server.route("/audio/<name>")
def serve_audio(name: str):
    """Audionotiz als 16-bit-WAV ausliefern (Whitelist: nur gescannte REC*.WAV).

    ``send_file(conditional=True)`` liefert Accept-Ranges/206 (Safari verlangt
    Byte-Range-Support für Media) und Last-Modified/ETag zur Revalidierung –
    wichtig, weil REC-Nummern nach einem Karten-Wechsel wieder von vorn beginnen
    und derselbe Name dann eine andere Aufnahme bezeichnet.
    """
    if name not in NOTE_FILES:
        abort(404)
    path = SAMPLES / name
    try:
        data = audio_notes.wav_bytes_16bit(path)
        mtime = path.stat().st_mtime
    except Exception:  # defekte/fehlende Datei → 404 statt 500
        abort(404)
    return send_file(io.BytesIO(data), mimetype="audio/wav", conditional=True,
                     last_modified=mtime, download_name=name,
                     etag=f"{name}-{int(mtime)}-{len(data)}")

app.index_string = """<!DOCTYPE html><html><head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
 body{margin:0;background:""" + C["page"] + """;color:""" + C["text"] + """;
   font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}
 .wrap{max-width:1100px;margin:0 auto;padding:20px;}
 .card{background:""" + C["surface2"] + """;border:1px solid """ + C["border"] + """;
   border-radius:14px;padding:16px 20px;margin-bottom:16px;}
 .ch{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:10px;}
 .ch h2{font-size:16px;font-weight:500;margin:0;}
 .ch .sub{font-size:12px;color:""" + C["muted"] + """;}
 .metric{background:""" + C["surface1"] + """;border-radius:10px;padding:14px 16px;}
 .metric .lbl{font-size:13px;color:""" + C["muted"] + """;}
 .metric .val{font-size:26px;font-weight:500;line-height:1.2;}
 .badge{font-size:12px;font-weight:500;padding:2px 10px;border-radius:999px;white-space:nowrap;}
 .erow{display:flex;align-items:center;gap:12px;padding:9px 0;border-top:1px solid """ + C["border"] + """;}
 .erow:first-child{border-top:none;}
 .Select-control,.is-focused{border-radius:8px !important;}
 .nav{display:flex;gap:4px;background:""" + C["surface1"] + """;padding:4px;border-radius:10px;}
 .nav label{display:flex;align-items:center;padding:6px 14px;border-radius:7px;font-size:13px;
   cursor:pointer;color:""" + C["muted"] + """;}
 .nav label:has(input:checked){background:""" + C["surface2"] + """;color:""" + C["text"] + """;
   box-shadow:0 1px 2px rgba(0,0,0,.08);}
 .nav input{display:none;}
 .nav i{margin-right:6px;font-size:15px;}
</style></head><body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body></html>"""


def header():
    return html.Div(style={"display": "flex", "alignItems": "center",
                           "justifyContent": "space-between", "gap": "12px",
                           "flexWrap": "wrap", "marginBottom": "16px"}, children=[
        html.Div(style={"display": "flex", "alignItems": "center", "gap": "10px"}, children=[
            html.I(className="ti ti-radar-2", style={"fontSize": "24px", "color": C["accent"]}),
            html.Div([
                html.Div("EMF Analyzer", style={"fontSize": "17px", "fontWeight": 500}),
                html.Div("NFA1000 · HF59B / HFW59D", style={"fontSize": "12px", "color": C["muted"]}),
            ]),
        ]),
        # Hauptmenü: Langzeit-Logs und Punktmessungen sind getrennte Ansichten.
        dcc.RadioItems(
            id="page", value="log", persistence=True, className="nav",
            options=[
                {"label": html.Span([html.I(className="ti ti-chart-line"), "Langzeit-Log"]),
                 "value": "log"},
                {"label": html.Span([html.I(className="ti ti-grid-dots"), "Punktmessung"]),
                 "value": "pm"},
            ],
        ),
    ])


def session_picker():
    """Auswahl der Langzeit-Aufzeichnung + Messort (nur in der Log-Ansicht)."""
    return html.Div(style={"display": "grid", "gridTemplateColumns": "minmax(0,3fr) minmax(0,2fr)",
                           "gap": "8px", "marginBottom": "12px"}, children=[
        dcc.Dropdown(
            id="session", clearable=False, value=_default(),
            options=[{"label": s.label(), "value": s.name} for s in SESSIONS],
        ),
        dcc.Input(id="location", type="text", debounce=True,
                  placeholder="Messort (z. B. Schlafzimmer, Bett Kopfende)",
                  style={"width": "100%", "padding": "7px 10px",
                         "fontSize": "13px", "borderRadius": "8px", "boxSizing": "border-box",
                         "border": "1px solid " + C["border"], "background": C["surface2"],
                         "color": C["text"]}),
    ])


app.layout = html.Div(className="wrap", children=[
    header(),
    dcc.Location(id="page-url"),   # feuert bei jedem Seiten-Load → samples/ neu einlesen
    dcc.Store(id="view"),          # {"session", "x0", "x1"} – gewählter Zeitausschnitt
    dcc.Store(id="reset-dummy"),   # Output-Senke für den clientseitigen Reset
    dcc.Store(id="hf-saved"),      # Output-Senke für die HF-Persistenz
    dcc.Store(id="loc-saved"),     # Output-Senke für die Messort-Persistenz
    dcc.Download(id="pdf-download"),
    # ---- Ansicht „Langzeit-Log“ ----
    html.Div(id="page-log", children=[
    session_picker(),
    html.Div(style={"display": "flex", "alignItems": "center",
                    "justifyContent": "space-between", "gap": "12px", "margin": "0 0 12px"},
             children=[
        html.Span(id="window-label", style={"fontSize": "13px", "color": C["muted"]}),
        html.Div(style={"display": "flex", "gap": "8px"}, children=[
            html.Button([html.I(className="ti ti-file-type-pdf",
                                style={"marginRight": "6px", "verticalAlign": "-2px"}),
                         "PDF-Report"],
                        id="pdf-btn", n_clicks=0, style={
                            "fontSize": "13px", "padding": "5px 12px", "borderRadius": "8px",
                            "border": "1px solid " + C["border"], "background": C["surface2"],
                            "color": C["text"], "cursor": "pointer"}),
            html.Button([html.I(className="ti ti-arrows-maximize",
                                style={"marginRight": "6px", "verticalAlign": "-2px"}),
                         "Ganze Aufzeichnung"],
                        id="reset-zoom", n_clicks=0, style={
                            "fontSize": "13px", "padding": "5px 12px", "borderRadius": "8px",
                            "border": "1px solid " + C["border"], "background": C["surface2"],
                            "color": C["text"], "cursor": "pointer"}),
        ]),
    ]),
    html.Div(id="cards", style={"display": "grid", "gap": "12px", "marginBottom": "16px",
                                "gridTemplateColumns": "repeat(auto-fit,minmax(150px,1fr))"}),
    html.Div(className="card", children=[
        html.Div(className="ch", children=[
            html.H2("HF-Analyser (CH4)"),
            html.Span("Einstellungen des HF59B/D · werden pro Messung gespeichert", className="sub"),
        ]),
        html.Div(style={"display": "grid", "gridTemplateColumns": "2fr 1fr 2fr", "gap": "12px",
                        "alignItems": "end"}, children=[
            html.Div([
                html.Div("Messbereich", style={"fontSize": "13px", "color": C["muted"],
                                               "marginBottom": "4px"}),
                dcc.Dropdown(id="hf-range", clearable=False, value="none", options=[
                    {"label": "— kein HF (CH4 = 4. NFA-Kanal) —", "value": "none"},
                    {"label": hf.RANGES["coarse"][1], "value": "coarse"},
                    {"label": hf.RANGES["medium"][1], "value": "medium"},
                    {"label": hf.RANGES["fine"][1], "value": "fine"},
                ]),
            ]),
            html.Div([
                html.Div("DC-Out", style={"fontSize": "13px", "color": C["muted"],
                                          "marginBottom": "4px"}),
                dcc.Dropdown(id="hf-dcout", clearable=False, value="1", options=[
                    {"label": "1 V", "value": "1"}, {"label": "2 V", "value": "2"}]),
            ]),
            html.Div([
                html.Div("Zubehör", style={"fontSize": "13px", "color": C["muted"],
                                           "marginBottom": "4px"}),
                dcc.Dropdown(id="hf-accessory", clearable=False, value="none",
                             options=[{"label": lbl, "value": k}
                                      for k, (_, lbl) in hf.ACCESSORIES.items()]),
            ]),
        ]),
        html.Div(id="hf-note", style={"fontSize": "12px", "color": C["muted"], "marginTop": "10px"}),
    ]),
    html.Div(className="card", children=[
        html.Div(className="ch", children=[
            html.H2(id="ts-title"),
            html.Span("SBM2015-Zonen · ziehen zum Zoomen, Doppelklick = zurück", className="sub"),
        ]),
        html.Div(id="ts-view-wrap", style={"display": "none", "marginBottom": "10px"}, children=[
            dcc.RadioItems(id="ts-view", value="field", inline=True,
                           options=[{"label": " Feld (CH1–3)", "value": "field"},
                                    {"label": " HF (CH4)", "value": "ch4"}],
                           labelStyle={"marginRight": "18px", "fontSize": "13px", "cursor": "pointer"},
                           inputStyle={"marginRight": "5px"}),
        ]),
        dcc.Graph(id="ts", config={"displayModeBar": False}, style={"height": "320px"}),
    ]),
    html.Div(className="card", children=[
        html.Div(className="ch", children=[
            html.H2("Band-Spektrogramm"), html.Span("welches Band wann dominiert", className="sub"),
        ]),
        dcc.Checklist(id="spectro-bands", inline=True, value=list(log_txt.BAND_COLS),
                      options=[{"label": " " + b, "value": b} for b in log_txt.BAND_COLS],
                      labelStyle={"marginRight": "14px", "fontSize": "13px", "cursor": "pointer"},
                      inputStyle={"marginRight": "5px"}, style={"marginBottom": "10px"}),
        dcc.Graph(id="spectro", config={"displayModeBar": False}, style={"height": "240px"}),
    ]),
    html.Div(className="card", children=[
        html.Div(className="ch", children=[
            html.H2("Audionotizen"), html.Span("während der Aufzeichnung diktiert", className="sub"),
        ]),
        html.Div(id="notes"),
    ]),
    html.Div(className="card", children=[
        html.Div(className="ch", children=[
            html.H2("Auffälligkeiten"), html.Span("Crest ≥ 4× · hohe Feldwerte", className="sub"),
        ]),
        html.Div(id="events"),
    ]),
    ]),
    # ---- Ansicht „Punktmessung“ ----
    html.Div(id="page-pm", style={"display": "none"}, children=[
    html.Div(className="card", children=[
        html.Div(className="ch", children=[
            html.H2("Punktmessung (9-/6-Punkt)"),
            html.Span("räumliche Feldverteilung · Schlafplatz (9-Punkt) / Arbeitsplatz (6-Punkt) · "
                      "frequenzspezifisch", className="sub"),
        ]),
        html.Div(style={"display": "grid", "gridTemplateColumns": "3fr 4fr", "gap": "12px",
                        "alignItems": "end", "marginBottom": "12px"}, children=[
            html.Div([
                html.Div("Messung", style={"fontSize": "13px", "color": C["muted"],
                                           "marginBottom": "4px"}),
                dcc.Dropdown(id="pm-select", clearable=False,
                             value=(POINT_MEAS[0].uid if POINT_MEAS else "none"),
                             options=([{"label": pm.label(), "value": pm.uid} for pm in POINT_MEAS]
                                      or [{"label": "— keine 9-/6-Punkt-Messung gefunden —",
                                           "value": "none"}])),
            ]),
            html.Div([
                html.Div("Frequenzband", style={"fontSize": "13px", "color": C["muted"],
                                                "marginBottom": "4px"}),
                dcc.RadioItems(id="pm-band", value="All 3D", inline=True,
                               options=[{"label": " " + b, "value": b} for b in PM_BANDS],
                               labelStyle={"marginRight": "12px", "fontSize": "12.5px",
                                           "cursor": "pointer"},
                               inputStyle={"marginRight": "4px"}),
            ]),
        ]),
        dcc.Graph(id="pm-heatmap", config={"displayModeBar": False}),
        html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "4px 16px",
                        "marginTop": "6px"}, children=[
            html.Span([
                html.Span(style={"display": "inline-block", "width": "12px", "height": "12px",
                                 "background": col, "borderRadius": "3px", "marginRight": "5px",
                                 "verticalAlign": "-1px"}),
                f"SBM: {lbl}"],
                style={"fontSize": "12px", "color": C["muted"]})
            for col, lbl in zip(PM_COLORS, PM_CAT_LABELS)
        ]),
    ]),
    ]),
    html.Div("Bewertung nach SBM-2015 (Baubiologie Maes / IBN) · E-Feld = potentialfrei · "
             "Magnetfeld = 95. Perzentil (Langzeit) · Schwellen in config.py einstellbar.",
             style={"fontSize": "12px", "color": C["muted"], "textAlign": "center", "padding": "4px 0 20px"}),
])


# ---- Rendering ------------------------------------------------------------
def fmt_dur(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} m" if h else f"{m} m {s:02d} s"


def de(x: float, nd: int = 1) -> str:
    if x != x:  # NaN
        return "–"
    return f"{x:.{nd}f}".replace(".", ",")


def parse_relayout(rl: dict | None):
    """Plotly-relayoutData → ("reset", None, None) | ("range", x0, x1) | None (irrelevant)."""
    if not rl:
        return None
    if rl.get("xaxis.autorange") is True or rl.get("autosize") is True:
        return ("reset", None, None)
    if "xaxis.range[0]" in rl and "xaxis.range[1]" in rl:
        return ("range", rl["xaxis.range[0]"], rl["xaxis.range[1]"])
    r = rl.get("xaxis.range")
    if isinstance(r, (list, tuple)) and len(r) == 2:
        return ("range", r[0], r[1])
    return None


def _ts(v):
    if v in (None, ""):
        return None
    try:
        return pd.Timestamp(v)
    except (ValueError, TypeError):
        return None


def _clamp(s: log_txt.Session, x0, x1):
    """Fenster auf die Aufzeichnungsgrenzen begrenzen; entartetes Fenster → volle Sicht."""
    if s.start is None:
        return None, None
    if x0 is not None:
        x0 = max(x0, s.start)
    if x1 is not None:
        x1 = min(x1, s.end)
    if x0 is not None and x1 is not None and x0 >= x1:
        return None, None
    return x0, x1


def window_label(s: log_txt.Session, x0, x1, m):
    icon = html.I(className="ti ti-zoom-in",
                  style={"marginRight": "6px", "verticalAlign": "-2px"})
    if not m["windowed"]:
        return [icon, f"Gesamte Aufzeichnung · {fmt_dur(m['duration_s'])}"]
    a = (x0 or s.start).strftime("%H:%M:%S")
    b = (x1 or s.end).strftime("%H:%M:%S")
    return [icon, html.Span("Zeitausschnitt ", style={"color": C["text"], "fontWeight": 500}),
            f"{a} – {b} · {fmt_dur(m['duration_s'])}"]


def sbm_category(value: float, zones: tuple[float, float, float]):
    """SBM-Stufe (Label, Farbe) für einen Messwert. (None, None) bei NaN."""
    if value != value:  # NaN
        return None, None
    gelb, rot, viol = zones
    if value >= viol:
        return "extrem auffällig", C["danger"]
    if value >= rot:
        return "stark auffällig", C["danger"]
    if value >= gelb:
        return "schwach auffällig", C["warning"]
    return "unauffällig", C["ok"]


def sbm_index(value: float, zones: tuple[float, float, float]):
    """SBM-Stufe als Index 0..3 (grün/gelb/rot/violett); None bei NaN oder unbekannten Zonen."""
    if value != value:  # NaN
        return None
    gelb, rot, viol = zones
    if not (gelb == gelb and gelb != float("inf")):  # unbekannte Einheit → keine Einstufung
        return None
    if value >= viol:
        return 3
    if value >= rot:
        return 2
    if value >= gelb:
        return 1
    return 0


def _blank_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, xref="paper", yref="paper",
                       x=0.5, y=0.5, font={"size": 13, "color": C["muted"]})
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin={"l": 10, "r": 10, "t": 10, "b": 10},
                      xaxis={"visible": False}, yaxis={"visible": False})
    return fig


def pm_heatmap_figure(pm: point_meas.PointMeasurement, channel: str) -> go.Figure:
    """Frequenzspezifische SBM-Heatmap der räumlichen Feldverteilung (3×3 bzw. 6×1)."""
    if channel not in pm.df.columns:
        channel = "All 3D"
    zones = cfg.zones(pm.main_unit)
    nr, nc = pm.n_rows, pm.n_cols
    z = [[None] * nc for _ in range(nr)]
    txt = [[""] * nc for _ in range(nr)]
    for label, r, c in pm.points:
        v = float(pm.df.loc[label, channel])
        z[r][c] = sbm_index(v, zones)
        # 9-Punkt: nur Wert (Position steht an den Achsen); 6-Punkt: Wert + Einheit.
        txt[r][c] = de(v) if nc > 1 else f"{de(v)} {pm.main_unit}"
    xlabels, ylabels = pm.axes

    colorscale = []       # 4-stufige, diskrete SBM-Skala (zmin/zmax = -0.5..3.5 zentriert die Stufen)
    for i, col in enumerate(PM_COLORS):
        colorscale += [[i / 4, col], [(i + 1) / 4, col]]

    fig = go.Figure(go.Heatmap(
        z=z, text=txt, texttemplate="%{text}",
        textfont={"size": 16 if nc > 1 else 15, "color": C["text"]},
        x=xlabels, y=ylabels, xgap=5, ygap=5,
        colorscale=colorscale, zmin=-0.5, zmax=3.5, showscale=False,
        hoverongaps=False,
        hovertemplate="%{y}" + (" / %{x}" if nc > 1 else "") + ": %{text}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 78, "r": 20, "t": 26, "b": 20},
        height=330 if nc > 1 else 460, font={"color": C["text"]},
    )
    fig.update_xaxes(side="top", showgrid=False, ticks="", tickfont={"size": 13})
    fig.update_yaxes(autorange="reversed", showgrid=False, ticks="", tickfont={"size": 13})
    return fig


def _ch4_card(s: log_txt.Session, m: dict, hf_info: dict | None):
    """CH4-Kachel: HF (µW/m² + SBM + mV/m) wenn HF gesetzt, sonst roher NFA-Kanal (mV/nT/V/m)."""
    if hf_info is not None:
        return html.Div(className="metric", children=[
            html.Div("HF (CH4) max" + (" · Ausschnitt" if m["windowed"] else ""), className="lbl"),
            html.Div(f"{de(hf_info['power'])} µW/m²", className="val",
                     style={"color": hf_info["color"] or C["text"]}),
            html.Div(f"SBM: {hf_info['cat']} · {de(hf_info['field'])} mV/m" if hf_info["cat"] else "",
                     style={"fontSize": "12px", "color": hf_info["color"] or C["muted"]}),
        ])
    return html.Div(className="metric", children=[
        html.Div("CH4 max", className="lbl"),
        html.Div(f"{de(m['ch4_max'])} {m['ch4_unit']}", className="val")])


def _field_card(s: log_txt.Session, m: dict):
    """Feld-Bewertungskachel. Magnetfeld: SBM-2015-Bewertung über das 95. Perzentil
    (Langzeit; Max als Zusatzinfo). E-Feld: über den Max."""
    w = m["windowed"]
    # Magnetfeld über das 95. Perzentil bewerten – aber nur, wenn es aussagekräftig ist
    # (genug Punkte, main_p95 nicht NaN). Sonst ehrlich den Max zeigen, statt ein
    # degeneriertes „Perzentil" zu behaupten (z. B. beim Zoom in eine einzelne Spitze).
    if m["main_unit"] == "nT" and m["main_p95"] == m["main_p95"]:
        assess, label = m["main_p95"], "95. Perzentil"
        max_note = f" · Max {de(m['main_max'])} {m['main_unit']}"
    else:
        assess, label = m["main_max"], f"Max {s.kind}"
        max_note = ""
    cat, cat_color = sbm_category(assess, cfg.zones(m["main_unit"]))
    sub = []
    if cat:
        sub.append(html.Span(f"SBM: {cat}", style={"color": cat_color}))
    if max_note:
        sub.append(html.Span(max_note, style={"color": C["muted"]}))
    return html.Div(className="metric", children=[
        html.Div(label + (" · Ausschnitt" if w else ""), className="lbl"),
        html.Div(f"{de(assess)} {m['main_unit']}", className="val",
                 style={"color": cat_color or C["text"]}),
        html.Div(sub, style={"fontSize": "12px"}),
    ])


def metric_cards(s: log_txt.Session, events: list, m: dict, hf_info: dict | None = None):
    w = m["windowed"]
    return [
        html.Div(className="metric", children=[
            html.Div("Dauer (Ausschnitt)" if w else "Messdauer", className="lbl"),
            html.Div(fmt_dur(m["duration_s"]), className="val")]),
        _field_card(s, m),
        html.Div(className="metric", children=[
            html.Div("Crest-Events ≥ 4×", className="lbl"),
            html.Div(str(m["crest_count"]), className="val",
                     style={"color": C["warning"] if m["crest_count"] else C["text"]})]),
        _ch4_card(s, m, hf_info),
    ]


def _hf_note(s: log_txt.Session, range_key: str, dc_out: str, accessory: str = "none") -> str:
    _, label = hf.RANGES[range_key]
    txt = f"HF59B/D angeschlossen · {label} · DC-Out {dc_out} V"
    acc = hf.ACCESSORIES.get(accessory)
    if acc and accessory != "none":
        txt += f" · {acc[1]}"
    if s.ch4_unit != "mV":
        txt += f"  ⚠ Header markiert CH4 dieser Messung als {s.ch4_unit} (kein externer Eingang)"
    return txt


def ts_figure(s: log_txt.Session) -> go.Figure:
    fig = go.Figure()
    ymax = 1.0
    for ch, (color, width) in BAND_STYLE.items():
        ser = analysis.downsample(s.df[ch], cfg.plot_max_points)
        if ser.empty:
            continue
        ymax = max(ymax, float(ser.max()))
        fig.add_trace(go.Scattergl(x=ser.index, y=ser.values, mode="lines", name=ch,
                                   line=dict(color=color, width=width)))
    gelb, rot, viol = cfg.zones(s.main_unit)
    top = max(ymax * 1.1, gelb * 1.3)
    for y0, y1, fill in zip([0, gelb, rot, viol], [gelb, rot, viol, top], SBM_FILL):
        if y0 < top:
            fig.add_hrect(y0=y0, y1=min(y1, top), fillcolor=fill, line_width=0, layer="below")
    # Crest-Marker
    cr = [e for e in events_for(s.name) if e.type == "crest"][:12]
    if cr:
        fig.add_trace(go.Scattergl(
            x=[e.start for e in cr], y=[e.peak for e in cr], mode="markers",
            name="Crest ≥ 4×", marker=dict(color=C["danger"], size=8, symbol="x")))
    # Audionotiz-Marker (vertikale Linien + Beschriftung)
    for note in NOTES_BY_SESSION.get(s.name, []):
        fig.add_vline(x=note.start, line=dict(color=C["accent"], width=1, dash="dot"))
        fig.add_annotation(x=note.start, y=1.0, yref="paper", yanchor="bottom",
                           text=note.path.stem, showarrow=False,
                           font=dict(size=10, color=C["accent"]))
    fig.update_layout(
        margin=dict(l=48, r=12, t=8, b=28), height=320, paper_bgcolor="white",
        plot_bgcolor="white", legend=dict(orientation="h", y=1.12, x=0, font=dict(size=11)),
        yaxis=dict(title=s.main_unit, range=[0, top], gridcolor=C["border"]),
        xaxis=dict(gridcolor=C["border"]))
    return fig


def ch4_ts_figure(s: log_txt.Session, hf_range, hf_dcout, accessory="none") -> go.Figure:
    """CH4 (HF) als eigenes Diagramm in µW/m² (Log-Skala, eigene SBM-Zonen 0,1/10/1000)."""
    factor = hf.power_factor(hf_range, float(hf_dcout), accessory)
    power = analysis.downsample(s.df["All CH4"] * factor, cfg.plot_max_points)
    fig = go.Figure()
    if not power.empty:
        fig.add_trace(go.Scattergl(x=power.index, y=power.values, mode="lines",
                                   name="CH4 (HF)", line=dict(color="#7f77dd", width=1.6)))
    gelb, rot, viol = cfg.zones("µW/m²")            # 0,1 / 10 / 1000
    ymax = float(power.max()) if not power.empty else viol
    bottom, top = 0.01, max(ymax * 1.4, viol * 1.5)
    for y0, y1, fill in zip([bottom, gelb, rot, viol], [gelb, rot, viol, top], SBM_FILL):
        fig.add_hrect(y0=y0, y1=y1, fillcolor=fill, line_width=0, layer="below")
    cr = ch4_events_for(s.name, hf_range, hf_dcout, accessory)[:12]
    if cr:
        fig.add_trace(go.Scattergl(x=[e.start for e in cr], y=[e.peak for e in cr], mode="markers",
                                   name="Crest ≥ 4×", marker=dict(color=C["danger"], size=8, symbol="x")))
    for note in NOTES_BY_SESSION.get(s.name, []):
        fig.add_vline(x=note.start, line=dict(color=C["accent"], width=1, dash="dot"))
        fig.add_annotation(x=note.start, y=1.0, yref="paper", yanchor="bottom",
                           text=note.path.stem, showarrow=False, font=dict(size=10, color=C["accent"]))
    fig.update_layout(
        margin=dict(l=54, r=12, t=8, b=28), height=320, paper_bgcolor="white",
        plot_bgcolor="white", legend=dict(orientation="h", y=1.12, x=0, font=dict(size=11)),
        yaxis=dict(title="µW/m²", type="log", range=[math.log10(bottom), math.log10(top)],
                   gridcolor=C["border"]),
        xaxis=dict(gridcolor=C["border"]))
    return fig


def ch4_raw_ts_figure(s: log_txt.Session) -> go.Figure:
    """CH4 als 4. NFA-Kanal (ohne HF) in seiner nativen Einheit – z. B. das bei einer
    Magnetfeldmessung automatisch mitgeloggte E-Feld (V/m). Linear, mit SBM-Zonen der Einheit."""
    unit = s.ch4_unit
    ser = analysis.downsample(s.df["All CH4"], cfg.plot_max_points)
    fig = go.Figure()
    if not ser.empty:
        fig.add_trace(go.Scattergl(x=ser.index, y=ser.values, mode="lines",
                                   name=f"CH4 ({unit})", line=dict(color="#5f5e5a", width=1.8)))
    gelb, rot, viol = cfg.zones(unit)              # (inf,inf,inf) für Einheiten ohne SBM-Zonen (z. B. mV)
    yrange = None
    if gelb != float("inf"):
        ymax = float(ser.max()) if not ser.empty else 1.0
        top = max(ymax * 1.1, gelb * 1.3)
        for y0, y1, fill in zip([0, gelb, rot, viol], [gelb, rot, viol, top], SBM_FILL):
            if y0 < top:
                fig.add_hrect(y0=y0, y1=min(y1, top), fillcolor=fill, line_width=0, layer="below")
        yrange = [0, top]
    for note in NOTES_BY_SESSION.get(s.name, []):
        fig.add_vline(x=note.start, line=dict(color=C["accent"], width=1, dash="dot"))
        fig.add_annotation(x=note.start, y=1.0, yref="paper", yanchor="bottom",
                           text=note.path.stem, showarrow=False, font=dict(size=10, color=C["accent"]))
    fig.update_layout(
        margin=dict(l=54, r=12, t=8, b=28), height=320, paper_bgcolor="white",
        plot_bgcolor="white", legend=dict(orientation="h", y=1.12, x=0, font=dict(size=11)),
        yaxis=dict(title=unit, range=yrange, gridcolor=C["border"]),
        xaxis=dict(gridcolor=C["border"]))
    return fig


def spectro_figure(s: log_txt.Session, bands=None) -> go.Figure:
    z, x, y = analysis.spectrogram(s, cfg)          # z: (Bänder × Zeit), y = BAND_COLS
    if bands is None:
        bands = list(y)
    idx = [i for i, b in enumerate(y) if b in bands]  # nur ausgewählte Bänder, Reihenfolge erhalten
    fig = go.Figure()
    if idx:
        fig.add_trace(go.Heatmap(z=z[idx], x=x, y=[y[i] for i in idx], colorscale="Blues",
                                 colorbar=dict(title=s.main_unit, thickness=10)))
    else:
        fig.add_annotation(text="Alle Bänder ausgeblendet – mindestens eines auswählen.",
                           showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
                           font=dict(color=C["muted"], size=13))
    fig.update_layout(margin=dict(l=70, r=12, t=8, b=28), height=240,
                      paper_bgcolor="white", plot_bgcolor="white",
                      # Spektrogramm folgt dem Zeitverlauf (on_zoom patcht die x-Range),
                      # ist aber nicht selbst zoombar → eindeutiges Interaktionsmodell.
                      xaxis=dict(fixedrange=True),
                      yaxis=dict(autorange="reversed", fixedrange=True))
    return fig


def note_rows(s: log_txt.Session, x0=None, x1=None):
    windowed = x0 is not None or x1 is not None
    notes = [n for n in NOTES_BY_SESSION.get(s.name, [])
             if analysis.overlaps(n.start, n.end, x0, x1)]
    if not notes:
        msg = ("Keine Audionotizen in diesem Zeitausschnitt." if windowed
               else "Keine Audionotizen zu dieser Aufzeichnung.")
        return html.Div(msg, style={"color": C["muted"], "fontSize": "14px", "padding": "6px 0"})
    rows = []
    for n in notes:
        outside = not (s.start <= n.start <= s.end)
        rows.append(html.Div(className="erow", children=[
            html.Span(n.start.strftime("%H:%M:%S"), className="badge",
                      style={"background": "#e6f1fb", "color": C["accent"]}),
            html.Span([
                n.path.stem,
                html.Span("  · kurz außerhalb der Aufzeichnung", style={"color": C["muted"]})
                if outside else "",
            ], style={"flex": 1, "fontSize": "14px"}),
            html.Span(f"{n.duration_s:.1f} s", style={"fontSize": "13px", "color": C["muted"]}),
            html.Audio(src=f"/audio/{n.name}", controls=True,
                       preload="none", style={"height": "32px", "maxWidth": "280px"}),
        ]))
    return rows


def event_rows(events: list, windowed: bool = False):
    if not events:
        msg = ("Keine Auffälligkeiten in diesem Zeitausschnitt." if windowed
               else "Keine Auffälligkeiten in dieser Aufzeichnung.")
        return html.Div(msg, style={"color": C["muted"], "fontSize": "14px", "padding": "6px 0"})
    rows = []
    for e in events[:10]:
        dgr = e.severity == "danger"
        if e.type == "crest":
            badge = f"Crest {de(e.ratio)}×"
            desc = f"Peak ≫ Ø · {e.channel} ({de(e.peak)} {e.unit})"
        else:
            badge = f"{de(e.peak)} {e.unit}"
            # Momentan-Spitze (nicht die SBM-Langzeit-Einstufung der Kachel, die beim
            # Magnetfeld auf dem 95. Perzentil beruht) – daher bewusst neutral formuliert.
            desc = f"Feldspitze (Momentanwert) · {e.channel}"
        rows.append(html.Div(className="erow", children=[
            html.Span(badge, className="badge", style={
                "background": C["danger_bg"] if dgr else C["warning_bg"],
                "color": C["danger"] if dgr else C["warning"]}),
            html.Span(desc, style={"flex": 1, "fontSize": "14px"}),
            html.Span(f"{e.duration_s:.1f} s", style={"fontSize": "13px", "color": C["muted"]}),
            html.Span(e.start.strftime("%H:%M:%S"), style={"fontSize": "13px", "color": C["muted"]}),
        ]))
    return rows


@app.callback(
    Output("session", "options"), Output("session", "value"),
    Output("pm-select", "options"), Output("pm-select", "value"),
    Input("page-url", "href"),
    State("session", "value"), State("pm-select", "value"),
)
def on_page_load(_href, cur_session, cur_pm):
    """Bei jedem Seiten-Load samples/ neu einlesen und die Auswahllisten aktualisieren, damit
    neu abgelegte Dateien ohne Server-Neustart erscheinen. Da diese Callback-Outputs die
    Inputs von on_session/render_* sind, wartet die Render-Kette auf refresh_data() (korrekte
    Reihenfolge). Aktuelle Auswahl bleibt erhalten, wenn es sie noch gibt, sonst Default."""
    refresh_data()
    sess_opts = [{"label": s.label(), "value": s.name} for s in SESSIONS]
    sess_val = cur_session if cur_session in BY_NAME else _default()
    pm_opts = ([{"label": pm.label(), "value": pm.uid} for pm in POINT_MEAS]
               or [{"label": "— keine 9-/6-Punkt-Messung gefunden —", "value": "none"}])
    pm_val = cur_pm if cur_pm in PM_BY_NAME else (POINT_MEAS[0].uid if POINT_MEAS else "none")
    return sess_opts, sess_val, pm_opts, pm_val


@app.callback(
    Output("view", "data"), Output("hf-range", "value"), Output("hf-dcout", "value"),
    Output("hf-accessory", "value"), Output("location", "value"),
    Input("session", "value"),
)
def on_session(name: str):
    """Session-Wechsel: Zeitausschnitt zurück, HF-Einstellung + Messort laden.
    (ts-Diagramm baut render_ts, Spektrogramm render_spectro – beide hängen an weiteren Inputs.)"""
    saved = HF_SETTINGS.get(name) if isinstance(HF_SETTINGS.get(name), dict) else {}
    return ({"session": name, "x0": None, "x1": None},
            saved.get("range", "none"), saved.get("dc_out", "1"), saved.get("accessory", "none"),
            LOCATIONS.get(name, ""))


@app.callback(
    Output("loc-saved", "data"),
    Input("location", "value"),
    State("session", "value"),
    prevent_initial_call=True,
)
def on_location_persist(location, name):
    """Messort der aktuellen Messung persistieren (nur bei echter Änderung schreiben)."""
    if not name:
        raise PreventUpdate
    loc = (location or "").strip()
    if loc == LOCATIONS.get(name, ""):
        raise PreventUpdate  # unverändert (z. B. beim Laden per on_session) → nicht schreiben
    if loc:
        LOCATIONS[name] = loc
    else:
        LOCATIONS.pop(name, None)
    hf.save_settings(LOCATIONS_FILE, LOCATIONS)   # generischer atomarer JSON-Write
    return name


@app.callback(
    Output("spectro", "figure"),
    Input("session", "value"), Input("spectro-bands", "value"),
    State("view", "data"),
)
def render_spectro(name: str, bands, view):
    """Band-Spektrogramm mit den ausgewählten Bändern; Zeitausschnitt wie render_ts erhalten."""
    if name not in BY_NAME:      # keine/entfernte Aufzeichnung (leerer Ordner oder anderer Tab)
        return _blank_fig("Keine Aufzeichnung ausgewählt.")
    fig = spectro_figure(BY_NAME[name], bands)
    if view and view.get("session") == name and view.get("x0"):
        fig.update_xaxes(range=[view["x0"], view["x1"]], autorange=False)
    return fig


@app.callback(
    Output("pm-heatmap", "figure"),
    Input("pm-select", "value"), Input("pm-band", "value"),
)
def render_pm(pm_name: str, band: str):
    """9-/6-Punkt-Heatmap für die gewählte Messung und das gewählte Frequenzband."""
    if not pm_name or pm_name not in PM_BY_NAME:
        return _blank_fig("Keine 9-/6-Punkt-Messung vorhanden. "
                          "Lege eine nicht-leere .9PM/.6PM in den samples-Ordner.")
    return pm_heatmap_figure(PM_BY_NAME[pm_name], band or "All 3D")


@app.callback(
    Output("ts", "figure"), Output("ts-title", "children"),
    Input("session", "value"), Input("ts-view", "value"),
    Input("hf-range", "value"), Input("hf-dcout", "value"), Input("hf-accessory", "value"),
    State("view", "data"),
)
def render_ts(name: str, ts_view, hf_range, hf_dcout, hf_accessory, view):
    """Zeitverlauf-Diagramm: Feld (CH1–3), CH4 als HF (µW/m²) oder CH4 als nativer 4. NFA-Kanal
    (z. B. E-Feld in V/m bei einer Magnetfeldmessung ohne HF)."""
    if name not in BY_NAME:      # keine/entfernte Aufzeichnung (leerer Ordner oder anderer Tab)
        return _blank_fig("Keine Aufzeichnung ausgewählt."), ""
    s = BY_NAME[name]
    hf_active = bool(hf_range) and hf_range != "none"
    if ts_view == "ch4" and hf_active:
        fig = ch4_ts_figure(s, hf_range, hf_dcout, hf_accessory)
        title = "HF-Signal (CH4) über Zeit · µW/m² (log)"
    elif ts_view == "ch4" and CH4_HAS_DATA.get(name):
        fig = ch4_raw_ts_figure(s)
        title = f"CH4 (4. NFA-Kanal) über Zeit · {s.ch4_unit}"
    else:
        fig = ts_figure(s)
        title = f"Frequenzbänder über Zeit · {s.main_unit}"
    # Zeitausschnitt über Toggle/HF-Wechsel erhalten; bei Session-Wechsel ist view noch
    # die alte Session (stale State) → dann volle Sicht.
    if view and view.get("session") == name and view.get("x0"):
        fig.update_xaxes(range=[view["x0"], view["x1"]], autorange=False)
    return fig, title


@app.callback(
    Output("ts-view", "options"), Output("ts-view-wrap", "style"),
    Input("session", "value"), Input("hf-range", "value"),
)
def ts_view_control(name, hf_range):
    """Feld/CH4-Umschalter: sichtbar sobald CH4 Daten hat (oder HF gesetzt); CH4-Label je Modus.
    HF gesetzt → CH4 als µW/m²; sonst CH4 als nativer 4. NFA-Kanal (z. B. E-Feld V/m)."""
    if name not in BY_NAME:      # keine/entfernte Aufzeichnung → Umschalter ausblenden
        return [{"label": " Feld (CH1–3)", "value": "field"}], {"display": "none"}
    s = BY_NAME[name]
    hf_active = bool(hf_range) and hf_range != "none"
    if hf_active:
        ch4_label = " HF (CH4)"
    else:
        u = s.ch4_unit
        ch4_label = f" CH4 ({u})" if u and u != "?" else " CH4"
    options = [{"label": " Feld (CH1–3)", "value": "field"}, {"label": ch4_label, "value": "ch4"}]
    show = hf_active or CH4_HAS_DATA.get(name, False)
    return options, {"marginBottom": "10px", "display": "block" if show else "none"}


@app.callback(
    Output("view", "data", allow_duplicate=True),
    Output("spectro", "figure", allow_duplicate=True),
    Input("ts", "relayoutData"),
    State("session", "value"), State("view", "data"),
    prevent_initial_call=True,
)
def on_zoom(relayout, session, view):
    """Zoom/Pan im Zeitverlauf → Fenster in den Store schreiben, Spektrogramm mit-zoomen."""
    parsed = parse_relayout(relayout)
    if parsed is None:
        raise PreventUpdate
    kind, x0, x1 = parsed
    newview = {"session": session, "x0": x0, "x1": x1}
    if view and view.get("session") == session \
            and view.get("x0") == x0 and view.get("x1") == x1:
        raise PreventUpdate  # keine echte Änderung → kein Re-Render, keine Rückkopplung
    patch = Patch()
    if kind == "reset":
        patch["layout"]["xaxis"]["autorange"] = True
    else:
        patch["layout"]["xaxis"]["autorange"] = False
        patch["layout"]["xaxis"]["range"] = [x0, x1]
    return newview, patch


@app.callback(
    Output("cards", "children"), Output("notes", "children"),
    Output("events", "children"), Output("window-label", "children"),
    Output("hf-note", "children"),
    Input("view", "data"), Input("hf-range", "value"), Input("hf-dcout", "value"),
    Input("hf-accessory", "value"),
)
def on_view(view, hf_range, hf_dcout, hf_accessory):
    """Panels auf den gewählten Ausschnitt rendern; CH4 ggf. als HF (µW/m²) umrechnen."""
    if not view or view.get("session") not in BY_NAME:
        raise PreventUpdate
    s = BY_NAME[view["session"]]
    x0, x1 = _clamp(s, _ts(view.get("x0")), _ts(view.get("x1")))
    # Hauptfeld-Events + (falls HF gesetzt) CH4-HF-Crest-Events, zusammen gefenstert/sortiert.
    combined = events_for(s.name) + ch4_events_for(s.name, hf_range, hf_dcout, hf_accessory)
    ev = analysis.sort_events(analysis.events_in_window(combined, x0, x1), cfg)
    m = analysis.metrics(s, ev, x0, x1)

    hf_info, note = None, ""
    if hf_range and hf_range != "none":
        note = _hf_note(s, hf_range, hf_dcout, hf_accessory)
        # hf_info immer setzen, damit Kachel (HF) und Notiz konsistent bleiben – auch
        # wenn CH4 im Ausschnitt keine Daten hat (dann power = NaN → Anzeige "–").
        if m["ch4_max"] == m["ch4_max"]:  # nicht NaN
            power = hf.to_power_uwm2(m["ch4_max"], hf_range, float(hf_dcout), hf_accessory)
            field = float(hf.power_to_field_mvm(power))
        else:
            power, field = float("nan"), float("nan")
        cat, color = sbm_category(power, cfg.zones("µW/m²"))
        hf_info = {"power": power, "field": field, "cat": cat, "color": color}
    else:
        extra = f" ({s.ch4_unit})" if s.ch4_unit != "?" else ""
        note = f"Kein HF angeschlossen – CH4 = 4. NFA-Kanal{extra}."

    return (metric_cards(s, ev, m, hf_info), note_rows(s, x0, x1),
            event_rows(ev, m["windowed"]), window_label(s, x0, x1, m), note)


@app.callback(
    Output("hf-saved", "data"),
    Input("hf-range", "value"), Input("hf-dcout", "value"), Input("hf-accessory", "value"),
    State("session", "value"),
    prevent_initial_call=True,
)
def on_hf_persist(hf_range, hf_dcout, hf_accessory, name):
    """HF-Einstellung der aktuellen Messung persistieren (nur bei echter Änderung schreiben)."""
    if not name:
        raise PreventUpdate
    new = (None if (not hf_range or hf_range == "none")
           else {"range": hf_range, "dc_out": str(hf_dcout), "accessory": hf_accessory})
    if new == HF_SETTINGS.get(name):
        raise PreventUpdate  # unverändert (z. B. beim Laden per on_session) → nicht schreiben
    if new is None:
        HF_SETTINGS.pop(name, None)
    else:
        HF_SETTINGS[name] = new
    hf.save_settings(HF_SETTINGS_FILE, HF_SETTINGS)
    return name


@app.callback(
    Output("pdf-download", "data"),
    Input("pdf-btn", "n_clicks"),
    State("session", "value"), State("hf-range", "value"),
    State("hf-dcout", "value"), State("hf-accessory", "value"), State("location", "value"),
    prevent_initial_call=True,
)
def make_pdf(n, name, hf_range, hf_dcout, hf_accessory, location):
    """PDF-Messprotokoll der aktuellen Aufzeichnung (volle Aufzeichnung, unabhängig vom Zoom)."""
    from emftool import report  # lazy: matplotlib erst beim ersten Report laden (App-Start bleibt schnell)
    if not name or name not in BY_NAME:
        raise PreventUpdate
    s = BY_NAME[name]
    hf_setting = (hf_range, hf_dcout, hf_accessory) if (hf_range and hf_range != "none") else None
    ev = analysis.sort_events(
        events_for(name) + ch4_events_for(name, hf_range, hf_dcout, hf_accessory), cfg)
    m = analysis.metrics(s, ev)
    notes = NOTES_BY_SESSION.get(name, [])
    data = report.build_pdf(s, ev, m, notes, cfg, hf=hf, hf_setting=hf_setting,
                            location=(location or "").strip())
    return dcc.send_bytes(lambda buf: buf.write(data), f"EMF-Report_{name}.pdf")


# Reset-Button: clientseitig den Zeitverlauf auf autorange zurücksetzen → löst on_zoom aus.
app.clientside_callback(
    """
    function(n) {
        if (!n) { return window.dash_clientside.no_update; }
        var el = document.getElementById('ts');
        var gd = el ? (el.querySelector('.js-plotly-plot') || el) : null;
        if (gd && window.Plotly) { window.Plotly.relayout(gd, {'xaxis.autorange': true}); }
        return n;
    }
    """,
    Output("reset-dummy", "data"),
    Input("reset-zoom", "n_clicks"),
    prevent_initial_call=True,
)


# Hauptmenü: Ansichten nur ein-/ausblenden (Komponenten bleiben gemountet, Zustand bleibt
# erhalten). Danach ein resize auslösen, damit die zuvor versteckten Graphen ihre Breite
# neu berechnen.
app.clientside_callback(
    """
    function(page) {
        setTimeout(function() { window.dispatchEvent(new Event('resize')); }, 0);
        return [{display: page === 'pm' ? 'none' : 'block'},
                {display: page === 'pm' ? 'block' : 'none'}];
    }
    """,
    Output("page-log", "style"), Output("page-pm", "style"),
    Input("page", "value"),
)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", "8050"))
    n_assigned = sum(len(v) for v in NOTES_BY_SESSION.values())
    print(f"Sessions: {len(SESSIONS)} · Audionotizen: {len(NOTES)} "
          f"({n_assigned} zugeordnet)  → http://127.0.0.1:{port}")
    app.run(debug=False, port=port)
