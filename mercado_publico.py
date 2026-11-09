"""Consulta de solo lectura a las APIs oficiales de ChileCompra/Mercado Público."""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import requests


API_ORDEN_COMPRA = "https://api.mercadopublico.cl/servicios/v1/publico/ordenesdecompra.json"
_CODIGO_CA = re.compile(r"^\d{1,12}-\d{1,8}-COT\d{2}$", re.I)
_CODIGO_OC = re.compile(r"^\d{1,12}-\d{1,8}-[A-Z]{1,3}\d{2}$", re.I)


class ErrorMercadoPublico(ValueError):
    """Error legible de acceso o respuesta de Mercado Público."""


def buscar_ticket(carpeta_app) -> tuple[str, str]:
    """Busca el ticket en secretos o variables de entorno, nunca en la cotización."""
    for nombre in ("MERCADO_PUBLICO_TICKET", "CHILECOMPRA_API_TICKET"):
        ticket = os.environ.get(nombre, "").strip()
        if ticket:
            return ticket, "variable privada del servidor"

    try:
        import streamlit as st
        ticket = str(
            st.secrets.get("MERCADO_PUBLICO_TICKET", "")
            or st.secrets.get("CHILECOMPRA_API_TICKET", "")
        ).strip()
    except Exception:
        ticket = ""
    if ticket:
        return ticket, "secretos de Streamlit"
    return "", "no configurado"


def clasificar_codigo(codigo: str) -> str:
    codigo = str(codigo or "").strip()
    if _CODIGO_CA.fullmatch(codigo):
        return "compra_agil"
    if _CODIGO_OC.fullmatch(codigo):
        return "orden_compra"
    raise ErrorMercadoPublico(
        "Revisa el código. Una Compra Ágil suele terminar en COT26 y una orden de compra en AG26, SE26 u otro código de dos letras y año."
    )


def _peticion(url: str, ticket: str, *, params=None, header_ticket=False) -> dict:
    try:
        respuesta = requests.get(
            url,
            params=params,
            headers={"ticket": ticket} if header_ticket else None,
            timeout=(6, 25),
        )
    except requests.RequestException as exc:
        raise ErrorMercadoPublico("No pude conectar con Mercado Público. Revisa la conexión e inténtalo nuevamente.") from exc

    if respuesta.status_code == 401:
        raise ErrorMercadoPublico("El ticket de Mercado Público no fue reconocido. Comprueba el ticket configurado en Ajustes.")
    if respuesta.status_code == 403:
        raise ErrorMercadoPublico("Mercado Público rechazó el ticket. Puede estar inactivo o bloqueado.")
    if respuesta.status_code == 404:
        raise ErrorMercadoPublico("No encontré ese código en Mercado Público. Revisa el número ingresado.")
    if respuesta.status_code == 429:
        raise ErrorMercadoPublico("Mercado Público alcanzó el límite de consultas del ticket. Inténtalo más tarde.")
    if respuesta.status_code >= 500:
        raise ErrorMercadoPublico("La API de Mercado Público no está disponible en este momento. Inténtalo más tarde.")
    if respuesta.status_code != 200:
        raise ErrorMercadoPublico(f"Mercado Público respondió con el código {respuesta.status_code}.")
    try:
        return respuesta.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ErrorMercadoPublico("La respuesta de Mercado Público no llegó en un formato legible.") from exc


def _lista(valor):
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def _texto(*valores) -> str:
    return " · ".join(str(v).strip() for v in valores if v is not None and str(v).strip())


