"""Transformaciones entre líneas, pedidos y tablas de cotización."""
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path
from uuid import uuid4

import pandas as pd

import cotizador as ct
import revisor as rv


COL_INCLUIR, COL_CANT, COL_PROD, COL_COD, COL_ORIGEN = (
    "Incluir", "Cantidad", "Producto", "Código", "¿Cómo lo identifiqué?")
COL_PRECIO = "Precio unitario"
COL_IDENTIDAD = "__gv_linea"
COL_UNIDAD = "Unidad"
COL_POSICION = "Orden"
COL_ANEXOS = "anexos_cotizacion"
ORIGEN = {"biblioteca": "Catálogo", "ia": "IA", "texto": "Texto del pedido",
          "similitud": "Coincidencia catálogo", "descripcion": "Descripción", "": ""}


def esta_marcado(valor, defecto=False) -> bool:
    try:
        if pd.isna(valor):
            return defecto
    except (TypeError, ValueError):
        pass
    return bool(valor)


def texto_seguro(valor) -> str:
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    return str(valor or "").strip()


def codigo_cotizacion(linea: dict) -> str:
    return texto_seguro(linea.get("codigo_cotizacion", linea.get("codigo_fuente") or linea.get("clave_auto")))


def tabla_desde_lineas(lineas: list[dict], con_iva: bool) -> pd.DataFrame:
    """Crea la tabla editable a partir de las líneas guardadas del borrador."""
    return pd.DataFrame({
        COL_INCLUIR: pd.Series([ln.get("incluir", ln.get("es_producto", True)) for ln in lineas], dtype="bool"),
        COL_POSICION: pd.Series(range(1, len(lineas) + 1), dtype="int64"),
        COL_CANT: pd.Series([ln.get("cantidad", 1) for ln in lineas], dtype="float64"),
        COL_PROD: pd.Series([ln.get("descripcion", "") for ln in lineas], dtype=object),
        COL_COD: pd.Series([codigo_cotizacion(ln) for ln in lineas], dtype=object),
        COL_UNIDAD: pd.Series([ln.get("unidad_fuente", "") for ln in lineas], dtype=object),
        COL_PRECIO: [ct.precio_para_mostrar(
            ln.get("unitario") if ln.get("unitario") is not None else ln.get("precio_neto"), con_iva)
            if ln.get("unitario") is not None or ln.get("precio_neto") is not None else None for ln in lineas],
        COL_ORIGEN: [ORIGEN.get(ln.get("origen", ""), "") for ln in lineas],
        COL_IDENTIDAD: pd.Series([ln[COL_IDENTIDAD] for ln in lineas], dtype=object),
    })


def orden_desde_lineas(lineas: list[dict], con_iva: bool) -> pd.DataFrame:
    """Conserva la referencia del pedido al volver a editar sus productos."""
    tabla = tabla_desde_lineas(lineas, con_iva)
    return tabla.drop(columns=COL_ORIGEN).assign(Origen=[ln.get("fuente_pedido", "") for ln in lineas])


def identificar_lineas(lineas: list[dict]) -> None:
    for linea in lineas:
        if not linea.get(COL_IDENTIDAD):
            linea[COL_IDENTIDAD] = uuid4().hex


def lineas_desde_editor(editada: pd.DataFrame, anteriores: dict, con_iva: bool,
                        ids_nuevos: dict) -> list[dict]:
    """Aplica los valores visibles sin perder los metadatos de las líneas existentes."""
    nuevas = []
    filas = editada.sort_values(COL_POSICION, kind="stable", na_position="last")
    for i, (indice, fila) in enumerate(filas.iterrows()):
        valor_descripcion = fila.get(COL_PROD)
        descripcion = "" if pd.isna(valor_descripcion) else str(valor_descripcion)
        cantidad = fila.get(COL_CANT)
        try:
            cantidad = 1.0 if pd.isna(cantidad) else float(cantidad)
        except (TypeError, ValueError):
            continue
        if not descripcion.strip() or not isfinite(cantidad) or cantidad <= 0:
            continue
        identidad = texto_seguro(fila.get(COL_IDENTIDAD))
        if not identidad:
            if indice not in ids_nuevos:
                ids_nuevos[indice] = uuid4().hex
            identidad = ids_nuevos[indice]
        previa = anteriores.get(identidad, {})
        precio_ui = fila.get(COL_PRECIO)
        try:
            importe = None if pd.isna(precio_ui) else float(precio_ui)
            previo = precio_neto(previa)
            if importe is None or not isfinite(importe) or importe < 0:
                neto = None
            elif previo is not None and ct.precio_para_mostrar(previo, con_iva) == importe:
                neto = previo
            else:
                neto = ct.neto_desde(importe, con_iva)
        except (TypeError, ValueError):
            neto = None
        incluir = esta_marcado(fila.get(COL_INCLUIR), True)
        codigo = texto_seguro(fila.get(COL_COD))
        nuevas.append({
            **previa,
            COL_IDENTIDAD: identidad,
            "fila": previa.get("fila", i + 1),
            "cantidad": ct._cantidad(cantidad),
            "descripcion": descripcion,
            "unitario": neto,
            "precio_neto": neto,
            "es_producto": previa.get("es_producto", True) or incluir,
            "clave_auto": previa.get("clave_auto", ""),
            "origen": previa.get("origen", "descripcion"),
            "incluir": incluir,
            "codigo_cotizacion": codigo,
            "codigo_fuente": previa.get("codigo_fuente", codigo),
            "unidad_fuente": texto_seguro(fila.get(COL_UNIDAD)),
            "fuente_pedido": previa.get("fuente_pedido", texto_seguro(fila.get("Origen"))),
        })
    return nuevas


