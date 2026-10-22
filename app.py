"""Cotizaciones con fotos y fichas técnicas. Ejecutar con:  streamlit run app.py"""
import hmac
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
import time
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

import buscador as bs
import cotizador as ct
import historial as hs
import lector_ia as li
import pdf_cotizacion as pc
import revisor as rv

APP_DIR = Path(os.environ.get("GIOVAL_DATA_DIR") or Path(__file__).parent).expanduser().resolve()
E = st.session_state
GIOVAL_REQUIRE_ACCESS = os.environ.get("GIOVAL_REQUIRE_ACCESS", "false").strip().lower() in {"1", "true", "yes", "on"}
GIOVAL_ACCESS_PASSWORD = os.environ.get("GIOVAL_ACCESS_PASSWORD", "")
GIOVAL_ALLOW_INITIAL_IMPORT = os.environ.get("GIOVAL_ALLOW_INITIAL_IMPORT", "false").strip().lower() in {"1", "true", "yes", "on"}

# Aplica datos de una cotización importada al inicio de la siguiente ejecución,
# antes de crear widgets con esas claves (Streamlit no permite cambiarlas después).
_pendiente_importado = E.pop("_pending_import_header", None)
if isinstance(_pendiente_importado, dict):
    for _clave_widget, _valor_widget in _pendiente_importado.items():
        if _clave_widget in {
            "precios_modo", "c_nombre", "c_rut", "c_dir", "c_ciudad", "c_tel", "c_email", "c_fecha", "cot_numero"
        }:
            E[_clave_widget] = _valor_widget

# ------------------------------------------------------------- DISEÑO "GRAPHIFY x GIOVAL"
ANIMAR_FONDO = False
# La interfaz adopta el panel de trabajo oscuro de Graphify; el azul eléctrico y
# los neutros de esta paleta siguen siendo los de Gioval.cl.
TEMA_BASE = {
    "base": "dark",
    "primaryColor": "#010ED0",
    "backgroundColor": "#0A0B10",
    "secondaryBackgroundColor": "#151720",
    "textColor": "#F3F4F8",
    "font": "sans serif",
}
TEMA_EXTRA = {
    "baseRadius": "small",
    "buttonRadius": "small",
    "borderColor": "#2A2D39",
    "dataframeBorderColor": "#2A2D39",
    "dataframeHeaderBackgroundColor": "#1B1E28",
    "dataframeHeaderTextColor": "#F3F4F8",
    "codeBackgroundColor": "#151720",
    "codeTextColor": "#F3F4F8",
    "linkColor": "#7781FF",
}


def preparar_tema() -> None:
    """Aplica el tema oscuro y actualiza un bloque [theme] previo si ya existía."""
    cfg = APP_DIR / ".streamlit" / "config.toml"
    try:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        texto_original = cfg.read_text("utf-8") if cfg.exists() else ""
        lineas = texto_original.splitlines()
        inicio = next((i for i, linea in enumerate(lineas) if linea.strip() == "[theme]"), None)
        if inicio is None:
            if lineas and lineas[-1].strip():
                lineas.append("")
            lineas.append("[theme]")
            inicio = len(lineas) - 1
        fin = next(
            (i for i in range(inicio + 1, len(lineas))
             if re.match(r"^\s*\[[^]]+\]\s*(?:#.*)?$", lineas[i])),
            len(lineas),
        )
        bloque_tema = lineas[inicio + 1:fin]
        for clave, valor in {**TEMA_BASE, **TEMA_EXTRA}.items():
            patron = re.compile(rf"^\s*{re.escape(clave)}\s*=")
            indice = next((i for i, linea in enumerate(bloque_tema) if patron.match(linea)), None)
            nueva = f'{clave} = "{valor}"'
            if indice is None:
                bloque_tema.append(nueva)
            else:
                bloque_tema[indice] = nueva
        lineas[inicio + 1:fin] = bloque_tema
        texto_nuevo = "\n".join(lineas).rstrip() + "\n"
        if texto_nuevo != texto_original:
            cfg.write_text(texto_nuevo, "utf-8")
    except OSError:
        # En entornos de solo lectura, los valores de ejecución aún se aplican abajo.
        pass
    for clave, valor in {**TEMA_BASE, **TEMA_EXTRA}.items():
        try:
            st._config.set_option(f"theme.{clave}", valor)
        except Exception:
            pass


preparar_tema()

st.set_page_config(page_title="Gioval · Cotizador comercial", page_icon=":material/description:",
                   layout="wide", initial_sidebar_state="collapsed")

# El espacio compartido falla cerrado: nunca muestra cotizaciones si falta la clave del servidor.
if GIOVAL_REQUIRE_ACCESS:
    if not GIOVAL_ACCESS_PASSWORD:
        st.error("El acceso al espacio compartido no está configurado. Contacta al administrador.")
        st.stop()
    if not E.get("gioval_autenticado"):
        st.markdown("## Gioval · Biblioteca compartida")
        st.caption("Ingresa la clave de acceso del equipo para consultar y actualizar la biblioteca central.")
        with st.form("gioval_login_compartido"):
            clave_ingresada = st.text_input("Clave de acceso", type="password", autocomplete="current-password")
            entrar = st.form_submit_button("Entrar a Gioval", type="primary", width="stretch")
        if entrar:
            if hmac.compare_digest(clave_ingresada.encode("utf-8"), GIOVAL_ACCESS_PASSWORD.encode("utf-8")):
                E["gioval_autenticado"] = True
                st.rerun()
            else:
                st.error("La clave no es correcta.")
        st.stop()

CSS_BASE = """
html, body, .stApp { font-size: 16px; line-height: 1.5; }
.block-container { max-width: 1580px; padding: 4rem 1.65rem 3rem !important; }
h1 { font-size: 2rem !important; line-height: 1.18; margin-bottom: .7rem; }
h2 { font-size: 1.45rem !important; line-height: 1.3; }
h3 { font-size: 1.15rem !important; line-height: 1.35; }
p, li { line-height: 1.52; }
[data-testid="stWidgetLabel"] p { font-size: .93rem !important; font-weight: 600; line-height: 1.4; }
.stCaption, [data-testid="stCaptionContainer"] { font-size: .84rem; line-height: 1.48; }
.stButton > button, .stDownloadButton > button, .stLinkButton > a {
    font-size: .94rem; font-weight: 650; padding: .58rem .9rem; border-radius: 6px;
    min-height: 2.65rem; transition: background-color .15s ease, border-color .15s ease, transform .15s ease;
}
.stAppDeployButton, [data-testid="stAppDeployButton"] { display: none; }
[data-testid="stAlert"] { border-radius: 7px; font-size: .94rem; }
[data-testid="stAlert"] p { line-height: 1.5; }
[data-baseweb="input"] > div, [data-baseweb="textarea"], [data-baseweb="select"] > div { border-radius: 6px; }
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea, [data-baseweb="select"] { font-size: .96rem !important; }
[data-baseweb="input"] input { min-height: 2.6rem; }
[data-baseweb="textarea"] textarea { line-height: 1.5; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border-radius: 7px; }
[data-testid="stDataEditor"] { font-size: .94rem; }
[data-testid="stExpander"] summary { min-height: 2.75rem; font-weight: 600; }
button:focus-visible, a:focus-visible, input:focus-visible, textarea:focus-visible {
    outline: 2px solid #010ED0 !important; outline-offset: 2px !important;
}
.pasos { display: flex; gap: 7px; flex-wrap: wrap; margin: 8px 0 12px; }
.paso { padding: 8px 12px; border-radius: 6px; font-size: .9rem; }
.paso b { margin-right: 7px; }
[data-testid="stRadio"] div[role="radiogroup"] { gap: .45rem; flex-wrap: wrap; }
@media (max-width: 800px) {
    .block-container { padding-left: .9rem; padding-right: .9rem; padding-top: .8rem; }
    h1 { font-size: 1.72rem !important; }
    .paso { padding: 7px 9px; font-size: .84rem; }
    .st-key-app-navigation [data-testid="stRadio"] label { padding: .55rem .65rem !important; }
}
"""

