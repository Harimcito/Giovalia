"""Cotizaciones con fotos y fichas técnicas. Ejecutar con:  streamlit run app.py"""
import re
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

import buscador as bs
import cotizador as ct
import historial as hs
import lector_ia as li
import pdf_cotizacion as pc
import revisor as rv

APP_DIR = Path(__file__).parent
E = st.session_state

# ------------------------------------------------------------- DISEÑO "tinta en agua"
ANIMAR_FONDO = True   # pon False si prefieres el fondo quieto
TEMA_BASE = {"base": "light", "primaryColor": "#2A7090", "backgroundColor": "#F5F9FB",
             "secondaryBackgroundColor": "#E3EFF5", "textColor": "#17394F", "font": "sans serif"}
TEMA_EXTRA = {"baseRadius": "large", "buttonRadius": "large", "borderColor": "#C9DEE8",
              "linkColor": "#2A7090", "dataframeHeaderBackgroundColor": "#DCEBF3"}


def preparar_tema() -> bool:
    """Instala el diseño claro la primera vez. Devuelve True si esta ventana ya lo está usando."""
    ya_activo = st.get_option("theme.base") == "light"
    try:  # queda guardado para las próximas veces que abras el programa
        cfg = APP_DIR / ".streamlit" / "config.toml"
        texto = cfg.read_text("utf-8") if cfg.exists() else ""
        if "[theme]" not in texto:
            cfg.parent.mkdir(exist_ok=True)
            bloque = "[theme]\n" + "\n".join(f'{k} = "{v}"' for k, v in TEMA_BASE.items()) + "\n"
            cfg.write_text(texto + ("\n" if texto else "") + bloque, "utf-8")
    except OSError:
        pass
    for k, v in {**TEMA_BASE, **TEMA_EXTRA}.items():  # vale desde la próxima actualización de la página
        try:
            st._config.set_option(f"theme.{k}", v)
        except Exception:
            pass
    return ya_activo


TEMA_ACTIVO = preparar_tema()

st.set_page_config(page_title="Cotizaciones con fotos y fichas", page_icon="💧",
                   layout="wide", initial_sidebar_state="collapsed")

CSS_BASE = """
html, body, .stApp { font-size: 18px; }
.block-container { max-width: 1280px; padding-top: 2.4rem; }
h2 { font-size: 1.6rem !important; }
.stButton > button, .stDownloadButton > button, .stLinkButton > a {
    font-size: 1.05rem; padding: 0.65rem 1.2rem; border-radius: 14px; }
.stAppDeployButton, [data-testid="stAppDeployButton"] { display: none; }
.pasos { display: flex; gap: 8px; flex-wrap: wrap; margin: 4px 0 18px 0; }
.paso { padding: 8px 16px; border-radius: 999px; border: 1px solid rgba(128,128,128,.45);
        opacity: .7; font-size: .95rem; }
.paso b { margin-right: 6px; }
.paso.activo { opacity: 1; border: 2px solid #ff4b4b; background: rgba(255,75,75,.18); font-weight: 600; }
.paso.hecho { opacity: 1; border-color: rgba(0,190,100,.7); background: rgba(0,190,100,.15); }
"""

