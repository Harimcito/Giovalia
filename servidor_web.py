"""Entrada HTTP del cotizador de escritorio."""
from hashlib import sha256
from pathlib import Path

import streamlit as st
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import sesion_web

sesion_web.activa = True


ARCHIVOS_VERSION = ("app.py", "giovalia.css", "interfaz_giovalia.css",
                    "experiencia_giovalia.css", ".streamlit/config.toml")
RAIZ = Path(__file__).resolve().parent


def huella_version():
    hashes = [sha256((RAIZ / archivo).read_bytes()).hexdigest() for archivo in ARCHIVOS_VERSION]
    return "giovalia-" + sha256("".join(hashes).encode("ascii")).hexdigest()


VERSION = huella_version()


async def version_servidor(request):
    return PlainTextResponse(VERSION, headers={"Cache-Control": "no-store"})


app = st.App(str(Path(__file__).with_name("app.py")),
             routes=[Route(sesion_web.RUTA, sesion_web.confirmar, methods=["POST"]),
                     Route("/api/giovalia/version", version_servidor)])
