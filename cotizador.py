"""Núcleo del cotizador: lee la cotización en Excel, busca fotos y fichas en la
biblioteca local y arma un PDF único: cotización + fotos + fichas técnicas.

Estructura de la biblioteca (carpeta "biblioteca" por defecto):

    biblioteca/
      indice.json            <- claves, nombres y alias aprendidos
      CF283A/foto.jpg        <- foto del producto (jpg, png o webp)
      CF283A/ficha.pdf       <- ficha técnica en PDF
      TK-3182/foto.png
      ...
"""
import csv
from functools import lru_cache
import io
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from copy import copy, deepcopy
from datetime import date
from pathlib import Path
from zipfile import ZipFile
from xml.sax.saxutils import escape

from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.properties import PageSetupProperties
from PIL import Image as PILImage
from pypdf import PdfReader, PdfWriter

from buscador import es_codigo, limpiar_consulta, menciones_clave
import pdf_cotizacion
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Líneas de la cotización que no son productos (no llevan foto ni ficha)
PALABRAS_NO_PRODUCTO = ("despacho", "flete", "envio", "transporte", "instalacion", "servicio")
EXT_FOTO = (".jpg", ".jpeg", ".png", ".webp")
_INDICE_LOCK = threading.RLock()
_INDICE_ABSENT = object()
# Tokens que parecen un código pero son medidas (64GB, 500ML, 75GR...)
_MEDIDA = re.compile(r"^(\d+(GB|TB|MB|ML|GR|G|KG|MM|CM|W|V|MAH|HZ|MP|PX)|\d+(X\d+)+(CM|MM|M)?)$")


