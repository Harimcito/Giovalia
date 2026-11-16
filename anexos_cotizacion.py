"""Anexos inmutables de una cotización, independientes de la ficha maestra."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
import re

from PIL import Image
from pypdf import PdfReader

_FORMATOS = {"foto": r"jpg|png|webp|jpeg", "ficha": "pdf"}
_CARPETA = "anexos_cotizaciones"


def limpiar_referencias(referencias):
    """Conserva solo claves y referencias a archivos de producto."""
    salida = {}
    if not isinstance(referencias, dict):
        return salida
    for clave, seleccion in referencias.items():
        if not isinstance(clave, str) or not isinstance(seleccion, dict):
            continue
        limpia = {
            tipo: valor for tipo, valor in seleccion.items()
            if tipo in _FORMATOS and (valor is None or isinstance(valor, str)
               and re.fullmatch(r"[0-9a-f]{64}\.(?:" + _FORMATOS[tipo] + ")", valor))
        }
        if isinstance(seleccion.get("ficha_opcional"), bool):
            limpia["ficha_opcional"] = seleccion["ficha_opcional"]
        if limpia:
            salida[clave] = limpia
    return salida


def guardar(biblioteca, tipo, contenido, *, original=None):
    """Devuelve una referencia por contenido sin modificar archivos de catálogo."""
    if original is not None:
        extension = original.suffix.lower()
        if not re.fullmatch(_FORMATOS[tipo], extension.lstrip(".")):
            raise ValueError("Formato de anexo no admitido.")
    elif tipo == "foto":
        with Image.open(BytesIO(contenido)) as imagen:
            extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(imagen.format)
            if not extension:
                raise ValueError("Usa una imagen JPG, PNG o WebP.")
            imagen.verify()
    elif tipo == "ficha":
        lector = PdfReader(BytesIO(contenido))
        if lector.is_encrypted or not lector.pages:
            raise ValueError("Sube un PDF sin contraseña y con al menos una página.")
        extension = ".pdf"
    else:
        raise ValueError("Tipo de anexo inválido.")

    carpeta = biblioteca.dir / _CARPETA
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = sha256(contenido).hexdigest() + extension
    destino = carpeta / nombre
    if not destino.is_file():
        with NamedTemporaryFile(dir=carpeta, suffix=".tmp", delete=False) as temporal:
            ruta = Path(temporal.name)
            temporal.write(contenido)
        try:
            ruta.replace(destino)
        finally:
            ruta.unlink(missing_ok=True)
    return nombre


def resolver(biblioteca, clave, seleccion):
    catalogo = biblioteca.archivos(clave)
    salida = []
    for tipo, referencia in zip(("foto", "ficha"), catalogo):
        if tipo not in seleccion:
            salida.append(referencia)
        else:
            nombre = seleccion[tipo]
            ruta = biblioteca.dir / _CARPETA / nombre if nombre else None
            salida.append(ruta if ruta and ruta.is_file() else None)
    opcional = seleccion.get("ficha_opcional", bool(biblioteca.indice.get(clave, {}).get("ficha_no_requerida")))
    return *salida, opcional


def congelar(biblioteca, claves, anexos):
    """Materializa las referencias incluso cuando provienen del catálogo."""
    congelados = limpiar_referencias(anexos)
    for clave in claves:
        seleccion = congelados.setdefault(clave, {})
        foto, ficha, opcional = resolver(biblioteca, clave, seleccion)
        for tipo, ruta in (("foto", foto), ("ficha", ficha)):
            if tipo not in seleccion:
                seleccion[tipo] = guardar(biblioteca, tipo, ruta.read_bytes(), original=ruta) if ruta else None
        seleccion.setdefault("ficha_opcional", opcional)
    return congelados
