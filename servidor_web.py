"""Entrada HTTP del cotizador de escritorio."""
from pathlib import Path

import streamlit as st
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import sesion_web

sesion_web.activa = True


async def version_servidor(request):
    return PlainTextResponse("gestion-comercial-v1", headers={"Cache-Control": "no-store"})


app = st.App(str(Path(__file__).with_name("app.py")),
             routes=[Route(sesion_web.RUTA, sesion_web.confirmar, methods=["POST"]),
                     Route("/api/giovalia/version", version_servidor)])
