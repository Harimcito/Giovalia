"""Cotizaciones con fotos y fichas técnicas. Ejecutar con:  streamlit run app.py"""
import hmac
import html
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
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath
from urllib.parse import quote_plus
from uuid import uuid4

import pandas as pd
import streamlit as st
from pypdf.errors import PdfReadError

import acceso
import anexos_cotizacion as anexos_doc
import fichas_catalogo as fichas_doc
import buscador as bs
import cotizador as ct
import historial as hs
import lector_ia as li
import mercado_publico as mp
import ui_montos as montos
import pdf_cotizacion as pc
import revisor as rv
import revision_cotizacion as revision_doc
import operacion_comercial as op
import operacion_comercial_ui as gestion
import flujo_cotizacion as flujo
import stock_catalogo
import cotizacion_opciones as opciones_cot
import cotizacion_opciones_ui as opciones_ui
import edicion_lineas as edicion
import interfaz_giovalia as interfaz
import identidad_visual as visual
import experiencia_giovalia as experiencia
from vista_precios import vista_precios
import catalogo_clientes as cc
from productos_ui import editar_ficha

APP_DIR = Path(os.environ.get("GIOVAL_DATA_DIR") or Path(__file__).parent).expanduser().resolve()
E = st.session_state
GIOVAL_PROFILES = acceso.perfiles_activos()
GIOVAL_REQUIRE_ACCESS = not GIOVAL_PROFILES and os.environ.get("GIOVAL_REQUIRE_ACCESS", "false").strip().lower() in {"1", "true", "yes", "on"}
GIOVAL_ACCESS_PASSWORD = os.environ.get("GIOVAL_ACCESS_PASSWORD", "")
GIOVAL_ALLOW_INITIAL_IMPORT = os.environ.get("GIOVAL_ALLOW_INITIAL_IMPORT", "false").strip().lower() in {"1", "true", "yes", "on"}

# Aplica datos de una cotización importada al inicio de la siguiente ejecución,
# antes de crear widgets con esas claves (Streamlit no permite cambiarlas después).
_pendiente_importado = E.pop("_pending_import_header", None)
if E.pop("_limpiar_linea_nueva", False):
    E["linea_nueva"] = ""
if isinstance(_pendiente_importado, dict):
    for _clave_widget, _valor_widget in _pendiente_importado.items():
        if _clave_widget in {
            "precios_modo", "c_nombre", "c_rut", "c_dir", "c_ciudad", "c_tel", "c_email", "c_fecha", "cot_numero"
        }:
            E[_clave_widget] = _valor_widget

# ------------------------------------------------------------- IDENTIDAD VISUAL
# La paleta común se define en .streamlit/config.toml para que local y despliegue coincidan.
st.set_page_config(
    page_title="Gioval · Cotizador comercial",
    page_icon=":material/description:",
    layout="wide",
    initial_sidebar_state="auto",
)
visual.estilos()

identidad = acceso.exigir_perfil() if GIOVAL_PROFILES else None

if GIOVAL_REQUIRE_ACCESS:
    if not GIOVAL_ACCESS_PASSWORD:
        st.error("El acceso a la biblioteca compartida no está configurado.")
        st.stop()
    if not E.get("gioval_autenticado"):
        st.title("Giovalia · Biblioteca compartida")
        with st.form("gioval_login_compartido", clear_on_submit=True):
            clave_ingresada = st.text_input(
                "Clave de acceso", type="password", key="gioval_clave", autocomplete="current-password",
            )
            entrar = st.form_submit_button("Entrar a Gioval", type="primary")
        if entrar:
            if hmac.compare_digest(clave_ingresada.encode("utf-8"), GIOVAL_ACCESS_PASSWORD.encode("utf-8")):
                E["gioval_autenticado"] = True
                st.rerun()
            st.error("La clave no es correcta.")
        st.stop()


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
                "SELECT id, xlsx, pdf FROM cotizaciones ORDER BY id"
            ).fetchall()
            for registro_id, xlsx, pdf in registros:
                destinos = []
                for ruta in (xlsx, pdf):
                    partes = PurePosixPath((ruta or "").replace("\\", "/")).parts
                    relativa = Path(*partes[-3:]) if len(partes) >= 3 and partes[-3] == "cotizaciones" else None
                    if (relativa and (stage / relativa).resolve().is_relative_to(stage)
                            and (stage / relativa).is_file()):
                        destinos.append(str(carpeta / relativa))
                    else:
                        destinos.append(None)
                conexion.execute(
                    "UPDATE cotizaciones SET xlsx=?, pdf=? WHERE id=?",
                    (*destinos, registro_id),
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
          "similitud": "Coincidencia catálogo", "descripcion": "Descripción", "": ""}

# ============================================================== CONFIGURACIÓN COMPACTA
# La configuración se renderiza en un popover junto al título, para no ocupar la pantalla.


# ============================================================== FUNCIONES COMUNES
VISTAS = ["Cotizar", "Historial", "Biblioteca", "Catálogo PDF", "Gestión comercial", "Datos pendientes"]
INICIO, PRECIOS = "Inicio", "Precios"
OPCIONES_PRECIO = ["Incluyen IVA (lo que paga el cliente)", "Son netos (sin IVA)"]
FUENTE_TEXTO = "Lo escribo o pego aquí"
FUENTE_TABLA = "Subir Excel o CSV"
FUENTE_DOCUMENTO = "Subir PDF o foto"
FUENTE_MERCADO_PUBLICO = "Mercado Público"
FUENTE_COTIZACION = "Cotización en Excel"
ETIQUETAS_FUENTES = {
    FUENTE_TEXTO: ":material/edit_note: Texto",
    FUENTE_TABLA: ":material/table_view: Excel o CSV",
    FUENTE_DOCUMENTO: ":material/description: PDF o foto",
    FUENTE_MERCADO_PUBLICO: ":material/account_balance: Mercado Público",
    FUENTE_COTIZACION: ":material/upload_file: Cotización en Excel",
}

if E.get("vista") in {"Mis productos", "Catálogo"}:
    E["vista"] = VISTAS[2]
elif E.get("vista") in {"Nueva cotización", "Inicio"}:
    E["vista"] = VISTAS[0]
E.setdefault("vista", INICIO)


def editor(df, **kw):
    """Tabla editable que se estira para mostrar todas sus filas, sin barra de desplazamiento interna."""
    try:
        return montos.editor(df, height="content", **kw)
    except Exception:  # versiones antiguas de Streamlit: calcula el alto a mano
        extra = 1 if kw.get("num_rows") == "dynamic" else 0
        return montos.editor(df, height=min(35 * (len(df) + 1 + extra) + 8, 2400), **kw)

@acceso.proteger
def limpiar_estado_trabajo():
    """Reinicia la cotización actual sin cerrar el login ni borrar preferencias útiles."""
    conservar = (
        "gioval_autenticado",
        "_gioval_token",
        "_gioval_identidad",
        "config_carpeta",
        "config_usar_lo",
        "config_api_key",
        "_mercado_publico_ticket",
        "_respaldo_zip",
        "_respaldo_nombre",
        "_servicios_sesion",
        "_filtros_historial", "_historial_seleccion", "_ambito_historial", "pref_densidad", "_preferencias_ux",
    )
    valores = {clave: E[clave] for clave in conservar if clave in E}
    E.clear()
    E.update(valores)


@acceso.proteger
def guardar_ticket_mp_sesion():
    """Mantiene el ticket disponible al cerrar Ajustes, sin escribirlo en disco."""
    ticket = str(E.get("config_ticket_mercado_publico", "")).strip()
    if ticket:
        E["_mercado_publico_ticket"] = ticket
    else:
        E.pop("_mercado_publico_ticket", None)


@acceso.proteger
def ir_a(paso: int):
    E["paso"] = paso
    persistir_borrador()
    st.rerun()


def mostrar_pasos(actual: int):
    nombres = ["Cargar pedido", "Editar productos", "Revisar fotos y fichas", "Revisar y emitir PDF"]
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            numero = E.get('cot_numero') or E.get('informe', {}).get('resumen', {}).get('numero', '')
            st.markdown(f"**Cotización {numero}**")
            if E.get("_ultima_guarda") and not E.get("_conflicto_guardado"):
                st.badge(f"Guardada · {E['_ultima_guarda']}", icon=":material/cloud_done:", color="green")
            st.button("Borradores", icon=":material/arrow_back:", type="tertiary",
                      key="cotizacion_volver_borradores", on_click=limpiar_estado_trabajo,
                      disabled=bool(E.get("_conflicto_guardado")))
            if E.get("_regreso_historial"):
                st.button("Volver al historial", icon=":material/history:", key="editor_regresar_historial",
                          on_click=lambda: E.update(vista=VISTAS[1]))
        visual.pasos(actual, nombres)
        lineas = [ln for ln in E.get("lineas", []) if ln.get("incluir", True)]
        if lineas:
            pendientes = sum(ln.get("unitario") is None and ln.get("precio_neto") is None for ln in lineas)
            if pendientes:
                st.badge(f"{pendientes} líneas por revisar · Falta precio", icon=":material/info:", color="orange")
            else:
                st.badge("Precios registrados", icon=":material/check_circle:", color="green")
        if not cliente_actual()["nombre"]:
            st.badge("Requiere revisión · Falta el nombre del cliente", icon=":material/edit_note:", color="orange")


def resolver_productos_catalogo(descripciones: list[str]) -> dict[str, dict]:
    """Resuelve descripciones contra productos guardados; recurre a IA ante ambigüedad."""
    resultados = {}
    pendientes = []
    for descripcion in descripciones:
        desc = _texto_seguro(descripcion)
        normalizada = ct.normalizar(desc)
        if not desc or normalizada in resultados:
            continue
        clave = bib.detectar(desc, solo_conocidos=True)
        if clave and not op.conflicto_presentacion(desc, bib.indice[clave]):
            resultados[normalizada] = {"clave": clave, "origen": "biblioteca", "certeza": "alta"}
            continue
        candidatos = [c for c in bib.coincidencias_semanticas(desc, limite=5)
                      if not op.conflicto_presentacion(desc, bib.indice[c["clave"]])][:2]
        primero = candidatos[0] if candidatos else None
        segundo = candidatos[1] if len(candidatos) > 1 else None
        if primero and primero["score"] >= 0.78 and (not segundo or primero["score"] - segundo["score"] >= 0.10):
            resultados[normalizada] = {"clave": primero["clave"], "origen": "similitud",
                                       "certeza": "alta", "nombre": primero["nombre"]}
        else:
            pendientes.append(desc)
    if pendientes and api_key:
        try:
            catalogo_ia = {clave: ficha for clave, ficha in bib.indice.items()
                           if not ct.identidad_conflictiva(clave, ficha)}
            con_ia = li.entender_productos(pendientes, api_key, catalogo_ia)
            for descripcion, match in zip(pendientes, con_ia):
                clave = str(match.get("clave", "")).strip().upper()
                if (clave in catalogo_ia and match.get("certeza") in ("alta", "media")
                        and not op.conflicto_presentacion(descripcion, catalogo_ia[clave])):
                    resultados[ct.normalizar(descripcion)] = {
                        "clave": clave, "origen": "ia", "certeza": match["certeza"],
                        "razon": match.get("razon", ""),
                    }
        except Exception as exc:
            E["_error_match_ia"] = str(exc)
    return resultados


def referencias_lineas_guardadas(lineas):
    return {
        ct.normalizar(linea.get("descripcion")): {
            "clave": linea.get("clave_auto") or "", "origen": linea.get("origen") or "descripcion",
        }
        for linea in lineas if linea.get("es_producto", True)
    }


def productos_pendientes_de_asociar(lineas: list[dict]) -> list[str]:
    pendientes = []
    vistos = set()
    for linea in lineas:
        descripcion = _texto_seguro(linea.get("descripcion"))
        clave = _texto_seguro(linea.get("clave_auto")).upper()
        ficha = bib.indice.get(clave, {})
        asociada = bool(clave in bib.indice and not ct.identidad_conflictiva(clave, ficha))
        normalizada = ct.normalizar(descripcion)
        if linea.get("es_producto", True) and descripcion and not asociada and normalizada not in vistos:
            vistos.add(normalizada)
            pendientes.append(descripcion)
    return pendientes


def referencia_mercado_publico_borrador(registro: dict, datos: dict) -> dict:
    """Recupera el código guardado o reconoce uno usado como número de cotización."""
    referencia = datos.get("mercado_publico")
    referencia = dict(referencia) if isinstance(referencia, dict) else {}
    if _texto_seguro(referencia.get("codigo")):
        return referencia
    candidato = _texto_seguro(registro.get("numero"))
    try:
        tipo = mp.clasificar_codigo(candidato)
    except ValueError:
        return referencia
    referencia.update({
        "codigo": candidato,
        "tipo": "Compra Ágil" if tipo == "compra_agil" else "Orden de compra",
    })
    return referencia


