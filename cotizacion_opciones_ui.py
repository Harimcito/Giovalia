"""Controles de compatibilidad y filtros guardados."""
import json

import streamlit as st

import cotizacion_opciones as opciones
from stock_catalogo import datos_stock
from ui_montos import clp


def buscar_impresora(bib, seccion):
    with st.expander("Buscar por modelo de impresora", icon=":material/print:"):
        consulta = st.text_input("Modelo de impresora", placeholder="Ej.: Brother HL-1212W",
                                 key=f"impresora_{seccion}", max_chars=100)
        if not consulta.strip():
            return None
        encontrados = opciones.buscar_compatibles(consulta, bib.indice)
        if not encontrados:
            st.info("No hay coincidencias en la compatibilidad verificada de la biblioteca.")
            return None
        etiquetas = {}
        for codigo, tipo, ficha in encontrados:
            precio = ficha.get("precio_neto")
            stock = datos_stock(ficha)
            disponibilidad = stock["estado_stock"] if stock["stock_confirmado_el"] else "Stock por confirmar"
            valor = f"{clp(precio)} neto" if precio is not None else "Sin precio"
            etiquetas[codigo] = f"{tipo} · {ficha.get('nombre') or codigo} · {codigo} · {valor} · {disponibilidad}"
        elegido = st.selectbox("Insumos compatibles", etiquetas, format_func=etiquetas.__getitem__,
                              key=f"compatible_{seccion}")
        accion = "Agregar a la cotización" if seccion == "cotizacion" else "Abrir ficha del producto"
        if st.button(accion, key=f"usar_compatible_{seccion}", icon=":material/arrow_forward:"):
            return elegido
    return None


VISTAS_INICIALES = {
    "Borradores recientes": ("", "Borrador", "Últimos 30 días", False),
    "Vencen hoy": ("", "Todos", "Todas las fechas", True),
}


def cargar_vistas(hist, cuenta):
    clave = f"vistas_cotizaciones:{cuenta or 'local'}"
    original = hist.config(clave)
    try:
        datos = json.loads(original) if original else {}
    except (ValueError, TypeError):
        datos = {}
    datos = datos if isinstance(datos, dict) else {}
    return clave, original, {**VISTAS_INICIALES, **datos}
