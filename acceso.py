"""Pantallas de acceso por perfil; se ejecutan antes de abrir los datos comerciales."""
import os
from functools import wraps
from pathlib import Path

import streamlit as st
from usuarios import Usuarios


def perfiles_activos():
    return os.environ.get("GIOVAL_PROFILES", "").lower() in {"1", "true", "yes", "on"}


def proteger(funcion):
    """Los callbacks corren antes del script: validar también en ese punto."""
    @wraps(funcion)
    def ejecutar(*args, **kwargs):
        if perfiles_activos():
            identidad = almacen().identidad(st.session_state.get("_gioval_token"))
            if not identidad or identidad["cambiar"]:
                st.session_state.clear()
                st.rerun()
        return funcion(*args, **kwargs)
    return ejecutar


def almacen():
    archivo = os.environ.get("GIOVAL_USERS_DB")
    if not archivo:
        st.error("El servidor no tiene configurada la base de usuarios. Contacta a Harim.")
        st.stop()
    if not Path(archivo).is_file():
        st.error("Falta preparar los perfiles en este computador. Contacta a Harim.")
        st.stop()
    return Usuarios(archivo)


def cerrar_sesion():
    almacen().salir(st.session_state.get("_gioval_token"))
    st.session_state.clear()
    st.rerun()


def formulario_clave(db, obligatorio=False):
    with st.form("cambiar_mi_clave", clear_on_submit=True):
        actual = st.text_input("Contraseña actual", type="password", key="clave_actual")
        nueva = st.text_input("Nueva contraseña (mínimo 6 caracteres, letras y números)", type="password", key="clave_nueva")
        repetir = st.text_input("Repetir nueva contraseña", type="password", key="clave_repetida")
        enviar = st.form_submit_button("Guardar mi contraseña", key="guardar_mi_clave")
    if enviar:
        try:
            if nueva != repetir:
                raise ValueError("Las contraseñas nuevas no coinciden.")
            db.cambiar_clave(st.session_state["_gioval_token"], actual, nueva)
            st.session_state.clear()
            st.session_state["_acceso_aviso"] = "Contraseña cambiada. Ingresa con la nueva."
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
    if obligatorio:
        st.button("Salir", on_click=cerrar_sesion, key="salir_cambio_clave")


def exigir_perfil():
    db = almacen()
    identidad = db.identidad(st.session_state.get("_gioval_token"))
    if not identidad:
        aviso = st.session_state.get("_acceso_aviso")
        # Una sesión expirada nunca conserva datos comerciales de otro perfil.
        if st.session_state.get("_gioval_token"):
            st.session_state.clear()
        st.title("GiovalIA")
        st.caption("Inventario y cotizaciones compartidos · acceso del equipo")
        if aviso:
            st.success(aviso)
        with st.form("acceso_perfiles", clear_on_submit=True):
            usuario = st.text_input("Usuario", key="acceso_usuario")
            clave = st.text_input("Contraseña", type="password", key="acceso_clave")
            enviar = st.form_submit_button("Ingresar", type="primary", key="acceso_ingresar")
        if enviar:
            token = db.entrar(usuario, clave)
            if token:
                st.session_state["_gioval_token"] = token
                st.rerun()
            st.error("No pude iniciar sesión. Revisa los datos o espera 15 minutos si hubo varios intentos fallidos.")
        st.stop()
    st.session_state["_gioval_identidad"] = identidad
    if identidad["cambiar"]:
        st.title(f"Hola, {identidad['nombre']}")
        st.info("Cambia tu contraseña temporal antes de abrir la biblioteca compartida.")
        formulario_clave(db, obligatorio=True)
        st.stop()
    return identidad


def panel_cuenta():
    db = almacen()
    identidad = db.identidad(st.session_state.get("_gioval_token"))
    if not identidad:
        st.stop()
    with st.expander("Mi cuenta y contraseña"):
        st.caption(f"Sesión de {identidad['nombre']}")
        formulario_clave(db)
    if identidad["rol"] == "admin":
        with st.expander("Administrar cuenta de Giovanna"):
            filas = {u["usuario"]: u for u in db.listar()}
            cuenta = filas.get("giovanna")
            if cuenta:
                with st.form("administrar_giovanna", clear_on_submit=True):
                    activo = st.checkbox("Cuenta habilitada", value=bool(cuenta["activo"]))
                    clave = st.text_input("Nueva contraseña temporal (opcional)", type="password")
                    enviar = st.form_submit_button("Guardar cuenta de Giovanna")
                if enviar:
                    try:
                        db.administrar(st.session_state["_gioval_token"], "giovanna", clave=clave or None, activo=activo)
                        st.success("Cuenta actualizada. Sus sesiones anteriores se cerraron.")
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
