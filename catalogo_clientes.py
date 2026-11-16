"""Generación de catálogos de productos para clientes de Giovalia."""
from __future__ import annotations

import io
import json
import math
import os
import re
import unicodedata

import pandas as pd
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from uuid import uuid4
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas

from revisor import IVA, clp
import buscador as bs
import seo_geo

AZUL = colors.HexColor("#010ED0")
CELESTE = colors.HexColor("#EEF1FF")
TINTA = colors.HexColor("#242627")
GRIS = colors.HexColor("#626A78")
FONDO = colors.HexColor("#F7F8FC")
ACENTO = colors.HexColor("#F4B35F")
CONTACTO = ("Cristóbal Colón 334, Local 16, Galería Sangra Colón · Arica, Chile\n"
            "+56 9 9849 8758 · contacto@gioval.cl · gioval.cl\n"
            "Lun–Vie 09:30–16:00 · Sáb 11:00–14:30")
LOGO = Path(__file__).with_name("logo_gioval_catalogo.png")
_LOCK = RLock()


class ConflictoCatalogo(ValueError):
    pass


def _json_leer(path: Path) -> dict:
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError("El registro de catálogos tiene un formato inválido.")
    return value


def _json_guardar(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False, mode="w",
                                encoding="utf-8") as archivo:
            temporal = Path(archivo.name)
            json.dump(value, archivo, ensure_ascii=False, indent=2)
        os.replace(temporal, path)
    finally:
        if temporal:
            temporal.unlink(missing_ok=True)


def precio_publico(net) -> int | None:
    try:
        bruto = float(net) * (1 + IVA)
        return round(bruto) if math.isfinite(bruto) and bruto >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def precio_neto_valido(net) -> int | None:
    try:
        valor = float(net)
        return round(valor) if math.isfinite(valor) and valor >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _nombre_visible(producto):
    return str(producto.get("nombre") or producto.get("clave") or "Producto")


def _titulo_comercial(producto):
    nombre = re.sub(r"\s+", " ", _nombre_visible(producto)).strip(" .·-|")
    if len(nombre) > 74 or "ANEXO" in nombre.upper() or "CARACTERÍSTICAS TÉCNICAS" in nombre.upper():
        nombre = re.split(r"\s*[·|;]\s*|(?<=[.!?])\s+|,\s+", nombre, maxsplit=1)[0]
    if len(nombre) > 64:
        nombre = re.split(r",\s+", nombre, maxsplit=1)[0]
    nombre = nombre.strip(" .·-|") or str(producto.get("clave") or "Producto").replace("-", " ").title()
    if nombre.isupper():
        nombre = nombre.title()
        for original, comercial in (("Hp", "HP"), ("Usb", "USB"), ("Ssd", "SSD"),
                                    ("A3", "A3"), ("A4", "A4"), ("Xl", "XL"),
                                    ("Cmyk", "CMYK"), ("Wi-Fi", "Wi-Fi"),
                                    ("Epson Ecotank", "Epson EcoTank"),
                                    ("Tn ", "TN "), ("Bk", "BK"), ("Cyan", "cian")):
            nombre = re.sub(re.escape(original), comercial, nombre, flags=re.IGNORECASE)
        nombre = re.sub(r"\b([A-Z]{1,4})\s+(\d{2,5}[A-Z]?)\b", r"\1 \2", nombre)
    return nombre


def _descripcion_comercial(producto):
    titulo = _titulo_comercial(producto)
    texto = re.sub(r"<[^>]+>", " ", str(producto.get("geo_resumen") or producto.get("descripcion") or ""))
    texto = re.sub(r"\s+", " ", texto).strip(" .;·-")
    if texto and texto.casefold().startswith(titulo.casefold()):
        texto = texto[len(titulo):].strip(" ,.;:·-")
    texto = re.sub(r"(?i)las características técnicas se pueden encontrar en el anexo[^.]*\.?", "", texto)
    texto = re.sub(r"(?i)ver anexo(?:\s+n[°º.]?\s*\d+)?[^.]*\.?", "", texto).strip(" .;·-")
    if texto and texto.casefold() not in {titulo.casefold(), str(producto.get("clave", "")).casefold()}:
        return _breve(texto, 190).strip(" ,;:-")
    rubro = str(producto.get("rubro") or "").casefold()
    nombre = titulo.casefold()
    if "plotter" in nombre:
        return f"{titulo}. Equipo de impresión de gran formato; consulta disponibilidad con Gioval."
    if any(p in nombre for p in ("impresora", "multifuncional", "scanner", "escáner")):
        return f"{titulo}. Equipo de impresión para el trabajo y el estudio. Consulta detalles y disponibilidad."
    if any(p in nombre for p in ("tinta", "tóner", "toner", "cartucho", "drum", "tambor")):
        return f"{titulo}. Confirma la referencia compatible con tu impresora antes de comprar."
    if any(p in nombre for p in ("papel", "resma", "hoja", "cartulina", "block")):
        return f"{titulo}. Papel para impresión, oficina o proyectos creativos."
    if any(p in nombre for p in ("lápiz", "lapiz", "plumón", "plumon", "marcador", "destacador", "bolígrafo", "lapicera")):
        return f"{titulo}. Artículo de escritura para clases, oficina y tareas cotidianas."
    if any(p in nombre for p in ("carpeta", "archivador", "funda", "separador", "archivo")):
        return f"{titulo}. Ayuda a mantener documentos y materiales organizados."
    if any(p in nombre for p in ("cinta", "adhesivo", "pegamento", "silicona")):
        return f"{titulo}. Insumo práctico para trabajos de oficina y uso cotidiano."
    if any(p in nombre for p in ("mouse", "teclado", "monitor", "notebook", "computador", "disco", "ssd", "usb")):
        return f"{titulo}. Accesorio de tecnología para complementar tu espacio de trabajo."
    if any(p in rubro for p in ("electrónica", "electronica", "tecnología", "tecnologia", "equipos")):
        return f"{titulo}. Tecnología y equipamiento para acompañar tu trabajo diario."
    return f"{titulo}. Artículo de librería y oficina para el estudio y el trabajo diario."


def _breve(texto, limite):
    return texto if len(texto) <= limite else texto[:limite].rsplit(" ", 1)[0] + "…"


def _foto_portable(path: Path | None, max_px: int = 440) -> bytes | None:
    if not path:
        return None
    try:
        with PILImage.open(path) as image:
            image.thumbnail((max_px, max_px))
            if image.mode not in ("RGB", "L"):
                rgba = image.convert("RGBA")
                image = PILImage.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=86, optimize=True)
            return out.getvalue()
    except (OSError, ValueError):
        return None


def _logo_portable(path: Path | None, max_px: int = 850) -> bytes | None:
    """Conserva la transparencia del logo para que no forme una caja blanca."""
    if not path:
        return None
    try:
        with PILImage.open(path) as image:
            image = image.convert("RGBA")
            image.thumbnail((max_px, max_px))
            out = io.BytesIO()
            image.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except (OSError, ValueError):
        return None


def _lineas(texto, font, size, max_ancho, max_lineas=3):
    palabras, lineas, actual = str(texto).split(), [], ""
    for palabra in palabras:
        prueba = f"{actual} {palabra}".strip()
        if stringWidth(prueba, font, size) <= max_ancho or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
            if len(lineas) >= max_lineas - 1:
                break
    if actual and len(lineas) < max_lineas:
        lineas.append(actual)
    return lineas[:max_lineas]