def _normalizar_compra_agil(datos: dict) -> dict:
    productos = []
    for p in _lista(datos.get("productos_solicitados")):
        if not isinstance(p, dict):
            continue
        nombre, detalle = p.get("nombre"), p.get("descripcion")
        descripcion = _texto(nombre, detalle) or str(p.get("codigo_producto") or "").strip()
        if not descripcion:
            continue
        productos.append({
            "cantidad": p.get("cantidad") or 1,
            "descripcion": descripcion,
            "unidad": p.get("unidad_medida") or "",
            "codigo": str(p.get("codigo_producto") or ""),
            "origen": "Listado de Mercado Público",
        })
    documentos = []
    for d in _lista(datos.get("documentos")):
        if isinstance(d, dict):
            documentos.append({
                "id": str(d.get("id") or ""),
                "nombre": str(d.get("nombre") or "Adjunto sin nombre"),
                "url": _url_segura(d),
            })
    inst = datos.get("institucion") if isinstance(datos.get("institucion"), dict) else {}
    entrega = datos.get("entrega") if isinstance(datos.get("entrega"), dict) else {}
    fechas = datos.get("fechas") if isinstance(datos.get("fechas"), dict) else {}
    presupuesto = datos.get("presupuesto") if isinstance(datos.get("presupuesto"), dict) else {}
    estado = datos.get("estado") if isinstance(datos.get("estado"), dict) else {}
    return {
        "tipo": "Compra Ágil",
        "codigo": str(datos.get("codigo") or ""),
        "nombre": str(datos.get("nombre") or ""),
        "descripcion": str(datos.get("descripcion") or ""),
        "institucion": str(inst.get("organismo_comprador") or ""),
        "rut_comprador": str(inst.get("rut") or ""),
        "unidad_compra": str(inst.get("unidad_compra") or ""),
        "region": str(inst.get("nombre_region") or inst.get("region") or ""),
        "estado": str(estado.get("glosa") or estado.get("codigo") or ""),
        "fecha_cierre": str(fechas.get("fecha_cierre") or ""),
        "direccion_entrega": str(entrega.get("direccion_entrega") or ""),
        "plazo_entrega": entrega.get("plazo_entrega_dias"),
        "presupuesto": presupuesto.get("monto_disponible_clp") or presupuesto.get("presupuesto_estimado"),
        "moneda": str(presupuesto.get("moneda") or "CLP"),
        "productos": productos,
        "documentos": documentos,
        "url_portal": "https://www.mercadopublico.cl/",
        "fuente_listado": "API oficial de Compra Ágil",
    }


def _valor_anidado(datos, *ruta):
    actual = datos
    for parte in ruta:
        if not isinstance(actual, dict):
            return None
        actual = actual.get(parte)
    return actual


def _normalizar_orden_compra(respuesta: dict, codigo: str) -> dict:
    listado = respuesta.get("Listado", {}) if isinstance(respuesta, dict) else {}
    ordenes = listado if isinstance(listado, list) else _lista(
        listado.get("OrdenCompra") if isinstance(listado, dict) else None
    )
    if not ordenes and isinstance(listado, dict):
        ordenes = [listado]
    if not ordenes:
        mensaje = str(respuesta.get("Mensaje") or "") if isinstance(respuesta, dict) else ""
        if mensaje:
            raise ErrorMercadoPublico(f"Mercado Público no devolvió la orden: {mensaje[:240]}")
        raise ErrorMercadoPublico("No encontré esa orden de compra en Mercado Público.")
    orden = next((o for o in ordenes if isinstance(o, dict)), None)
    if not orden:
        raise ErrorMercadoPublico("El detalle de la orden de compra llegó vacío.")
    items = _valor_anidado(orden, "Items", "Listado")
    productos = []
    for p in _lista(items):
        if not isinstance(p, dict):
            continue
        descripcion = _texto(
            p.get("NombreProducto"), p.get("EspecificacionComprador"),
            p.get("Descripcion"), p.get("EspecificacionProveedor"),
        )
        if not descripcion:
            descripcion = str(p.get("CodigoProducto") or "").strip()
        if not descripcion:
            continue
        productos.append({
            "cantidad": p.get("Cantidad") or 1,
            "descripcion": descripcion,
            "unidad": str(p.get("UnidadMedida") or ""),
            "codigo": str(p.get("CodigoProducto") or ""),
            "origen": "Listado de la orden de compra",
        })
    return {
        "tipo": "Orden de compra",
        "codigo": str(orden.get("Codigo") or codigo),
        "nombre": str(orden.get("Nombre") or ""),
        "descripcion": str(orden.get("Descripcion") or ""),
        "institucion": str(_valor_anidado(orden, "Comprador", "NombreOrganismo") or ""),
        "rut_comprador": str(_valor_anidado(orden, "Comprador", "RutUnidad") or ""),
        "unidad_compra": str(_valor_anidado(orden, "Comprador", "NombreUnidad") or ""),
        "region": str(_valor_anidado(orden, "Comprador", "RegionUnidad") or ""),
        "estado": str(orden.get("Estado") or orden.get("CodigoEstado") or ""),
        "fecha_cierre": "",
        "direccion_entrega": str(_valor_anidado(orden, "Comprador", "DireccionUnidad") or ""),
        "plazo_entrega": None,
        "presupuesto": orden.get("Total"),
        "moneda": str(orden.get("TipoMoneda") or "CLP"),
        "productos": productos,
        "documentos": [],
        "url_portal": "https://www.mercadopublico.cl/",
        "fuente_listado": "API oficial de Órdenes de Compra",
    }