CSS_TINTA = """
.stApp {
  background:
    radial-gradient(60vw 50vh at 5% 0%, rgba(86,170,205,.34), transparent 65%),
    radial-gradient(50vw 45vh at 100% 8%, rgba(158,138,222,.26), transparent 65%),
    radial-gradient(55vw 50vh at 85% 100%, rgba(110,205,190,.30), transparent 65%),
    radial-gradient(45vw 40vh at 0% 95%, rgba(245,165,190,.22), transparent 65%),
    linear-gradient(180deg, #F7FBFD 0%, #EEF6FA 100%);
  background-size: 130% 130%, 130% 130%, 130% 130%, 130% 130%, 100% 100%;
  background-attachment: fixed;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] > div:first-child { background: rgba(255,255,255,.6); backdrop-filter: blur(10px); }
h1, h2, h3 { color: #17394F; letter-spacing: -0.01em; }
h1.titulo { font-size: 2.1rem; font-weight: 700; margin: 0 0 .4rem 0; padding: 0;
  background: linear-gradient(90deg, #17394F 0%, #2A7090 55%, #7A6BC4 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.gota { display: inline-block; width: .8em; height: .8em; margin-right: .45rem; vertical-align: -0.05em;
  border-radius: 62% 38% 55% 45% / 55% 45% 60% 40%;
  background: radial-gradient(circle at 30% 30%, #A9E1F0, #2A7090 58%, #6C5CB5); }
button[kind="primary"], [data-testid="stBaseButton-primary"] {
  background: linear-gradient(135deg, #2A7090 0%, #4F8FB5 55%, #8A7FCB 100%); border: none; color: #fff;
  box-shadow: 0 6px 18px rgba(42,112,144,.28); }
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover { filter: brightness(1.08); color: #fff; }
button[kind="primary"]:disabled, [data-testid="stBaseButton-primary"]:disabled { opacity: .45; box-shadow: none; }
button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
  background: rgba(255,255,255,.78); border: 1px solid rgba(42,112,144,.35); color: #17394F; }
button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover { background: #fff; border-color: #2A7090; }
[data-testid="stVerticalBlockBorderWrapper"]:has(.tarjeta-marca):not(:has([data-testid="stVerticalBlockBorderWrapper"] .tarjeta-marca)) {
  background: rgba(255,255,255,.72); backdrop-filter: blur(8px); border: 1px solid rgba(255,255,255,.95);
  border-radius: 22px; box-shadow: 0 10px 34px rgba(50,100,135,.15); }
[data-testid="stAlert"] { border-radius: 16px; }
[data-baseweb="input"] > div, [data-baseweb="textarea"], [data-baseweb="select"] > div { border-radius: 14px; }
.paso { background: rgba(255,255,255,.62); border-color: rgba(42,112,144,.25); color: #17394F; }
.paso.activo { background: linear-gradient(135deg, #2A7090, #8A7FCB); color: #fff; border: none;
  box-shadow: 0 6px 16px rgba(42,112,144,.28); }
.paso.hecho { background: rgba(110,205,190,.38); border-color: rgba(60,160,150,.6); }
"""
CSS_ANIMACION = """
@keyframes tinta { 0% { background-position: 0% 0%, 100% 0%, 60% 100%, 0% 100%, 0 0; }
                   100% { background-position: 18% 12%, 82% 18%, 70% 82%, 12% 88%, 0 0; } }
.stApp { animation: tinta 80s ease-in-out infinite alternate; }
@media (prefers-reduced-motion: reduce) { .stApp { animation: none; } }
"""
st.markdown("<style>" + CSS_BASE + (CSS_TINTA + (CSS_ANIMACION if ANIMAR_FONDO else "") if TEMA_ACTIVO else "")
            + "</style>", unsafe_allow_html=True)

E.setdefault("paso", 1)

COL_INCLUIR, COL_CANT, COL_PROD, COL_COD, COL_ORIGEN = (
    "Incluir", "Cantidad", "Producto", "Código", "¿Cómo lo identifiqué?")
COL_PRECIO = "Precio unitario"
ORIGEN = {"biblioteca": "📚 Ya lo conocía", "ia": "🧠 Lo deduje con IA", "texto": "🔤 Estaba en el texto",
          "descripcion": "✏️ Lo armé con el nombre", "": ""}