def _ilustracion_producto(c, x, y, w, h, producto):
    texto = f"{_titulo_comercial(producto)} {producto.get('descripcion', '')}".casefold()
    c.saveState()
    c.setFillColor(CELESTE)
    c.roundRect(x, y, w, h, 9, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.circle(x + w - 35, y + h - 32, 23, fill=1, stroke=0)
    cx, cy = x + w / 2, y + h * .52
    c.setStrokeColor(AZUL)
    c.setFillColor(colors.white)
    c.setLineWidth(3)
    if any(t in texto for t in ("papel", "resma", "block", "hoja", "cuaderno", "libreta", "cartulina")):
        c.roundRect(cx - 35, cy - 32, 67, 78, 4, fill=1, stroke=1)
        c.setFillColor(colors.white)
        c.roundRect(cx - 43, cy - 38, 67, 78, 4, fill=1, stroke=1)
        c.setStrokeColor(colors.HexColor("#AAB7F7"))
        for off in (-17, -5, 7):
            c.line(cx - 30, cy + off, cx + 10, cy + off)
    elif any(t in texto for t in ("impresora", "plotter", "scanner", "multifuncional")):
        c.roundRect(cx - 43, cy - 22, 86, 49, 8, fill=1, stroke=1)
        c.setFillColor(colors.white)
        c.rect(cx - 28, cy + 17, 56, 35, fill=1, stroke=1)
        c.setFillColor(CELESTE)
        c.rect(cx - 30, cy - 7, 60, 15, fill=1, stroke=0)
        c.setStrokeColor(AZUL)
        c.line(cx - 22, cy - 17, cx + 22, cy - 17)
        c.circle(cx + 30, cy - 4, 3, fill=1, stroke=0)
    elif any(t in texto for t in ("tinta", "toner", "tóner", "cartucho", "drum", "tambor")):
        c.roundRect(cx - 26, cy - 37, 52, 78, 8, fill=1, stroke=1)
        c.setFillColor(AZUL)
        c.roundRect(cx - 18, cy - 14, 36, 25, 4, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(cx, cy - 5, "G")
    elif any(t in texto for t in ("cinta", "adhesiva", "scotch")):
        c.circle(cx, cy, 39, fill=1, stroke=1)
        c.setFillColor(CELESTE)
        c.circle(cx, cy, 17, fill=1, stroke=0)
        c.setStrokeColor(ACENTO)
        c.setLineWidth(5)
        c.arc(cx - 25, cy - 25, cx + 25, cy + 25, 25, 125)
    elif any(t in texto for t in ("lapiz", "lápiz", "plumon", "plumón", "marcador", "destacador", "regla")):
        c.setStrokeColor(AZUL)
        c.setLineWidth(12)
        c.line(cx - 34, cy - 30, cx + 34, cy + 30)
        c.setStrokeColor(ACENTO)
        c.setLineWidth(4)
        c.line(cx - 41, cy - 37, cx - 32, cy - 28)
    elif any(t in texto for t in ("carpeta", "archivador", "funda", "separador")):
        p = c.beginPath()
        p.moveTo(cx - 42, cy - 27); p.lineTo(cx - 42, cy + 29); p.lineTo(cx - 8, cy + 29)
        p.lineTo(cx + 1, cy + 40); p.lineTo(cx + 42, cy + 40); p.lineTo(cx + 42, cy - 27); p.close()
        c.drawPath(p, fill=1, stroke=1)
        c.setFillColor(CELESTE); c.roundRect(cx - 34, cy - 18, 68, 47, 3, fill=1, stroke=0)
    elif any(t in texto for t in ("pila", "batería", "bateria")):
        c.roundRect(cx - 20, cy - 38, 40, 77, 8, fill=1, stroke=1)
        c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 20); c.drawCentredString(cx, cy - 7, "+")
    elif any(t in texto for t in ("mouse", "teclado", "monitor", "comput", "usb", "electr", "disco", "ssd")):
        c.roundRect(cx - 43, cy - 26, 86, 55, 7, fill=1, stroke=1)
        c.setFillColor(CELESTE); c.roundRect(cx - 34, cy - 17, 68, 37, 3, fill=1, stroke=0)
        c.setStrokeColor(AZUL); c.setLineWidth(4); c.line(cx, cy - 27, cx, cy - 37)
        c.line(cx - 18, cy - 38, cx + 18, cy - 38)
    else:
        c.circle(cx, cy, 38, fill=1, stroke=1)
        c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 30); c.drawCentredString(cx, cy - 11, "G")
    c.setFillColor(TINTA)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(cx, y + 10, "IMAGEN REFERENCIAL")
    c.restoreState()


