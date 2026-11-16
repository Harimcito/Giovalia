"""Navegación, listas y vista previa de cotizaciones."""
from io import BytesIO
from pathlib import Path

import streamlit as st
from pypdf import PdfReader, PdfWriter


def lista(titulo, columnas, filas, *, key, activa=None, montos=(), compacta=False,
          fijar=False, proteger=False, memoria=None):
    render = st.components.v2.component(
        "giovalia_lista", isolate_styles=False,
        html='<div class="gv-lista" tabindex="0" role="region" aria-label="Lista desplazable"></div>',
        js=Path(__file__).with_name("interfaz_listas.js").read_text(encoding="utf-8"),
    )
    resultado = render(
        key=key, on_fila_change=lambda: None,
        data=dict(titulo=titulo, columnas=list(columnas), filas=filas, activa=activa,
                  montos=list(montos), compacta=compacta, fijar=fijar, proteger=proteger, memoria=memoria),
    )
    return resultado.fila


def fragmento_pdf(pdf, inicio, fin):
    lector = PdfReader(BytesIO(pdf))
    escritor = PdfWriter()
    for pagina in lector.pages[inicio:fin]:
        escritor.add_page(pagina)
    salida = BytesIO()
    escritor.write(salida)
    return salida.getvalue()
