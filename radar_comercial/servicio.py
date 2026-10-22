"""Servicios para Radar Comercial Gioval.

La aplicación reutiliza la biblioteca existente de cotizador.py e historial.py.
Las oportunidades y sus evaluaciones viven en una base SQLite separada. El ticket
de ChileCompra solo se usa en memoria para realizar solicitudes HTTP.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sqlite3
import tempfile
import unicodedata
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import requests

API_BASE = "https://api2.mercadopublico.cl"
API_LIST_URL = f"{API_BASE}/v2/compra-agil"
PORTAL_URL = "https://www.mercadopublico.cl/"
ESTADOS_API = {
    "publicada": "Publicada (recibiendo cotizaciones)",
    "proveedor_seleccionado": "Proveedor seleccionado",
    "cerrada": "Cerrada",
    "desierta": "Desierta",
    "cancelada": "Cancelada",
}
ESTADOS_SEGUIMIENTO = (
    "Nueva", "En evaluación", "Cotizar", "Presentada",
    "Adjudicada", "Perdida", "No participar",
)
REGIONES = {
    1: "Tarapacá", 2: "Antofagasta", 3: "Atacama", 4: "Coquimbo",
    5: "Valparaíso", 6: "O'Higgins", 7: "Maule", 8: "Biobío",
    9: "Araucanía", 10: "Los Lagos", 11: "Aysén",
    12: "Magallanes y Antártica", 13: "Metropolitana",
    14: "Los Ríos", 15: "Arica y Parinacota", 16: "Ñuble",
}
PALABRAS_BUSQUEDA_DEFAULT = (
    "toner", "tinta", "resma", "papel", "articulos de oficina",
    "libreria", "impresora",
)
STOPWORDS = {
    "de", "del", "la", "las", "el", "los", "y", "para", "con", "sin",
    "por", "un", "una", "uno", "al", "en", "a", "the", "and", "of",
    "producto", "productos", "unidad", "unidades", "articulo", "articulos",
}


class APICompraAgilError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def ahora_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def numero_seguro(valor, defecto=None):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return defecto
    if not math.isfinite(n):
        return defecto
    return n


def normalizar_texto(valor) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.casefold()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def tokens_relevantes(valor) -> set[str]:
    return {
        t for t in normalizar_texto(valor).split()
        if len(t) >= 2 and t not in STOPWORDS
    }


def normalizar_detalle(detalle: dict | None) -> dict:
    """Conserva datos comerciales públicos; excluye datos de proveedores y otros terceros."""
    if not isinstance(detalle, dict) or not detalle:
        return {}
    relevante = (
        detalle.get("codigo") or detalle.get("nombre") or detalle.get("descripcion")
        or detalle.get("productos_solicitados") or detalle.get("institucion")
        or detalle.get("presupuesto") or detalle.get("fechas") or detalle.get("documentos")
    )
    if not relevante:
        return {}
    estado = detalle.get("estado") or {}
    convocatoria = detalle.get("convocatoria") or {}
    institucion = detalle.get("institucion") or {}
    fechas = detalle.get("fechas") or {}
    presupuesto = detalle.get("presupuesto") or {}
    montos = detalle.get("montos") or {}
    entrega = detalle.get("entrega") or {}
    items = []
    for fila in detalle.get("productos_solicitados") or []:
        if not isinstance(fila, dict):
            continue
        items.append({
            "codigo_producto": str(fila.get("codigo_producto") or ""),
            "nombre": str(fila.get("nombre") or ""),
            "descripcion": str(fila.get("descripcion") or ""),
            "cantidad": numero_seguro(fila.get("cantidad"), 1),
            "unidad_medida": str(fila.get("unidad_medida") or ""),
        })
    docs = []
    for doc in detalle.get("documentos") or []:
        if isinstance(doc, dict):
            docs.append({
                "id": str(doc.get("id") or ""),
                "nombre": str(doc.get("nombre") or ""),
            })
    return {
        "codigo": str(detalle.get("codigo") or ""),
        "nombre": str(detalle.get("nombre") or ""),
        "descripcion": str(detalle.get("descripcion") or ""),
        "estado": {
            "codigo": str(estado.get("codigo") or ""),
            "glosa": str(estado.get("glosa") or ""),
        },
        "convocatoria": {
            "estado_convocatoria": numero_seguro(convocatoria.get("estado_convocatoria")),
            "descripcion": str(convocatoria.get("descripcion") or ""),
        },
        "fechas": {
            "fecha_publicacion": str(fechas.get("fecha_publicacion") or ""),
            "fecha_cierre": str(fechas.get("fecha_cierre") or ""),
            "fecha_ultimo_cambio": str(fechas.get("fecha_ultimo_cambio") or ""),
        },
        "institucion": {
            "organismo_comprador": str(institucion.get("organismo_comprador") or ""),
            "unidad_compra": str(institucion.get("unidad_compra") or ""),
            "region": numero_seguro(institucion.get("region")),
            "nombre_region": str(institucion.get("nombre_region") or ""),
        },
        "presupuesto": {
            "tipo_presupuesto": str(presupuesto.get("tipo_presupuesto") or ""),
            "moneda": str(presupuesto.get("moneda") or montos.get("moneda") or "CLP"),
            "presupuesto_estimado": numero_seguro(presupuesto.get("presupuesto_estimado")),
            "monto_disponible": numero_seguro(
                presupuesto.get("monto_disponible", montos.get("monto_disponible"))
            ),
            "monto_disponible_clp": numero_seguro(
                presupuesto.get("monto_disponible_clp", montos.get("monto_disponible_clp"))
            ),
        },
        "entrega": {
            "direccion_entrega": str(entrega.get("direccion_entrega") or ""),
            "plazo_entrega_dias": numero_seguro(entrega.get("plazo_entrega_dias")),
        },
        "productos_solicitados": items,
        "documentos": docs,
    }


def registro_desde_api(item: dict) -> dict:
    """Proyecta un registro de listado API a los campos que necesita el Radar."""
    if not isinstance(item, dict):
        raise ValueError("El registro recibido desde ChileCompra no es válido.")
    estado = item.get("estado") or {}
    convocatoria = item.get("convocatoria") or {}
    institucion = item.get("institucion") or {}
    fechas = item.get("fechas") or {}
    montos = item.get("montos") or item.get("presupuesto") or {}
    codigo = str(item.get("codigo") or "").strip()
    nombre = str(item.get("nombre") or "").strip()
    if not codigo or not nombre:
        raise ValueError("La API devolvió una oportunidad sin código o nombre.")
    presupuesto = numero_seguro(
        montos.get("monto_disponible_clp"),
        numero_seguro(montos.get("presupuesto_estimado")),
    )
    region = numero_seguro(institucion.get("region"))
    return {
        "codigo": codigo,
        "nombre": nombre,
        "estado_fuente": str(estado.get("codigo") or ""),
        "estado_glosa": str(estado.get("glosa") or ""),
        "convocatoria": numero_seguro(convocatoria.get("estado_convocatoria")),
        "region": int(region) if region is not None else None,
        "region_nombre": str(
            institucion.get("nombre_region") or REGIONES.get(int(region), "")
            if region is not None else institucion.get("nombre_region") or ""
        ),
        "organismo": str(institucion.get("organismo_comprador") or ""),
        "fecha_publicacion": str(fechas.get("fecha_publicacion") or ""),
        "fecha_cierre": str(fechas.get("fecha_cierre") or ""),
        "fecha_ultimo_cambio": str(fechas.get("fecha_ultimo_cambio") or ""),
        "presupuesto_clp": presupuesto,
        "moneda": str(montos.get("moneda") or "CLP"),
        "descripcion": str(item.get("descripcion") or ""),
        "fuente": "API Compra Ágil",
        "detalle": {},
        "api_detalle_url": f"{API_BASE}/v2/compra-agil/{quote(codigo, safe='-')}",
    }


def _request_json(ticket: str, url: str, params: dict | None = None) -> dict:
    if not ticket or not ticket.strip():
        raise APICompraAgilError(
            "Ingresa tu ticket personal de la API de ChileCompra. La aplicación no lo guarda."
        )
    try:
        response = requests.get(
            url, headers={"ticket": ticket.strip()},
            params=params or {}, timeout=(5, 25),
        )
    except requests.RequestException as exc:
        raise APICompraAgilError(
            f"No pude conectar con ChileCompra: {exc.__class__.__name__}. Revisa internet e inténtalo de nuevo."
        ) from exc

    if response.status_code == 401:
        raise APICompraAgilError(
            "ChileCompra rechazó el ticket (HTTP 401). Revísalo en el portal oficial.", 401
        )
    if response.status_code == 429:
        raise APICompraAgilError(
            "Se agotó la cuota diaria de este ticket (HTTP 429). La guía oficial indica esperar hasta el próximo día calendario.",
            429,
        )
    if response.status_code in (403,):
        raise APICompraAgilError(
            "La API denegó el acceso. Revisa el ticket y sus condiciones de uso.", 403
        )
    if response.status_code >= 500:
        raise APICompraAgilError(
            f"La API de ChileCompra respondió HTTP {response.status_code}. Intenta más tarde.",
            response.status_code,
        )
    if not response.ok:
        raise APICompraAgilError(
            f"La API de ChileCompra respondió HTTP {response.status_code}.", response.status_code
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise APICompraAgilError("ChileCompra respondió con un formato que no pude interpretar.") from exc
    if not isinstance(body, dict):
        raise APICompraAgilError("ChileCompra devolvió una respuesta inesperada.")
    if str(body.get("success") or "").upper() not in ("OK", ""):
        errores = body.get("errors") or []
        mensaje = "; ".join(
            str(x.get("mensaje") or x.get("detalle") or x)
            for x in errores if isinstance(x, dict)
        ) if isinstance(errores, list) else str(errores)
        raise APICompraAgilError(mensaje or "La API informó que la consulta no fue exitosa.")
    return body


def obtener_detalle_compra_agil(ticket: str, codigo: str) -> dict:
    codigo = (codigo or "").strip()
    if not codigo:
        raise APICompraAgilError("Falta el código de Compra Ágil.")
    body = _request_json(
        ticket, f"{API_BASE}/v2/compra-agil/{quote(codigo, safe='-')}"
    )
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise APICompraAgilError("La API no devolvió el detalle de esta oportunidad.")
    return normalizar_detalle(payload)


def buscar_compras_agiles(
    ticket: str,
    palabras: Iterable[str],
    regiones: Iterable[int] = (15,),
    estados: Iterable[str] = ("publicada",),
    tamano_pagina: int = 50,
) -> tuple[list[dict], list[str], int]:
    """Busca hasta ocho términos, una página por término; devuelve registros, avisos y llamadas."""
    terminos = []
    vistos = set()
    for termino in palabras:
        t = " ".join(str(termino or "").split())
        llave = normalizar_texto(t)
        if t and llave not in vistos:
            vistos.add(llave)
            terminos.append(t)
    terminos = terminos[:8]
    if not terminos:
        raise ValueError("Escribe al menos una palabra clave.")
    region_ids = [int(n) for n in regiones if numero_seguro(n) is not None and 1 <= int(n) <= 16]
    estado_ids = [str(e).strip() for e in estados if str(e).strip() in ESTADOS_API]
    params_base = {
        "tamano_pagina": max(1, min(int(tamano_pagina), 50)),
        "numero_pagina": 1,
        "ordenar_por": "FechaPublicacion",
    }
    if region_ids:
        params_base["region"] = ",".join(str(n) for n in dict.fromkeys(region_ids))
    if estado_ids:
        params_base["estado"] = ",".join(dict.fromkeys(estado_ids))

    registros, errores = {}, []
    llamadas = 0
    for termino in terminos:
        params = {**params_base, "q": termino}
        try:
            body = _request_json(ticket, API_LIST_URL, params)
            llamadas += 1
        except APICompraAgilError as exc:
            errores.append(f"{termino}: {exc}")
            if exc.status_code in (401, 403, 429):
                break
            continue
        payload = body.get("payload") or {}
        items = payload.get("items") or []
        for item in items:
            try:
                registro = registro_desde_api(item)
            except ValueError:
                continue
            registro["_coincidencia_busqueda"] = termino
            registros[registro["codigo"]] = registro
    return list(registros.values()), errores, llamadas


def normalizar_importacion(fila: dict, fuente: str = "Importación Excel/CSV") -> dict | None:
    """Convierte columnas comunes de exportaciones públicas a un registro interoperable."""
    def clave_columna(s):
        return normalizar_texto(s).replace(" ", "")

    fila_norm = {clave_columna(k): v for k, v in fila.items()}
    def leer(*nombres):
        for nombre in nombres:
            k = clave_columna(nombre)
            if k in fila_norm and fila_norm[k] not in (None, ""):
                return fila_norm[k]
        return None

    codigo = leer(
        "codigo", "codigo compra agil", "numero compra agil", "codigo del proceso",
        "numero de compra", "codigo proceso", "id compra agil", "codigo de compra",
    )
    nombre = leer(
        "nombre", "titulo", "nombre del proceso", "nombre compra", "descripcion",
        "objeto de compra", "detalle del requerimiento",
    )
    if nombre is None:
        return None
    nombre = str(nombre).strip()
    if not nombre:
        return None
    fecha_cierre = leer("fecha cierre", "fecha de cierre", "cierre", "fecha cierre cotizacion")
    fecha_publicacion = leer("fecha publicacion", "fecha de publicacion", "publicacion")
    presupuesto = numero_seguro(leer(
        "monto disponible clp", "presupuesto estimado", "presupuesto", "monto disponible",
        "monto", "monto total",
    ))
    organismo = leer("organismo comprador", "institucion", "institución", "nombre institucion", "comprador")
    estado = leer("estado", "estado compra agil", "estado del proceso") or "Importada"
    region = leer("region", "región", "region comprador")
    region_num = numero_seguro(region)
    if region_num is None:
        region_nombre = str(region or "")
        region_num = next((k for k, v in REGIONES.items() if normalizar_texto(v) == normalizar_texto(region_nombre)), None)
    else:
        region_num = int(region_num) if 1 <= region_num <= 16 else None
        region_nombre = REGIONES.get(region_num, "") if region_num else ""
    if not codigo or not str(codigo).strip():
        raw = "|".join((nombre, str(organismo or ""), str(fecha_publicacion or ""), str(fecha_cierre or "")))
        codigo = "IMP-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
    return {
        "codigo": str(codigo).strip(),
        "nombre": nombre,
        "estado_fuente": str(estado),
        "estado_glosa": str(estado),
        "convocatoria": None,
        "region": region_num,
        "region_nombre": region_nombre,
        "organismo": str(organismo or ""),
        "fecha_publicacion": str(fecha_publicacion or ""),
        "fecha_cierre": str(fecha_cierre or ""),
        "fecha_ultimo_cambio": "",
        "presupuesto_clp": presupuesto,
        "moneda": str(leer("moneda", "moneda presupuesto") or "CLP"),
        "descripcion": str(leer("descripcion", "detalle", "requerimiento") or ""),
        "fuente": fuente,
        "detalle": {"productos_solicitados": []},
        "api_detalle_url": "",
    }


def parsear_lineas_productos(texto: str) -> list[dict]:
    """Formato editable por el usuario: cantidad | descripción | unidad (unidad opcional)."""
    salida = []
    for linea in (texto or "").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        partes = [p.strip() for p in linea.split("|")]
        if len(partes) >= 2:
            cantidad = numero_seguro(partes[0], 1)
            descripcion = partes[1]
            unidad = partes[2] if len(partes) >= 3 else ""
        else:
            cantidad, descripcion, unidad = 1, linea, ""
        if descripcion:
            salida.append({
                "codigo_producto": "",
                "nombre": descripcion,
                "descripcion": descripcion,
                "cantidad": cantidad if cantidad is not None and cantidad > 0 else 1,
                "unidad_medida": unidad,
            })
    return salida


def puntuar_coincidencia(descripcion_solicitada: str, clave: str, ficha: dict) -> float:
    candidatos = [str(clave), str(ficha.get("nombre") or "")]
    candidatos.extend(str(alias) for alias in ficha.get("aliases") or [])
    consulta = normalizar_texto(descripcion_solicitada)
    if not consulta:
        return 0.0
    mejor = 0.0
    qtokens = tokens_relevantes(descripcion_solicitada)
    for texto in candidatos:
        norm = normalizar_texto(texto)
        if not norm:
            continue
        if consulta == norm:
            return 1.0
        # Los códigos de fabricante suelen aparecer dentro de una descripción más larga.
        consulta_compacta = consulta.replace(" ", "")
        norm_compacto = norm.replace(" ", "")
        if len(norm_compacto) >= 4 and norm_compacto in consulta_compacta:
            mejor = max(mejor, 0.96)
        c_tokens = tokens_relevantes(texto)
        union = qtokens | c_tokens
        jaccard = len(qtokens & c_tokens) / len(union) if union else 0.0
        similitud = SequenceMatcher(None, consulta, norm).ratio()
        score = 0.62 * jaccard + 0.38 * similitud
        mejor = max(mejor, score)
    return round(mejor, 4)


def sugerir_productos_catalogo(descripcion: str, indice: dict, limite: int = 5) -> list[dict]:
    resultados = []
    for clave, ficha in (indice or {}).items():
        if not isinstance(ficha, dict):
            continue
        score = puntuar_coincidencia(descripcion, clave, ficha)
        if score <= 0:
            continue
        resultados.append({
            "clave": clave,
            "nombre": str(ficha.get("nombre") or clave),
            "score": score,
            "costo_catalogo": numero_seguro(ficha.get("costo_neto")),
            "precio_catalogo": numero_seguro(ficha.get("precio_neto")),
            "margen_pct": numero_seguro(ficha.get("margen_pct")),
            "aliases": list(ficha.get("aliases") or []),
        })
    resultados.sort(key=lambda r: (r["score"], r["clave"]), reverse=True)
    return resultados[:max(1, int(limite))]


def importe_catalogo_a_neto(valor, base_importes: str) -> float | None:
    n = numero_seguro(valor)
    if n is None:
        return None
    if base_importes == "Incluyen IVA":
        return n / 1.19
    return n


def evaluar_rentabilidad(
    lineas: list[dict],
    indice_catalogo: dict,
    base_importes: str,
    despacho_neto: float = 0,
    otros_costos_netos: float = 0,
    margen_objetivo_pct: float = 0,
    costos_adicionales_confirmados: bool = False,
) -> dict:
    """Calcula el estimado neto; no declara equivalencia técnica ni sustituye la revisión humana."""
    ingresos_netos = 0.0
    costo_productos_parcial = 0.0
    faltan_costos = []
    faltan_precios = []
    faltan_equivalencias = []
    lineas_resultado = []
    for ln in lineas:
        if not ln.get("incluir", True):
            continue
        cantidad = numero_seguro(ln.get("cantidad"), 0) or 0
        precio = numero_seguro(ln.get("precio_oferta_unitario_neto"))
        clave = str(ln.get("codigo_catalogo") or "").strip()
        ficha = (indice_catalogo or {}).get(clave) if clave else None
        if cantidad <= 0:
            faltan_precios.append(str(ln.get("descripcion_solicitada") or "Línea sin cantidad"))
            continue
        if precio is None or precio <= 0:
            faltan_precios.append(str(ln.get("descripcion_solicitada") or "Línea sin precio ofertado"))
        else:
            ingresos_netos += cantidad * precio
        costo_neto = None
        if not ficha:
            faltan_equivalencias.append(str(ln.get("descripcion_solicitada") or "Línea sin producto de catálogo"))
        elif not ln.get("equivalencia_confirmada", False):
            faltan_equivalencias.append(
                f"{ln.get('descripcion_solicitada') or 'Producto'} (equivalencia aún no confirmada)"
            )
        if ficha:
            costo_base = numero_seguro(ficha.get("costo_neto"))
            if costo_base is None or costo_base < 0:
                faltan_costos.append(str(ficha.get("nombre") or clave))
            else:
                costo_neto = importe_catalogo_a_neto(costo_base, base_importes)
                if costo_neto is not None:
                    costo_productos_parcial += cantidad * costo_neto
                else:
                    faltan_costos.append(str(ficha.get("nombre") or clave))
        lineas_resultado.append({
            "descripcion_solicitada": str(ln.get("descripcion_solicitada") or ""),
            "cantidad": cantidad,
            "codigo_catalogo": clave,
            "producto_catalogo": str((ficha or {}).get("nombre") or ""),
            "precio_oferta_unitario_neto": precio,
            "costo_unitario_neto": costo_neto,
            "ingreso_neto_linea": cantidad * precio if precio is not None else None,
            "costo_neto_linea": cantidad * costo_neto if costo_neto is not None else None,
        })

    despacho_neto = max(0.0, numero_seguro(despacho_neto, 0) or 0)
    otros_costos_netos = max(0.0, numero_seguro(otros_costos_netos, 0) or 0)
    costo_estimado_parcial = costo_productos_parcial + despacho_neto + otros_costos_netos
    equivalencias_completas = not faltan_equivalencias
    costos_completos = not faltan_costos and equivalencias_completas and costos_adicionales_confirmados
    precios_completos = not faltan_precios and ingresos_netos > 0
    beneficio = ingresos_netos - costo_estimado_parcial if precios_completos and costos_completos else None
    margen_venta = (
        beneficio / ingresos_netos * 100
        if beneficio is not None and ingresos_netos > 0 and costos_completos else None
    )
    retorno_costo = (
        beneficio / costo_estimado_parcial * 100
        if beneficio is not None and costo_estimado_parcial > 0 and costos_completos else None
    )
    objetivo = max(0.0, min(90.0, numero_seguro(margen_objetivo_pct, 0) or 0))
    precio_minimo_objetivo = (
        costo_estimado_parcial / (1 - objetivo / 100)
        if costos_completos and objetivo > 0 else None
    )
    if not precios_completos or not costos_completos:
        resultado = "Incompleto: faltan datos por verificar"
    elif beneficio is not None and beneficio < 0:
        resultado = "No rentable con los valores ingresados"
    elif objetivo > 0 and margen_venta is not None and margen_venta < objetivo:
        resultado = "Bajo el margen objetivo"
    else:
        resultado = "Rentabilidad estimada positiva; revisar condiciones"
    return {
        "lineas": lineas_resultado,
        "ingresos_netos": round(ingresos_netos, 2),
        "costo_productos_parcial_neto": round(costo_productos_parcial, 2),
        "despacho_neto": round(despacho_neto, 2),
        "otros_costos_netos": round(otros_costos_netos, 2),
        "costo_estimado_parcial_neto": round(costo_estimado_parcial, 2),
        "beneficio_estimado_neto": round(beneficio, 2) if beneficio is not None else None,
        "margen_sobre_venta_pct": round(margen_venta, 2) if margen_venta is not None else None,
        "retorno_sobre_costo_pct": round(retorno_costo, 2) if retorno_costo is not None else None,
        "precio_minimo_neto_objetivo": round(precio_minimo_objetivo, 2) if precio_minimo_objetivo is not None else None,
        "margen_objetivo_pct": objetivo,
        "precios_completos": precios_completos,
        "costos_completos": costos_completos,
        "costos_adicionales_confirmados": bool(costos_adicionales_confirmados),
        "faltan_costos": faltan_costos,
        "faltan_precios": faltan_precios,
        "faltan_equivalencias": faltan_equivalencias,
        "resultado": resultado,
    }


def normalizar_registro_para_store(registro: dict) -> dict:
    """Lista blanca de campos comerciales para evitar persistir objetos ajenos a la evaluación."""
    detalle = normalizar_detalle(registro.get("detalle") or {})
    return {
        "codigo": str(registro.get("codigo") or "").strip(),
        "nombre": str(registro.get("nombre") or "").strip(),
        "estado_fuente": str(registro.get("estado_fuente") or ""),
        "estado_glosa": str(registro.get("estado_glosa") or ""),
        "convocatoria": numero_seguro(registro.get("convocatoria")),
        "region": numero_seguro(registro.get("region")),
        "region_nombre": str(registro.get("region_nombre") or ""),
        "organismo": str(registro.get("organismo") or ""),
        "fecha_publicacion": str(registro.get("fecha_publicacion") or ""),
        "fecha_cierre": str(registro.get("fecha_cierre") or ""),
        "fecha_ultimo_cambio": str(registro.get("fecha_ultimo_cambio") or ""),
        "presupuesto_clp": numero_seguro(registro.get("presupuesto_clp")),
        "moneda": str(registro.get("moneda") or "CLP"),
        "descripcion": str(registro.get("descripcion") or ""),
        "fuente": str(registro.get("fuente") or "Manual"),
        "detalle": detalle,
        "api_detalle_url": str(registro.get("api_detalle_url") or ""),
    }


class RadarStore:
    """Base separada del cotizador que almacena oportunidades y evaluaciones compartidas."""

    def __init__(self, archivo: str | Path):
        self.path = Path(archivo)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS configuracion (
                    clave TEXT PRIMARY KEY,
                    valor TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oportunidades (
                    codigo TEXT PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    estado_fuente TEXT DEFAULT '',
                    estado_glosa TEXT DEFAULT '',
                    convocatoria REAL,
                    region INTEGER,
                    region_nombre TEXT DEFAULT '',
                    organismo TEXT DEFAULT '',
                    fecha_publicacion TEXT DEFAULT '',
                    fecha_cierre TEXT DEFAULT '',
                    fecha_ultimo_cambio TEXT DEFAULT '',
                    presupuesto_clp REAL,
                    moneda TEXT DEFAULT 'CLP',
                    descripcion TEXT DEFAULT '',
                    fuente TEXT DEFAULT 'Manual',
                    detalle_json TEXT DEFAULT '{}',
                    api_detalle_url TEXT DEFAULT '',
                    decision TEXT DEFAULT 'Nueva',
                    notas TEXT DEFAULT '',
                    creado TEXT NOT NULL,
                    actualizado TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluaciones (
                    codigo TEXT PRIMARY KEY,
                    datos_json TEXT NOT NULL,
                    actualizado TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_oportunidades_decision ON oportunidades(decision);
                CREATE INDEX IF NOT EXISTS idx_oportunidades_cierre ON oportunidades(fecha_cierre);
            """)

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA foreign_keys=ON")
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def config(self, clave: str, defecto=None):
        with self._connection() as con:
            row = con.execute("SELECT valor FROM configuracion WHERE clave=?", (clave,)).fetchone()
        return row["valor"] if row else defecto

    def set_config(self, clave: str, valor):
        with self._connection() as con:
            con.execute(
                "INSERT INTO configuracion(clave, valor) VALUES(?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (clave, str(valor)),
            )

    def guardar_oportunidad(self, registro: dict) -> str:
        r = normalizar_registro_para_store(registro)
        if not r["codigo"] or not r["nombre"]:
            raise ValueError("La oportunidad debe tener código y nombre.")
        ts = ahora_iso()
        with self._connection() as con:
            con.execute("""
                INSERT INTO oportunidades(
                    codigo,nombre,estado_fuente,estado_glosa,convocatoria,region,region_nombre,
                    organismo,fecha_publicacion,fecha_cierre,fecha_ultimo_cambio,presupuesto_clp,
                    moneda,descripcion,fuente,detalle_json,api_detalle_url,decision,notas,creado,actualizado
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Nueva','',?,?)
                ON CONFLICT(codigo) DO UPDATE SET
                    nombre=excluded.nombre,
                    estado_fuente=excluded.estado_fuente,
                    estado_glosa=excluded.estado_glosa,
                    convocatoria=excluded.convocatoria,
                    region=excluded.region,
                    region_nombre=excluded.region_nombre,
                    organismo=excluded.organismo,
                    fecha_publicacion=excluded.fecha_publicacion,
                    fecha_cierre=excluded.fecha_cierre,
                    fecha_ultimo_cambio=excluded.fecha_ultimo_cambio,
                    presupuesto_clp=excluded.presupuesto_clp,
                    moneda=excluded.moneda,
                    descripcion=CASE WHEN excluded.descripcion<>'' THEN excluded.descripcion ELSE oportunidades.descripcion END,
                    fuente=excluded.fuente,
                    detalle_json=CASE WHEN excluded.detalle_json<>'{}' THEN excluded.detalle_json ELSE oportunidades.detalle_json END,
                    api_detalle_url=CASE WHEN excluded.api_detalle_url<>'' THEN excluded.api_detalle_url ELSE oportunidades.api_detalle_url END,
                    actualizado=excluded.actualizado
            """, (
                r["codigo"], r["nombre"], r["estado_fuente"], r["estado_glosa"], r["convocatoria"],
                r["region"], r["region_nombre"], r["organismo"], r["fecha_publicacion"],
                r["fecha_cierre"], r["fecha_ultimo_cambio"], r["presupuesto_clp"], r["moneda"],
                r["descripcion"], r["fuente"], json.dumps(r["detalle"], ensure_ascii=False),
                r["api_detalle_url"], ts, ts,
            ))
        return r["codigo"]

    def guardar_detalle(self, codigo: str, detalle: dict):
        safe = normalizar_detalle(detalle)
        with self._connection() as con:
            con.execute(
                "UPDATE oportunidades SET detalle_json=?, descripcion=CASE WHEN ?<>'' THEN ? ELSE descripcion END, "
                "estado_fuente=CASE WHEN ?<>'' THEN ? ELSE estado_fuente END, "
                "estado_glosa=CASE WHEN ?<>'' THEN ? ELSE estado_glosa END, actualizado=? WHERE codigo=?",
                (
                    json.dumps(safe, ensure_ascii=False), safe.get("descripcion", ""),
                    safe.get("descripcion", ""), safe.get("estado", {}).get("codigo", ""),
                    safe.get("estado", {}).get("codigo", ""), safe.get("estado", {}).get("glosa", ""),
                    safe.get("estado", {}).get("glosa", ""), ahora_iso(), codigo,
                ),
            )

    def actualizar_seguimiento(self, codigo: str, decision: str, notas: str = ""):
        if decision not in ESTADOS_SEGUIMIENTO:
            raise ValueError("El estado de seguimiento no es válido.")
        with self._connection() as con:
            con.execute(
                "UPDATE oportunidades SET decision=?, notas=?, actualizado=? WHERE codigo=?",
                (decision, str(notas or "").strip()[:5000], ahora_iso(), codigo),
            )

    def guardar_evaluacion(self, codigo: str, datos: dict):
        # La proyección solo almacena datos de la oportunidad, productos y evaluación económica.
        safe = {
            "lineas": datos.get("lineas") or [],
            "despacho_neto": numero_seguro(datos.get("despacho_neto"), 0),
            "otros_costos_netos": numero_seguro(datos.get("otros_costos_netos"), 0),
            "costos_adicionales_confirmados": bool(datos.get("costos_adicionales_confirmados", False)),
            "margen_objetivo_pct": numero_seguro(datos.get("margen_objetivo_pct"), 0),
            "resultado_calculo": datos.get("resultado_calculo") or {},
        }
        with self._connection() as con:
            con.execute(
                "INSERT INTO evaluaciones(codigo, datos_json, actualizado) VALUES(?,?,?) "
                "ON CONFLICT(codigo) DO UPDATE SET datos_json=excluded.datos_json, actualizado=excluded.actualizado",
                (codigo, json.dumps(safe, ensure_ascii=False), ahora_iso()),
            )

    def evaluacion(self, codigo: str) -> dict:
        with self._connection() as con:
            row = con.execute("SELECT datos_json FROM evaluaciones WHERE codigo=?", (codigo,)).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["datos_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}

    def obtener(self, codigo: str) -> dict | None:
        with self._connection() as con:
            row = con.execute("SELECT * FROM oportunidades WHERE codigo=?", (codigo,)).fetchone()
            if not row:
                return None
            rec = dict(row)
        try:
            rec["detalle"] = json.loads(rec.pop("detalle_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            rec["detalle"] = {}
        rec["evaluacion"] = self.evaluacion(codigo)
        return rec

    def listar(self, decision: str | None = None, busqueda: str = "") -> list[dict]:
        sql = "SELECT * FROM oportunidades"
        args = []
        filtros = []
        if decision and decision != "Todas":
            filtros.append("decision=?")
            args.append(decision)
        if busqueda.strip():
            filtros.append("(codigo LIKE ? OR nombre LIKE ? OR organismo LIKE ? OR descripcion LIKE ?)")
            q = f"%{busqueda.strip()}%"
            args.extend((q, q, q, q))
        if filtros:
            sql += " WHERE " + " AND ".join(filtros)
        sql += " ORDER BY CASE decision WHEN 'Nueva' THEN 0 WHEN 'En evaluación' THEN 1 ELSE 2 END, fecha_cierre ASC, actualizado DESC"
        with self._connection() as con:
            rows = con.execute(sql, args).fetchall()
            result = []
            for row in rows:
                r = dict(row)
                try:
                    r["detalle"] = json.loads(r.pop("detalle_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    r["detalle"] = {}
                result.append(r)
        for r in result:
            r["evaluacion"] = self.evaluacion(r["codigo"])
        return result

    def total(self) -> int:
        with self._connection() as con:
            row = con.execute("SELECT COUNT(*) AS n FROM oportunidades").fetchone()
        return int(row["n"] or 0)

    def crear_respaldo_zip(self) -> bytes:
        """Crea una copia consistente de la base propia del Radar sin detenerlo."""
        with tempfile.TemporaryDirectory(prefix="gioval-radar-backup-") as tmp:
            copia_db = Path(tmp) / "radar_comercial.db"
            with sqlite3.connect(self.path, timeout=30) as origen:
                with sqlite3.connect(copia_db) as destino:
                    origen.backup(destino)
            salida = io.BytesIO()
            with zipfile.ZipFile(salida, "w", compression=zipfile.ZIP_DEFLATED) as archivo_zip:
                archivo_zip.write(copia_db, arcname="radar_comercial.db")
                archivo_zip.writestr(
                    "LEEME.txt",
                    "Respaldo de Radar Comercial Gioval.\\n"
                    "Contiene oportunidades, seguimiento y evaluaciones.\\n"
                    "No contiene el catálogo ni el historial del cotizador, que se respaldan por separado.\\n"
                    "Para restaurarlo, detén Radar y reemplaza radar_comercial.db en el directorio de datos.\\n",
                )
            return salida.getvalue()


def resumen_historial_cotizador(registros: list[dict]) -> dict:
    """Agrega únicamente cantidades/importes/líneas; no devuelve identificadores de clientes."""
    total = aceptadas = 0
    monto_aceptado = 0.0
    productos = {}
    for reg in registros or []:
        total += 1
        estado = str(reg.get("estado") or "")
        if estado == "Aceptada":
            aceptadas += 1
            monto_aceptado += numero_seguro(reg.get("total"), 0) or 0
        datos = reg.get("datos") or {}
        if isinstance(datos, str):
            try:
                datos = json.loads(datos)
            except (TypeError, json.JSONDecodeError):
                datos = {}
        for ln in datos.get("lineas") or []:
            if not isinstance(ln, dict):
                continue
            nombre = str(ln.get("descripcion") or "").strip()
            if nombre:
                productos[nombre] = productos.get(nombre, 0) + max(
                    0, numero_seguro(ln.get("cantidad"), 0) or 0
                )
    return {
        "cotizaciones": total,
        "aceptadas": aceptadas,
        "monto_aceptado": round(monto_aceptado, 2),
        "productos_frecuentes": sorted(productos.items(), key=lambda x: x[1], reverse=True)[:8],
    }
