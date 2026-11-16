"""Detecta fichas que necesitan revisión sin alterar los archivos de la biblioteca."""
import re
import unicodedata
from functools import lru_cache

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from buscador import es_codigo

FALTANTE = "Ficha faltante"
ILEGIBLE = "PDF ilegible"
SIN_TEXTO = "PDF sin texto verificable"
SIN_CODIGO = "Código no encontrado"


@lru_cache(maxsize=1024)
def _texto_ficha(ruta, tamaño, modificado_ns):
    """Invalida el resultado cuando se reemplaza la ficha."""
    try:
        with ruta.open("rb") as archivo:
            lector = PdfReader(archivo)
            if (lector.is_encrypted and not lector.decrypt("")) or not len(lector.pages):
                return None
            return "\n".join(pagina.extract_text() or "" for pagina in lector.pages)
    except (OSError, PyPdfError, ValueError, KeyError, TypeError, IndexError, NotImplementedError):
        return None


def problema_ficha(ruta, codigos, *, opcional=False):
    if ruta is None:
        return "" if opcional else FALTANTE
    try:
        estado = ruta.stat()
    except OSError:
        return ILEGIBLE
    texto = _texto_ficha(ruta, estado.st_size, estado.st_mtime_ns)
    if texto is None:
        return ILEGIBLE
    comprobables = [codigo for codigo in codigos
                    if es_codigo(codigo) or (codigo.isascii() and codigo.isdigit() and len(codigo) >= 3)]
    if not comprobables:
        return ""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().upper()
    if not re.search("[A-Z0-9]", texto):
        return SIN_TEXTO
    for codigo in comprobables:
        tramos = re.findall("[A-Z0-9]+", codigo.upper())
        # Acepta TN1060, TN-1060 y TN 1060, pero no un código más largo que lo contiene.
        patron = r"[\s._/-]*".join("".join(tramos))
        if re.search(rf"(?<![A-Z0-9]){patron}(?![A-Z0-9])", texto):
            return ""
    return SIN_CODIGO