def generar_pdf(productos: list[dict], nombre: str, vigencia: str, contacto: str,
                logo: Path | None = None) -> bytes:
    """Catálogo de marca para compartir por móvil e imprimir en A4."""
    buffer = io.BytesIO()
    ancho, alto = A4
    c = pdfcanvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    c.setTitle(f"Gioval | {nombre}")
    c.setAuthor("Gioval · gioval.cl")
    c.setSubject("Catálogo comercial · precios netos más IVA")
    margen = 34
    logo_bytes = _logo_portable(logo or LOGO, max_px=850)

    rubros = {}
    for producto in productos:
        rubros.setdefault(producto.get("rubro") or "Productos", []).append(producto)

    # Portada: identidad original de Gioval, clara, cordial y con acento tecnológico.
    c.setFillColor(FONDO); c.rect(0, 0, ancho, alto, fill=1, stroke=0)
    c.setFillColor(AZUL); c.roundRect(ancho - 184, alto - 299, 214, 310, 34, fill=1, stroke=0)
    c.setFillColor(colors.white); c.circle(ancho - 95, alto - 168, 76, fill=1, stroke=0)
    c.setFillColor(CELESTE); c.circle(ancho - 95, alto - 168, 60, fill=1, stroke=0)
    c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 42); c.drawCentredString(ancho - 95, alto - 182, "G")
    c.setFillColor(ACENTO); c.circle(ancho - 36, alto - 285, 16, fill=1, stroke=0)
    if logo_bytes:
        c.drawImage(ImageReader(io.BytesIO(logo_bytes)), margen, alto - 70, width=116, height=27,
                    preserveAspectRatio=True, anchor="sw", mask="auto")
    else:
        c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 25); c.drawString(margen, alto - 57, "Gv")
        c.setFillColor(TINTA); c.drawString(margen + 37, alto - 57, "GIOVAL")
    c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 8)
    c.drawString(margen, alto - 98, "PAPELERÍA · IMPRESIÓN · TECNOLOGÍA")
    c.setFillColor(TINTA); c.setFont("Helvetica-Bold", 31)
    c.drawString(margen, alto - 164, "Todo para")
    c.drawString(margen, alto - 202, "trabajar, estudiar")
    c.drawString(margen, alto - 240, "e imprimir mejor.")
    c.setFillColor(GRIS); c.setFont("Helvetica", 12)
    c.drawString(margen, alto - 270, "Soluciones útiles, precios claros y atención cercana.")
    c.setFillColor(AZUL); c.roundRect(margen, alto - 307, 127, 25, 12, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(margen + 63.5, alto - 298, f"{len(productos)} PRODUCTOS")
    c.setFillColor(GRIS); c.setFont("Helvetica", 9)
    c.drawString(margen, alto - 338, "Una empresa familiar del norte de Chile, lista para ayudarte.")

    c.setFillColor(TINTA); c.setFont("Helvetica-Bold", 13)
    c.drawString(margen, alto - 398, "Explora por categoría")
    fams = list(rubros.items())
    col_w = (ancho - 2 * margen - 18) / 2
    for i, (rubro, filas) in enumerate(fams[:12]):
        col, row = i % 2, i // 2
        x = margen + col * (col_w + 18)
        y = alto - 432 - row * 31
        c.setFillColor(colors.white); c.roundRect(x, y - 8, col_w, 24, 8, fill=1, stroke=0)
        c.setFillColor(AZUL); c.circle(x + 12, y + 4, 3, fill=1, stroke=0)
        c.setFillColor(TINTA); c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 22, y + 1, _breve(rubro, 31))
        c.setFillColor(GRIS); c.setFont("Helvetica", 8)
        c.drawRightString(x + col_w - 9, y + 1, str(len(filas)))
    if len(fams) > 12:
        c.setFillColor(GRIS); c.setFont("Helvetica", 8)
        c.drawString(margen, alto - 630, f"Y {len(fams) - 12} categorías más en el interior.")

    pilares = (("Atención cercana", "Una empresa familiar del norte"),
               ("Cotización sin costo", "Precios claros para tu negocio"),
               ("Despacho a todo Chile", "Coordinamos contigo desde Arica"))
    pillar_gap = 10
    pillar_w = (ancho - 2 * margen - 2 * pillar_gap) / 3
    for i, (titulo, detalle) in enumerate(pilares):
        x = margen + i * (pillar_w + pillar_gap)
        c.setFillColor(colors.white); c.roundRect(x, 150, pillar_w, 55, 9, fill=1, stroke=0)
        c.setFillColor(AZUL); c.circle(x + 12, 190, 3, fill=1, stroke=0)
        c.setFillColor(TINTA); c.setFont("Helvetica-Bold", 7.5); c.drawString(x + 21, 187, titulo)
        c.setFillColor(GRIS); c.setFont("Helvetica", 6.2); c.drawString(x + 10, 169, detalle)

    y_contacto = 105
    c.setFillColor(AZUL); c.roundRect(margen, 33, ancho - 2 * margen, 48, 13, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 9)
    c.drawString(margen + 13, 61, "Hablemos de lo que necesitas")
    c.setFont("Helvetica", 8); c.drawString(margen + 13, 47, "+56 9 9849 8758 · contacto@gioval.cl · gioval.cl")
    c.setFillColor(GRIS); c.setFont("Helvetica", 8)
    c.drawString(margen, y_contacto, "Cristóbal Colón 334, Local 16 · Galería Sangra Colón · Arica, Chile")
    if vigencia:
        c.drawString(margen, y_contacto - 14, f"Vigencia de precios: {vigencia}")
    c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 8)
    c.drawRightString(ancho - margen, 96, "PRECIOS NETOS · + IVA")
    c.showPage()

    card_gap = 13
    card_w = (ancho - 2 * margen - card_gap) / 2
    card_h = 327
    top = alto - 80
    productos_ordenados = [(rubro, producto) for rubro, filas in rubros.items() for producto in filas]
    paginas = [productos_ordenados[i:i + 4] for i in range(0, len(productos_ordenados), 4)]
    page = 1
    for grupo in paginas:
            familias_pagina = list(dict.fromkeys(familia for familia, _ in grupo))
            destacado = len(grupo) == 1
            c.setFillColor(FONDO); c.rect(0, 0, ancho, alto, fill=1, stroke=0)
            c.setFillColor(AZUL); c.rect(0, alto - 57, ancho, 57, fill=1, stroke=0)
            # En la franja azul se usa una firma blanca vectorial: el wordmark
            # original tiene letras oscuras y no ofrece contraste sobre azul.
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 16)
            c.drawString(margen, alto - 34, "Gv")
            c.setFont("Helvetica-Bold", 13)
            c.drawString(margen + 28, alto - 33, "GIOVAL")
            c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 8)
            etiqueta_familias = familias_pagina[0] if len(familias_pagina) == 1 else "Librería · impresión · tecnología"
            c.drawRightString(ancho - margen, alto - 31, etiqueta_familias.upper()[:44])
            titulo_familia = familias_pagina[0] if len(familias_pagina) == 1 else "Productos por categoría"
            c.setFillColor(TINTA); c.setFont("Helvetica-Bold", 15)
            c.drawString(margen, alto - 75, _breve(titulo_familia, 50))
            c.setFillColor(GRIS); c.setFont("Helvetica", 8)
            c.drawRightString(ancho - margen, alto - 75, "Valores netos · + IVA")
            if destacado:
                familia, producto = grupo[0]
                x, y0 = margen, alto - 130
                card_w_dest, card_h_dest = ancho - 2 * margen, 430
                bottom = y0 - card_h_dest
                c.setFillColor(colors.white); c.setStrokeColor(colors.HexColor("#E4E7F0"))
                c.roundRect(x, bottom, card_w_dest, card_h_dest, 14, fill=1, stroke=1)
                image_x, image_y, image_w, image_h = x + 13, bottom + 13, 248, card_h_dest - 26
                c.setFillColor(CELESTE); c.roundRect(image_x, image_y, image_w, image_h, 10, fill=1, stroke=0)
                foto = _foto_portable(producto.get("foto_path"), max_px=1100)
                if foto:
                    c.drawImage(ImageReader(io.BytesIO(foto)), image_x + 6, image_y + 6,
                                width=image_w - 12, height=image_h - 12, preserveAspectRatio=True,
                                anchor="c", mask="auto")
                else:
                    _ilustracion_producto(c, image_x, image_y, image_w, image_h, producto)
                tx = image_x + image_w + 18
                tw = x + card_w_dest - tx - 13
                c.setFillColor(CELESTE); c.roundRect(tx, y0 - 56, tw, 18, 8, fill=1, stroke=0)
                c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 7)
                c.drawString(tx + 8, y0 - 50, _breve(familia.upper(), 38))
                title = _titulo_comercial(producto)
                ty = y0 - 87
                for linea in _lineas(title, "Helvetica-Bold", 17, tw - 5, 4):
                    c.setFillColor(TINTA); c.setFont("Helvetica-Bold", 17); c.drawString(tx, ty, linea); ty -= 21
                ty -= 5
                for linea in _lineas(_descripcion_comercial(producto), "Helvetica", 11, tw - 5, 5):
                    c.setFillColor(GRIS); c.setFont("Helvetica", 11); c.drawString(tx, ty, linea); ty -= 15
                c.setFillColor(GRIS); c.setFont("Helvetica", 8)
                c.drawString(tx, bottom + 86, f"CÓDIGO  {producto.get('clave', '')}"[:48])
                neto = precio_neto_valido(producto.get("precio_neto_catalogo", producto.get("precio_neto")))
                c.setFillColor(AZUL); c.roundRect(tx - 2, bottom + 15, tw + 2, 58, 10, fill=1, stroke=0)
                c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 8); c.drawString(tx + 9, bottom + 55, "PRECIO NETO")
                c.setFont("Helvetica-Bold", 18); c.drawString(tx + 9, bottom + 29, clp(neto) if neto is not None else "Consultar precio")
                c.setFont("Helvetica-Bold", 11); c.drawRightString(tx + tw - 9, bottom + 39, "+ IVA")
            else:
                for idx, (familia, producto) in enumerate(grupo):
                    col, row = idx % 2, idx // 2
                    x = margen + col * (card_w + card_gap)
                    y0 = top - row * (card_h + 13)
                    bottom = y0 - card_h
                    c.setFillColor(colors.white); c.setStrokeColor(colors.HexColor("#E4E7F0"))
                    c.roundRect(x, bottom, card_w, card_h, 12, fill=1, stroke=1)
                    image_x, image_y, image_w, image_h = x + 10, y0 - 147, card_w - 20, 136
                    c.setFillColor(CELESTE); c.roundRect(image_x, image_y, image_w, image_h, 9, fill=1, stroke=0)
                    foto = _foto_portable(producto.get("foto_path"), max_px=900)
                    if foto:
                        c.drawImage(ImageReader(io.BytesIO(foto)), image_x + 5, image_y + 5,
                                    width=image_w - 10, height=image_h - 10, preserveAspectRatio=True,
                                    anchor="c", mask="auto")
                    else:
                        _ilustracion_producto(c, image_x, image_y, image_w, image_h, producto)
                    c.setFillColor(CELESTE); c.roundRect(x + 12, y0 - 164, card_w - 24, 14, 7, fill=1, stroke=0)
                    c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 6.5)
                    c.drawString(x + 18, y0 - 160, _breve(familia, 42))
                    title = _titulo_comercial(producto)
                    ty = y0 - 182
                    for linea in _lineas(title, "Helvetica-Bold", 11, card_w - 23, 2):
                        c.setFillColor(TINTA); c.setFont("Helvetica-Bold", 11)
                        c.drawString(x + 12, ty, linea)
                        ty -= 14
                    descripcion = _descripcion_comercial(producto)
                    ty -= 2
                    for linea in _lineas(descripcion, "Helvetica", 8.5, card_w - 24, 4):
                        c.setFillColor(GRIS); c.setFont("Helvetica", 8.5)
                        c.drawString(x + 12, ty, linea)
                        ty -= 11
                    c.setFillColor(GRIS); c.setFont("Helvetica", 7)
                    c.drawString(x + 12, bottom + 62, f"CÓDIGO  {producto.get('clave', '')}"[:48])
                    neto = producto.get("precio_neto_catalogo", producto.get("precio_neto"))
                    neto = precio_neto_valido(neto)
                    c.setFillColor(AZUL); c.roundRect(x + 9, bottom + 10, card_w - 18, 43, 9, fill=1, stroke=0)
                    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 7)
                    c.drawString(x + 17, bottom + 39, "PRECIO NETO")
                    c.setFont("Helvetica-Bold", 16)
                    c.drawString(x + 17, bottom + 19, clp(neto) if neto is not None else "Consultar precio")
                    c.setFont("Helvetica-Bold", 10); c.drawRightString(x + card_w - 17, bottom + 27, "+ IVA")
            c.setStrokeColor(colors.HexColor("#DFE2EC")); c.line(margen, 26, ancho - margen, 26)
            c.setFillColor(GRIS); c.setFont("Helvetica", 7)
            c.drawString(margen, 13, "Gioval · Atención cercana en Arica y despacho a todo Chile · gioval.cl")
            c.setFillColor(AZUL); c.setFont("Helvetica-Bold", 7)
            c.drawRightString(ancho - margen, 13, f"{page:02d}")
            c.showPage(); page += 1
    c.save()
    return buffer.getvalue()