def subtotales(tabla: pd.DataFrame, con_iva: bool) -> pd.DataFrame:
    """Devuelve subtotales en el modo de precio que muestra el editor."""
    etiqueta = "con IVA" if con_iva else "neto"
    filas = []
    for _, fila in tabla.iterrows():
        descripcion = texto_seguro(fila.get(COL_PROD))
        if not descripcion:
            continue
        incluir = esta_marcado(fila.get(COL_INCLUIR), True)
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
            "Código": texto_seguro(fila.get(COL_COD)),
            f"Precio unitario ({etiqueta})": precio,
            f"Subtotal ({etiqueta})": subtotal,
        })
    return pd.DataFrame(filas)


def precio_neto(linea: dict):
    unitario = linea.get("unitario")
    return unitario if unitario is not None else linea.get("precio_neto")


def lineas_incluidas(lineas: list[dict]) -> list[dict]:
    return [linea for linea in lineas
            if esta_marcado(linea.get("incluir", linea.get("es_producto", True)), True)]


def resumen_precios(lineas: list[dict]) -> tuple[dict, int, int]:
    """Calcula totales, líneas incluidas y pendientes con una sola regla de inclusión."""
    incluidas = [
        {"cantidad": linea.get("cantidad", 0), "precio_neto": precio_neto(linea)}
        for linea in lineas_incluidas(lineas)
    ]
    return rv.totales_de(incluidas), len(incluidas), sum(ln["precio_neto"] is None for ln in incluidas)


def datos_revision(lineas: list[dict], cliente: dict) -> tuple[dict, list[str]]:
    """Prepara la revisión con los mismos importes y cliente que tendrá el Excel regenerado."""
    incluidas = lineas_incluidas(lineas)
    productos = [
        {"cantidad": ln.get("cantidad", 1), "descripcion": ln.get("descripcion", ""),
         "unitario": precio_neto(ln)}
        for ln in incluidas
    ]
    totales = rv.totales_de([{"cantidad": p["cantidad"], "precio_neto": p["unitario"]} for p in productos])
    fecha = cliente.get("fecha")
    if isinstance(fecha, date):
        fecha = fecha.strftime("%d/%m/%Y")
    revision = {
        "lineas": productos,
        "totales": [("Total Neto", totales["neto"]), ("IVA", totales["iva"]), ("Total", totales["total"])],
        "izquierda": [("Nombre", cliente.get("nombre", "")), ("RUT", cliente.get("rut", ""))],
        "derecha": [("Fecha", fecha)],
    }
    return revision, [texto_seguro(ln.get("clave_auto")).upper() for ln in incluidas]


@dataclass(frozen=True)
class AnexosProducto:
    foto: Path | None
    ficha: Path | None
    ficha_opcional: bool
    foto_omitida: bool = False
    ficha_omitida: bool = False

    @property
    def falta_ficha(self) -> bool:
        return not self.ficha and not self.ficha_opcional and not self.ficha_omitida

    @property
    def completo(self) -> bool:
        return (bool(self.foto) or self.foto_omitida) and not self.falta_ficha


def filtrar_anexos(anexos: dict[str, AnexosProducto], descripciones: dict[str, str],
                   texto: str, filtro: str) -> list[str]:
    """Devuelve las claves visibles manteniendo el orden de la cotización."""
    consulta = texto.strip().casefold()
    criterios = {
        "Por revisar": lambda a: not a.completo,
        "Sin foto": lambda a: not a.foto and not a.foto_omitida,
        "Sin ficha": lambda a: a.falta_ficha,
        "Ficha opcional": lambda a: a.ficha_opcional,
        "Listos": lambda a: a.completo,
    }
    coincide = criterios.get(filtro, lambda _: True)
    return [
        clave for clave, archivos in anexos.items()
        if consulta in f"{clave} {descripciones[clave]}".casefold() and coincide(archivos)
    ]
