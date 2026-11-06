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
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

import buscador as bs
import cotizador as ct
import historial as hs
import lector_ia as li
import mercado_publico as mp
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

# ------------------------------------------------------------- IDENTIDAD VISUAL
# La paleta común se define en .streamlit/config.toml para que local y despliegue coincidan.
st.set_page_config(
    page_title="Gioval · Cotizador comercial",
    page_icon=":material/description:",
    layout="wide",
    initial_sidebar_state="collapsed",
)


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
          "similitud": "Coincidencia catálogo", "descripcion": "Descripción", "": ""}

# ============================================================== CONFIGURACIÓN COMPACTA
# La configuración se renderiza en un popover junto al título, para no ocupar la pantalla.


# ============================================================== FUNCIONES COMUNES
VISTAS = ["Cotizar", "Historial", "Biblioteca"]
OPCIONES_PRECIO = ["Incluyen IVA (lo que paga el cliente)", "Son netos (sin IVA)"]
if E.get("vista") in {"Mis productos", "Catálogo"}:
    E["vista"] = VISTAS[2]
elif E.get("vista") in {"Nueva cotización", "Inicio"}:
    E["vista"] = VISTAS[0]
E.setdefault("vista", VISTAS[0])


def editor(df, **kw):
    """Tabla editable que se estira para mostrar todas sus filas, sin barra de desplazamiento interna."""
    try:
        return st.data_editor(df, height="content", **kw)
    except Exception:  # versiones antiguas de Streamlit: calcula el alto a mano
        extra = 1 if kw.get("num_rows") == "dynamic" else 0
        return st.data_editor(df, height=min(35 * (len(df) + 1 + extra) + 8, 2400), **kw)

def limpiar_estado_trabajo():
    """Reinicia la cotización actual sin cerrar el login ni borrar preferencias útiles."""
    conservar = (
        "gioval_autenticado",
        "config_usar_lo",
        "config_api_key",
        "_mercado_publico_ticket",
        "_respaldo_zip",
        "_respaldo_nombre",
    )
    valores = {clave: E[clave] for clave in conservar if clave in E}
    E.clear()
    E.update(valores)


def guardar_ticket_mp_sesion():
    """Mantiene el ticket disponible al cerrar Ajustes, sin escribirlo en disco."""
    ticket = str(E.get("config_ticket_mercado_publico", "")).strip()
    if ticket:
        E["_mercado_publico_ticket"] = ticket
    else:
        E.pop("_mercado_publico_ticket", None)


def ir_a(paso: int):
    E["paso"] = paso
    persistir_borrador()
    st.rerun()


def mostrar_pasos(actual: int):
    nombres = ["Pedido", "Productos", "Fotos y fichas", "PDF"]
    with st.container(horizontal=True, vertical_alignment="center"):
        for i, nombre in enumerate(nombres, 1):
            if i < actual:
                st.badge(f"{i}. {nombre}", icon=":material/check_circle:", color="green")
            elif i == actual:
                st.badge(f"{i}. {nombre}", icon=":material/edit_note:", color="blue")
            else:
                st.badge(f"{i}. {nombre}", icon=":material/radio_button_unchecked:", color="gray")
    st.progress((actual - 1) / 3, text=f"Etapa {actual} de 4 · {nombres[actual - 1]}")
    if E.get("_ultima_guarda"):
        etiqueta = "biblioteca compartida" if GIOVAL_REQUIRE_ACCESS else "este computador"
        st.caption(f"Guardado en {etiqueta} · {E['_ultima_guarda']}")


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
        if clave:
            resultados[normalizada] = {"clave": clave, "origen": "biblioteca", "certeza": "alta"}
            continue
        candidatos = bib.coincidencias_semanticas(desc, limite=2)
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
                if clave in catalogo_ia and match.get("certeza") in ("alta", "media"):
                    resultados[ct.normalizar(descripcion)] = {
                        "clave": clave, "origen": "ia", "certeza": match["certeza"],
                        "razon": match.get("razon", ""),
                    }
        except Exception as exc:
            E["_error_match_ia"] = str(exc)
    return resultados