def normalizar(texto) -> str:
    """Mayúsculas, sin tildes ni símbolos: 'TK-3182' -> 'TK3182'."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", t.upper())


_PALABRAS_BUSQUEDA = {"DE", "DEL", "LA", "EL", "LOS", "LAS", "PARA", "CON", "Y", "A",
                      "TIPO", "SIMILAR", "PARECIDO", "EQUIVALENTE", "UN", "UNA", "HOJAS",
                      "HOJA", "UNIDAD", "UNIDADES", "TAMANO", "MARCA", "PRODUCTO"}
_TAMANO_PRODUCTO = {"CARTA": "CARTA", "LETTER": "CARTA", "OFICIO": "OFICIO", "LEGAL": "OFICIO",
                    "A4": "A4", "A3": "A3", "A5": "A5", "TABLOIDE": "TABLOIDE"}
_MARCAS_PRODUCTO = {"EQUALIT", "DURACELL", "FULTON", "COLON", "HP", "CANON", "EPSON", "KYOCERA",
                    "BROTHER", "SAMSUNG", "XEROX", "LEXMARK", "TOSHIBA", "LAVORO", "RHEIN", "TORRE"}


@lru_cache(maxsize=8192)
def _tokens_producto(texto: str, ignorar_similares: bool = True) -> frozenset[str]:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().upper()
    tokens = re.findall(r"[A-Z]+\d+[A-Z]*|\d+[A-Z]+|[A-Z]+|\d+(?:\.\d+)?", t)
    salida = set()
    for token in tokens:
        if token.endswith("S") and len(token) > 4 and token[:-1] in {"RESMA", "TONER", "TINTA", "CARTUCHO"}:
            token = token[:-1]
        if token == "SIMILARES":
            token = "SIMILAR"
        if token not in _PALABRAS_BUSQUEDA or (token == "SIMILAR" and not ignorar_similares):
            salida.add(token)
    return frozenset(salida)


@lru_cache(maxsize=32)
def _carpetas_por_alias(ruta: str, mtime_ns: int) -> dict[str, tuple[Path, ...]]:
    """Indexa una vez las carpetas antiguas; evita recorrer toda la biblioteca por producto."""
    raiz = Path(ruta)
    mapa: dict[str, list[Path]] = {}
    for carpeta in raiz.iterdir():
        if carpeta.is_dir():
            mapa.setdefault(normalizar(carpeta.name), []).append(carpeta)
    return {alias: tuple(candidatas) for alias, candidatas in mapa.items()}


def _mapa_carpetas(raiz: Path) -> dict[str, tuple[Path, ...]]:
    try:
        mtime_ns = raiz.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return _carpetas_por_alias(str(raiz.resolve()), mtime_ns)


def _conflicto_producto(a: set[str], b: set[str]) -> bool:
    tamanos_a = {_TAMANO_PRODUCTO[t] for t in a if t in _TAMANO_PRODUCTO}
    tamanos_b = {_TAMANO_PRODUCTO[t] for t in b if t in _TAMANO_PRODUCTO}
    if tamanos_a and tamanos_b and tamanos_a.isdisjoint(tamanos_b):
        return True
    pilas_a, pilas_b = a & {"AA", "AAA"}, b & {"AA", "AAA"}
    if pilas_a and pilas_b and pilas_a.isdisjoint(pilas_b):
        return True
    marcas_a, marcas_b = a & _MARCAS_PRODUCTO, b & _MARCAS_PRODUCTO
    if marcas_a and marcas_b and marcas_a.isdisjoint(marcas_b):
        return True
    # No unir presentaciones distintas cuando ambas describen explícitamente una cantidad.
    nums_a = {t for t in a if t.isdigit() and len(t) <= 5}
    nums_b = {t for t in b if t.isdigit() and len(t) <= 5}
    return bool(nums_a and nums_b and nums_a.isdisjoint(nums_b))


def identidad_conflictiva(clave: str, ficha: dict) -> bool:
    """Marca fichas que mezclan tamaños, presentaciones o marcas incompatibles entre clave y alias."""
    alias = ficha.get("aliases", []) or []
    if not isinstance(alias, list):
        alias = [alias]
    variantes = [clave, ficha.get("nombre", ""), *alias]
    tokens = [_tokens_producto(v, ignorar_similares=False) for v in variantes if v]
    tamanos = {_TAMANO_PRODUCTO[t] for grupo in tokens for t in grupo if t in _TAMANO_PRODUCTO}
    pilas = {t for grupo in tokens for t in grupo if t in {"AA", "AAA"}}
    marcas = {t for grupo in tokens for t in grupo if t in _MARCAS_PRODUCTO}
    return len(tamanos) > 1 or len(pilas) > 1 or len(marcas) > 1


# ----------------------------------------------------------------------------
# 1. Lectura del Excel
# ----------------------------------------------------------------------------
def leer_cotizacion(ruta) -> list[dict]:
    """Devuelve las líneas de la cotización: cantidad, descripción y precios.

    Ubica la tabla por sus encabezados ('Cantidad', 'Descripción', 'P. Unitario'),
    así que no depende de filas ni columnas fijas.
    """
    ws = load_workbook(ruta).active
    fila_enc, cols = None, {}
    for fila in ws.iter_rows():
        etiquetas = {normalizar(c.value): c.column for c in fila if isinstance(c.value, str)}
        if "CANTIDAD" in etiquetas and "DESCRIPCION" in etiquetas:
            fila_enc, cols = fila[0].row, etiquetas
            break
    if fila_enc is None:
        raise ValueError("No encontré los encabezados 'Cantidad' y 'Descripción' en la hoja.")

    c_cant, c_desc, c_unit = cols["CANTIDAD"], cols["DESCRIPCION"], cols.get("PUNITARIO")
    lineas = []
    for r in range(fila_enc + 1, ws.max_row + 1):
        cant, desc = ws.cell(r, c_cant).value, ws.cell(r, c_desc).value
        if not isinstance(cant, (int, float)) or cant <= 0:
            continue
        if not isinstance(desc, str) or not desc.strip():
            continue
        desc = " ".join(desc.split())
        unit = ws.cell(r, c_unit).value if c_unit else None
        lineas.append({
            "fila": r,
            "cantidad": int(cant) if float(cant).is_integer() else cant,
            "descripcion": desc,
            "unitario": unit if isinstance(unit, (int, float)) else None,
            "es_producto": not any(p in normalizar(desc).lower() for p in PALABRAS_NO_PRODUCTO),
        })
    return lineas


# ----------------------------------------------------------------------------
# 2. Biblioteca local de productos
# ----------------------------------------------------------------------------
class Biblioteca:
    def __init__(self, carpeta="biblioteca"):
        self.dir = Path(carpeta)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._idx = self.dir / "indice.json"
        self.indice: dict = json.loads(self._idx.read_text("utf-8")) if self._idx.exists() else {}
        if not isinstance(self.indice, dict):
            raise ValueError(f"El índice de productos no es válido: {self._idx}")
        self._snapshot_indice = deepcopy(self.indice)
        self._indice_mtime_ns = self._mtime_indice()
        self.asociar_carpetas_anteriores()

    def _mtime_indice(self) -> int:
        try:
            return self._idx.stat().st_mtime_ns
        except OSError:
            return 0

    def refrescar_si_cambio(self) -> bool:
        """Recarga el índice solo si otra sesión modificó la biblioteca."""
        mtime_ns = self._mtime_indice()
        if mtime_ns == self._indice_mtime_ns:
            return False
        indice = json.loads(self._idx.read_text("utf-8")) if self._idx.exists() else {}
        if not isinstance(indice, dict):
            raise ValueError(f"El índice de productos no es válido: {self._idx}")
        self.indice = indice
        self._snapshot_indice = deepcopy(indice)
        self._indice_mtime_ns = mtime_ns
        return True

    def _guardar_indice(self):
        """Guarda solo los cambios de esta instancia sin borrar productos añadidos por otra sesión."""
        with _INDICE_LOCK:
            indice_actual = json.loads(self._idx.read_text("utf-8")) if self._idx.exists() else {}
            if not isinstance(indice_actual, dict):
                raise ValueError(f"El índice de productos no es válido: {self._idx}")

            original = self._snapshot_indice
            local = self.indice
            combinado = deepcopy(indice_actual)

            # Propaga las eliminaciones solo si ninguna otra sesión cambió esa ficha después de leerla.
            for clave in original:
                if clave not in local and combinado.get(clave, _INDICE_ABSENT) == original[clave]:
                    combinado.pop(clave, None)

            for clave, ficha_local in local.items():
                ficha_original = original.get(clave, _INDICE_ABSENT)
                ficha_actual = combinado.get(clave, _INDICE_ABSENT)

                if ficha_original is _INDICE_ABSENT:
                    # Dos personas pueden registrar códigos distintos al mismo tiempo.
                    if ficha_actual is _INDICE_ABSENT:
                        combinado[clave] = deepcopy(ficha_local)
                    elif isinstance(ficha_actual, dict) and isinstance(ficha_local, dict):
                        fusion = deepcopy(ficha_actual)
                        for campo, valor in ficha_local.items():
                            if campo == "aliases":
                                fusion[campo] = sorted(set(fusion.get(campo, [])) | set(valor or []))
                            elif campo not in fusion:
                                fusion[campo] = deepcopy(valor)
                        combinado[clave] = fusion
                    continue

                if ficha_local == ficha_original:
                    continue
                if ficha_actual is _INDICE_ABSENT or ficha_actual == ficha_original:
                    combinado[clave] = deepcopy(ficha_local)
                    continue

                # Si dos sesiones editaron una misma ficha, combina los campos cambiados
                # por separado para preservar, por ejemplo, los alias de ambas sesiones.
                if isinstance(ficha_actual, dict) and isinstance(ficha_original, dict) and isinstance(ficha_local, dict):
                    fusion = deepcopy(ficha_actual)
                    campos = set(ficha_original) | set(ficha_local)
                    for campo in campos:
                        base = ficha_original.get(campo, _INDICE_ABSENT)
                        valor = ficha_local.get(campo, _INDICE_ABSENT)
                        if valor == base:
                            continue
                        if valor is _INDICE_ABSENT:
                            if fusion.get(campo, _INDICE_ABSENT) == base:
                                fusion.pop(campo, None)
                        elif campo == "aliases":
                            fusion[campo] = sorted(set(fusion.get(campo, [])) | set(valor or []))
                        else:
                            fusion[campo] = deepcopy(valor)
                    combinado[clave] = fusion
                else:
                    combinado[clave] = deepcopy(ficha_local)

            temporal = self._idx.with_suffix(".json.tmp")
            temporal.write_text(json.dumps(combinado, ensure_ascii=False, indent=2), "utf-8")
            os.replace(temporal, self._idx)
            self.indice = combinado
            self._snapshot_indice = deepcopy(combinado)
            self._indice_mtime_ns = self._mtime_indice()

    def asociar_carpetas_anteriores(self, score_minimo: float = 0.65, margen_minimo: float = 0.12) -> int:
        """Vincula archivos bajo claves antiguas cuando una sola ficha coincide con alta confianza."""
        aliases_vistos = {}
        for clave, ficha in self.indice.items():
            valores = ficha.get("aliases", []) or []
            if not isinstance(valores, list):
                valores = [valores]
            for valor in [clave, *valores]:
                aliases_vistos.setdefault(normalizar(valor), set()).add(clave)

        cantidad = 0
        carpetas = sorted(
            (carpeta for grupo in _mapa_carpetas(self.dir).values() for carpeta in grupo),
            key=lambda p: p.name.casefold(),
        )
        for carpeta in carpetas:
            if not carpeta.is_dir() or carpeta.name.strip().upper() in self.indice:
                continue
            tiene_archivo = any((carpeta / f"foto{ext}").exists() for ext in EXT_FOTO) or (carpeta / "ficha.pdf").exists()
            if not tiene_archivo:
                continue
            identidad = normalizar(carpeta.name)
            if identidad in aliases_vistos:
                continue
            candidatos = self.coincidencias_semanticas(carpeta.name, limite=2)
            primero = candidatos[0] if candidatos else None
            segundo = candidatos[1] if len(candidatos) > 1 else None
            if not primero or primero["score"] < score_minimo:
                continue
            if segundo and primero["score"] - segundo["score"] < margen_minimo:
                continue
            ficha = self.indice.get(primero["clave"], {})
            aliases = ficha.setdefault("aliases", [])
            if not isinstance(aliases, list):
                aliases = ficha["aliases"] = [aliases]
            aliases.append(carpeta.name)
            aliases_vistos.setdefault(identidad, set()).add(primero["clave"])
            cantidad += 1
        if cantidad:
            self._guardar_indice()
        return cantidad

    @staticmethod
    def _slug(clave: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", clave.strip().upper())

    def carpeta(self, clave: str) -> Path:
        return self.dir / self._slug(clave)

    def archivos(self, clave: str):
        """(foto, ficha), usando también las carpetas de códigos antiguos vinculados por alias."""
        if not clave:
            return None, None
        c = self.carpeta(clave)
        foto = next((p for e in EXT_FOTO if (p := c / f"foto{e}").exists()), None)
        ficha = c / "ficha.pdf"
        if foto and ficha.exists():
            return foto, ficha

        ficha_producto = self.indice.get(clave.strip().upper(), {})
        alias_norm = {normalizar(clave)}
        alias_norm.update(normalizar(a) for a in ficha_producto.get("aliases", []) if a)
        mapa_carpetas = _mapa_carpetas(self.dir)
        carpetas_anteriores = sorted(
            (p for alias in alias_norm for p in mapa_carpetas.get(alias, ()) if p != c),
            key=lambda p: p.name,
        )
        for anterior in carpetas_anteriores:
            if not foto:
                foto = next((p for e in EXT_FOTO if (p := anterior / f"foto{e}").exists()), None)
            if not ficha.exists():
                ficha_antigua = anterior / "ficha.pdf"
                if ficha_antigua.exists():
                    ficha = ficha_antigua
            if foto and ficha.exists():
                break
        return foto, (ficha if ficha.exists() else None)

    def guardar_archivo(self, clave: str, tipo: str, nombre_origen: str, datos: bytes):
        c = self.carpeta(clave)
        c.mkdir(parents=True, exist_ok=True)
        if tipo == "foto":
            ext = Path(nombre_origen).suffix.lower()
            if ext not in EXT_FOTO:
                raise ValueError(f"Formato de imagen no soportado: {ext}")
            for e in EXT_FOTO:  # reemplaza la foto anterior
                (c / f"foto{e}").unlink(missing_ok=True)
            (c / f"foto{ext}").write_bytes(datos)
        elif tipo == "ficha":
            (c / "ficha.pdf").write_bytes(datos)
        else:
            raise ValueError("tipo debe ser 'foto' o 'ficha'")

    def registrar(self, clave: str, nombre: str | None = None, alias: str | None = None,
                  precio_neto: float | None = None):
        """Anota el producto en el índice. El alias permite reconocer la misma
        descripción en futuras cotizaciones aunque no traiga el código; el precio
        neto se recuerda para autocompletar la próxima orden."""
        if not clave:
            return
        clave = clave.strip().upper()
        ficha = self.indice.setdefault(clave, {"nombre": nombre or clave, "aliases": []})
        if precio_neto:
            ficha["precio_neto"] = float(precio_neto)
        if alias and len(normalizar(alias)) >= 6:
            n = normalizar(alias)
            if n not in ficha["aliases"]:
                ficha["aliases"].append(n)
        self._guardar_indice()

    # ---------------------------------------------------------- catálogo editable
    CAMPOS_EDITABLES = ("nombre", "costo_neto", "margen_pct", "notas", "precio_neto", "ficha_no_requerida")

    def actualizar(self, clave: str, **campos):
        """Cambia nombre, costo neto, margen %, notas o último precio. None borra el dato."""
        ficha = self.indice.get(clave.strip().upper())
        if ficha is None:
            return
        for k, v in campos.items():
            if k not in self.CAMPOS_EDITABLES:
                continue
            if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
                if k != "nombre":
                    ficha.pop(k, None)
            elif k == "ficha_no_requerida":
                ficha[k] = bool(v)
            else:
                ficha[k] = float(v) if k in ("costo_neto", "margen_pct", "precio_neto") else str(v).strip()
        self._guardar_indice()

    def eliminar(self, clave: str):
        """Borra el producto del catálogo junto con su foto y su ficha."""
        clave = clave.strip().upper()
        self.indice.pop(clave, None)
        shutil.rmtree(self.carpeta(clave), ignore_errors=True)
        self._guardar_indice()

    def renombrar(self, viejo: str, nuevo: str, combinar: bool = False) -> str:
        """Cambia el código de un producto (y mueve su carpeta). Si el nuevo ya existe hay que
        pedir combinar=True: se unen los alias y se conserva lo que el nuevo ya tenía."""
        viejo, nuevo = viejo.strip().upper(), nuevo.strip().upper()
        if not nuevo or viejo == nuevo or viejo not in self.indice:
            raise ValueError("Código no válido.")
        if nuevo in self.indice and not combinar:
            raise ValueError("Ya existe un producto con ese código.")
        origen = self.indice.pop(viejo)
        destino = self.indice.setdefault(nuevo, {"nombre": origen.get("nombre", nuevo), "aliases": []})
        destino["aliases"] = sorted(set(destino.get("aliases", [])) | set(origen.get("aliases", []))
                                    | {normalizar(viejo)})
        for k, v in origen.items():
            if k not in ("aliases", "nombre") and k not in destino:
                destino[k] = v
        carp_v, carp_n = self.carpeta(viejo), self.carpeta(nuevo)
        if carp_v.exists():
            carp_n.mkdir(parents=True, exist_ok=True)
            for archivo in carp_v.iterdir():
                if not (carp_n / archivo.name).exists() and not (
                        archivo.stem == "foto" and any((carp_n / f"foto{e}").exists() for e in EXT_FOTO)):
                    shutil.move(str(archivo), carp_n / archivo.name)
            shutil.rmtree(carp_v, ignore_errors=True)
        self._guardar_indice()
        return nuevo

    def precio_sugerido(self, clave: str) -> float | None:
        """Precio neto sugerido = costo × (1 + margen %). None si falta el costo o el margen."""
        ficha = self.indice.get(clave.strip().upper(), {})
        if ficha.get("costo_neto") and ficha.get("margen_pct") is not None:
            return ficha["costo_neto"] * (1 + ficha["margen_pct"] / 100)
        return None

    def filas_catalogo(self) -> list[dict]:
        filas = []
        for clave, f in sorted(self.indice.items()):
            foto, ficha = self.archivos(clave)
            filas.append({"clave": clave, "nombre": f.get("nombre", clave), "foto": bool(foto), "ficha": bool(ficha),
                          "ficha_no_requerida": bool(f.get("ficha_no_requerida", False)),
                          "precio_neto": f.get("precio_neto"), "costo_neto": f.get("costo_neto"),
                          "margen_pct": f.get("margen_pct"), "sugerido_neto": self.precio_sugerido(clave),
                          "notas": f.get("notas", "")})
        return filas

    def detectar(self, descripcion: str, solo_conocidos: bool = False) -> str:
        """Clave del producto según la descripción ('' si no se reconoce).

        1) Busca claves/alias ya conocidos en la biblioteca (gana el más largo).
        2) Si no, toma el primer token que parezca código de fabricante
           (mezcla de letras y números: CF283A, TK-3182, T544...).
        """
        dn = normalizar(descripcion)
        mejor, largo = "", 0
        for clave, datos in self.indice.items():
            if identidad_conflictiva(clave, datos):
                continue
            aliases = datos.get("aliases", []) or []
            if not isinstance(aliases, list):
                aliases = [aliases]
            for token in [clave, datos.get("nombre", "")] + aliases:
                n = normalizar(token)
                if len(n) >= 3 and n in dn and len(n) > largo:
                    mejor, largo = clave, len(n)
        if mejor or solo_conocidos:
            return mejor
        for tok in re.findall(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)*\b", descripcion.upper()):
            if len(tok) >= 4 and re.search(r"[A-Z]", tok) and re.search(r"\d", tok) \
                    and not _MEDIDA.match(tok):
                return tok
        return ""

    def coincidencias_semanticas(self, descripcion: str, limite: int = 5) -> list[dict]:
        """Productos del catálogo más cercanos por nombre y alias, sin mezclar medidas incompatibles."""
        consulta = _tokens_producto(descripcion, ignorar_similares=True)
        if not consulta:
            return []
        resultados = []
        for clave, ficha in self.indice.items():
            if identidad_conflictiva(clave, ficha):
                continue
            variantes = [ficha.get("nombre", clave), clave, *(ficha.get("aliases", []) or [])]
            mejor = 0.0
            for variante in variantes:
                tokens = _tokens_producto(variante, ignorar_similares=False)
                if not tokens or _conflicto_producto(consulta, tokens):
                    continue
                comunes = consulta & tokens
                if not comunes:
                    continue
                # F1 sobre tokens: tolera palabras adicionales sin aceptar un solo término genérico.
                score = 2 * len(comunes) / (len(consulta) + len(tokens))
                marcas_consulta = consulta & _MARCAS_PRODUCTO
                marcas_candidata = tokens & _MARCAS_PRODUCTO
                if marcas_consulta and not marcas_candidata:
                    score *= 0.75
                if len(comunes) == 1 and len(consulta) > 1:
                    score *= 0.65
                mejor = max(mejor, score)
            if mejor:
                resultados.append({"clave": clave, "nombre": ficha.get("nombre", clave), "score": mejor})
        return sorted(resultados, key=lambda x: (-x["score"], x["clave"]))[:max(1, limite)]



def clave_descriptiva(descripcion: str) -> str:
    """Clave legible para productos sin código de fabricante:
    SACA-CORCHETES-PALANCA-METALICO.
    """
    t = unicodedata.normalize(
        "NFKD",
        limpiar_consulta(descripcion)
    ).encode("ascii", "ignore").decode()

    palabras = [
        re.sub(r"[^A-Za-z0-9]", "", w).upper()
        for w in t.split()
    ]

    palabras = [w for w in palabras if w and not w.isdigit()][:4]

    return "-".join(palabras)[:40] or "PRODUCTO"



def resolver_clave(bib: "Biblioteca", descripcion: str, ia: dict | None = None) -> tuple[str, str]:
    """(clave, origen). Prioridad: lo que ya aprendió la biblioteca > la IA (certeza alta o media)
    > código encontrado en el texto > clave descriptiva. Así todo producto queda buscable."""
    if conocido := bib.detectar(descripcion, solo_conocidos=True):
        return conocido, "biblioteca"
    if ia and ia.get("clave") and ia.get("certeza") in ("alta", "media"):
        clave = ia["clave"].strip().upper()
        if clave in bib.indice:
            return clave, "ia"
    candidatos = bib.coincidencias_semanticas(descripcion, limite=2)
    if candidatos and candidatos[0]["score"] >= 0.78 and (
            len(candidatos) == 1 or candidatos[0]["score"] - candidatos[1]["score"] >= 0.10):
        return candidatos[0]["clave"], "similitud"
    if texto := bib.detectar(descripcion):
        return texto, "texto"
    return clave_descriptiva(descripcion), "descripcion"


# ----------------------------------------------------------------------------
# 3. Generación del PDF
# ----------------------------------------------------------------------------
def _comando_soffice() -> list[str]:
    cmd = os.environ.get("SOFFICE_CMD")
    if cmd:
        return shlex.split(cmd)
    for nombre in ("soffice", "libreoffice"):
        if ruta := shutil.which(nombre):
            return [ruta]
    for ruta in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                 r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if os.path.exists(ruta):
            return [ruta]
    raise RuntimeError("No encontré LibreOffice (gratis en libreoffice.org). "
                       "Instálalo o define la variable SOFFICE_CMD con la ruta de soffice.")


def excel_a_pdf(xlsx, destino, motor="nativo") -> Path:
    """Convierte la cotización a PDF.

    motor="nativo": la dibuja directamente (rápido, sin programas externos ni ventanas).
    motor="libreoffice": usa LibreOffice para un formato idéntico al Excel.
    """
    if motor == "nativo":
        return pdf_cotizacion.render_pdf(xlsx, destino)
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "cotizacion.xlsx"
        wb = load_workbook(xlsx)
        ws = wb.active
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        wb.save(copia)
        extra = {"creationflags": 0x08000000} if os.name == "nt" else {}  # sin ventana de consola
        perfil = (Path(tmp) / "perfil_lo").as_uri()  # perfil propio: no choca con un LibreOffice abierto
        subprocess.run([*_comando_soffice(), "--headless", "--invisible", "--norestore", "--nolockcheck",
                        "--nologo", "--nofirststartwizard", f"-env:UserInstallation={perfil}",
                        "--convert-to", "pdf", "--outdir", tmp, str(copia)],
                       check=True, capture_output=True, timeout=180, cwd=tmp, **extra)
        shutil.copy(Path(tmp) / "cotizacion.pdf", destino)
    return Path(destino)


def _imagen(ruta, max_w, max_h) -> Image:
    with PILImage.open(ruta) as im:
        if im.mode in ("RGBA", "LA", "P"):  # fondos transparentes -> blanco
            im = im.convert("RGBA")
            fondo = PILImage.new("RGB", im.size, "white")
            fondo.paste(im, mask=im.split()[-1])
            im = fondo
        else:
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        w, h = im.size
    buf.seek(0)
    esc = min(max_w / w, max_h / h)
    return Image(buf, width=w * esc, height=h * esc)


def anexo_fotos(items: list[dict], destino) -> Path:
    """PDF con una grilla de 2 columnas: foto + clave + descripción."""
    estilos = getSampleStyleSheet()
    pie = ParagraphStyle("pie", parent=estilos["Normal"], fontSize=9, leading=11, alignment=1)
    celdas = []
    for it in items:
        foto = it["foto"]
        if foto:
            try:
                img = _imagen(foto, 7.5 * cm, 5.6 * cm)
            except Exception:
                foto = None
        if not foto:
            img = Paragraph("<font color='#999999'>Sin imagen</font>", pie)
        titulo = f"<b>{escape(it['clave'])}</b><br/>" if it["clave"] else ""
        desc = escape(it["descripcion"][:130])
        celdas.append([img, Spacer(1, 6),
                       Paragraph(f"{titulo}{desc}<br/><font color='#666666'>Cantidad cotizada: "
                                 f"{it['cantidad']}</font>", pie)])
    filas = [celdas[i:i + 2] for i in range(0, len(celdas), 2)]
    if filas and len(filas[-1]) == 1:
        filas[-1].append("")
    tabla = Table(filas, colWidths=[9 * cm, 9 * cm], rowHeights=[8.0 * cm] * len(filas))
    tabla.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
    ]))

    def numerar(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(letter[0] - 1.5 * cm, 1 * cm, f"Anexo 1 · Fotografías · pág. {doc.page}")

    doc = SimpleDocTemplate(str(destino), pagesize=letter, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.8 * cm)
    doc.build([Paragraph("Anexo 1 · Fotografías de los productos", estilos["Heading2"]),
               Spacer(1, 8), tabla], onFirstPage=numerar, onLaterPages=numerar)
    return Path(destino)


def generar_pdf_final(xlsx, lineas: list[dict], bib: Biblioteca, salida, motor="nativo") -> dict:
    """Arma: cotización + anexo de fotos + fichas técnicas (una por producto).

    `lineas` son las líneas a incluir, cada una con 'clave' (puede ir vacía).
    Devuelve un informe con lo que faltó (sin clave / sin foto / sin ficha).
    """
    informe = {"sin_clave": [], "sin_foto": [], "sin_ficha": [], "ficha_dudosa": [], "ficha_ilegible": []}
    items, vistos, fichas = [], set(), []
    for ln in lineas:
        clave = ln.get("clave", "").strip().upper()
        if clave and clave in vistos:  # mismo producto en dos líneas: una sola foto/ficha
            continue
        foto, ficha = bib.archivos(clave)
        if not clave:
            informe["sin_clave"].append(ln["descripcion"][:60])
        else:
            vistos.add(clave)
            if not foto:
                informe["sin_foto"].append(clave)
            ficha_no_requerida = bool(bib.indice.get(clave, {}).get("ficha_no_requerida", False))
            if not ficha and not ficha_no_requerida:
                informe["sin_ficha"].append(clave)
            elif ficha:
                fichas.append((clave, ficha))
                try:  # ¿la ficha guardada realmente nombra este producto?
                    if es_codigo(clave) and len(menciones_clave(ficha.read_bytes(), [clave])) == 0:
                        informe["ficha_dudosa"].append(clave)
                except Exception:
                    informe["ficha_dudosa"].append(clave)
        items.append({**ln, "clave": clave, "foto": foto})

    with tempfile.TemporaryDirectory() as tmp:
        cot = excel_a_pdf(xlsx, Path(tmp) / "cot.pdf", motor)
        escritor = PdfWriter()
        escritor.append(str(cot), outline_item="Cotización")
        if items:
            escritor.append(str(anexo_fotos(items, Path(tmp) / "fotos.pdf")),
                            outline_item="Anexo 1 · Fotografías")
        for clave, ficha in fichas:
            try:
                escritor.append(str(ficha), outline_item=f"Ficha técnica · {clave}")
            except Exception:
                informe["ficha_ilegible"].append(clave)
        with open(salida, "wb") as f:
            escritor.write(f)
    informe["paginas"] = len(PdfReader(str(salida)).pages)
    return informe


# ----------------------------------------------------------------------------
# 4. Orden en bruto -> cotización con formato (usa tu plantilla de Excel)
# ----------------------------------------------------------------------------
PLANTILLA = Path(__file__).with_name("plantilla_cotizacion.xlsx")
IVA = 0.19
_QTY_INICIO = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:x|×|un\.?|unid\.?|unidades?|u\.?)?\s*[:\-–.)]*\s+(\S.*)$", re.I)
_QTY_FINAL = re.compile(r"^(\S.*?)[\s,;:\-–]*[(\[]?\s*(?:x|×|cant(?:idad)?[:.]?)\s*(\d+)\s*"
                        r"(?:un\w*\.?|u\.?)?[)\]]?$", re.I)
_QTY_PAREN = re.compile(r"^(\S.*?)[\s,;:\-–]*\(\s*(\d+)\s*(?:un\w*\.?|u\.?)\s*\)$", re.I)
_SINONIMOS_CANT = {"CANTIDAD", "CANT", "QTY", "UNIDADES", "UNID", "CANTIDADES"}
_SINONIMOS_DESC = {"DESCRIPCION", "PRODUCTO", "PRODUCTOS", "DETALLE", "ITEM", "ARTICULO", "NOMBRE", "GLOSA"}


def limpiar_descripcion(texto: str) -> str:
    """Espacios normalizados y primera letra en mayúscula."""
    t = " ".join(str(texto).split())
    return t[:1].upper() + t[1:]


def _num(valor):
    try:
        return float(str(valor).replace(".", "").replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        return None


def _cantidad(n: float):
    return int(n) if float(n).is_integer() else n


def parsear_texto_orden(texto: str) -> list[dict]:
    """Convierte texto pegado (una línea por producto) en [{cantidad, descripcion}].

    Acepta: '10 Cartucho CF283A', '10x CF283A', 'Cartucho CF283A x10',
    'Cartucho CF283A (10 un)' y filas pegadas desde Excel (separadas por tab).
    Si no encuentra cantidad usa 1 (se corrige en la tabla de revisión).
    """
    salida = []
    for linea in texto.splitlines():
        s = linea.strip().lstrip("•-*·").strip()
        if not s:
            continue
        if "\t" in s:
            partes = [p.strip() for p in s.split("\t") if p.strip()]
            nums = [(i, _num(p)) for i, p in enumerate(partes) if _num(p) is not None]
            textos = [p for p in partes if _num(p) is None]
            if nums and textos:
                salida.append({"cantidad": _cantidad(nums[0][1]), "descripcion": limpiar_descripcion(max(textos, key=len))})
                continue
        if m := _QTY_INICIO.match(s):
            salida.append({"cantidad": _cantidad(_num(m.group(1)) or 1), "descripcion": limpiar_descripcion(m.group(2))})
        elif (m := _QTY_FINAL.match(s)) or (m := _QTY_PAREN.match(s)):
            salida.append({"cantidad": int(m.group(2)), "descripcion": limpiar_descripcion(m.group(1))})
        else:
            salida.append({"cantidad": 1, "descripcion": limpiar_descripcion(s)})
    return salida


def leer_orden_archivo(ruta) -> list[dict]:
    """Lee una orden en bruto desde Excel o CSV (con o sin encabezados)."""
    ruta = Path(ruta)
    if ruta.suffix.lower() == ".csv":
        crudo = ruta.read_bytes().decode("utf-8-sig", errors="replace")
        dialecto = csv.Sniffer().sniff(crudo[:2000], delimiters=",;\t") if crudo.strip() else csv.excel
        filas = [list(f) for f in csv.reader(io.StringIO(crudo), dialecto)]
    else:
        filas = [[c.value for c in fila] for fila in load_workbook(ruta, data_only=True).active.iter_rows()]

    col_cant = col_desc = None
    for fila in filas[:15]:  # ¿hay encabezados?
        etiquetas = {normalizar(v): i for i, v in enumerate(fila) if isinstance(v, str)}
        c = next((i for k, i in etiquetas.items() if k in _SINONIMOS_CANT), None)
        d = next((i for k, i in etiquetas.items() if k in _SINONIMOS_DESC), None)
        if c is not None and d is not None:
            col_cant, col_desc = c, d
            break

    salida = []
    for fila in filas:
        textos = [str(v).strip() for v in fila if isinstance(v, str) and v.strip() and _num(v) is None]
        if col_desc is not None:
            desc = fila[col_desc] if col_desc < len(fila) else None
            cant = _num(fila[col_cant]) if col_cant < len(fila) else None
            if isinstance(desc, str) and desc.strip() and cant and cant > 0:
                salida.append({"cantidad": _cantidad(cant), "descripcion": limpiar_descripcion(desc)})
            continue
        if not textos:
            continue
        nums = [_num(v) for v in fila if not isinstance(v, str) and _num(v) is not None and _num(v) > 0]
        desc = max(textos, key=len)
        if nums:
            salida.append({"cantidad": _cantidad(nums[0]), "descripcion": limpiar_descripcion(desc)})
        else:
            salida += parsear_texto_orden(desc)
    return salida


def neto_desde(precio: float, con_iva: bool) -> float:
    """Precio neto unitario a partir de lo que el usuario ingresó."""
    return precio / (1 + IVA) if con_iva else float(precio)


def precio_para_mostrar(precio_neto: float, con_iva: bool) -> int:
    return round(precio_neto * (1 + IVA)) if con_iva else round(precio_neto)


def crear_cotizacion_xlsx(lineas: list[dict], cliente: dict, salida, plantilla=None) -> Path:
    """Genera una cotización profesional, compacta y con filas acordes a los productos."""
    origen = Path(plantilla or PLANTILLA)
    wb = Workbook()
    ws = wb.active
    ws.title = "Cotización"
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 90

    # Recupera el logo y los datos comerciales que ya trae la plantilla instalada.
    empresa_default = [
        "Giovanna Valenzuela Heresi", "Galería Sangra Colón Local N° 16",
        "Fono: +569 98498758", "Art. Nac. e Importados, insumos de Computación",
        "Librería · RUT 9.705.675-7", "giovanna@gioval.cl",
    ]
    empresa = empresa_default
    try:
        fuente = load_workbook(origen, data_only=True, read_only=True)
        hoja_fuente = fuente.active
        extraidos = [str(hoja_fuente.cell(r, 4).value).strip() for r in range(2, 8)
                     if hoja_fuente.cell(r, 4).value not in (None, "")]
        if extraidos:
            empresa = extraidos[:6]
        fuente.close()
    except Exception:
        pass

    azul = "183B63"
    azul_medio = "2C5D86"
    azul_claro = "EAF1F7"
    gris = "526274"
    gris_claro = "F6F8FB"
    linea = "D8E0E8"
    blanco = "FFFFFF"
    texto = "243447"
    fuente_base = "Arial"
    borde_inferior = Border(bottom=Side(style="thin", color=linea))
    borde_fuerte = Border(top=Side(style="medium", color=azul))
    relleno_cabecera = PatternFill("solid", fgColor=azul)
    relleno_seccion = PatternFill("solid", fgColor=azul_claro)

    for columna, ancho in {"A": 2.5, "B": 12, "C": 14, "D": 14, "E": 14, "F": 14,
                           "G": 17, "H": 18}.items():
        ws.column_dimensions[columna].width = ancho
    ws.row_dimensions[1].height = 8
    ws.row_dimensions[2].height = 22
    for r in range(3, 8):
        ws.row_dimensions[r].height = 15
    ws.row_dimensions[8].height = 8

    for r in range(2, 8):
        ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=6)
        celda = ws.cell(r, 4, empresa[r - 2] if r - 2 < len(empresa) else "")
        celda.font = Font(name=fuente_base, size=10 if r == 2 else 9,
                          bold=(r == 2), color=azul if r == 2 else gris)
        celda.alignment = Alignment(vertical="center", wrap_text=False)

    ws.merge_cells("G2:H2")
    ws["G2"] = "COTIZACIÓN"
    ws["G2"].font = Font(name=fuente_base, size=16, bold=True, color=azul)
    ws["G2"].alignment = Alignment(horizontal="right", vertical="center")
    ws.merge_cells("G3:H3")
    ws["G3"] = "Propuesta comercial"
    ws["G3"].font = Font(name=fuente_base, size=9, color=gris)
    ws["G3"].alignment = Alignment(horizontal="right", vertical="center")

    try:
        with ZipFile(origen) as archivo:
            medios = sorted(n for n in archivo.namelist() if n.startswith("xl/media/") and not n.endswith("/"))
            if medios:
                logo = XLImage(io.BytesIO(archivo.read(medios[0])))
                escala = min(1.0, 180 / logo.width, 78 / logo.height)
                logo.width, logo.height = int(logo.width * escala), int(logo.height * escala)
                ws.add_image(logo, "B2")
    except Exception:
        pass

    # Datos del cliente y de la cotización.
    ws.merge_cells("B9:H9")
    ws["B9"] = "DATOS DEL CLIENTE"
    ws["B9"].fill = relleno_seccion
    ws["B9"].font = Font(name=fuente_base, size=10, bold=True, color=azul)
    ws["B9"].alignment = Alignment(vertical="center")
    for col in range(2, 9):
        ws.cell(9, col).fill = relleno_seccion
    ws.row_dimensions[9].height = 20

    pares = [
        (10, "Nombre:", cliente.get("nombre"), "RUT:", cliente.get("rut")),
        (11, "Dirección:", cliente.get("direccion"), "Fecha:", cliente.get("fecha")),
        (12, "Ciudad:", cliente.get("ciudad"), "N° Cotización:", str(cliente.get("numero") or "")),
        (13, "Teléfono:", cliente.get("telefono"), "Correo:", cliente.get("email")),
    ]
    for r, etiqueta_izq, valor_izq, etiqueta_der, valor_der in pares:
        ws.cell(r, 2, etiqueta_izq)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)
        ws.cell(r, 3, valor_izq or None)
        ws.cell(r, 6, etiqueta_der)
        ws.merge_cells(start_row=r, start_column=7, end_row=r, end_column=8)
        ws.cell(r, 7, valor_der or None)
        for col in (2, 6):
            ws.cell(r, col).font = Font(name=fuente_base, size=9, bold=True, color=gris)
            ws.cell(r, col).alignment = Alignment(vertical="center")
        for col in (3, 7):
            celda = ws.cell(r, col)
            celda.font = Font(name=fuente_base, size=9, color=texto)
            celda.fill = PatternFill("solid", fgColor=gris_claro)
            celda.alignment = Alignment(vertical="center", wrap_text=True)
            celda.border = borde_inferior
        ws.row_dimensions[r].height = 22
    ws["G11"].number_format = "dd/mm/yyyy"
    ws["G12"].number_format = "@"

    ws.merge_cells("B15:H15")
    ws["B15"] = "Detalle de productos · montos netos expresados en pesos chilenos (CLP)"
    ws["B15"].font = Font(name=fuente_base, size=9, italic=True, color=gris)
    ws["B15"].alignment = Alignment(vertical="center")
    ws.row_dimensions[14].height = 6
    ws.row_dimensions[15].height = 18

    encabezado = 16
    ws.merge_cells(start_row=encabezado, start_column=3, end_row=encabezado, end_column=6)
    for celda, valor in ((2, "Cantidad"), (3, "Descripción"), (7, "P. Unitario"), (8, "Total")):
        ws.cell(encabezado, celda, valor)
    for col in range(2, 9):
        celda = ws.cell(encabezado, col)
        celda.fill = relleno_cabecera
        celda.font = Font(name=fuente_base, size=9, bold=True, color=blanco)
        celda.alignment = Alignment(horizontal="center", vertical="center")
        celda.border = Border(bottom=Side(style="thin", color=blanco))
    ws.row_dimensions[encabezado].height = 25

    primera = encabezado + 1
    cantidad_filas = max(1, len(lineas))
    ultima = primera + cantidad_filas - 1
    ancho_descripcion = sum(ws.column_dimensions[c].width or 12 for c in "CDEF")
    for i, r in enumerate(range(primera, ultima + 1)):
        ln = lineas[i] if i < len(lineas) else None
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=6)
        ws.cell(r, 2, ln.get("cantidad") if ln else None)
        descripcion_excel = str(ln.get("descripcion", "")) if ln else ""
        if ln and ln.get("unidad"):
            unidad = str(ln["unidad"]).strip()
            if unidad and unidad.casefold() not in descripcion_excel.casefold():
                descripcion_excel += f" · Unidad: {unidad}"
        if ln and ln.get("codigo"):
            codigo = str(ln["codigo"]).strip()
            if codigo and codigo.casefold() not in descripcion_excel.casefold():
                descripcion_excel += f" · Código: {codigo}"
        ws.cell(r, 3, descripcion_excel or None)
        precio = ln.get("precio_neto", ln.get("unitario")) if ln else None
        ws.cell(r, 7, precio)
        ws.cell(r, 8, f'=IF(OR(B{r}="",G{r}=""),"",ROUND(B{r}*G{r},0))')
        filas_alt = i % 2 == 1
        for col in range(2, 9):
            celda = ws.cell(r, col)
            celda.font = Font(name=fuente_base, size=10, color=texto)
            celda.fill = PatternFill("solid", fgColor=gris_claro if filas_alt else blanco)
            celda.border = borde_inferior
            celda.alignment = Alignment(vertical="center")
        ws.cell(r, 2).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(r, 3).alignment = Alignment(vertical="center", wrap_text=True)
        for col in (7, 8):
            ws.cell(r, col).alignment = Alignment(horizontal="right", vertical="center")
            ws.cell(r, col).number_format = '"$"#,##0;[Red]-"$"#,##0'
        if precio is not None:
            ws.cell(r, 7).number_format = '"$"#,##0;[Red]-"$"#,##0'
        descripcion = str(ln.get("descripcion", "")) if ln else ""
        renglones = max(1, math.ceil(len(descripcion) / max(1, ancho_descripcion * 1.15)))
        ws.row_dimensions[r].height = min(54, max(25, 15 * renglones + 8))
        if ln and isinstance(ln.get("cantidad"), (int, float)):
            ws.cell(r, 2).number_format = "#,##0.##"

    t_neto, t_iva, t_total = ultima + 2, ultima + 3, ultima + 4
    ws[f"G{t_neto}"] = "Total Neto"
    ws[f"H{t_neto}"] = f'=IF(COUNT(B{primera}:B{ultima})=0,"",ROUND(SUM(H{primera}:H{ultima}),0))'
    ws[f"G{t_iva}"] = "IVA (19%)"
    ws[f"H{t_iva}"] = f'=IF(H{t_neto}="","",ROUND(H{t_neto}*{IVA},0))'
    ws[f"G{t_total}"] = "Total"
    ws[f"H{t_total}"] = f'=IF(H{t_neto}="","",H{t_neto}+H{t_iva})'
    for r in range(t_neto, t_total + 1):
        for col in (7, 8):
            celda = ws.cell(r, col)
            celda.font = Font(name=fuente_base, size=10, bold=(r == t_total), color=azul if r == t_total else texto)
            celda.alignment = Alignment(horizontal="right", vertical="center")
            celda.number_format = '"$"#,##0;[Red]-"$"#,##0'
        ws.row_dimensions[r].height = 21
    for col in (7, 8):
        ws.cell(t_neto, col).border = borde_fuerte
    for col in (7, 8):
        ws.cell(t_total, col).fill = relleno_seccion
        ws.cell(t_total, col).border = Border(top=Side(style="thin", color=azul))

    pie = t_total + 2
    ws.merge_cells(start_row=pie, start_column=3, end_row=pie, end_column=8)
    ws.cell(pie, 3, "Gracias por preferir Gioval.")
    ws.cell(pie, 3).font = Font(name=fuente_base, size=9, italic=True, color=gris)
    ws.cell(pie, 3).alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[pie].height = 20

    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.page_margins.left, ws.page_margins.right = 0.45, 0.45
    ws.page_margins.top, ws.page_margins.bottom = 0.5, 0.5
    ws.print_options.horizontalCentered = True
    ws.print_area = f"B1:H{pie}"
    ws.print_title_rows = f"{encabezado}:{encabezado}"
    ws.oddFooter.center.text = "Gioval · Cotización"
    ws.oddFooter.right.text = "Página &[Page] de &[Pages]"
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.calculation.calcMode = "auto"
    wb.save(salida)
    return Path(salida)