# ============================================================== AJUSTES (barra lateral)
with st.sidebar:
    st.header("⚙️ Ajustes")
    carpeta = st.text_input("Carpeta donde guardo las fotos y fichas", "biblioteca",
                            help="No hace falta cambiarla.")
    bib = ct.Biblioteca(carpeta)
    hist = hs.Historial(carpeta)
    st.caption(f"📦 {len(bib.indice)} productos guardados")
    api_key, origen_key = li.buscar_api_key(APP_DIR)
    if api_key:
        st.caption(f"🔑 Clave de OpenAI lista ({origen_key})")
    else:
        api_key = st.text_input("Clave de OpenAI", type="password",
                                help="Sirve para entender mejor los productos y leer pedidos en PDF o foto.")
        if api_key and st.button("💾 Guardar esta clave"):
            li.guardar_api_key(APP_DIR, api_key)
            st.success("Guardada. La próxima vez se carga sola.")
    with st.expander("Opciones avanzadas"):
        usar_lo = st.checkbox("Usar LibreOffice para el PDF", value=False,
                              help="Dejar desmarcado. Solo para un formato idéntico al Excel; "
                                   "puede abrir un cuadro de impresora.")
    with st.expander("Numeración de cotizaciones"):
        fmt = st.text_input("Formato del número", value=hist.config("formato_numero", hs.FORMATO_DEFECTO),
                            help="{n} = número correlativo, {aa} = año con 2 cifras, {aaaa} = año completo. "
                                 "Ejemplo: {n:04d}-COT{aa}")
        sig = st.number_input("Próximo número", min_value=1, step=1, value=hist.siguiente_correlativo())
        st.caption(f"Se verá así: {hist.formatear(int(sig), date.today(), fmt)}")
        if st.button("Guardar numeración"):
            hist.set_config("formato_numero", fmt)
            hist.set_config("siguiente_correlativo", int(sig))
            st.success("Guardado.")
    st.divider()
    st.button("📝 Volver a los borradores", on_click=lambda: E.clear())


# ============================================================== FUNCIONES COMUNES
VISTAS = ["🧾 Nueva cotización", "📚 Historial", "📦 Mis productos"]
OPCIONES_PRECIO = ["Incluyen IVA (lo que paga el cliente)", "Son netos (sin IVA)"]
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

    # Una cotización importada parte mostrando precios netos. Un pedido nuevo respeta el modo elegido.
    if origen_pedido == "cotizacion":
        E["precios_modo"] = OPCIONES_PRECIO[1]
        try:
            d = pc.leer_para_pdf(ruta)
            cli = {ct.normalizar(etiqueta): valor for etiqueta, valor in d["izquierda"] + d["derecha"]}
            E["c_nombre"] = cli.get("NOMBRE", "")
            E["c_rut"] = cli.get("RUT", "")
            E["c_dir"] = cli.get("DIRECCION", "")
            E["c_ciudad"] = cli.get("CIUDAD", "")
            E["c_tel"] = cli.get("TELEFONO", "")
            E["c_email"] = cli.get("CORREO", cli.get("EMAIL", ""))
            numero_importado = str(cli.get("NCOTIZACION") or "").strip()
            if numero_importado:
                E["cot_numero"] = numero_importado
            fecha_importada = str(cli.get("FECHA") or "").strip()
            try:
                E["c_fecha"] = date.fromisoformat(fecha_importada[:10])
            except ValueError:
                try:
                    E["c_fecha"] = datetime.strptime(fecha_importada, "%d/%m/%Y").date()
                except ValueError:
                    E.setdefault("c_fecha", date.today())
        except Exception:
            E.setdefault("c_fecha", date.today())
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
    E["tabla"] = tabla_desde_lineas(lineas)
    E["tabla_edit"] = E["tabla"].copy()
    E["editor_version"] = int(E.get("editor_version", 0)) + 1
    E["ruta"] = Path(ruta)
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
        hist.actualizar(E["cot_id"], origen=origen_pedido)
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


def tabla_desde_lineas(lineas: list[dict]) -> pd.DataFrame:
    """Crea la tabla editable a partir de las líneas guardadas del borrador."""
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