def _borrador(valor):
    """Lee selecciones antiguas que guardaban solo los códigos."""
    if isinstance(valor, list):
        return {"claves": valor}
    return valor if isinstance(valor, dict) else {}


def guardar_borrador(path, nombre, datos, *, anterior, version):
    with _LOCK:
        registro = _json_leer(path)
        selecciones = registro.setdefault("selecciones", {})
        if anterior is not None and selecciones.get(anterior) != version:
            raise ConflictoCatalogo("El borrador cambió en otra sesión. Vuelve a abrirlo antes de guardar.")
        if any(k != anterior and k.casefold() == nombre.casefold() for k in selecciones):
            raise ConflictoCatalogo("Ya existe un catálogo con ese nombre.")
        if anterior is not None and anterior != nombre:
            selecciones.pop(anterior, None)
        selecciones[nombre] = datos
        _json_guardar(path, registro)


def emitir(path, productos, nombre, vigencia, contacto):
    pdf = generar_pdf(productos, nombre, vigencia, contacto)
    fecha = datetime.now().astimezone()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", nombre).strip("_")[:50] or "gioval"
    filename = f"catalogo_{slug}_{fecha:%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.pdf"
    salida = path.parent / "catalogos_emitidos" / filename
    with _LOCK:
        registro = _json_leer(path)
        salida.parent.mkdir(parents=True, exist_ok=True)
        archivo = salida.open("xb")
        try:
            with archivo:
                archivo.write(pdf)
            registro.setdefault("emitidos", []).append({
                "archivo": filename, "nombre": nombre,
                "fecha": fecha.isoformat(timespec="seconds"), "vigencia": vigencia,
                "claves": [p["clave"] for p in productos],
            })
            _json_guardar(path, registro)
        except (OSError, ValueError, TypeError):
            salida.unlink(missing_ok=True)
            raise
    return filename


def _descarga(st, path, nombre, *, key, etiqueta="Descargar PDF"):
    if not nombre or Path(nombre).name != nombre or not nombre.endswith(".pdf"):
        return
    archivo = path.parent / "catalogos_emitidos" / nombre
    try:
        contenido = archivo.read_bytes()
    except OSError:
        st.warning("El PDF archivado no está disponible.")
        return
    st.download_button(etiqueta, contenido, file_name=archivo.name, mime="application/pdf",
                       key=key, icon=":material/download:", on_click="ignore")


def _archivo(st, path, registro):
    emitidos = {p["archivo"]: p for p in reversed(registro.get("emitidos", [])) if p.get("archivo")}
    if not emitidos:
        st.info("Aquí aparecerán los PDF generados desde Giovalia.")
        return
    def etiqueta(nombre):
        item = emitidos[nombre]
        fecha = (item.get("fecha") or "")[:16].replace("T", " · ")
        return f"{item.get('nombre') or 'Catálogo'} · {fecha}"

    elegido = st.selectbox("Versión", list(emitidos), format_func=etiqueta, key="catalogo_archivo")
    version = emitidos[elegido]
    st.caption(f"{len(version.get('claves', []))} productos")
    if vigencia := version.get("vigencia"):
        st.caption(f"Vigencia · {vigencia}")
    _descarga(st, path, elegido, key="catalogo_archivo_descarga")