CSS_TINTA = """
:root {
    --gioval-blue: #010ED0;
    --gioval-blue-soft: rgba(1,14,208,.14);
    --ui-bg: #0A0B10;
    --ui-panel: #11131B;
    --ui-panel-2: #171923;
    --ui-panel-3: #1C1F2A;
    --ui-line: #292C38;
    --ui-text: #F3F4F8;
    --ui-muted: #9A9EAC;
    --ui-dim: #717686;
}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background: var(--ui-bg) !important;
    color: var(--ui-text);
}
.stApp {
    background-image:
        radial-gradient(ellipse at 8% 0%, rgba(1,14,208,.10), transparent 34rem),
        radial-gradient(ellipse at 96% 4%, rgba(1,14,208,.07), transparent 28rem);
}
[data-testid="stHeader"] { background: rgba(10,11,16,.96); }
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display:none !important; }
.block-container { color:var(--ui-text); }
h1,h2,h3,h4,h5 { color:var(--ui-text); letter-spacing:-.018em; }
p, li, label, [data-testid="stMarkdownContainer"] { color:inherit; }
a, [data-testid="stMarkdownContainer"] a { color:#7781FF; }
.stCaption, [data-testid="stCaptionContainer"], [data-testid="stWidgetLabel"] p { color:var(--ui-muted) !important; }
hr, [data-testid="stDivider"] { border-color:var(--ui-line) !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--ui-line); }
[data-testid="stExpander"] {
    background:var(--ui-panel); border:1px solid var(--ui-line); border-radius:7px;
}
[data-testid="stExpander"] summary { color:var(--ui-text); }
[data-testid="stExpander"] summary:hover { color:#AEB4FF; }
.stButton > button, .stDownloadButton > button, .stLinkButton > a {
    border-radius:6px; font-weight:650;
}
button[kind="primary"], [data-testid="stBaseButton-primary"] {
    background:var(--gioval-blue); border:1px solid var(--gioval-blue); color:#FFFFFF; box-shadow:none;
}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
    background:#1724EA; border-color:#4650FF; color:#FFFFFF; transform:translateY(-1px);
    box-shadow:0 0 0 3px rgba(1,14,208,.16);
}
button[kind="primary"]:disabled, [data-testid="stBaseButton-primary"]:disabled { opacity:.42; box-shadow:none; transform:none; }
button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
    background:#171923; border:1px solid #323542; color:#E7E8EE; box-shadow:none;
}
button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {
    background:#202331; border-color:var(--gioval-blue); color:#FFFFFF;
}
[data-testid="stPopover"], .st-key-settings-popover button { border-radius:6px; }
.st-key-settings-popover button { min-height:2.5rem; box-shadow:none; background:#151720; border-color:#343746; color:#F3F4F8; }
[data-testid="stPopoverBody"] {
    background:#11131B !important; color:#F3F4F8 !important;
    border:1px solid #303341; box-shadow:0 18px 48px rgba(0,0,0,.44);
}
[data-testid="stPopoverBody"] [data-testid="stMarkdownContainer"], [data-testid="stPopoverBody"] p { color:#D4D6DE; }
[data-baseweb="input"] > div, [data-baseweb="textarea"], [data-baseweb="select"] > div {
    background:#0D0F16 !important; border-color:#353847 !important; border-radius:6px;
}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea, [data-baseweb="select"] input {
    color:#F3F4F8 !important; caret-color:#FFFFFF;
}
[data-baseweb="input"] > div:focus-within, [data-baseweb="textarea"]:focus-within, [data-baseweb="select"] > div:focus-within {
    border-color:var(--gioval-blue) !important; box-shadow:0 0 0 1px var(--gioval-blue) !important;
}
[data-baseweb="select"] svg { color:#B4B7C4 !important; }
[data-baseweb="popover"], [role="listbox"], [data-baseweb="menu"] {
    background:#171923 !important; color:#F3F4F8 !important; border-color:#343746 !important;
}
[data-baseweb="menu"] li:hover, [role="option"]:hover { background:#22253A !important; }
[data-testid="stFileUploader"] section {
    background:#11131B; border:1px dashed #414557; border-radius:7px;
}
[data-testid="stFileUploader"] small, [data-testid="stFileUploader"] span { color:#A9ADBA; }
[data-testid="stCheckbox"] label, [data-testid="stRadio"] label { color:#D9DBE4; }
[data-testid="stRadio"] div[role="radiogroup"] label {
    background:#141620; border:1px solid #303341; border-radius:6px; color:#D9DBE4;
}
[data-testid="stRadio"] div[role="radiogroup"] label:has([aria-checked="true"]) {
    background:#11142A; border-color:var(--gioval-blue); color:#FFFFFF;
}
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap:.25rem; border-bottom:1px solid var(--ui-line); background:transparent;
}
[data-testid="stTabs"] button[role="tab"] {
    font-size:.94rem; font-weight:600; padding:.65rem .95rem; border-radius:5px 5px 0 0; color:#979BAA;
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color:#FFFFFF; border-bottom-color:var(--gioval-blue); background:rgba(1,14,208,.09);
}
/* Barras de progreso: mantener la paleta oscura sin alterar los botones primarios. */
[data-testid="stProgress"] {
    background:transparent !important;
}
[data-testid="stProgress"] > div:not([role="progressbar"]):not([data-baseweb="progress-bar"]):not([data-testid="stProgressBar"]) {
    background:transparent !important;
}
[data-testid="stProgress"] [role="progressbar"],
[data-testid="stProgress"] [data-baseweb="progress-bar"],
[data-testid="stProgress"] [data-testid="stProgressBar"] {
    background:#20232E !important;
    border-radius:999px !important;
}
[data-testid="stProgress"] [role="progressbar"] > div,
[data-testid="stProgress"] [data-baseweb="progress-bar"] > div,
[data-testid="stProgress"] [data-testid="stProgressBar"] > div {
    background:#3A3E49 !important;
    border-radius:999px !important;
}
[data-testid="stProgress"] [data-testid="stMarkdownContainer"],
[data-testid="stProgress"] [data-testid="stMarkdownContainer"] p,
[data-testid="stProgress"] p {
    background:transparent !important;
    color:#989DAC !important;
}
[data-testid="stMetric"] {
    background:transparent; border:0 !important; border-top:2px solid var(--gioval-blue) !important;
    border-radius:4px; padding:12px 13px; box-shadow:none;
}
[data-testid="stMetricLabel"] { color:var(--ui-muted) !important; font-size:.82rem; }
[data-testid="stMetricValue"] { color:#F5F6FA !important; font-weight:750; }
[data-testid="stVerticalBlockBorderWrapper"]:has([data-testid="stMetric"]) {
    background:linear-gradient(145deg,#13151E,#101219); border:1px solid var(--ui-line);
    border-radius:7px; box-shadow:0 5px 16px rgba(0,0,0,.12);
}
[data-testid="stVerticalBlockBorderWrapper"]:has(.tarjeta-marca):not(:has([data-testid="stVerticalBlockBorderWrapper"] .tarjeta-marca)) {
    background:#11131B; border:1px solid #292C38; border-radius:7px; box-shadow:0 4px 18px rgba(0,0,0,.16);
}
[data-testid="stDataFrame"], [data-testid="stDataEditor"], [data-testid="stTable"] {
    background:var(--ui-panel) !important;
    color:var(--ui-text) !important;
    border:1px solid var(--ui-line) !important;
    border-radius:6px;
    overflow:hidden;
}
[data-testid="stDataEditor"] > div, [data-testid="stDataFrame"] > div {
    background:var(--ui-panel) !important;
}
[data-testid="stDataEditor"] canvas, [data-testid="stDataFrame"] canvas {
    background-color:var(--ui-panel) !important;
    color-scheme:dark;
}
[data-testid="stDataEditor"] [role="grid"], [data-testid="stDataFrame"] [role="grid"] {
    background:var(--ui-panel) !important;
    color:var(--ui-text) !important;
}
[data-testid="stAlert"] { background:#131520; border:1px solid #303341; border-radius:6px; color:#E5E6ED; }
[data-testid="stAlert"] p { color:inherit; }
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] { color:inherit; }
.st-key-app-navigation {
    margin:.65rem 0 1.35rem; padding:.22rem .35rem 0;
    border:1px solid #242733; border-bottom:1px solid #2F3241;
    border-radius:7px 7px 0 0; background:#0E1017;
}
.st-key-app-navigation [data-testid="stRadio"] label [data-baseweb="radio"] {
    display:none !important;
}
.st-key-app-navigation [data-testid="stRadio"] label {
    cursor:pointer;
}
.st-key-app-navigation [data-testid="stRadio"] label:has([aria-checked="true"]) {
    box-shadow:inset 0 -1px 0 rgba(1,14,208,.14);
}
.st-key-app-navigation [data-testid="stRadio"] div[role="radiogroup"] {
    gap:.18rem; border:0; background:transparent; align-items:stretch;
}
.st-key-app-navigation [data-testid="stRadio"] label {
    border:0 !important; border-bottom:2px solid transparent !important;
    border-radius:4px 4px 0 0 !important; background:transparent !important;
    padding:.67rem 1rem .58rem !important; margin:0 !important;
    color:#999EAE !important; font-weight:600; min-height:2.6rem;
}
.st-key-app-navigation [data-testid="stRadio"] label:hover {
    background:#171923 !important; color:#F3F4F8 !important;
}
.st-key-app-navigation [data-testid="stRadio"] label:has([aria-checked="true"]) {
    color:#FFFFFF !important; border-bottom-color:var(--gioval-blue) !important;
    background:rgba(1,14,208,.10) !important; font-weight:750;
}
.st-key-app-navigation [data-testid="stRadio"] [data-baseweb="radio"] > div:first-child { display:none !important; }
.pasos { display:flex; gap:7px; flex-wrap:wrap; margin:8px 0 12px; }
.paso {
    display:inline-flex; align-items:center; padding:8px 12px; border-radius:5px;
    border:1px solid #2A2D39; background:#12141C; color:#989DAC; font-size:.89rem;
}
.paso b { margin-right:7px; }
.paso.activo {
    border:1px solid var(--gioval-blue); background:rgba(1,14,208,.18); color:#FFFFFF;
    font-weight:750; box-shadow:inset 3px 0 0 var(--gioval-blue);
}
.paso.hecho { border-color:#262A36; background:#10121A; color:#C7C9D3; }
.marca-app {
    display:flex; align-items:center; gap:13px; min-height:60px;
    padding:0 0 12px; border-bottom:1px solid #272A35;
}
.marca-simbolo {
    position:relative; display:inline-flex; align-items:center; justify-content:center;
    flex:0 0 43px; width:43px; height:43px; border-radius:7px;
    background:#FFFFFF; border:1px solid #FFFFFF; box-shadow:none;
}
.marca-simbolo::before {
    content:"Gv"; color:var(--gioval-blue); font-size:1.55rem; font-weight:900; letter-spacing:-.12em;
    text-shadow:0 0 18px rgba(1,14,208,.55); transform:translateX(-1px);
}
.marca-simbolo::after {
    content:""; position:absolute; width:5px; height:5px; border-radius:50%;
    background:var(--gioval-blue); right:6px; top:6px; box-shadow:0 0 9px rgba(1,14,208,.7);
}
.marca-texto { display:flex; flex-direction:column; gap:3px; }
.marca-nombre { color:#FFFFFF !important; font-size:1.35rem; font-weight:800; letter-spacing:-.04em; line-height:1.05; }
.marca-descripcion { color:#A9ADBA !important; font-size:.83rem; font-weight:500; line-height:1.3; }
.marca-pill {
    display:inline-flex; width:fit-content; align-items:center; margin-top:4px;
    padding:3px 8px; border:1px solid rgba(1,14,208,.58); border-radius:4px;
    background:rgba(1,14,208,.13); color:#B9BEFF; font-size:.70rem; font-weight:700; letter-spacing:.035em;
}
.inicio-hero {
    position:relative; overflow:hidden; background:#10121A;
    border:1px solid #292C38; border-left:3px solid var(--gioval-blue);
    border-radius:7px; padding:27px 29px; margin:6px 0 19px;
    box-shadow:0 12px 30px rgba(0,0,0,.16);
    background-image:
        radial-gradient(ellipse at 83% 30%,rgba(1,14,208,.17),transparent 21rem),
        linear-gradient(rgba(255,255,255,.026) 1px,transparent 1px),
        linear-gradient(90deg,rgba(255,255,255,.026) 1px,transparent 1px);
    background-size:auto,28px 28px,28px 28px;
}
.inicio-hero::before {
    content:""; position:absolute; width:310px; height:225px; right:0; top:0; opacity:.9; pointer-events:none;
    background-image:
        linear-gradient(28deg,transparent 49.7%,rgba(1,14,208,.24) 49.9%,rgba(1,14,208,.24) 50.1%,transparent 50.3%),
        linear-gradient(145deg,transparent 49.7%,rgba(125,135,255,.18) 49.9%,rgba(125,135,255,.18) 50.1%,transparent 50.3%),
        radial-gradient(circle at 20% 35%,#010ED0 0 3px,transparent 4px),
        radial-gradient(circle at 49% 57%,#6571FF 0 3px,transparent 4px),
        radial-gradient(circle at 76% 25%,#010ED0 0 4px,transparent 5px),
        radial-gradient(circle at 82% 80%,#414BFF 0 3px,transparent 4px),
        radial-gradient(circle at 35% 83%,#5260FF 0 2px,transparent 3px);
    mask-image:linear-gradient(to left,#000 10%,transparent 100%);
}
.inicio-hero::after {
    content:""; position:absolute; width:165px; height:165px; right:-66px; bottom:-93px;
    border:1px solid rgba(1,14,208,.45); border-radius:50%;
    box-shadow:0 0 0 21px rgba(1,14,208,.06),0 0 0 43px rgba(1,14,208,.035); pointer-events:none;
}
.inicio-kicker { color:#AAB0FF; font-size:.72rem; font-weight:800; letter-spacing:.14em; }
.inicio-hero h2 { margin:7px 0 9px; color:#FFFFFF; font-size:1.75rem !important; line-height:1.2; position:relative; z-index:1; }
.inicio-hero p { margin:0; color:#A5A9B6; font-size:.96rem; line-height:1.6; max-width:750px; position:relative; z-index:1; }
.inicio-badges { display:flex; flex-wrap:wrap; gap:7px; margin-top:16px; position:relative; z-index:1; }
.inicio-badge {
    display:inline-flex; align-items:center; border:1px solid #30334B;
    background:rgba(1,14,208,.10); color:#C0C5FF; border-radius:4px;
    padding:4px 9px; font-size:.77rem; font-weight:600;
}
.inicio-section-label { color:#868B9A; font-size:.7rem; font-weight:800; letter-spacing:.13em; text-transform:uppercase; }
.st-key-settings-popover {
    display:flex;
    justify-content:flex-end;
    position:relative;
    z-index:5;
    padding-top:2px;
}
.st-key-settings-popover button {
    min-height:2.7rem;
    padding:.62rem 1.05rem;
    background:#151720 !important;
    color:#F3F4F8 !important;
    border:1px solid #41465A !important;
    border-radius:6px;
    box-shadow:0 2px 8px rgba(0,0,0,.18);
}
.st-key-settings-popover button:hover {
    background:#1B1E2A !important;
    border-color:var(--gioval-blue) !important;
    color:#FFFFFF !important;
}
@media (max-width:800px) {
    .block-container {
        padding-left:.85rem !important;
        padding-right:.85rem !important;
        padding-top:4.25rem !important;
    }
    .marca-app { gap:10px; }
    .marca-simbolo { flex-basis:39px; width:39px; height:39px; }
    .marca-nombre { font-size:1.22rem; }
    .marca-descripcion { font-size:.78rem; }
    .inicio-hero { padding:21px 19px; }
    .inicio-hero h2 { font-size:1.45rem !important; max-width:95%; }
    .inicio-hero::before { opacity:.42; right:-110px; }
    .st-key-app-navigation { padding:.15rem .15rem 0; }
    .st-key-app-navigation [data-testid="stRadio"] label { padding:.55rem .6rem !important; }
}
"""
CSS_ANIMACION = """
@keyframes tinta { 0% { background-position: 0% 0%, 100% 0%, 60% 100%, 0% 100%, 0 0; }
                   100% { background-position: 18% 12%, 82% 18%, 70% 82%, 12% 88%, 0 0; } }
.stApp { animation: tinta 80s ease-in-out infinite alternate; }
@media (prefers-reduced-motion: reduce) { .stApp { animation: none; } }
"""
st.markdown("<style>" + CSS_BASE + CSS_TINTA + (CSS_ANIMACION if ANIMAR_FONDO else "")
            + "</style>", unsafe_allow_html=True)


def crear_respaldo_biblioteca(carpeta: Path) -> bytes:
    """Empaqueta una copia consistente de SQLite y los archivos compartidos."""
    if not carpeta.exists():
        raise FileNotFoundError(f"No existe la carpeta de biblioteca: {carpeta}")
    db_origen = carpeta / "cotizador.db"
    if not db_origen.is_file():
        raise FileNotFoundError(f"No existe la base de datos de la biblioteca: {db_origen}")

    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="gioval-respaldo-") as temporal:
        db_copia = Path(temporal) / "cotizador.db"
        origen = sqlite3.connect(str(db_origen), timeout=30)
        destino = sqlite3.connect(str(db_copia), timeout=30)
        try:
            # El backup API de SQLite captura una instantánea coherente aunque otro usuario
            # esté guardando una cotización en ese momento.
            origen.backup(destino)
        finally:
            destino.close()
            origen.close()

        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archivo_zip:
            for ruta in sorted(carpeta.rglob("*")):
                if not ruta.is_file() or "__pycache__" in ruta.parts or ".git" in ruta.parts:
                    continue
                if ruta.name in {"cotizador.db-wal", "cotizador.db-shm"}:
                    continue
                nombre_zip = str(Path(carpeta.name) / ruta.relative_to(carpeta))
                archivo_zip.write(db_copia if ruta == db_origen else ruta, arcname=nombre_zip)
    if buffer.tell() == 0:
        raise ValueError("La biblioteca no contiene archivos para respaldar.")
    return buffer.getvalue()


def restaurar_respaldo_inicial(carpeta: Path, datos_zip: bytes) -> tuple[int, int]:
    """Importa un respaldo ZIP solo si la biblioteca compartida todavía está vacía."""
    carpeta = carpeta.resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    hist_actual = hs.Historial(carpeta)
    bib_actual = ct.Biblioteca(carpeta)
    if hist_actual.listar() or bib_actual.indice:
        raise ValueError("La biblioteca central ya tiene cotizaciones o productos. No se sobrescribió nada.")
    permitidos_vacios = {"cotizador.db", "indice.json"}
    adicionales = [
        p for p in carpeta.rglob("*")
        if p.is_file() and p.name not in permitidos_vacios
    ]
    if adicionales:
        raise ValueError("La biblioteca central no está vacía. No se sobrescribió nada.")

    with tempfile.TemporaryDirectory(prefix="gioval-restaurar-") as temporal:
        stage = Path(temporal) / "biblioteca"
        stage.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(datos_zip)) as archivo_zip:
            miembros = archivo_zip.infolist()
            archivos = [m for m in miembros if not m.is_dir()]
            if not archivos:
                raise ValueError("El ZIP no contiene archivos.")
            total_descomprimido = sum(m.file_size for m in archivos)
            if total_descomprimido > 500 * 1024 * 1024:
                raise ValueError("El respaldo supera el límite de 500 MB.")
            raices = {
                PurePosixPath(m.filename.replace("\\", "/")).parts[0]
                for m in miembros if PurePosixPath(m.filename.replace("\\", "/")).parts
            }
            if len(raices) != 1:
                raise ValueError("El ZIP debe contener una sola carpeta de biblioteca.")
            for miembro in miembros:
                ruta_zip = PurePosixPath(miembro.filename.replace("\\", "/"))
                if ruta_zip.is_absolute() or any(parte in {".", ".."} for parte in ruta_zip.parts):
                    raise ValueError("El ZIP contiene una ruta no permitida.")
                modo = (miembro.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(modo):
                    raise ValueError("El ZIP contiene un enlace simbólico no permitido.")
                relativa = ruta_zip.parts[1:]
                if not relativa:
                    continue
                destino = stage.joinpath(*relativa)
                if not destino.resolve().is_relative_to(stage.resolve()):
                    raise ValueError("El ZIP intenta escribir fuera de la biblioteca.")
                if miembro.is_dir():
                    destino.mkdir(parents=True, exist_ok=True)
                    continue
                destino.parent.mkdir(parents=True, exist_ok=True)
                with archivo_zip.open(miembro) as origen, destino.open("wb") as salida:
                    shutil.copyfileobj(origen, salida)

        db_stage = stage / "cotizador.db"
        if not db_stage.is_file():
            raise ValueError("El respaldo no contiene cotizador.db.")
        with sqlite3.connect(db_stage) as conexion:
            integridad = conexion.execute("PRAGMA integrity_check").fetchone()[0]
            tablas = {
                fila[0] for fila in conexion.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if integridad != "ok" or not {"cotizaciones", "clientes", "config"}.issubset(tablas):
                raise ValueError("La base de datos del respaldo no supera la comprobación de integridad.")

            # El respaldo puede venir de otro computador y contener rutas absolutas del disco
            # de origen. Al importar, reescribe esas rutas al destino central antes de moverlo.
            registros = conexion.execute(
                "SELECT id, numero FROM cotizaciones ORDER BY id"
            ).fetchall()
            for registro_id, numero in registros:
                numero_seguro = re.sub(r"[^A-Za-z0-9._-]", "_", str(numero))
                carpeta_archivos = stage / "cotizaciones" / numero_seguro
                xlsx_stage = carpeta_archivos / f"{numero_seguro}.xlsx"
                pdf_stage = carpeta_archivos / f"{numero_seguro}.pdf"
                xlsx_destino = carpeta / "cotizaciones" / numero_seguro / f"{numero_seguro}.xlsx"
                pdf_destino = carpeta / "cotizaciones" / numero_seguro / f"{numero_seguro}.pdf"
                conexion.execute(
                    "UPDATE cotizaciones SET xlsx=?, pdf=? WHERE id=?",
                    (
                        str(xlsx_destino) if xlsx_stage.is_file() else None,
                        str(pdf_destino) if pdf_stage.is_file() else None,
                        registro_id,
                    ),
                )
            conexion.commit()
            integridad = conexion.execute("PRAGMA integrity_check").fetchone()[0]
            if integridad != "ok":
                raise ValueError("La base de datos no superó la comprobación posterior a la migración.")

        indice_stage = stage / "indice.json"
        if indice_stage.exists():
            indice = json.loads(indice_stage.read_text("utf-8"))
            if not isinstance(indice, dict):
                raise ValueError("El índice de productos del respaldo no es válido.")
        else:
            indice_stage.write_text("{}", encoding="utf-8")

        # Solo se copia al destino después de validar el ZIP completo y la base de datos.
        for origen in stage.rglob("*"):
            destino = carpeta / origen.relative_to(stage)
            if origen.is_dir():
                destino.mkdir(parents=True, exist_ok=True)
            else:
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origen, destino)

    hist_final = hs.Historial(carpeta)
    bib_final = ct.Biblioteca(carpeta)
    return len(hist_final.listar()), len(bib_final.indice)


E.setdefault("paso", 1)

COL_INCLUIR, COL_CANT, COL_PROD, COL_COD, COL_ORIGEN = (
    "Incluir", "Cantidad", "Producto", "Código", "¿Cómo lo identifiqué?")
COL_PRECIO = "Precio unitario"
ORIGEN = {"biblioteca": "Catálogo", "ia": "IA", "texto": "Texto del pedido",
          "descripcion": "Descripción", "": ""}

# ============================================================== CONFIGURACIÓN COMPACTA
# La configuración se renderiza en un popover junto al título, para no ocupar la pantalla.


# ============================================================== FUNCIONES COMUNES
VISTAS = ["Nueva cotización", "Historial", "Catálogo"]
OPCIONES_PRECIO = ["Incluyen IVA (lo que paga el cliente)", "Son netos (sin IVA)"]
if E.get("vista") == "Mis productos":
    E["vista"] = "Catálogo"
E.setdefault("vista", VISTAS[0])


def editor(df, **kw):
    """Tabla editable que se estira para mostrar todas sus filas, sin barra de desplazamiento interna."""
    try:
        return st.data_editor(df, height="content", **kw)
    except Exception:  # versiones antiguas de Streamlit: calcula el alto a mano
        extra = 1 if kw.get("num_rows") == "dynamic" else 0
        return st.data_editor(df, height=min(35 * (len(df) + 1 + extra) + 8, 2400), **kw)


def ir_a(paso: int):
    E["paso"] = paso
    persistir_borrador()
    st.rerun()


def mostrar_pasos(actual: int):
    nombres = ["Pedido", "Productos", "Fotos y fichas", "PDF"]
    piezas = []
    for i, n in enumerate(nombres, 1):
        clase = "hecho" if i < actual else "activo" if i == actual else ""
        piezas.append(f'<div class="paso {clase}"><b>{"✓" if i < actual else i}</b>{n}</div>')
    st.markdown('<div class="pasos">' + "".join(piezas) + "</div>", unsafe_allow_html=True)
    st.progress((actual - 1) / 3, text=f"Etapa {actual} de 4 · {nombres[actual - 1]}")
    if E.get("_ultima_guarda"):
        st.caption(f"Borrador guardado en este computador · {E['_ultima_guarda']}")


def cargar(ruta: Path, origen_pedido: str):
    """Lee una cotización y prepara líneas, datos del cliente y precios editables."""
    lineas = ct.leer_cotizacion(ruta)
    productos = [ln for ln in lineas if ln["es_producto"]]
    ia = {}
    if api_key and productos:
        try:
            with st.spinner("Estoy entendiendo cada producto…"):
                resultados = li.entender_productos([ln["descripcion"] for ln in productos], api_key)
            ia = {ln["descripcion"]: r for ln, r in zip(productos, resultados)}
        except Exception:
            st.warning("No pude usar la IA para entender los productos. Sigo con la búsqueda normal.")

    # Los valores del Excel se aplican al inicio de la siguiente ejecución de Streamlit,
    # no aquí: las pestañas del paso 1 ya pueden haber creado widgets con esas claves.
    if origen_pedido == "cotizacion":
        pendiente = {
            "precios_modo": OPCIONES_PRECIO[1],
            "c_nombre": "", "c_rut": "", "c_dir": "", "c_ciudad": "", "c_tel": "", "c_email": "",
            "c_fecha": date.today(),
        }
        try:
            d = pc.leer_para_pdf(ruta)
            cli = {ct.normalizar(etiqueta): valor for etiqueta, valor in d["izquierda"] + d["derecha"]}
            pendiente.update({
                "c_nombre": cli.get("NOMBRE", ""),
                "c_rut": cli.get("RUT", ""),
                "c_dir": cli.get("DIRECCION", ""),
                "c_ciudad": cli.get("CIUDAD", ""),
                "c_tel": cli.get("TELEFONO", ""),
                "c_email": cli.get("CORREO", cli.get("EMAIL", "")),
            })
            numero_importado = str(cli.get("NCOTIZACION") or "").strip()
            if numero_importado:
                pendiente["cot_numero"] = numero_importado
            fecha_importada = str(cli.get("FECHA") or "").strip()
            try:
                pendiente["c_fecha"] = date.fromisoformat(fecha_importada[:10])
            except ValueError:
                try:
                    pendiente["c_fecha"] = datetime.strptime(fecha_importada, "%d/%m/%Y").date()
                except ValueError:
                    pass
        except Exception:
            pass
        E["_pending_import_header"] = pendiente
    else:
        E.setdefault("c_fecha", date.today())

    for ln in lineas:
        if ln["es_producto"]:
            ln["clave_auto"], ln["origen"] = ct.resolver_clave(bib, ln["descripcion"], ia.get(ln["descripcion"]))
        else:
            ln["clave_auto"], ln["origen"] = "", ""
        ln["incluir"] = bool(ln.get("incluir", True))

    for k in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
        E.pop(k, None)
    E["editado_manual"] = False
    E["info_desc"] = ia
    E["origen_pedido"] = origen_pedido
    E["lineas"] = lineas
    E["tabla"] = tabla_desde_lineas(
        lineas,
        con_iva=(E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]) if origen_pedido == "pedido" else False,
    )
    E["tabla_edit"] = E["tabla"].copy()
    E["tabla_base"] = E["tabla"].copy()
    E["editor_version"] = int(E.get("editor_version", 0)) + 1
    E["ruta"] = Path(ruta)
    _cabecera_pendiente = E.get("_pending_import_header")
    if isinstance(_cabecera_pendiente, dict):
        E["_snapshot_cabecera"] = tuple(
            _texto_seguro(_cabecera_pendiente.get(k, E.get(k)))
            for k in ("c_nombre", "c_rut", "c_dir", "c_ciudad", "c_tel", "c_email")
        ) + (str(_cabecera_pendiente.get("c_fecha", E.get("c_fecha", date.today()))),)
    else:
        E["_snapshot_cabecera"] = tuple(
            _texto_seguro(E.get(k)) for k in ("c_nombre", "c_rut", "c_dir", "c_ciudad", "c_tel", "c_email")
        ) + (str(E.get("c_fecha", date.today())),)
    if E.get("cot_id"):
        registro_guardado = hist.obtener(E["cot_id"])
        if registro_guardado and registro_guardado.get("xlsx") and Path(registro_guardado["xlsx"]).exists():
            E["ruta"] = Path(registro_guardado["xlsx"])
        elif Path(ruta).exists():
            hist.guardar_archivos(E["cot_id"], xlsx=ruta)
            registro_guardado = hist.obtener(E["cot_id"])
            if registro_guardado and registro_guardado.get("xlsx"):
                E["ruta"] = Path(registro_guardado["xlsx"])
        try:
            if GIOVAL_REQUIRE_ACCESS:
                hist.actualizar_si_version(
                    E["cot_id"], E.get("_cotizacion_version"), origen=origen_pedido
                )
            else:
                hist.actualizar(E["cot_id"], origen=origen_pedido)
            registro_actual = hist.obtener(E["cot_id"])
            if registro_actual:
                E["_cotizacion_version"] = registro_actual.get("actualizado")
        except hs.ConflictoActualizacion as exc:
            E["_conflicto_guardado"] = True
            E["_conflicto_guardado_id"] = E.get("cot_id")
            E["_conflicto_guardado_mensaje"] = str(exc)
            return
    persistir_borrador()


