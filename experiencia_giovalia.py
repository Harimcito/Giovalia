"""Preferencias y presentación para las tareas diarias de cotización."""
from pathlib import Path

import streamlit as st

TAMANOS = ("Normal", "Grande", "Muy grande")


def normalizar_preferencias(datos):
    datos = datos if isinstance(datos, dict) else {}
    texto = datos.get("texto")
    return {
        "texto": texto if texto in TAMANOS else TAMANOS[0],
        "contraste": datos.get("contraste") is True,
        "mostrador": datos.get("mostrador") is True,
    }


def preferencias(estado, identidad, acceso):
    if "_preferencias_ux" not in estado:
        guardadas = acceso.almacen().leer_preferencias_ux(identidad["usuario"]) if identidad else None
        estado["_preferencias_ux"] = normalizar_preferencias(guardadas)
    actual = estado["_preferencias_ux"]

    def guardar():
        datos = normalizar_preferencias({
            "texto": estado["ux_texto"], "contraste": estado["ux_contraste"],
            "mostrador": estado["ux_mostrador"],
        })
        if identidad:
            acceso.almacen().guardar_preferencias_ux(identidad["usuario"], datos)
        estado["_preferencias_ux"] = datos

    with st.popover("Vista y lectura", icon=":material/visibility:"):
        st.selectbox("Tamaño del texto", TAMANOS, index=TAMANOS.index(actual["texto"]),
                     key="ux_texto", on_change=guardar)
        st.toggle("Alto contraste", value=actual["contraste"], key="ux_contraste", on_change=guardar)
        st.toggle("Modo mostrador", value=actual["mostrador"], key="ux_mostrador", on_change=guardar)
    escala = TAMANOS.index(actual["texto"])
    st.html(f'<span id="gv-preferencias" hidden data-escala="{escala}" '
            f'data-contraste="{int(actual["contraste"])}"></span>')
    st.html(Path(__file__).with_name("experiencia_giovalia.css"))
    return actual


def ayuda(titulo, ejemplo):
    with st.popover(titulo, icon=":material/help:"):
        st.write(ejemplo)


@st.dialog("Vista ampliada del anexo", width="large")
def mostrar_anexo(ruta, nombre, formato):
    st.subheader(nombre)
    try:
        if formato == "foto":
            st.image(str(ruta), width="stretch", alt=nombre)
        else:
            archivo = ruta.read_bytes()
            st.pdf(archivo, height=720, alt=nombre)
            st.download_button("Descargar ficha", archivo, file_name=f"{nombre}.pdf",
                               mime="application/pdf", on_click="ignore")
    except (OSError, ValueError, st.errors.StreamlitAPIException):
        st.warning("No se pudo abrir el anexo. Puedes reemplazar el archivo desde Más opciones.")
