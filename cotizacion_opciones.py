"""Compatibilidad, faltantes y opciones de una cotización."""
from collections import Counter
from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
import re
import unicodedata

from pypdf import PdfReader, PdfWriter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from flujo_cotizacion import EN_STOCK, A_PEDIDO, plazo_efectivo, texto_plazo
from stock_catalogo import datos_stock
from ui_montos import clp

TIPOS = ("Sin clasificar", "Original", "Alternativo")
GRUPO = "grupo_alternativa"
MARCAS = {"brother", "canon", "epson", "hp", "lexmark", "ricoh", "samsung", "xerox", "kyocera"}


def texto_modelo(valor):
    return unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode().casefold()


def buscar_compatibles(consulta, indice):
    palabras = re.findall(r"[a-z0-9]+", texto_modelo(consulta))
    if palabras and palabras[0] in MARCAS:
        palabras = palabras[1:]
    modelo = "".join(palabras)
    if len(modelo) < 3 or not any(c.isdigit() for c in modelo):
        return []
    patron = re.compile(r"(?<![a-z0-9])" + r"[\s_-]*".join(map(re.escape, modelo)) + r"(?![a-z0-9])")
    resultados = []
    for codigo, ficha in indice.items():
        if not patron.search(texto_modelo(ficha.get("compatibilidad") or "")):
            continue
        tipo = ficha.get("tipo_insumo")
        tipo = tipo if tipo in TIPOS else TIPOS[0]
        resultados.append((codigo, tipo, ficha))
    return sorted(resultados, key=lambda fila: (TIPOS.index(fila[1]) or len(TIPOS), fila[0]))


def cantidad_valida(valor):
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return numero if numero.is_finite() and numero >= 0 else None


def faltantes(lineas, biblioteca):
    demanda = Counter()
    nombres = {}
    for linea in lineas:
        cantidad = cantidad_valida(linea.get("cantidad"))
        if not (cantidad and linea.get("es_producto", True) and linea.get("incluir", True)):
            continue
        codigo = biblioteca.clave_vigente(linea.get("clave_auto") or "")
        llave = (codigo, "" if codigo else linea.get("descripcion", ""))
        demanda[llave] += cantidad
        nombres.setdefault(llave, linea.get("descripcion"))
    salida = []
    for llave, pedida in demanda.items():
        codigo, _ = llave
        ficha = biblioteca.indice.get(codigo, {})
        stock = datos_stock(ficha)
        disponible = None
        if stock["stock_confirmado_el"]:
            if stock["estado_stock"] == EN_STOCK:
                disponible = cantidad_valida(ficha.get("stock_cantidad"))
            elif stock["estado_stock"] == A_PEDIDO:
                disponible = Decimal(0)
        cantidad_faltante = None if disponible is None else max(pedida - disponible, 0)
        if cantidad_faltante == 0:
            continue
        salida.append({
            "Producto": ficha.get("nombre") or nombres[llave],
            "Código": codigo or "Sin vínculo",
            "Pedido": float(pedida),
            "Disponible confirmado": None if disponible is None else float(disponible),
            "Faltante": None if cantidad_faltante is None else float(cantidad_faltante),
            "Proveedor o sucursal": stock["stock_fuente"] or "Por confirmar",
            "Reposición (días)": ficha.get("reposicion_dias"),
            "Confirmado el": stock["stock_confirmado_el"],
        })
    return salida


def grupos(lineas):
    salida = {}
    for linea in lineas:
        if linea.get(GRUPO) and linea.get("es_producto", True):
            salida.setdefault(linea[GRUPO], []).append(linea)
    return {grupo: opciones for grupo, opciones in salida.items() if len(opciones) > 1}


def normalizar_alternativas(lineas):
    asociaciones = grupos(lineas)
    activas = set()
    salida = []
    for linea in lineas:
        grupo = linea.get(GRUPO)
        if grupo and (grupo not in asociaciones or not linea.get("es_producto", True)):
            linea = {k: v for k, v in linea.items() if k != GRUPO}
        elif grupo and linea.get("incluir", True):
            if grupo in activas:
                linea = {**linea, "incluir": False}
            activas.add(grupo)
        salida.append(linea)
    return salida


def anexar_opciones(pdf, lineas):
    grupos_activos = [opciones for opciones in grupos(lineas).values() if any(o.get("incluir", True) for o in opciones)]
    if not grupos_activos:
        return pdf, 0
    estilos = getSampleStyleSheet()
    contenido = [Paragraph("Opciones de productos", estilos["Title"]),
                 Paragraph("El total de la cotización incluye únicamente las opciones seleccionadas.",
                           estilos["BodyText"])]
    for numero, opciones in enumerate(grupos_activos, 1):
        contenido.append(Paragraph(f"Grupo {numero}", estilos["Heading2"]))
        for opcion in opciones:
            unitario = opcion.get("unitario")
            unitario = cantidad_valida(opcion.get("precio_neto") if unitario is None else unitario)
            cantidad = cantidad_valida(opcion.get("cantidad")) or Decimal(0)
            estado = "Incluida" if opcion.get("incluir", True) else "Alternativa"
            importe = (f"{clp(unitario)} neto por unidad · subtotal neto {clp(unitario * cantidad)}"
                       if unitario is not None else "Precio por confirmar")
            descripcion = escape(str(opcion.get("descripcion", "")))
            plazo = texto_plazo(plazo_efectivo(opcion))
            contenido.append(Paragraph(
                f"<b>{estado}</b> · {descripcion} · Cantidad: {cantidad:g} · {importe} · {plazo}",
                estilos["BodyText"],
            ))
            contenido.append(Spacer(1, 6))
    anexo = BytesIO()
    SimpleDocTemplate(anexo).build(contenido)
    anexo.seek(0)
    paginas = len(PdfReader(anexo).pages)
    salida = BytesIO()
    escritor = PdfWriter()
    escritor.append(BytesIO(pdf))
    escritor.append(anexo, outline_item="Opciones de productos")
    escritor.write(salida)
    return salida.getvalue(), paginas