def cargar(ruta: Path, origen_pedido: str, *, lineas_pedido=None):
    """Lee una cotización y prepara líneas, datos del cliente y precios editables."""
    lineas = ct.leer_cotizacion(ruta) if lineas_pedido is None else lineas_pedido
    productos = [ln for ln in lineas if ln["es_producto"]]
    matches = resolver_productos_catalogo([ln["descripcion"] for ln in productos])

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
        if lineas_pedido is not None:
            continue
        if ln["es_producto"]:
            match = matches.get(ct.normalizar(ln["descripcion"]), {})
            ln["clave_auto"] = match.get("clave") or ct.resolver_clave(bib, ln["descripcion"])[0]
            ln["origen"] = match.get("origen") or ("texto" if ln["clave_auto"] else "descripcion")
        else:
            ln["clave_auto"], ln["origen"] = "", ""
        ln["incluir"] = bool(ln.get("incluir", True))

    for k in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
        E.pop(k, None)
    E["editado_manual"] = False
    E["info_desc"] = matches
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
            archivar_cotizacion(E["cot_id"], xlsx=ruta)
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
        **flujo.columnas_entrega(lineas),
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
        es_producto = not any(p in ct.normalizar(descripcion).lower() for p in ct.PALABRAS_NO_PRODUCTO)
        match = E.get("_orden_coincidencias", {}).get(ct.normalizar(descripcion), {}) if es_producto else {}
        clave = match.get("clave") or (bib.detectar(descripcion) if es_producto else "")
        if clave in bib.indice and op.conflicto_presentacion(descripcion, bib.indice[clave]):
            clave = ""
            match = {}
        origen = match.get("origen") or (
            "biblioteca" if clave and clave in bib.indice else "texto" if clave else "descripcion"
        )
        salida.append({
            "cantidad": ct._cantidad(cantidad), "descripcion": descripcion, "unitario": precio_neto,
            "precio_neto": precio_neto, "es_producto": es_producto,
            "incluir": _esta_marcado(fila.get("Incluir"), True),
            "clave_auto": clave, "origen": origen,
            "fuente_pedido": _texto_seguro(fila.get("Origen")),
            "codigo_fuente": _texto_seguro(fila.get("Código")),
            "unidad_fuente": _texto_seguro(fila.get("Unidad")),
            **flujo.datos_entrega(
                fila.get(flujo.COL_STOCK, bib.indice.get(clave, {}).get("estado_stock")),
                fila.get(flujo.COL_PLAZO),
            ),
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


def datos_mercado_publico_guardables() -> dict | None:
    """Guarda referencia y datos básicos, sin conservar adjuntos ni el ticket API."""
    detalle = E.get("_mercado_publico_detalle")
    detalle = detalle if isinstance(detalle, dict) else {}
    codigo = _texto_seguro(
        E.get("_mercado_publico_codigo") or E.get("mercado_publico_codigo") or detalle.get("codigo")
    )
    if not codigo:
        return None
    return {
        "codigo": codigo,
        "tipo": _texto_seguro(detalle.get("tipo")),
        "institucion": _texto_seguro(detalle.get("institucion")),
        "unidad_compra": _texto_seguro(detalle.get("unidad_compra")),
        "rut_comprador": _texto_seguro(detalle.get("rut_comprador")),
    }


def completar_comprador_mercado_publico(detalle: dict) -> list[str]:
    """Usa el organismo comprador y su RUT oficial en la cabecera de la cotización."""
    valores = {
        "c_nombre": _texto_seguro(detalle.get("institucion")),
        "c_rut": _texto_seguro(detalle.get("rut_comprador")),
    }
    aplicados = []
    for campo, valor in valores.items():
        if valor and _texto_seguro(E.get(campo)) != valor:
            E[campo] = valor
            aplicados.append("nombre" if campo == "c_nombre" else "RUT")
    return aplicados


def totales_actuales() -> dict:
    lineas = [
        {"cantidad": ln.get("cantidad", 0), "precio_neto": (ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"))}
        for ln in E.get("lineas", [])
        if _esta_marcado(ln.get("incluir", ln.get("es_producto", True)), True)
    ]
    return rv.totales_de(lineas)


def mostrar_totales():
    """Muestra neto, IVA y total en un panel consistente en cada etapa."""
    tot = totales_actuales()
    with st.container(border=True):
        a, b, c = st.columns(3)
        a.metric("Neto", rv.clp(tot["neto"]))
        b.metric("IVA (19 %)", rv.clp(tot["iva"]))
        c.metric("Total con IVA", rv.clp(tot["total"]), border=True)
        pendientes = [
            ln for ln in E.get("lineas", [])
            if _esta_marcado(ln.get("incluir", ln.get("es_producto", True)), True)
            and ln.get("unitario") is None and ln.get("precio_neto") is None
        ]
        if pendientes:
            st.badge(
                f"{len(pendientes)} {'línea sin precio' if len(pendientes) == 1 else 'líneas sin precio'}",
                icon=":material/pending_actions:", color="orange",
                help="El total corresponde a los productos incluidos con precio registrado.",
            )


def mostrar_resumen_lineas():
    """Resumen de lectura con subtotal por línea, precio pendiente e inclusión."""
    tabla = E.get("tabla_edit")
    if not isinstance(tabla, pd.DataFrame) or tabla.empty:
        return
    resumen = st.expander("Subtotales por producto", icon=":material/table_rows:", key="subtotal_lineas",
                          on_change="rerun")
    if not resumen.open:
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
    with resumen:
        montos.tabla(
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
        if not ln.get("es_producto") or precio_neto is None or not ln.get("incluir", True):
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

        try:
            bib.carpeta(clave)
        except ValueError:
            continue
        ficha = bib.indice.setdefault(clave, {"nombre": descripcion or clave, "aliases": [], "id_global": str(uuid4())})
        nombre = descripcion or clave
        if not ficha.get("nombre"):
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
        "lineas": [{k: v for k, v in linea.items() if k != edicion.ID} for linea in lineas],
        "anexos": anexos_doc.limpiar_referencias(E.get("anexos_cotizacion")),
        "cliente": cliente,
        "origen_pedido": E.get("origen_pedido", "pedido"),
        "fuente_pedido": E.get("fuente_pedido", E.get("fuente_orden", "")),
        "precios_modo": modo_precios,
        "paso": int(E.get("paso", 1)),
        "orden_df": orden,
        "orden_texto": E.get("orden_texto", ""),
        "fuente_orden": E.get("fuente_orden", ""),
        "mercado_publico": datos_mercado_publico_guardables(),
        "editado_manual": bool(E.get("editado_manual", False)),
    }
    fecha = cliente.get("fecha") or date.today()
    campos_guardado = {
        "fecha": fecha, "cliente": cliente["nombre"], "rut": cliente["rut"],
        "neto": tot["neto"], "iva": tot["iva"], "total": tot["total"],
        "con_iva": int(modo_precios == OPCIONES_PRECIO[0]),
    }
    # Streamlit vuelve a ejecutar esta función en muchos reruns sin cambios.
    # Evita abrir/actualizar SQLite y recorrer el catálogo en cada interacción.
    huella = json.dumps(
        {"datos": datos, "campos": campos_guardado},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    if E.get("_snapshot_guardado_id") == cot_id and E.get("_snapshot_guardado") == huella:
        return
    registro = hist.obtener(cot_id)
    if not registro:
        return
    try:
        hist.actualizar_si_version(cot_id, E.get("_cotizacion_version"), datos=datos, **campos_guardado)
    except hs.ConflictoActualizacion as exc:
        E["_conflicto_guardado"] = True
        E["_conflicto_guardado_id"] = cot_id
        E["_conflicto_guardado_mensaje"] = str(exc)
        return
    registro_actual = hist.obtener(cot_id)
    if registro_actual:
        E["_cotizacion_version"] = registro_actual.get("actualizado")
    E["_snapshot_guardado_id"] = cot_id
    E["_snapshot_guardado"] = huella
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
            if GIOVAL_REQUIRE_ACCESS or GIOVAL_PROFILES:
                hist.renumerar(
                    cot_id, numero, version_esperada=E.get("_cotizacion_version")
                )
            else:
                hist.renumerar(cot_id, numero)
            E.pop("error_numero", None)
            E["editado_manual"] = True
            for k in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
                E.pop(k, None)
            actualizado = hist.obtener(cot_id)
            if actualizado:
                E["_cotizacion_version"] = actualizado.get("actualizado")
                if actualizado.get("xlsx"):
                    E["ruta"] = Path(actualizado["xlsx"])
        except hs.ConflictoActualizacion as exc:
            E["_conflicto_guardado"] = True
            E["_conflicto_guardado_id"] = cot_id
            E["_conflicto_guardado_mensaje"] = str(exc)
            return
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
    ids = editada[edicion.ID]
    editada = editada.drop(columns=[edicion.ID])
    tabla_anterior = E.get("tabla_edit")
    cambio = isinstance(tabla_anterior, pd.DataFrame) and not tabla_anterior.equals(editada)
    con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
    nuevas = []
    anteriores_base = {ln[edicion.ID]: ln for ln in E.get("lineas_base", anteriores)}
    ids_nuevas = E.setdefault("_ids_filas_nuevas", {})
    posicion_nueva = 0
    for i, fila in editada.iterrows():
        fila_id = ids.at[i]
        previa = anteriores_base.get(fila_id, {})
        if not previa:
            if posicion_nueva not in ids_nuevas:
                ids_nuevas[posicion_nueva] = uuid4().hex
            fila_id = ids_nuevas[posicion_nueva]
            posicion_nueva += 1
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
        precio_ui = fila.get(COL_PRECIO)
        anterior_neto = previa.get("unitario")
        if anterior_neto is None:
            anterior_neto = previa.get("precio_neto")
        try:
            if pd.isna(precio_ui) or float(precio_ui) < 0:
                precio_neto = None
            elif anterior_neto is not None and precio_ui == ct.precio_para_mostrar(anterior_neto, con_iva):
                precio_neto = anterior_neto
            else:
                precio_neto = ct.neto_desde(float(precio_ui), con_iva)
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
            edicion.ID: fila_id,
            "fila": previa.get("fila", i + 1),
            "cantidad": ct._cantidad(cantidad),
            "descripcion": ct.limpiar_descripcion(descripcion),
            "unitario": precio_neto,
            "precio_neto": precio_neto,
            "es_producto": es_producto,
            "clave_auto": clave,
            "origen": origen,
            "incluir": _esta_marcado(fila.get(COL_INCLUIR), es_producto),
            **flujo.datos_entrega(fila.get(flujo.COL_STOCK), fila.get(flujo.COL_PLAZO)),
        })
    normalizadas = opciones_cot.normalizar_alternativas(nuevas)
    if normalizadas != nuevas:
        aplicar_lineas_editor(normalizadas)
        st.rerun()
    E["tabla_edit"] = editada
    E["lineas"] = nuevas
    if cambio:
        registrar_deshacer(anteriores, nuevas)
        if E.get("paso") == 2 and {ln[edicion.ID] for ln in anteriores} != {ln[edicion.ID] for ln in nuevas}:
            E["_rebasar_lineas"] = True
        E["editado_manual"] = True
        for k in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
            E.pop(k, None)
        # El Excel se regenera al pulsar "Actualizar totales" o al crear el PDF.
        # Evita operaciones de disco pesadas durante la edición normal de cada celda.
        persistir_borrador()


def editor_productos(*, compacto=False):
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
    if E.get("_lineas_base_version") != (E.get("cot_id"), version):
        E["lineas"] = edicion.identificar(E.get("lineas", []))
        E["lineas_base"] = list(E["lineas"])
        E["_ids_filas_nuevas"] = {}
        E["_lineas_base_version"] = (E.get("cot_id"), version)
    base = base.assign(**{
        edicion.ID: pd.Series([ln[edicion.ID] for ln in E["lineas_base"]], index=base.index, dtype="object"),
        flujo.COL_STOCK: base.get(flujo.COL_STOCK, flujo.POR_CONFIRMAR),
        flujo.COL_PLAZO: pd.to_numeric(base.get(flujo.COL_PLAZO, pd.Series(index=base.index, dtype=float))),
    })
    editada = editor(
        base, num_rows="dynamic", hide_index=True, width="stretch",
        key=f"editor_productos_{version}", row_height=34 if compacto else 46,
        column_order=[COL_PROD, COL_COD, COL_INCLUIR, COL_CANT, COL_PRECIO, flujo.COL_STOCK, flujo.COL_PLAZO, COL_ORIGEN],
        disabled=[COL_ORIGEN, edicion.ID],
        column_config={
            edicion.ID: None,
            COL_INCLUIR: st.column_config.CheckboxColumn("Incluir", width="small"),
            COL_CANT: st.column_config.NumberColumn("Cantidad", min_value=0, step=1, width="small"),
            COL_PROD: st.column_config.TextColumn("Producto", width="medium", pinned=True),
            COL_COD: st.column_config.TextColumn("Código", width="small", pinned=True),
            COL_PRECIO: st.column_config.NumberColumn("Precio unitario", min_value=0, format="$ %d", width="medium"),
            COL_ORIGEN: st.column_config.TextColumn("Identificación", width="medium"),
            **columnas_editor_entrega(),
        },
    )
    sincronizar_editor_productos(editada)
    return editada


def columnas_editor_entrega():
    return {
        flujo.COL_STOCK: st.column_config.SelectboxColumn(options=flujo.DISPONIBILIDADES),
        flujo.COL_PLAZO: st.column_config.NumberColumn(min_value=0, step=1),
    }


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
        st.caption("Presiona Enter para aplicar el valor de la celda.")

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


def refrescar_excel_actual(*, archivar=True):
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
            "descripcion": flujo.descripcion_oferta(ln),
            "precio_neto": (ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto")),
        })
    cliente = cliente_actual()
    cliente["numero"] = registro["numero"]
    ruta_tmp = Path(tempfile.mkdtemp()) / "cotizacion_actualizada.xlsx"
    ct.crear_cotizacion_xlsx(lineas_excel, cliente, ruta_tmp)
    if GIOVAL_REQUIRE_ACCESS or not archivar:
        # El Excel contiene los datos del cliente de esta sesión: queda temporal y solo se descarga.
        E["ruta"] = ruta_tmp
    else:
        archivar_cotizacion(cot_id, xlsx=ruta_tmp)
        registro = hist.obtener(cot_id)
        if registro and registro.get("xlsx"):
            E["ruta"] = Path(registro["xlsx"])


@acceso.proteger
def iniciar_borrador(fuente: str = FUENTE_TEXTO):
    """Crea inmediatamente una cotización vacía, lista para guardar avances."""
    limpiar_estado_trabajo()
    fecha = date.today()
    cliente = {"nombre": "", "rut": "", "direccion": "", "ciudad": "", "telefono": "", "email": "", "fecha": fecha}
    cot_id, numero = hist.crear(cliente, [], {"neto": 0, "iva": 0, "total": 0},
                                con_iva=True, fecha=fecha, origen="pedido")
    E.update(
        cot_id=cot_id, cot_numero=numero, c_fecha=fecha, precios_modo=OPCIONES_PRECIO[0],
        paso=1, vista=VISTAS[0], lineas=[], orden_texto="", fuente_orden="", fuente_pedido=fuente,
        editor_version=1, editado_manual=False, origen_pedido="pedido",
        _cotizacion_version=(hist.obtener(cot_id) or {}).get("actualizado"),
    )
    persistir_borrador()


@acceso.proteger
def restaurar_borrador(cot_id: int):
    """Recupera desde SQLite una cotización incompleta, con su lista y precios."""
    registro = hist.obtener(cot_id)
    if not registro:
        return
    datos = registro.get("datos", {})
    cliente = datos.get("cliente", {})
    lineas_restauradas = datos.get("lineas", [])
    mercado_publico = referencia_mercado_publico_borrador(registro, datos)
    mp_codigo = _texto_seguro(mercado_publico.get("codigo"))
    fuente_guardada = datos.get("fuente_pedido", datos.get("fuente_orden", ""))
    es_mercado_publico = bool(mp_codigo or fuente_guardada == "Mercado Público")
    for ln in lineas_restauradas:
        if ln.get("unitario") is None and ln.get("precio_neto") is not None:
            ln["unitario"] = ln["precio_neto"]
        descripcion = _texto_seguro(ln.get("descripcion"))
        ln.setdefault("es_producto", not any(p in ct.normalizar(descripcion).lower() for p in ct.PALABRAS_NO_PRODUCTO))
        ln.setdefault("incluir", True)
        ln.setdefault("clave_auto", "")
        ln.setdefault("origen", "descripcion")
    orden_coincidencias = referencias_lineas_guardadas(lineas_restauradas)
    limpiar_estado_trabajo()
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
        anexos_cotizacion=anexos_doc.limpiar_referencias(datos.get("anexos")),
        orden_texto=datos.get("orden_texto", ""), fuente_orden=datos.get("fuente_orden", ""),
        fuente_pedido="Mercado Público" if es_mercado_publico else fuente_guardada,
        mercado_publico_codigo=mp_codigo, _mercado_publico_codigo=mp_codigo,
        _orden_coincidencias=orden_coincidencias,
        _productos_catalogo_revision=productos_pendientes_de_asociar(lineas_restauradas),
        editado_manual=bool(datos.get("editado_manual", False)), editor_version=1,
        vista=VISTAS[0],
    )
    if es_mercado_publico:
        E["fuente_pedido"] = "Mercado Público"
    if mp_codigo:
        E["_mercado_publico_detalle"] = {
            "codigo": mp_codigo,
            "tipo": _texto_seguro(mercado_publico.get("tipo")),
            "institucion": _texto_seguro(mercado_publico.get("institucion")),
            "unidad_compra": _texto_seguro(mercado_publico.get("unidad_compra")),
            "rut_comprador": _texto_seguro(mercado_publico.get("rut_comprador")),
            "documentos": None,
            "url_portal": "https://www.mercadopublico.cl/",
        }
        completar_comprador_mercado_publico(E["_mercado_publico_detalle"])
    if datos.get("paso", 1) == 1 and isinstance(datos.get("orden_df"), list):
        df = pd.DataFrame(datos["orden_df"])
        for columna, defecto in (("Incluir", True), ("Cantidad", None), ("Producto", ""), ("Precio unitario", None)):
            if columna not in df:
                df[columna] = defecto
        for columna, defecto in (("Código", ""), ("Unidad", ""), ("Origen", "")):
            if columna not in df:
                df[columna] = defecto
        columnas = ["Incluir", "Cantidad", "Producto", "Código", "Unidad", "Origen", "Precio unitario"]
        df = df[columnas + [c for c in (flujo.COL_STOCK, flujo.COL_PLAZO) if c in df]]
        if not df.empty:
            df["Cantidad"] = pd.to_numeric(df["Cantidad"], errors="coerce")
            df["Precio unitario"] = pd.to_numeric(df["Precio unitario"], errors="coerce")
        E["orden_df"] = df
        E["orden_df_base"] = df.copy()
        E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
        lineas_por_descripcion = {
            ct.normalizar(ln.get("descripcion")): ln for ln in lineas_restauradas
        }
        con_iva_guardado = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
        for indice, fila in E["orden_df"].iterrows():
            try:
                precio_vacio = pd.isna(fila.get("Precio unitario"))
            except (TypeError, ValueError):
                precio_vacio = fila.get("Precio unitario") in (None, "")
            ln = lineas_por_descripcion.get(ct.normalizar(fila.get("Producto")))
            if ln:
                neto = ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto")
                if neto is not None and precio_vacio:
                    E["orden_df"].at[indice, "Precio unitario"] = ct.precio_para_mostrar(neto, con_iva_guardado)
        E["orden_df_base"] = E["orden_df"].copy()
    elif lineas_restauradas:
        # Reconstruye la tabla desde la lista comercial saneada, sin requerir
        # el pedido fuente ni un Excel que pudiera contener datos del cliente.
        df = pd.DataFrame({
            "Incluir": [_esta_marcado(ln.get("incluir"), True) for ln in lineas_restauradas],
            "Cantidad": [ln.get("cantidad", 1) for ln in lineas_restauradas],
            "Producto": [ln.get("descripcion", "") for ln in lineas_restauradas],
            "Código": [ln.get("codigo_fuente", "") for ln in lineas_restauradas],
            "Unidad": [ln.get("unidad_fuente", "") for ln in lineas_restauradas],
            "Origen": [ln.get("fuente_pedido", "") for ln in lineas_restauradas],
            "Precio unitario": [ct.precio_para_mostrar(
                ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"),
                bool(registro.get("con_iva", 1)))
                if (ln.get("unitario") is not None or ln.get("precio_neto") is not None) else None
                for ln in lineas_restauradas],
            **flujo.columnas_entrega(lineas_restauradas),
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
    """Inicio operativo: atajos, indicadores, borradores y cotizaciones recientes."""
    registros = hist.listar(incluir_datos=False)
    borradores = [r for r in registros if r.get("estado") == "Borrador"]
    cotizaciones_aceptadas = sum(
        float(r.get("total") or 0) for r in registros if r.get("estado") == "Aceptada"
    )

    with st.container(key="gv_inicio_encabezado"):
        titulo, accion = st.columns([3, 1.35], vertical_alignment="center")
        with titulo:
            st.title("Cotizaciones")
            st.caption("Inicia una propuesta o retoma un borrador guardado.")
        with accion:
            st.button(
                "Nueva cotización", icon=":material/add:", type="primary",
                width="stretch", on_click=iniciar_borrador, key="inicio_nueva_cotizacion",
            )
            st.button(
                "Desde Mercado Público", icon=":material/account_balance:", width="stretch",
                on_click=iniciar_borrador, args=(FUENTE_MERCADO_PUBLICO,), key="inicio_mercado_publico",
            )

    if not opciones_ux["mostrador"]:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Cotizaciones", len(registros), border=True)
        m2.metric("Borradores activos", len(borradores), border=True)
        m3.metric("Monto aceptado", rv.clp(cotizaciones_aceptadas), border=True)
        m4.metric("Productos en biblioteca", len(bib.indice), border=True)

    st.caption("Vistas guardadas")
    mostrar_vistas_guardadas()
    der, izq = st.columns([2.2, 1])
    with izq:
        with st.container(border=True, key="gv_inicio_retomar"):
            st.subheader("Continuar")
            if E.get("cot_id"):
                st.button("Retomar la cotización abierta", icon=":material/arrow_forward:", width="stretch",
                          on_click=navegar_vista, args=(VISTAS[0],), key="inicio_cotizacion_activa")
            if borradores:
                ultimo = max(borradores, key=lambda r: (r.get("actualizado") or r.get("creado") or "", r["id"]))
                st.button("Retomar el último borrador", icon=":material/edit_document:", type="primary",
                          width="stretch", key="inicio_ultimo_borrador", on_click=restaurar_borrador, args=(ultimo["id"],))
                st.caption(ultimo["numero"])
                opciones = {
                    (
                        f"{r['numero']} · {fecha_corta(r.get('fecha') or '')}"
                        if GIOVAL_REQUIRE_ACCESS
                        else f"{r['numero']} · {r.get('cliente') or 'Sin cliente'} · {fecha_corta(r.get('fecha') or '')}"
                    ): r["id"]
                    for r in borradores
                }
                elegido = st.selectbox(
                    "Borradores pendientes", list(opciones),
                    key="borrador_a_retomar", label_visibility="collapsed",
                )
                st.button(
                    "Abrir borrador", icon=":material/arrow_forward:",
                    width="stretch", on_click=restaurar_borrador, args=(opciones[elegido],),
                    key="inicio_retomar_borrador",
                )
            else:
                st.caption("No tienes borradores pendientes.")
    with der, st.container(border=True, key="gv_inicio_actividad"):
        st.subheader("Actividad")
        recientes, borradores_tab = st.tabs(["Recientes", "Borradores"])
        with recientes:
            mostrar_actividad_inicio("Cotizaciones recientes", registros[:5], key="actividad_reciente")
        with borradores_tab:
            consulta = st.text_input("Buscar borrador", key="inicio_buscar_borrador", placeholder="Número de cotización")
            visibles = [r for r in borradores if consulta.strip().casefold() in r["numero"].casefold()]
            mostrar_actividad_inicio("Borradores recientes", visibles[:8], key="actividad_borradores")
        st.button("Ver todas las cotizaciones", icon=":material/arrow_forward:", width="stretch",
                  on_click=navegar_vista, args=(VISTAS[1],), key="inicio_ver_todas")


def mostrar_actividad_inicio(titulo, registros, *, key):
    if not registros:
        st.caption("No hay cotizaciones en esta lista.")
        return
    elegido = lista_cotizaciones(titulo, registros, key=key)
    if elegido is None:
        return
    registro = hist.obtener(elegido)
    if registro and registro["estado"] == "Borrador":
        if E.get("cot_id") == elegido:
            E["vista"] = VISTAS[0]
        else:
            restaurar_borrador(elegido)
    else:
        mostrar_cotizacion_historial(elegido)
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


def _mostrar_detalle_mercado_publico(detalle: dict):
    if detalle.get("aviso"):
        st.warning(detalle["aviso"])
    st.markdown(f"**{detalle.get('codigo', '')} · {detalle.get('nombre', 'Solicitud de compra')}**")
    datos = [
        ("Tipo", detalle.get("tipo")),
        ("Organismo", detalle.get("institucion")),
        ("RUT comprador", detalle.get("rut_comprador")),
        ("Unidad de compra", detalle.get("unidad_compra")),
        ("Estado", detalle.get("estado")),
        ("Cierre", detalle.get("fecha_cierre")),
        ("Dirección de entrega", detalle.get("direccion_entrega")),
        ("Plazo de entrega", f"{detalle['plazo_entrega']} días" if detalle.get("plazo_entrega") else ""),
    ]
    st.caption(" · ".join(f"{k}: {v}" for k, v in datos if v))
    if detalle.get("descripcion"):
        with st.expander("Descripción de la solicitud", expanded=False):
            st.write(detalle["descripcion"])
    documentos = detalle.get("documentos") or []
    if detalle.get("documentos") is None:
        st.caption("El código de Mercado Público quedó vinculado a este borrador. Consulta nuevamente para actualizar sus adjuntos.")
        return
    if not documentos:
        st.caption("La API no informa adjuntos para este código.")
        return
    st.markdown("**Archivos adjuntos informados por Mercado Público**")
    montos.tabla(
        pd.DataFrame([{"Archivo": d.get("nombre", ""), "Identificador": d.get("id", "")} for d in documentos]),
        hide_index=True,
        width="stretch",
        alt="Adjuntos publicados en Mercado Público",
    )
    con_enlace = [d for d in documentos if d.get("url")]
    if con_enlace:
        for i, documento in enumerate(con_enlace):
            st.link_button(
                f"Abrir {documento.get('nombre', 'adjunto')}", documento["url"],
                key=f"mp-adjunto-{i}-{detalle.get('codigo', '')}",
            )
    else:
        st.warning(
            "La API entrega el nombre y el identificador de estos archivos, pero no el enlace de descarga. "
            "Abre el portal para descargarlos y luego súbelos aquí; Giovalia los leerá y sumará sus productos al listado."
        )
        st.link_button("Abrir Mercado Público", detalle.get("url_portal") or "https://www.mercadopublico.cl/", icon=":material/open_in_new:")


def _firma_lineas_borrador(lineas):
    """Firma estable de productos para reenlazar una sesión a su borrador guardado."""
    firma = []
    for linea in lineas or []:
        descripcion = ct.normalizar(_texto_seguro(linea.get("descripcion")))
        try:
            cantidad = round(float(linea.get("cantidad") or 0), 8)
        except (TypeError, ValueError):
            cantidad = _texto_seguro(linea.get("cantidad"))
        incluir = _esta_marcado(linea.get("incluir"), True)
        firma.append((descripcion, cantidad, incluir))
    return tuple(firma)


def pedido_ui(fuente: str):
    E.setdefault("precios_modo", OPCIONES_PRECIO[0])
    con_iva = st.segmented_control(
        "Los precios que ingresarás",
        OPCIONES_PRECIO,
        key="precios_modo",
        format_func=lambda valor: "Con IVA" if valor == OPCIONES_PRECIO[0] else "Valores netos",
        required=True,
        width="content",
        persist_state="session",
    ) == OPCIONES_PRECIO[0]
    E["fuente_orden"] = fuente
    texto = st.text_area(
        "Escribe un producto por línea, con su cantidad adelante",
        placeholder="10 Tóner HP CF283A\n15 Tóner Kyocera TK-3182\n2 Resma carta",
        height=170, key="orden_texto",
    ) if fuente == FUENTE_TEXTO else ""
    archivo_orden, archivos_ia = None, []
    archivos_mp = []
    codigo_mp = ""
    if fuente == FUENTE_TABLA:
        archivo_orden = st.file_uploader("Sube el Excel o CSV del pedido", type=["xlsx", "csv"], key="orden_archivo")
    elif fuente == FUENTE_DOCUMENTO:
        archivos_ia = st.file_uploader("Sube el PDF o las fotos del pedido (puedes subir varias)",
                                       type=["pdf", "jpg", "jpeg", "png", "webp"],
                                       accept_multiple_files=True, key="orden_ia")
        st.caption("El pedido se envía a OpenAI para leerlo. Después revisa que haya quedado bien.")
        if not api_key:
            st.warning("Para leer PDF o fotos necesito la clave de OpenAI (Ajustes, arriba a la derecha).")
    elif fuente == FUENTE_MERCADO_PUBLICO:
        codigo_mp = st.text_input(
            "Código de Compra Ágil u orden de compra",
            placeholder="635-659-COT26 o 635-659-AG26",
            key="mercado_publico_codigo",
        ).strip()
        archivos_mp = st.file_uploader(
            "Adjuntos descargados desde Mercado Público (opcional)",
            type=["pdf", "xlsx", "csv", "docx", "jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="mercado_publico_adjuntos",
            help="Los archivos se leen durante esta consulta y no se guardan en el historial.",
        )
        if api_key:
            st.caption("PDF, Word e imágenes se enviarán a OpenAI para extraer productos. Excel y CSV se leen localmente.")
        if not mp_ticket:
            st.warning("Configura el ticket de Mercado Público en Ajustes para consultar por código.")
        detalle_mostrado = E.get("_mercado_publico_detalle")
        if isinstance(detalle_mostrado, dict) and detalle_mostrado.get("codigo", "").casefold() == codigo_mp.casefold():
            _mostrar_detalle_mercado_publico(detalle_mostrado)

    etiqueta_leer = "Consultar y cargar productos" if fuente == FUENTE_MERCADO_PUBLICO else "Leer el pedido"
    if st.button(etiqueta_leer, icon=":material/description:", type="primary", width="stretch"):
        try:
            if fuente == FUENTE_MERCADO_PUBLICO:
                if not codigo_mp:
                    raise ValueError("Ingresa el código de la Compra Ágil o de la orden de compra.")
                mp.clasificar_codigo(codigo_mp)
                try:
                    with st.spinner("Consultando Mercado Público…"):
                        detalle_mp = mp.consultar_codigo(codigo_mp, mp_ticket)
                except mp.ErrorMercadoPublico as exc:
                    if not archivos_mp:
                        raise
                    st.warning(f"{exc} Continuaré con los adjuntos que subiste. Revisa e ingresa el nombre y RUT del comprador; no se pudieron verificar en Mercado Público.")
                    detalle_mp = {"codigo": codigo_mp, "productos": [], "documentos": []}
                E["_mercado_publico_detalle"] = detalle_mp
                E["_mercado_publico_codigo"] = codigo_mp
                if detalle_mp.get("aviso"):
                    st.warning(detalle_mp["aviso"])
                completar_comprador_mercado_publico(detalle_mp)
                filas = list(detalle_mp.get("productos", []))
                if archivos_mp:
                    if any(Path(a.name).suffix.lower() in {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp"}
                           for a in archivos_mp) and not api_key:
                        raise ValueError("Para leer PDF, Word o imágenes necesitas configurar OpenAI en Ajustes. Excel y CSV no requieren esa clave.")
                    with st.spinner("Leyendo los adjuntos y contrastando productos…"):
                        filas.extend(mp.leer_adjuntos(archivos_mp, api_key))
            elif fuente == FUENTE_DOCUMENTO:
                if not archivos_ia:
                    raise ValueError("Primero sube un PDF o una foto.")
                with st.spinner("Leyendo el pedido…"):
                    filas = li.leer_orden_ia([(f.name, f.getvalue()) for f in archivos_ia], api_key)
            elif fuente == FUENTE_TABLA:
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
            if fuente == FUENTE_MERCADO_PUBLICO and E.get("_mercado_publico_detalle"):
                st.warning("La plataforma no entregó productos en el listado normal. Descarga un adjunto y súbelo arriba para extraer los productos.")
            else:
                st.warning("No encontré productos. Revisa el pedido e inténtalo de nuevo.")
        else:
            E.pop("_error_match_ia", None)
            with st.spinner("Buscando tus productos en la biblioteca…"):
                coincidencias = resolver_productos_catalogo([f["descripcion"] for f in filas])
            for fila in filas:
                codigo_catalogo = _texto_seguro(fila.get("codigo")).upper()
                ficha = bib.indice.get(codigo_catalogo, {})
                if (codigo_catalogo in bib.indice and not ct.identidad_conflictiva(codigo_catalogo, ficha)
                        and not op.conflicto_presentacion(fila["descripcion"], ficha)):
                    coincidencias[ct.normalizar(fila["descripcion"])] = {
                        "clave": codigo_catalogo, "origen": "biblioteca", "certeza": "alta"
                    }
            E["_orden_coincidencias"] = coincidencias
            E["_productos_catalogo_revision"] = list(dict.fromkeys(
                _texto_seguro(fila.get("descripcion")) for fila in filas
                if not coincidencias.get(ct.normalizar(fila.get("descripcion")), {}).get("clave")
            ))
            precios = []
            for fila in filas:
                match = coincidencias.get(ct.normalizar(fila["descripcion"]), {})
                clave = match.get("clave")
                neto = bib.precio_sugerido(clave) or bib.indice.get(clave, {}).get("precio_neto")
                precios.append(ct.precio_para_mostrar(neto, con_iva) if neto else None)
            origen_por_fuente = {
                FUENTE_TEXTO: "Texto ingresado",
                FUENTE_TABLA: "Planilla del pedido",
                FUENTE_DOCUMENTO: "Extracción de PDF o imagen",
                FUENTE_MERCADO_PUBLICO: "Mercado Público",
            }
            E["orden_df"] = pd.DataFrame({
                "Incluir": [True] * len(filas),
                "Cantidad": [fila["cantidad"] for fila in filas],
                "Producto": [fila["descripcion"] for fila in filas],
                "Código": [fila.get("codigo", "") for fila in filas],
                "Unidad": [fila.get("unidad", "") for fila in filas],
                "Origen": [fila.get("origen") or origen_por_fuente[fuente] for fila in filas],
                "Precio unitario": precios,
            })
            E["orden_df_base"] = E["orden_df"].copy()
            E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
            E["editor_version"] = int(E.get("editor_version", 0)) + 1
            if coincidencias:
                st.success(f"Encontré {len(coincidencias)} producto(s) en tu biblioteca y usé sus precios guardados.")
            if E["_productos_catalogo_revision"]:
                st.info(
                    f"{len(E['_productos_catalogo_revision'])} producto(s) quedaron pendientes de asociación. "
                    "Podrás vincularlos al catálogo en el paso de revisión para recuperar sus fotos y precios."
                )
            if E.get("_error_match_ia"):
                st.info("No pude consultar la IA esta vez; puedes continuar y revisar los productos sin coincidencia.")
            if fuente == FUENTE_MERCADO_PUBLICO and not E.get("_mercado_publico_detalle", {}).get("productos"):
                st.warning("La plataforma no entregó productos en el listado normal. Revisa los adjuntos de arriba: puedes descargar los archivos del portal y cargarlos aquí para extraer y agregar sus productos.")

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
        sigue_cotizacion.button("Siguiente", disabled=True, key="siguiente_sin_pedido", width="stretch")
        st.info("Escribe o carga un pedido para comenzar. Tus datos de cliente y el número se guardan en el borrador.")
        persistir_borrador()
        return

    st.divider()
    st.subheader("Productos y precios")
    st.caption("Puedes cambiar cantidades, nombres y precios, agregar filas y dejar precios pendientes. Los totales se actualizan antes de avanzar.")
    if fuente == FUENTE_MERCADO_PUBLICO and E.get("_mercado_publico_detalle"):
        st.info("Las líneas del listado y de cada adjunto se mantienen separadas y muestran su origen. Si describen el mismo producto con datos distintos, contrástalas y edita la versión que corresponda antes de continuar.")
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
            "Código": st.column_config.TextColumn("Código", width="small"),
            "Unidad": st.column_config.TextColumn("Unidad", width="small"),
            "Origen": st.column_config.TextColumn("Origen", width="medium"),
            "Precio unitario": st.column_config.NumberColumn(min_value=0, format="$ %d"),
            **columnas_editor_entrega(),
        },
        disabled=["Origen"],
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
    if fuente in (FUENTE_TABLA, FUENTE_DOCUMENTO, FUENTE_MERCADO_PUBLICO):
        firma_revision = sha256(orden.to_json(orient="split", default_handler=str).encode()).hexdigest()[:16]
        revisado = st.checkbox(
            "Revisé los productos, cantidades y fuentes importadas",
            key=f"verificar_importacion_{firma_revision}",
        )
        continuar_igual = continuar_igual and revisado
    if sigue_cotizacion.button("Guardar pedido y continuar →", type="primary", width="stretch",
                 disabled=not any(ln.get("incluir", True) for ln in lineas_orden) or not continuar_igual):
        cliente = cliente_actual()
        totales = rv.totales_de([
            {"cantidad": ln["cantidad"], "precio_neto": ln.get("unitario")}
            for ln in lineas_orden if ln.get("unitario") is not None
        ])
        cot_id = E.get("cot_id")
        if not cot_id:
            cot_id, numero = hist.crear(cliente, lineas_orden, totales, con_iva, cliente["fecha"], origen="pedido")
            codigo_mp = _texto_seguro(E.get("_mercado_publico_codigo") or E.get("mercado_publico_codigo"))
            E["cot_id"] = cot_id
            E["cot_numero"] = codigo_mp or numero
            registro_nuevo = hist.obtener(cot_id)
            E["_cotizacion_version"] = registro_nuevo.get("actualizado") if registro_nuevo else None
        else:
            registro = hist.obtener(cot_id)
            if registro and _texto_seguro(E.get("cot_numero")) != registro["numero"]:
                try:
                    if GIOVAL_REQUIRE_ACCESS:
                        hist.renumerar(
                            cot_id,
                            _texto_seguro(E.get("cot_numero")),
                            version_esperada=E.get("_cotizacion_version"),
                        )
                    else:
                        hist.renumerar(cot_id, _texto_seguro(E.get("cot_numero")))
                    registro_renumerado = hist.obtener(cot_id)
                    if registro_renumerado:
                        E["_cotizacion_version"] = registro_renumerado.get("actualizado")
                except hs.ConflictoActualizacion as exc:
                    E["_conflicto_guardado"] = True
                    E["_conflicto_guardado_id"] = cot_id
                    E["_conflicto_guardado_mensaje"] = str(exc)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                    return
        registro = hist.obtener(E["cot_id"])
        if not registro:
            numero_actual = _texto_seguro(E.get("cot_numero")).casefold()
            firma_actual = _firma_lineas_borrador(lineas_orden)
            if numero_actual and firma_actual:
                for candidato_resumen in hist.listar():
                    if _texto_seguro(candidato_resumen.get("numero")).casefold() != numero_actual:
                        continue
                    candidato = hist.obtener(candidato_resumen.get("id"))
                    if not candidato:
                        continue
                    if candidato.get("estado") != "Borrador":
                        break
                    firma_guardada = _firma_lineas_borrador(
                        candidato.get("datos", {}).get("lineas", [])
                    )
                    version_actual = E.get("_cotizacion_version")
                    version_coincide = (
                        not GIOVAL_REQUIRE_ACCESS
                        or (version_actual and version_actual == candidato.get("actualizado"))
                    )
                    if firma_actual == firma_guardada and version_coincide:
                        E["cot_id"] = int(candidato["id"])
                        E["_cotizacion_version"] = candidato.get("actualizado")
                        registro = candidato
                        st.info("Reconecté esta sesión con el borrador guardado porque coinciden su número y sus productos.")
                    break
        if not registro:
            st.error("No encontré el borrador asociado a esta sesión. Tus datos siguen en esta pantalla.")
            st.info("Puedes guardarlos como una cotización nueva; el borrador anterior no se modificará.")
            st.button(
                "Guardar mis cambios como una cotización nueva",
                icon=":material/content_copy:",
                type="primary",
                width="stretch",
                on_click=guardar_conflicto_como_nueva,
                key="pedido_recuperar_borrador_como_nuevo",
            )
            return
        ruta = Path(tempfile.mkdtemp()) / (
            f"Cotizacion_{re.sub(r'[^A-Za-z0-9._-]', '_', registro['numero'])}_"
            f"{re.sub(r'[^A-Za-z0-9]+', '_', nombre).strip('_')[:30] or 'cliente'}.xlsx"
        )
        try:
            ct.crear_cotizacion_xlsx(
                [{"cantidad": ln["cantidad"], "descripcion": flujo.descripcion_oferta(ln), "precio_neto": ln.get("unitario"),
                  "codigo": ln.get("codigo_fuente"), "unidad": ln.get("unidad_fuente")}
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
                "anexos": anexos_doc.limpiar_referencias(E.get("anexos_cotizacion")),
                "origen_pedido": "pedido", "paso": 2,
                "fuente_pedido": E.get("fuente_pedido", E.get("fuente_orden", "")),
                "fuente_orden": E.get("fuente_orden", ""),
                "orden_texto": E.get("orden_texto", ""),
                "mercado_publico": datos_mercado_publico_guardables(),
            }
            if GIOVAL_REQUIRE_ACCESS or GIOVAL_PROFILES:
                hist.actualizar_si_version(
                    E["cot_id"], E.get("_cotizacion_version"),
                    datos=datos_cotizacion, **campos_cotizacion
                )
            else:
                hist.actualizar(E["cot_id"], datos=datos_cotizacion, **campos_cotizacion)
            registro_nuevo = hist.obtener(E["cot_id"])
            if registro_nuevo:
                E["_cotizacion_version"] = registro_nuevo.get("actualizado")
            archivar_cotizacion(E["cot_id"], xlsx=ruta)
            hist.guardar_cliente(cliente)
        except hs.ConflictoActualizacion as exc:
            E["_conflicto_guardado"] = True
            E["_conflicto_guardado_id"] = E.get("cot_id")
            E["_conflicto_guardado_mensaje"] = str(exc)
            st.rerun()
        except Exception as exc:
            st.error(f"No pude guardar el borrador. Los datos siguen en pantalla: {exc}")
            return
        registro_guardado = hist.obtener(E["cot_id"])
        ruta_guardada = registro_guardado.get("xlsx") if registro_guardado else None
        # El modo compartido no archiva Excel con datos personales: usa el temporal de esta sesión.
        E["ruta"] = Path(ruta_guardada) if ruta_guardada and Path(ruta_guardada).exists() else ruta
        cargar(E["ruta"], "pedido", lineas_pedido=lineas_orden)
        ir_a(2)
    else:
        persistir_borrador()


def cotizacion_ui():
    st.write("Si ya tienes la cotización armada en Excel, súbela y yo le agrego las fotos y fichas.")
    archivo = st.file_uploader("Sube la cotización en Excel", type=["xlsx"], key="cot_archivo")
    if sigue_cotizacion.button("Continuar →", type="primary", width="stretch", key="cot_continuar", disabled=not archivo):
        ruta = Path(tempfile.mkdtemp()) / archivo.name
        ruta.write_bytes(archivo.getvalue())
        try:
            cargar(ruta, "cotizacion")
        except ValueError as e:
            st.error(str(e))
            return
        ir_a(2)


def paso_1():
    visual.titulo("Pedido", "Elige cómo cargar los productos de la cotización.", "Cotización", "")
    atras_cotizacion.button("Volver", key="inicio_desde_pedido", width="stretch", on_click=limpiar_estado_trabajo)
    if E.get("fuente_pedido") not in ETIQUETAS_FUENTES:
        E["fuente_pedido"] = FUENTE_TEXTO
    fuente = st.segmented_control(
        "Origen del pedido",
        list(ETIQUETAS_FUENTES),
        key="fuente_pedido",
        format_func=ETIQUETAS_FUENTES.__getitem__,
        required=True,
        width="stretch",
        wrap=True,
        persist_state="session",
    )
    if fuente == FUENTE_COTIZACION:
        cotizacion_ui()
    else:
        pedido_ui(fuente)


def agregar_desde_catalogo():
    """Permite buscar un producto conocido e incorporarlo a la cotización actual."""
    catalogo = st.expander("Agregar desde la biblioteca", icon=":material/library_add:",
                           key="agregar_desde_catalogo", on_change="rerun")
    if not catalogo.open:
        return
    with catalogo:
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
        precio_ui = montos.entrada(
            "Precio unitario", _container=c2, min_value=0.0, step=100.0,
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
                **flujo.datos_entrega(bib.indice[producto["clave"]].get("estado_stock"), None),
            }
            aplicar_lineas_editor([*E.get("lineas", []), linea])
            refrescar_excel_actual()
            st.rerun()


# ============================================================== PASO 2: PRODUCTOS
def revisar_asociaciones_catalogo(pendientes: list[str]):
    """Permite vincular manualmente las líneas que no tuvieron una coincidencia suficiente."""
    E["_productos_catalogo_revision"] = pendientes
    if not pendientes:
        return

    productos = {
        f"{clave} · {_texto_seguro(ficha.get('nombre') or clave)}": clave
        for clave, ficha in sorted(bib.indice.items())
        if not ct.identidad_conflictiva(clave, ficha)
    }
    if not productos:
        st.caption("Agrega productos a la biblioteca para poder vincularlos a esta cotización.")
        return
    opciones = ["Dejar pendiente"] + list(productos)
    panel = st.expander(
        f"Revisar coincidencias ({len(pendientes)})", icon=":material/link:",
        key="revisar_coincidencias", on_change="rerun",
    )
    if not panel.open:
        return
    with panel:
        st.caption("Elige el producto que corresponde a cada descripción del pedido.")
        for indice, descripcion in enumerate(pendientes):
            normalizada = ct.normalizar(descripcion)
            c_desc, c_producto, c_accion = st.columns([2.2, 3, 1])
            c_desc.caption(descripcion)
            elegido = c_producto.selectbox(
                "Producto guardado", opciones,
                key=f"catalogo_vinculo_{E.get('cot_id', 'actual')}_{indice}_{normalizada[:20]}",
                label_visibility="collapsed",
            )
            if c_accion.button(
                "Vincular", key=f"catalogo_vincular_{E.get('cot_id', 'actual')}_{indice}_{normalizada[:20]}",
                disabled=elegido == opciones[0], width="stretch",
            ):
                clave = productos[elegido]
                ficha = bib.indice.get(clave, {})
                precio = bib.precio_sugerido(clave)
                if precio is None:
                    precio = ficha.get("precio_neto")
                for linea in E.get("lineas", []):
                    if ct.normalizar(linea.get("descripcion")) != normalizada:
                        continue
                    linea["clave_auto"] = clave
                    linea["origen"] = "biblioteca"
                    if precio is not None:
                        linea["unitario"] = precio
                        linea["precio_neto"] = precio
                E.setdefault("_orden_coincidencias", {})[normalizada] = {
                    "clave": clave, "origen": "biblioteca", "certeza": "alta"
                }
                if isinstance(E.get("orden_df"), pd.DataFrame):
                    con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
                    for fila_idx, fila in E["orden_df"].iterrows():
                        if ct.normalizar(fila.get("Producto")) == normalizada and precio is not None:
                            E["orden_df"].at[fila_idx, "Precio unitario"] = ct.precio_para_mostrar(precio, con_iva)
                    E["orden_df_base"] = E["orden_df"].copy()
                    E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
                E["tabla"] = tabla_desde_lineas(E.get("lineas", []))
                E["tabla_edit"] = E["tabla"].copy()
                E["tabla_base"] = E["tabla"].copy()
                E["editor_version"] = int(E.get("editor_version", 0)) + 1
                E["editado_manual"] = True
                for clave_estado in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
                    E.pop(clave_estado, None)
                persistir_borrador()
                st.rerun()


def registrar_deshacer(anteriores, nuevas):
    if E.get("paso") == 2 and all(edicion.ID in linea for linea in anteriores):
        cambios = E.get("_deshacer_lineas", [])
        E["_deshacer_lineas"] = [*cambios, *edicion.cambios(anteriores, nuevas)][-50:]


def aplicar_lineas_editor(lineas, *, registrar=True):
    lineas = opciones_cot.normalizar_alternativas(edicion.identificar(lineas))
    if registrar:
        registrar_deshacer(E.get("lineas", []), lineas)
    tabla = tabla_desde_lineas(lineas)
    E.update(
        lineas=lineas, tabla=tabla, tabla_edit=tabla.copy(), tabla_base=tabla.copy(),
        editor_version=int(E.get("editor_version", 0)) + 1, editado_manual=True,
    )
    for clave in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo", "pdf_confirmado"):
        E.pop(clave, None)
    persistir_borrador()


def mostrar_deshacer_lineas():
    historial = E.get("_deshacer_lineas", [])
    por_id = {linea[edicion.ID]: linea for linea in E["lineas"]}
    disponibles = {}
    for indice, cambio in reversed(list(enumerate(historial))):
        if cambio["id"] not in disponibles and edicion.debe_deshacerse(cambio, por_id):
            disponibles[cambio["id"]] = indice
    if not disponibles or E.get("_conflicto_guardado"):
        return
    with st.container(key="panel_deshacer_lineas"):
        panel = st.popover("Deshacer cambios por línea", icon=":material/undo:")
    with panel:
        for indice in disponibles.values():
            cambio = historial[indice]
            anterior, posterior = cambio["antes"], cambio["despues"]
            nombre = (posterior or anterior)["descripcion"]
            accion = "Recuperar" if posterior is None else "Quitar línea agregada" if anterior is None else "Revertir edición"
            if st.button(f"{accion} · {nombre}", key=f"deshacer_linea_{indice}", width="stretch"):
                E["_deshacer_lineas"] = historial[:indice] + historial[indice + 1:]
                aplicar_lineas_editor(edicion.deshacer(E["lineas"], cambio), registrar=False)
                st.rerun()


def confirmar_linea_editor(indice, clave_widget, total, avanzar):
    E["_acuse_linea"] = E.get("_acuse_linea", 0) + 1
    E["_estado_acuse_linea"] = "error"
    try:
        precio = montos.leer(E[clave_widget])
        if precio is not None and precio < 0:
            raise ValueError("El precio debe ser mayor o igual a cero.")
    except ValueError as exc:
        E["_error_precio_linea"] = str(exc)
        return
    descripcion = ct.limpiar_descripcion(E[f"{clave_widget}_descripcion"])
    cantidad = E[f"{clave_widget}_cantidad"]
    if not descripcion or not cantidad or cantidad <= 0:
        E["_error_precio_linea"] = "Completa el producto y una cantidad mayor que cero."
        return
    codigo = E[f"{clave_widget}_codigo"].strip().upper()
    es_producto = not any(p in ct.normalizar(descripcion).lower() for p in ct.PALABRAS_NO_PRODUCTO)
    cambios = {
        **flujo.datos_entrega(E[f"{clave_widget}_stock"], E[f"{clave_widget}_plazo"]),
        "descripcion": descripcion, "cantidad": ct._cantidad(cantidad),
        "clave_auto": codigo or (ct.clave_descriptiva(descripcion) if es_producto else ""),
        "es_producto": es_producto, "incluir": E[f"{clave_widget}_incluir"],
    }
    E["_linea_pendiente"] = (indice, precio, cambios)
    if avanzar:
        E["linea_editor"] = (indice + 1) % total


def descartar_linea_editor():
    E["_acuse_linea"] = E.get("_acuse_linea", 0) + 1
    E["_revision_campos_linea"] = E.get("_revision_campos_linea", 0) + 1
    E["_estado_acuse_linea"] = "aplicado"


def detalle_linea_editor():
    lineas = E.get("lineas", [])
    if not lineas:
        return
    indices = range(len(lineas))
    if E.get("linea_editor") not in indices:
        E["linea_editor"] = 0
    indice = E["linea_editor"]
    linea = lineas[indice]
    st.html('<span id="giovalia-detalle-linea" tabindex="-1"></span>')
    st.subheader(f"{indice + 1}. {linea['descripcion']}")
    con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
    previo = linea.get("unitario")
    mostrado = None if previo is None else ct.precio_para_mostrar(previo, con_iva)
    entrega = flujo.datos_entrega(linea.get("disponibilidad"), linea.get("plazo_dias"))
    estado, dias = entrega["disponibilidad"], entrega["plazo_dias"]
    acuse = E.get("_acuse_linea", 0)
    revision = E.get("_revision_campos_linea", 0)
    clave_widget = f"precio_linea_{E['cot_id']}_{E.get('editor_version', 0)}_{indice}_{mostrado}_{estado}_{dias}_{revision}"
    experiencia.ayuda("Cómo ingresar precio y entrega",
                     "Si elegiste precios netos, escribe el monto sin IVA. "
                     "En stock significa disponible. A pedido indica que hay que reponer. "
                     "En plazo, 0 indica entrega inmediata. Si el plazo está vacío, queda por confirmar salvo que haya stock.")
    with st.form("form_precio_linea", border=False):
        st.text_input("Producto de la línea seleccionada", value=linea["descripcion"], key=f"{clave_widget}_descripcion")
        codigo, cantidad = st.columns(2)
        codigo.text_input("Código de la línea seleccionada", value=linea.get("clave_auto") or "", key=f"{clave_widget}_codigo")
        cantidad.number_input("Cantidad de la línea seleccionada", min_value=0.0, step=1.0,
                              value=float(linea["cantidad"]), key=f"{clave_widget}_cantidad")
        st.checkbox("Incluir en la cotización", value=linea.get("incluir", True), key=f"{clave_widget}_incluir")
        st.text_input("Precio de la línea seleccionada", value=montos.clp(mostrado), key=clave_widget,
                      placeholder="Monto en pesos")
        stock, plazo = st.columns(2)
        stock.selectbox(flujo.COL_STOCK, flujo.DISPONIBILIDADES, index=flujo.DISPONIBILIDADES.index(estado),
                        key=f"{clave_widget}_stock")
        plazo.number_input(flujo.COL_PLAZO, min_value=0, step=1, value=dias, key=f"{clave_widget}_plazo")
        if aviso := E.pop("_error_precio_linea", None):
            st.error(aviso)
        aplicar, avanzar = st.columns(2)
        aplicar.form_submit_button("Guardar línea", key="aplicar_precio", on_click=confirmar_linea_editor,
                                   args=(indice, clave_widget, len(lineas), False))
        avanzar.form_submit_button("Siguiente precio", key="siguiente_precio", on_click=confirmar_linea_editor,
                                   args=(indice, clave_widget, len(lineas), True), disabled=len(lineas) < 2)
        st.form_submit_button("Descartar edición", key="descartar_linea", on_click=descartar_linea_editor)
        st.html(
            f'<p id="giovalia-linea-pendiente" role="status" hidden data-acuse="{acuse}" '
            f'data-estado="{E.get("_estado_acuse_linea", "")}" data-precio="{html.escape(montos.clp(mostrado))}" '
            f'data-descripcion="{html.escape(linea["descripcion"])}" data-codigo="{html.escape(linea.get("clave_auto") or "")}" '
            f'data-cantidad="{linea["cantidad"]}" data-incluir="{int(linea.get("incluir", True))}" '
            f'data-stock="{html.escape(estado)}" data-plazo="{"" if dias is None else dias}">'
            'Hay cambios en esta línea por guardar.</p>'
        )
    clave = linea.get("clave_auto")
    if clave and linea.get("es_producto", True):
        clave_catalogo = bib.clave_vigente(clave)
        if clave_catalogo:
            confirmacion = stock_catalogo.datos_stock(bib.indice[clave_catalogo])
            fecha = confirmacion["stock_confirmado_el"]
            if fecha:
                fuente = confirmacion["stock_fuente"]
                rotulo = f"Stock de biblioteca · {confirmacion['estado_stock']} · {fecha:%d/%m/%Y}"
                st.caption(f"{rotulo} · {fuente}" if fuente else rotulo)
            else:
                st.caption("Stock de biblioteca sin fecha de confirmación.")
        tarjeta(clave, linea["descripcion"], *estado_anexos(clave, True), de_cotizacion=True)


def mostrar_tabla_lineas(compacta):
    lineas = E["lineas"]
    if not lineas:
        st.info("Agrega un producto para editar su precio y entrega.")
        return
    activa = E.get("linea_editor", 0)
    if activa not in range(len(lineas)):
        activa = 0
    con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
    filas = [
        [ln[edicion.ID], ln["descripcion"], ln.get("clave_auto") or "—", str(ln["cantidad"]),
         montos.clp(ct.precio_para_mostrar(ln["unitario"], con_iva)) if ln.get("unitario") is not None else "Sin precio",
         f"{ln.get('disponibilidad') or flujo.POR_CONFIRMAR} · {flujo.texto_plazo(flujo.plazo_efectivo(ln))}"
         if ln.get("incluir", True) else "Alternativa" if ln.get(opciones_cot.GRUPO) else "No incluido"]
        for ln in lineas
    ]
    elegida = interfaz.lista(
        "Productos de la cotización", ("Producto", "Código", "Cantidad", "Precio unitario", "Entrega"),
        filas, key=f"tabla_lineas_{E['cot_id']}", activa=lineas[activa][edicion.ID], compacta=compacta,
        montos=(2, 3), fijar=True, proteger=True,
    )
    elegida = next((i for i, ln in enumerate(lineas) if ln[edicion.ID] == elegida), None)
    if elegida is not None and elegida != activa:
        E["linea_editor"] = elegida
        E["_destino_visual"] = "linea"
        st.rerun()


def mostrar_entregas(lineas):
    incluidos = [ln for ln in lineas if ln.get("incluir", True)]
    grupos = {}
    for ln in incluidos:
        grupos.setdefault(flujo.plazo_efectivo(ln), []).append(ln["descripcion"])
    if len(grupos) > 1:
        with st.expander("Ver propuesta de entregas parciales"):
            for dias, productos in sorted(grupos.items(), key=lambda grupo: (grupo[0] is None, grupo[0])):
                st.markdown(f"**{flujo.texto_plazo(dias)}**")
                st.write(" · ".join(productos))


def editar_alternativas():
    lineas = E["lineas"]
    candidatas = {ln[edicion.ID]: f"{i}. {ln['descripcion']}" for i, ln in enumerate(lineas, 1)
                  if ln.get("es_producto", True) and not ln.get(opciones_cot.GRUPO)}
    grupos = opciones_cot.grupos(lineas)
    if len(candidatas) < 2 and not grupos:
        return
    with st.expander("Alternativas de productos"):
        st.caption("En cada grupo se suma solo la opción seleccionada. El PDF incluye el detalle de las alternativas.")
        if len(candidatas) >= 2:
            seleccion = st.multiselect("Líneas del nuevo grupo", candidatas, format_func=candidatas.__getitem__,
                                       key=f"nuevas_opciones_{E['cot_id']}_{E.get('editor_version', 0)}")
            if st.button("Agrupar como alternativas", disabled=len(seleccion) < 2):
                grupo = uuid4().hex
                activa = next((ln[edicion.ID] for ln in lineas
                              if ln[edicion.ID] in seleccion and ln.get("incluir", True)), seleccion[0])
                aplicar_lineas_editor([
                    {**ln, opciones_cot.GRUPO: grupo, "incluir": ln[edicion.ID] == activa}
                    if ln[edicion.ID] in seleccion else ln for ln in lineas
                ])
                st.rerun()
        for numero, (grupo, opciones) in enumerate(grupos.items(), 1):
            nombres = {ln[edicion.ID]: f"{ln['descripcion']} · {i}" for i, ln in enumerate(opciones, 1)}
            actual = next((ln[edicion.ID] for ln in opciones if ln.get("incluir", True)), None)
            elegida = st.selectbox(f"Opción que suma al total · Grupo {numero}", nombres,
                                  index=list(nombres).index(actual) if actual else None,
                                  format_func=nombres.__getitem__, key=f"alternativa_{grupo}_{actual}")
            if elegida != actual:
                aplicar_lineas_editor([
                    {**ln, "incluir": ln[edicion.ID] == elegida} if ln.get(opciones_cot.GRUPO) == grupo else ln
                    for ln in lineas
                ])
                st.rerun()
            if st.button(f"Desagrupar opciones {numero}", key=f"desagrupar_{grupo}"):
                aplicar_lineas_editor([
                    {k: v for k, v in ln.items() if k != opciones_cot.GRUPO}
                    if ln.get(opciones_cot.GRUPO) == grupo else ln for ln in lineas
                ])
                st.rerun()


def mostrar_faltantes_cotizacion():
    with st.expander("Stock y faltantes de la cotización", icon=":material/inventory:"):
        st.caption("Suma las cantidades repetidas por código y usa la última confirmación de la biblioteca.")
        faltantes = opciones_cot.faltantes(E["lineas"], bib)
        if faltantes:
            st.dataframe(faltantes, hide_index=True, width="stretch")
            st.caption("Las cantidades vacías requieren confirmación en la biblioteca.")
        else:
            st.caption("La biblioteca cubre las cantidades de los productos incluidos.")


def paso_2():
    visual.titulo("Editar cotización", "Edita las líneas que irán en la propuesta.", "Cotización", "")
    st.caption("Ctrl+K buscar · Alt+N agregar línea y Enter · Alt+P siguiente precio · Ctrl+S guardar")
    trabajo = st.container()
    resumen = st.container(key="totales_editor_cotizacion")
    tabla, detalle = trabajo.columns([2.5, 1.3], border=True)
    with tabla:
        if codigo := opciones_ui.buscar_impresora(bib, "cotizacion"):
            ficha = bib.indice[codigo]
            precio = ficha.get("precio_neto")
            stock = stock_catalogo.datos_stock(ficha)
            disponibilidad = stock["estado_stock"] if stock["stock_confirmado_el"] else flujo.POR_CONFIRMAR
            aplicar_lineas_editor([*E["lineas"], {
                "descripcion": ficha.get("nombre") or codigo, "cantidad": 1, "clave_auto": codigo,
                "es_producto": True, "incluir": True, "origen": "descripcion",
                "precio_neto": precio, "unitario": precio,
                **flujo.datos_entrega(disponibilidad, ficha.get("reposicion_dias")
                                      if disponibilidad == flujo.A_PEDIDO else None),
            }])
            st.rerun()
        nueva = st.text_input("Nueva línea", key="linea_nueva", placeholder="Descripción del producto y Enter")
        densidad = st.segmented_control("Vista de la tabla", ["Cómoda", "Compacta"],
                                       default=E.get("pref_densidad", "Cómoda"), key="densidad_editor",
                                       required=True, label_visibility="collapsed")
        E["pref_densidad"] = densidad
        with st.expander("Editar la tabla completa"):
            editor_productos(compacto=densidad == "Compacta")
        if cambio := E.pop("_linea_pendiente", None):
            indice, precio, campos = cambio
            lineas = list(E["lineas"])
            if indice < len(lineas):
                con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
                actual = lineas[indice].get("unitario")
                mostrado = None if actual is None else ct.precio_para_mostrar(actual, con_iva)
                if precio != mostrado or any(lineas[indice].get(k) != v for k, v in campos.items()):
                    neto = actual if precio == mostrado else None if precio is None else ct.neto_desde(precio, con_iva)
                    if campos["clave_auto"] != lineas[indice].get("clave_auto"):
                        campos["origen"] = "descripcion"
                    lineas[indice] = {**lineas[indice], **campos, "unitario": neto, "precio_neto": neto}
                    aplicar_lineas_editor(lineas)
                if not E.get("_conflicto_guardado"):
                    E["_estado_acuse_linea"] = "aplicado"
                    E["_revision_campos_linea"] = E.get("_revision_campos_linea", 0) + 1
                    st.toast("Borrador guardado")
        descripcion = ct.limpiar_descripcion(nueva)
        if descripcion:
            clave = bib.detectar(descripcion) or ct.clave_descriptiva(descripcion)
            nuevas = [*E.get("lineas", []), {
                "descripcion": descripcion, "cantidad": 1, "clave_auto": clave,
                "es_producto": True, "incluir": True, "origen": "descripcion",
                **flujo.datos_entrega(bib.indice.get(clave, {}).get("estado_stock"), None),
            }]
            E["_limpiar_linea_nueva"] = True
            aplicar_lineas_editor(nuevas)
            E.pop("_rebasar_lineas", None)
            st.rerun()
        if E.pop("_rebasar_lineas", False):
            aplicar_lineas_editor(E["lineas"], registrar=False)
            st.rerun()
        mostrar_tabla_lineas(densidad == "Compacta")
        estado = st.container(horizontal=True)
        mostrar_deshacer_lineas()
    with detalle, st.expander("Detalle de la línea", expanded=True):
        detalle_linea_editor()
    with resumen:
        mostrar_totales()
    lineas = [ln for ln in E.get("lineas", []) if ln.get("es_producto", True)]
    editar_alternativas()
    mostrar_faltantes_cotizacion()
    mostrar_entregas(lineas)
    sin_vinculo = productos_pendientes_de_asociar(lineas)
    with estado:
        if sin_vinculo:
            etiqueta = "producto sin vínculo" if len(sin_vinculo) == 1 else "productos sin vínculo"
            st.badge(f"{len(sin_vinculo)} {etiqueta}", icon=":material/link_off:", color="orange")
        elif lineas:
            st.badge("Productos vinculados", icon=":material/link:", color="green")
    boton_actualizar_totales()
    with st.expander("Actualizar precios de la biblioteca"):
        st.caption("Si confirmas, los precios de las líneas incluidas quedarán disponibles para futuras cotizaciones.")
        if st.button("Guardar precios en la biblioteca", key="confirmar_precios_biblioteca"):
            guardar_precios_inventario(E["lineas"])
            st.success("Precios guardados en la biblioteca.")
    gestion.margen_pedido(E.get("lineas", []), bib, hist, E.get("cot_id"))
    agregar_desde_catalogo()
    revisar_asociaciones_catalogo(sin_vinculo)
    mostrar_resumen_lineas()
    editar_cabecera()
    panel_revision()

    if atras_cotizacion.button("← Volver", key="editor_volver_pedido", width="stretch"):
        E.pop("_deshacer_lineas", None)
        con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
        E["orden_df"] = pd.DataFrame({
            "Incluir": [_esta_marcado(ln.get("incluir"), True) for ln in E.get("lineas", [])],
            "Cantidad": [ln.get("cantidad", 1) for ln in E.get("lineas", [])],
            "Producto": [ln.get("descripcion", "") for ln in E.get("lineas", [])],
            "Precio unitario": [ct.precio_para_mostrar(
                ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"), con_iva)
                if (ln.get("unitario") is not None or ln.get("precio_neto") is not None) else None
                for ln in E.get("lineas", [])],
            "Código": [ln.get("clave_auto", "") for ln in E.get("lineas", [])],
            **flujo.columnas_entrega(E.get("lineas", [])),
        })
        E["orden_df_base"] = E["orden_df"].copy()
        E["_orden_coincidencias"] = referencias_lineas_guardadas(E["lineas"])
        E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
        ir_a(1)
    if sigue_cotizacion.button("Siguiente: fotos y fichas →", key="editor_ir_fotos", type="primary", width="stretch"):
        claves, _ = datos_productos()
        if not claves:
            st.error("Marca al menos un producto en la columna **Incluir** para buscar sus fotos y fichas.")
            return
        persistir_borrador()
        ir_a(3)


# ============================================================== PASO 3: FOTOS Y FICHAS
def revisar_ficha_producto(clave, ficha, opcional=False):
    datos = bib.indice.get(clave, {})
    codigos = (clave, datos.get("modelo"), datos.get("mpn"), *ct._alias_de_ficha(datos))
    return fichas_doc.problema_ficha(ficha, [str(codigo) for codigo in codigos if codigo], opcional=opcional)


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


@st.cache_data(ttl=900, max_entries=128, show_spinner=False)
def _buscar_fotos_cacheado(consulta: str, clave: str):
    diag = {}
    return bs.buscar_fotos(consulta, clave=clave, diag=diag), diag


@st.cache_data(ttl=900, max_entries=128, show_spinner=False)
def _buscar_fichas_cacheado(consulta: str, clave: str):
    diag = {}
    return bs.buscar_fichas(consulta, clave=clave, diag=diag), diag


def buscar_para(clave: str, q_foto: str, q_ficha: str, fotos=True, fichas=True) -> list[str]:
    avisos = []
    for activo, tipo, funcion, consulta in (
        (fotos, "foto", _buscar_fotos_cacheado, q_foto),
        (fichas, "ficha", _buscar_fichas_cacheado, q_ficha),
    ):
        if activo:
            try:
                resultados, diag = funcion(consulta, clave)
                E[f"cand_{tipo}_{clave}"] = resultados
            except bs.ErrorBusqueda:
                diag = {}
                avisos.append("El buscador no respondió. Intenta de nuevo en un minuto.")
            E[f"diag_{tipo}_{clave}"] = diag
    return avisos


def cambiar_anexo(clave, campo, valor):
    E.setdefault("anexos_cotizacion", {}).setdefault(clave, {})[campo] = valor
    for llave in ("pdf_bytes", "xlsx_vista_previa", "informe", "pdf_confirmado"):
        E.pop(llave, None)
    persistir_borrador()


def aplicar_anexo(clave, tipo, nombre, contenido, de_cotizacion):
    if de_cotizacion:
        anterior = E.get("anexos_cotizacion", {}).get(clave, {}).get(tipo)
        referencia = anexos_doc.guardar(bib, tipo, contenido)
        cambiar_anexo(clave, tipo, referencia)
        if anterior != referencia:
            hist.registrar_cambio("Cotización", E.get("cot_numero"), f"{tipo} · {clave}", anterior, referencia)
    else:
        bib.guardar_archivo(clave, tipo, nombre, contenido)


def guardar_candidato(clave: str, tipo: str, c: dict, de_cotizacion: bool):
    try:
        aplicar_anexo(clave, tipo, "x" + c.get("ext", "") if tipo == "foto" else "f.pdf",
                      c["datos"], de_cotizacion)
    except (OSError, ValueError, PdfReadError) as error:
        st.error(str(error))
        return
    E.pop(f"cand_{tipo}_{clave}", None)
    st.rerun()


@acceso.proteger
def guardar_anexo_subido(clave: str, tipo: str, de_cotizacion: bool):
    llave = f"{tipo}_{clave}"
    E.pop(f"error_{llave}", None)
    subido = E.get(llave)
    if subido:
        try:
            aplicar_anexo(clave, tipo, subido.name, subido.getvalue(), de_cotizacion)
        except (OSError, ValueError, PdfReadError) as exc:
            E[f"error_{llave}"] = f"No pude guardar {subido.name}. {exc}"


def estado_anexos(clave: str, de_cotizacion: bool = False):
    seleccion = E.get("anexos_cotizacion", {}).get(clave, {}) if de_cotizacion else {}
    return anexos_doc.resolver(bib, clave, seleccion)


def tarjeta(clave: str, desc: str, foto, ficha, ficha_no_requerida: bool, *, de_cotizacion=False):
    problema_ficha = revisar_ficha_producto(clave, ficha, ficha_no_requerida)
    estado_ficha = problema_ficha or ("Disponible" if ficha else "No requerida")
    aviso_ficha = {
        fichas_doc.ILEGIBLE: "Este PDF no se pudo leer. Puedes reemplazarlo en Más opciones.",
        fichas_doc.SIN_TEXTO: "Este PDF no tiene texto verificable. Abre la ficha para revisar el código.",
        fichas_doc.SIN_CODIGO: "El texto del PDF no contiene el código, modelo ni alias del producto.",
    }.get(problema_ficha, "")
    with st.container(border=True):
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
            st.subheader(desc)
            st.caption(f"Código: {clave}")
            with st.container(horizontal=True):
                st.badge(
                    "Foto disponible" if foto else "Falta foto",
                    icon=":material/image:" if foto else ":material/image_not_supported:",
                    color="green" if foto else "orange",
                )
                st.badge(
                    f"Ficha: {estado_ficha}",
                    icon=":material/description:",
                    color="green" if estado_ficha == "Disponible" else (
                        "gray" if estado_ficha == "No requerida" else "orange"
                    ),
                )
            for tipo, anexo in (("foto", foto), ("ficha", ficha)):
                if anexo and st.button(f"Ampliar {tipo}", key=f"ampliar_{tipo}_{clave}", icon=":material/zoom_in:"):
                    experiencia.mostrar_anexo(anexo, f"{desc} · {clave}", tipo)
                if aviso := E.get(f"error_{tipo}_{clave}"):
                    st.error(aviso)
            nueva_no_requerida = st.checkbox(
                "Este producto no requiere ficha técnica",
                value=ficha_no_requerida,
                key=f"ficha_no_requerida_{clave}",
                help="Útil para artículos de librería u otros productos sin ficha técnica. Seguirá incluyendo la foto si existe.",
            )
            if nueva_no_requerida != ficha_no_requerida:
                if de_cotizacion:
                    cambiar_anexo(clave, "ficha_opcional", nueva_no_requerida)
                else:
                    bib.actualizar(clave, ficha_no_requerida=nueva_no_requerida)
                st.rerun()
            b1, b2 = st.columns(2)
            sug_foto, sug_ficha = consultas_sugeridas(clave, desc)
            q_foto, q_ficha = E.get(f"qf_{clave}", sug_foto), E.get(f"qd_{clave}", sug_ficha)
            buscar_ficha = bool(problema_ficha)
            if b1.button("Buscar archivos" if ficha and buscar_ficha else "Buscar archivos faltantes",
                         key=f"b_{clave}", width="stretch",
                         disabled=bool(foto) and not buscar_ficha):
                with st.spinner("Buscando…"):
                    for aviso in buscar_para(clave, q_foto, q_ficha, fotos=not foto, fichas=buscar_ficha):
                        st.warning(aviso)
            with b2.popover("Más opciones", width="stretch"):
                if de_cotizacion:
                    st.caption("Los cambios se aplican a esta cotización. Usa anexos de producto.")
                    seleccion = E.get("anexos_cotizacion", {}).get(clave, {})
                    for tipo in ("foto", "ficha"):
                        if st.button(f"Quitar {tipo} de esta cotización", key=f"quitar_{tipo}_{clave}"):
                            cambiar_anexo(clave, tipo, None)
                            st.rerun()
                        if tipo in seleccion and st.button(f"Volver a usar {tipo} de la biblioteca",
                                                           key=f"heredar_{tipo}_{clave}"):
                            seleccion.pop(tipo)
                            persistir_borrador()
                            st.rerun()
                    personalizados = [(tipo, ruta) for tipo, ruta in (("foto", foto), ("ficha", ficha))
                                      if seleccion.get(tipo) and ruta]
                    if personalizados and st.button("Copiar anexos seleccionados a la biblioteca",
                                                        key=f"copiar_anexos_bib_{clave}"):
                        try:
                            bib.registrar(clave, nombre=desc)
                            for tipo, ruta in personalizados:
                                bib.guardar_archivo(clave, tipo, ruta.name, ruta.read_bytes())
                        except (ValueError, OSError) as error:
                            st.error(str(error))
                        else:
                            st.success("Anexos copiados a la ficha maestra.")
                st.text_input("Buscar la foto como", value=sug_foto, key=f"qf_{clave}")
                st.text_input("Buscar la ficha como", value=sug_ficha, key=f"qd_{clave}")
                if ficha and problema_ficha in (fichas_doc.SIN_TEXTO, fichas_doc.SIN_CODIGO):
                    st.download_button("Descargar ficha actual", data=ficha.read_bytes,
                                       file_name=f"{clave}.pdf", mime="application/pdf", key=f"df_{clave}")
                if ficha and problema_ficha != fichas_doc.ILEGIBLE and st.button(
                    "Sacar la foto desde la ficha", key=f"xf_{clave}"
                ):
                    E[f"cand_foto_{clave}"] = bs.fotos_de_ficha(ficha.read_bytes())
                st.file_uploader("Subir mi propia foto", type=["jpg", "jpeg", "png", "webp"], key=f"foto_{clave}",
                                 on_change=guardar_anexo_subido, args=(clave, "foto", de_cotizacion))
                st.file_uploader("Subir mi propia ficha (PDF)", type=["pdf"], key=f"ficha_{clave}",
                                 on_change=guardar_anexo_subido, args=(clave, "ficha", de_cotizacion))
                url_foto = st.text_input("…o pegar el enlace de una imagen", key=f"urlf_{clave}")
                if st.button("Usar ese enlace de imagen", key=f"dlf_{clave}") and url_foto:
                    try:
                        datos, ext, _, _ = bs.descargar_imagen(url_foto.strip())
                        aplicar_anexo(clave, "foto", "x" + ext, datos, de_cotizacion)
                        st.rerun()
                    except Exception:
                        st.error("No pude usar ese enlace.")
                url_pdf = st.text_input("…o pegar el enlace de un PDF", key=f"urlp_{clave}")
                if st.button("Usar ese enlace de PDF", key=f"dlp_{clave}") and url_pdf:
                    try:
                        datos, _ = bs.descargar_pdf(url_pdf.strip())
                        aplicar_anexo(clave, "ficha", "f.pdf", datos, de_cotizacion)
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
                            guardar_candidato(clave, "foto", c, de_cotizacion)
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
                        guardar_candidato(clave, "ficha", c, de_cotizacion)


def paso_3():
    st.title("Fotos y fichas técnicas", icon=":material/photo_library:")
    st.caption("Puedes seguir corrigiendo cantidades y precios aquí. Los cambios se guardan en el borrador.")
    editor_productos()
    boton_actualizar_totales()
    mostrar_totales()
    mostrar_resumen_lineas()
    editar_cabecera()
    claves, descripciones = datos_productos()
    if not claves:
        st.warning("No hay productos seleccionados. Marca al menos uno en el paso anterior.")
        if atras_cotizacion.button("← Volver", key="anexos_volver_sin_productos", width="stretch"):
            ir_a(2)
        sigue_cotizacion.button("Siguiente", disabled=True, key="anexos_sin_productos", width="stretch")
        return
    anexos = {clave: estado_anexos(clave, True) for clave in claves}
    faltan = [clave for clave, (foto, ficha, opcional) in anexos.items() if not foto or not (ficha or opcional)]
    listos = len(claves) - len(faltan)
    st.progress(
        listos / len(claves),
        text=f"{listos} de {len(claves)} productos listos · foto y ficha, o ficha marcada como no requerida",
    )
    if faltan:
        st.caption("Primero se buscan los archivos que faltan; después puedes revisar cada resultado y confirmar si corresponde.")
        if st.button(f"Buscar fotos y fichas pendientes ({len(faltan)})", icon=":material/search:", type="primary", width="stretch"):
            persistir_borrador()
            barra = st.progress(0.0)
            estado_busqueda = st.empty()
            estado_busqueda.caption("Buscando…")
            hubo_error = False
            for i, clave in enumerate(faltan):
                foto, ficha, no_requerida = anexos[clave]
                sug_foto, sug_ficha = consultas_sugeridas(clave, descripciones[clave])
                hubo_error |= bool(buscar_para(
                    clave, E.get(f"qf_{clave}", sug_foto), E.get(f"qd_{clave}", sug_ficha),
                    fotos=not foto, fichas=(not ficha and not no_requerida),
                ))
                barra.progress((i + 1) / len(faltan))
                estado_busqueda.caption(f"Buscando archivos… {i + 1} de {len(faltan)} productos")
                persistir_borrador()
            barra.empty()
            estado_busqueda.empty()
            if hubo_error:
                st.warning("El buscador no respondió para algunos productos. Revisa los pendientes e inténtalo de nuevo.")
            else:
                st.success("Búsqueda terminada. Revisa las opciones encontradas y confirma los archivos correctos.")
    else:
        st.success("Todos los productos cotizados tienen foto y una ficha disponible o marcada como no requerida.")
    st.caption("En artículos de librería puedes marcar «Este producto no requiere ficha técnica» desde su tarjeta.")

    st.divider()
    st.subheader("Productos y anexos")
    c_filtro, c_estado = st.columns([2, 1])
    busqueda_anexos = c_filtro.text_input(
        "Buscar producto", key="filtro_anexos_texto",
        placeholder="Código, marca o descripción",
    )
    filtro_anexos = c_estado.selectbox(
        "Mostrar",
        ["Todos", "Incompletos", "Sin foto", "Sin ficha pendiente", "Ficha no requerida", "Completos"],
        key="filtro_anexos_estado",
    )
    consulta_anexos = busqueda_anexos.strip().casefold()
    visibles = []
    for clave in claves:
        foto, ficha, no_requerida = anexos[clave]
        completo = bool(foto and (ficha or no_requerida))
        if consulta_anexos and consulta_anexos not in (clave + " " + descripciones[clave]).casefold():
            continue
        if filtro_anexos == "Incompletos" and completo:
            continue
        if filtro_anexos == "Sin foto" and foto:
            continue
        if filtro_anexos == "Sin ficha pendiente" and (ficha or no_requerida):
            continue
        if filtro_anexos == "Ficha no requerida" and not no_requerida:
            continue
        if filtro_anexos == "Completos" and not completo:
            continue
        visibles.append(clave)
    st.caption(f"Mostrando {len(visibles)} de {len(claves)} productos. El filtro solo cambia la vista; no excluye productos de la cotización.")
    if visibles:
        for clave in visibles:
            tarjeta(clave, descripciones[clave], *anexos[clave], de_cotizacion=True)
    else:
        st.info("No hay productos que coincidan con este filtro.")

    st.divider()
    if atras_cotizacion.button("← Volver", width="stretch"):
        ir_a(2)
    if sigue_cotizacion.button("Siguiente: revisar y crear el PDF →", type="primary", width="stretch"):
        persistir_borrador()
        ir_a(4)


# ============================================================== PASO 4: PDF
# ============================================================== REVISIÓN DE LA COTIZACIÓN
def hallazgos(d=None) -> list[dict]:
    d = pc.leer_para_pdf(E["ruta"]) if d is None else d
    claves = ["" if str(c) == "nan" else str(c or "").strip().upper() for c in E["tabla_edit"][COL_COD]]
    return rv.revisar(d, claves if len(claves) == len(d["lineas"]) else None, bib)


def hallazgos_editor():
    indices = [i for i, ln in enumerate(E["lineas"]) if ln.get("incluir", True)]
    lineas = [E["lineas"][i] for i in indices]
    cliente, tot = cliente_actual(), totales_actuales()
    resultado = rv.revisar({
        "lineas": lineas, "izquierda": [("Nombre", cliente["nombre"]), ("RUT", cliente["rut"])],
        "derecha": [("Fecha", cliente["fecha"].strftime("%d/%m/%Y"))],
        "totales": [("Total Neto", tot["neto"]), ("IVA", tot["iva"]), ("Total", tot["total"])],
    }, [ln.get("clave_auto", "") for ln in lineas], bib)
    return [{**h, "linea": indices[h["linea"]]} if "linea" in h else h for h in resultado]


def mostrar_hallazgo(h, key):
    if not h.get("campo"):
        st.text(h["texto"])
        return
    mensaje, accion = st.columns([3, 1.25], vertical_alignment="center")
    mensaje.text(h["texto"])
    accion.button("Ir al campo", icon=":material/arrow_forward:", key=key, width="stretch",
                  on_click=lambda: E.update(_hallazgo_destino=(h["campo"], h.get("linea"))))


def ir_al_hallazgo(campo, linea):
    if linea is not None:
        E["linea_editor"] = linea
    for clave in ("pdf_bytes", "pdf_nombre", "xlsx_vista_previa", "informe", "pdf_confirmado"):
        E.pop(clave, None)
    E["_destino_visual"] = campo
    ir_a(2)


def panel_revision(en_paso_4: bool = False) -> int:
    """Muestra errores y avisos antes de crear el PDF; devuelve errores bloqueantes."""
    try:
        hallazgos_actuales = hallazgos_editor()
    except Exception as exc:
        st.warning(f"No pude terminar la revisión automática: {exc}")
        return 0
    errores = [h for h in hallazgos_actuales if h["nivel"] == "error"]
    avisos = [h for h in hallazgos_actuales if h["nivel"] == "aviso"]
    if not hallazgos_actuales:
        st.success("Revisión completada: totales, IVA, precios y datos del cliente en orden.")
        return 0

    st.markdown("#### Revisión antes de generar el PDF")
    resumen = []
    if errores:
        resumen.append(f"{len(errores)} error(es) por corregir")
    if avisos:
        resumen.append(f"{len(avisos)} aviso(s) para revisar")
    st.caption(" · ".join(resumen))

    if errores:
        st.error(f"{len(errores)} problema(s) que requieren atención.")
        with st.expander("Ver errores de la cotización", expanded=True):
            for i, problema in enumerate(errores):
                mostrar_hallazgo(problema, f"revision_campo_error_{i}")
        if en_paso_4:
            st.button(
                "Volver a productos y precios",
                icon=":material/edit:",
                width="stretch",
                on_click=ir_a,
                args=(2,),
                key="revision_volver_productos",
            )
        elif E.get("origen_pedido") == "pedido":
            st.caption("El pedido sigue guardado. Puedes volver a los pasos anteriores para corregirlo.")

    if avisos:
        with st.expander(f"Ver avisos de revisión ({len(avisos)})", expanded=bool(not errores)):
            for i, aviso in enumerate(avisos):
                mostrar_hallazgo(aviso, f"revision_campo_aviso_{i}")
    return len(errores)


# ============================================================== PASO 4: PDF
def archivar_cotizacion(cot_id, **archivos):
    version = E.get("_cotizacion_version")
    if GIOVAL_PROFILES and cot_id != E.get("cot_id"):
        version = hist.obtener(cot_id)["actualizado"]
    hist.guardar_archivos(cot_id, version_esperada=version, **archivos)
    if GIOVAL_PROFILES or not GIOVAL_REQUIRE_ACCESS:
        E["_cotizacion_version"] = hist.obtener(cot_id)["actualizado"]


def congelar_anexos_cotizacion():
    claves, _ = datos_productos()
    E["anexos_cotizacion"] = anexos_doc.congelar(bib, claves, E.get("anexos_cotizacion", {}))
    persistir_borrador()
    if E.get("_conflicto_guardado"):
        raise hs.ConflictoActualizacion("Recarga la cotización antes de generar una versión.")


def resumen_pdf(documento):
    cabecera_pdf = {ct.normalizar(etiqueta): valor for etiqueta, valor in documento["izquierda"] + documento["derecha"]}
    montos_pdf = {ct.normalizar(etiqueta): valor for etiqueta, valor in documento["totales"]}
    entregas = {
        flujo.plazo_efectivo(ln) for ln in E["lineas"]
        if ln.get("incluir", True) and ln.get("es_producto", True)
    }
    plazos = [flujo.texto_plazo(dias) for dias in sorted(entregas, key=lambda v: (v is None, v or 0))]
    return {
        "cliente": cabecera_pdf.get("NOMBRE") or "Pendiente",
        "numero": (hist.obtener(E["cot_id"]) or {}).get("numero") or E.get("cot_numero") or "Pendiente",
        "total": montos_pdf.get("TOTAL"),
        "entrega": " · ".join(plazos) or "Por confirmar",
    }


def generar_pdf(estado=None):
    """Prepara un PDF y su Excel para revisar, sin confirmar la emisión."""
    def progreso(etapa):
        if estado is not None:
            estado.update(label=etapa)
    progreso("Preparando cotización")
    refrescar_excel_actual(archivar=False)
    congelar_anexos_cotizacion()
    finales = [
        {**ln, "clave": _texto_seguro(ln.get("clave_auto")).upper()}
        for ln in E.get("lineas", [])
        if _esta_marcado(ln.get("incluir", True), True) and ln.get("es_producto", True)
    ]
    ruta = Path(E["ruta"])
    with tempfile.TemporaryDirectory() as temporal:
        salida = Path(temporal) / "vista_previa.pdf"
        informe = ct.generar_pdf_final(
            ruta, finales, bib, salida, motor="libreoffice" if usar_lo else "nativo",
            resolver_anexos=lambda clave: estado_anexos(clave, True),
            progreso=progreso,
        )
        pdf_bytes, extra = opciones_cot.anexar_opciones(salida.read_bytes(), E.get("lineas", []))
        if extra:
            inicio_opciones = informe["paginas"]
            informe["paginas"] += extra
            informe["secciones"].append(("Opciones de productos", inicio_opciones, informe["paginas"]))
    progreso("Comprobando la vista previa")
    informe["paginas_revisar"] = revision_doc.revisar_paginas(pdf_bytes, informe["paginas_cotizacion"])
    documento = pc.leer_para_pdf(ruta)
    informe["validacion_excel"] = hallazgos(documento)
    informe["resumen"] = resumen_pdf(documento)
    numero = re.sub(r"[^A-Za-z0-9._-]", "_", E.get("cot_numero") or "cotizacion")
    E.update(pdf_bytes=pdf_bytes, pdf_nombre=f"{numero}.pdf", informe=informe,
             xlsx_vista_previa=ruta.read_bytes(), pdf_confirmado=False,
             version_vista_previa=E.get("_cotizacion_version"))


def confirmar_pdf():
    with tempfile.TemporaryDirectory() as temporal:
        xlsx = Path(temporal) / "cotizacion.xlsx"
        xlsx.write_bytes(E["xlsx_vista_previa"])
        hist.guardar_archivos(
            E["cot_id"], xlsx=xlsx, pdf=E["pdf_bytes"], version_esperada=E["version_vista_previa"],
            etiqueta_version="PDF confirmado",
        )
    E["_cotizacion_version"] = hist.obtener(E["cot_id"])["actualizado"]
    E["pdf_confirmado"] = True


def paso_4():
    visual.titulo("Revisa y crea tu PDF", "Comprueba los importes y el contenido antes de emitir.", "Cotización", "")
    if "pdf_bytes" not in E:
        editor_productos()
        boton_actualizar_totales()
        mostrar_totales()
        mostrar_resumen_lineas()
        editar_cabecera()
        panel_revision(en_paso_4=True)

        if st.button("Guardar versión de trabajo", key="guardar_version_cotizacion"):
            try:
                congelar_anexos_cotizacion()
                hist.guardar_version(E["cot_id"], E["_cotizacion_version"], "Versión de trabajo")
            except (OSError, ValueError, hs.ConflictoActualizacion) as error:
                st.error(str(error))
            else:
                st.success("Versión disponible en el Historial.")
        if sigue_cotizacion.button("Crear el PDF para revisar", type="primary", width="stretch", disabled=bool(E.get("error_numero"))):
            try:
                with st.status("Preparando cotización", expanded=False) as estado:
                    generar_pdf(estado)
                    estado.update(label="PDF listo para revisar", state="complete")
            except Exception as error:
                st.error(f"No pude preparar el PDF. {error}")
            else:
                st.rerun()
        if atras_cotizacion.button("← Volver a fotos y fichas", width="stretch"):
            ir_a(3)
    else:
        informe = E["informe"]
        st.subheader(f"Vista previa · {informe['paginas']} páginas")
        visor, acciones = st.columns([2.1, 1])
        with visor:
            secciones = informe.get("secciones", [])
            rotulos = ["Documento completo", *(f"{nombre} · pág. {inicio + 1}" for nombre, inicio, _ in secciones)]
            elegida = st.selectbox("Ir a una sección del PDF", range(len(rotulos)), format_func=rotulos.__getitem__,
                                   key=f"seccion_pdf_{E['cot_id']}")
            contenido = E["pdf_bytes"]
            if elegida:
                _, inicio, fin = secciones[elegida - 1]
                contenido = interfaz.fragmento_pdf(contenido, inicio, fin)
            try:
                st.pdf(contenido, height=650, key=f"pdf_preview_{E['cot_id']}_{elegida}", alt=rotulos[elegida])
            except Exception:
                st.download_button("Abrir copia para revisar", E["pdf_bytes"], file_name=E["pdf_nombre"],
                                   mime="application/pdf", on_click="ignore")
        with acciones:
            acciones_vista_pdf(informe)
    st.button("Hacer otra cotización", width="stretch", on_click=limpiar_estado_trabajo)


def acciones_vista_pdf(informe):
    with st.container(border=True):
        st.subheader("Revisar y aprobar")
        if resumen := informe.get("resumen"):
            st.caption("Cliente")
            st.text(resumen["cliente"])
            st.caption("Número de cotización")
            st.text(resumen["numero"])
            total = resumen["total"]
            st.metric("Total con IVA", rv.clp(total) if total is not None else "Por revisar")
            st.caption("Plazo de entrega")
            st.text(resumen["entrega"])
        etiquetas = (
            ("sin_clave", "Productos sin código"), ("sin_foto", "Fotos faltantes"),
            ("sin_ficha", "Fichas técnicas faltantes"), ("ficha_dudosa", "Fichas con código por verificar"),
            ("ficha_ilegible", "Fichas ilegibles excluidas"),
        )
        alertas = [f"{texto} · " + ", ".join(informe[campo]) for campo, texto in etiquetas if informe.get(campo)]
        alertas += informe["paginas_revisar"]
        revision = informe["validacion_excel"]
        if alertas or revision:
            with st.expander(f"Alertas de revisión ({len(alertas) + len(revision)})", expanded=True):
                for alerta in alertas:
                    st.text(alerta)
                indices = [i for i, ln in enumerate(E["lineas"]) if ln.get("incluir", True)]
                for i, hallazgo in enumerate(revision):
                    linea = hallazgo.get("linea")
                    if linea is None:
                        mostrar_hallazgo(hallazgo, f"pdf_campo_{i}")
                    elif linea < len(indices):
                        mostrar_hallazgo({**hallazgo, "linea": indices[linea]}, f"pdf_campo_{i}")
                    else:
                        st.text(hallazgo["texto"])
        if not E.get("pdf_confirmado"):
            requiere_revision = bool(informe["paginas_revisar"] or informe["ficha_ilegible"]
                                     or any(item["nivel"] == "error" for item in informe["validacion_excel"]))
            acepto = not requiere_revision or st.checkbox("Revisé las alertas y quiero aprobar esta versión",
                                                          key="ok_revision_pdf")
            if sigue_cotizacion.button("Confirmar versión y habilitar descarga", type="primary", width="stretch", disabled=not acepto):
                try:
                    confirmar_pdf()
                except (OSError, hs.ConflictoActualizacion) as error:
                    st.error(str(error))
                else:
                    st.rerun()
        else:
            sigue_cotizacion.badge("PDF aprobado", icon=":material/task_alt:", color="green")
            st.success("Versión registrada en el historial.")
            st.download_button("Descargar el PDF", E["pdf_bytes"], file_name=E["pdf_nombre"],
                               mime="application/pdf", type="primary", on_click="ignore")
            st.download_button("Descargar la cotización en Excel", E["xlsx_vista_previa"],
                               file_name=Path(E["pdf_nombre"]).with_suffix(".xlsx").name, on_click="ignore")
        if atras_cotizacion.button("Volver a editar", key="salir_vista_previa", width="stretch"):
            for campo in ("pdf_bytes", "xlsx_vista_previa", "pdf_nombre", "informe", "pdf_confirmado"):
                E.pop(campo, None)
            E["editado_manual"] = True
            st.rerun()


# ============================================================== HISTORIAL
def fecha_corta(iso: str) -> str:
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso or "")


@acceso.proteger
def cargar_en_pedido(cot_id: int, mantener: bool):
    """Lleva una cotización guardada al paso 1: como copia nueva, o para seguir editándola."""
    rec = hist.obtener(cot_id)
    if not rec:
        return
    E["_regreso_historial"] = E.get("vista") == VISTAS[1]
    for campo in ("_deshacer_lineas", "_linea_pendiente", "_lineas_base_version", "linea_editor",
                  "_error_precio_linea", "_estado_acuse_linea", "_revision_campos_linea"):
        E.pop(campo, None)
    datos = rec["datos"]
    lineas, cli = datos.get("lineas", []), datos.get("cliente", {})
    con_iva = bool(rec["con_iva"])
    mercado_publico = referencia_mercado_publico_borrador(rec, datos)
    mp_codigo = _texto_seguro(mercado_publico.get("codigo"))
    fuente_guardada = datos.get("fuente_pedido", datos.get("fuente_orden", ""))
    es_mercado_publico = bool(mp_codigo or fuente_guardada == "Mercado Público")
    orden_coincidencias = referencias_lineas_guardadas(lineas)
    for k in ("lineas", "ruta", "tabla", "tabla_edit", "tabla_base", "info_desc", "pdf_bytes", "pdf_nombre", "informe",
              "orden_df_base", "orden_editor_version", "orden_editor", "ok_riesgo", "ok_sin_precio", "cli_sel", "origen_pedido",
              "fuente_pedido", "mercado_publico_codigo", "_mercado_publico_codigo", "_mercado_publico_detalle"):
        E.pop(k, None)
    E["precios_modo"] = OPCIONES_PRECIO[0 if con_iva else 1]
    E["orden_df"] = pd.DataFrame({
        "Incluir": [_esta_marcado(ln.get("incluir"), True) for ln in lineas],
        "Cantidad": [ln["cantidad"] for ln in lineas],
        "Producto": [ln["descripcion"] for ln in lineas],
        "Código": [ln.get("codigo_fuente", "") for ln in lineas],
        "Unidad": [ln.get("unidad_fuente", "") for ln in lineas],
        "Origen": [ln.get("fuente_pedido", "") for ln in lineas],
        "Precio unitario": [ct.precio_para_mostrar(
            ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"), con_iva)
            if (ln.get("unitario") is not None or ln.get("precio_neto") is not None) else None for ln in lineas],
        **flujo.columnas_entrega(lineas),
    })
    E["_orden_coincidencias"] = orden_coincidencias
    E["_productos_catalogo_revision"] = productos_pendientes_de_asociar(lineas)
    E["orden_df_base"] = E["orden_df"].copy()
    E["orden_editor_version"] = int(E.get("orden_editor_version", 0)) + 1
    E.update(c_nombre=cli.get("nombre", ""), c_rut=cli.get("rut", ""), c_dir=cli.get("direccion", ""),
             c_ciudad=cli.get("ciudad", ""), c_tel=cli.get("telefono", ""), c_email=cli.get("email", ""))
    E["fuente_pedido"] = "Mercado Público" if es_mercado_publico else fuente_guardada
    if mp_codigo:
        E["mercado_publico_codigo"] = mp_codigo
        E["_mercado_publico_codigo"] = mp_codigo
        E["_mercado_publico_detalle"] = {
            "codigo": mp_codigo,
            "tipo": _texto_seguro(mercado_publico.get("tipo")),
            "institucion": _texto_seguro(mercado_publico.get("institucion")),
            "unidad_compra": _texto_seguro(mercado_publico.get("unidad_compra")),
            "rut_comprador": _texto_seguro(mercado_publico.get("rut_comprador")),
            "documentos": None,
            "url_portal": "https://www.mercadopublico.cl/",
        }
        completar_comprador_mercado_publico(E["_mercado_publico_detalle"])
    try:
        E["c_fecha"] = date.fromisoformat(rec["fecha"][:10]) if mantener else date.today()
    except ValueError:
        E["c_fecha"] = date.today()
    if mantener:
        E["cot_id"] = cot_id
        E["cot_numero"] = rec["numero"]
        E["_cotizacion_version"] = rec.get("actualizado")
    else:
        lineas_totales = [
            {
                "cantidad": ln.get("cantidad", 1),
                "precio_neto": ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"),
            }
            for ln in lineas if _esta_marcado(ln.get("incluir"), True)
        ]
        totales_nuevos = rv.totales_de(lineas_totales)
        cliente_nuevo = cliente_actual()
        nuevo_id, nuevo_numero = hist.crear(
            cliente_nuevo, lineas, totales_nuevos, con_iva=con_iva,
            fecha=E.get("c_fecha", date.today()),
            origen=datos.get("origen_pedido", rec.get("origen", "pedido")),
        )
        E["cot_id"] = nuevo_id
        E["cot_numero"] = nuevo_numero
        for clave_estado in ("pdf_bytes", "pdf_nombre", "informe", "ok_riesgo"):
            E.pop(clave_estado, None)
        E["editado_manual"] = True
        nuevo_registro = hist.obtener(nuevo_id)
        E["_cotizacion_version"] = nuevo_registro.get("actualizado") if nuevo_registro else None
    E["lineas"] = lineas
    E["anexos_cotizacion"] = anexos_doc.limpiar_referencias(datos.get("anexos"))
    E.pop("_conflicto_guardado", None)
    E.pop("_conflicto_guardado_id", None)
    E.pop("_conflicto_guardado_mensaje", None)
    E["paso"], E["vista"] = 1, VISTAS[0]
    persistir_borrador()


@acceso.proteger
def restaurar_version_interfaz(cot_id, origen, token):
    try:
        hist.restaurar_version(cot_id, origen, token)
    except (ValueError, hs.ConflictoActualizacion) as error:
        E["_error_restaurar_version"] = str(error)
    else:
        restaurar_borrador(cot_id)
        E["_mensaje_guardado"] = "Versión restaurada. El estado anterior quedó guardado."


def panel_versiones(registro):
    cot_id = registro["id"]
    with st.expander("Versiones de la cotización", key=f"versiones_{cot_id}", on_change="rerun") as panel:
        if not panel.open:
            return
        if error := E.pop("_error_restaurar_version", None):
            st.error(error)
        versiones = hist.versiones(cot_id)
        if not versiones:
            st.caption("Las versiones guardadas y las confirmaciones de PDF aparecerán aquí.")
            return
        por_id = {v["id"]: v for v in versiones}
        def etiqueta(identificador):
            if identificador is None:
                return "Cotización actual"
            v = por_id[identificador]
            return f"N.º {identificador} · {v['etiqueta']} · {v['fecha'][:19].replace('T', ' ')}"

        antes, despues = st.columns(2)
        origen = antes.selectbox("Versión de origen", por_id, format_func=etiqueta, key=f"version_origen_{cot_id}")
        destino = despues.selectbox("Comparar con", [None, *por_id], format_func=etiqueta, key=f"version_destino_{cot_id}")
        nuevo = registro if destino is None else por_id[destino]["contenido"]
        diferencias = revision_doc.comparar(por_id[origen]["contenido"], nuevo)
        if diferencias:
            st.dataframe(diferencias, hide_index=True, width="stretch")
        else:
            st.caption("Sin diferencias en los datos comparados.")
        confirmado = st.checkbox("Restaurar esta versión como borrador", key=f"version_seguro_{cot_id}_{origen}")
        st.button("Restaurar versión", disabled=not confirmado, key=f"restaurar_version_{cot_id}",
                  on_click=restaurar_version_interfaz, args=(cot_id, origen, registro["actualizado"]))


def lista_cotizaciones(titulo, registros, *, key, activa=None, memoria=None):
    columnas = ["Cotización", "Fecha", "Total", "Estado"]
    if not GIOVAL_REQUIRE_ACCESS:
        columnas.append("Cliente")
    filas = [
        [r["id"], r["numero"], fecha_corta(r["fecha"]), rv.clp(r["total"] or 0), r["estado"],
         *([] if GIOVAL_REQUIRE_ACCESS else [r["cliente"] or "Sin cliente"])]
        for r in registros
    ]
    elegida = interfaz.lista(titulo, columnas, filas, key=key, activa=activa, montos=(2,), memoria=memoria)
    return elegida if elegida in {r["id"] for r in registros} else None


def mostrar_vistas_guardadas(filtros=None):
    cuenta = identidad["usuario"] if identidad else None
    clave, original, vistas = opciones_ui.cargar_vistas(hist, cuenta)
    contexto = "historial" if filtros is not None else "inicio"
    acciones = st.container(horizontal=True)
    for nombre, vista in vistas.items():
        if not isinstance(vista, (list, tuple)) or len(vista) != 4:
            continue
        acciones.button(nombre, key=f"vista_{contexto}_{nombre}", on_click=aplicar_vista_guardada,
                        args=(tuple(vista),))
    if filtros is None:
        return
    personalizadas = {k: v for k, v in vistas.items() if k not in opciones_ui.VISTAS_INICIALES}
    with st.form("administrar_vistas_guardadas"):
        nombre = st.text_input("Nombre para guardar los filtros actuales", max_chars=45, key="nombre_vista_cotizaciones")
        borrar = st.selectbox("Vista personal a eliminar", personalizadas, index=None, key="borrar_vista_cotizaciones")
        guardar = st.form_submit_button("Guardar vista")
        eliminar = st.form_submit_button("Eliminar vista", disabled=not personalizadas)
    nombre = nombre.strip()
    if guardar and (not nombre or nombre in opciones_ui.VISTAS_INICIALES):
        st.warning("Escribe un nombre distinto de las vistas incluidas.")
        return
    if guardar and len(personalizadas) >= 8 and nombre not in personalizadas:
        st.warning("Puedes tener hasta ocho vistas personales.")
        return
    if guardar or (eliminar and borrar):
        if guardar:
            personalizadas[nombre] = list(filtros)
        else:
            personalizadas.pop(borrar)
        try:
            hist.set_config(clave, json.dumps(personalizadas, ensure_ascii=False), anterior=original)
        except hs.ConflictoActualizacion as exc:
            st.warning(str(exc))
        else:
            st.rerun()


def aplicar_vista_guardada(filtros):
    texto, estado, periodo, vencen = filtros
    navegar_vista(VISTAS[1])
    E.update(h_texto=texto, h_estado=estado, h_periodo=periodo, h_vence_hoy=vencen,
             _filtros_historial=(texto, estado, periodo), _filtro_vence_hoy=vencen)
    E.pop("_historial_seleccion", None)


def historial_cotizaciones():
    E.setdefault("h_ver", 0)
    todos = hist.listar(incluir_datos=False)
    if not todos:
        st.info("Todavía no hay cotizaciones. Cada vez que crees una, quedará guardada aquí con su número.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Cotizaciones", len(todos), border=True)
    m2.metric("Monto cotizado", rv.clp(sum(r["total"] or 0 for r in todos)), border=True)
    m3.metric("Monto aceptado", rv.clp(sum(r["total"] or 0 for r in todos if r["estado"] == "Aceptada")), border=True)
    f1, f2, f3 = st.columns([2.5, 1, 1.4])
    estados = ["Todos", *hs.ESTADOS]
    periodos = ["Todas las fechas", "Últimos 30 días", "Últimos 90 días", "Este año"]
    filtros = E.get("_filtros_historial", ("", estados[0], periodos[0]))
    for clave, valor in zip(("h_texto", "h_estado", "h_periodo"), filtros):
        E.setdefault(clave, valor)
    E.setdefault("h_vence_hoy", E.get("_filtro_vence_hoy", False))
    texto = f1.text_input(
        "Buscar",
        placeholder="Número de cotización" if GIOVAL_REQUIRE_ACCESS else "Número, cliente, RUT o notas",
        key="h_texto",
    )
    estado = f2.selectbox("Estado", estados, key="h_estado")
    periodo = f3.selectbox("Período", periodos, key="h_periodo")
    vencen = st.checkbox("Vencen hoy", key="h_vence_hoy")
    E["_filtro_vence_hoy"] = vencen
    E["_filtros_historial"] = (texto, estado, periodo)
    with st.expander("Vistas guardadas"):
        mostrar_vistas_guardadas((texto, estado, periodo, vencen))
    regs = hist.listar(texto, estado, incluir_datos=False) if texto.strip() or estado != "Todos" else todos
    hoy = date.today()
    if periodo == "Últimos 30 días":
        minimo = hoy - timedelta(days=30)
        regs = [r for r in regs if str(r.get("fecha") or "")[:10] >= minimo.isoformat()]
    elif periodo == "Últimos 90 días":
        minimo = hoy - timedelta(days=90)
        regs = [r for r in regs if str(r.get("fecha") or "")[:10] >= minimo.isoformat()]
    elif periodo == "Este año":
        regs = [r for r in regs if str(r.get("fecha") or "")[:4] == str(hoy.year)]
    if vencen:
        seguimientos = hist.seguimientos()
        regs = [r for r in regs if r["estado"] in ("Borrador", "Enviada")
                and seguimientos.get(r["id"], {}).get("vence") == hoy.isoformat()]
    st.caption(f"Mostrando {len(regs)} cotización(es) con los filtros seleccionados.")
    if not regs:
        st.info("No encontré cotizaciones con esos filtros. Cambia el estado, período o búsqueda.")
        return
    lista, detalle = st.columns([1.55, 1])
    vigente = E.get("_historial_seleccion")
    if vigente not in {r["id"] for r in regs}:
        vigente = regs[0]["id"]
    ambito = E.setdefault("_ambito_historial", uuid4().hex)
    huella = sha256(repr((ambito, texto, estado, periodo, vencen)).encode()).hexdigest()[:20]
    with lista:
        elegido = lista_cotizaciones("Cotizaciones del historial", regs, key=f"historial_lista_{huella}",
                                     activa=vigente, memoria=f"giovalia:historial:{huella}")
        if elegido is not None and elegido != vigente:
            E["_historial_seleccion"] = elegido
            st.rerun()
    E["_historial_seleccion"] = vigente
    panel = lista.expander("Editar estados y notas" if not GIOVAL_REQUIRE_ACCESS else "Editar estados")
    datos_tabla = {
        "id": [r["id"] for r in regs], "N°": [r["numero"] for r in regs],
        "Fecha": [fecha_corta(r["fecha"]) for r in regs],
        "Total": [r["total"] for r in regs], "Estado": [r["estado"] for r in regs],
    }
    if not GIOVAL_REQUIRE_ACCESS:
        datos_tabla["Cliente"] = [r["cliente"] for r in regs]
        datos_tabla["Notas"] = [r["notas"] or "" for r in regs]
    df = pd.DataFrame(datos_tabla)
    with panel:
        editado = editor(
            df, hide_index=True, width="stretch", key=f"h_tabla_{E['h_ver']}_{texto}_{estado}_{periodo}",
            disabled=[c for c in ("N°", "Fecha", "Total", "Cliente") if c in df.columns],
            column_config={"id": None, "Total": st.column_config.NumberColumn(format="$ %d"),
                           "Estado": st.column_config.SelectboxColumn(options=hs.ESTADOS, required=True)})
    if panel.button("Guardar estados" if GIOVAL_REQUIRE_ACCESS else "Guardar estados y notas", icon=":material/save:"):
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

    rec = hist.obtener(vigente)
    if not rec:
        detalle.info("La cotización seleccionada ya no está disponible.")
        return
    with detalle.container(border=True):
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
        panel_versiones(rec)
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
    st.title("Historial de cotizaciones")
    if cliente := E.pop("_cliente_busqueda", None):
        st.subheader(cliente["nombre"])
        st.write(" · ".join(str(cliente.get(campo) or "") for campo in ("rut", "telefono", "email")).strip(" ·"))
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
@acceso.proteger
def renombrar_producto(clave: str):
    nuevo = str(E.get(f"ren_{clave}", "")).strip().upper()
    try:
        final = bib.renombrar(clave, nuevo, combinar=bool(E.get(f"comb_{clave}")))
        E["p_sel"], E["p_msg"] = final, ("ok", f"Listo: ahora se llama {final}.")
    except ValueError as e:
        E["p_msg"] = ("error", str(e))


@acceso.proteger
def borrar_producto(clave: str):
    bib.eliminar(clave)
    E.pop("p_sel", None)
    E["p_msg"] = ("ok", f"Eliminé {clave}.")


def abrir_producto_desde_catalogo(clave: str):
    E.update(vista=VISTAS[2], p_sel=clave, p_texto="", p_filtro_estado="Todos")


def abrir_producto_catalogo_ui(clave: str):
    E["_abrir_producto_catalogo"] = clave
    st.rerun()


def abrir_cliente_desde_busqueda(cliente):
    E.update(vista=VISTAS[1], h_texto=cliente["rut"] or cliente["nombre"],
             h_estado="Todos", h_periodo="Todas las fechas", h_vence_hoy=False, _filtro_vence_hoy=False,
             _cliente_busqueda=cliente)


def mostrar_busqueda_global():
    with st.container(key="busqueda_global"):
        ventana = st.popover("Buscar · Ctrl+K", icon=":material/search:",
                             key="busqueda_global_popover", on_change="rerun")
    if not ventana.open:
        return
    with ventana:
        consulta = st.text_input(
            "Buscar en Giovalia", key="consulta_global", value=E.get("_ultima_consulta_global", ""),
            placeholder="Producto o cotización" if GIOVAL_REQUIRE_ACCESS else "Producto, cliente o cotización",
        ).strip()
        E["_ultima_consulta_global"] = consulta
        st.html(f'<span id="gv-consulta" hidden data-consulta="{html.escape(consulta)}"></span>')
        if not consulta:
            return
        productos, clientes, cotizaciones = flujo.buscar(consulta, bib, hist, privado=GIOVAL_REQUIRE_ACCESS)
        if not any((productos, clientes, cotizaciones)):
            st.info("No encontré coincidencias.")
        if productos:
            st.markdown("**Productos**")
            for codigo, nombre in productos:
                st.button(f"{codigo} · {nombre}", key=f"buscar_producto_{codigo}",
                          on_click=abrir_producto_desde_catalogo, args=(codigo,))
        if clientes:
            st.markdown("**Clientes**")
            for cliente in clientes:
                etiqueta = " · ".join(filter(None, (cliente["nombre"], cliente["rut"])))
                st.button(etiqueta, key=f"buscar_cliente_{cliente['id']}",
                          on_click=abrir_cliente_desde_busqueda, args=(cliente,))
        if cotizaciones:
            st.markdown("**Cotizaciones**")
            for cot_id, numero, coincidencias in cotizaciones:
                st.button(f"Abrir {numero}", key=f"buscar_cotizacion_{cot_id}",
                          on_click=restaurar_borrador, args=(cot_id,))
                if coincidencias:
                    st.caption(" · ".join(dict.fromkeys(coincidencias[:3])))


def editar_stock_masivo(visibles, filtro):
    panel = st.expander("Actualizar stock de varios productos", key="stock_masivo_panel", on_change="rerun")
    if not panel.open:
        E.pop("_base_stock_masivo", None)
        return
    contexto, bases = E.get("_base_stock_masivo", (None, None))
    if contexto != filtro:
        bases = {clave: dict(bib.indice[clave]) for clave in visibles}
        E["_base_stock_masivo"] = (filtro, bases)
    # El formulario se evalúa contra la lista mostrada al abrirlo, incluso cuando
    # otra sesión ya corrigió un producto y lo sacó del filtro.
    with panel, st.form(f"stock_masivo_{filtro}"):
        codigos = st.multiselect(
            "Productos para actualizar", list(bases), key=f"stock_masivo_codigos_{filtro}",
            format_func=lambda codigo: f"{codigo} · {bases[codigo].get('nombre') or codigo}",
        )
        c1, c2, c3 = st.columns(3)
        estado = c1.selectbox("Nuevo estado de stock", flujo.DISPONIBILIDADES, key="stock_masivo_estado")
        fecha = c2.date_input("Fecha de confirmación", date.today(), max_value=date.today(), key="stock_masivo_fecha")
        fuente = c3.text_input("Proveedor o sucursal", max_chars=120, key="stock_masivo_fuente")
        if st.form_submit_button("Aplicar stock a la selección", key="stock_masivo_aplicar", type="primary"):
            if not codigos:
                st.warning("Selecciona al menos un producto.")
                return
            try:
                campos = stock_catalogo.campos_stock(estado, fecha, fuente)
                campos["stock_cantidad"] = None
                cantidad = bib.actualizar_varios(codigos, campos, bases)
            except (ValueError, OSError) as exc:
                E.pop("_base_stock_masivo", None)
                st.error(str(exc))
            else:
                E.pop("_base_stock_masivo", None)
                E.pop("_base_stock_pendientes", None)
                E["_mensaje_guardado"] = f"Stock actualizado en {cantidad} producto(s)."
                st.rerun()


def editar_stock_individual(clave, seccion, *, incluye_costo=False):
    clave_base = f"_base_stock_{seccion}"
    base_clave, base = E.get(clave_base, (None, None))
    if base_clave != clave:
        base = dict(bib.indice[clave])
        E[clave_base] = (clave, base)
    confirmacion = stock_catalogo.datos_stock(base)
    fecha_anterior = confirmacion["stock_confirmado_el"]
    if fecha_anterior:
        st.caption(f"Confirmado el {fecha_anterior:%d/%m/%Y} · "
                   f"{confirmacion['stock_fuente'] or 'Origen sin registrar'}")
    with st.form(f"stock_{seccion}_{clave}"):
        c1, c2 = st.columns(2)
        if incluye_costo:
            costo_base = base.get("costo_neto")
            costo = c1.number_input("Costo neto", min_value=0.0, step=100.0,
                                    value=None if costo_base is None else float(costo_base))
        disponibilidad = c2.selectbox(
            "Stock en biblioteca", flujo.DISPONIBILIDADES,
            index=flujo.DISPONIBILIDADES.index(confirmacion["estado_stock"]),
        )
        fecha = c1.date_input("Fecha de confirmación", fecha_anterior or date.today(), max_value=date.today())
        fuente = c2.text_input("Proveedor o sucursal", confirmacion["stock_fuente"], max_chars=120)
        cantidad_stock = base.get("stock_cantidad")
        cantidad = c1.number_input("Cantidad disponible confirmada", min_value=0.0, step=1.0,
                                   value=None if cantidad_stock is None else float(cantidad_stock))
        reposicion = c2.number_input("Reposición (días)", min_value=0, step=1, value=base.get("reposicion_dias"))
        if st.form_submit_button("Guardar costo y stock" if incluye_costo else "Guardar stock", type="primary"):
            try:
                campos = stock_catalogo.campos_stock(disponibilidad, fecha, fuente)
                campos.update(stock_cantidad=cantidad if disponibilidad == flujo.EN_STOCK else None,
                              reposicion_dias=reposicion)
                if incluye_costo:
                    campos["costo_neto"] = costo
                bib.actualizar(clave, version_esperada=base, **campos)
            except (ValueError, OSError) as exc:
                st.error(str(exc))
                E.pop(clave_base, None)
            else:
                E.pop(clave_base, None)
                E.pop("_base_stock_masivo", None)
                E["_mensaje_guardado"] = f"Datos guardados para {clave}."
                st.rerun()


def vista_pendientes():
    st.title("Datos pendientes")
    filtro = st.segmented_control(
        "Pendiente", ("Todos", "Sin costo", "Stock sin confirmar", "Ficha incompleta", "Stock confirmado"),
        default="Todos", required=True, key="pendientes_filtro",
    )
    motivos_por_producto = {}
    for clave, datos in sorted(bib.indice.items()):
        motivos = {}
        stock = stock_catalogo.datos_stock(datos)
        if datos.get("costo_neto") is None:
            motivos["Sin costo"] = "Sin costo"
        if stock["estado_stock"] == flujo.POR_CONFIRMAR:
            motivos["Stock sin confirmar"] = "Stock sin confirmar"
        elif stock["stock_confirmado_el"] is None:
            motivos["Stock sin confirmar"] = "Stock sin fecha de confirmación"
        if filtro in ("Todos", "Ficha incompleta"):
            _, ficha = bib.archivos(clave)
            problema = revisar_ficha_producto(clave, ficha, datos.get("ficha_no_requerida", False))
            if problema:
                motivos["Ficha incompleta"] = problema
        if filtro == "Stock confirmado" and stock["estado_stock"] != flujo.POR_CONFIRMAR:
            motivos_por_producto[clave] = {"Stock confirmado": stock["estado_stock"]}
        elif motivos and (filtro == "Todos" or filtro in motivos):
            motivos_por_producto[clave] = motivos
    visibles = list(motivos_por_producto)
    if visibles or E.get("_base_stock_masivo"):
        editar_stock_masivo(visibles, filtro)
    if not visibles:
        st.success("No hay productos con stock confirmado." if filtro == "Stock confirmado"
                   else "No hay productos pendientes con este filtro.")
        return
    st.caption(f"{len(visibles)} productos")
    clave = st.selectbox("Producto pendiente", visibles,
                        format_func=lambda codigo: f"{codigo} · {bib.indice[codigo].get('nombre') or codigo}")
    producto = bib.indice[clave]
    st.write(" · ".join(motivos_por_producto[clave].values()))
    editar_stock_individual(clave, "pendientes", incluye_costo=True)
    tarjeta(clave, producto.get("nombre") or clave, *estado_anexos(clave))


def vista_productos():
    E.setdefault("p_ver", 0)
    st.title("Biblioteca de productos", icon=":material/inventory_2:")
    st.caption("Precios, fotos y fichas se recuperan cuando una cotización coincide con estos productos.")
    if "p_msg" in E:
        nivel, texto = E.pop("p_msg")
        (st.success if nivel == "ok" else st.error)(texto)

    with st.expander("Agregar un producto", expanded=False):
        n1, n2 = st.columns([1, 2])
        nuevo_cod = n1.text_input("Código del producto", key="np_cod")
        nuevo_nom = n2.text_input("Nombre o descripción", key="np_nom")
        if st.button("Agregar producto", icon=":material/add:", disabled=not nuevo_cod.strip(),
                     key="cat_agregar_producto"):
            clave_nueva = nuevo_cod.strip().upper()
            try:
                bib.registrar(clave_nueva, nombre=nuevo_nom.strip() or clave_nueva)
            except ValueError as error:
                st.error(str(error))
            else:
                E["p_ver"] += 1
                E["p_msg"] = ("ok", f"Producto {clave_nueva} agregado al catálogo.")
                st.rerun()

    if codigo := opciones_ui.buscar_impresora(bib, "biblioteca"):
        abrir_producto_catalogo_ui(codigo)
    todas = bib.filas_catalogo()
    if not todas:
        st.info("Todavía no hay productos. Agrega uno aquí o se irán incorporando al preparar cotizaciones.")
        return

    total_sin_foto = sum(not f["foto"] for f in todas)
    total_sin_ficha = sum(not f["ficha"] and not f.get("ficha_no_requerida", False) for f in todas)
    total_sin_precio = sum(f.get("precio_neto") is None for f in todas)
    total_margen_pendiente = sum(f.get("costo_neto") is None or f.get("margen_pct") is None for f in todas)
    opciones = (("Productos", len(todas), "Todos"), ("Sin foto", total_sin_foto, "Sin foto"),
                ("Sin ficha", total_sin_ficha, "Sin ficha pendiente"), ("Sin precio", total_sin_precio, "Sin precio"))
    for col, (titulo, conteo, opcion) in zip(st.columns(4), opciones):
        col.button(f"{titulo} · {conteo}", width="stretch", key=f"biblioteca_indicador_{opcion}",
                   type="primary" if E.get("p_filtro_estado", "Todos") == opcion else "secondary",
                   on_click=lambda valor=opcion: E.update(p_filtro_estado=valor))

    f1, f2 = st.columns([1.5, 1])
    texto = f1.text_input("Buscar producto", placeholder="Código, modelo, marca o nombre", key="p_texto")
    filtro = f2.selectbox(
        "Filtrar por estado",
        ["Todos", "Incompletos", "Sin foto", "Sin ficha pendiente", "Sin precio",
         "Margen pendiente", "Ficha no requerida"],
        key="p_filtro_estado",
    )
    if filtro != "Todos" or texto.strip():
        st.button("Limpiar búsqueda y filtros", icon=":material/filter_alt_off:", key="biblioteca_limpiar",
                  on_click=lambda: E.update(p_texto="", p_filtro_estado="Todos"))
    consulta = texto.strip().casefold()
    filas = []
    for producto in todas:
        clave, nombre = producto["clave"], producto["nombre"]
        sin_ficha = not producto["ficha"] and not producto.get("ficha_no_requerida", False)
        sin_precio = producto.get("precio_neto") is None
        margen_pendiente = producto.get("costo_neto") is None or producto.get("margen_pct") is None
        incompleto = not producto["foto"] or sin_ficha
        coincide_texto = not consulta or consulta in f"{clave} {nombre}".casefold()
        coincide_filtro = (
            filtro == "Todos"
            or (filtro == "Incompletos" and incompleto)
            or (filtro == "Sin foto" and not producto["foto"])
            or (filtro == "Sin ficha pendiente" and sin_ficha)
            or (filtro == "Sin precio" and sin_precio)
            or (filtro == "Margen pendiente" and margen_pendiente)
            or (filtro == "Ficha no requerida" and producto.get("ficha_no_requerida", False))
        )
        if coincide_texto and coincide_filtro:
            filas.append(producto)

    st.caption(
        f"Mostrando {len(filas)} de {len(todas)} productos · "
        f"{total_margen_pendiente} con costo o margen pendiente."
    )
    if not filas:
        st.info("No hay productos que coincidan con esos filtros.")
        return

    modo = st.segmented_control("Formato de la biblioteca", ["Tabla", "Galería"], default="Tabla",
                                required=True, key="biblioteca_modo")
    if modo == "Galería":
        galeria_biblioteca(filas)
    else:
        editar_tabla_biblioteca(filas, texto, filtro)
    st.divider()
    detalle_biblioteca(filas)


def galeria_biblioteca(filas):
    paginas = (len(filas) + 11) // 12
    clave = "biblioteca_galeria_pagina"
    if E.get(clave, 1) > paginas:
        E[clave] = 1
    pagina = st.number_input("Página de productos", 1, paginas, step=1, key=clave) if paginas > 1 else 1
    grupo = filas[12 * (pagina - 1):12 * pagina]
    for i in range(0, len(grupo), 3):
        for col, producto in zip(st.columns(3), grupo[i:i + 3]):
            codigo = producto["clave"]
            with col.container(border=True):
                foto, _ = bib.archivos(codigo)
                if foto:
                    try:
                        st.image(str(foto), width=144, alt=producto["nombre"])
                    except (OSError, ValueError, st.errors.StreamlitAPIException):
                        st.caption("Foto no disponible")
                else:
                    st.caption("Sin foto")
                st.write(producto["nombre"])
                st.caption(codigo)
                st.button("Ver producto", key=f"biblioteca_galeria_{codigo}", width="stretch",
                          on_click=lambda c=codigo: E.update(p_sel=c, _destino_visual="producto"))


def editar_tabla_biblioteca(filas, texto, filtro):
    mostrar_detalles = st.checkbox(
        "Mostrar notas y sugerencia con IVA", value=False, key="p_mostrar_detalles"
    )
    mostrar = {
        "Código": [f["clave"] for f in filas],
        "Producto": [f["nombre"] for f in filas],
        "Foto": ["Disponible" if f["foto"] else "Falta" for f in filas],
        "Ficha": [
            "Disponible" if f["ficha"] else
            "No requerida" if f.get("ficha_no_requerida", False) else "Falta"
            for f in filas
        ],
        "Último precio neto": [f["precio_neto"] for f in filas],
        "Costo neto": [f["costo_neto"] for f in filas],
        "Margen %": [f["margen_pct"] for f in filas],
        "Sugerido neto": [f["sugerido_neto"] for f in filas],
    }
    if mostrar_detalles:
        mostrar["Sugerido con IVA"] = [
            None if f["sugerido_neto"] is None else f["sugerido_neto"] * (1 + rv.IVA)
            for f in filas
        ]
        mostrar["Notas"] = [f["notas"] or "" for f in filas]

    mostrar_df = pd.DataFrame(mostrar)
    cols_numero = ["Último precio neto", "Costo neto", "Sugerido neto"]
    if mostrar_detalles:
        cols_numero.append("Sugerido con IVA")
    configuracion_columnas = {
        col: st.column_config.NumberColumn(format="$ %d", width="small")
        for col in cols_numero
    }
    configuracion_columnas["Margen %"] = st.column_config.NumberColumn(
        format="%.1f %%", min_value=0, step=1, width="small"
    )
    configuracion_columnas["Código"] = st.column_config.TextColumn(width="small", pinned=True)
    configuracion_columnas["Producto"] = st.column_config.TextColumn(width="medium", pinned=True)
    configuracion_columnas["Foto"] = st.column_config.TextColumn(width="small")
    configuracion_columnas["Ficha"] = st.column_config.TextColumn(width="small")

    editado = editor(
        mostrar_df, hide_index=True, width="stretch",
        key=f"p_tabla_{E['p_ver']}_{texto}_{filtro}_{mostrar_detalles}",
        disabled=[
            "Código", "Foto", "Ficha", "Último precio neto", "Sugerido neto",
            *(["Sugerido con IVA"] if mostrar_detalles else []),
        ],
        column_config=configuracion_columnas,
    )
    with st.expander("Cómo se calcula el precio sugerido"):
        st.caption(
            "Precio sugerido neto = costo neto × (1 + margen sobre el costo). "
            "Por ejemplo, un costo de $10.000 con margen de 30 % produce un precio neto "
            "sugerido de $13.000. La sugerencia con IVA se calcula aparte al 19 %."
        )

    if st.button("Guardar cambios", icon=":material/save:", type="primary", key="cat_guardar_cambios"):
        cambios = 0
        for (_, fila), producto in zip(editado.iterrows(), filas):
            notas = (
                fila["Notas"] if "Notas" in fila.index and isinstance(fila["Notas"], str)
                else producto.get("notas") or ""
            )
            nuevos = {
                "nombre": str(fila["Producto"] or "").strip() or producto["clave"],
                "costo_neto": fila["Costo neto"],
                "margen_pct": fila["Margen %"],
                "notas": notas,
            }
            viejos = {
                "nombre": producto["nombre"],
                "costo_neto": producto["costo_neto"],
                "margen_pct": producto["margen_pct"],
                "notas": producto["notas"] or "",
            }
            distintos = False
            for campo in nuevos:
                nuevo, viejo = nuevos[campo], viejos[campo]
                try:
                    ambos_vacios = pd.isna(nuevo) and viejo is None
                except (TypeError, ValueError):
                    ambos_vacios = False
                if not ambos_vacios and nuevo != viejo:
                    distintos = True
                    break
            if distintos:
                bib.actualizar(producto["clave"], **nuevos)
                cambios += 1
        E["p_ver"] += 1
        E["p_msg"] = ("ok", f"Guardado: {cambios} producto(s) actualizado(s) en la biblioteca compartida.")
        st.rerun()



def detalle_biblioteca(filas):
    st.html('<span id="giovalia-detalle-producto" tabindex="-1"></span>')
    st.subheader("Detalle del producto")
    codigos = [f["clave"] for f in filas]
    if E.get("p_sel") not in codigos:
        E["p_sel"] = codigos[0]
    elegido = st.selectbox("Selecciona un producto", codigos, key="p_sel")
    producto_elegido = next(f for f in filas if f["clave"] == elegido)
    tarjeta(elegido, producto_elegido["nombre"], *estado_anexos(elegido))
    with st.expander("Disponibilidad de stock", key="biblioteca_stock_panel", on_change="rerun") as panel_stock:
        if panel_stock.open:
            editar_stock_individual(elegido, "biblioteca")
        else:
            E.pop("_base_stock_biblioteca", None)
    editar_ficha(bib, elegido)
    with st.expander("Acciones avanzadas: cambiar código o eliminar"):
        st.text_input("Nuevo código", key=f"ren_{elegido}")
        st.checkbox("Combinar si ese código ya existe", key=f"comb_{elegido}")
        st.button("Cambiar el código", key=f"renb_{elegido}",
                  on_click=renombrar_producto, args=(elegido,))
        st.divider()
        seguro = st.checkbox(
            "Confirmo que quiero eliminar el producto y sus archivos",
            key=f"delp_{elegido}",
        )
        st.button("Eliminar producto", disabled=not seguro, key=f"delpb_{elegido}",
                  on_click=borrar_producto, args=(elegido,))


def configuracion_compacta():
    """Resuelve los servicios de sesión y carga Ajustes solo cuando se abre."""
    carpeta_compartida = str((APP_DIR / "biblioteca").resolve())
    carpeta = carpeta_compartida if (GIOVAL_REQUIRE_ACCESS or GIOVAL_PROFILES) else E.get("config_carpeta", carpeta_compartida)
    ruta = str(Path(carpeta).resolve())
    servicios = E.get("_servicios_sesion")
    if not isinstance(servicios, dict) or servicios.get("carpeta") != ruta:
        servicios = {"carpeta": ruta, "biblioteca": ct.Biblioteca(ruta), "historial": hs.Historial(ruta)}
        E["_servicios_sesion"] = servicios
    else:
        servicios["biblioteca"].refrescar_si_cambio()

    historiador = servicios["historial"]
    historiador.actor = (identidad["nombre"] if identidad else
                         "Equipo con clave compartida" if GIOVAL_REQUIRE_ACCESS else "Usuario local")
    servicios["biblioteca"].registrar_cambio = historiador.registrar_cambio
    conexion_ia = li.buscar_api_key(APP_DIR)
    conexion_mp = mp.buscar_ticket(APP_DIR)
    ajustes = st.popover("Ajustes", icon=":material/settings:", key="settings-popover", on_change="rerun")
    if ajustes.open:
        with ajustes:
            if identidad:
                acceso.panel_cuenta()
            if not identidad or identidad["rol"] == "admin":
                mostrar_preferencias(carpeta_compartida, servicios, conexion_ia, conexion_mp)

    api_key = str(conexion_ia[0] or E.get("config_api_key", "")).strip()
    usar_lo = bool(E.get("config_usar_lo", False))
    ticket_mp = str(conexion_mp[0] or E.get("_mercado_publico_ticket", "")).strip()
    return carpeta, servicios["biblioteca"], servicios["historial"], api_key, usar_lo, ticket_mp


def mostrar_preferencias(carpeta_compartida, servicios, conexion_ia, conexion_mp):
    """Muestra conexiones, numeración y respaldo de la biblioteca activa."""
    bib_panel, hist_panel = servicios["biblioteca"], servicios["historial"]
    api_key, origen_key = conexion_ia
    ticket_mp, origen_ticket_mp = conexion_mp
    st.markdown("### Preferencias")

    if GIOVAL_REQUIRE_ACCESS:
        st.text_input(
            "Biblioteca compartida",
            value=carpeta_compartida,
            disabled=True,
            help="Todos los usuarios autorizados trabajan sobre estos mismos datos.",
            key="config_carpeta_compartida",
        )
        st.caption("Los cambios quedan guardados en el servidor central y se ven en las demás instalaciones.")
    else:
        st.text_input(
            "Carpeta de datos y productos",
            value=carpeta_compartida,
            key="config_carpeta",
            persist_state="session",
            help="Normalmente puedes dejar la biblioteca de este computador como está.",
        )
    st.divider()
    st.markdown("**Conexión de inteligencia artificial**")
    if api_key:
        st.caption(f"Conectada · Clave encontrada en {origen_key}")
    elif GIOVAL_REQUIRE_ACCESS:
        st.caption(
            "La IA no está configurada en el servidor. El administrador puede definir "
            "OPENAI_API_KEY en las variables del servidor; no se guardan claves "
            "personales desde una sesión compartida."
        )
    else:
        api_key = st.text_input(
            "Clave de OpenAI",
            type="password",
            key="config_api_key",
            persist_state="session",
            help="Se usa para interpretar pedidos en PDF o foto y mejorar la identificación de productos.",
        )
        if api_key and st.button(
            "Guardar clave",
            icon=":material/save:",
            key="config_guardar_api",
            width="stretch",
        ):
            try:
                li.guardar_api_key(APP_DIR, api_key)
                st.success("Clave guardada en este computador.")
            except (OSError, ValueError) as exc:
                st.error(f"No pude guardar la clave en el archivo .env. Comprueba los permisos de esta carpeta. Detalle: {exc}")

    st.divider()
    st.markdown("**Conexión a Mercado Público**")
    if ticket_mp:
        st.caption(f"Conectada · Ticket encontrado en {origen_ticket_mp}")
    elif GIOVAL_REQUIRE_ACCESS:
        st.caption(
            "El administrador debe definir MERCADO_PUBLICO_TICKET en los secretos de Streamlit "
            "o como variable privada del servidor."
        )
    else:
        if "_mercado_publico_ticket" in E and "config_ticket_mercado_publico" not in E:
            E["config_ticket_mercado_publico"] = E["_mercado_publico_ticket"]
        ticket_mp = st.text_input(
            "Ticket API de Mercado Público",
            type="password",
            key="config_ticket_mercado_publico",
            on_change=guardar_ticket_mp_sesion,
            help="Se usa para consultar solicitudes Compra Ágil y órdenes por código. No se incluye en las cotizaciones.",
        )
        st.caption("Se conserva solo en la sesión actual. También puedes definir MERCADO_PUBLICO_TICKET en los secretos privados del servidor.")

    with st.expander("Opciones para crear el PDF", expanded=False):
        st.checkbox(
            "Usar LibreOffice para convertir a PDF",
            value=False,
            key="config_usar_lo",
            persist_state="session",
            help="Déjalo desmarcado salvo que necesites conservar exactamente el formato de Excel.",
        )
        st.caption("La opción normal genera el PDF sin abrir LibreOffice.")

    with st.expander("Numeración de cotizaciones", expanded=False):
        if "config_formato_numero" not in E:
            E["config_formato_numero"] = hist_panel.config("formato_numero", hs.FORMATO_DEFECTO)
        if "config_siguiente_numero" not in E:
            E["config_siguiente_numero"] = hist_panel.siguiente_correlativo()
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
        if GIOVAL_REQUIRE_ACCESS:
            st.caption(
                "El ZIP incluye productos, fotos, fichas y el historial comercial compartido; "
                "no incluye la libreta de clientes ni PDF/Excel finales con datos personales."
            )
        else:
            st.caption("El ZIP incluye la base de datos, catálogo, fotos, fichas y archivos de cotizaciones.")
        ultimo_respaldo = hist_panel.config("ultimo_respaldo_preparado")
        if ultimo_respaldo:
            try:
                fecha_respaldo = datetime.fromisoformat(ultimo_respaldo).strftime("%d/%m/%Y a las %H:%M")
            except (TypeError, ValueError):
                fecha_respaldo = str(ultimo_respaldo)
            st.caption(f"Último ZIP preparado: {fecha_respaldo}.")
        else:
            st.caption("Todavía no se ha preparado un respaldo desde este equipo.")
        if st.button("Preparar respaldo", icon=":material/archive:", key="preparar_respaldo", width="stretch"):
            try:
                E["_respaldo_zip"] = crear_respaldo_biblioteca(bib_panel.dir)
                ahora_respaldo = datetime.now()
                E["_respaldo_nombre"] = f"respaldo_giovalia_{ahora_respaldo:%Y%m%d_%H%M%S}.zip"
                hist_panel.set_config("ultimo_respaldo_preparado", ahora_respaldo.isoformat(timespec="seconds"))
                st.success("ZIP preparado. Descárgalo y guarda una copia fuera del computador central.")
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
            st.caption(
                "La fecha indica cuándo se generó el ZIP, no confirma que lo hayas descargado. "
                "Guárdalo también en otro disco o ubicación segura."
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


@acceso.proteger
def guardar_conflicto_como_nueva():
    """Conserva el trabajo de esta sesión como una cotización nueva sin tocar la versión compartida."""
    lineas = list(E.get("lineas", []))
    cliente = cliente_actual()
    totales = totales_actuales()
    con_iva = E.get("precios_modo", OPCIONES_PRECIO[0]) == OPCIONES_PRECIO[0]
    try:
        nuevo_id, nuevo_numero = hist.crear(
            cliente, lineas, totales, con_iva=con_iva,
            fecha=cliente.get("fecha") or date.today(),
            origen=E.get("origen_pedido", "pedido"),
        )
        E["cot_id"] = nuevo_id
        E["cot_numero"] = nuevo_numero
        nuevo_registro = hist.obtener(nuevo_id)
        E["_cotizacion_version"] = nuevo_registro.get("actualizado") if nuevo_registro else None
        E.pop("_conflicto_guardado", None)
        E.pop("_conflicto_guardado_id", None)
        E.pop("_conflicto_guardado_mensaje", None)
        E["_mensaje_guardado"] = f"Guardé tus cambios como la cotización nueva {nuevo_numero}. La versión anterior no se modificó."
        persistir_borrador()
    except Exception as exc:
        E["_conflicto_guardado_mensaje"] = f"No pude crear la copia de tus cambios: {exc}"


def proteger_edicion_al_navegar():
    if E.get("_vista_renderizada") != VISTAS[0] or not E.get("cot_id"):
        return
    persistir_borrador()
    orden = E.get("orden_df")
    if isinstance(orden, pd.DataFrame):
        E["orden_df_base"] = orden.copy()
        E["orden_editor_version"] = E.get("orden_editor_version", 0) + 1
    if E.get("paso", 1) > 1 and "lineas" in E:
        tabla = tabla_desde_lineas(E["lineas"])
        E.update(tabla=tabla, tabla_edit=tabla.copy(), tabla_base=tabla.copy(),
                 editor_version=E.get("editor_version", 0) + 1)


def navegar_vista(seccion):
    if seccion != E.get("_vista_renderizada"):
        proteger_edicion_al_navegar()
    E["vista"] = seccion


def mostrar_cotizacion_historial(cot_id):
    navegar_vista(VISTAS[1])
    E.update(_historial_seleccion=cot_id, _filtros_historial=("", "Todos", "Todas las fechas"),
             h_texto="", h_estado="Todos", h_periodo="Todas las fechas", h_vence_hoy=False, _filtro_vence_hoy=False)


def abrir_enlace_guardado():
    cot = st.query_params.get("cotizacion", "")
    producto = st.query_params.get("producto", "")
    for campo in ("cotizacion", "producto"):
        if campo in st.query_params:
            del st.query_params[campo]
    if re.fullmatch(r"[1-9][0-9]{0,17}", cot):
        mostrar_cotizacion_historial(int(cot))
    elif producto and len(producto) <= 100:
        clave = bib.clave_vigente(producto)
        if clave:
            abrir_producto_desde_catalogo(clave)


def refrescar_biblioteca():
    bib.refrescar_si_cambio()
    E["_mensaje_guardado"] = "Biblioteca e historial actualizados."


# ============================================================== PANTALLA
cabecera = st.container(key="cabecera_giovalia", gap="xsmall")
resumen_cabecera = cabecera.empty()
with cabecera.container(key="gv_cabecera_acciones", gap="xsmall"):
    busqueda_cabecera = st.container()
    opciones_ux = experiencia.preferencias(E, identidad, acceso)
    carpeta, bib, hist, api_key, usar_lo, mp_ticket = configuracion_compacta()
    st.button("Actualizar", key="refrescar_compartido", icon=":material/refresh:", on_click=refrescar_biblioteca)
    if GIOVAL_REQUIRE_ACCESS or GIOVAL_PROFILES:
        if st.button("Salir", icon=":material/logout:", key="gioval_cerrar_sesion"):
            if GIOVAL_PROFILES:
                acceso.cerrar_sesion()
            E.clear()
            st.rerun()
abrir_enlace_guardado()
with resumen_cabecera.container():
    visual.topbar(E.get("vista", INICIO), identidad["nombre"] if identidad else "",
                  compartida=GIOVAL_REQUIRE_ACCESS or GIOVAL_PROFILES)

if E.get("_conflicto_guardado"):
    st.error(
        "Esta cotización cambió en otra sesión. Para proteger el trabajo del otro usuario, "
        "Gioval detuvo el guardado de tus cambios. Tu edición sigue abierta en esta pantalla."
    )
    if E.get("_conflicto_guardado_mensaje"):
        st.caption(E["_conflicto_guardado_mensaje"])
    st.warning("Puedes recargar la versión central —descartando los cambios de esta sesión— o guardarlos como una cotización nueva.")
    cc1, cc2 = st.columns(2)
    conflicto_id = E.get("_conflicto_guardado_id") or E.get("cot_id")
    cc1.button(
        "Recargar versión compartida",
        icon=":material/refresh:",
        width="stretch",
        on_click=restaurar_borrador,
        args=(conflicto_id,),
        key="conflicto_recargar",
    )
    cc2.button(
        "Guardar mis cambios como cotización nueva",
        icon=":material/content_copy:",
        type="primary",
        width="stretch",
        on_click=guardar_conflicto_como_nueva,
        key="conflicto_guardar_copia",
    )
if E.get("_mensaje_guardado"):
    st.success(E.pop("_mensaje_guardado"))

if destino := E.pop("_hallazgo_destino", None):
    ir_al_hallazgo(*destino)

etiquetas_vista = {
    INICIO: ":material/space_dashboard: Inicio",
    PRECIOS: ":material/query_stats: Precios",
    VISTAS[0]: ":material/edit_document: Cotizar",
    VISTAS[1]: ":material/history: Historial",
    VISTAS[2]: ":material/inventory_2: Biblioteca",
    VISTAS[3]: ":material/menu_book: Catálogo PDF",
    VISTAS[4]: ":material/monitoring: Gestión comercial",
    VISTAS[5]: ":material/fact_check: Datos pendientes",
}
if codigo := E.pop("_abrir_producto_catalogo", None):
    abrir_producto_desde_catalogo(codigo)
vistas_principales = [INICIO, VISTAS[0], PRECIOS, VISTAS[2], VISTAS[3], VISTAS[1], VISTAS[4], VISTAS[5]]
vista_activa = E.get("vista", INICIO)
if vista_activa not in vistas_principales:
    vista_activa = E["vista"] = INICIO
vistas_visibles = vistas_principales
if opciones_ux["mostrador"]:
    vistas_visibles = [INICIO, VISTAS[0], VISTAS[2], VISTAS[1]]
    if vista_activa not in vistas_visibles:
        vistas_visibles = vistas_visibles + [vista_activa]
    with st.popover("Gestión", icon=":material/settings:"):
        for avanzada in (PRECIOS, *VISTAS[3:]):
            st.button(avanzada, key=f"ux_gestion_{vistas_principales.index(avanzada)}", width="stretch",
                      on_click=navegar_vista, args=(avanzada,))
with st.sidebar:
    visual.marca()
    with st.container(key="gv_nav", gap="xxsmall"):
        for seccion in vistas_visibles:
            icono, _ = etiquetas_vista[seccion].split(" ", 1)
            st.button(seccion, icon=icono, width="stretch", key=f"gv_nav_{vistas_principales.index(seccion)}",
                      type="primary" if vista_activa == seccion else "secondary",
                      on_click=navegar_vista, args=(seccion,))
    if E.get("cot_id"):
        st.divider()
        st.caption("Cotización abierta")
        st.badge(E.get("cot_numero") or (E.get("informe") or {}).get("resumen", {}).get("numero") or "Borrador",
                 icon=":material/draft:", color="blue")
with st.container(key="gv_nav_movil"):
    vista = st.segmented_control(
        "Secciones", vistas_visibles, key="vista", format_func=etiquetas_vista.__getitem__,
        required=True, width="stretch", wrap=True, label_visibility="collapsed",
        on_change=lambda: navegar_vista(E["vista"]),
    )
ruta_visual = (vista, E.get("paso") if vista == VISTAS[0] else None)
if E.get("_ruta_visual") != ruta_visual:
    E["_ruta_visual"] = ruta_visual
    E["_secuencia_visual"] = E.get("_secuencia_visual", 0) + 1
    if not E.get("_destino_visual"):
        with st.container(key="gv_scroll_runtime"):
            visual.inicio_de_seccion(E["_secuencia_visual"])
E["_vista_renderizada"] = vista
with st.container(key="gv_scripts_runtime"):
    st.html(Path(__file__).with_name("atajos_cotizacion.html"), unsafe_allow_javascript=True)
    st.html(Path(__file__).with_name("cambios_linea.html"), unsafe_allow_javascript=True)
    st.html(Path(__file__).with_name("interfaz_foco.html"), unsafe_allow_javascript=True)
st.html(Path(__file__).with_name("interfaz_giovalia.css"))
with busqueda_cabecera:
    mostrar_busqueda_global()
if vista == INICIO:
    with st.container(key="gv_inicio"):
        selector_borradores()
elif vista == PRECIOS:
    vista_precios(bib, hist)
elif vista == VISTAS[1]:
    vista_historial()
elif vista == VISTAS[2]:
    vista_productos()
elif vista == VISTAS[3]:
    cc.render_ui(st, bib, abrir_producto=abrir_producto_catalogo_ui)
elif vista == VISTAS[4]:
    gestion.render(bib, hist, cargar_en_pedido, privado=GIOVAL_REQUIRE_ACCESS,
                   admin=not identidad or identidad["rol"] == "admin", vista_precios=vista_precios)
elif vista == VISTAS[5]:
    vista_pendientes()
elif not E.get("cot_id"):
    with st.container(key="gv_inicio"):
        selector_borradores()
else:
    E.setdefault("paso", 1)
    if E["paso"] > 1 and "lineas" not in E:
        E["paso"] = 1
    mostrar_pasos(E["paso"])
    barra = st.container(key="barra_cotizacion")
    atras_cotizacion, guardar_cotizacion, sigue_cotizacion, total_cotizacion = barra.columns(
        [1.05, 1.05, 1.45, 0.95], vertical_alignment="center"
    )
    {1: paso_1, 2: paso_2, 3: paso_3, 4: paso_4}[E["paso"]]()
    if guardar_cotizacion.button("Guardar borrador", key="guardar_editor", icon=":material/save:", width="stretch",
                                 disabled=bool(E.get("_conflicto_guardado") or E.get("pdf_bytes"))):
        persistir_borrador()
        if not E.get("_conflicto_guardado"):
            st.toast("Borrador guardado")
    total_cotizacion.markdown(f"Total con IVA  \n**{rv.clp(totales_actuales()['total'])}**")

if destino := E.pop("_destino_visual", None):
    selectores = {
        "linea": "#giovalia-detalle-linea", "producto": "#giovalia-detalle-producto",
        "nombre": ".st-key-c_nombre input", "rut": ".st-key-c_rut input",
        "precio": '[aria-label="Precio de la línea seleccionada"]',
        "cantidad": '[aria-label="Cantidad de la línea seleccionada"]',
    }
    selector = html.escape(selectores[destino])
    st.html(f'<span id="gv-destino" hidden data-selector="{selector}" data-movil="{int(destino == "linea")}"></span>')