def _url_segura(documento: dict) -> str:
    """Usa solo URLs HTTPS del dominio oficial si la API llegara a incluirlas."""
    from urllib.parse import urlparse
    for campo in ("url", "url_descarga", "link", "href"):
        valor = str(documento.get(campo) or "").strip()
        if not valor:
            continue
        parsed = urlparse(valor)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and (
            hostname == "mercadopublico.cl" or hostname.endswith(".mercadopublico.cl")
        ):
            return valor
    return ""


def consultar_codigo(codigo: str, ticket: str) -> dict:
    """Consulta el detalle de una Compra Ágil o una orden de compra por su código."""
    codigo = str(codigo or "").strip()
    tipo = clasificar_codigo(codigo)
    ticket = str(ticket or "").strip()
    if not ticket:
        raise ErrorMercadoPublico("Configura tu ticket en Ajustes antes de consultar Mercado Público.")
    if tipo == "compra_agil":
        from radar_comercial.servicio import APICompraAgilError, obtener_detalle_compra_agil, _request_json, API_LIST_URL
        try:
            respuesta = obtener_detalle_compra_agil(ticket, codigo)
        except APICompraAgilError as exc:
            transitorio = exc.status_code in (502, 503, 504) or isinstance(
                exc.__cause__, (requests.Timeout, requests.ConnectionError)
            )
            if not transitorio:
                raise ErrorMercadoPublico(str(exc)) from exc
            try:
                listado = _request_json(ticket, API_LIST_URL, params={"id": codigo, "tamano_pagina": 1})
                items = (listado.get("payload") or {}).get("items") or []
                encontrado = next((item for item in items if isinstance(item, dict)
                                   and str(item.get("codigo", "")).casefold() == codigo.casefold()), None)
            except (APICompraAgilError, TypeError, AttributeError) as alternativa:
                raise ErrorMercadoPublico(
                    f"{exc} La búsqueda alternativa por código tampoco pudo completarse."
                ) from alternativa
            if not encontrado:
                raise ErrorMercadoPublico(str(exc)) from exc
            resultado = _normalizar_compra_agil({**encontrado, "presupuesto": encontrado.get("montos") or {}})
            resultado["detalle_incompleto"] = True
            resultado["aviso"] = (
                "El detalle de ChileCompra no respondió. Recuperé los datos disponibles por la búsqueda "
                "oficial del código. Los productos no pudieron verificarse: carga los adjuntos para "
                "extraerlos o vuelve a consultar más tarde."
            )
            return resultado
        return _normalizar_compra_agil(respuesta)

    respuesta = _peticion(API_ORDEN_COMPRA, ticket, params={"codigo": codigo, "ticket": ticket})
    return _normalizar_orden_compra(respuesta, codigo)