def datos_productos():
    tabla = E.get("tabla_edit")
    if tabla is None or tabla.empty:
        return [], {}
    descripciones, claves = {}, []
    for _, fila in tabla.iterrows():
        inc = fila[COL_INCLUIR]
        # Checkbox puede venir como bool, numpy.bool_ o NaN
        if inc is None or (isinstance(inc, float) and pd.isna(inc)) or not bool(inc):
            continue
        desc_filtro = _texto_seguro(fila.get(COL_PROD))
        if not desc_filtro or any(p in ct.normalizar(desc_filtro).lower() for p in ct.PALABRAS_NO_PRODUCTO):
            continue
        clave = str(fila[COL_COD] if pd.notna(fila[COL_COD]) else "").strip().upper()
        if not clave or clave == "NAN":
            # Si está marcado pero sin código, generamos uno a partir del nombre
            desc = str(fila[COL_PROD] or "")
            clave = ct.clave_descriptiva(desc) if desc else ""
        if not clave or clave == "NAN":
            continue
        desc = str(fila[COL_PROD] or "")
        descripciones.setdefault(clave, desc)
        if clave not in claves:
            claves.append(clave)
    return claves, descripciones


def _esta_marcado(valor, defecto=False) -> bool:
    try:
        if pd.isna(valor):
            return defecto
    except (TypeError, ValueError):
        pass
    return bool(valor)


def _texto_seguro(valor) -> str:
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    return str(valor or "").strip()


