"""Herramientas de preparación SEO y GEO para el inventario Gioval.

Los textos sugeridos y el marcado estructurado solo usan datos confirmados de la
ficha. No inventan stock, compatibilidad, precios públicos ni URLs de producto.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
import unicodedata
from urllib.parse import urlparse


DISPONIBILIDADES = {
    "No indicar": None,
    "En stock": "https://schema.org/InStock",
    "Bajo pedido": "https://schema.org/PreOrder",
    "Agotado": "https://schema.org/OutOfStock",
}
CONDICIONES = {
    "No indicar": None,
    "Nuevo": "https://schema.org/NewCondition",
    "Usado": "https://schema.org/UsedCondition",
    "Reacondicionado": "https://schema.org/RefurbishedCondition",
}
PERFIL_NEGOCIO = {
    "@type": "Store",
    "@id": "https://gioval.cl/#negocio",
    "name": "Gioval",
    "url": "https://gioval.cl",
    "description": "Librería, tintas y soluciones de impresión con atención en Arica, Chile.",
    "telephone": "+56998498758",
    "email": "contacto@gioval.cl",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "Cristóbal Colón 334, Local 16, Galería Sangra Colón",
        "addressLocality": "Arica",
        "addressRegion": "Arica y Parinacota",
        "addressCountry": "CL",
    },
    "openingHours": ["Mo-Fr 09:30-16:00", "Sa 11:00-14:30"],
    "areaServed": "Chile",
}

GUIA_CONTENIDO = (
    "Mantén una sola identidad de negocio: Gioval, Arica, Chile; usa el contacto y horario vigentes del perfil. "
    "Describe cada producto con hechos de su envase, ficha técnica o proveedor. Especifica marca, modelo, formato, "
    "unidad de venta y compatibilidad solo cuando estén verificados. Escribe títulos y descripciones únicos, claros "
    "y útiles para personas; usa una voz tecnológica, cercana y clara; evita listas de palabras clave, superlativos "
    "sin prueba y promesas genéricas. Para GEO, "
    "responde preguntas concretas con contexto, fuente y fecha de revisión. No afirmes stock ni precio público sin "
    "confirmación. En páginas web, enlaza la ficha, la imagen y el marcado estructurado a URLs públicas reales. "
    "Usa español de Chile, CLP, IVA y ubicación explícitos cuando correspondan."
)


def slugify(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).casefold()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", texto)).strip("-")


def url_publica_valida(url: str) -> bool:
    try:
        texto = str(url or "").strip()
        partes = urlparse(texto)
        host = partes.hostname or ""
        return (
            partes.scheme == "https" and "." in host and partes.username is None
            and partes.port in (443, None) and not re.search(r"\s", texto)
        )
    except ValueError:
        return False


def gtin_schema(gtin: str) -> str | None:
    valor = re.sub(r"[ -]", "", str(gtin or ""))
    propiedades = {8: "gtin8", 12: "gtin12", 13: "gtin13", 14: "gtin14"}
    if len(valor) not in propiedades or not re.fullmatch(r"[0-9]+", valor):
        return None
    digitos, control = valor[:-1], int(valor[-1])
    suma = sum(int(digito) * (3 if posicion % 2 == 0 else 1)
               for posicion, digito in enumerate(reversed(digitos)))
    return propiedades[len(valor)] if (10 - suma % 10) % 10 == control else None


def precio_publico_clp(valor) -> int | None:
    try:
        precio = float(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(precio) if math.isfinite(precio) and round(precio) > 0 else None


def evaluar_ficha(ficha: dict, *, tiene_foto: bool = False) -> dict:
    gtin_ingresado = str(ficha.get("gtin") or "").strip()
    gtin_valido = not gtin_ingresado or bool(gtin_schema(gtin_ingresado))
    identidad = any(str(ficha.get(campo) or "").strip()
                    for campo in ("marca", "modelo", "mpn")) or bool(gtin_ingresado and gtin_valido)
    requisitos = [
        (bool(str(ficha.get("nombre") or "").strip()), "Nombre del producto"),
        (identidad, "Marca, modelo o identificador del fabricante"),
        (gtin_valido, "GTIN/EAN válido (dígito de control)"),
        (bool(str(ficha.get("descripcion") or "").strip()), "Descripción basada en datos del producto"),
        (bool(str(ficha.get("seo_titulo") or "").strip()), "Título SEO"),
        (bool(str(ficha.get("seo_descripcion") or "").strip()), "Descripción para buscadores"),
        (bool(str(ficha.get("seo_slug") or "").strip()), "Slug público"),
        (bool(str(ficha.get("seo_imagen_alt") or "").strip()), "Texto alternativo de la imagen"),
        (bool(str(ficha.get("geo_resumen") or "").strip()), "Resumen directo para respuestas GEO"),
        (bool(str(ficha.get("geo_fuente") or "").strip()), "Fuente de verificación"),
        (bool(str(ficha.get("geo_verificado") or "").strip()), "Fecha de verificación"),
        (tiene_foto, "Foto del producto"),
    ]
    listos = sum(ok for ok, _ in requisitos)
    return {
        "porcentaje": round(100 * listos / len(requisitos)),
        "listo": listos == len(requisitos),
        "faltantes": [label for ok, label in requisitos if not ok],
        "total": len(requisitos),
    }


def sugerencia_slug(ficha: dict, clave: str) -> str:
    return slugify(ficha.get("seo_slug") or ficha.get("nombre") or clave)


def titulo_sugerido(nombre: str, max_chars: int = 60) -> str:
    titulo = f"{str(nombre or '').strip()} | Gioval"
    if len(titulo) <= max_chars:
        return titulo
    base = str(nombre or "").strip()
    fragmento = base[:max(1, max_chars - 9)].rsplit(" ", 1)[0] or base[:max(1, max_chars - 9)]
    return f"{fragmento} | Gioval"


def resumen_factual_sugerido(ficha: dict, clave: str) -> str:
    nombre = str(ficha.get("nombre") or clave).strip()
    descripcion = str(ficha.get("descripcion") or "").strip()
    return f"{nombre}. {descripcion}" if descripcion else f"{nombre} · Código {clave}."


def descripcion_seo_sugerida(ficha: dict, clave: str, limite: int = 160) -> str:
    texto = resumen_factual_sugerido(ficha, clave)
    if len(texto) <= limite:
        return texto
    corto = texto[:limite].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return corto + "…"


def borradores_editoriales(ficha: dict, clave: str) -> dict:
    nombre = str(ficha.get("nombre") or clave).strip()
    return {
        "titulo_seo": ficha.get("seo_titulo") or titulo_sugerido(nombre),
        "descripcion_seo": ficha.get("seo_descripcion") or descripcion_seo_sugerida(ficha, clave),
        "slug": sugerencia_slug(ficha, clave),
        "texto_alternativo_imagen": ficha.get("seo_imagen_alt") or nombre,
        "resumen_geo": ficha.get("geo_resumen") or resumen_factual_sugerido(ficha, clave),
    }


def producto_jsonld(clave: str, ficha: dict, *, categoria: str | None = None) -> dict:
    nombre = str(ficha.get("nombre") or clave).strip()
    descripcion = str(ficha.get("geo_resumen") or ficha.get("descripcion") or "").strip()
    producto = {"@type": "Product", "name": nombre, "sku": clave}
    if descripcion:
        producto["description"] = descripcion
    if categoria:
        producto["category"] = categoria
    if ficha.get("marca"):
        producto["brand"] = {"@type": "Brand", "name": str(ficha["marca"]).strip()}
    if ficha.get("modelo"):
        producto["model"] = str(ficha["modelo"]).strip()
    if ficha.get("mpn"):
        producto["mpn"] = str(ficha["mpn"]).strip()
    gtin = re.sub(r"[ -]", "", str(ficha.get("gtin") or ""))
    propiedad_gtin = gtin_schema(gtin)
    if propiedad_gtin:
        producto[propiedad_gtin] = gtin
    if url_publica_valida(ficha.get("seo_url_publica")):
        producto["url"] = ficha["seo_url_publica"].strip()
    if url_publica_valida(ficha.get("seo_imagen_url")):
        producto["image"] = ficha["seo_imagen_url"].strip()

    if precio := precio_publico_clp(ficha.get("seo_precio_publico")):
        offer = {"@type": "Offer", "price": str(precio), "priceCurrency": "CLP"}
        disponibilidad = DISPONIBILIDADES.get(str(ficha.get("seo_disponibilidad") or "No indicar"))
        condicion = CONDICIONES.get(str(ficha.get("seo_condicion") or "No indicar"))
        if disponibilidad:
            offer["availability"] = disponibilidad
        if condicion:
            offer["itemCondition"] = condicion
        producto["offers"] = offer

    propiedades = []
    for linea in str(ficha.get("atributos_tecnicos") or "").splitlines():
        nombre_attr, separador, valor = linea.partition(":")
        if separador and nombre_attr.strip() and valor.strip():
            propiedades.append({"@type": "PropertyValue", "name": nombre_attr.strip(),
                                "value": valor.strip()})
    if ficha.get("unidad_venta"):
        propiedades.append({"@type": "PropertyValue", "name": "Unidad de venta",
                            "value": str(ficha["unidad_venta"]).strip()})
    if ficha.get("compatibilidad"):
        propiedades.append({"@type": "PropertyValue", "name": "Compatibilidad",
                            "value": str(ficha["compatibilidad"]).strip()})
    if propiedades:
        producto["additionalProperty"] = propiedades
    return producto


def perfil_negocio_base() -> dict:
    direccion = PERFIL_NEGOCIO["address"]
    return {
        "name": PERFIL_NEGOCIO["name"], "url": PERFIL_NEGOCIO["url"],
        "description": PERFIL_NEGOCIO["description"], "telephone": PERFIL_NEGOCIO["telephone"],
        "email": PERFIL_NEGOCIO["email"], "streetAddress": direccion["streetAddress"],
        "addressLocality": direccion["addressLocality"], "addressRegion": direccion["addressRegion"],
        "addressCountry": direccion["addressCountry"],
        "openingHours": "\n".join(PERFIL_NEGOCIO["openingHours"]),
        "areaServed": PERFIL_NEGOCIO["areaServed"],
    }


def leer_perfil_negocio(path: Path) -> dict:
    perfil = perfil_negocio_base()
    try:
        recibido = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return perfil
    if isinstance(recibido, dict):
        perfil.update({k: str(v) for k, v in recibido.items() if k in perfil})
    return perfil


def guardar_perfil_negocio(path: Path, perfil: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False,
                                mode="w", encoding="utf-8") as archivo:
            temporal = Path(archivo.name)
            json.dump({k: str(v).strip() for k, v in perfil.items()}, archivo, ensure_ascii=False, indent=2)
        os.replace(temporal, path)
    finally:
        if temporal:
            temporal.unlink(missing_ok=True)


def negocio_jsonld(perfil: dict | None = None) -> dict:
    perfil = {**perfil_negocio_base(), **(perfil or {})}
    horarios = [linea.strip() for linea in str(perfil.get("openingHours", "")).splitlines() if linea.strip()]
    direccion = {"@type": "PostalAddress"}
    for campo in ("streetAddress", "addressLocality", "addressRegion", "addressCountry"):
        if perfil.get(campo):
            direccion[campo] = perfil[campo]
    negocio = {
        "@type": "Store", "@id": f"{perfil.get('url', '').rstrip('/')}/#negocio",
        "name": perfil.get("name", "Gioval"), "url": perfil.get("url", ""),
    }
    if perfil.get("areaServed"):
        negocio["areaServed"] = perfil["areaServed"]
    if len(direccion) > 1:
        negocio["address"] = direccion
    for campo in ("description", "telephone", "email"):
        if perfil.get(campo):
            negocio[campo] = perfil[campo]
    if horarios:
        negocio["openingHours"] = horarios
    return negocio


def paquete_jsonld(productos: list[dict], perfil_negocio: dict | None = None) -> bytes:
    datos = {"@context": "https://schema.org", "@graph": [negocio_jsonld(perfil_negocio), *[
        producto_jsonld(p["clave"], p, categoria=p.get("rubro")) for p in productos
    ]]}
    return json.dumps(datos, ensure_ascii=False, indent=2).encode("utf-8")


def paquete_editorial(productos: list[dict], perfil_negocio: dict | None = None) -> bytes:
    filas = []
    for p in productos:
        clave = p["clave"]
        ficha = p
        borradores = borradores_editoriales(ficha, clave)
        imagen_publica = ficha.get("seo_imagen_url") if url_publica_valida(ficha.get("seo_imagen_url")) else None
        url_publica = ficha.get("seo_url_publica") if url_publica_valida(ficha.get("seo_url_publica")) else None
        filas.append({
            "sku": clave,
            "nombre": ficha.get("nombre") or clave,
            "marca": ficha.get("marca"),
            "modelo": ficha.get("modelo"),
            "gtin": ficha.get("gtin"),
            "mpn": ficha.get("mpn"),
            "categoria": ficha.get("rubro"),
            "descripcion": ficha.get("descripcion"),
            "seo": {
                "titulo": ficha.get("seo_titulo"),
                "descripcion": ficha.get("seo_descripcion"),
                "slug": sugerencia_slug(ficha, clave),
                "url_publica": url_publica,
                "imagen_publica": imagen_publica,
                "texto_alternativo_imagen": ficha.get("seo_imagen_alt"),
                "borradores_para_revisar": borradores,
            },
            "open_graph_y_redes": {
                "open_graph": {"og:type": "product", "og:title": borradores["titulo_seo"],
                               "og:description": borradores["descripcion_seo"],
                               "og:url": url_publica, "og:image": imagen_publica},
                "twitter_card": {"card": "summary_large_image" if imagen_publica else "summary",
                                 "title": borradores["titulo_seo"],
                                 "description": borradores["descripcion_seo"], "image": imagen_publica},
            },
            "geo": {
                "resumen": ficha.get("geo_resumen"),
                "preguntas_y_respuestas": ficha.get("geo_faq"),
                "fuente": ficha.get("geo_fuente"),
                "verificado": ficha.get("geo_verificado"),
            },
            "venta_publica": {
                "precio_iva_incluido_clp": precio_publico_clp(ficha.get("seo_precio_publico")),
                "disponibilidad": ficha.get("seo_disponibilidad"),
                "condicion": ficha.get("seo_condicion"),
            },
        })
    return json.dumps({"negocio": negocio_jsonld(perfil_negocio), "idioma": "es-CL",
                       "criterios_editoriales": GUIA_CONTENIDO, "productos": filas},
                      ensure_ascii=False, indent=2).encode("utf-8")
