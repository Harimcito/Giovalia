"""Cliente de solo lectura para consultar una Compra Ágil por código."""
from __future__ import annotations

import math
import time
from urllib.parse import quote

import requests


API_BASE = "https://api2.mercadopublico.cl"
API_LIST_URL = f"{API_BASE}/v2/compra-agil"


class APICompraAgilError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def numero_seguro(valor, defecto=None):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return defecto
    if not math.isfinite(n):
        return defecto
    return n


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


def _request_json(ticket: str, url: str, params: dict | None = None) -> dict:
    if not ticket or not ticket.strip():
        raise APICompraAgilError(
            "Ingresa tu ticket personal de la API de ChileCompra. La aplicación no lo guarda."
        )
    response = None
    for intento in range(2):
        try:
            response = requests.get(
                url, headers={"ticket": ticket.strip()},
                params=params or {}, timeout=(8, 40),
            )
            if intento == 0 and response.status_code in (502, 503, 504):
                response.close()
                time.sleep(1)
                continue
            break
        except requests.RequestException as exc:
            if intento == 0:
                time.sleep(1)
                continue
            if isinstance(exc, requests.Timeout):
                mensaje = "ChileCompra tardó demasiado en responder después de dos intentos. Espera un momento y vuelve a consultar; tu ticket no se ha cambiado."
            else:
                mensaje = "No pude conectar con ChileCompra. Revisa internet e inténtalo nuevamente."
            raise APICompraAgilError(mensaje) from exc

    if response.status_code == 400:
        try:
            errores = response.json().get("errors") or []
            detalle = "; ".join(str(e.get("detalle") or e.get("mensaje") or "")
                                for e in errores if isinstance(e, dict))
        except (ValueError, TypeError, AttributeError):
            detalle = ""
        detalle = detalle.replace(ticket.strip(), "[ticket omitido]")[:500]
        raise APICompraAgilError(
            "ChileCompra rechazó los parámetros de la consulta (HTTP 400). " + detalle, 400
        )
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
            f"El servicio de ChileCompra no pudo entregar el detalle (HTTP {response.status_code}). "
            "Intenta más tarde o descarga los adjuntos desde Mercado Público y súbelos aquí para cargar sus productos.",
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