def tabla_desde_lineas(lineas: list[dict], con_iva: bool | None = None) -> pd.DataFrame:
    """Crea la tabla editable a partir de las líneas guardadas del borrador."""
    if con_iva is None:
        con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
    return pd.DataFrame({
        COL_INCLUIR: [ln.get("incluir", ln.get("es_producto", True)) for ln in lineas],
        COL_CANT: [ln.get("cantidad", 1) for ln in lineas],
        COL_PROD: [ln.get("descripcion", "") for ln in lineas],
        COL_COD: [ln.get("clave_auto", "") for ln in lineas],
        COL_PRECIO: [ct.precio_para_mostrar(
            ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"), con_iva)
            if ln.get("unitario") is not None or ln.get("precio_neto") is not None else None for ln in lineas],
        COL_ORIGEN: [ORIGEN.get(ln.get("origen", ""), "") for ln in lineas],
    })


def lineas_desde_orden(orden: pd.DataFrame, con_iva: bool) -> list[dict]:
    """Convierte el editor de pedido en líneas canónicas para el borrador."""
    salida = []
    for _, fila in orden.iterrows():
        descripcion = _texto_seguro(fila.get("Producto"))
        if not descripcion or descripcion.lower() == "nan":
            continue
        try:
            cantidad = float(fila.get("Cantidad"))
            if pd.isna(cantidad) or cantidad <= 0:
                continue
        except (TypeError, ValueError):
            continue
        precio_ui = fila.get("Precio unitario")
        try:
            precio_neto = None if pd.isna(precio_ui) or float(precio_ui) <= 0 else ct.neto_desde(float(precio_ui), con_iva)
        except (TypeError, ValueError):
            precio_neto = None
        descripcion = ct.limpiar_descripcion(descripcion)
        clave = bib.detectar(descripcion) or ""
        es_producto = not any(p in ct.normalizar(descripcion).lower() for p in ct.PALABRAS_NO_PRODUCTO)
        salida.append({
            "cantidad": ct._cantidad(cantidad), "descripcion": descripcion, "unitario": precio_neto,
            "precio_neto": precio_neto, "es_producto": es_producto,
            "incluir": _esta_marcado(fila.get("Incluir"), True),
            "clave_auto": clave, "origen": "biblioteca" if clave else "descripcion",
        })
    return salida


def cliente_actual() -> dict:
    return {
        "nombre": _texto_seguro(E.get("c_nombre")),
        "rut": _texto_seguro(E.get("c_rut")),
        "direccion": _texto_seguro(E.get("c_dir")),
        "ciudad": _texto_seguro(E.get("c_ciudad")),
        "telefono": _texto_seguro(E.get("c_tel")),
        "email": _texto_seguro(E.get("c_email")),
        "fecha": E.get("c_fecha", date.today()),
    }


def totales_actuales() -> dict:
    lineas = [
        {"cantidad": ln.get("cantidad", 0), "precio_neto": (ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"))}
        for ln in E.get("lineas", [])
        if _esta_marcado(ln.get("incluir", ln.get("es_producto", True)), True)
    ]
    return rv.totales_de(lineas)


def mostrar_totales():
    """Muestra neto, IVA y total actual sin esperar a generar el PDF."""
    tot = totales_actuales()
    a, b, c = st.columns(3)
    a.metric("Neto", rv.clp(tot["neto"]))
    b.metric("IVA (19 %)", rv.clp(tot["iva"]))
    c.metric("Total", rv.clp(tot["total"]))


def mostrar_resumen_lineas():
    """Resumen de lectura con subtotal por línea, precio pendiente e inclusión."""
    tabla = E.get("tabla_edit")
    if not isinstance(tabla, pd.DataFrame) or tabla.empty:
        return
    con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
    etiqueta = "con IVA" if con_iva else "neto"
    filas = []
    for _, fila in tabla.iterrows():
        descripcion = _texto_seguro(fila.get(COL_PROD))
        if not descripcion:
            continue
        incluir = _esta_marcado(fila.get(COL_INCLUIR), True)
        try:
            cantidad = float(fila.get(COL_CANT))
            if pd.isna(cantidad):
                cantidad = 0
        except (TypeError, ValueError):
            cantidad = 0
        try:
            valor = fila.get(COL_PRECIO)
            precio = None if pd.isna(valor) else float(valor)
        except (TypeError, ValueError):
            precio = None
        subtotal = (precio * cantidad) if incluir and precio is not None else (None if incluir else 0)
        estado = "Excluido" if not incluir else ("Precio pendiente" if precio is None else "Incluido")
        filas.append({
            "Estado": estado, "Cantidad": cantidad, "Producto": descripcion,
            "Código": _texto_seguro(fila.get(COL_COD)),
            f"Precio unitario ({etiqueta})": precio,
            f"Subtotal ({etiqueta})": subtotal,
        })
    if not filas:
        return
    with st.expander("Resumen por producto y subtotales", expanded=True):
        st.dataframe(
            pd.DataFrame(filas), hide_index=True, width="stretch",
            column_config={
                "Cantidad": st.column_config.NumberColumn(format="%.2f"),
                f"Precio unitario ({etiqueta})": st.column_config.NumberColumn(format="$ %d"),
                f"Subtotal ({etiqueta})": st.column_config.NumberColumn(format="$ %d"),
            },
        )


def guardar_precios_inventario(lineas: list[dict]):
    """Aprende precios y alias, guardando el índice una sola vez si hubo cambios."""
    cambio_indice = False
    for ln in lineas:
        precio_neto = ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto")
        if not ln.get("es_producto") or precio_neto is None:
            continue
        descripcion = _texto_seguro(ln.get("descripcion"))
        clave = _texto_seguro(ln.get("clave_auto")).upper()
        if not clave:
            clave = ct.clave_descriptiva(descripcion) if descripcion else ""
            ln["clave_auto"] = clave
        if not clave:
            continue
        try:
            precio_neto = float(precio_neto)
            if pd.isna(precio_neto):
                continue
        except (TypeError, ValueError):
            continue

        ficha = bib.indice.setdefault(clave, {"nombre": descripcion or clave, "aliases": []})
        nombre = descripcion or clave
        if ficha.get("nombre") != nombre:
            ficha["nombre"] = nombre
            cambio_indice = True
        if ficha.get("precio_neto") != precio_neto:
            ficha["precio_neto"] = precio_neto
            cambio_indice = True
        aliases = ficha.setdefault("aliases", [])
        if not isinstance(aliases, list):
            aliases = ficha["aliases"] = []
            cambio_indice = True
        alias = ct.normalizar(descripcion)
        if len(alias) >= 6 and alias not in aliases:
            aliases.append(alias)
            cambio_indice = True

    if cambio_indice:
        bib._guardar_indice()


def persistir_borrador():
    """Persiste el trabajo actual en SQLite y evita sobrescribir otra sesión."""
    cot_id = E.get("cot_id")
    if not cot_id or E.get("_conflicto_guardado"):
        return
    registro = hist.obtener(cot_id)
    if not registro:
        return
    pendiente = E.get("_pending_import_header")
    cliente = cliente_actual()
    if isinstance(pendiente, dict):
        nombres_cliente = {
            "c_nombre": "nombre", "c_rut": "rut", "c_dir": "direccion",
            "c_ciudad": "ciudad", "c_tel": "telefono", "c_email": "email",
        }
        for clave_widget, campo_cliente in nombres_cliente.items():
            if clave_widget in pendiente:
                cliente[campo_cliente] = _texto_seguro(pendiente[clave_widget])
        cliente["fecha"] = pendiente.get("c_fecha", cliente.get("fecha", date.today()))
    modo_precios = pendiente.get("precios_modo", E.get("precios_modo", OPCIONES_PRECIO[0])) if isinstance(pendiente, dict) else E.get("precios_modo", OPCIONES_PRECIO[0])
    lineas = E.get("lineas", [])
    tot = totales_actuales()
    orden = None
    if isinstance(E.get("orden_df"), pd.DataFrame):
        try:
            orden = E["orden_df"].astype(object).where(pd.notna(E["orden_df"]), None).to_dict("records")
        except Exception:
            orden = None
    datos = {
        "lineas": lineas,
        "cliente": cliente,
        "origen_pedido": E.get("origen_pedido", "pedido"),
        "precios_modo": modo_precios,
        "paso": int(E.get("paso", 1)),
        "orden_df": orden,
        "orden_texto": E.get("orden_texto", ""),
        "fuente_orden": E.get("fuente_orden", ""),
        "editado_manual": bool(E.get("editado_manual", False)),
    }
    fecha = cliente.get("fecha") or date.today()
    campos_guardado = {
        "fecha": fecha, "cliente": cliente["nombre"], "rut": cliente["rut"],
        "neto": tot["neto"], "iva": tot["iva"], "total": tot["total"],
        "con_iva": int(modo_precios == OPCIONES_PRECIO[0]),
    }
    try:
        if GIOVAL_REQUIRE_ACCESS:
            version_esperada = E.get("_cotizacion_version")
            if not version_esperada:
                E["_conflicto_guardado"] = True
                E["_conflicto_guardado_id"] = cot_id
                E["_conflicto_guardado_mensaje"] = (
                    "Esta sesión no tiene una versión base de la cotización. Recárgala antes de guardar."
                )
                return
            hist.actualizar_si_version(
                cot_id, version_esperada, datos=datos, **campos_guardado
            )
        else:
            hist.actualizar(cot_id, datos=datos, **campos_guardado)
    except hs.ConflictoActualizacion as exc:
        E["_conflicto_guardado"] = True
        E["_conflicto_guardado_id"] = cot_id
        E["_conflicto_guardado_mensaje"] = str(exc)
        return
    registro_actual = hist.obtener(cot_id)
    if registro_actual:
        E["_cotizacion_version"] = registro_actual.get("actualizado")
    guardar_precios_inventario(lineas)
    E["_ultima_guarda"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def widget_numero_cotizacion():
    """Campo editable que actualiza el registro y renombra sus archivos si ya existen."""
    cot_id = E.get("cot_id")
    registro = hist.obtener(cot_id) if cot_id else None
    if not registro:
        st.info("El número se asignará al guardar este borrador.")
        return
    E.setdefault("cot_numero", registro["numero"])
    numero = st.text_input("N° de cotización (editable)", key="cot_numero")
    numero = _texto_seguro(numero)
    if not numero:
        E["error_numero"] = "El número de cotización no puede quedar vacío."
    elif numero != registro["numero"]:
        try:
            hist.renumerar(cot_id, numero)
            E.pop("error_numero", None)
            E["editado_manual"] = True
            for k in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
                E.pop(k, None)
            actualizado = hist.obtener(cot_id)
            if actualizado and actualizado.get("xlsx"):
                E["ruta"] = Path(actualizado["xlsx"])
        except ValueError as exc:
            E["error_numero"] = str(exc)
    else:
        E.pop("error_numero", None)
    if E.get("error_numero"):
        st.error(E["error_numero"])
    st.caption("Puedes cambiar este número antes de crear el PDF.")


def marcar_cambios_cabecera():
    """Detecta cambios en los datos del cliente para que también lleguen al Excel/PDF."""
    campos = ("c_nombre", "c_rut", "c_dir", "c_ciudad", "c_tel", "c_email")
    estado_actual = tuple(_texto_seguro(E.get(k)) for k in campos) + (str(E.get("c_fecha", date.today())),)
    anterior = E.get("_snapshot_cabecera")
    cambio = anterior is not None and anterior != estado_actual
    if cambio:
        E["editado_manual"] = True
        for k in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
            E.pop(k, None)
        # El Excel se actualiza al pedir el recálculo o antes de generar el PDF.
    E["_snapshot_cabecera"] = estado_actual
    return cambio


def editar_cabecera():
    """Permite corregir número, fecha y datos del cliente en cualquier etapa."""
    st.subheader("Datos de la cotización")
    widget_numero_cotizacion()
    for k in ("c_nombre", "c_rut", "c_dir", "c_ciudad", "c_tel", "c_email"):
        E.setdefault(k, "")
    E.setdefault("c_fecha", date.today())
    c1, c2 = st.columns(2)
    c1.text_input("Nombre o razón social", key="c_nombre")
    c2.text_input("RUT", key="c_rut")
    c3, c4 = st.columns(2)
    c3.text_input("Dirección", key="c_dir")
    c4.text_input("Ciudad", key="c_ciudad")
    c5, c6 = st.columns(2)
    c5.text_input("Teléfono", key="c_tel")
    c6.text_input("Correo (opcional)", key="c_email")
    st.date_input("Fecha de la cotización", key="c_fecha")
    marcar_cambios_cabecera()
    if E.get("c_rut") and rv.validar_rut(E["c_rut"]) is False:
        st.warning("El RUT no parece válido; revisa el dígito verificador.")
    persistir_borrador()


def sincronizar_editor_productos(editada: pd.DataFrame):
    """Copia cambios del editor a las líneas canónicas que usa el borrador y el PDF."""
    anteriores = list(E.get("lineas", []))
    tabla_anterior = E.get("tabla_edit")
    cambio = isinstance(tabla_anterior, pd.DataFrame) and not tabla_anterior.equals(editada)
    con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
    nuevas = []
    for i, (_, fila) in enumerate(editada.iterrows()):
        descripcion = _texto_seguro(fila.get(COL_PROD))
        cantidad = fila.get(COL_CANT)
        try:
            if pd.isna(cantidad):
                continue
            cantidad = float(cantidad)
        except (TypeError, ValueError):
            continue
        if not descripcion or descripcion.lower() == "nan" or cantidad <= 0:
            continue
        previa = anteriores[i] if i < len(anteriores) else {}
        precio_ui = fila.get(COL_PRECIO)
        try:
            precio_neto = None if pd.isna(precio_ui) or float(precio_ui) < 0 else ct.neto_desde(float(precio_ui), con_iva)
        except (TypeError, ValueError):
            precio_neto = None
        clave = _texto_seguro(fila.get(COL_COD)).upper()
        es_producto = not any(p in ct.normalizar(descripcion).lower() for p in ct.PALABRAS_NO_PRODUCTO)
        if es_producto and (not clave or clave == "NAN"):
            clave = ct.clave_descriptiva(descripcion)
        origen = previa.get("origen", "descripcion")
        if clave and clave != _texto_seguro(previa.get("clave_auto")).upper():
            origen = "descripcion"
        nuevas.append({
            **previa,
            "fila": previa.get("fila", i + 1),
            "cantidad": ct._cantidad(cantidad),
            "descripcion": ct.limpiar_descripcion(descripcion),
            "unitario": precio_neto,
            "es_producto": es_producto,
            "clave_auto": clave,
            "origen": origen,
            "incluir": _esta_marcado(fila.get(COL_INCLUIR), es_producto),
        })
    E["tabla_edit"] = editada
    E["lineas"] = nuevas
    if cambio:
        E["editado_manual"] = True
        for k in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
            E.pop(k, None)
        guardar_precios_inventario(nuevas)
        # El Excel se regenera al pulsar "Actualizar totales" o al crear el PDF.
        # Evita operaciones de disco pesadas durante la edición normal de cada celda.
        persistir_borrador()


def editor_productos():
    """Lista editable de cantidades, descripciones, códigos y precios para todos los pasos."""
    # No pases de vuelta el dataframe ya editado como nuevo origen: Streamlit mantiene
    # un delta de edición propio del widget y, si el origen cambia en cada rerun,
    # algunas modificaciones quedan una interacción atrasadas.
    base = E.get("tabla_base")
    if not isinstance(base, pd.DataFrame):
        base = E.get("tabla", E.get("tabla_edit"))
        if base is None:
            base = pd.DataFrame(columns=[COL_INCLUIR, COL_CANT, COL_PROD, COL_COD, COL_PRECIO, COL_ORIGEN])
        E["tabla_base"] = base.copy()
    version = int(E.get("editor_version", 0))
    editada = editor(
        base, num_rows="dynamic", hide_index=True, width="stretch",
        key=f"editor_productos_{version}",
        disabled=[COL_ORIGEN],
        column_config={
            COL_INCLUIR: st.column_config.CheckboxColumn("Incluir", width="small"),
            COL_CANT: st.column_config.NumberColumn("Cantidad", min_value=0, step=1, width="small"),
            COL_PROD: st.column_config.TextColumn("Producto", width="large"),
            COL_COD: st.column_config.TextColumn("Código", width="medium"),
            COL_PRECIO: st.column_config.NumberColumn("Precio unitario", min_value=0, format="$ %d", width="medium"),
            COL_ORIGEN: st.column_config.TextColumn("Identificación", width="medium"),
        },
    )
    sincronizar_editor_productos(editada)
    return editada


def boton_actualizar_totales():
    """Fuerza el recálculo visible y guarda los datos actuales del borrador."""
    c_boton, c_ayuda = st.columns([1.25, 3])
    with c_boton:
        actualizar = st.button(
            "Actualizar totales",
            icon=":material/calculate:",
            key=f"actualizar_totales_{int(E.get('paso', 1))}",
            help="Confirma el precio editado, recalcula los importes y guarda el borrador.",
            width="stretch",
        )
    with c_ayuda:
        st.caption("Al terminar de editar, presiona Enter o haz clic fuera de la celda. Luego selecciona «Actualizar totales» para guardar y recalcular.")

    if actualizar:
        persistir_borrador()
        # Actualiza el Excel intermedio solo a petición del usuario; nunca se hace
        # durante cada cambio de celda. En la generación final se vuelve a actualizar.
        try:
            if E.get("lineas") and E.get("ruta"):
                refrescar_excel_actual()
        except Exception as exc:
            st.warning(f"El cálculo está actualizado, pero el Excel intermedio no se regeneró: {exc}")
        E["_ultima_guarda"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        st.success("Totales recalculados y borrador guardado.")


def refrescar_excel_actual():
    """Genera una copia actualizada del Excel para que el PDF use la última edición."""
    cot_id = E.get("cot_id")
    if not cot_id or "lineas" not in E:
        return
    if E.get("origen_pedido") == "cotizacion" and not E.get("editado_manual", False) and not GIOVAL_REQUIRE_ACCESS:
        return  # En modo local se respeta el Excel subido; el espacio compartido lo regenera sin datos de cliente.
    registro = hist.obtener(cot_id)
    if not registro:
        return
    lineas_excel = []
    for ln in E.get("lineas", []):
        if not _esta_marcado(ln.get("incluir", ln.get("es_producto", True)), True):
            continue
        lineas_excel.append({
            "cantidad": ln.get("cantidad", 1),
            "descripcion": ln.get("descripcion", ""),
            "precio_neto": (ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto")),
        })
    cliente = cliente_actual()
    cliente["numero"] = registro["numero"]
    ruta_tmp = Path(tempfile.mkdtemp()) / "cotizacion_actualizada.xlsx"
    ct.crear_cotizacion_xlsx(lineas_excel, cliente, ruta_tmp)
    if GIOVAL_REQUIRE_ACCESS:
        # El Excel contiene los datos del cliente de esta sesión: queda temporal y solo se descarga.
        E["ruta"] = ruta_tmp
    else:
        hist.guardar_archivos(cot_id, xlsx=ruta_tmp)
        registro = hist.obtener(cot_id)
        if registro and registro.get("xlsx"):
            E["ruta"] = Path(registro["xlsx"])


def iniciar_borrador():
    """Crea inmediatamente una cotización vacía, lista para guardar avances."""
    E.clear()
    fecha = date.today()
    cliente = {"nombre": "", "rut": "", "direccion": "", "ciudad": "", "telefono": "", "email": "", "fecha": fecha}
    cot_id, numero = hist.crear(cliente, [], {"neto": 0, "iva": 0, "total": 0},
                                con_iva=True, fecha=fecha, origen="pedido")
    E.update(
        cot_id=cot_id, cot_numero=numero, c_fecha=fecha, precios_modo=OPCIONES_PRECIO[0],
        paso=1, vista=VISTAS[0], lineas=[], orden_texto="", fuente_orden="",
        editor_version=1, editado_manual=False, origen_pedido="pedido",
        _cotizacion_version=(hist.obtener(cot_id) or {}).get("actualizado"),
    )
    persistir_borrador()


def restaurar_borrador(cot_id: int):
    """Recupera desde SQLite una cotización incompleta, con su lista y precios."""
    registro = hist.obtener(cot_id)
    if not registro:
        return
    datos = registro.get("datos", {})
    cliente = datos.get("cliente", {})
    lineas_restauradas = datos.get("lineas", [])
    for ln in lineas_restauradas:
        if ln.get("unitario") is None and ln.get("precio_neto") is not None:
            ln["unitario"] = ln["precio_neto"]
        descripcion = _texto_seguro(ln.get("descripcion"))
        ln.setdefault("es_producto", not any(p in ct.normalizar(descripcion).lower() for p in ct.PALABRAS_NO_PRODUCTO))
        ln.setdefault("incluir", True)
        ln.setdefault("clave_auto", bib.detectar(descripcion) or (ct.clave_descriptiva(descripcion) if ln.get("es_producto") and descripcion else ""))
        ln.setdefault("origen", "biblioteca" if bib.detectar(descripcion) else "descripcion")
    E.clear()
    try:
        fecha = date.fromisoformat(str(registro.get("fecha") or "")[:10])
    except ValueError:
        fecha = date.today()
    E.update(
        cot_id=cot_id, cot_numero=registro["numero"], c_fecha=fecha,
        c_nombre=cliente.get("nombre", registro.get("cliente", "")),
        c_rut=cliente.get("rut", registro.get("rut", "")),
        c_dir=cliente.get("direccion", ""), c_ciudad=cliente.get("ciudad", ""),
        c_tel=cliente.get("telefono", ""), c_email=cliente.get("email", ""),
        _cotizacion_version=registro.get("actualizado"),
        precios_modo=datos.get("precios_modo", OPCIONES_PRECIO[0 if registro.get("con_iva", 1) else 1]),
        origen_pedido=datos.get("origen_pedido", registro.get("origen", "pedido")),
        paso=int(datos.get("paso", 1)), lineas=lineas_restauradas,
        orden_texto=datos.get("orden_texto", ""), fuente_orden=datos.get("fuente_orden", ""),
        editado_manual=bool(datos.get("editado_manual", False)), editor_version=1,
        vista=VISTAS[0],
    )
    if isinstance(datos.get("orden_df"), list):
        df = pd.DataFrame(datos["orden_df"])
        for columna, defecto in (("Incluir", True), ("Cantidad", None), ("Producto", ""), ("Precio unitario", None)):
            if columna not in df:
                df[columna] = defecto
        df = df[["Incluir", "Cantidad", "Producto", "Precio unitario"]]
        if not df.empty:
            df["Cantidad"] = pd.to_numeric(df["Cantidad"], errors="coerce")
            df["Precio unitario"] = pd.to_numeric(df["Precio unitario"], errors="coerce")
        E["orden_df"] = df
        E["orden_df_base"] = df.copy()
        E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
    elif lineas_restauradas:
        # Reconstruye la tabla desde la lista comercial saneada, sin requerir
        # el pedido fuente ni un Excel que pudiera contener datos del cliente.
        df = pd.DataFrame({
            "Incluir": [_esta_marcado(ln.get("incluir"), True) for ln in lineas_restauradas],
            "Cantidad": [ln.get("cantidad", 1) for ln in lineas_restauradas],
            "Producto": [ln.get("descripcion", "") for ln in lineas_restauradas],
            "Precio unitario": [ct.precio_para_mostrar(
                ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"),
                bool(registro.get("con_iva", 1)))
                if (ln.get("unitario") is not None or ln.get("precio_neto") is not None) else None
                for ln in lineas_restauradas],
        })
        E["orden_df"] = df
        E["orden_df_base"] = df.copy()
        E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
    if E["lineas"]:
        E["tabla"] = tabla_desde_lineas(E["lineas"])
        E["tabla_edit"] = E["tabla"].copy()
        E["tabla_base"] = E["tabla"].copy()
        if registro.get("xlsx") and Path(registro["xlsx"]).exists():
            E["ruta"] = Path(registro["xlsx"])
        else:
            refrescar_excel_actual()
    elif registro.get("xlsx") and Path(registro["xlsx"]).exists():
        E["ruta"] = Path(registro["xlsx"])
    E["paso"] = E["paso"] if E["lineas"] else 1
    persistir_borrador()


def selector_borradores():
    """Panel de inicio: accesos rápidos, métricas y borradores para retomar."""
    borradores = []
    for reg in hist.listar("", "Borrador"):
        pdf = Path(reg["pdf"]) if reg.get("pdf") else None
        if not pdf or not pdf.exists():
            borradores.append(reg)

    st.markdown(
        '<section class="inicio-hero">'
        '<div class="inicio-kicker">WORKSPACE / GIOVAL&nbsp;&nbsp; · &nbsp;&nbsp;COTIZACIONES</div>'
        '<h2>De tu pedido a una cotización lista.</h2>'
        '<p>Gestiona pedidos, precios, fotos y fichas técnicas en un espacio de trabajo. '
        'Prepara una propuesta ordenada, revisa los totales y descarga un PDF profesional para compartir.</p>'
        '<div class="inicio-badges">'
        '<span class="inicio-badge">Atención cercana</span>'
        '<span class="inicio-badge">Soluciones para empresas y personas</span>'
        '<span class="inicio-badge">Experiencia local en el norte</span>'
        '</div></section>',
        unsafe_allow_html=True,
    )
    a, b = st.columns([3, 2])
    with a:
        st.markdown('<div class="inicio-section-label">Empezar</div>', unsafe_allow_html=True)
        st.subheader("¿Preparamos una cotización?")
        st.caption("Crea un pedido nuevo o continúa con un trabajo pendiente.")
        st.button("Crear nueva cotización", icon=":material/add_circle:", type="primary", width="stretch", on_click=iniciar_borrador,
                  key="inicio_nueva_cotizacion")
    with b:
        st.markdown('<div class="inicio-section-label">Control comercial</div>', unsafe_allow_html=True)
        st.subheader("Revisa tu actividad")
        st.caption("Consulta cotizaciones anteriores y vuelve a contactar a tus clientes.")
        st.button("Abrir historial", icon=":material/history:", width="stretch",
                  on_click=lambda: E.update(vista=VISTAS[1]), key="inicio_abrir_historial")

    st.divider()
    st.subheader("Retomar un borrador")
    if borradores:
        opciones = {
            f"{r['numero']} · {r.get('cliente') or 'Sin cliente'} · {fecha_corta(r.get('fecha') or '')}": r["id"]
            for r in borradores
        }
        elegido = st.selectbox("Selecciona una cotización sin terminar", list(opciones), key="borrador_a_retomar")
        c1, c2 = st.columns(2)
        c1.button(
            "Retomar borrador", icon=":material/play_arrow:", type="primary", width="stretch",
            on_click=restaurar_borrador, args=(opciones[elegido],), key="inicio_retomar_borrador",
        )
        c2.button("Iniciar otro borrador", icon=":material/add_circle:", width="stretch",
                  on_click=iniciar_borrador, key="inicio_otro_borrador")
    else:
        st.info("No tienes borradores pendientes. Cuando inicies una cotización, podrás retomarla desde aquí aunque se cierre la aplicación.")
        st.button("Comenzar ahora →", type="primary", width="stretch",
                  on_click=iniciar_borrador, key="inicio_comenzar_ahora")

# ============================================================== PASO 1: PEDIDO
def aplicar_cliente_guardado():
    """Al elegir un cliente guardado, llena los datos de abajo."""
    elegido = E.get("cli_sel")
    for c in hist.clientes():
        if c["nombre"] == elegido:
            E.update(c_nombre=c["nombre"], c_rut=c["rut"], c_dir=c["direccion"], c_ciudad=c["ciudad"],
                     c_tel=c["telefono"], c_email=c["email"])
            return
    E.update(c_nombre="", c_rut="", c_dir="", c_ciudad="", c_tel="", c_email="")


def pedido_ui():
    st.write("El pedido se conserva como borrador mientras trabajas. Puedes volver a abrirlo si la aplicación se interrumpe.")
    con_iva = st.radio("Los precios que voy a escribir…", OPCIONES_PRECIO, horizontal=True,
                       key="precios_modo") == OPCIONES_PRECIO[0]
    FUENTES = ["Lo escribo o pego aquí", "Subir Excel o CSV", "Subir PDF o foto"]
    fuente = st.radio("¿Cómo me das el pedido?", FUENTES, horizontal=True, key="fuente_pedido")
    E["fuente_orden"] = fuente
    texto = st.text_area(
        "Escribe un producto por línea, con su cantidad adelante",
        placeholder="10 Tóner HP CF283A\n15 Tóner Kyocera TK-3182\n2 Resma carta",
        height=170, key="orden_texto",
    ) if fuente == FUENTES[0] else ""
    archivo_orden, archivos_ia = None, []
    if fuente == FUENTES[1]:
        archivo_orden = st.file_uploader("Sube el Excel o CSV del pedido", type=["xlsx", "csv"], key="orden_archivo")
    elif fuente == FUENTES[2]:
        archivos_ia = st.file_uploader("Sube el PDF o las fotos del pedido (puedes subir varias)",
                                       type=["pdf", "jpg", "jpeg", "png", "webp"],
                                       accept_multiple_files=True, key="orden_ia")
        st.caption("El pedido se envía a OpenAI para leerlo. Después revisa que haya quedado bien.")
        if not api_key:
            st.warning("Para leer PDF o fotos necesito la clave de OpenAI (Ajustes, arriba a la derecha).")

    if st.button("Leer el pedido", icon=":material/description:", type="primary", width="stretch"):
        try:
            if fuente == FUENTES[2]:
                if not archivos_ia:
                    raise ValueError("Primero sube un PDF o una foto.")
                with st.spinner("Leyendo el pedido…"):
                    filas = li.leer_orden_ia([(f.name, f.getvalue()) for f in archivos_ia], api_key)
            elif fuente == FUENTES[1]:
                if not archivo_orden:
                    raise ValueError("Primero sube un Excel o CSV.")
                tmp = Path(tempfile.mkdtemp()) / archivo_orden.name
                tmp.write_bytes(archivo_orden.getvalue())
                filas = ct.leer_orden_archivo(tmp)
            else:
                filas = ct.parsear_texto_orden(texto)
        except Exception as exc:
            st.error(f"No pude leer el pedido: {exc}")
            filas = []
        if not filas:
            st.warning("No encontré productos. Revisa el pedido e inténtalo de nuevo.")
        else:
            precios = []
            for fila in filas:
                clave = bib.detectar(fila["descripcion"])
                neto = bib.precio_sugerido(clave) or bib.indice.get(clave, {}).get("precio_neto")
                precios.append(ct.precio_para_mostrar(neto, con_iva) if neto else None)
            E["orden_df"] = pd.DataFrame({
                "Incluir": [True] * len(filas),
                "Cantidad": [fila["cantidad"] for fila in filas],
                "Producto": [fila["descripcion"] for fila in filas],
                "Precio unitario": precios,
            })
            E["orden_df_base"] = E["orden_df"].copy()
            E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
            E["editor_version"] = int(E.get("editor_version", 0)) + 1

    st.subheader("Datos de la cotización")
    widget_numero_cotizacion()
    for k in ("c_nombre", "c_rut", "c_dir", "c_ciudad", "c_tel", "c_email"):
        E.setdefault(k, "")
    E.setdefault("c_fecha", date.today())
    c1, c2 = st.columns(2)
    nombre = c1.text_input("Nombre o razón social", key="c_nombre")
    rut = c2.text_input("RUT", key="c_rut")
    if rut and rv.validar_rut(rut) is False:
        c2.caption("Este RUT no parece válido. Revisa el dígito final.")
    c3, c4 = st.columns(2)
    c3.text_input("Dirección", key="c_dir")
    c4.text_input("Ciudad", key="c_ciudad")
    c5, c6 = st.columns(2)
    c5.text_input("Teléfono", key="c_tel")
    c6.text_input("Correo (opcional)", key="c_email")
    st.date_input("Fecha de la cotización", key="c_fecha")
    marcar_cambios_cabecera()

    if "orden_df" not in E:
        st.info("Escribe o carga un pedido para comenzar. Tus datos de cliente y el número se guardan en el borrador.")
        persistir_borrador()
        return

    st.divider()
    st.subheader("Productos y precios")
    st.caption("Puedes cambiar cantidades, nombres y precios, agregar filas y dejar precios pendientes. Los totales se actualizan antes de avanzar.")
    orden_base = E.get("orden_df_base")
    if not isinstance(orden_base, pd.DataFrame):
        orden_base = E["orden_df"].copy()
        E["orden_df_base"] = orden_base.copy()
    orden_version = int(E.get("orden_editor_version", 0))
    orden = editor(
        orden_base, num_rows="dynamic", hide_index=True, width="stretch",
        key=f"orden_editor_{orden_version}",
        column_config={
            "Incluir": st.column_config.CheckboxColumn("Incluir", width="small"),
            "Cantidad": st.column_config.NumberColumn(min_value=0, step=1, width="small"),
            "Producto": st.column_config.TextColumn("Producto", width="large"),
            "Precio unitario": st.column_config.NumberColumn(min_value=0, format="$ %d"),
        },
    )
    E["orden_df"] = orden
    lineas_orden = lineas_desde_orden(orden, con_iva)
    E["lineas"] = lineas_orden
    E["tabla"] = tabla_desde_lineas(lineas_orden)
    E["tabla_edit"] = E["tabla"].copy()
    E["origen_pedido"] = "pedido"
    boton_actualizar_totales()
    mostrar_totales()

    sin_precio = [ln["descripcion"][:45] for ln in lineas_orden
                  if ln.get("incluir", True) and ln.get("unitario") is None]
    continuar_igual = True
    if sin_precio:
        st.warning("Faltan precios en: " + ", ".join(sin_precio))
        continuar_igual = st.checkbox("Quiero continuar igual y completar los precios después", key="ok_sin_precio")
    if st.button("Guardar pedido y continuar →", type="primary", width="stretch",
                 disabled=not any(ln.get("incluir", True) for ln in lineas_orden) or not continuar_igual):
        cliente = cliente_actual()
        totales = rv.totales_de([
            {"cantidad": ln["cantidad"], "precio_neto": ln.get("unitario")}
            for ln in lineas_orden if ln.get("unitario") is not None
        ])
        cot_id = E.get("cot_id")
        if not cot_id:
            cot_id, numero = hist.crear(cliente, lineas_orden, totales, con_iva, cliente["fecha"], origen="pedido")
            E["cot_id"], E["cot_numero"] = cot_id, numero
        else:
            registro = hist.obtener(cot_id)
            if registro and _texto_seguro(E.get("cot_numero")) != registro["numero"]:
                try:
                    hist.renumerar(cot_id, _texto_seguro(E.get("cot_numero")))
                except ValueError as exc:
                    st.error(str(exc))
                    return
        registro = hist.obtener(E["cot_id"])
        if not registro:
            st.error("No pude recuperar el borrador. No se avanzó para evitar perder los datos.")
            return
        ruta = Path(tempfile.mkdtemp()) / (
            f"Cotizacion_{re.sub(r'[^A-Za-z0-9._-]', '_', registro['numero'])}_"
            f"{re.sub(r'[^A-Za-z0-9]+', '_', nombre).strip('_')[:30] or 'cliente'}.xlsx"
        )
        try:
            ct.crear_cotizacion_xlsx(
                [{"cantidad": ln["cantidad"], "descripcion": ln["descripcion"], "precio_neto": ln.get("unitario")}
                 for ln in lineas_orden if _esta_marcado(ln.get("incluir"), True)],
                {**cliente, "numero": registro["numero"]}, ruta,
            )
            campos_cotizacion = {
                "fecha": cliente["fecha"], "cliente": nombre, "rut": rut,
                "neto": totales["neto"], "iva": totales["iva"], "total": totales["total"],
                "con_iva": int(con_iva), "origen": "pedido",
            }
            datos_cotizacion = {
                "lineas": lineas_orden, "cliente": cliente, "precios_modo": E["precios_modo"],
                "origen_pedido": "pedido", "paso": 2,
            }
            if GIOVAL_REQUIRE_ACCESS:
                hist.actualizar_si_version(
                    E["cot_id"], E.get("_cotizacion_version"),
                    datos=datos_cotizacion, **campos_cotizacion
                )
            else:
                hist.actualizar(E["cot_id"], datos=datos_cotizacion, **campos_cotizacion)
            registro_nuevo = hist.obtener(E["cot_id"])
            if registro_nuevo:
                E["_cotizacion_version"] = registro_nuevo.get("actualizado")
            hist.guardar_archivos(E["cot_id"], xlsx=ruta)
            hist.guardar_cliente(cliente)
        except Exception as exc:
            st.error(f"No pude guardar el borrador. Los datos siguen en pantalla: {exc}")
            return
        registro_guardado = hist.obtener(E["cot_id"])
        ruta_guardada = registro_guardado.get("xlsx") if registro_guardado else None
        # El modo compartido no archiva Excel con datos personales: usa el temporal de esta sesión.
        E["ruta"] = Path(ruta_guardada) if ruta_guardada and Path(ruta_guardada).exists() else ruta
        cargar(E["ruta"], "pedido")
        ir_a(2)
    else:
        persistir_borrador()


def cotizacion_ui():
    st.write("Si ya tienes la cotización armada en Excel, súbela y yo le agrego las fotos y fichas.")
    archivo = st.file_uploader("Sube la cotización en Excel", type=["xlsx"], key="cot_archivo")
    if archivo and st.button("Continuar →", type="primary", width="stretch", key="cot_continuar"):
        ruta = Path(tempfile.mkdtemp()) / archivo.name
        ruta.write_bytes(archivo.getvalue())
        try:
            cargar(ruta, "cotizacion")
        except ValueError as e:
            st.error(str(e))
            return
        ir_a(2)


def paso_1():
    st.header("1. ¿Qué tienes?")
    if not api_key:
        st.info("Con la clave de OpenAI (en Ajustes, arriba a la derecha) entiendo mucho mejor los "
                "productos y puedo leer pedidos en PDF o foto.")
    tab_pedido, tab_cot = st.tabs(["Tengo un pedido", "Ya tengo la cotización hecha"])
    with tab_pedido:
        pedido_ui()
    with tab_cot:
        cotizacion_ui()


def agregar_desde_catalogo():
    """Permite buscar un producto conocido e incorporarlo a la cotización actual."""
    with st.expander("Agregar producto desde mi catálogo", expanded=False):
        filas = bib.filas_catalogo()
        if not filas:
            st.info("Todavía no hay productos en el catálogo. Puedes agregarlos aquí o desde una cotización.")
            return
        busqueda = st.text_input("Buscar por código o nombre", key="catalogo_busqueda",
                                 placeholder="Ej.: CF283A, tóner HP, resma oficio")
        consulta = busqueda.strip().casefold()
        filtradas = [f for f in filas if not consulta or consulta in (str(f["clave"]) + " " + str(f["nombre"])).casefold()]
        if not filtradas:
            st.info("No encontré productos con esa búsqueda. Prueba con otra palabra o código.")
            return
        opciones = [f"{f['clave']} · {f['nombre']}" for f in filtradas]
        key_busqueda = busqueda.strip().lower().replace(" ", "_")[:24] or "todos"
        elegido = st.selectbox("Producto del catálogo", opciones, key=f"catalogo_producto_{key_busqueda}")
        producto = filtradas[opciones.index(elegido)]
        con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
        precio_neto_base = producto.get("sugerido_neto")
        if precio_neto_base is None:
            precio_neto_base = producto.get("precio_neto")
        precio_sugerido = (ct.precio_para_mostrar(float(precio_neto_base), con_iva)
                           if precio_neto_base is not None else None)
        st.caption(f"Código: {producto['clave']} · {('precio ingresado con IVA' if con_iva else 'precio neto sin IVA')}")
        c1, c2 = st.columns([1, 2])
        cantidad = c1.number_input("Cantidad", min_value=1, value=1, step=1,
                                   key=f"catalogo_cantidad_{producto['clave']}")
        precio_ui = c2.number_input(
            "Precio unitario", min_value=0.0, step=100.0,
            value=float(precio_sugerido or 0), format="%.0f",
            key=f"catalogo_precio_{producto['clave']}",
        )
        pendiente = st.checkbox(
            "Dejar el precio pendiente para completarlo después",
            value=precio_sugerido is None, key=f"catalogo_pendiente_{producto['clave']}",
        )
        if precio_sugerido is None:
            st.caption("Este producto no tiene un último precio ni un precio sugerido guardado.")
        if st.button("Agregar a la cotización", type="primary", width="stretch",
                     key=f"catalogo_agregar_{producto['clave']}"):
            precio_neto = None if pendiente or precio_ui <= 0 else ct.neto_desde(float(precio_ui), con_iva)
            linea = {
                "fila": len(E.get("lineas", [])) + 1,
                "cantidad": ct._cantidad(float(cantidad)),
                "descripcion": producto["nombre"] or producto["clave"],
                "unitario": precio_neto, "precio_neto": precio_neto,
                "es_producto": True, "clave_auto": producto["clave"],
                "origen": "biblioteca", "incluir": True,
            }
            lineas_actuales = list(E.get("lineas", []))
            lineas_actuales.append(linea)
            E["lineas"] = lineas_actuales
            E["tabla"] = tabla_desde_lineas(lineas_actuales, con_iva=con_iva)
            E["tabla_edit"] = E["tabla"].copy()
            E["tabla_base"] = E["tabla"].copy()
            E["editor_version"] = int(E.get("editor_version", 0)) + 1
            E["editado_manual"] = True
            for clave_estado in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
                E.pop(clave_estado, None)
            refrescar_excel_actual()
            persistir_borrador()
            st.rerun()


# ============================================================== PASO 2: PRODUCTOS
def paso_2():
    st.header("2. Revisa tus productos y precios")
    st.write("Puedes agregar, quitar o modificar cantidades, descripciones, códigos y precios aquí. "
             "Lo que cambies se conserva al avanzar y se usa al crear el PDF.")
    if not api_key:
        st.info("Con la clave de OpenAI puedo deducir mejor la marca, el modelo y el código.")

    agregar_desde_catalogo()
    editor_productos()
    boton_actualizar_totales()
    mostrar_totales()
    mostrar_resumen_lineas()
    editar_cabecera()
    panel_revision()

    atras, sigue = st.columns(2)
    if atras.button("← Volver", width="stretch"):
        con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
        E["orden_df"] = pd.DataFrame({
            "Incluir": [_esta_marcado(ln.get("incluir"), True) for ln in E.get("lineas", [])],
            "Cantidad": [ln.get("cantidad", 1) for ln in E.get("lineas", [])],
            "Producto": [ln.get("descripcion", "") for ln in E.get("lineas", [])],
            "Precio unitario": [ct.precio_para_mostrar(
                ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"), con_iva)
                if (ln.get("unitario") is not None or ln.get("precio_neto") is not None) else None
                for ln in E.get("lineas", [])],
        })
        E["orden_df_base"] = E["orden_df"].copy()
        E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
        ir_a(1)
    if sigue.button("Siguiente: fotos y fichas →", type="primary", width="stretch"):
        claves, _ = datos_productos()
        if not claves:
            st.error("Marca al menos un producto en la columna **Incluir** para buscar sus fotos y fichas.")
            return
        persistir_borrador()
        ir_a(3)


# ============================================================== PASO 3: FOTOS Y FICHAS
@st.cache_data(show_spinner=False)
def menciones_guardada(ruta: str, mtime: float, clave: str) -> int:
    """Veces que la ficha guardada nombra el código (-1 = no se pudo leer)."""
    try:
        return len(bs.menciones_clave(Path(ruta).read_bytes(), [clave]))
    except Exception:
        return -1


def consultas_sugeridas(clave: str, desc: str) -> tuple[str, str]:
    info = E.get("info_desc", {}).get(desc, {})
    base = bs.limpiar_consulta(desc)
    foto, ficha = info.get("consulta_foto") or base, info.get("consulta_ficha") or base
    if bs.es_codigo(clave):
        foto = foto if ct.normalizar(clave) in ct.normalizar(foto) else f"{clave} {foto}"
        ficha = ficha if ct.normalizar(clave) in ct.normalizar(ficha) else f"{clave} {ficha}"
    return foto, ficha


def detalle_busqueda(d: dict, tipo: str) -> str:
    if not d:
        return ""
    if not d.get("crudos"):
        return "El buscador no respondió (puede ser un límite temporal): intenta de nuevo en un minuto."
    if tipo == "foto":
        return f"Revisé {d['crudos']} imágenes, pero eran muy pequeñas o no se pudieron descargar."
    return f"Revisé {d['crudos']} resultados, pero ninguno era una ficha en PDF utilizable."


def buscar_para(clave: str, q_foto: str, q_ficha: str, fotos=True, fichas=True) -> list[str]:
    avisos = []
    for activo, tipo, funcion, consulta in ((fotos, "foto", bs.buscar_fotos, q_foto),
                                            (fichas, "ficha", bs.buscar_fichas, q_ficha)):
        if activo:
            diag = {}
            try:
                E[f"cand_{tipo}_{clave}"] = funcion(consulta, clave=clave, diag=diag)
            except bs.ErrorBusqueda:
                avisos.append("El buscador no respondió. Intenta de nuevo en un minuto.")
            E[f"diag_{tipo}_{clave}"] = diag
    return avisos


def guardar_candidato(clave: str, tipo: str, c: dict):
    bib.guardar_archivo(clave, tipo, "x" + c["ext"] if tipo == "foto" else "f.pdf", c["datos"])
    E.pop(f"cand_{tipo}_{clave}", None)
    st.rerun()


def tarjeta(clave: str, desc: str):
    foto, ficha = bib.archivos(clave)
    aviso_ficha = ""
    if ficha:
        n = menciones_guardada(str(ficha), ficha.stat().st_mtime, clave) if bs.es_codigo(clave) else -2
        estado_ficha = "Revisar" if n == 0 else "Disponible"
        if n == 0:
            aviso_ficha = (f"La ficha guardada no menciona el código «{clave}». Puede ser de otro producto: "
                           "busca otra o revísala.")
    else:
        estado_ficha = "Falta"
    with st.container(border=True):
        st.markdown('<span class="tarjeta-marca"></span>', unsafe_allow_html=True)
        c1, c2 = st.columns([1, 3])
        with c1:
            if foto:
                try:
                    st.image(str(foto), width=190)
                except Exception:  # archivo dañado: no debe botar la pantalla
                    st.markdown(" \n*No pude mostrar la foto. Elige otra.*")
            else:
                st.markdown(" \n*Sin foto*")
        with c2:
            st.markdown(f"**{desc}**")
            st.caption(f"Código: {clave}")
            st.markdown(f"**Foto:** {'Disponible' if foto else 'Falta'} &nbsp; · &nbsp; **Ficha técnica:** {estado_ficha}")
            b1, b2 = st.columns(2)
            sug_foto, sug_ficha = consultas_sugeridas(clave, desc)
            q_foto, q_ficha = E.get(f"qf_{clave}", sug_foto), E.get(f"qd_{clave}", sug_ficha)
            if b1.button("Buscar en internet", key=f"b_{clave}", width="stretch"):
                with st.spinner("Buscando…"):
                    for aviso in buscar_para(clave, q_foto, q_ficha):
                        st.warning(aviso)
            with b2.popover("Más opciones", width="stretch"):
                st.text_input("Buscar la foto como", value=sug_foto, key=f"qf_{clave}")
                st.text_input("Buscar la ficha como", value=sug_ficha, key=f"qd_{clave}")
                if ficha and st.button("Sacar la foto desde la ficha", key=f"xf_{clave}"):
                    E[f"cand_foto_{clave}"] = bs.fotos_de_ficha(ficha.read_bytes())
                st.file_uploader("Subir mi propia foto", type=["jpg", "jpeg", "png", "webp"], key=f"foto_{clave}")
                st.file_uploader("Subir mi propia ficha (PDF)", type=["pdf"], key=f"ficha_{clave}")
                url_foto = st.text_input("…o pegar el enlace de una imagen", key=f"urlf_{clave}")
                if st.button("Usar ese enlace de imagen", key=f"dlf_{clave}") and url_foto:
                    try:
                        datos, ext, _, _ = bs.descargar_imagen(url_foto.strip())
                        bib.guardar_archivo(clave, "foto", "x" + ext, datos)
                        st.rerun()
                    except Exception:
                        st.error("No pude usar ese enlace.")
                url_pdf = st.text_input("…o pegar el enlace de un PDF", key=f"urlp_{clave}")
                if st.button("Usar ese enlace de PDF", key=f"dlp_{clave}") and url_pdf:
                    try:
                        datos, _ = bs.descargar_pdf(url_pdf.strip())
                        bib.guardar_archivo(clave, "ficha", "f.pdf", datos)
                        st.rerun()
                    except Exception:
                        st.error("No pude usar ese enlace.")
                st.link_button("Buscar fotos en Google", f"https://www.google.com/search?tbm=isch&q={quote_plus(q_foto)}")
                st.link_button("Buscar ficha en Google",
                               f"https://www.google.com/search?q={quote_plus(q_ficha + ' ficha técnica filetype:pdf')}")
        if aviso_ficha:
            st.warning(aviso_ficha)

        fotos_c = E.get(f"cand_foto_{clave}")
        if fotos_c is not None:
            if not fotos_c:
                st.info("No encontré fotos. " + detalle_busqueda(E.get(f"diag_foto_{clave}", {}), "foto")
                        + " Prueba con «Más opciones».")
            else:
                st.markdown("**Elige la foto que corresponde:**")
                cols = st.columns(3)
                for i, c in enumerate(fotos_c):
                    with cols[i % 3]:
                        st.image(c["datos"], width=260)
                        st.caption(("Coincide con el código" if c["verificada"] else "Sin confirmar")
                                   + (" · baja calidad" if c.get("baja_resolucion") else ""))
                        if st.button("Elegir esta foto", key=f"uf_{clave}_{i}", width="stretch"):
                            guardar_candidato(clave, "foto", c)
        fichas_c = E.get(f"cand_ficha_{clave}")
        if fichas_c is not None:
            if not fichas_c:
                st.info("No encontré una ficha técnica en PDF. " + detalle_busqueda(
                    E.get(f"diag_ficha_{clave}", {}), "ficha") + " Es normal en productos de librería: "
                        "el PDF llevará solo la foto.")
            else:
                st.markdown("**Elige la ficha técnica que corresponde:**")
                for i, c in enumerate(fichas_c):
                    k1, k2 = st.columns([1, 4])
                    if c["miniatura"]:
                        k1.image(c["miniatura"], width="stretch")
                    if not c.get("verificable", True):
                        estado = "Abre el PDF y confirma que es el producto"
                    else:
                        estado = {3: f"Menciona el código {clave}",
                                  2: f"Menciona {clave}, pero es largo ({c['paginas']} pág.): puede ser un catálogo",
                                  1: f"No menciona {clave}: puede ser de otro producto"}[c["nivel"]]
                    k2.markdown(f"{estado}  \n{c['titulo'][:80]}  \n[Abrir el PDF]({c['url']})")
                    if k2.button("Elegir esta ficha" if c["nivel"] >= 2 else "Elegirla igual",
                                 key=f"ufi_{clave}_{i}"):
                        guardar_candidato(clave, "ficha", c)


def paso_3():
    st.header("3. Fotos y fichas técnicas")
    st.caption("Puedes seguir corrigiendo cantidades y precios aquí. Los cambios se guardan en el borrador.")
    editor_productos()
    boton_actualizar_totales()
    mostrar_totales()
    mostrar_resumen_lineas()
    editar_cabecera()
    claves, descripciones = datos_productos()
    if not claves:
        st.warning("No hay productos seleccionados. Marca al menos uno en el paso anterior.")
        if st.button("← Volver"):
            ir_a(2)
        return
    for clave in claves:
        for tipo in ("foto", "ficha"):
            subido = E.get(f"{tipo}_{clave}")
            if subido:
                bib.guardar_archivo(clave, tipo, subido.name, subido.getvalue())

    listos = sum(1 for c in claves if all(bib.archivos(c)))
    st.progress(listos / len(claves), text=f"{listos} de {len(claves)} productos tienen foto y ficha")
    faltan = [c for c in claves if not all(bib.archivos(c))]
    if faltan:
        st.write("Presiona el botón y yo busco en internet. Después elige la foto y la ficha correcta para cada producto.")
        if st.button(f"Buscar fotos y fichas de lo que falta ({len(faltan)})", icon=":material/search:", type="primary", width="stretch"):
            persistir_borrador()
            barra, hubo_error = st.progress(0.0, text="Buscando…"), False
            for i, clave in enumerate(faltan):
                foto, ficha = bib.archivos(clave)
                sug_foto, sug_ficha = consultas_sugeridas(clave, descripciones[clave])
                hubo_error |= bool(buscar_para(
                    clave, E.get(f"qf_{clave}", sug_foto), E.get(f"qd_{clave}", sug_ficha),
                    fotos=not foto, fichas=not ficha,
                ))
                barra.progress((i + 1) / len(faltan), text=f"Buscando… {i + 1} de {len(faltan)}")
                persistir_borrador()
            barra.empty()
            if hubo_error:
                st.warning("El buscador no respondió en algunos productos. Intenta de nuevo en un minuto.")
            else:
                st.success("Listo. Baja y elige la foto y la ficha de cada producto.")
    else:
        st.success("¡Todos los productos tienen foto y ficha!")
    st.caption("Si un producto no tiene ficha técnica, no pasa nada: el PDF llevará solo la foto.")

    st.divider()
    st.subheader("Productos y anexos")
    c_filtro, c_estado = st.columns([2, 1])
    busqueda_anexos = c_filtro.text_input(
        "Buscar producto", key="filtro_anexos_texto",
        placeholder="Código, marca o descripción",
    )
    estado_anexos = c_estado.selectbox(
        "Mostrar", ["Todos", "Incompletos", "Sin foto", "Sin ficha", "Completos"],
        key="filtro_anexos_estado",
    )
    consulta_anexos = busqueda_anexos.strip().casefold()
    visibles = []
    for clave in claves:
        foto, ficha = bib.archivos(clave)
        completo = bool(foto and ficha)
        if consulta_anexos and consulta_anexos not in (clave + " " + descripciones[clave]).casefold():
            continue
        if estado_anexos == "Incompletos" and completo:
            continue
        if estado_anexos == "Sin foto" and foto:
            continue
        if estado_anexos == "Sin ficha" and ficha:
            continue
        if estado_anexos == "Completos" and not completo:
            continue
        visibles.append(clave)
    st.caption(f"Mostrando {len(visibles)} de {len(claves)} productos. El filtro solo cambia la vista; no excluye productos de la cotización.")
    if visibles:
        for clave in visibles:
            tarjeta(clave, descripciones[clave])
    else:
        st.info("No hay productos que coincidan con este filtro.")

    st.divider()
    atras, sigue = st.columns(2)
    if atras.button("← Volver", width="stretch"):
        ir_a(2)
    if sigue.button("Siguiente: revisar y crear el PDF →", type="primary", width="stretch"):
        persistir_borrador()
        ir_a(4)


# ============================================================== PASO 4: PDF
# ============================================================== REVISIÓN DE LA COTIZACIÓN
def hallazgos() -> list[dict]:
    d = pc.leer_para_pdf(E["ruta"])
    claves = ["" if str(c) == "nan" else str(c or "").strip().upper() for c in E["tabla_edit"][COL_COD]]
    return rv.revisar(d, claves if len(claves) == len(d["lineas"]) else None, bib)


def panel_revision(en_paso_4: bool = False) -> int:
    """Muestra lo que encontró el revisor. Devuelve cuántos errores graves hay."""
    try:
        hs_ = hallazgos()
    except Exception:
        st.info("No pude revisar la cotización automáticamente.")
        return 0
    errores = [h["texto"] for h in hs_ if h["nivel"] == "error"]
    avisos = [h["texto"] for h in hs_ if h["nivel"] == "aviso"]
    if not hs_:
        st.success("Revisé la cotización: los totales, el IVA, los precios y los datos del cliente están en orden.")
        return 0
    st.markdown("#### Revisión de la cotización")
    if errores:
        st.error("**Hay que corregir:**\n\n" + "\n".join(f"- {t}" for t in errores))
        if not en_paso_4 and E.get("origen_pedido") == "pedido":
            st.caption("Para corregir precios o datos del cliente, vuelve al paso 1: tu pedido sigue ahí.")
    if avisos:
        st.warning("**Conviene revisar:**\n\n" + "\n".join(f"- {t}" for t in avisos))
    return len(errores)


# ============================================================== PASO 4: PDF
def registrar_en_historial(pdf_bytes: bytes):
    """Guarda el PDF en el historial (y registra la cotización si venía de un Excel ya hecho)."""
    cot_id = E.get("cot_id")
    if cot_id and hist.obtener(cot_id):
        hist.guardar_archivos(cot_id, pdf=pdf_bytes)
        return
    if E.get("origen_pedido") == "cotizacion":
        d = pc.leer_para_pdf(E["ruta"])
        cli = {ct.normalizar(e): v for e, v in d["izquierda"] + d["derecha"]}
        tot = {ct.normalizar(e): v for e, v in d["totales"]}
        numero = hist.numero_libre(str(cli.get("NCOTIZACION") or Path(E["ruta"]).stem))
        lineas = [{"cantidad": ln["cantidad"], "descripcion": ln["descripcion"], "precio_neto": ln["unitario"]}
                  for ln in d["lineas"]]
        cot_id, _ = hist.crear({"nombre": cli.get("NOMBRE", ""), "rut": cli.get("RUT", "")}, lineas,
                               {"neto": tot.get("TOTALNETO") or 0, "iva": tot.get("IVA") or 0,
                                "total": tot.get("TOTAL") or 0},
                               con_iva=False, origen="cotizacion", numero=numero)
        hist.guardar_archivos(cot_id, xlsx=E["ruta"], pdf=pdf_bytes)
        E["cot_id"] = cot_id


def generar_pdf():
    """Regenera el Excel con los datos actuales y genera el PDF/anexos."""
    refrescar_excel_actual()
    lineas = E.get("lineas", [])
    finales = []
    for ln in lineas:
        if not _esta_marcado(ln.get("incluir", True), True):
            continue
        if not ln.get("es_producto", True):
            continue  # servicios/flete permanecen en el Excel, pero no llevan foto propia
        clave = _texto_seguro(ln.get("clave_auto")).upper()
        if clave and clave != "NAN":
            bib.registrar(
                clave, nombre=ln["descripcion"],
                alias=ln["descripcion"] if ln.get("origen") != "biblioteca" else None,
                precio_neto=(ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto")),
            )
        else:
            clave = ""
        finales.append({**ln, "clave": clave})

    ruta = E["ruta"]
    salida = ruta.with_name(f"{ruta.stem}_con_anexos.pdf")
    with st.spinner("Armando tu PDF…"):
        informe = ct.generar_pdf_final(ruta, finales, bib, salida,
                                       motor="libreoffice" if usar_lo else "nativo")
    pdf_bytes = salida.read_bytes()
    registrar_en_historial(pdf_bytes)
    E.update(pdf_bytes=pdf_bytes, pdf_nombre=salida.name, informe=informe)
    persistir_borrador()


def paso_4():
    st.header("4. Revisa y crea tu PDF")

    if "pdf_bytes" not in E:
        st.write("Esta es la última oportunidad para corregir los productos, cantidades, códigos, precios y datos del cliente antes de crear el PDF.")
        editor_productos()
        boton_actualizar_totales()
        mostrar_totales()
        mostrar_resumen_lineas()
        editar_cabecera()

        claves, _ = datos_productos()
        con_foto = sum(1 for c in claves if bib.archivos(c)[0])
        con_ficha = sum(1 for c in claves if bib.archivos(c)[1])
        st.write(f"La cotización tiene **{len(E.get('lineas', []))} líneas**. Fotos: **{con_foto}** de {len(claves)}. "
                 f"Fichas técnicas: **{con_ficha}** de {len(claves)}.")
        n_errores = panel_revision(en_paso_4=True)
        permitido = not bool(E.get("error_numero"))
        if n_errores:
            acepto = st.checkbox("Entiendo los problemas de arriba y quiero crear el PDF igual", key="ok_riesgo")
            permitido = permitido and acepto
        if E.get("error_numero"):
            st.error("Corrige el número de cotización antes de generar el PDF.")
        if st.button("Crear el PDF", icon=":material/picture_as_pdf:", type="primary", width="stretch", disabled=not permitido):
            try:
                refrescar_excel_actual()
                generar_pdf()
            except Exception as exc:
                st.error(f"No pude crear el PDF: {exc}")
    else:
        st.success("El PDF ya está generado. Para evitar que el archivo y los datos se desincronicen, la edición está pausada.")
        mostrar_totales()
        if st.button("Volver a editar y regenerar el PDF", type="primary", width="stretch"):
            for clave_estado in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
                E.pop(clave_estado, None)
            try:
                if E.get("cot_id") and hist.obtener(E["cot_id"]):
                    if GIOVAL_REQUIRE_ACCESS:
                        hist.actualizar_si_version(
                            E["cot_id"], E.get("_cotizacion_version"), estado="Borrador"
                        )
                    else:
                        hist.actualizar(E["cot_id"], pdf=None, estado="Borrador")
                    registro_actual = hist.obtener(E["cot_id"])
                    if registro_actual:
                        E["_cotizacion_version"] = registro_actual.get("actualizado")
                E["editado_manual"] = True
            except hs.ConflictoActualizacion as exc:
                E["_conflicto_guardado"] = True
                E["_conflicto_guardado_id"] = E.get("cot_id")
                E["_conflicto_guardado_mensaje"] = str(exc)
            st.rerun()

    if "pdf_bytes" in E:
        if GIOVAL_REQUIRE_ACCESS:
            st.success(
                f"¡Listo! Tu PDF tiene {E['informe']['paginas']} páginas. La cotización comercial quedó en el historial compartido; "
                "el PDF conserva los datos de cliente ingresados en esta sesión y no se almacena en la biblioteca común."
            )
        else:
            st.success(f"¡Listo! Tu PDF tiene {E['informe']['paginas']} páginas y quedó guardado en el historial.")
        st.download_button("Descargar el PDF", E["pdf_bytes"], file_name=E["pdf_nombre"], icon=":material/download:",
                           mime="application/pdf", type="primary", width="stretch")
        if E.get("ruta") and Path(E["ruta"]).exists():
            st.download_button("Descargar la cotización en Excel", Path(E["ruta"]).read_bytes(), icon=":material/download:",
                               file_name=Path(E["ruta"]).name, width="stretch")
        with st.expander("Vista previa del PDF completo", expanded=True):
            try:
                st.pdf(E["pdf_bytes"], height=760, key=f"pdf_preview_{E.get('cot_id', 'actual')}")
            except Exception:
                st.info("El visor integrado no está disponible en esta instalación. Usa el botón de descarga del PDF.")
        textos = (("sin_clave", "Estos productos no tienen código, así que no llevan foto ni ficha"),
                  ("sin_foto", "Les falta la foto"), ("sin_ficha", "Les falta la ficha técnica"),
                  ("ficha_dudosa", "Revisa estas fichas: no mencionan el código del producto"),
                  ("ficha_ilegible", "No pude leer estas fichas"))
        for clave_info, texto in textos:
            if E["informe"][clave_info]:
                st.warning(f"{texto}: " + ", ".join(E["informe"][clave_info]))

    st.divider()
    if "pdf_bytes" not in E:
        atras, nueva = st.columns(2)
        if atras.button("← Volver a fotos y fichas", width="stretch"):
            ir_a(3)
        nueva.button("Hacer otra cotización", width="stretch", on_click=lambda: E.clear())
    else:
        nueva = st.button("Hacer otra cotización", width="stretch", on_click=lambda: E.clear())


# ============================================================== HISTORIAL
def fecha_corta(iso: str) -> str:
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso or "")


def cargar_en_pedido(cot_id: int, mantener: bool):
    """Lleva una cotización guardada al paso 1: como copia nueva, o para seguir editándola."""
    rec = hist.obtener(cot_id)
    if not rec:
        return
    datos = rec["datos"]
    lineas, cli = datos.get("lineas", []), datos.get("cliente", {})
    con_iva = bool(rec["con_iva"])
    for k in ("lineas", "ruta", "tabla", "tabla_edit", "tabla_base", "info_desc", "pdf_bytes", "pdf_nombre", "informe",
              "orden_df_base", "orden_editor_version", "orden_editor", "ok_riesgo", "ok_sin_precio", "cli_sel", "origen_pedido"):
        E.pop(k, None)
    E["precios_modo"] = OPCIONES_PRECIO[0 if con_iva else 1]
    E["orden_df"] = pd.DataFrame({
        "Incluir": [_esta_marcado(ln.get("incluir"), True) for ln in lineas],
        "Cantidad": [ln["cantidad"] for ln in lineas],
        "Producto": [ln["descripcion"] for ln in lineas],
        "Precio unitario": [ct.precio_para_mostrar(
            ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"), con_iva)
            if (ln.get("unitario") is not None or ln.get("precio_neto") is not None) else None for ln in lineas]})
    E["orden_df_base"] = E["orden_df"].copy()
    E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
    E.update(c_nombre=cli.get("nombre", ""), c_rut=cli.get("rut", ""), c_dir=cli.get("direccion", ""),
             c_ciudad=cli.get("ciudad", ""), c_tel=cli.get("telefono", ""), c_email=cli.get("email", ""))
    try:
        E["c_fecha"] = date.fromisoformat(rec["fecha"][:10]) if mantener else date.today()
    except ValueError:
        E["c_fecha"] = date.today()
    if mantener:
        E["cot_id"] = cot_id
        E["_cotizacion_version"] = rec.get("actualizado")
    else:
        E.pop("cot_id", None)
        E.pop("_cotizacion_version", None)
    E.pop("_conflicto_guardado", None)
    E.pop("_conflicto_guardado_id", None)
    E.pop("_conflicto_guardado_mensaje", None)
    E["paso"], E["vista"] = 1, VISTAS[0]


def historial_cotizaciones():
    E.setdefault("h_ver", 0)
    todos = hist.listar()
    if not todos:
        st.info("Todavía no hay cotizaciones. Cada vez que crees una, quedará guardada aquí con su número.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Cotizaciones", len(todos))
    m2.metric("Monto cotizado", rv.clp(sum(r["total"] or 0 for r in todos)))
    m3.metric("Monto aceptado", rv.clp(sum(r["total"] or 0 for r in todos if r["estado"] == "Aceptada")))
    f1, f2 = st.columns([3, 2])
    texto = f1.text_input(
        "Buscar",
        placeholder="Número de cotización" if GIOVAL_REQUIRE_ACCESS else "Número, cliente, RUT o notas",
        key="h_texto",
    )
    estado = f2.selectbox("Estado", ["Todos"] + hs.ESTADOS, key="h_estado")
    regs = hist.listar(texto, estado)
    if not regs:
        st.warning("No encontré cotizaciones con ese filtro.")
        return
    datos_tabla = {
        "id": [r["id"] for r in regs], "N°": [r["numero"] for r in regs],
        "Fecha": [fecha_corta(r["fecha"]) for r in regs],
        "Total": [r["total"] for r in regs], "Estado": [r["estado"] for r in regs],
    }
    if not GIOVAL_REQUIRE_ACCESS:
        datos_tabla["Cliente"] = [r["cliente"] for r in regs]
        datos_tabla["Notas"] = [r["notas"] or "" for r in regs]
    df = pd.DataFrame(datos_tabla)
    editado = editor(
        df, hide_index=True, width="stretch", key=f"h_tabla_{E['h_ver']}_{texto}_{estado}",
        disabled=[c for c in ("N°", "Fecha", "Total", "Cliente") if c in df.columns],
        column_config={"id": None, "Total": st.column_config.NumberColumn(format="$ %d"),
                       "Estado": st.column_config.SelectboxColumn(options=hs.ESTADOS, required=True)})
    if st.button("Guardar estados" if GIOVAL_REQUIRE_ACCESS else "Guardar estados y notas", icon=":material/save:"):
        cambios, conflictos = 0, 0
        for (_, fila), reg in zip(editado.iterrows(), regs):
            nuevo_estado = fila["Estado"]
            if GIOVAL_REQUIRE_ACCESS:
                if nuevo_estado != reg["estado"]:
                    try:
                        hist.actualizar_si_version(reg["id"], reg.get("actualizado"), estado=nuevo_estado)
                        cambios += 1
                        if E.get("cot_id") == reg["id"]:
                            actual = hist.obtener(reg["id"])
                            E["_cotizacion_version"] = actual.get("actualizado") if actual else None
                    except hs.ConflictoActualizacion:
                        conflictos += 1
                        if E.get("cot_id") == reg["id"]:
                            E["_conflicto_guardado"] = True
                            E["_conflicto_guardado_id"] = reg["id"]
                            E["_conflicto_guardado_mensaje"] = (
                                "Otra sesión modificó esta cotización; no cambié su estado. Recarga antes de editarla."
                            )
            else:
                nuevas_notas = str(fila["Notas"] or "")
                if nuevo_estado != reg["estado"] or nuevas_notas != (reg["notas"] or ""):
                    hist.actualizar(reg["id"], estado=nuevo_estado, notas=nuevas_notas)
                    cambios += 1
        E["h_ver"] += 1
        if conflictos:
            st.warning(f"Guardé {cambios} cambios. No se pudieron guardar {conflictos} porque otra sesión modificó esas cotizaciones.")
        else:
            st.success(f"Guardado ({cambios} cambios).")
        st.rerun()

    st.divider()
    opciones = {
        (f"{r['numero']} · {rv.clp(r['total'] or 0)}" if GIOVAL_REQUIRE_ACCESS
         else f"{r['numero']} · {r['cliente'] or 'sin cliente'} · {rv.clp(r['total'] or 0)}"): r["id"]
        for r in regs
    }
    elegida = st.selectbox("Elige una cotización para ver qué puedes hacer con ella", list(opciones))
    rec = hist.obtener(opciones[elegida])
    with st.container(border=True):
        if GIOVAL_REQUIRE_ACCESS:
            st.markdown(f"**{rec['numero']}** · Cotización comercial compartida · {rec['estado']} · "
                        f"{fecha_corta(rec['fecha'])}")
        else:
            st.markdown(f"**{rec['numero']}** · {rec['cliente'] or 'sin cliente'} · {rec['estado']} · "
                        f"{fecha_corta(rec['fecha'])}")
        d1, d2 = st.columns(2)
        pdf, xlsx = Path(rec["pdf"] or ""), Path(rec["xlsx"] or "")
        if rec["pdf"] and pdf.exists():
            d1.download_button("Descargar el PDF", pdf.read_bytes(), file_name=pdf.name, mime="application/pdf", icon=":material/download:",
                               width="stretch", key=f"dp_{rec['id']}")
        else:
            d1.caption(
                "El PDF no se archiva para proteger los datos del cliente. Abre la cotización y vuelve a generarlo."
                if GIOVAL_REQUIRE_ACCESS else "Todavía no se ha creado el PDF de esta cotización."
            )
        if rec["xlsx"] and xlsx.exists():
            d2.download_button("Descargar el Excel", xlsx.read_bytes(), file_name=xlsx.name, icon=":material/download:",
                               width="stretch", key=f"dx_{rec['id']}")
        if GIOVAL_REQUIRE_ACCESS:
            d2.caption("El Excel se genera en tu sesión y se descarga; no se guarda en la biblioteca común.")
        b1, b2 = st.columns(2)
        b1.button("Usar como base de una cotización nueva", width="stretch", key=f"dup_{rec['id']}",
                  on_click=cargar_en_pedido, args=(rec["id"], False))
        b2.button("Seguir editando esta cotización", width="stretch", key=f"edi_{rec['id']}",
                  on_click=cargar_en_pedido, args=(rec["id"], True))
        with st.expander("Eliminar esta cotización"):
            seguro = st.checkbox("Sí, quiero eliminarla para siempre", key=f"del_{rec['id']}")
            if st.button("Eliminar", disabled=not seguro, key=f"delb_{rec['id']}"):
                hist.eliminar(rec["id"])
                E["h_ver"] += 1
                st.rerun()


def clientes_ui():
    E.setdefault("cli_ver", 0)
    cols = ["id", "nombre", "rut", "direccion", "ciudad", "telefono", "email"]
    filas = hist.clientes()
    df = pd.DataFrame(filas, columns=cols) if filas else pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
    st.write("Estos son los clientes que he guardado. Se llenan solos al crear cotizaciones; "
             "también puedes corregirlos o agregar uno en la última fila.")
    editado = editor(
        df, num_rows="dynamic", hide_index=True, width="stretch", key=f"cli_tabla_{E['cli_ver']}",
        column_config={"id": None, "nombre": "Nombre o razón social", "rut": "RUT", "direccion": "Dirección",
                       "ciudad": "Ciudad", "telefono": "Teléfono", "email": "Correo"})
    if st.button("Guardar clientes", icon=":material/save:"):
        hist.reemplazar_clientes(editado.to_dict("records"))
        E["cli_ver"] += 1
        st.success("Clientes guardados.")
        st.rerun()


def vista_historial():
    st.header("Historial")
    if GIOVAL_REQUIRE_ACCESS:
        st.caption(
            "Biblioteca compartida: se sincronizan los productos y el historial comercial "
            "(líneas, precios, totales y estado). Los datos de cliente se ingresan en cada "
            "sesión y no se guardan aquí; los PDF y Excel se descargan, pero no se archivan."
        )
        historial_cotizaciones()
    else:
        tab_cot, tab_cli = st.tabs(["Cotizaciones", "Clientes"])
        with tab_cot:
            historial_cotizaciones()
        with tab_cli:
            clientes_ui()


# ============================================================== MIS PRODUCTOS
def renombrar_producto(clave: str):
    nuevo = str(E.get(f"ren_{clave}", "")).strip().upper()
    try:
        final = bib.renombrar(clave, nuevo, combinar=bool(E.get(f"comb_{clave}")))
        E["p_sel"], E["p_msg"] = final, ("ok", f"Listo: ahora se llama {final}.")
    except ValueError as e:
        E["p_msg"] = ("error", str(e))


def borrar_producto(clave: str):
    bib.eliminar(clave)
    E.pop("p_sel", None)
    E["p_msg"] = ("ok", f"Eliminé {clave}.")


def vista_productos():
    E.setdefault("p_ver", 0)
    st.header("Catálogo de productos")
    st.write("Aquí están los productos que he aprendido. Puedes corregir nombres, anotar el **costo** y el **margen** "
             "para que te sugiera el precio de venta, y cambiar su foto o su ficha.")
    if "p_msg" in E:
        nivel, texto = E.pop("p_msg")
        (st.success if nivel == "ok" else st.error)(texto)
    with st.expander("Agregar un producto a mano"):
        n1, n2 = st.columns(2)
        nuevo_cod = n1.text_input("Código del producto", key="np_cod")
        nuevo_nom = n2.text_input("Nombre", key="np_nom")
        if st.button("Agregar", disabled=not nuevo_cod.strip()):
            bib.registrar(nuevo_cod.strip().upper(), nombre=nuevo_nom.strip() or nuevo_cod.strip())
            E["p_ver"] += 1
            st.rerun()

    filas = bib.filas_catalogo()
    if not filas:
        st.info("Todavía no hay productos guardados. Se irán agregando solos a medida que armes cotizaciones.")
        return
    f1, f2 = st.columns([3, 2])
    texto = f1.text_input("Buscar", placeholder="Código o nombre", key="p_texto")
    solo = f2.checkbox("Solo los incompletos (sin foto o sin ficha)", key="p_solo")
    filas = [f for f in filas if (not texto or texto.lower() in (f["clave"] + " " + f["nombre"]).lower())
             and (not solo or not (f["foto"] and f["ficha"]))]
    if not filas:
        st.warning("No hay productos con ese filtro.")
        return
    mostrar = pd.DataFrame({
        "Código": [f["clave"] for f in filas], "Producto": [f["nombre"] for f in filas],
        "Foto": ["Disponible" if f["foto"] else "Falta" for f in filas], "Ficha": ["Disponible" if f["ficha"] else "Falta" for f in filas],
        "Último precio neto": [f["precio_neto"] for f in filas], "Costo neto": [f["costo_neto"] for f in filas],
        "Margen %": [f["margen_pct"] for f in filas],
        "Sugerido neto": [f["sugerido_neto"] for f in filas],
        "Sugerido con IVA": [None if f["sugerido_neto"] is None else f["sugerido_neto"] * (1 + rv.IVA) for f in filas],
        "Notas": [f["notas"] for f in filas]})
    editado = editor(
        mostrar, hide_index=True, width="stretch", key=f"p_tabla_{E['p_ver']}_{texto}_{solo}",
        disabled=["Código", "Foto", "Ficha", "Último precio neto", "Sugerido neto", "Sugerido con IVA"],
        column_config={c: st.column_config.NumberColumn(format="$ %d") for c in
                       ("Último precio neto", "Costo neto", "Sugerido neto", "Sugerido con IVA")}
                      | {"Margen %": st.column_config.NumberColumn(format="%.1f %%", min_value=0, step=1)})
    st.caption("Precio sugerido = costo × (1 + margen). El margen es sobre el costo: con costo $10.000 y margen 30 % "
               "el precio neto sugerido es $13.000. Al crear un pedido, estos productos aparecen con su precio ya puesto.")
    if st.button("Guardar cambios de la tabla", icon=":material/save:"):
        cambios = 0
        for (_, fila), f in zip(editado.iterrows(), filas):
            nuevos = {"nombre": fila["Producto"], "costo_neto": fila["Costo neto"], "margen_pct": fila["Margen %"],
                      "notas": fila["Notas"] if isinstance(fila["Notas"], str) else ""}
            viejos = {"nombre": f["nombre"], "costo_neto": f["costo_neto"], "margen_pct": f["margen_pct"],
                      "notas": f["notas"] or ""}
            if any(not (pd.isna(nuevos[k]) and viejos[k] is None) and nuevos[k] != viejos[k] for k in nuevos):
                bib.actualizar(f["clave"], **nuevos)
                cambios += 1
        E["p_ver"] += 1
        st.success(f"Guardado ({cambios} productos actualizados).")
        st.rerun()

    st.divider()
    codigos = [f["clave"] for f in filas]
    elegido = st.selectbox("Elige un producto para ver o cambiar su foto y su ficha", codigos, key="p_sel")
    nombre = next(f["nombre"] for f in filas if f["clave"] == elegido)
    tarjeta(elegido, nombre)
    with st.expander("Cambiar el código o eliminar este producto"):
        st.text_input("Nuevo código", key=f"ren_{elegido}")
        st.checkbox("Si ya existe un producto con ese código, combinarlos", key=f"comb_{elegido}")
        st.button("Cambiar el código", key=f"renb_{elegido}", on_click=renombrar_producto, args=(elegido,))
        st.divider()
        seguro = st.checkbox("Sí, quiero eliminar este producto con su foto y su ficha", key=f"delp_{elegido}")
        st.button("Eliminar producto", disabled=not seguro, key=f"delpb_{elegido}",
                  on_click=borrar_producto, args=(elegido,))


def configuracion_compacta():
    """Muestra opciones secundarias bajo demanda y devuelve los servicios activos de la app."""
    carpeta_compartida = str((APP_DIR / "biblioteca").resolve())
    if not GIOVAL_REQUIRE_ACCESS:
        E.setdefault("config_carpeta", carpeta_compartida)
    api_key, origen_key = li.buscar_api_key(APP_DIR)

    with st.popover(
        "Ajustes",
        icon=":material/settings:",
        type="secondary",
        width="content",
        key="settings-popover",
        help="Preferencias, conexión de IA, numeración y copia de seguridad.",
    ):
        st.markdown("### Preferencias")
        st.caption("Las opciones avanzadas están aquí para mantener despejada la pantalla de trabajo.")

        if GIOVAL_REQUIRE_ACCESS:
            carpeta = carpeta_compartida
            st.text_input(
                "Biblioteca compartida",
                value=carpeta,
                disabled=True,
                help="Todos los usuarios autorizados trabajan sobre estos mismos datos.",
                key="config_carpeta_compartida",
            )
            st.caption("Los cambios quedan guardados en el servidor central y se ven en las demás instalaciones.")
        else:
            carpeta = st.text_input(
                "Carpeta de datos y productos",
                key="config_carpeta",
                help="Normalmente puedes dejar la biblioteca de este computador como está.",
            )
        bib_panel = ct.Biblioteca(carpeta)
        hist_panel = hs.Historial(carpeta)
        st.caption(f"{len(bib_panel.indice)} productos en el catálogo")

        with st.expander("Resumen comercial", expanded=False):
            registros = hist_panel.listar()
            borradores_resumen = []
            for registro in hist_panel.listar("", "Borrador"):
                pdf_registro = Path(registro["pdf"]) if registro.get("pdf") else None
                if not pdf_registro or not pdf_registro.exists():
                    borradores_resumen.append(registro)

            st.caption("Indicadores y actividad reciente. El resumen queda aquí para mantener despejada la pantalla principal.")
            col_r1, col_r2 = st.columns(2)
            col_r1.metric("Cotizaciones registradas", len(registros))
            col_r2.metric("Borradores pendientes", len(borradores_resumen))
            col_r3, col_r4 = st.columns(2)
            col_r3.metric(
                "Monto aceptado",
                rv.clp(sum(float(registro.get("total") or 0) for registro in registros
                           if registro.get("estado") == "Aceptada")),
            )
            col_r4.metric("Productos en catálogo", len(bib_panel.indice))

            if registros:
                recientes = sorted(
                    registros,
                    key=lambda registro: str(
                        registro.get("actualizado") or registro.get("creado") or registro.get("fecha") or ""
                    ),
                    reverse=True,
                )[:5]
                df_recientes = pd.DataFrame({
                    "N° cotización": [registro.get("numero", "") for registro in recientes],
                    "Fecha": [fecha_corta(registro.get("fecha") or "") for registro in recientes],
                    "Cliente": [registro.get("cliente") or "Sin cliente" for registro in recientes],
                    "Total": [registro.get("total") or 0 for registro in recientes],
                    "Estado": [registro.get("estado") or "Borrador" for registro in recientes],
                })
                st.dataframe(
                    df_recientes,
                    hide_index=True,
                    width="stretch",
                    column_config={"Total": st.column_config.NumberColumn(format="$ %d")},
                )
            else:
                st.caption("Todavía no hay cotizaciones registradas.")

        st.divider()
        st.markdown("**Conexión de inteligencia artificial**")
        if api_key:
            st.caption(f"Conectada · Clave encontrada en {origen_key}")
        elif GIOVAL_REQUIRE_ACCESS:
            st.caption(
                "La IA no está configurada en el servidor. El administrador puede definir "
                "OPENAI_API_KEY en las variables del servicio Render; no se guardan claves "
                "personales desde una sesión compartida."
            )
        else:
            api_key = st.text_input(
                "Clave de OpenAI",
                type="password",
                key="config_api_key",
                help="Se usa para interpretar pedidos en PDF o foto y mejorar la identificación de productos.",
            )
            if api_key and st.button(
                "Guardar clave",
                icon=":material/save:",
                key="config_guardar_api",
                width="stretch",
            ):
                li.guardar_api_key(APP_DIR, api_key)
                st.success("Clave guardada en este computador.")

        with st.expander("Opciones para crear el PDF", expanded=False):
            st.checkbox(
                "Usar LibreOffice para convertir a PDF",
                value=False,
                key="config_usar_lo",
                help="Déjalo desmarcado salvo que necesites conservar exactamente el formato de Excel.",
            )
            st.caption("La opción normal genera el PDF sin abrir LibreOffice.")

        with st.expander("Numeración de cotizaciones", expanded=False):
            E.setdefault("config_formato_numero", hist_panel.config("formato_numero", hs.FORMATO_DEFECTO))
            E.setdefault("config_siguiente_numero", hist_panel.siguiente_correlativo())
            fmt = st.text_input(
                "Formato del número",
                key="config_formato_numero",
                help="{n} = correlativo, {aa} = año de 2 cifras, {aaaa} = año completo. Ej.: {n:04d}-COT{aa}",
            )
            sig = st.number_input(
                "Próximo número",
                min_value=1,
                step=1,
                key="config_siguiente_numero",
            )
            st.caption(f"Vista previa: {hist_panel.formatear(int(sig), date.today(), fmt)}")
            if st.button("Guardar numeración", icon=":material/save:", key="config_guardar_num", width="stretch"):
                hist_panel.set_config("formato_numero", fmt)
                hist_panel.set_config("siguiente_correlativo", int(sig))
                st.success("Numeración guardada.")

        with st.expander("Copia de seguridad", expanded=False):
            st.caption("Incluye la base de datos, catálogo, fotos, fichas y cotizaciones guardadas.")
            if st.button("Preparar respaldo", icon=":material/archive:", key="preparar_respaldo", width="stretch"):
                try:
                    E["_respaldo_zip"] = crear_respaldo_biblioteca(bib_panel.dir)
                    E["_respaldo_nombre"] = f"respaldo_giovalia_{datetime.now():%Y%m%d_%H%M%S}.zip"
                    st.success("Respaldo preparado. Descárgalo y guárdalo fuera de la carpeta del programa.")
                except Exception as exc:
                    E.pop("_respaldo_zip", None)
                    st.error(f"No pude preparar el respaldo: {exc}")
            if E.get("_respaldo_zip"):
                st.download_button(
                    "Descargar respaldo ZIP",
                    E["_respaldo_zip"],
                    icon=":material/download:",
                    file_name=E.get("_respaldo_nombre", "respaldo_giovalia.zip"),
                    mime="application/zip",
                    width="stretch",
                    key="descargar_respaldo",
                )

            if GIOVAL_REQUIRE_ACCESS and GIOVAL_ALLOW_INITIAL_IMPORT:
                st.divider()
                st.markdown("**Importar respaldo inicial al espacio compartido**")
                st.caption("Solo debe usarse cuando la biblioteca central está vacía, antes de que el equipo empiece a trabajar.")
                archivo_inicial = st.file_uploader(
                    "Respaldo ZIP de otro computador",
                    type=["zip"],
                    key="respaldo_inicial_zip",
                    max_upload_size=500,
                )
                confirmar_importacion = st.checkbox(
                    "Confirmo que quiero cargar este respaldo en la biblioteca central vacía.",
                    key="confirmar_respaldo_inicial",
                )
                if st.button(
                    "Importar biblioteca inicial",
                    icon=":material/cloud_upload:",
                    disabled=not (archivo_inicial and confirmar_importacion),
                    key="importar_biblioteca_inicial",
                    width="stretch",
                ):
                    try:
                        cantidad_cot, cantidad_prod = restaurar_respaldo_inicial(
                            bib_panel.dir, archivo_inicial.getvalue()
                        )
                        st.success(
                            f"Biblioteca inicial importada: {cantidad_cot} cotizaciones y "
                            f"{cantidad_prod} productos. Desactiva GIOVAL_ALLOW_INITIAL_IMPORT en el servidor."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"No pude importar el respaldo: {exc}")

        st.divider()
        st.button("Volver a los borradores", icon=":material/home:", key="settings_volver_inicio", width="stretch", on_click=lambda: E.clear())

    # El contenido del popover se calcula también cuando está cerrado; reutilizamos sus servicios
    # para evitar abrir dos instancias del catálogo y del historial en cada ejecución.
    api_key_actual, _ = li.buscar_api_key(APP_DIR)
    if not api_key_actual:
        api_key_actual = E.get("config_api_key", "")
    usar_lo = bool(E.get("config_usar_lo", False))
    return carpeta, bib_panel, hist_panel, api_key_actual, usar_lo


# ============================================================== PANTALLA
# Identidad visual alineada con Gioval.cl: marca, rubro y configuración secundaria.
if GIOVAL_REQUIRE_ACCESS:
    col_marca, col_salir, col_ajustes = st.columns([5.5, 1, 1])
else:
    col_marca, col_ajustes = st.columns([6, 1])
with col_marca:
    st.markdown(
        '<div class="marca-app">'
        '<span class="marca-simbolo" aria-hidden="true"></span>'
        '<div class="marca-texto">'
        '<div class="marca-nombre">Gioval</div>'
        '<div class="marca-descripcion">Tintas, tóners y artículos de oficina</div>'
        f'<div class="marca-pill">{"Biblioteca compartida" if GIOVAL_REQUIRE_ACCESS else "Cotizador comercial"}</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )
with col_ajustes:
    carpeta, bib, hist, api_key, usar_lo = configuracion_compacta()
if GIOVAL_REQUIRE_ACCESS:
    with col_salir:
        if st.button("Salir", icon=":material/logout:", width="stretch", key="gioval_cerrar_sesion"):
            E.pop("gioval_autenticado", None)
            st.rerun()

with st.container(key="app-navigation"):
    vista = st.radio("Secciones", VISTAS, horizontal=True, key="vista", label_visibility="collapsed")
if vista == VISTAS[1]:
    vista_historial()
elif vista == VISTAS[2]:
    vista_productos()
elif not E.get("cot_id"):
    selector_borradores()
else:
    E.setdefault("paso", 1)
    if E["paso"] > 1 and "lineas" not in E:
        E["paso"] = 1
    mostrar_pasos(E["paso"])
    {1: paso_1, 2: paso_2, 3: paso_3, 4: paso_4}[E["paso"]]()
