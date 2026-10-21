"""Búsqueda en internet de fotos y fichas técnicas (DuckDuckGo, sin API key).

Cada candidato se DESCARGA y VALIDA antes de mostrarse (imagen real y de buen
tamaño, PDF real y legible), así los botones "Usar" siempre funcionan.
"""
import io
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from PIL import Image as PILImage
from pypdf import PdfReader

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_IMG = 10 * 1024 * 1024
MAX_PDF = 25 * 1024 * 1024
MIN_LADO = 250  # px; descarta miniaturas

# marca -> trozos de dominio oficial (se priorizan en los resultados)
MARCAS = {
    "HP": ("hp.com",), "KYOCERA": ("kyocera",), "EPSON": ("epson",), "BROTHER": ("brother",),
    "CANON": ("canon",), "SAMSUNG": ("samsung",), "LEXMARK": ("lexmark",), "RICOH": ("ricoh",),
    "XEROX": ("xerox",), "LOGITECH": ("logitech",), "KINGSTON": ("kingston",),
    "LENOVO": ("lenovo",), "DELL": ("dell",), "ASUS": ("asus",), "ACER": ("acer",),
    "FABER": ("faber-castell",), "STABILO": ("stabilo",), "BIC": ("bic.com",),
}


class ErrorBusqueda(Exception):
    """La búsqueda falló (sin internet, límite del buscador, etc.)."""


def marca_en(texto: str) -> str:
    palabras = {p.strip(".,;:()").upper() for p in texto.split()}
    return next((m for m in MARCAS if m in palabras), "")


def _bonus(url: str, marca: str) -> int:
    host = urlparse(url).netloc.lower()
    return 3 if marca and any(d in host for d in MARCAS.get(marca, ())) else 0


def _buscador():
    try:
        from ddgs import DDGS
    except ImportError:  # nombre antiguo del paquete
        from duckduckgo_search import DDGS
    return DDGS()


# ---------------------------------------------------------------- descargas
def descargar_imagen(url: str):
    """(bytes, extensión, ancho, alto). Lanza ValueError si no sirve."""
    origen = "{0.scheme}://{0.netloc}/".format(urlparse(url))
    r = requests.get(url, headers={"User-Agent": UA, "Referer": origen}, timeout=15, stream=True)
    r.raise_for_status()
    datos = r.raw.read(MAX_IMG + 1, decode_content=True)
    if len(datos) > MAX_IMG:
        raise ValueError("imagen demasiado pesada")
    with PILImage.open(io.BytesIO(datos)) as im:
        formato, (ancho, alto) = im.format, im.size
        im.verify()
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(formato)
    if not ext:
        raise ValueError(f"formato no soportado: {formato}")
    if min(ancho, alto) < MIN_LADO:
        raise ValueError("imagen muy pequeña")
    return datos, ext, ancho, alto


def descargar_pdf(url: str):
    """(bytes, nº de páginas). Lanza ValueError si no es un PDF válido."""
    origen = "{0.scheme}://{0.netloc}/".format(urlparse(url))
    r = requests.get(url, headers={"User-Agent": UA, "Referer": origen}, timeout=20, stream=True)
    r.raise_for_status()
    datos = r.raw.read(MAX_PDF + 1, decode_content=True)
    if len(datos) > MAX_PDF:
        raise ValueError("PDF demasiado pesado")
    if not datos.lstrip()[:5] == b"%PDF-":
        raise ValueError("el enlace no es un PDF")
    lector = PdfReader(io.BytesIO(datos))
    if lector.is_encrypted and not lector.decrypt(""):
        raise ValueError("PDF protegido con clave")
    if len(lector.pages) < 1:
        raise ValueError("PDF vacío")
    return datos, len(lector.pages)


def miniatura_pdf(datos: bytes) -> bytes | None:
    """PNG de la primera página (para que reconozcas la ficha). None si falla."""
    try:
        import pypdfium2 as pdfium
        pagina = pdfium.PdfDocument(datos)[0]
        buf = io.BytesIO()
        pagina.render(scale=0.45).to_pil().save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------- búsqueda
def buscar_fotos(
    consulta: str,
    max_resultados: int = 6,
    *,
    clave: str | None = None,
    diag: dict | None = None,
) -> list[dict]:
    """Candidatos de foto descargados y validados.

    clave y diag mantienen compatibilidad con la interfaz de app.py.
    """
    if diag is None:
        diag = {}
    marca = marca_en(consulta)
    try:
        crudos = _buscador().images(consulta, max_results=40)
    except Exception as e:
        raise ErrorBusqueda(f"No pude buscar imágenes: {e}") from e

    diag["crudos"] = len(crudos)
    candidatos, vistos = [], set()
    for r in crudos:
        url = r.get("image") or r.get("url")
        if not url or url in vistos or url.lower().split("?")[0].endswith((".svg", ".gif")):
            continue
        vistos.add(url)
        ancho, alto = int(r.get("width") or 0), int(r.get("height") or 0)
        if ancho and alto and min(ancho, alto) < MIN_LADO:
            continue
        # mejor puntaje: dominio de la marca y formato casi cuadrado
        forma = min(ancho, alto) / max(ancho, alto) if ancho and alto else 0.5
        candidatos.append((_bonus(url, marca) + forma, r, url))
    candidatos.sort(key=lambda t: -t[0])

    def bajar(item):
        _, r, url = item
        try:
            datos, ext, ancho, alto = descargar_imagen(url)
        except Exception:
            return None
        fuente = r.get("source") or urlparse(url).netloc
        clave_txt = str(clave or "").strip()
        texto_candidato = "{} {}".format(r.get("title", ""), url)
        verificada = bool(clave_txt and es_codigo(clave_txt) and menciones_clave(texto_candidato, [clave_txt]))
        return {"url": url, "datos": datos, "ext": ext, "ancho": ancho, "alto": alto,
                "fuente": fuente, "titulo": r.get("title", ""),
                "verificada": verificada, "baja_resolucion": False}

    with ThreadPoolExecutor(max_workers=6) as pool:
        validos = [c for c in pool.map(bajar, candidatos[:max_resultados * 3]) if c]
    return validos[:max_resultados]


