"""Coincidencias de requisitos en el PDF técnico de una cotización."""
from io import BytesIO
import re
import unicodedata

from pypdf import PdfReader


MAX_BYTES = 12 * 1024 * 1024
MAX_PAGINAS = 80
CAMPO = "requisitos_ficha"


def dividir_requisitos(texto):
    return list(dict.fromkeys(r.strip() for r in texto.splitlines() if r.strip()))


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"(?<=\d),(?=\d)", ".", texto)
    texto = re.sub(r"\s*/\s*", "/", texto)
    return " ".join(texto.split())


def leer_paginas(contenido):
    try:
        lector = PdfReader(BytesIO(contenido))
        if lector.is_encrypted:
            return [], "Ficha protegida. Ábrela y revisa el requisito manualmente."
        paginas = []
        for pagina in lector.pages[:MAX_PAGINAS]:
            try:
                paginas.append(pagina.extract_text() or "")
            except Exception:
                paginas.append("")
        if not any(texto.strip() for texto in paginas):
            return [], "Ficha sin texto extraíble. Abre el PDF y revisa visualmente."
        parcial = len(lector.pages) > MAX_PAGINAS
        aviso = f"Solo se analizaron las primeras {MAX_PAGINAS} páginas." if parcial else ""
        return paginas, aviso
    except Exception:
        return [], "No se pudo leer la ficha. Revisa el archivo seleccionado."


def coincidencias(requisitos, paginas):
    texto_normalizado = [normalizar(pagina) for pagina in paginas]
    comprobacion = {}
    for requisito in requisitos:
        termino = normalizar(requisito)
        if not termino or not any(letra.isalnum() for letra in termino):
            comprobacion[requisito] = []
            continue
        patron = re.compile(r"(?<![\w.,])" + re.escape(termino).replace(r"\ ", r"\s*") + r"(?!\w|[.,]\d)")
        comprobacion[requisito] = []
        for numero, pagina in enumerate(texto_normalizado, 1):
            hallazgo = patron.search(pagina)
            if hallazgo:
                inicio, fin = hallazgo.span()
                comprobacion[requisito].append((numero, pagina[max(0, inicio - 65):fin + 65].strip()))
    return comprobacion