def asociar_lineas_con_biblioteca(
    lineas: list[dict], aplicar_precios_catalogo: bool = False
) -> dict[str, dict]:
    """Restaura vínculos guardados y agrega coincidencias altas o probables."""
    pendientes = []
    for linea in lineas:
        descripcion = _texto_seguro(linea.get("descripcion"))
        clave = _texto_seguro(linea.get("clave_auto")).upper()
        ficha = bib.indice.get(clave, {})
        valida = bool(clave in bib.indice and not ct.identidad_conflictiva(clave, ficha))
        codigo_fuente = _texto_seguro(linea.get("codigo_fuente")).upper()
        ficha_codigo = bib.indice.get(codigo_fuente, {})
        codigo_catalogo = bool(codigo_fuente in bib.indice and not ct.identidad_conflictiva(codigo_fuente, ficha_codigo))
        es_producto = bool(linea.get("es_producto", True))
        if es_producto and descripcion and not valida and not codigo_catalogo:
            pendientes.append(descripcion)

    coincidencias = resolver_productos_catalogo(pendientes)
    mapa_orden = {}
    for linea in lineas:
        descripcion = _texto_seguro(linea.get("descripcion"))
        normalizada = ct.normalizar(descripcion)
        if not descripcion or not linea.get("es_producto", True):
            continue

        clave = _texto_seguro(linea.get("clave_auto")).upper()
        ficha = bib.indice.get(clave, {})
        valida = bool(clave in bib.indice and not ct.identidad_conflictiva(clave, ficha))
        match = coincidencias.get(normalizada, {})
        if not valida:
            clave = ""
            codigo_fuente = _texto_seguro(linea.get("codigo_fuente")).upper()
            ficha_codigo = bib.indice.get(codigo_fuente, {})
            codigo_catalogo = bool(
                codigo_fuente in bib.indice
                and not ct.identidad_conflictiva(codigo_fuente, ficha_codigo)
            )
            if codigo_catalogo:
                clave = codigo_fuente
                linea["clave_auto"] = clave
                linea["origen"] = "biblioteca"
            # Coincidencias altas y probables se vinculan; las bajas quedan pendientes.
            elif match.get("certeza") in {"alta", "media"}:
                propuesta = _texto_seguro(match.get("clave")).upper()
                ficha_propuesta = bib.indice.get(propuesta, {})
                if propuesta in bib.indice and not ct.identidad_conflictiva(propuesta, ficha_propuesta):
                    clave = propuesta
                    linea["clave_auto"] = clave
                    linea["origen"] = match.get("origen", "similitud")
            if not clave:
                linea["clave_auto"] = ""
                linea["origen"] = "descripcion"
        else:
            linea["clave_auto"] = clave
            linea["origen"] = linea.get("origen") or "biblioteca"

        if clave and (aplicar_precios_catalogo or (linea.get("unitario") is None and linea.get("precio_neto") is None)):
            precio = bib.precio_sugerido(clave)
            if precio is None:
                precio = bib.indice.get(clave, {}).get("precio_neto")
            if precio is not None:
                linea["unitario"] = precio
                linea["precio_neto"] = precio

        mapa_orden[normalizada] = {
            "clave": clave,
            "origen": linea.get("origen") or ("biblioteca" if clave else "descripcion"),
            "certeza": match.get("certeza", "alta" if clave else "baja"),
        }
    return mapa_orden


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