def guardar_precios_inventario(lineas: list[dict]):
    """Aprende/actualiza el último precio neto utilizado para cada producto."""
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
            bib.registrar(clave, nombre=descripcion or clave,
                          alias=descripcion or None, precio_neto=float(precio_neto))
            bib.actualizar(clave, nombre=descripcion or clave, precio_neto=float(precio_neto))
        except (TypeError, ValueError):
            # Una línea incompleta no debe impedir guardar el borrador.
            continue


def persistir_borrador():
    """Persiste el trabajo actual en SQLite para recuperarlo tras reiniciar la app."""
    cot_id = E.get("cot_id")
    if not cot_id:
        return
    registro = hist.obtener(cot_id)
    if not registro:
        return
    cliente = cliente_actual()
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
        "precios_modo": E.get("precios_modo", OPCIONES_PRECIO[0]),
        "paso": int(E.get("paso", 1)),
        "orden_df": orden,
        "orden_texto": E.get("orden_texto", ""),
        "fuente_orden": E.get("fuente_orden", ""),
        "editado_manual": bool(E.get("editado_manual", False)),
    }
    fecha = cliente.get("fecha") or date.today()
    hist.actualizar(
        cot_id, datos=datos, fecha=fecha, cliente=cliente["nombre"], rut=cliente["rut"],
        neto=tot["neto"], iva=tot["iva"], total=tot["total"],
        con_iva=int(E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]),
    )
    guardar_precios_inventario(lineas)


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
    if numero and numero != registro["numero"]:
        try:
            hist.renumerar(cot_id, numero)
            E.pop("error_numero", None)
            E["editado_manual"] = True
            for k in ("pdf_bytes", "pdf_nombre", "informe"):
                E.pop(k, None)
            actualizado = hist.obtener(cot_id)
            if actualizado and actualizado.get("xlsx"):
                E["ruta"] = Path(actualizado["xlsx"])
        except ValueError as exc:
            E["error_numero"] = str(exc)
    elif numero == registro["numero"]:
        E.pop("error_numero", None)
    if E.get("error_numero"):
        st.error(E["error_numero"])
    st.caption("Puedes cambiarlo durante la cotización, antes de crear el PDF.")


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
        if E.get("lineas") and E.get("ruta"):
            refrescar_excel_actual()
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
    persistir_borrador()
    if cambio:
        refrescar_excel_actual()


def editor_productos():
    """Lista editable de cantidades, descripciones, códigos y precios para todos los pasos."""
    base = E.get("tabla_edit", E.get("tabla"))
    if base is None:
        base = pd.DataFrame(columns=[COL_INCLUIR, COL_CANT, COL_PROD, COL_COD, COL_PRECIO, COL_ORIGEN])
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


def refrescar_excel_actual():
    """Genera una copia actualizada del Excel para que el PDF use la última edición."""
    cot_id = E.get("cot_id")
    if not cot_id or "lineas" not in E:
        return
    if E.get("origen_pedido") == "cotizacion" and not E.get("editado_manual", False):
        return  # Si no se editó, se respeta el Excel original subido.
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
    if E["lineas"]:
        E["tabla"] = tabla_desde_lineas(E["lineas"])
        E["tabla_edit"] = E["tabla"].copy()
        if registro.get("xlsx") and Path(registro["xlsx"]).exists():
            E["ruta"] = Path(registro["xlsx"])
        else:
            refrescar_excel_actual()
    elif registro.get("xlsx") and Path(registro["xlsx"]).exists():
        E["ruta"] = Path(registro["xlsx"])
    E["paso"] = E["paso"] if E["lineas"] else 1
    persistir_borrador()


