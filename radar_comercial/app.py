"""Radar Comercial Gioval: oportunidades de Compra Ágil y rentabilidad estimada.

Ejecutar desde la raíz del repositorio:
    streamlit run radar_comercial/app.py --server.port 8502

Lee solo los datos comerciales del cotizador. El seguimiento se guarda en
GIOVAL_DATA_DIR/radar_comercial.db y nunca modifica el historial del cotizador.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import cotizador as ct
import historial as hs
from radar_comercial.servicio import (
    APICompraAgilError,
    ESTADOS_API,
    ESTADOS_SEGUIMIENTO,
    PALABRAS_BUSQUEDA_DEFAULT,
    PORTAL_URL,
    REGIONES,
    RadarStore,
    buscar_compras_agiles,
    evaluar_rentabilidad,
    importe_catalogo_a_neto,
    normalizar_detalle,
    normalizar_importacion,
    obtener_detalle_compra_agil,
    parsear_lineas_productos,
    resumen_historial_cotizador,
    sugerir_productos_catalogo,
)

st.set_page_config(
    page_title="Gioval · Radar Comercial",
    page_icon=":material/travel_explore:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DATA_DIR = Path(os.environ.get("GIOVAL_DATA_DIR") or RAIZ).expanduser().resolve()
BIBLIOTECA_DIR = DATA_DIR / "biblioteca"
RADAR_DB = DATA_DIR / "radar_comercial.db"
GIOVAL_REQUIRE_ACCESS = os.environ.get("GIOVAL_REQUIRE_ACCESS", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
GIOVAL_ACCESS_PASSWORD = os.environ.get("GIOVAL_ACCESS_PASSWORD", "")
API_TICKET_ENV = os.environ.get("CHILECOMPRA_API_TICKET", "").strip()
ESTILO = """
<style>
:root { color-scheme: dark; }
.stApp { background: #0A0B10; }
.block-container { padding-top: 1.2rem; padding-bottom: 2.5rem; max-width: 1500px; }
.hero-radar { padding:1.2rem 1.4rem; border:1px solid #2A2D39; border-radius:12px;
  background:linear-gradient(120deg,#151720,#0E1016); margin:.4rem 0 1.1rem; }
.hero-radar .kicker { color:#9BA2FF; text-transform:uppercase; font-size:.72rem; letter-spacing:.12em; font-weight:750; }
.hero-radar h1 { color:#F3F4F8; margin:.35rem 0; font-size:1.8rem; }
.hero-radar p { color:#A7ABBA; margin:0; max-width:950px; }
.panel-nota { background:#11141B; border:1px solid #30333D; border-radius:8px; padding:.8rem 1rem; color:#C2C6D1; }
.radar-tag { display:inline-block; border:1px solid #363A47; background:#171A23; padding:.16rem .48rem; border-radius:4px; font-size:.78rem; color:#D6D9E3; }
[data-testid="stMetric"] { background:#11141B; border:1px solid #292D38; padding:.65rem .8rem; border-radius:8px; }
[data-testid="stProgress"] [role="progressbar"] { background:#232631 !important; }
[data-testid="stProgress"] [role="progressbar"] > div { background:#626979 !important; }
div[data-testid="stDataFrame"] { border:1px solid #2A2D39; border-radius:8px; overflow:hidden; }
[data-testid="stSegmentedControl"] { margin:.8rem 0 1rem; }
[data-testid="stSegmentedControl"] button { border-radius:8px !important; min-height:2.7rem; }
[data-testid="stSegmentedControl"] button[aria-pressed="true"] {
  background:#242834 !important; border:1px solid #555B6B !important; color:#FFFFFF !important;
}
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input { border-radius:7px !important; }
button[kind="primary"] { font-weight:650 !important; }
.st-key-radar-header { margin-bottom:.7rem; }
.radar-flash { margin:.6rem 0 1rem; }
@media (max-width: 700px) {
  .block-container { padding-left:.8rem; padding-right:.8rem; }
  .hero-radar { padding:1rem; }
  .hero-radar h1 { font-size:1.35rem; line-height:1.3; }
  [data-testid="stSegmentedControl"] button { padding-left:.32rem !important; padding-right:.32rem !important; font-size:.78rem !important; }
}
</style>
"""
st.markdown(ESTILO, unsafe_allow_html=True)

if GIOVAL_REQUIRE_ACCESS:
    if not GIOVAL_ACCESS_PASSWORD:
        st.error("El acceso compartido no está configurado. Contacta a quien administra el servidor de Gioval.")
        st.stop()
    if not st.session_state.get("radar_autenticado"):
        st.markdown(
            '<section class="hero-radar"><div class="kicker">Gioval / Herramientas comerciales</div>'
            '<h1>Radar Comercial</h1><p>Accede al espacio de trabajo de oportunidades y rentabilidad.</p></section>',
            unsafe_allow_html=True,
        )
        with st.form("radar_login_form"):
            password = st.text_input("Clave de acceso de Gioval", type="password")
            ingresar = st.form_submit_button("Ingresar", type="primary", width="stretch")
        if ingresar:
            if hmac.compare_digest(password, GIOVAL_ACCESS_PASSWORD):
                st.session_state["radar_autenticado"] = True
                st.rerun()
            else:
                st.error("La clave no coincide.")
        st.stop()

try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    catalogo = ct.Biblioteca(BIBLIOTECA_DIR)
    historial = hs.Historial(BIBLIOTECA_DIR)
    store = RadarStore(RADAR_DB)
except Exception as exc:
    st.error(f"No pude abrir la biblioteca compartida. No se modificarán las cotizaciones. Detalle: {exc}")
    st.stop()

base_opciones = ("Incluyen IVA", "Netos sin IVA")
base_actual = store.config("base_importes_catalogo", "Incluyen IVA")
if base_actual not in base_opciones:
    base_actual = "Incluyen IVA"

with st.container(key="radar-header"):
    c_marca, c_conexion = st.columns([5, 1.35])
    with c_marca:
        st.markdown("### GIOVAL")
        st.caption("Radar comercial · Herramienta interna de ventas")
    with c_conexion:
        st.markdown("**Conexión**")
        st.markdown(
            f'<span class="radar-tag">{"Biblioteca compartida" if GIOVAL_REQUIRE_ACCESS else "Biblioteca local"}</span>',
            unsafe_allow_html=True,
        )
        if GIOVAL_REQUIRE_ACCESS and st.button(
            "Salir", icon=":material/logout:", key="radar_logout", width="stretch"
        ):
            st.session_state.pop("radar_autenticado", None)
            st.rerun()
    st.markdown(
        '<section class="hero-radar"><div class="kicker">Compras públicas / Rentabilidad</div>'
        '<h1>Encuentra oportunidades. Cotiza con criterio.</h1>'
        '<p>Detecta compras relevantes para Gioval, compara sus requerimientos con tu catálogo y '
        'estima la rentabilidad antes de preparar una oferta.</p></section>',
        unsafe_allow_html=True,
    )

with st.expander("Conexiones y criterios económicos", expanded=False):
    st.markdown("**Ticket de ChileCompra**")
    ticket = st.text_input(
        "Ticket personal de la API Compra Ágil",
        value=API_TICKET_ENV,
        type="password",
        key="ticket_api_compra_agil",
        help="Se utiliza solo en memoria para consultar la API. No se guarda en la biblioteca ni en radar_comercial.db.",
    )
    st.caption(
        "Para obtenerlo, entra al portal oficial de API de Mercado Público, acepta las condiciones e inicia "
        "la solicitud con Clave Única. Cada ticket tiene una cuota diaria; la aplicación no intenta eludirla."
    )
    st.link_button(
        "Solicitar / consultar ticket oficial",
        "https://www.chilecompra.cl/api/",
        icon=":material/open_in_new:",
    )
    st.divider()
    base_importes = st.selectbox(
        "Los precios y costos existentes en el catálogo...",
        options=list(base_opciones),
        index=list(base_opciones).index(base_actual),
        key="base_importes_catalogo",
        help="Este ajuste solo afecta al cálculo del Radar; no cambia los datos del cotizador.",
    )
    if base_importes != store.config("base_importes_catalogo", "Incluyen IVA"):
        store.set_config("base_importes_catalogo", base_importes)
    st.caption(
        "El Radar convierte los importes seleccionados a valores netos para estimar costos. "
        "Puedes modificar este criterio sin reescribir el catálogo. Revisa la base de los importes antes "
        "de decidir si una oferta es rentable."
    )

@st.cache_data(ttl=5, show_spinner=False)
def filas_catalogo_cache(directorio: str) -> list[dict]:
    """Reduce lecturas del sistema de archivos; los cambios se reflejan en unos segundos."""
    return ct.Biblioteca(directorio).filas_catalogo()


@st.cache_data(ttl=15, show_spinner=False)
def resumen_historial_cache(ruta_db: str, firma_db: str) -> dict:
    """Resume el historial comercial y evita recorrer SQLite en cada interacción."""
    h = hs.Historial(Path(ruta_db).parent)
    return resumen_historial_cotizador(h.listar())


indice = catalogo.indice
productos = filas_catalogo_cache(str(BIBLIOTECA_DIR))
oportunidades_todas = store.listar()
ruta_cotizador_db = BIBLIOTECA_DIR / "cotizador.db"
try:
    _stat_db = ruta_cotizador_db.stat()
    _firma_db = f"{_stat_db.st_mtime_ns}:{_stat_db.st_size}"
except OSError:
    _firma_db = "sin-base"
resumen_cotizador = resumen_historial_cache(str(ruta_cotizador_db), _firma_db)
try:
    _stat_indice = (BIBLIOTECA_DIR / "indice.json").stat()
    FIRMA_INDICE = f"{_stat_indice.st_mtime_ns}:{_stat_indice.st_size}"
except OSError:
    FIRMA_INDICE = "sin-indice"

def clp(valor) -> str:
    try:
        return "$" + f"{round(float(valor)):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"

def fecha_corta(valor) -> str:
    texto = str(valor or "")
    if not texto:
        return "Sin fecha"
    try:
        return date.fromisoformat(texto[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return texto[:16].replace("T", " ")

def slug(valor: str) -> str:
    return hashlib.sha1(str(valor).encode("utf-8")).hexdigest()[:12]

def detalle_a_texto(lineas: list[dict]) -> str:
    resultado = []
    for ln in lineas or []:
        if not isinstance(ln, dict):
            continue
        qty = ln.get("cantidad", 1)
        desc = ln.get("descripcion") or ln.get("nombre") or ""
        unit = ln.get("unidad_medida") or ""
        if desc:
            resultado.append(f"{qty} | {desc} | {unit}".rstrip(" |"))
    return "\n".join(resultado)


def poner_aviso(mensaje: str, tipo: str = "success") -> None:
    st.session_state["radar_aviso_pendiente"] = {"mensaje": mensaje, "tipo": tipo}


def mostrar_aviso_pendiente() -> None:
    aviso = st.session_state.pop("radar_aviso_pendiente", None)
    if not isinstance(aviso, dict) or not aviso.get("mensaje"):
        return
    funcion = {
        "success": st.success,
        "warning": st.warning,
        "error": st.error,
        "info": st.info,
    }.get(aviso.get("tipo"), st.info)
    with st.container(key="radar-flash"):
        funcion(aviso["mensaje"])


def recordar_campo_radar(clave: str) -> None:
    """Conserva un borrador de campo aunque Streamlit deje de renderizar su sección."""
    if clave not in st.session_state:
        return
    borradores = dict(st.session_state.get("_radar_borradores_widgets", {}))
    borradores[clave] = st.session_state[clave]
    st.session_state["_radar_borradores_widgets"] = borradores


def restaurar_campo_radar(clave: str, valor_inicial):
    """Restaura el último valor de sesión cuando una sección vuelve a renderizarse."""
    if clave not in st.session_state:
        borradores = st.session_state.get("_radar_borradores_widgets", {})
        st.session_state[clave] = borradores.get(clave, valor_inicial)
    return st.session_state[clave]


def recordar_oportunidad_evaluacion() -> None:
    codigo = st.session_state.get("evaluar_codigo")
    if codigo:
        st.session_state["_radar_evaluar_codigo"] = codigo


def navegar_a_evaluacion(codigo: str) -> None:
    """Abre el evaluador directamente en la oportunidad elegida."""
    st.session_state["evaluar_codigo"] = codigo
    st.session_state["_radar_evaluar_codigo"] = codigo
    st.session_state["radar_vista"] = "Evaluar rentabilidad"


def navegar_a_vista(vista: str) -> None:
    if vista in {"Panel comercial", "Radar de oportunidades", "Evaluar rentabilidad", "Seguimiento"}:
        st.session_state["radar_vista"] = vista


def estado_plazo(fecha_cierre: str) -> str:
    if not fecha_cierre:
        return "Sin fecha"
    try:
        dias = (date.fromisoformat(str(fecha_cierre)[:10]) - date.today()).days
    except ValueError:
        return "Verificar fecha"
    if dias < 0:
        return "Vencida"
    if dias == 0:
        return "Cierra hoy"
    if dias == 1:
        return "Cierra mañana"
    if dias <= 3:
        return f"Cierra en {dias} días"
    return f"{dias} días"


@st.cache_data(ttl=60, show_spinner=False)
def sugerencias_catalogo_cache(descripcion: str, firma_indice: str, _indice: dict) -> list[dict]:
    """Memoiza coincidencias de catálogo sin invalidar por cada rerun de Streamlit."""
    return sugerir_productos_catalogo(descripcion, _indice, limite=5)

# ─────────────────────────────────────── Navegación
st.markdown(
    '<div class="panel-nota">'
    '<b>Estado de trabajo</b> &nbsp; · &nbsp; '
    + ("Biblioteca central compartida" if GIOVAL_REQUIRE_ACCESS else "Biblioteca local")
    + " &nbsp; · &nbsp; "
    + ("API de ChileCompra lista para consultar" if ticket.strip() else "Sin ticket API: puedes importar Excel/CSV o ingresar oportunidades manualmente")
    + "</div>",
    unsafe_allow_html=True,
)
vista_actual = st.segmented_control(
    "Sección",
    options=["Panel comercial", "Radar de oportunidades", "Evaluar rentabilidad", "Seguimiento"],
    default="Panel comercial",
    key="radar_vista",
    label_visibility="collapsed",
    width="stretch",
)
mostrar_aviso_pendiente()

if vista_actual == "Panel comercial":
    st.subheader("Vista general")
    en_evaluacion = sum(1 for r in oportunidades_todas if r.get("decision") == "En evaluación")
    para_cotizar = sum(1 for r in oportunidades_todas if r.get("decision") in ("Cotizar", "Presentada"))
    productos_con_costo = sum(1 for f in productos if f.get("costo_neto") is not None)
    productos_sin_costo = max(0, len(productos) - productos_con_costo)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Oportunidades registradas", len(oportunidades_todas))
    m2.metric("En evaluación", en_evaluacion)
    m3.metric("Para cotizar / presentadas", para_cotizar)
    m4.metric("Productos en biblioteca", len(productos))
    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Cotizaciones en historial", resumen_cotizador["cotizaciones"])
    m6.metric("Aceptadas en historial", resumen_cotizador["aceptadas"])
    m7.metric("Monto aceptado", clp(resumen_cotizador["monto_aceptado"]))
    m8.metric("Productos sin costo", productos_sin_costo)
    if productos_sin_costo:
        st.warning(
            f"Hay {productos_sin_costo} producto(s) sin costo registrado. Las estimaciones de rentabilidad "
            "quedarán incompletas mientras no se confirme ese dato."
        )
    atajo1, atajo2, atajo3 = st.columns(3)
    atajo1.button(
        "Buscar oportunidades",
        icon=":material/travel_explore:",
        type="primary",
        width="stretch",
        on_click=navegar_a_vista,
        args=("Radar de oportunidades",),
        key="panel_ir_radar",
    )
    pendientes_evaluables = [
        r for r in oportunidades_todas
        if r.get("decision") in ("Nueva", "En evaluación", "Cotizar")
    ]
    atajo2.button(
        "Continuar evaluación",
        icon=":material/calculate:",
        width="stretch",
        disabled=not pendientes_evaluables,
        on_click=navegar_a_evaluacion,
        args=(pendientes_evaluables[0]["codigo"],) if pendientes_evaluables else ("",),
        key="panel_ir_evaluacion",
    )
    atajo3.button(
        "Ver seguimiento",
        icon=":material/track_changes:",
        width="stretch",
        on_click=navegar_a_vista,
        args=("Seguimiento",),
        key="panel_ir_seguimiento",
    )
    st.divider()
    p1, p2 = st.columns([1.25, 1])
    with p1:
        st.markdown("#### Trabajo pendiente")
        pendientes = [r for r in oportunidades_todas if r.get("decision") in ("Nueva", "En evaluación", "Cotizar")]
        if pendientes:
            ver = []
            for r in pendientes[:8]:
                ver.append({
                    "Código": r["codigo"],
                    "Oportunidad": r["nombre"],
                    "Cierre": fecha_corta(r.get("fecha_cierre")),
                    "Seguimiento": r["decision"],
                })
            st.dataframe(pd.DataFrame(ver), hide_index=True, width="stretch")
        else:
            st.info("No hay oportunidades pendientes. Busca nuevas o importa resultados oficiales.")
    with p2:
        st.markdown("#### Productos que más aparecen en tus cotizaciones")
        if resumen_cotizador["productos_frecuentes"]:
            st.dataframe(
                pd.DataFrame(resumen_cotizador["productos_frecuentes"], columns=["Producto", "Cantidad acumulada"]),
                hide_index=True, width="stretch",
            )
        else:
            st.caption("Cuando el historial acumule líneas de productos, aparecerán aquí.")
    st.caption(
        "Los indicadores de cotizaciones vienen del historial existente. Esta aplicación solo agrega "
        "oportunidades y evaluaciones en radar_comercial.db, separado del historial de cotizaciones."
    )

# ─────────────────────────────────────── Radar / importación
elif vista_actual == "Radar de oportunidades":
    st.subheader("Buscar oportunidades")
    st.write(
        "Busca procesos publicados y guarda una lista compartida de oportunidades. "
        "El ticket se consulta desde tu sesión; nunca se almacena."
    )
    with st.form("buscar_api_form"):
        b1, b2 = st.columns([2.3, 1])
        with b1:
            terminos_texto = st.text_area(
                "Palabras clave (una por línea)",
                value="\n".join(PALABRAS_BUSQUEDA_DEFAULT),
                height=150,
                help="Usa términos concretos como toner, tinta, resma, papel, librería o impresora.",
            )
        with b2:
            regiones_nombres = st.multiselect(
                "Regiones",
                options=list(REGIONES.values()),
                default=["Arica y Parinacota"],
            )
            estados_api = st.multiselect(
                "Estados de Compra Ágil",
                options=list(ESTADOS_API.keys()),
                default=["publicada"],
                format_func=lambda e: ESTADOS_API[e],
            )
            max_registros = st.selectbox("Resultados máximos por palabra", [15, 30, 50], index=2)
        buscar = st.form_submit_button("Buscar oportunidades", type="primary", width="stretch")
    st.caption(
        "Cada palabra usa una solicitud a la API y devuelve como máximo una página. "
        "La búsqueda se limita a ocho términos por operación para cuidar la cuota diaria."
    )
    if buscar:
        if not ticket.strip():
            st.error("Ingresa tu ticket personal en «Conexiones y criterios económicos» o importa un Excel/CSV oficial.")
        else:
            palabras = list(dict.fromkeys(x.strip() for x in terminos_texto.splitlines() if x.strip()))
            regiones = [k for k, v in REGIONES.items() if v in regiones_nombres]
            if not palabras:
                st.warning("Escribe al menos una palabra clave.")
            else:
                if len(palabras) > 8:
                    st.warning("La API admite hasta 8 términos por operación; se usarán los primeros 8.")
                barra_busqueda = st.progress(0.0, text="Preparando búsqueda…")
                detalle_busqueda = st.empty()

                def progreso_api(actual: int, total: int, termino: str) -> None:
                    barra_busqueda.progress(
                        actual / max(total, 1),
                        text=f"Consulta {actual} de {total}",
                    )
                    detalle_busqueda.caption(f"Palabra clave: {termino}")

                try:
                    encontrados, errores_api, llamadas = buscar_compras_agiles(
                        ticket=ticket,
                        palabras=palabras[:8],
                        regiones=regiones,
                        estados=estados_api,
                        tamano_pagina=max_registros,
                        on_progress=progreso_api,
                    )
                    for registro in encontrados:
                        store.guardar_oportunidad(registro)
                    barra_busqueda.empty()
                    detalle_busqueda.empty()
                    if encontrados:
                        mensaje = (
                            f"Búsqueda terminada: {len(encontrados)} oportunidad(es) nueva(s) o actualizada(s), "
                            f"{llamadas} consulta(s) a ChileCompra."
                        )
                        if errores_api:
                            mensaje += " Avisos parciales: " + " · ".join(errores_api[:2])
                            poner_aviso(mensaje, "warning")
                        else:
                            poner_aviso(mensaje, "success")
                    elif errores_api:
                        poner_aviso("No se pudo completar la búsqueda. " + " · ".join(errores_api[:3]), "warning")
                    else:
                        poner_aviso("No encontré coincidencias. Prueba otros términos o amplía la región.", "info")
                    st.rerun()
                except (APICompraAgilError, ValueError) as exc:
                    barra_busqueda.empty()
                    detalle_busqueda.empty()
                    st.error(str(exc))

    st.divider()
    st.markdown("#### Importar resultados desde Excel o CSV")
    st.caption(
        "Alternativa si aún no tienes ticket: usa el buscador oficial de Compra Ágil, descarga los resultados "
        "en Excel y cárgalos aquí. La importación intenta reconocer columnas habituales; revisa los campos."
    )
    archivo = st.file_uploader(
        "Archivo de resultados .xlsx o .csv",
        type=["xlsx", "csv"],
        key="radar_import_file",
    )
    if st.button("Importar archivo seleccionado", disabled=archivo is None, key="radar_importar_archivo"):
        try:
            if archivo.name.lower().endswith(".csv"):
                try:
                    df_import = pd.read_csv(io.BytesIO(archivo.getvalue()), encoding="utf-8-sig")
                except UnicodeDecodeError:
                    df_import = pd.read_csv(io.BytesIO(archivo.getvalue()), encoding="latin-1")
            else:
                df_import = pd.read_excel(io.BytesIO(archivo.getvalue()))
            importados, omitidos = 0, 0
            for fila in df_import.to_dict(orient="records"):
                r = normalizar_importacion(fila)
                if not r:
                    omitidos += 1
                    continue
                store.guardar_oportunidad(r)
                importados += 1
            if importados:
                poner_aviso(
                    f"Importación completada: {importados} registro(s) guardado(s); "
                    f"{omitidos} fila(s) omitida(s) por falta de título."
                )
                st.rerun()
            else:
                st.warning(
                    "No reconocí filas con nombre o título. Puedes crear una oportunidad manualmente "
                    "o revisar los encabezados del Excel."
                )
        except Exception as exc:
            st.error(f"No pude leer el archivo: {exc}")

    with st.expander("Agregar una oportunidad manualmente"):
        with st.form("radar_manual_form"):
            c1, c2 = st.columns([1, 2])
            manual_codigo = c1.text_input("Código oficial (si existe)")
            manual_titulo = c2.text_input("Título de la oportunidad *")
            c3, c4 = st.columns(2)
            manual_org = c3.text_input("Organismo comprador")
            manual_region = c4.selectbox("Región", options=list(REGIONES.values()), index=14)
            c5, c6 = st.columns(2)
            manual_cierre = c5.date_input("Fecha de cierre (si está publicada)", value=None, format="DD/MM/YYYY")
            manual_presupuesto = c6.number_input("Presupuesto de referencia en CLP", min_value=0, value=0, step=1000)
            manual_descripcion = st.text_area("Descripción / requerimiento publicado")
            manual_lineas = st.text_area(
                "Productos solicitados (opcional; cantidad | descripción | unidad)",
                placeholder="2 | Tóner CF283A | unidad\n10 | Resma oficio 500 hojas | resma",
            )
            guardar_manual = st.form_submit_button("Guardar oportunidad", type="primary")
        if guardar_manual:
            if not manual_titulo.strip():
                st.error("El título es obligatorio.")
            else:
                codigo = manual_codigo.strip()
                if not codigo:
                    semilla = "|".join((manual_titulo.strip(), manual_org.strip(), str(manual_cierre or "")))
                    codigo = "MANUAL-" + hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:10].upper()
                region_id = next(k for k, v in REGIONES.items() if v == manual_region)
                detalle = normalizar_detalle({
                    "codigo": codigo,
                    "nombre": manual_titulo.strip(),
                    "descripcion": manual_descripcion,
                    "estado": {"codigo": "publicada" if manual_cierre is None or manual_cierre >= date.today() else "cerrada",
                               "glosa": "Ingresada manualmente"},
                    "fechas": {"fecha_cierre": manual_cierre.isoformat() if manual_cierre else ""},
                    "institucion": {"organismo_comprador": manual_org, "region": region_id,
                                    "nombre_region": manual_region},
                    "presupuesto": {"moneda": "CLP", "monto_disponible_clp": manual_presupuesto or None},
                    "productos_solicitados": parsear_lineas_productos(manual_lineas),
                })
                reg = {
                    "codigo": codigo, "nombre": manual_titulo.strip(), "estado_fuente": detalle.get("estado", {}).get("codigo", "publicada"),
                    "estado_glosa": "Ingresada manualmente", "region": region_id,
                    "region_nombre": manual_region, "organismo": manual_org,
                    "fecha_cierre": manual_cierre.isoformat() if manual_cierre else "",
                    "presupuesto_clp": manual_presupuesto or None, "moneda": "CLP",
                    "descripcion": manual_descripcion, "fuente": "Manual",
                    "detalle": detalle, "api_detalle_url": "",
                }
                store.guardar_oportunidad(reg)
                poner_aviso(f"Oportunidad guardada con identificador {codigo}.")
                st.rerun()

    st.divider()
    st.markdown("#### Oportunidades guardadas")
    filtros1, filtros2, filtros3 = st.columns([2.2, 1, 1.2])
    buscar_guardadas = filtros1.text_input(
        "Buscar", placeholder="Código, título u organismo", key="radar_buscar_guardadas"
    )
    decision_filtro = filtros2.selectbox(
        "Seguimiento", ["Todas", *ESTADOS_SEGUIMIENTO], key="radar_decision_filtro"
    )
    plazo_filtro = filtros3.selectbox(
        "Fecha de cierre",
        ["Todas", "Cierra hoy", "Próximos 3 días", "Vencidas", "Con fecha vigente", "Sin fecha"],
        key="radar_plazo_filtro",
    )
    lista = store.listar(decision=decision_filtro, busqueda=buscar_guardadas)

    def cumple_filtro_plazo(registro: dict) -> bool:
        plazo = estado_plazo(registro.get("fecha_cierre", ""))
        if plazo_filtro == "Todas":
            return True
        if plazo_filtro == "Cierra hoy":
            return plazo == "Cierra hoy"
        if plazo_filtro == "Próximos 3 días":
            return plazo == "Cierra mañana" or plazo.startswith("Cierra en ")
        if plazo_filtro == "Vencidas":
            return plazo == "Vencida"
        if plazo_filtro == "Con fecha vigente":
            return plazo not in {"Vencida", "Sin fecha", "Verificar fecha"}
        return plazo == "Sin fecha"

    lista = [registro for registro in lista if cumple_filtro_plazo(registro)]
    st.caption(f"{len(lista)} oportunidad(es) coinciden con los filtros actuales.")
    if lista:
        tabla = []
        for r in lista:
            items_req = (r.get("detalle") or {}).get("productos_solicitados") or []
            tabla.append({
                "Código": r["codigo"],
                "Oportunidad": r["nombre"],
                "Organismo": r["organismo"],
                "Región": r["region_nombre"],
                "Cierre": fecha_corta(r["fecha_cierre"]),
                "Plazo": estado_plazo(r.get("fecha_cierre", "")),
                "Ítems": len(items_req),
                "Presupuesto ref. CLP": r["presupuesto_clp"],
                "Seguimiento": r["decision"],
                "Fuente": r["fuente"],
            })
        limite_tabla = st.selectbox(
            "Filas visibles",
            options=[25, 50, 100],
            index=1,
            key="radar_limite_tabla",
            help="Reduce la lista con los filtros de búsqueda, seguimiento o fecha de cierre.",
        )
        st.dataframe(
            pd.DataFrame(tabla[:limite_tabla]),
            hide_index=True, width="stretch",
            column_config={"Presupuesto ref. CLP": st.column_config.NumberColumn(format="$ %d")},
        )
        claves = [r["codigo"] for r in lista]
        if st.session_state.get("radar_opportunity_selected") not in claves:
            st.session_state["radar_opportunity_selected"] = claves[0]
        elegido = st.selectbox(
            "Abrir oportunidad",
            options=claves,
            format_func=lambda k: f"{k} · {next((x['nombre'] for x in lista if x['codigo']==k), '')}",
            key="radar_opportunity_selected",
        )
        rec = store.obtener(elegido)
        if rec:
            st.markdown(f"### {rec['nombre']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Estado publicado", rec.get("estado_glosa") or rec.get("estado_fuente") or "No indicado")
            c2.metric("Cierre", fecha_corta(rec.get("fecha_cierre")))
            c3.metric("Presupuesto de referencia", clp(rec.get("presupuesto_clp")))
            if rec.get("organismo"):
                st.caption(f"Organismo comprador: {rec['organismo']} · Región: {rec.get('region_nombre') or 'No indicada'}")
            if rec.get("descripcion"):
                with st.expander("Descripción", expanded=True):
                    st.write(rec["descripcion"])
            detalle = rec.get("detalle") or {}
            items = detalle.get("productos_solicitados") or []
            if items:
                st.markdown("**Productos solicitados detectados**")
                st.dataframe(
                    pd.DataFrame([{
                        "Cantidad": x.get("cantidad"),
                        "Producto requerido": x.get("descripcion") or x.get("nombre"),
                        "Unidad": x.get("unidad_medida"),
                    } for x in items]),
                    hide_index=True, width="stretch",
                )
            if rec.get("fuente") == "API Compra Ágil" and ticket.strip():
                if st.button("Actualizar detalle desde ChileCompra", key=f"detalle_api_{slug(elegido)}"):
                    try:
                        with st.spinner("Obteniendo detalle del proceso..."):
                            det = obtener_detalle_compra_agil(ticket, elegido)
                        store.guardar_detalle(elegido, det)
                        poner_aviso("Detalle oficial actualizado.")
                        st.rerun()
                    except APICompraAgilError as exc:
                        st.error(str(exc))
            a_portal, a_eval = st.columns([1, 1])
            a_portal.link_button(
                "Revisar en Mercado Público",
                PORTAL_URL,
                icon=":material/open_in_new:",
                width="stretch",
            )
            a_eval.button(
                "Evaluar rentabilidad",
                icon=":material/calculate:",
                type="primary",
                width="stretch",
                on_click=navegar_a_evaluacion,
                args=(elegido,),
                key=f"ir_evaluar_{slug(elegido)}",
            )
            st.caption("En el portal oficial, busca por el código indicado y confirma estado, fechas, requisitos y documentos.")
            with st.form(f"seguimiento_{slug(elegido)}"):
                nuevo_estado = st.selectbox(
                    "Estado interno de seguimiento",
                    options=list(ESTADOS_SEGUIMIENTO),
                    index=list(ESTADOS_SEGUIMIENTO).index(rec["decision"]) if rec["decision"] in ESTADOS_SEGUIMIENTO else 0,
                )
                nuevas_notas = st.text_area("Notas comerciales (sin datos personales de clientes)", value=rec.get("notas") or "")
                guardar_seguimiento = st.form_submit_button("Guardar seguimiento")
            if guardar_seguimiento:
                store.actualizar_seguimiento(elegido, nuevo_estado, nuevas_notas)
                poner_aviso("Seguimiento guardado para ambos trabajadores.")
                st.rerun()
    else:
        st.info("Todavía no hay oportunidades registradas con esos filtros.")

# ─────────────────────────────────────── Evaluador
elif vista_actual == "Evaluar rentabilidad":
    st.subheader("Evaluar compatibilidad y rentabilidad")
    if not oportunidades_todas:
        st.info("Primero busca, importa o registra una oportunidad en la pestaña Radar.")
    else:
        codigos_evaluables = [r["codigo"] for r in oportunidades_todas]
        seleccion_previa = st.session_state.get("_radar_evaluar_codigo")
        if seleccion_previa not in codigos_evaluables:
            seleccion_previa = codigos_evaluables[0]
        if st.session_state.get("evaluar_codigo") not in codigos_evaluables:
            st.session_state["evaluar_codigo"] = seleccion_previa
        codigo_eval = st.selectbox(
            "Oportunidad a evaluar",
            options=codigos_evaluables,
            format_func=lambda k: f"{k} · {next((x['nombre'] for x in oportunidades_todas if x['codigo']==k), '')}",
            key="evaluar_codigo",
            on_change=recordar_oportunidad_evaluacion,
        )
        st.session_state["_radar_evaluar_codigo"] = codigo_eval
        rec_eval = store.obtener(codigo_eval)
        if rec_eval:
            detalle_eval = rec_eval.get("detalle") or {}
            eval_guardada = rec_eval.get("evaluacion") or {}
            st.markdown(f"### {rec_eval['nombre']}")
            st.caption(
                f"Código: {codigo_eval} · Organismo: {rec_eval.get('organismo') or 'No indicado'} · "
                f"Cierre: {fecha_corta(rec_eval.get('fecha_cierre'))}"
            )
            presupuesto = rec_eval.get("presupuesto_clp")
            if presupuesto:
                st.info(
                    f"Presupuesto informado por la fuente: {clp(presupuesto)}. "
                    "Es una referencia: verifica en las bases si incluye IVA, despacho y todos los ítems."
                )
            productos_req = detalle_eval.get("productos_solicitados") or []
            texto_base = detalle_a_texto(productos_req)
            if not texto_base and detalle_eval.get("descripcion"):
                st.caption("No hay líneas de producto estructuradas. Extrae los requisitos del texto y escríbelos abajo.")
            with st.expander("Requisitos de la oportunidad", expanded=not bool(texto_base)):
                if detalle_eval.get("descripcion"):
                    st.write(detalle_eval["descripcion"])
                clave_requisitos = f"requisitos_eval_{slug(codigo_eval)}"
                restaurar_campo_radar(clave_requisitos, texto_base)
                texto_lineas = st.text_area(
                    "Una línea por requisito: cantidad | descripción | unidad",
                    height=140,
                    key=clave_requisitos,
                    on_change=recordar_campo_radar,
                    args=(clave_requisitos,),
                    help="Formato: cantidad | descripción | unidad. Verifica presentación, marca, modelo y especificaciones.",
                )
                if st.button("Guardar requisitos", key=f"guardar_requisitos_{slug(codigo_eval)}"):
                    actualizado = dict(detalle_eval)
                    actualizado["productos_solicitados"] = parsear_lineas_productos(texto_lineas)
                    store.guardar_detalle(codigo_eval, actualizado)
                    poner_aviso("Requisitos de la oportunidad guardados.")
                    st.rerun()

            productos_req = (store.obtener(codigo_eval) or {}).get("detalle", {}).get("productos_solicitados") or []
            if not productos_req:
                st.warning("Para evaluar la rentabilidad, agrega al menos una línea de producto solicitada.")
            else:
                base_importes = store.config("base_importes_catalogo", "Incluyen IVA")
                eval_lineas_guardadas = eval_guardada.get("lineas") or []
                opciones_codigo = [""] + sorted(indice.keys())
                etiquetas_codigo = {
                    "": "— Sin producto equivalente confirmado —",
                    **{k: f"{k} · {indice[k].get('nombre', k)}" for k in indice},
                }
                lineas_para_calculo = []
                st.markdown("#### Comparación contra tu catálogo")
                st.caption(
                    "La coincidencia automática solo propone candidatos. Confirma manualmente modelo, presentación, "
                    "compatibilidad y unidad antes de considerar completo el cálculo."
                )
                for i, req in enumerate(productos_req):
                    desc = str(req.get("descripcion") or req.get("nombre") or f"Producto {i+1}")
                    old = eval_lineas_guardadas[i] if i < len(eval_lineas_guardadas) else {}
                    matches = sugerencias_catalogo_cache(desc, FIRMA_INDICE, indice)
                    defecto = str(old.get("codigo_catalogo") or "")
                    if defecto not in indice:
                        defecto = matches[0]["clave"] if matches and matches[0]["score"] >= 0.30 else ""
                    idx_defecto = opciones_codigo.index(defecto) if defecto in opciones_codigo else 0
                    equivalencia_guardada = bool(old.get("equivalencia_confirmada", False))
                    codigo_guardado = bool(old.get("codigo_catalogo"))
                    with st.expander(
                        f"{i+1}. {desc[:95]}",
                        expanded=(i == 0 or not equivalencia_guardada or not codigo_guardado),
                    ):
                        c1, c2 = st.columns([2.2, 1])
                        codigo_prod = c1.selectbox(
                            "Producto de tu biblioteca",
                            options=opciones_codigo,
                            index=idx_defecto,
                            format_func=lambda k: etiquetas_codigo.get(k, k),
                            key=f"match_{slug(codigo_eval)}_{i}",
                        )
                        cantidad_default = old.get("cantidad", req.get("cantidad", 1))
                        cantidad = c2.number_input(
                            "Cantidad requerida",
                            min_value=0.0, step=1.0,
                            value=max(0.0, float(cantidad_default or 0)),
                            key=f"qty_{slug(codigo_eval)}_{i}",
                        )
                        confirmada = st.checkbox(
                            "Confirmé modelo, presentación y especificaciones",
                            value=bool(old.get("equivalencia_confirmada", False)),
                            key=f"eq_{slug(codigo_eval)}_{i}",
                        )
                        ficha = indice.get(codigo_prod, {}) if codigo_prod else {}
                        if matches:
                            st.caption(
                                "Candidato más parecido: "
                                + ", ".join(f"{m['clave']} ({m['score']:.0%})" for m in matches[:3])
                                + ". La puntuación es textual, no certifica equivalencia."
                            )
                        costo_base = ficha.get("costo_neto")
                        precio_base = catalogo.precio_sugerido(codigo_prod) if codigo_prod else None
                        if precio_base is None and ficha:
                            precio_base = ficha.get("precio_neto")
                        costo_neto = importe_catalogo_a_neto(costo_base, base_importes)
                        sugerido_neto = importe_catalogo_a_neto(precio_base, base_importes)
                        if costo_neto is not None:
                            st.caption(f"Costo de catálogo normalizado a neto: {clp(costo_neto)} por unidad.")
                        else:
                            st.warning("Este producto no tiene costo registrado; no se puede validar la rentabilidad completa.")
                        precio_old = old.get("precio_oferta_unitario_neto")
                        if precio_old is None:
                            precio_old = sugerido_neto
                        precio_oferta = st.number_input(
                            "Precio unitario de oferta neto (sin IVA)",
                            min_value=0.0, step=100.0,
                            value=max(0.0, float(precio_old or 0)),
                            key=f"offer_{slug(codigo_eval)}_{i}",
                            help="Puedes partir del precio sugerido del catálogo y corregirlo según el proceso.",
                        )
                        incluir = st.checkbox(
                            "Incluir esta línea en la estimación",
                            value=bool(old.get("incluir", True)),
                            key=f"include_{slug(codigo_eval)}_{i}",
                        )
                        if not confirmada and codigo_prod:
                            st.warning("La equivalencia aún no está confirmada. El cálculo quedará incompleto.")
                        lineas_para_calculo.append({
                            "descripcion_solicitada": desc,
                            "cantidad": cantidad,
                            "codigo_catalogo": codigo_prod,
                            "precio_oferta_unitario_neto": precio_oferta or None,
                            "equivalencia_confirmada": confirmada,
                            "incluir": incluir,
                        })

                e1, e2 = st.columns(2)
                despacho_guardado = eval_guardada.get("despacho_neto", 0)
                otros_guardados = eval_guardada.get("otros_costos_netos", 0)
                with e1:
                    despacho = st.number_input(
                        "Despacho / logística neta estimada (CLP)",
                        min_value=0.0, step=100.0,
                        value=max(0.0, float(despacho_guardado or 0)),
                        key=f"despacho_{slug(codigo_eval)}",
                    )
                with e2:
                    otros_costos = st.number_input(
                        "Otros costos netos (CLP)",
                        min_value=0.0, step=100.0,
                        value=max(0.0, float(otros_guardados or 0)),
                        key=f"otros_costos_{slug(codigo_eval)}",
                    )
                costos_confirmados = st.checkbox(
                    "Confirmo que despacho y otros costos están revisados (pueden ser $0)",
                    value=bool(eval_guardada.get("costos_adicionales_confirmados", False)),
                    key=f"costos_confirmados_{slug(codigo_eval)}",
                )
                margen_default = float(store.config("margen_objetivo_pct", "0") or 0)
                margen_objetivo = st.number_input(
                    "Margen objetivo sobre la venta neta (%) · 0 = no definido",
                    min_value=0.0, max_value=90.0, value=margen_default, step=1.0,
                    key=f"margen_objetivo_{slug(codigo_eval)}",
                )
                if margen_objetivo <= 0:
                    st.caption("Aún no se fijó un margen objetivo empresarial. Se mostrará el resultado estimado, sin declarar que cumple un umbral.")
                resultado = evaluar_rentabilidad(
                    lineas_para_calculo, indice, base_importes,
                    despacho_neto=despacho,
                    otros_costos_netos=otros_costos,
                    margen_objetivo_pct=margen_objetivo,
                    costos_adicionales_confirmados=costos_confirmados,
                )
                st.divider()
                st.markdown("#### Resultado preliminar")
                a1, a2, a3, a4 = st.columns(4)
                a1.metric("Ingreso de oferta neto", clp(resultado["ingresos_netos"]))
                a2.metric("Costo de productos identificado", clp(resultado["costo_productos_parcial_neto"]))
                a3.metric("Beneficio neto estimado", clp(resultado["beneficio_estimado_neto"]))
                a4.metric("Margen sobre venta", f"{resultado['margen_sobre_venta_pct']:.1f}%" if resultado["margen_sobre_venta_pct"] is not None else "Incompleto")
                st.caption(
                    f"IVA referencial (19 %): {clp(resultado['ingresos_netos'] * 0.19)} · "
                    f"Oferta referencial con IVA: {clp(resultado['ingresos_netos'] * 1.19)}."
                )
                if resultado["precio_minimo_neto_objetivo"] is not None:
                    st.info(
                        f"Precio neto orientativo para el margen objetivo de {margen_objetivo:.1f}%: "
                        f"{clp(resultado['precio_minimo_neto_objetivo'])} "
                        f"(con IVA: {clp(resultado['precio_minimo_neto_objetivo'] * 1.19)})."
                    )
                if presupuesto:
                    st.caption(
                        f"El presupuesto de la fuente es {clp(presupuesto)}. Compáralo con el total con IVA "
                        "solo después de confirmar qué incluye; no se interpreta automáticamente como tu precio permitido."
                    )
                if resultado["faltan_equivalencias"]:
                    st.warning("Equivalencias por confirmar o líneas excluidas: " + "; ".join(resultado["faltan_equivalencias"]))
                if resultado["faltan_costos"]:
                    st.warning("Productos sin costo suficiente: " + "; ".join(resultado["faltan_costos"]))
                if resultado["faltan_precios"]:
                    st.warning("Líneas sin precio ofertado: " + "; ".join(resultado["faltan_precios"]))
                st.markdown(f"**Lectura del cálculo:** {resultado['resultado']}")
                if st.button("Guardar evaluación compartida", type="primary", key=f"guardar_eval_{slug(codigo_eval)}"):
                    payload_eval = {
                        "lineas": lineas_para_calculo,
                        "despacho_neto": despacho,
                        "otros_costos_netos": otros_costos,
                        "costos_adicionales_confirmados": costos_confirmados,
                        "margen_objetivo_pct": margen_objetivo,
                        "resultado_calculo": resultado,
                    }
                    store.guardar_evaluacion(codigo_eval, payload_eval)
                    store.set_config("margen_objetivo_pct", margen_objetivo)
                    decision_actual = (store.obtener(codigo_eval) or {}).get("decision", "Nueva")
                    if decision_actual in ("Nueva", "En evaluación"):
                        store.actualizar_seguimiento(codigo_eval, "En evaluación", (store.obtener(codigo_eval) or {}).get("notas", ""))
                    poner_aviso("Evaluación guardada en la base compartida de Radar Comercial.")
                    st.rerun()

                st.markdown("#### Checklist antes de decidir")
                checklist = [
                    "Confirmar en el portal oficial que la compra sigue abierta y revisar la convocatoria vigente.",
                    "Verificar marca, modelo, presentación, unidad y todas las especificaciones del producto.",
                    "Actualizar costos de compra y plazos de reposición con el proveedor.",
                    "Confirmar despacho a la dirección indicada y cualquier costo adicional.",
                    "Revisar el presupuesto, plazo de entrega y condiciones de pago en el proceso.",
                    "Enviar la oferta directamente en Mercado Público; Giovalia no la presenta automáticamente.",
                ]
                for j, item in enumerate(checklist):
                    st.checkbox(item, key=f"check_{slug(codigo_eval)}_{j}")
    st.caption(
        "La coincidencia de texto y los cálculos son ayudas de análisis, no una confirmación legal/técnica "
        "ni garantía de adjudicación. Verifica las bases y el producto exacto antes de ofertar."
    )

# ─────────────────────────────────────── Seguimiento y datos propios
elif vista_actual == "Seguimiento":
    st.subheader("Seguimiento comercial")
    oportunidades = store.listar()
    if not oportunidades:
        st.info("Aún no hay oportunidades para reportar.")
    else:
        decisiones = {}
        for r in oportunidades:
            decisiones[r["decision"]] = decisiones.get(r["decision"], 0) + 1
        c1, c2 = st.columns([1, 1.2])
        with c1:
            st.markdown("#### Estado del embudo")
            st.dataframe(
                pd.DataFrame([{"Estado": k, "Oportunidades": decisiones.get(k, 0)} for k in ESTADOS_SEGUIMIENTO]),
                hide_index=True, width="stretch",
            )
        with c2:
            st.markdown("#### Evaluaciones guardadas")
            guardadas = []
            for r in oportunidades:
                e = r.get("evaluacion") or {}
                calc = e.get("resultado_calculo") or {}
                if e:
                    guardadas.append({
                        "Código": r["codigo"],
                        "Oportunidad": r["nombre"],
                        "Seguimiento": r["decision"],
                        "Lectura": calc.get("resultado", "Sin resultado"),
                        "Beneficio neto": calc.get("beneficio_estimado_neto"),
                        "Actualizado": fecha_corta(r.get("actualizado")),
                    })
            if guardadas:
                st.dataframe(
                    pd.DataFrame(guardadas), hide_index=True, width="stretch",
                    column_config={"Beneficio neto": st.column_config.NumberColumn(format="$ %d")},
                )
            else:
                st.caption("Guarda una primera evaluación para verla en este panel.")
        exportar = []
        for r in oportunidades:
            exportar.append({
                "Codigo": r["codigo"], "Oportunidad": r["nombre"], "Organismo": r["organismo"],
                "Region": r["region_nombre"], "Cierre": r["fecha_cierre"], "Estado_fuente": r["estado_fuente"],
                "Seguimiento": r["decision"], "Presupuesto_CLP": r["presupuesto_clp"],
                "Fuente": r["fuente"], "Notas": r["notas"],
            })
        csv_bytes = pd.DataFrame(exportar).to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Exportar seguimiento a CSV", csv_bytes,
            file_name=f"gioval_radar_{date.today():%Y%m%d}.csv",
            mime="text/csv", icon=":material/download:",
        )
    st.divider()
    with st.expander("Copia de seguridad de Radar", expanded=False):
        st.caption(
            "Este ZIP conserva oportunidades, estados, notas comerciales y evaluaciones del Radar. "
            "Es independiente del respaldo de la biblioteca y cotizaciones del cotizador."
        )
        if st.button("Preparar respaldo de Radar", icon=":material/archive:", key="radar_preparar_respaldo"):
            try:
                st.session_state["radar_respaldo_zip"] = store.crear_respaldo_zip()
                st.session_state["radar_respaldo_nombre"] = f"gioval_radar_{date.today():%Y%m%d}.zip"
                st.session_state["radar_respaldo_fecha"] = date.today().strftime("%d/%m/%Y")
                st.success("Respaldo preparado. Descárgalo y guarda una copia fuera del computador central.")
            except Exception as exc:
                st.session_state.pop("radar_respaldo_zip", None)
                st.error(f"No pude preparar el respaldo: {exc}")
        if st.session_state.get("radar_respaldo_zip"):
            st.download_button(
                "Descargar respaldo ZIP",
                st.session_state["radar_respaldo_zip"],
                file_name=st.session_state.get("radar_respaldo_nombre", "gioval_radar.zip"),
                mime="application/zip",
                icon=":material/download:",
                key="radar_descargar_respaldo",
            )
            if st.session_state.get("radar_respaldo_fecha"):
                st.caption(
                    f"ZIP preparado el {st.session_state['radar_respaldo_fecha']}. "
                    "La fecha no confirma la descarga; conserva otra copia segura."
                )
    st.divider()
    st.markdown("#### Fuente de datos")
    st.markdown(
        "- **Catálogo y costos:** se leen desde la biblioteca local o compartida del cotizador.\n"
        "- **Historial:** se agregan cantidades, importes y líneas de producto del historial, sin mostrar ni guardar nombres/RUT de clientes.\n"
        "- **Radar y evaluaciones:** se guardan en una base separada llamada radar_comercial.db.\n"
        "- **Ticket API:** no se escribe en ninguna base de datos ni archivo de configuración."
    )
