"""Entrada HTTP del cotizador de escritorio."""
from pathlib import Path

import streamlit as st
from starlette.routing import Route

import sesion_web

sesion_web.activa = True
app = st.App(str(Path(__file__).with_name("app.py")),
             routes=[Route(sesion_web.RUTA, sesion_web.confirmar, methods=["POST"])])