def selector_borradores():
    """Pantalla de inicio para retomar un borrador o comenzar uno nuevo."""
    borradores = []
    for reg in hist.listar("", "Borrador"):
        pdf = Path(reg["pdf"]) if reg.get("pdf") else None
        if not pdf or not pdf.exists():
            borradores.append(reg)
    st.header("📝 Cotizaciones y borradores")
    if borradores:
        st.write("Encontré cotizaciones sin PDF terminado. Puedes retomar cualquiera; los cambios se guardan automáticamente.")
        opciones = {
            f"{r['numero']} · {r.get('cliente') or 'sin cliente'} · {fecha_corta(r.get('fecha') or '')}": r["id"]
            for r in borradores
        }
        elegido = st.selectbox("Borrador que quieres retomar", list(opciones), key="borrador_a_retomar")
        c1, c2 = st.columns(2)
        if c1.button("↩️ Retomar borrador", type="primary", width="stretch"):
            restaurar_borrador(opciones[elegido])
            st.rerun()
        if c2.button("➕ Iniciar otra cotización", width="stretch"):
            iniciar_borrador()
            st.rerun()
    else:
        st.write("Cuando comienzas, se crea un borrador automáticamente. Puedes volver a él si se cierra la aplicación.")
        if st.button("➕ Iniciar nueva cotización", type="primary", width="stretch"):
            iniciar_borrador()
            st.rerun()


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
    FUENTES = ["✍️ Lo escribo o pego aquí", "📊 Subir Excel o CSV", "📷 Subir PDF o foto"]
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
            st.warning("Para leer PDF o fotos necesito la clave de OpenAI (⚙️ Ajustes, arriba a la izquierda).")

    if st.button("📖 Leer el pedido", type="primary", width="stretch"):
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
            E.pop("orden_editor", None)
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
        c2.caption("⚠️ Este RUT no parece válido. Revisa el dígito final.")
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
    orden = editor(
        E["orden_df"], num_rows="dynamic", hide_index=True, width="stretch", key="orden_editor",
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
            hist.actualizar(
                E["cot_id"], datos={"lineas": lineas_orden, "cliente": cliente, "precios_modo": E["precios_modo"],
                                    "origen_pedido": "pedido", "paso": 2},
                fecha=cliente["fecha"], cliente=nombre, rut=rut, neto=totales["neto"],
                iva=totales["iva"], total=totales["total"], con_iva=int(con_iva), origen="pedido",
            )
            hist.guardar_archivos(E["cot_id"], xlsx=ruta)
            hist.guardar_cliente(cliente)
        except Exception as exc:
            st.error(f"No pude guardar el borrador. Los datos siguen en pantalla: {exc}")
            return
        E["ruta"] = Path(hist.obtener(E["cot_id"])["xlsx"])
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
        st.info("💡 Con la clave de OpenAI (en ⚙️ Ajustes, arriba a la izquierda) entiendo mucho mejor los "
                "productos y puedo leer pedidos en PDF o foto.")
    tab_pedido, tab_cot = st.tabs(["📝 Tengo un pedido", "📄 Ya tengo la cotización hecha"])
    with tab_pedido:
        pedido_ui()
    with tab_cot:
        cotizacion_ui()


# ============================================================== PASO 2: PRODUCTOS
def paso_2():
    st.header("2. Revisa tus productos y precios")
    st.write("Puedes agregar, quitar o modificar cantidades, descripciones, códigos y precios aquí. "
             "Lo que cambies se conserva al avanzar y se usa al crear el PDF.")
    if not api_key:
        st.info("Con la clave de OpenAI puedo deducir mejor la marca, el modelo y el código.")

    editor_productos()
    editar_cabecera()
    mostrar_totales()
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
        E.pop("orden_editor", None)
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
        estado_ficha = "⚠️ revisar" if n == 0 else "✅ lista"
        if n == 0:
            aviso_ficha = (f"La ficha guardada no menciona el código «{clave}». Puede ser de otro producto: "
                           "busca otra o revísala.")
    else:
        estado_ficha = "❌ falta"
    with st.container(border=True):
        st.markdown('<span class="tarjeta-marca"></span>', unsafe_allow_html=True)
        c1, c2 = st.columns([1, 3])
        with c1:
            if foto:
                try:
                    st.image(str(foto), width=190)
                except Exception:  # archivo dañado: no debe botar la pantalla
                    st.markdown("🖼️  \n*No pude mostrar la foto. Elige otra.*")
            else:
                st.markdown("🖼️  \n*Sin foto*")
        with c2:
            st.markdown(f"**{desc}**")
            st.caption(f"Código: {clave}")
            st.markdown(f"📷 Foto: {'✅ lista' if foto else '❌ falta'} &nbsp;&nbsp;&nbsp; 📄 Ficha técnica: {estado_ficha}")
            b1, b2 = st.columns(2)
            sug_foto, sug_ficha = consultas_sugeridas(clave, desc)
            q_foto, q_ficha = E.get(f"qf_{clave}", sug_foto), E.get(f"qd_{clave}", sug_ficha)
            if b1.button("🔎 Buscar en internet", key=f"b_{clave}", width="stretch"):
                with st.spinner("Buscando…"):
                    for aviso in buscar_para(clave, q_foto, q_ficha):
                        st.warning(aviso)
            with b2.popover("⚙️ Más opciones", width="stretch"):
                st.text_input("Buscar la foto como", value=sug_foto, key=f"qf_{clave}")
                st.text_input("Buscar la ficha como", value=sug_ficha, key=f"qd_{clave}")
                if ficha and st.button("📄 Sacar la foto desde la ficha", key=f"xf_{clave}"):
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
                        st.caption(("✅ Coincide con el código" if c["verificada"] else "⚪ Sin confirmar")
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
                        estado = "ℹ️ Abre el PDF y confirma que es el producto"
                    else:
                        estado = {3: f"✅ Menciona el código {clave}",
                                  2: f"🟡 Menciona {clave}, pero es largo ({c['paginas']} pág.): puede ser un catálogo",
                                  1: f"⚠️ No menciona {clave}: puede ser de otro producto"}[c["nivel"]]
                    k2.markdown(f"{estado}  \n{c['titulo'][:80]}  \n[Abrir el PDF]({c['url']})")
                    if k2.button("Elegir esta ficha" if c["nivel"] >= 2 else "Elegirla igual",
                                 key=f"ufi_{clave}_{i}"):
                        guardar_candidato(clave, "ficha", c)


def paso_3():
    st.header("3. Fotos y fichas técnicas")
    st.caption("La lista y los precios siguen editables en esta etapa. Los cambios se guardan en el borrador.")
    editor_productos()
    editar_cabecera()
    mostrar_totales()
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
        if st.button(f"🔎 Buscar fotos y fichas de lo que falta ({len(faltan)})", type="primary", width="stretch"):
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

    for clave in claves:
        tarjeta(clave, descripciones[clave])

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
        st.success("✅ Revisé la cotización: los totales, el IVA, los precios y los datos del cliente están en orden.")
        return 0
    st.markdown("#### 🔍 Revisión de la cotización")
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
    st.write("Este es el último paso para corregir los datos. Puedes seguir editando los productos, sus cantidades, códigos y precios hasta presionar **Crear el PDF**.")
    editor_productos()
    editar_cabecera()
    mostrar_totales()

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
    if st.button("📄 Crear el PDF", type="primary", width="stretch", disabled=not permitido):
        try:
            refrescar_excel_actual()
            generar_pdf()
        except Exception as exc:
            st.error(f"No pude crear el PDF: {exc}")
    if "pdf_bytes" in E:
        st.success(f"¡Listo! Tu PDF tiene {E['informe']['paginas']} páginas y quedó guardado en el historial.")
        st.download_button("⬇️ Descargar el PDF", E["pdf_bytes"], file_name=E["pdf_nombre"],
                           mime="application/pdf", type="primary", width="stretch")
        if E.get("ruta") and Path(E["ruta"]).exists():
            st.download_button("⬇️ Descargar la cotización en Excel", Path(E["ruta"]).read_bytes(),
                               file_name=Path(E["ruta"]).name, width="stretch")
        textos = (("sin_clave", "Estos productos no tienen código, así que no llevan foto ni ficha"),
                  ("sin_foto", "Les falta la foto"), ("sin_ficha", "Les falta la ficha técnica"),
                  ("ficha_dudosa", "Revisa estas fichas: no mencionan el código del producto"),
                  ("ficha_ilegible", "No pude leer estas fichas"))
        for clave_info, texto in textos:
            if E["informe"][clave_info]:
                st.warning(f"{texto}: " + ", ".join(E["informe"][clave_info]))
    st.divider()
    atras, nueva = st.columns(2)
    if atras.button("← Volver a fotos y fichas", width="stretch"):
        ir_a(3)
    nueva.button("🔄 Hacer otra cotización", width="stretch", on_click=lambda: E.clear())


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
    for k in ("lineas", "ruta", "tabla", "tabla_edit", "info_desc", "pdf_bytes", "pdf_nombre", "informe",
              "orden_editor", "ok_riesgo", "ok_sin_precio", "cli_sel", "origen_pedido"):
        E.pop(k, None)
    E["precios_modo"] = OPCIONES_PRECIO[0 if con_iva else 1]
    E["orden_df"] = pd.DataFrame({
        "Incluir": [_esta_marcado(ln.get("incluir"), True) for ln in lineas],
        "Cantidad": [ln["cantidad"] for ln in lineas],
        "Producto": [ln["descripcion"] for ln in lineas],
        "Precio unitario": [ct.precio_para_mostrar(
            ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"), con_iva)
            if (ln.get("unitario") is not None or ln.get("precio_neto") is not None) else None for ln in lineas]})
    E.update(c_nombre=cli.get("nombre", ""), c_rut=cli.get("rut", ""), c_dir=cli.get("direccion", ""),
             c_ciudad=cli.get("ciudad", ""), c_tel=cli.get("telefono", ""), c_email=cli.get("email", ""))
    try:
        E["c_fecha"] = date.fromisoformat(rec["fecha"][:10]) if mantener else date.today()
    except ValueError:
        E["c_fecha"] = date.today()
    if mantener:
        E["cot_id"] = cot_id
    else:
        E.pop("cot_id", None)
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
    texto = f1.text_input("Buscar", placeholder="Número, cliente, RUT o notas", key="h_texto")
    estado = f2.selectbox("Estado", ["Todos"] + hs.ESTADOS, key="h_estado")
    regs = hist.listar(texto, estado)
    if not regs:
        st.warning("No encontré cotizaciones con ese filtro.")
        return
    df = pd.DataFrame({"id": [r["id"] for r in regs], "N°": [r["numero"] for r in regs],
                       "Fecha": [fecha_corta(r["fecha"]) for r in regs], "Cliente": [r["cliente"] for r in regs],
                       "Total": [r["total"] for r in regs], "Estado": [r["estado"] for r in regs],
                       "Notas": [r["notas"] or "" for r in regs]})
    editado = editor(
        df, hide_index=True, width="stretch", key=f"h_tabla_{E['h_ver']}_{texto}_{estado}",
        disabled=["N°", "Fecha", "Cliente", "Total"],
        column_config={"id": None, "Total": st.column_config.NumberColumn(format="$ %d"),
                       "Estado": st.column_config.SelectboxColumn(options=hs.ESTADOS, required=True)})
    if st.button("💾 Guardar estados y notas"):
        cambios = 0
        for (_, fila), reg in zip(editado.iterrows(), regs):
            nuevo_estado, nuevas_notas = fila["Estado"], str(fila["Notas"] or "")
            if nuevo_estado != reg["estado"] or nuevas_notas != (reg["notas"] or ""):
                hist.actualizar(reg["id"], estado=nuevo_estado, notas=nuevas_notas)
                cambios += 1
        E["h_ver"] += 1
        st.success(f"Guardado ({cambios} cambios).")
        st.rerun()

    st.divider()
    opciones = {f"{r['numero']} · {r['cliente'] or 'sin cliente'} · {rv.clp(r['total'] or 0)}": r["id"] for r in regs}
    elegida = st.selectbox("Elige una cotización para ver qué puedes hacer con ella", list(opciones))
    rec = hist.obtener(opciones[elegida])
    with st.container(border=True):
        st.markdown(f"**{rec['numero']}** · {rec['cliente'] or 'sin cliente'} · {rec['estado']} · "
                    f"{fecha_corta(rec['fecha'])}")
        d1, d2 = st.columns(2)
        pdf, xlsx = Path(rec["pdf"] or ""), Path(rec["xlsx"] or "")
        if rec["pdf"] and pdf.exists():
            d1.download_button("⬇️ Descargar el PDF", pdf.read_bytes(), file_name=pdf.name, mime="application/pdf",
                               width="stretch", key=f"dp_{rec['id']}")
        else:
            d1.caption("Todavía no se ha creado el PDF de esta cotización.")
        if rec["xlsx"] and xlsx.exists():
            d2.download_button("⬇️ Descargar el Excel", xlsx.read_bytes(), file_name=xlsx.name,
                               width="stretch", key=f"dx_{rec['id']}")
        b1, b2 = st.columns(2)
        b1.button("🔁 Usar como base de una cotización nueva", width="stretch", key=f"dup_{rec['id']}",
                  on_click=cargar_en_pedido, args=(rec["id"], False))
        b2.button("✏️ Seguir editando esta cotización", width="stretch", key=f"edi_{rec['id']}",
                  on_click=cargar_en_pedido, args=(rec["id"], True))
        with st.expander("🗑️ Eliminar esta cotización"):
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
    if st.button("💾 Guardar clientes"):
        hist.reemplazar_clientes(editado.to_dict("records"))
        E["cli_ver"] += 1
        st.success("Clientes guardados.")
        st.rerun()


def vista_historial():
    st.header("📚 Historial")
    tab_cot, tab_cli = st.tabs(["Cotizaciones", "👥 Clientes"])
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
    st.header("📦 Mis productos")
    st.write("Aquí están los productos que he aprendido. Puedes corregir nombres, anotar el **costo** y el **margen** "
             "para que te sugiera el precio de venta, y cambiar su foto o su ficha.")
    if "p_msg" in E:
        nivel, texto = E.pop("p_msg")
        (st.success if nivel == "ok" else st.error)(texto)
    with st.expander("➕ Agregar un producto a mano"):
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
        "Foto": ["✅" if f["foto"] else "❌" for f in filas], "Ficha": ["✅" if f["ficha"] else "❌" for f in filas],
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
    if st.button("💾 Guardar cambios de la tabla"):
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
    with st.expander("✏️ Cambiar el código o eliminar este producto"):
        st.text_input("Nuevo código", key=f"ren_{elegido}")
        st.checkbox("Si ya existe un producto con ese código, combinarlos", key=f"comb_{elegido}")
        st.button("Cambiar el código", key=f"renb_{elegido}", on_click=renombrar_producto, args=(elegido,))
        st.divider()
        seguro = st.checkbox("Sí, quiero eliminar este producto con su foto y su ficha", key=f"delp_{elegido}")
        st.button("Eliminar producto", disabled=not seguro, key=f"delpb_{elegido}",
                  on_click=borrar_producto, args=(elegido,))


# ============================================================== PANTALLA
if TEMA_ACTIVO:
    st.markdown('<h1 class="titulo"><span class="gota"></span>Cotizaciones con fotos y fichas</h1>',
                unsafe_allow_html=True)
else:
    st.title("💧 Cotizaciones con fotos y fichas")
    st.info("🎨 Instalé el nuevo diseño. Presiona la tecla **F5** (actualizar la página) para verlo. "
            "Si sigue igual, cierra la ventana negra y abre el programa de nuevo.")
vista = st.radio("Ir a", VISTAS, horizontal=True, key="vista", label_visibility="collapsed")
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