def _docx_a_texto(datos: bytes) -> str:
    """Extrae texto y tablas simples de Word .docx sin guardar el adjunto."""
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(datos)) as paquete:
            xml = paquete.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ErrorMercadoPublico("No pude leer el Word. Guarda el documento como .docx e inténtalo otra vez.") from exc
    try:
        raiz = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ErrorMercadoPublico("El documento Word está dañado o incompleto.") from exc
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    texto = []
    for parrafo in raiz.findall(".//w:p", ns):
        linea = "".join(nodo.text or "" for nodo in parrafo.findall(".//w:t", ns)).strip()
        if linea:
            texto.append(linea)
    return "\n".join(texto)[:100000]


def _docx_imagenes(datos: bytes, nombre: str) -> list[tuple[str, bytes]]:
    """Toma imágenes incrustadas en Word para el mismo OCR visual de los PDFs."""
    imagenes = []
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(datos)) as paquete:
            for miembro in paquete.namelist():
                if not miembro.startswith("word/media/"):
                    continue
                ext = Path(miembro).suffix.lower()
                if ext in {".png", ".jpg", ".jpeg", ".webp"}:
                    imagenes.append((f"{Path(nombre).name} · {Path(miembro).name}", paquete.read(miembro)))
    except zipfile.BadZipFile:
        return []
    return imagenes


def leer_adjuntos(archivos, api_key: str) -> list[dict]:
    """Lee adjuntos en memoria: Excel/CSV localmente; PDF, Word e imágenes con el lector IA actual."""
    import lector_ia
    import cotizador

    archivos = list(archivos or [])
    if len(archivos) > 12:
        raise ErrorMercadoPublico("Puedes cargar hasta 12 adjuntos a la vez.")
    if sum(len(a.getvalue()) for a in archivos if hasattr(a, "getvalue")) > 50 * 1024 * 1024:
        raise ErrorMercadoPublico("El total de adjuntos supera 50 MB. Cárgalos en grupos más pequeños.")
    filas, para_ia, texto_docx = [], [], []
    for archivo in archivos or []:
        nombre = str(getattr(archivo, "name", "adjunto"))
        datos = archivo.getvalue() if hasattr(archivo, "getvalue") else bytes(archivo[1])
        extension = Path(nombre).suffix.lower()
        if extension in {".xlsx", ".csv"}:
            with tempfile.TemporaryDirectory(prefix="gioval-mp-") as temporal:
                ruta = Path(temporal) / Path(nombre).name
                ruta.write_bytes(datos)
                leidas = cotizador.leer_orden_archivo(ruta)
            filas.extend({**f, "origen": f"Adjunto: {Path(nombre).name}"} for f in leidas)
        elif extension == ".docx":
            texto = _docx_a_texto(datos)
            if texto:
                texto_docx.append(f"ARCHIVO {Path(nombre).name}:\n{texto}")
            para_ia.extend(_docx_imagenes(datos, nombre))
        elif extension in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}:
            para_ia.append((Path(nombre).name, datos))
        else:
            raise ErrorMercadoPublico(
                f"El archivo {Path(nombre).name} no tiene un formato compatible. Usa PDF, Excel, CSV, Word .docx o imagen."
            )
    if texto_docx or para_ia:
        nombres = [Path(getattr(a, "name", "adjunto")).name for a in archivos]
        texto = "\n\n".join(texto_docx)
        leidas = lector_ia.leer_orden_ia(para_ia, api_key, texto_adicional=texto)
        nombres_validos = {nombre.casefold(): nombre for nombre in nombres}
        def resolver_origen(fila):
            indicado = Path(str(fila.get("origen") or "")).name.casefold()
            if indicado in nombres_validos:
                return nombres_validos[indicado]
            # Las imágenes incrustadas en Word incluyen el nombre interno del medio.
            if indicado:
                for nombre in nombres:
                    if indicado.startswith(nombre.casefold() + " ") or indicado.startswith(nombre.casefold() + " ·"):
                        return nombre
            if len(nombres) == 1:
                return nombres[0]
            return "Adjunto (origen por confirmar)"
        filas.extend({**fila, "origen": f"Adjunto: {resolver_origen(fila)}"} for fila in leidas)
    return filas
