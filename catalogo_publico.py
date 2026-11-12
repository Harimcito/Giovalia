"""Genera JSON-LD de productos aprobados sin exponer datos operacionales.

El índice de Giovalia es privado. Este módulo solo lee campos bajo
publicacion y nunca copia precios/costos/márgenes desde la ficha interna.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


_DISPONIBILIDAD = {
    "en_stock": "https://schema.org/InStock",
    "agotado": "https://schema.org/OutOfStock",
    "disponibilidad_limitada": "https://schema.org/LimitedAvailability",
    "pedido_pendiente": "https://schema.org/BackOrder",
    "preventa": "https://schema.org/PreOrder",
}
_DERECHOS_IMAGEN = {"propia", "licenciada"}
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _url_https(value: object) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
        return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _fecha(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _valor_texto(value: object, limite: int = 5000) -> str:
    return " ".join(str(value or "").split())[:limite]


def _precio_publico(publicacion: dict, url: str, clave: str, errores: list[dict]) -> dict | None:
    oferta = publicacion.get("oferta")
    if not isinstance(oferta, dict) or oferta.get("estado") != "aprobada":
        return None

    try:
        precio = float(oferta.get("precio"))
    except (TypeError, ValueError):
        errores.append({"clave": clave, "campo": "oferta.precio", "motivo": "precio_publico_invalido"})
        return None
    moneda = str(oferta.get("moneda") or "").strip().upper()
    disponibilidad = _DISPONIBILIDAD.get(str(oferta.get("disponibilidad") or "").strip())
    verificado = _fecha(oferta.get("verificado_el"))
    vigencia = _fecha(oferta.get("vigente_hasta"))
    fuente = oferta.get("fuente")

    if precio <= 0 or not moneda.isalpha() or len(moneda) != 3:
        errores.append({"clave": clave, "campo": "oferta", "motivo": "precio_o_moneda_invalida"})
        return None
    if not disponibilidad:
        errores.append({"clave": clave, "campo": "oferta.disponibilidad", "motivo": "disponibilidad_no_reconocida"})
        return None
    if not verificado or not vigencia or vigencia < date.today() or not _url_https(fuente):
        errores.append({"clave": clave, "campo": "oferta", "motivo": "fuente_o_vigencia_no_verificable"})
        return None
    if oferta.get("impuestos_incluidos") not in (True, False):
        errores.append({"clave": clave, "campo": "oferta.impuestos_incluidos", "motivo": "base_tributaria_pendiente"})
        return None

    return {
        "@type": "Offer",
        "url": url,
        "price": precio,
        "priceCurrency": moneda,
        "availability": disponibilidad,
        "itemCondition": "https://schema.org/NewCondition",
        "priceValidUntil": vigencia.isoformat(),
    }


def crear_producto_jsonld(clave: str, ficha: dict, base_url: str, errores: list[dict]) -> dict | None:
    """Devuelve un Product aprobado o None; nunca usa costo ni precio de cotización."""
    publicacion = ficha.get("publicacion") if isinstance(ficha, dict) else None
    if not isinstance(publicacion, dict) or publicacion.get("estado") != "aprobado":
        return None

    id_global = str(ficha.get("id_global") or "").strip()
    try:
        uuid.UUID(id_global)
    except (ValueError, AttributeError):
        errores.append({"clave": clave, "campo": "id_global", "motivo": "uuid_maestro_pendiente"})
        return None

    slug = str(publicacion.get("slug") or "").strip()
    nombre = _valor_texto(publicacion.get("nombre"), 300)
    descripcion = _valor_texto(publicacion.get("descripcion"), 5000)
    if not _SLUG.fullmatch(slug) or not nombre or not descripcion:
        errores.append({"clave": clave, "campo": "publicacion", "motivo": "slug_nombre_o_descripcion_pendiente"})
        return None

    url = f"{base_url.rstrip('/')}/{slug}"
    imagenes = []
    for imagen in publicacion.get("imagenes", []) if isinstance(publicacion.get("imagenes"), list) else []:
        if not isinstance(imagen, dict):
            continue
        img_url = str(imagen.get("url") or "").strip()
        alt = _valor_texto(imagen.get("alt"), 300)
        if imagen.get("aprobada") is True and imagen.get("derechos") in _DERECHOS_IMAGEN and _url_https(img_url) and alt:
            imagenes.append(img_url)
    if not imagenes:
        errores.append({"clave": clave, "campo": "publicacion.imagenes", "motivo": "sin_imagen_aprobada"})
        return None

    producto = {
        "@context": "https://schema.org",
        "@type": "Product",
        "@id": f"{url}#product",
        "url": url,
        "name": nombre,
        "description": descripcion,
        "image": sorted(set(imagenes)),
    }

    for origen, destino in (
        ("codigo_publico", "sku"),
        ("codigo_fabricante", "mpn"),
        ("gtin", "gtin"),
        ("categoria", "category"),
    ):
        valor = _valor_texto(publicacion.get(origen), 300)
        if valor:
            producto[destino] = valor

    marca = _valor_texto(publicacion.get("marca"), 160)
    if marca:
        producto["brand"] = {"@type": "Brand", "name": marca}

    propiedades = []
    for propiedad in publicacion.get("especificaciones", []) if isinstance(publicacion.get("especificaciones"), list) else []:
        if isinstance(propiedad, dict):
            nombre_prop = _valor_texto(propiedad.get("nombre"), 120)
            valor_prop = _valor_texto(propiedad.get("valor"), 300)
            if nombre_prop and valor_prop:
                propiedades.append({"@type": "PropertyValue", "name": nombre_prop, "value": valor_prop})
    if propiedades:
        producto["additionalProperty"] = propiedades

    oferta = _precio_publico(publicacion, url, clave, errores)
    if oferta:
        producto["offers"] = oferta
    return producto


def exportar_catalogo(indice: dict, base_url: str) -> tuple[dict, list[dict]]:
    """Construye una salida solo con fichas aprobadas y datos públicos explícitos."""
    if not _url_https(base_url):
        raise ValueError("La URL base debe ser HTTPS y pertenecer al sitio público aprobado.")
    productos = []
    errores = []
    for clave, ficha in sorted(indice.items()):
        producto = crear_producto_jsonld(str(clave), ficha, base_url, errores)
        if producto:
            productos.append(producto)
    return {"@context": "https://schema.org", "@graph": productos}, errores


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Ruta del índice privado indice.json")
    parser.add_argument("--output", required=True, help="Nuevo archivo JSON de salida; no puede sobrescribir el índice")
    parser.add_argument("--base-url", required=True, help="URL HTTPS del sitio público")
    args = parser.parse_args()

    origen = Path(args.input).expanduser().resolve()
    destino = Path(args.output).expanduser().resolve()
    if origen == destino:
        parser.error("El archivo de salida debe ser distinto al índice privado.")
    indice = json.loads(origen.read_text(encoding="utf-8-sig"))
    if not isinstance(indice, dict):
        parser.error("El índice debe ser un objeto JSON.")
    salida, errores = exportar_catalogo(indice, args.base_url)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Productos públicos exportados: {len(salida['@graph'])}")
    print(f"Registros omitidos por datos pendientes: {len(errores)}")
    for error in errores:
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