def cargar(ruta: Path, origen_pedido: str):
    """Lee una cotización y prepara líneas, datos del cliente y precios editables."""
    lineas = ct.leer_cotizacion(ruta)
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
        es_producto = not any(p in ct.normalizar(descripcion).lower() for p in ct.PALABRAS_NO_PRODUCTO)
        match = E.get("_orden_coincidencias", {}).get(ct.normalizar(descripcion), {}) if es_producto else {}
        clave = match.get("clave") or (bib.detectar(descripcion) if es_producto else "")
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
        c.metric("Total", rv.clp(tot["total"]))
        pendientes = [
            ln for ln in E.get("lineas", [])
            if _esta_marcado(ln.get("incluir", ln.get("es_producto", True)), True)
            and ln.get("unitario") is None and ln.get("precio_neto") is None
        ]
        if pendientes:
            st.caption(
                f"Pendiente de precio: {len(pendientes)} línea(s). "
                "El total mostrado no suma los precios que faltan."
            )


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
        "lineas": lineas,
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
            if GIOVAL_REQUIRE_ACCESS:
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
    limpiar_estado_trabajo()
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
    orden_coincidencias = asociar_lineas_con_biblioteca(
        lineas_restauradas, aplicar_precios_catalogo=es_mercado_publico
    )
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
    if isinstance(datos.get("orden_df"), list):
        df = pd.DataFrame(datos["orden_df"])
        for columna, defecto in (("Incluir", True), ("Cantidad", None), ("Producto", ""), ("Precio unitario", None)):
            if columna not in df:
                df[columna] = defecto
        for columna, defecto in (("Código", ""), ("Unidad", ""), ("Origen", "")):
            if columna not in df:
                df[columna] = defecto
        df = df[["Incluir", "Cantidad", "Producto", "Código", "Unidad", "Origen", "Precio unitario"]]
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
                clave = _texto_seguro(ln.get("clave_auto")).upper()
                precio_catalogo = bib.precio_sugerido(clave) if clave in bib.indice else None
                if precio_catalogo is None and clave in bib.indice:
                    precio_catalogo = bib.indice[clave].get("precio_neto")
                usar_catalogo = bool(es_mercado_publico and precio_catalogo is not None)
                if neto is not None and (precio_vacio or usar_catalogo):
                    if usar_catalogo:
                        neto = precio_catalogo
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
    registros = hist.listar()
    borradores = [r for r in registros if r.get("estado") == "Borrador"]
    cotizaciones_aceptadas = sum(
        float(r.get("total") or 0) for r in registros if r.get("estado") == "Aceptada"
    )

    with st.container(border=True):
        titulo, accion = st.columns([3, 1], vertical_alignment="center")
        with titulo:
            st.caption("GIOVAL · COTIZADOR COMERCIAL")
            st.title("Cotiza sin compromiso")
            st.caption("Prepara una propuesta clara con tus productos, precios y fotos guardados en la biblioteca.")
        with accion:
            st.button(
                "Nueva cotización", icon=":material/add:", type="primary",
                width="stretch", on_click=iniciar_borrador, key="inicio_nueva_cotizacion",
            )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Cotizaciones", len(registros), border=True)
    m2.metric("Borradores activos", len(borradores), border=True)
    m3.metric("Monto aceptado", rv.clp(cotizaciones_aceptadas), border=True)
    m4.metric("Productos en biblioteca", len(bib.indice), border=True)

    izq, der = st.columns([1.15, 1])
    with izq:
        with st.container(border=True):
            st.subheader("Retomar un borrador")
            if borradores:
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
                    "Continuar cotización", icon=":material/arrow_forward:", type="primary",
                    width="stretch", on_click=restaurar_borrador, args=(opciones[elegido],),
                    key="inicio_retomar_borrador",
                )
            else:
                st.caption("No tienes borradores pendientes.")
                st.button(
                    "Crear una cotización", icon=":material/add:", type="primary",
                    width="stretch", on_click=iniciar_borrador, key="inicio_comenzar_ahora",
                )
    with der:
        with st.container(border=True):
            st.subheader("Actividad reciente")
            if registros:
                recientes = registros[:5]
                tabla = {
                    "N°": [r.get("numero", "") for r in recientes],
                    "Fecha": [fecha_corta(r.get("fecha") or "") for r in recientes],
                    "Total": [r.get("total") or 0 for r in recientes],
                    "Estado": [r.get("estado") or "Borrador" for r in recientes],
                }
                if not GIOVAL_REQUIRE_ACCESS:
                    tabla["Cliente"] = [r.get("cliente") or "Sin cliente" for r in recientes]
                st.dataframe(
                    pd.DataFrame(tabla), hide_index=True, width="stretch",
                    column_config={"Total": st.column_config.NumberColumn(format="$ %d")},
                    alt="Cotizaciones recientes",
                )
                st.button(
                    "Ver todas las cotizaciones", icon=":material/arrow_forward:",
                    width="stretch", on_click=lambda: E.update(vista=VISTAS[1]), key="inicio_ver_todas",
                )
            else:
                st.caption("Las cotizaciones que prepares aparecerán aquí.")


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
    st.dataframe(
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


def pedido_ui():
    st.caption("El borrador se guarda mientras trabajas. Puedes volver a él desde el inicio.")
    E.setdefault("precios_modo", OPCIONES_PRECIO[0])
    E.setdefault("fuente_pedido", "Lo escribo o pego aquí")
    con_iva = st.segmented_control(
        "Los precios que ingresarás",
        OPCIONES_PRECIO,
        key="precios_modo",
        format_func=lambda valor: "Con IVA" if valor == OPCIONES_PRECIO[0] else "Valores netos",
        required=True,
        width="content",
        persist_state="session",
    ) == OPCIONES_PRECIO[0]
    FUENTES = ["Lo escribo o pego aquí", "Subir Excel o CSV", "Subir PDF o foto", "Mercado Público"]
    etiqueta_fuente = {
        FUENTES[0]: ":material/edit_note: Texto",
        FUENTES[1]: ":material/table_view: Excel o CSV",
        FUENTES[2]: ":material/description: PDF o foto",
        FUENTES[3]: ":material/account_balance: Mercado Público",
    }
    fuente = st.segmented_control(
        "Origen del pedido",
        FUENTES,
        key="fuente_pedido",
        format_func=lambda valor: etiqueta_fuente[valor],
        required=True,
        width="stretch",
        wrap=True,
        persist_state="session",
    )
    E["fuente_orden"] = fuente
    texto = st.text_area(
        "Escribe un producto por línea, con su cantidad adelante",
        placeholder="10 Tóner HP CF283A\n15 Tóner Kyocera TK-3182\n2 Resma carta",
        height=170, key="orden_texto",
    ) if fuente == FUENTES[0] else ""
    archivo_orden, archivos_ia = None, []
    archivos_mp = []
    codigo_mp = ""
    if fuente == FUENTES[1]:
        archivo_orden = st.file_uploader("Sube el Excel o CSV del pedido", type=["xlsx", "csv"], key="orden_archivo")
    elif fuente == FUENTES[2]:
        archivos_ia = st.file_uploader("Sube el PDF o las fotos del pedido (puedes subir varias)",
                                       type=["pdf", "jpg", "jpeg", "png", "webp"],
                                       accept_multiple_files=True, key="orden_ia")
        st.caption("El pedido se envía a OpenAI para leerlo. Después revisa que haya quedado bien.")
        if not api_key:
            st.warning("Para leer PDF o fotos necesito la clave de OpenAI (Ajustes, arriba a la derecha).")
    elif fuente == FUENTES[3]:
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

    etiqueta_leer = "Consultar y cargar productos" if fuente == FUENTES[3] else "Leer el pedido"
    if st.button(etiqueta_leer, icon=":material/description:", type="primary", width="stretch"):
        try:
            if fuente == FUENTES[3]:
                if not codigo_mp:
                    raise ValueError("Ingresa el código de la Compra Ágil o de la orden de compra.")
                with st.spinner("Consultando Mercado Público…"):
                    detalle_mp = mp.consultar_codigo(codigo_mp, mp_ticket)
                E["_mercado_publico_detalle"] = detalle_mp
                E["_mercado_publico_codigo"] = codigo_mp
                completar_comprador_mercado_publico(detalle_mp)
                filas = list(detalle_mp.get("productos", []))
                if archivos_mp:
                    if any(Path(a.name).suffix.lower() in {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp"}
                           for a in archivos_mp) and not api_key:
                        raise ValueError("Para leer PDF, Word o imágenes necesitas configurar OpenAI en Ajustes. Excel y CSV no requieren esa clave.")
                    with st.spinner("Leyendo los adjuntos y contrastando productos…"):
                        filas.extend(mp.leer_adjuntos(archivos_mp, api_key))
            elif fuente == FUENTES[2]:
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
            if fuente == FUENTES[3] and E.get("_mercado_publico_detalle"):
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
                if codigo_catalogo in bib.indice and not ct.identidad_conflictiva(codigo_catalogo, ficha):
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
                clave = match.get("clave") or bib.detectar(fila["descripcion"], solo_conocidos=True)
                neto = bib.precio_sugerido(clave) or bib.indice.get(clave, {}).get("precio_neto")
                precios.append(ct.precio_para_mostrar(neto, con_iva) if neto else None)
            E["orden_df"] = pd.DataFrame({
                "Incluir": [True] * len(filas),
                "Cantidad": [fila["cantidad"] for fila in filas],
                "Producto": [fila["descripcion"] for fila in filas],
                "Código": [fila.get("codigo", "") for fila in filas],
                "Unidad": [fila.get("unidad", "") for fila in filas],
                "Origen": [fila.get("origen", "Pedido ingresado") for fila in filas],
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
            if fuente == FUENTES[3] and not E.get("_mercado_publico_detalle", {}).get("productos"):
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
        st.info("Escribe o carga un pedido para comenzar. Tus datos de cliente y el número se guardan en el borrador.")
        persistir_borrador()
        return

    st.divider()
    st.subheader("Productos y precios")
    st.caption("Puedes cambiar cantidades, nombres y precios, agregar filas y dejar precios pendientes. Los totales se actualizan antes de avanzar.")
    if fuente == FUENTES[3] and E.get("_mercado_publico_detalle"):
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
                [{"cantidad": ln["cantidad"], "descripcion": ln["descripcion"], "precio_neto": ln.get("unitario"),
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
                "origen_pedido": "pedido", "paso": 2,
                "fuente_pedido": E.get("fuente_pedido", E.get("fuente_orden", "")),
                "fuente_orden": E.get("fuente_orden", ""),
                "orden_texto": E.get("orden_texto", ""),
                "mercado_publico": datos_mercado_publico_guardables(),
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
    st.title("Prepara tu cotización", icon=":material/receipt_long:")
    st.caption("Inicia con un pedido, una solicitud de Mercado Público o una cotización en Excel.")
    if not api_key:
        st.info(
            "Configura la clave de OpenAI en Ajustes para leer pedidos en PDF o imagen y mejorar la identificación de productos.",
            icon=":material/description:",
        )
    E.setdefault("tipo_entrada_cotizacion", "Pedido nuevo")
    opcion_inicio = st.segmented_control(
        "Punto de partida",
        ["Pedido nuevo", "Cotización en Excel"],
        key="tipo_entrada_cotizacion",
        format_func=lambda valor: (
            ":material/playlist_add: Pedido nuevo"
            if valor == "Pedido nuevo" else ":material/description: Cotización en Excel"
        ),
        required=True,
        width="stretch",
        persist_state="session",
    )
    if opcion_inicio == "Pedido nuevo":
        pedido_ui()
    else:
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
def revisar_asociaciones_catalogo():
    """Permite vincular manualmente las líneas que no tuvieron una coincidencia suficiente."""
    pendientes = productos_pendientes_de_asociar(E.get("lineas", []))
    E["_productos_catalogo_revision"] = pendientes
    if not pendientes:
        return

    st.warning(
        f"{len(pendientes)} producto(s) aún no tienen un vínculo seguro. "
        "Asócialos con la biblioteca para recuperar sus precios y archivos guardados.",
        title="Revisar coincidencias de productos",
        icon=":material/link_off:",
    )
    productos = {
        f"{clave} · {_texto_seguro(ficha.get('nombre') or clave)}": clave
        for clave, ficha in sorted(bib.indice.items())
        if not ct.identidad_conflictiva(clave, ficha)
    }
    if not productos:
        return
    opciones = ["Dejar pendiente"] + list(productos)
    with st.expander("Vincular con productos guardados", icon=":material/link:", expanded=True):
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


def paso_2():
    st.title("Productos y precios", icon=":material/inventory_2:")
    st.caption("Revisa la descripción, vinculación con biblioteca, cantidades y precios antes de continuar.")
    lineas = [ln for ln in E.get("lineas", []) if ln.get("es_producto", True)]
    sin_vinculo = productos_pendientes_de_asociar(lineas)
    vinculados = sum(bool(_texto_seguro(ln.get("clave_auto"))) for ln in lineas)
    precio_pendiente = sum(
        _esta_marcado(ln.get("incluir"), True)
        and ln.get("unitario") is None and ln.get("precio_neto") is None
        for ln in lineas
    )
    indicadores = st.columns(4)
    indicadores[0].metric("Productos del pedido", len(lineas), border=True)
    indicadores[1].metric("Vinculados a biblioteca", vinculados, border=True)
    indicadores[2].metric("Sin vínculo", len(sin_vinculo), border=True)
    indicadores[3].metric("Precio por completar", precio_pendiente, border=True)
    if lineas and not sin_vinculo:
        st.caption("Los vínculos guardados permiten recuperar los precios, fotos y fichas de cada producto.")
    if not api_key:
        st.info(
            "La clave de OpenAI mejora la identificación de marca, modelo y código.",
            icon=":material/description:",
        )

    agregar_desde_catalogo()
    editor_productos()
    revisar_asociaciones_catalogo()
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


def guardar_candidato(clave: str, tipo: str, c: dict):
    bib.guardar_archivo(clave, tipo, "x" + c["ext"] if tipo == "foto" else "f.pdf", c["datos"])
    E.pop(f"cand_{tipo}_{clave}", None)
    st.rerun()


def tarjeta(clave: str, desc: str):
    foto, ficha = bib.archivos(clave)
    ficha_no_requerida = bool(bib.indice.get(clave, {}).get("ficha_no_requerida", False))
    aviso_ficha = ""
    if ficha:
        n = menciones_guardada(str(ficha), ficha.stat().st_mtime, clave) if bs.es_codigo(clave) else -2
        estado_ficha = "Revisar" if n == 0 else "Disponible"
        if n == 0:
            aviso_ficha = (f"La ficha guardada no menciona el código «{clave}». Puede ser de otro producto: "
                           "busca otra o revísala.")
    else:
        estado_ficha = "No requerida" if ficha_no_requerida else "Falta"
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
                    color="green" if ficha or ficha_no_requerida else "orange",
                )
            nueva_no_requerida = st.checkbox(
                "Este producto no requiere ficha técnica",
                value=ficha_no_requerida,
                key=f"ficha_no_requerida_{clave}",
                help="Útil para artículos de librería u otros productos sin ficha técnica. Seguirá incluyendo la foto si existe.",
            )
            if nueva_no_requerida != ficha_no_requerida:
                bib.actualizar(clave, ficha_no_requerida=nueva_no_requerida)
                st.rerun()
            b1, b2 = st.columns(2)
            sug_foto, sug_ficha = consultas_sugeridas(clave, desc)
            q_foto, q_ficha = E.get(f"qf_{clave}", sug_foto), E.get(f"qd_{clave}", sug_ficha)
            if b1.button("Buscar archivos faltantes", key=f"b_{clave}", width="stretch"):
                with st.spinner("Buscando…"):
                    for aviso in buscar_para(clave, q_foto, q_ficha, fotos=not bool(foto), fichas=not bool(ficha)):
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
        if st.button("← Volver"):
            ir_a(2)
        return
    for clave in claves:
        for tipo in ("foto", "ficha"):
            subido = E.get(f"{tipo}_{clave}")
            if subido:
                bib.guardar_archivo(clave, tipo, subido.name, subido.getvalue())

    def archivos_necesarios(clave):
        foto, ficha = bib.archivos(clave)
        no_requerida = bool(bib.indice.get(clave, {}).get("ficha_no_requerida", False))
        return bool(foto and (ficha or no_requerida))

    listos = sum(1 for clave in claves if archivos_necesarios(clave))
    st.progress(
        listos / len(claves),
        text=f"{listos} de {len(claves)} productos listos · foto y ficha, o ficha marcada como no requerida",
    )
    faltan = []
    for clave in claves:
        foto, ficha = bib.archivos(clave)
        no_requerida = bool(bib.indice.get(clave, {}).get("ficha_no_requerida", False))
        if not foto or (not ficha and not no_requerida):
            faltan.append(clave)
    if faltan:
        st.caption("Primero se buscan los archivos que faltan; después puedes revisar cada resultado y confirmar si corresponde.")
        if st.button(f"Buscar fotos y fichas pendientes ({len(faltan)})", icon=":material/search:", type="primary", width="stretch"):
            persistir_borrador()
            barra = st.progress(0.0)
            estado_busqueda = st.empty()
            estado_busqueda.caption("Buscando…")
            hubo_error = False
            for i, clave in enumerate(faltan):
                foto, ficha = bib.archivos(clave)
                no_requerida = bool(bib.indice.get(clave, {}).get("ficha_no_requerida", False))
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
    estado_anexos = c_estado.selectbox(
        "Mostrar",
        ["Todos", "Incompletos", "Sin foto", "Sin ficha pendiente", "Ficha no requerida", "Completos"],
        key="filtro_anexos_estado",
    )
    consulta_anexos = busqueda_anexos.strip().casefold()
    visibles = []
    for clave in claves:
        foto, ficha = bib.archivos(clave)
        no_requerida = bool(bib.indice.get(clave, {}).get("ficha_no_requerida", False))
        completo = bool(foto and (ficha or no_requerida))
        if consulta_anexos and consulta_anexos not in (clave + " " + descripciones[clave]).casefold():
            continue
        if estado_anexos == "Incompletos" and completo:
            continue
        if estado_anexos == "Sin foto" and foto:
            continue
        if estado_anexos == "Sin ficha pendiente" and (ficha or no_requerida):
            continue
        if estado_anexos == "Ficha no requerida" and not no_requerida:
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
    """Muestra errores y avisos antes de crear el PDF; devuelve errores bloqueantes."""
    try:
        hallazgos_actuales = hallazgos()
    except Exception as exc:
        st.warning(f"No pude terminar la revisión automática: {exc}")
        return 0
    errores = [h["texto"] for h in hallazgos_actuales if h["nivel"] == "error"]
    avisos = [h["texto"] for h in hallazgos_actuales if h["nivel"] == "aviso"]
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
            for problema in errores:
                st.markdown(f"- {problema}")
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
            for aviso in avisos:
                st.markdown(f"- {aviso}")
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
        registro_nuevo = hist.obtener(cot_id)
        E["_cotizacion_version"] = registro_nuevo.get("actualizado") if registro_nuevo else None


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
    st.title("Revisa y crea tu PDF", icon=":material/picture_as_pdf:")

    if "pdf_bytes" not in E:
        st.write("Esta es la última oportunidad para corregir los productos, cantidades, códigos, precios y datos del cliente antes de crear el PDF.")
        editor_productos()
        boton_actualizar_totales()
        mostrar_totales()
        mostrar_resumen_lineas()
        editar_cabecera()

        claves, _ = datos_productos()
        con_foto = sum(1 for clave in claves if bib.archivos(clave)[0])
        con_ficha = sum(1 for clave in claves if bib.archivos(clave)[1])
        sin_ficha_requerida = sum(
            1 for clave in claves
            if not bib.archivos(clave)[1]
            and not bib.indice.get(clave, {}).get("ficha_no_requerida", False)
        )
        ficha_no_requerida = sum(
            1 for clave in claves if bib.indice.get(clave, {}).get("ficha_no_requerida", False)
        )
        st.write(
            f"La cotización tiene **{len(E.get('lineas', []))} líneas**. "
            f"Fotos: **{con_foto}** de {len(claves)} · "
            f"Fichas disponibles: **{con_ficha}** · "
            f"Fichas pendientes: **{sin_ficha_requerida}** · "
            f"No requeridas: **{ficha_no_requerida}**."
        )
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
        nueva.button("Hacer otra cotización", width="stretch", on_click=limpiar_estado_trabajo)
    else:
        nueva = st.button("Hacer otra cotización", width="stretch", on_click=limpiar_estado_trabajo)


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
    mercado_publico = referencia_mercado_publico_borrador(rec, datos)
    mp_codigo = _texto_seguro(mercado_publico.get("codigo"))
    fuente_guardada = datos.get("fuente_pedido", datos.get("fuente_orden", ""))
    es_mercado_publico = bool(mp_codigo or fuente_guardada == "Mercado Público")
    orden_coincidencias = asociar_lineas_con_biblioteca(
        lineas, aplicar_precios_catalogo=es_mercado_publico
    )
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
            if (ln.get("unitario") is not None or ln.get("precio_neto") is not None) else None for ln in lineas]})
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
    E.pop("_conflicto_guardado", None)
    E.pop("_conflicto_guardado_id", None)
    E.pop("_conflicto_guardado_mensaje", None)
    E["paso"], E["vista"] = 1, VISTAS[0]
    persistir_borrador()


def historial_cotizaciones():
    E.setdefault("h_ver", 0)
    todos = hist.listar()
    if not todos:
        st.info("Todavía no hay cotizaciones. Cada vez que crees una, quedará guardada aquí con su número.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Cotizaciones", len(todos), border=True)
    m2.metric("Monto cotizado", rv.clp(sum(r["total"] or 0 for r in todos)), border=True)
    m3.metric("Monto aceptado", rv.clp(sum(r["total"] or 0 for r in todos if r["estado"] == "Aceptada")), border=True)
    f1, f2, f3 = st.columns([2.5, 1, 1.4])
    texto = f1.text_input(
        "Buscar",
        placeholder="Número de cotización" if GIOVAL_REQUIRE_ACCESS else "Número, cliente, RUT o notas",
        key="h_texto",
    )
    estado = f2.selectbox("Estado", ["Todos"] + hs.ESTADOS, key="h_estado")
    periodo = f3.selectbox(
        "Período", ["Todas las fechas", "Últimos 30 días", "Últimos 90 días", "Este año"],
        key="h_periodo",
    )
    regs = hist.listar(texto, estado)
    hoy = date.today()
    if periodo == "Últimos 30 días":
        minimo = hoy - timedelta(days=30)
        regs = [r for r in regs if str(r.get("fecha") or "")[:10] >= minimo.isoformat()]
    elif periodo == "Últimos 90 días":
        minimo = hoy - timedelta(days=90)
        regs = [r for r in regs if str(r.get("fecha") or "")[:10] >= minimo.isoformat()]
    elif periodo == "Este año":
        regs = [r for r in regs if str(r.get("fecha") or "")[:4] == str(hoy.year)]
    st.caption(f"Mostrando {len(regs)} cotización(es) con los filtros seleccionados.")
    if not regs:
        st.info("No encontré cotizaciones con esos filtros. Cambia el estado, período o búsqueda.")
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
    st.title("Cotizaciones", icon=":material/receipt_long:")
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
            bib.registrar(clave_nueva, nombre=nuevo_nom.strip() or clave_nueva)
            E["p_ver"] += 1
            st.success(f"Producto {clave_nueva} agregado al catálogo.")
            st.rerun()

    todas = bib.filas_catalogo()
    if not todas:
        st.info("Todavía no hay productos. Agrega uno aquí o se irán incorporando al preparar cotizaciones.")
        return

    total_sin_foto = sum(not f["foto"] for f in todas)
    total_sin_ficha = sum(not f["ficha"] and not f.get("ficha_no_requerida", False) for f in todas)
    total_sin_precio = sum(f.get("precio_neto") is None for f in todas)
    total_margen_pendiente = sum(f.get("costo_neto") is None or f.get("margen_pct") is None for f in todas)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Productos", len(todas), border=True)
    m2.metric("Sin foto", total_sin_foto, border=True)
    m3.metric("Sin ficha pendiente", total_sin_ficha, border=True)
    m4.metric("Sin precio reciente", total_sin_precio, border=True)

    f1, f2 = st.columns([1.5, 1])
    texto = f1.text_input("Buscar producto", placeholder="Código, modelo, marca o nombre", key="p_texto")
    filtro = f2.selectbox(
        "Filtrar por estado",
        ["Todos", "Incompletos", "Sin foto", "Sin ficha pendiente", "Sin precio",
         "Margen pendiente", "Ficha no requerida"],
        key="p_filtro_estado",
    )
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
    configuracion_columnas["Código"] = st.column_config.TextColumn(width="medium")
    configuracion_columnas["Producto"] = st.column_config.TextColumn(width="large")
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

    st.divider()
    st.subheader("Detalle del producto")
    codigos = [f["clave"] for f in filas]
    elegido = st.selectbox("Selecciona un producto", codigos, key="p_sel")
    producto_elegido = next(f for f in filas if f["clave"] == elegido)
    tarjeta(elegido, producto_elegido["nombre"])
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
    """Muestra opciones secundarias bajo demanda y devuelve los servicios activos de la app."""
    carpeta_compartida = str((APP_DIR / "biblioteca").resolve())
    if not GIOVAL_REQUIRE_ACCESS:
        E.setdefault("config_carpeta", carpeta_compartida)
    api_key, origen_key = li.buscar_api_key(APP_DIR)
    ticket_mp, origen_ticket_mp = mp.buscar_ticket(APP_DIR)
    if not ticket_mp:
        ticket_mp = str(
            E.get("_mercado_publico_ticket")
            or E.get("config_ticket_mercado_publico", "")
        ).strip()
        if ticket_mp:
            origen_ticket_mp = "esta sesión"

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
                datos_recientes = {
                    "N° cotización": [registro.get("numero", "") for registro in recientes],
                    "Fecha": [fecha_corta(registro.get("fecha") or "") for registro in recientes],
                    "Total": [registro.get("total") or 0 for registro in recientes],
                    "Estado": [registro.get("estado") or "Borrador" for registro in recientes],
                }
                if not GIOVAL_REQUIRE_ACCESS:
                    datos_recientes["Cliente"] = [
                        registro.get("cliente") or "Sin cliente" for registro in recientes
                    ]
                df_recientes = pd.DataFrame(datos_recientes)
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
                try:
                    li.guardar_api_key(APP_DIR, api_key)
                    E["config_api_key"] = api_key.strip()
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

        st.divider()
        st.button("Volver a los borradores", icon=":material/home:", key="settings_volver_inicio", width="stretch", on_click=limpiar_estado_trabajo)

    # El contenido del popover se calcula también cuando está cerrado; reutilizamos sus servicios
    # para evitar abrir dos instancias del catálogo y del historial en cada ejecución.
    api_key_actual, _ = li.buscar_api_key(APP_DIR)
    if not api_key_actual:
        api_key_actual = E.get("config_api_key", "")
    usar_lo = bool(E.get("config_usar_lo", False))
    ticket_mp_actual, _ = mp.buscar_ticket(APP_DIR)
    if not ticket_mp_actual:
        ticket_mp_actual = str(
            E.get("_mercado_publico_ticket")
            or E.get("config_ticket_mercado_publico", "")
        ).strip()
    return carpeta, bib_panel, hist_panel, api_key_actual, usar_lo, ticket_mp_actual


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


# ============================================================== PANTALLA
# Marca y navegación compactas para priorizar el trabajo del cotizador.
if GIOVAL_REQUIRE_ACCESS:
    col_marca, col_estado, col_salir, col_ajustes = st.columns(
        [3.5, 2, 1, 1], vertical_alignment="center"
    )
else:
    col_marca, col_estado, col_ajustes = st.columns([4, 2, 1], vertical_alignment="center")
with col_marca:
    st.markdown("## Gioval")
    st.caption("Tintas, tóners y artículos de oficina · Arica, Chile")
with col_estado:
    st.badge(
        "Biblioteca compartida" if GIOVAL_REQUIRE_ACCESS else "Cotizador comercial",
        icon=":material/verified_user:" if GIOVAL_REQUIRE_ACCESS else ":material/receipt_long:",
        color="blue",
    )
with col_ajustes:
    carpeta, bib, hist, api_key, usar_lo, mp_ticket = configuracion_compacta()
if GIOVAL_REQUIRE_ACCESS:
    with col_salir:
        if st.button("Salir", icon=":material/logout:", width="stretch", key="gioval_cerrar_sesion"):
            E.pop("gioval_autenticado", None)
            st.rerun()

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

etiquetas_vista = {
    VISTAS[0]: ":material/edit_document: Cotizar",
    VISTAS[1]: ":material/history: Historial",
    VISTAS[2]: ":material/inventory_2: Biblioteca",
}
vista = st.segmented_control(
    "Secciones",
    VISTAS,
    key="vista",
    format_func=lambda valor: etiquetas_vista[valor],
    required=True,
    width="stretch",
    label_visibility="collapsed",
)
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