def _seo_geo_ui(st, biblioteca, clasificar, data_dir):
    st.subheader("Preparación SEO + GEO", anchor=False, icon=":material/travel_explore:")
    st.caption("Completa datos verificables desde la ficha maestra. El inventario y los PDF quedan preparados "
               "para una futura publicación web; esta sección no publica cambios en gioval.cl.")
    perfil_path = Path(data_dir) / "perfil_seo_geo.json"
    perfil = seo_geo.leer_perfil_negocio(perfil_path)
    productos = []
    auditoria = []
    listos = 0
    suma = 0
    for clave, ficha in sorted(biblioteca.indice.items()):
        rubro = clasificar(clave, ficha)["rubro"] if clasificar else "Productos"
        foto = bool(biblioteca.archivos(clave)[0])
        producto = {"clave": clave, **ficha, "rubro": rubro}
        estado = seo_geo.evaluar_ficha(producto, tiene_foto=foto)
        productos.append(producto)
        listos += estado["listo"]
        suma += estado["porcentaje"]
        auditoria.append({"Producto": ficha.get("nombre") or clave, "Código": clave,
                          "Categoría": rubro, "Preparación": f"{estado['porcentaje']} %",
                          "Pendientes": " · ".join(estado["faltantes"]) or "Completo"})

    media = round(suma / max(1, len(auditoria)))
    a, b, c = st.columns(3)
    a.metric("Fichas de producto", len(productos), border=True)
    b.metric("SEO/GEO completo", listos, border=True)
    c.metric("Avance medio", f"{media} %", border=True)

    with st.expander("Perfil público del negocio", icon=":material/storefront:"):
        with st.form("seo_geo_perfil_negocio"):
            a, b = st.columns(2)
            negocio_nombre = a.text_input("Nombre comercial", value=perfil["name"], max_chars=120)
            negocio_url = b.text_input("Sitio web", value=perfil["url"], max_chars=300)
            negocio_descripcion = st.text_area("Descripción del negocio", value=perfil["description"],
                                               max_chars=500, height=80)
            a, b = st.columns(2)
            negocio_telefono = a.text_input("Teléfono público", value=perfil["telephone"], max_chars=60)
            negocio_email = b.text_input("Correo público", value=perfil["email"], max_chars=180)
            negocio_direccion = st.text_input("Dirección", value=perfil["streetAddress"], max_chars=240)
            a, b, c = st.columns(3)
            negocio_ciudad = a.text_input("Ciudad", value=perfil["addressLocality"], max_chars=100)
            negocio_region = b.text_input("Región", value=perfil["addressRegion"], max_chars=100)
            negocio_pais = c.text_input("Código país", value=perfil["addressCountry"], max_chars=2)
            negocio_area = st.text_input("Área de atención o despacho", value=perfil["areaServed"], max_chars=180)
            negocio_horario = st.text_area(
                "Horarios (uno por línea, formato Schema.org)", value=perfil["openingHours"],
                max_chars=240, height=80, help="Ej.: Mo-Fr 09:30-16:00 y Sa 11:00-14:30")
            guardar_perfil = st.form_submit_button("Guardar perfil del negocio", icon=":material/save:")
        if guardar_perfil:
            nuevo_perfil = {
                "name": negocio_nombre.strip(), "url": negocio_url.strip(),
                "description": negocio_descripcion.strip(), "telephone": negocio_telefono.strip(),
                "email": negocio_email.strip(), "streetAddress": negocio_direccion.strip(),
                "addressLocality": negocio_ciudad.strip(), "addressRegion": negocio_region.strip(),
                "addressCountry": negocio_pais.strip().upper(), "areaServed": negocio_area.strip(),
                "openingHours": negocio_horario.strip(),
            }
            if not nuevo_perfil["name"]:
                st.error("El nombre comercial no puede estar vacío.")
            elif not seo_geo.url_publica_valida(nuevo_perfil["url"]):
                st.error("Ingresa la URL HTTPS real del sitio público.")
            else:
                try:
                    seo_geo.guardar_perfil_negocio(perfil_path, nuevo_perfil)
                except OSError as error:
                    st.error(f"No se pudo guardar el perfil: {error}")
                else:
                    perfil = nuevo_perfil
                    st.success("Perfil público actualizado. Se utilizará en las exportaciones SEO/GEO.")

    with st.expander("Estándar editorial para todo Gioval", icon=":material/verified:"):
        st.write(seo_geo.GUIA_CONTENIDO)
        st.markdown(
            "- Mantén el nombre **Gioval** y los datos de contacto del negocio consistentes en web, catálogo y redes.\n"
            "- Prioriza marca, modelo, formato, unidad y compatibilidad confirmados por envase o fabricante.\n"
            "- Para respuestas GEO, añade resumen directo, preguntas reales, fuente y fecha de verificación.\n"
            "- No publiques precios, stock, certificaciones ni atributos que no estén confirmados.\n"
            "- Usa fotos accesibles por URL pública y texto alternativo que describa el producto.\n"
            "- En la web, alinea título, descripción, URL canónica y JSON-LD con lo que la persona puede ver. "
            "Mantén sitemap y enlaces internos vigentes; valida el marcado después de publicar."
        )

    st.caption("Para editar una ficha: Biblioteca → abrir producto → Editar datos del producto → Datos SEO y GEO.")
    x, y = st.columns(2)
    consulta = x.text_input("Buscar en el inventario SEO/GEO", key="seo_geo_buscar",
                            placeholder="Nombre, código o categoría").strip().casefold()
    estado = y.selectbox("Mostrar preparación", ["Todas", "Incompletas", "Completas"], key="seo_geo_estado")
    visibles = []
    for row in auditoria:
        texto = f"{row['Producto']} {row['Código']} {row['Categoría']}".casefold()
        completo = row["Pendientes"] == "Completo"
        if consulta and consulta not in texto:
            continue
        if estado == "Incompletas" and completo or estado == "Completas" and not completo:
            continue
        visibles.append(row)
    st.dataframe(visibles, width="stretch", height=340)

    if not productos:
        return
    x, y = st.columns(2)
    x.download_button("Descargar datos SEO + GEO", seo_geo.paquete_editorial(productos, perfil),
                      file_name="gioval_seo_geo_productos.json", mime="application/json",
                      key="seo_geo_descargar_paquete", icon=":material/download:", on_click="ignore")
    y.download_button("Descargar marcado Product JSON-LD", seo_geo.paquete_jsonld(productos, perfil),
                      file_name="gioval_product_jsonld.json", mime="application/ld+json",
                      key="seo_geo_descargar_jsonld", icon=":material/code:", on_click="ignore")
    st.caption("El JSON-LD solo agrega precio público, disponibilidad, URL o imagen cuando se completan "
               "explícitamente en la ficha; no convierte el último precio cotizado en una oferta pública.")


FAMILIAS = ("Tintas y tóners", "Papel", "Librería", "Impresión", "Otros")