def buscar_fichas(
    consulta: str,
    max_resultados: int = 4,
    *,
    clave: str | None = None,
    diag: dict | None = None,
) -> list[dict]:
    """Candidatos de ficha técnica (PDF) descargados y validados.

    clave y diag mantienen compatibilidad con la interfaz de app.py.
    """
    if diag is None:
        diag = {}
    marca = marca_en(consulta)
    crudos = []
    try:
        for sufijo in ("ficha técnica datasheet filetype:pdf", "specifications pdf"):
            crudos += _buscador().text(f"{consulta} {sufijo}", max_results=15)
    except Exception as e:
        if not crudos:
            raise ErrorBusqueda(f"No pude buscar fichas técnicas: {e}") from e

    diag["crudos"] = len(crudos)
    candidatos, vistos = [], set()
    for r in crudos:
        url = r.get("href") or r.get("url")
        if not url or url in vistos or ".pdf" not in url.lower():
            continue
        vistos.add(url)
        candidatos.append((_bonus(url, marca), r, url))
    candidatos.sort(key=lambda t: -t[0])

    def bajar(item):
        _, r, url = item
        try:
            datos, paginas = descargar_pdf(url)
        except Exception:
            return None
        titulo = r.get("title", "") or url
        clave_txt = str(clave or "").strip()
        menciona = bool(clave_txt and es_codigo(clave_txt) and menciones_clave("{} {}".format(titulo, url), [clave_txt]))
        nivel = 3 if menciona and paginas <= 8 else 2 if menciona else 1
        return {"url": url, "datos": datos, "paginas": paginas,
                "miniatura": miniatura_pdf(datos), "titulo": titulo,
                "fuente": urlparse(url).netloc, "verificable": bool(clave_txt and es_codigo(clave_txt)),
                "nivel": nivel}

    with ThreadPoolExecutor(max_workers=4) as pool:
        validos = [c for c in pool.map(bajar, candidatos[:max_resultados * 3]) if c]
    return validos[:max_resultados]


def fotos_de_ficha(datos: bytes) -> list[dict]:
    """Genera un candidato de imagen a partir de la primera página de un PDF."""
    miniatura = miniatura_pdf(datos)
    if not miniatura:
        return []
    try:
        with PILImage.open(io.BytesIO(miniatura)) as im:
            ancho, alto = im.size
            im.verify()
    except Exception:
        return []
    return [{
        "url": "", "datos": miniatura, "ext": ".png", "ancho": ancho, "alto": alto,
        "fuente": "PDF local", "titulo": "Primera página de la ficha técnica",
        "verificada": False, "baja_resolucion": min(ancho, alto) < MIN_LADO,
    }]


import re
import unicodedata


def limpiar_consulta(texto: str) -> str:
    """Normaliza una consulta para facilitar la búsqueda de productos."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^\w\s./-]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def es_codigo(texto: str) -> bool:
    """Detecta códigos alfanuméricos de productos, evitando medidas comunes."""
    texto = str(texto).strip()

    if not texto or len(texto) < 4 or len(texto) > 40:
        return False

    compacto = re.sub(r"[^A-Z0-9]", "", texto.upper())

    if not compacto or not re.search(r"[A-Z]", compacto):
        return False

    if not re.search(r"\d", compacto):
        return False

    if re.fullmatch(
        r"\d+(GB|TB|MB|ML|GR|G|KG|MM|CM|W|V|MAH|HZ|MP|PX)",
        compacto,
    ):
        return False

    return True


def menciones_clave(texto: str, claves) -> list:
    """Encuentra claves mencionadas en el texto, ignorando mayúsculas y tildes."""
    texto_norm = unicodedata.normalize("NFKD", str(texto))
    texto_norm = texto_norm.encode("ascii", "ignore").decode("ascii").upper()
    texto_norm = re.sub(r"[^A-Z0-9]", "", texto_norm)

    encontradas = []

    for clave in claves:
        clave_norm = unicodedata.normalize("NFKD", str(clave))
        clave_norm = clave_norm.encode("ascii", "ignore").decode("ascii").upper()
        clave_norm = re.sub(r"[^A-Z0-9]", "", clave_norm)

        if clave_norm and clave_norm in texto_norm:
            encontradas.append(clave)

    return encontradas
