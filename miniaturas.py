"""Miniaturas locales, sin publicar archivos ni alterar los datos editados."""
import base64
from functools import lru_cache
import io
import unicodedata
from pathlib import Path
import pandas as pd
import streamlit as st
from PIL import Image, ImageOps

COLUMNA = "Imagen"

def normalizar(texto):
    return ' '.join(unicodedata.normalize('NFKC', str(texto or '')).casefold().split())

@lru_cache(maxsize=512)
def _cargar(ruta, fecha, tamano):
    try:
        with Image.open(ruta) as original:
            imagen = ImageOps.exif_transpose(original).convert('RGBA')
            imagen.thumbnail((160, 160))
            lienzo = Image.new('RGBA', (160, 160), 'white')
            lienzo.alpha_composite(imagen, ((160-imagen.width)//2, (160-imagen.height)//2))
            buffer = io.BytesIO()
            lienzo.convert('RGB').save(buffer, format='PNG', optimize=True)
        return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    except (OSError, ValueError, Image.DecompressionBombError):
        return None

def miniatura(bib, clave):
    if not clave or clave not in bib.indice:
        return None
    try:
        foto, _ = bib.archivos(clave)
        if foto:
            ruta = Path(foto)
            datos = ruta.stat()
            return _cargar(str(ruta.resolve()), datos.st_mtime_ns, datos.st_size)
    except (OSError, ValueError):
        pass
    return None

def preparar(datos, bib, opciones=None, lineas=()):
    df = datos.copy() if isinstance(datos, pd.DataFrame) else pd.DataFrame(datos)
    kw = dict(opciones or {})
    if 'Producto' not in df.columns:
        return df, kw
    identidades = {}
    for clave, ficha in bib.indice.items():
        aliases = ficha.get('aliases') or []
        if isinstance(aliases, str):
            aliases = [aliases]
        for nombre in [ficha.get('nombre'), *aliases]:
            if nombre:
                identidades.setdefault(normalizar(nombre), set()).add(clave)
    vinculos = {}
    for linea in lineas:
        if linea.get('clave_auto') in bib.indice:
            vinculos.setdefault(normalizar(linea.get('descripcion')), set()).add(linea['clave_auto'])
    imagenes = []
    for _, fila in df.iterrows():
        clave = next((str(fila[c]) for c in ('Código', 'Codigo', 'clave') if c in df.columns and str(fila[c]) in bib.indice), None)
        if clave is None:
            nombre = normalizar(fila.get('Producto'))
            candidatos = vinculos.get(nombre) or identidades.get(nombre, set())
            clave = next(iter(candidatos)) if len(candidatos) == 1 else None
        imagenes.append(miniatura(bib, clave))
    df.insert(0, COLUMNA, imagenes)
    config = dict(kw.get('column_config') or {})
    config[COLUMNA] = st.column_config.ImageColumn('Foto', width=76, pinned=True, help='Foto asociada al producto. Doble clic para ampliar. Vacío: sin foto disponible.')
    kw['column_config'] = config
    kw['row_height'] = 64
    if 'column_order' in kw:
        kw['column_order'] = [COLUMNA, *kw['column_order']]
    return df, kw