def _normalizar(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", plano).strip()


def _categoria(producto: dict, meta: dict) -> str:
    definida = meta.get(producto["clave"], {}).get("categoria")
    if definida in FAMILIAS:
        return definida
    texto = _normalizar(f"{producto.get('clave', '')} {producto.get('nombre', '')}")
    if any(p in texto for p in ("tinta", "toner", "cartucho", "botella ink", "drum", "tambor", "waste toner")):
        return "Tintas y tóners"
    if any(p in texto for p in ("resma", "papel", "gramaje", "carta", "oficio", "pliego", "fotografico")):
        return "Papel"
    if any(p in texto for p in ("impresion", "etiqueta", "adhesivo", "papel transfer")):
        return "Impresión"
    if any(p in texto for p in ("lapiz", "cuaderno", "tijera", "regla", "carpeta", "engrapadora", "clips", "marcador", "corrector", "pegamento")):
        return "Librería"
    return "Otros"


def _productos(biblioteca, clasificar):
    filas = []
    for p in biblioteca.filas_catalogo():
        ficha = biblioteca.indice[p["clave"]]
        rubro = clasificar(p["clave"], ficha)["rubro"] if clasificar else "Productos"
        filas.append({**p, "descripcion": ficha.get("descripcion", ""),
                      "geo_resumen": ficha.get("geo_resumen", ""), "rubro": rubro,
                      "precio_neto_catalogo": precio_neto_valido(p.get("precio_neto"))})
    return sorted(filas, key=lambda p: (p["rubro"], _nombre_visible(p).casefold(), p["clave"]))


def _tokens(texto):
    tokens = set(re.sub(r"[^A-Z0-9]+", " ", str(texto).upper()).split())
    aliases = {"C": "CYAN", "M": "MAGENTA", "Y": "AMARILLO", "K": "NEGRO",
               "BK": "NEGRO", "CIAN": "CYAN", "BLACK": "NEGRO", "YELLOW": "AMARILLO"}
    tokens = {aliases.get(token, token) for token in tokens}
    return {p for p in tokens if p not in {"O", "DE", "THE", "PARA", "CON", "AL", "UNA", "UN"}}


def _confianza_foto(producto, candidato):
    texto_producto = " ".join(str(producto.get(campo) or "")
                              for campo in ("clave", "nombre", "descripcion"))
    texto_titulo = str(candidato.get("titulo", ""))
    consulta, titulo = _tokens(texto_producto), _tokens(texto_titulo)
    puntaje = len(consulta & titulo) / len(consulta) if consulta else 0.0

    # Las variantes son parte de la identidad comercial: no confundir original,
    # alternativo, colores ni un pack completo con una unidad suelta.
    alternativo = bool(consulta & {"ALTERNATIVO", "ALTERNATIVA", "EQUIVALENTE"})
    original = "ORIGINAL" in consulta
    titulo_alternativo = bool(titulo & {"ALTERNATIVO", "ALTERNATIVA", "COMPATIBLE", "EQUIVALENTE"})
    titulo_original = "ORIGINAL" in titulo
    if alternativo and titulo_original and not titulo_alternativo:
        return 0.0
    if original and titulo_alternativo and not titulo_original:
        return 0.0
    if alternativo and not titulo_alternativo:
        puntaje = min(puntaje, 0.65)

    colores = {"NEGRO", "CYAN", "MAGENTA", "AMARILLO", "AZUL", "ROJO", "VERDE"}
    color_producto, color_titulo = consulta & colores, titulo & colores
    if "CMYK" in titulo:
        color_titulo.update({"CYAN", "MAGENTA", "AMARILLO", "NEGRO"})
    if color_producto and color_titulo and color_producto != color_titulo:
        return 0.0
    if len(color_producto) == 1 and len(color_titulo) > 1:
        return 0.0
    if color_producto and not color_titulo and len(color_producto) > 1:
        puntaje = min(puntaje, 0.65)

    producto_pack = bool(consulta & {"PACK", "JUEGO", "KIT", "SET", "COMBO"})
    titulo_pack = bool(titulo & {"PACK", "JUEGO", "KIT", "SET", "COMBO"})
    colores_pack = bool(color_producto and len(color_producto) > 1 and color_producto <= color_titulo)
    if producto_pack and not titulo_pack and not colores_pack:
        puntaje = min(puntaje, 0.65)
    if candidato.get("verificada"):
        return 0.98
    return puntaje


def _curar_fotos(st, biblioteca, path, productos, abrir_producto):
    estado = _json_leer(path)
    fuentes = estado.setdefault("fuentes_foto", {})
    historial = estado.setdefault("busquedas_foto", {})
    faltantes = [p for p in productos if not p.get("foto")]
    total = len(faltantes)
    st.subheader("Completar fotos faltantes", anchor=False)
    st.caption("Buscaré por modelo y descripción. Solo asignaré una imagen si coincide el código o el nombre con suficiente claridad; las dudas quedarán para revisión.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Sin foto", total, border=True)
    asignadas = sum(v.get("estado") == "asignada" for v in historial.values())
    c2.metric("Fotos incorporadas", asignadas, border=True)
    c3.metric("Candidatas por revisar", sum(v.get("estado") == "revisar" for v in historial.values()), border=True)
    if total:
        st.info(f"Los productos sin fotografía exacta aparecerán en el PDF con una ilustración de categoría marcada como referencial. "
                f"La sección de revisión permite abrir la ficha y reemplazar esa ilustración por una foto confirmada.")
    pendientes = [p for p in faltantes if historial.get(p["clave"], {}).get("estado")
                  not in {"asignada", "revisar", "sin_resultado", "error"}]
    lote = pendientes[:12]
    if st.button(f"Buscar próximas {len(lote)} fotos seguras",
                 type="primary", disabled=not lote, key="catalogo_fotos_lote"):
        progreso = st.progress(0, text="Preparando búsqueda de imágenes…")
        detalle = st.empty()
        total_lote = len(lote)
        for i, producto in enumerate(lote, 1):
            clave = producto["clave"]
            query = f"{clave} {_nombre_visible(producto)} {producto.get('rubro', '')} foto producto"
            try:
                candidatos = bs.buscar_fotos(query, max_resultados=4, clave=clave, diag={})
                for candidato in candidatos:
                    candidato["verificada"] = bool(
                        bs.es_codigo(clave) and bs.menciones_clave(candidato.get("titulo", ""), [clave]))
                evaluados = sorted(((_confianza_foto(producto, c), c) for c in candidatos),
                                   key=lambda x: x[0], reverse=True)
                puntaje, mejor = evaluados[0] if evaluados else (0.0, None)
                if mejor and puntaje >= .90:
                    biblioteca.guardar_archivo(clave, "foto", "foto" + mejor["ext"], mejor["datos"])
                    fuentes[clave] = {"url": mejor["url"], "titulo": mejor.get("titulo", ""),
                                      "fuente": mejor.get("fuente", ""), "confianza": round(puntaje, 3),
                                      "fecha": datetime.now().astimezone().isoformat(timespec="seconds")}
                    historial[clave] = {"estado": "asignada", "puntaje": round(puntaje, 3)}
                elif candidatos:
                    historial[clave] = {"estado": "revisar", "puntaje": round(puntaje, 3),
                                        "candidatos": [{"titulo": c.get("titulo", ""),
                                                        "url": c.get("url", ""),
                                                        "fuente": c.get("fuente", "")}
                                                       for score, c in evaluados[:3]]}
                else:
                    historial[clave] = {"estado": "sin_resultado"}
            except Exception as error:
                historial[clave] = {"estado": "error", "detalle": str(error)[:160]}
            progreso.progress(i / max(total_lote, 1), text=f"Buscando fotos · {i}/{total_lote}")
            _json_guardar(path, estado)
            detalle.caption(f"Lote {i}/{total_lote} · asignadas: {sum(v.get('estado') == 'asignada' for v in historial.values())} · requieren revisión: {sum(v.get('estado') == 'revisar' for v in historial.values())} · sin resultado: {sum(v.get('estado') == 'sin_resultado' for v in historial.values())}")
        st.success(f"Lote terminado. Quedan {max(0, len(pendientes) - total_lote)} productos pendientes de búsqueda.")
        st.rerun()
    revisiones = [(p, historial.get(p["clave"], {})) for p in faltantes
                  if historial.get(p["clave"], {}).get("estado") == "revisar"]
    if revisiones:
        st.markdown("**Fotos candidatas para revisión manual**")
        st.caption("Revisa modelo, color, gramaje y presentación antes de incorporar la foto. Las variantes parecidas pueden verse casi iguales.")
        por_pagina = 12
        paginas = (len(revisiones) + por_pagina - 1) // por_pagina
        pagina = st.number_input("Página de candidatos", min_value=1, max_value=paginas,
                                 value=1, step=1, key=f"catalogo_fotos_revision_pagina_{paginas}") if paginas > 1 else 1
        inicio = (pagina - 1) * por_pagina
        for producto, ficha in revisiones[inicio:inicio + por_pagina]:
            with st.container(border=True):
                st.markdown(f"**{_nombre_visible(producto)}** · {producto['clave']}")
                if ficha.get("motivo"):
                    st.caption(ficha["motivo"])
                for candidato in ficha.get("candidatos", [])[:2]:
                    if candidato.get("url"):
                        st.markdown(f"• [{_breve(candidato.get('titulo') or candidato['url'], 100)}]({candidato['url']})")
                if abrir_producto and st.button("Revisar foto en la ficha", key=f"catalogo_foto_revisar_{producto['clave']}"):
                    abrir_producto(producto["clave"])


def _fotos_ui(st, biblioteca, path, clasificar, abrir_producto):
    _curar_fotos(st, biblioteca, path, _productos(biblioteca, clasificar), abrir_producto)


def _validar_foto_subida(archivo):
    if archivo is None:
        return None
    contenido = archivo.getvalue()
    if not contenido or len(contenido) > 10 * 1024 * 1024:
        raise ValueError("La imagen está vacía o supera el límite de 10 MB.")
    try:
        with PILImage.open(io.BytesIO(contenido)) as image:
            formato = image.format
            image.verify()
    except (OSError, ValueError) as exc:
        raise ValueError("El archivo no es una imagen válida.") from exc
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(formato)
    if not ext:
        raise ValueError("Usa una imagen JPG, PNG o WEBP.")
    return ext, contenido


def _registrar_foto_editor(path, clave, *, filename):
    registro = _json_leer(path)
    fecha = datetime.now().astimezone().isoformat(timespec="seconds")
    registro.setdefault("fuentes_foto", {})[clave] = {
        "fuente": "Carga manual desde el editor de catálogos",
        "titulo": filename,
        "fecha": fecha,
        "seleccion": "actualizada por el usuario",
    }
    registro.setdefault("busquedas_foto", {})[clave] = {
        "estado": "asignada",
        "seleccion": "foto actualizada desde el editor de catálogos",
        "fecha": fecha,
    }
    _json_guardar(path, registro)


def _tarjeta_producto_editor(st, biblioteca, path, producto, *, scope):
    sesion = st.session_state
    clave = producto["clave"]
    ident = sha256(f"{scope}:{clave}".encode()).hexdigest()[:14]
    concepto_key = f"catalogo_concepto_{ident}"
    precio_key = f"catalogo_precio_{ident}"
    foto_key = f"catalogo_foto_{ident}"
    guardado_key = f"catalogo_guardar_producto_{ident}"
    inicial_neto = producto.get("precio_neto_catalogo")
    sesion.setdefault(concepto_key, _nombre_visible(producto))
    sesion.setdefault(precio_key, int(inicial_neto) if inicial_neto is not None else None)

    concepto_vista = str(sesion.get(concepto_key) or "").strip() or "Concepto sin nombre"
    precio_vista = sesion.get(precio_key)
    subida = sesion.get(foto_key)
    foto_actual = biblioteca.archivos(clave)[0]

    with st.container(border=True):
        vista, edicion = st.columns([0.95, 1.05], gap="medium", vertical_alignment="top")
        with vista:
            if subida is not None:
                st.image(subida.getvalue(), width="stretch", alt=f"Vista previa de {concepto_vista}")
            elif foto_actual:
                st.image(str(foto_actual), width="stretch", alt=f"Foto de {concepto_vista}")
            else:
                st.info("Este producto todavía no tiene foto.")
            st.caption(producto.get("rubro") or "Productos")
            st.markdown(f"**{escape(concepto_vista)}**")
            st.caption(f"Código · {clave}")
            if precio_vista is None:
                st.badge("Precio pendiente", color="orange", icon=":material/price_change:")
            else:
                publico = precio_publico(precio_vista)
                st.markdown(f"**Precio neto · {clp(precio_vista)}**  \n+ IVA · {clp(publico) if publico is not None else '—'}")

        with edicion:
            st.text_input("Concepto", max_chars=180, key=concepto_key,
                          help="Este nombre se guarda en el producto maestro.")
            st.number_input("Precio neto (CLP)", min_value=0, step=500, format="%d",
                            key=precio_key, help="El precio guardado alimentará cotizaciones futuras.")
            st.file_uploader("Cambiar foto", type=["jpg", "jpeg", "png", "webp"],
                             max_upload_size=10, key=foto_key,
                             help="La imagen nueva reemplazará la foto principal del producto.")
            if st.button("Guardar en ficha maestra", key=guardado_key,
                         icon=":material/save:", width="stretch"):
                nuevo_concepto = str(sesion.get(concepto_key) or "").strip()
                nuevo_precio = sesion.get(precio_key)
                try:
                    if not nuevo_concepto:
                        raise ValueError("El concepto no puede quedar vacío.")
                    nueva_foto = _validar_foto_subida(sesion.get(foto_key))
                    if nueva_foto:
                        ext, contenido = nueva_foto
                        biblioteca.guardar_archivo(clave, "foto", f"foto{ext}", contenido)
                        _registrar_foto_editor(path, clave, filename=sesion[foto_key].name)
                    biblioteca.actualizar(clave, nombre=nuevo_concepto, precio_neto=nuevo_precio)
                except (OSError, ValueError) as exc:
                    st.error(f"No se guardó la ficha. {exc}")
                else:
                    sesion["_catalogo_producto_guardado"] = True
                    limpiar = sesion.setdefault("_catalogo_reset_editors", [])
                    limpiar.append([concepto_key, precio_key, foto_key])
                    st.rerun()


def _revision(st, biblioteca, path, productos, *, scope):
    st.subheader("Vista previa y edición", anchor=False, icon=":material/view_quilt:")
    precios_pendientes = sum(p["precio_neto_catalogo"] is None for p in productos)
    fotos_pendientes = sum(not p["foto"] for p in productos)
    a, b, c = st.columns(3)
    a.metric("Productos incluidos", len(productos), border=True)
    b.metric("Sin precio", precios_pendientes, border=True)
    c.metric("Sin foto", fotos_pendientes, border=True)
    if precios_pendientes:
        st.warning("Completa el precio en cada ficha para habilitar la generación del PDF.")
    if not productos:
        return

    for keys in st.session_state.pop("_catalogo_reset_editors", []):
        for key in keys:
            st.session_state.pop(key, None)
    if st.session_state.pop("_catalogo_producto_guardado", False):
        st.success("Ficha maestra actualizada. El nuevo precio ya se usará en futuras cotizaciones.",
                   icon=":material/check_circle:")

    st.caption("Edita el concepto, el precio neto y la foto junto a la vista previa. Al guardar, el nombre, "
               "el precio de futuras cotizaciones y la imagen actualizan la ficha maestra.")
    a, b, c = st.columns([2, 1, 1])
    busqueda = a.text_input("Buscar en los productos", key=f"catalogo_buscar_{scope}",
                            placeholder="Nombre, código o categoría").strip().casefold()
    categorias = ["Todas las categorías", *sorted({p["rubro"] for p in productos if p.get("rubro")})]
    categoria = b.selectbox("Categoría", categorias, key=f"catalogo_categoria_{scope}")
    filtro = c.selectbox("Estado", ["Todos", "Sin precio", "Con foto", "Sin foto"],
                         key=f"catalogo_filtro_{scope}")
    visibles = [p for p in productos
                if busqueda in f"{p['clave']} {_nombre_visible(p)} {p['rubro']}".casefold()
                and (categoria == "Todas las categorías" or p["rubro"] == categoria)
                and (filtro == "Todos" or filtro == "Sin precio" and p["precio_neto_catalogo"] is None
                     or filtro == "Con foto" and bool(p["foto"])
                     or filtro == "Sin foto" and not p["foto"])]
    if not visibles:
        st.caption("No hay productos que coincidan con esos filtros.")
        return
    por_pagina = 8
    paginas = (len(visibles) + por_pagina - 1) // por_pagina
    pagina = st.number_input("Página", 1, paginas, step=1,
                             key=f"catalogo_pag_{scope}_{paginas}") if paginas > 1 else 1
    inicio = (pagina - 1) * por_pagina
    st.caption(f"Mostrando {inicio + 1}–{min(inicio + por_pagina, len(visibles))} de {len(visibles)} productos")
    for producto in visibles[inicio:inicio + por_pagina]:
        _tarjeta_producto_editor(st, biblioteca, path, producto, scope=scope)


def _editar(st, biblioteca, path, registro, clasificar):
    productos = _productos(biblioteca, clasificar)
    sesion = st.session_state
    prefijo = "catalogo_campo_"
    if not productos:
        st.info("Agrega productos a la biblioteca para preparar un catálogo.")
        return
    if limpiar := sesion.pop("_catalogo_limpiar", None):
        for llave in tuple(sesion):
            if llave.startswith(prefijo) and llave.endswith(limpiar):
                sesion.pop(llave, None)
    selecciones = registro.get("selecciones", {})
    selector = "catalogo_cliente_seleccion"
    destino = sesion.pop("_catalogo_reabrir", None)
    if destino in selecciones:
        sesion[selector] = destino
    seleccion = st.selectbox("Borrador de catálogo", [None, *sorted(selecciones)],
                            format_func=lambda n: n if n else "Nuevo catálogo",
                            key=selector, persist_state="session")
    scope = sha256(repr(seleccion).encode()).hexdigest()[:16]
    clave_base = f"_catalogo_version_{scope}"
    version = sesion.setdefault(clave_base, selecciones.get(seleccion))
    borrador = _borrador(version)
    if version != selecciones.get(seleccion):
        st.warning("Este borrador cambió en otra sesión.")
        if st.button("Recargar borrador", key=f"catalogo_recargar_{scope}"):
            for llave in tuple(sesion):
                if llave.startswith(prefijo) and llave.endswith(scope):
                    sesion.pop(llave, None)
            sesion.pop(clave_base, None)
            st.rerun()
        return
    with st.container(border=True):
        st.subheader("Selección y portada", anchor=False)
        c1, c2 = st.columns(2)
        nombre = c1.text_input("Nombre del catálogo", value=seleccion or "", max_chars=100,
                               key=f"{prefijo}nombre_{scope}", persist_state="session").strip()
        vigencia = c2.text_input("Vigencia general de los precios", value=borrador.get("vigencia", ""),
                                placeholder="Ej.: Hasta el 30 de noviembre", max_chars=100,
                                key=f"{prefijo}vigencia_{scope}", persist_state="session").strip()
        incluir_todos = st.checkbox("Incluir toda la biblioteca", value=bool(borrador.get("todos")),
                                    key=f"{prefijo}todos_{scope}", persist_state="session")
        por_clave = {p["clave"]: p for p in productos}
        claves_guardadas = list(dict.fromkeys(
            actual for k in borrador.get("claves", []) if (actual := biblioteca.clave_vigente(k)) in por_clave
        ))
        seleccionados = st.multiselect(
            "Productos incluidos", por_clave, default=claves_guardadas,
            format_func=lambda k: f"{_nombre_visible(por_clave[k])} · {k}",
            key=f"{prefijo}productos_{scope}", disabled=incluir_todos, persist_state="session",
        )
        contacto = st.text_area("Contacto en la portada", value=borrador.get("contacto", CONTACTO),
                                max_chars=500, key=f"{prefijo}contacto_{scope}", persist_state="session")

    seleccionados = [k for k in seleccionados if k in por_clave]
    incluidos = set(por_clave if incluir_todos else seleccionados)
    elegidos = [p for p in productos if p["clave"] in incluidos]
    _revision(st, biblioteca, path, elegidos, scope=scope)
    st.caption("El PDF muestra precios netos más IVA. Cada ficha se puede editar directamente en la vista previa.")
    izquierda, derecha = st.columns(2)
    if izquierda.button("Guardar borrador", icon=":material/save:", disabled=not nombre or not incluidos,
                        key="catalogo_cliente_guardar"):
        try:
            guardar_borrador(path, nombre, {"claves": seleccionados, "todos": incluir_todos,
                                            "vigencia": vigencia, "contacto": contacto},
                              anterior=seleccion, version=version)
        except (OSError, ValueError) as exc:
            st.error(f"No se pudo guardar el borrador. {exc}")
        else:
            sesion.pop(clave_base, None)
            sesion["_catalogo_reabrir"] = nombre
            sesion["_catalogo_limpiar"] = scope
            sesion["_catalogo_guardado"] = True
            st.rerun()
    if sesion.pop("_catalogo_guardado", False):
        st.success("Borrador guardado.")
    if derecha.button("Generar y guardar PDF", icon=":material/picture_as_pdf:", type="primary",
                       disabled=not nombre or not elegidos or any(p["precio_neto_catalogo"] is None for p in elegidos),
                       key="catalogo_cliente_generar"):
        with st.spinner("Preparando el catálogo…"):
            try:
                archivo = emitir(path, [{**p, "foto_path": biblioteca.archivos(p["clave"])[0]} for p in elegidos],
                                 nombre, vigencia, contacto)
            except (OSError, ValueError) as exc:
                st.error(f"No se pudo generar el PDF. {exc}")
            else:
                sesion["_catalogo_ultimo_pdf"] = (nombre, archivo)
                st.success("Catálogo generado y guardado en el historial de versiones.")
    ultimo_nombre, ultimo_archivo = sesion.get("_catalogo_ultimo_pdf", ("", None))
    if ultimo_nombre == nombre:
        _descarga(st, path, ultimo_archivo, key="catalogo_cliente_descargar", etiqueta="Descargar último PDF emitido")


def _homologar(st, biblioteca, path, meta):
    if not biblioteca.indice:
        st.info("Agrega productos para definir sus familias.")
        return
    filas = [{"Código": clave, "Producto": ficha.get("nombre") or clave,
              "Familia": _categoria({**ficha, "clave": clave}, meta),
              "Variante / medida": meta.get(clave, {}).get("variante", "")}
             for clave, ficha in sorted(biblioteca.indice.items())]
    editadas = st.data_editor(
        pd.DataFrame(filas), hide_index=True, width="stretch",
        disabled=["Código", "Producto"], key="catalogo_homologacion_editor",
        column_config={"Familia": st.column_config.SelectboxColumn(options=FAMILIAS, required=True)},
    )
    if st.button("Guardar familias y variantes", key="catalogo_homologacion_guardar"):
        with _LOCK:
            registro = _json_leer(path)
            vigente = registro.setdefault("producto_meta", {})
            for anterior, nueva in zip(filas, editadas.to_dict("records")):
                if anterior != nueva:
                    vigente.setdefault(nueva["Código"], {}).update(
                        categoria=nueva["Familia"], variante=str(nueva["Variante / medida"] or "").strip()
                    )
            _json_guardar(path, registro)
        st.success("Familias y variantes guardadas.")


def render_ui(st, biblioteca, *, abrir_producto=None):
    """Planificación de catálogo, curaduría de fotos y preparación SEO/GEO."""
    st.title("Catálogos para clientes", icon=":material/menu_book:")
    path = biblioteca.dir / "catalogos_clientes.json"
    try:
        registro = _json_leer(path)
    except (OSError, ValueError):
        st.error("No pude leer el registro de catálogos.")
        return
    meta = registro.get("producto_meta", {})

    def clasificar(clave, ficha):
        return {"rubro": _categoria({**ficha, "clave": clave}, meta)}

    editor, fotos, seo, familias, archivo = st.tabs(
        ["Editor visual", "Fotos faltantes", "SEO y GEO", "Familias y variantes", "Versiones emitidas"],
        key="catalogo_vista", on_change="rerun",
    )
    if editor.open:
        with editor:
            _editar(st, biblioteca, path, registro, clasificar)
    if fotos.open:
        with fotos:
            _fotos_ui(st, biblioteca, path, clasificar, abrir_producto)
    if seo.open:
        with seo:
            _seo_geo_ui(st, biblioteca, clasificar, biblioteca.dir)
    if familias.open:
        with familias:
            _homologar(st, biblioteca, path, meta)
    if archivo.open:
        with archivo:
            _archivo(st, path, registro)
