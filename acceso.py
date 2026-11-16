"""Pantallas de acceso por perfil; se ejecutan antes de abrir los datos comerciales."""
import os
import logging
from functools import wraps
from pathlib import Path

import streamlit as st
import identidad_visual as visual
import sesion_web
from correo_seguridad import CorreoNoConfigurado, enviar_codigo_recuperacion, enviar_codigo_seguridad, smtp_configurado
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
    token = st.session_state.get("_gioval_token")
    cookie = st.context.cookies.get(sesion_web.COOKIE) if sesion_web.activa else None
    identidad = db.identidad(token or cookie)
    if not identidad:
        aviso = st.session_state.get("_acceso_aviso")
        # Una sesión expirada nunca conserva datos comerciales de otro perfil.
        if st.session_state.get("_gioval_token"):
            st.session_state.clear()
        visual.marca()
        izquierda, derecha = st.columns([1.35, 1], gap="large", vertical_alignment="center")
        with izquierda:
            visual.hero("Tu operación comercial,<br><span>conectada.</span>",
                        "Un espacio para cotizar, organizar productos y revisar la rentabilidad de cada oportunidad.", compacto=True)
        with derecha:
            with st.container(border=True, key="gv_login"):
                st.badge("Acceso del equipo", icon=":material/lock:", color="blue")
                st.subheader("Bienvenido a Giovalia")
                st.caption("Ingresa para continuar con tus productos y cotizaciones.")
                if aviso:
                    st.success(aviso)
                with st.form("acceso_perfiles", clear_on_submit=True):
                    usuario = st.text_input("Usuario o correo", key="acceso_usuario", placeholder="Tu usuario o correo")
                    clave = st.text_input("Contraseña", type="password", key="acceso_clave", placeholder="Tu contraseña")
                    enviar = st.form_submit_button("Entrar a mi espacio", type="primary", key="acceso_ingresar", width="stretch")
                if os.getenv("GIOVAL_ALLOW_SIGNUP", "").strip().lower() == "true":
                    with st.expander("Crear cuenta"):
                        st.caption("Las cuentas nuevas tienen acceso de trabajador. Verifica tu correo para activarlas.")
                        with st.form("registro_nueva_cuenta", clear_on_submit=True):
                            nombre_registro = st.text_input("Nombre", key="registro_nombre")
                            correo_registro = st.text_input("Correo", key="registro_correo")
                            clave_registro = st.text_input("Contraseña (mínimo 12 caracteres, letras y números)",
                                                           type="password", key="registro_clave")
                            repetir_registro = st.text_input("Repetir contraseña", type="password", key="registro_repetir")
                            crear_cuenta = st.form_submit_button("Crear cuenta", key="registro_crear")
                        if crear_cuenta:
                            if not smtp_configurado():
                                st.error("El registro por correo aún no está configurado. Contacta al administrador de Giovalia.")
                            else:
                                try:
                                    if clave_registro != repetir_registro:
                                        raise ValueError("Las contraseñas no coinciden.")
                                    solicitud = db.registrar_cuenta(nombre_registro, correo_registro, clave_registro)
                                except ValueError as exc:
                                    st.error(str(exc))
                                else:
                                    if solicitud:
                                        destino, codigo = solicitud
                                        try:
                                            enviar_codigo_seguridad(destino, codigo, verificacion=True)
                                        except Exception:
                                            logging.exception("Falló el envío de un código de activación de cuenta.")
                                            db.cancelar_codigo(codigo)
                                            db.cancelar_registro(destino)
                                    st.success("Si el correo está disponible, recibirás un código para verificar y activar la cuenta.")
                with st.expander("Verificar correo"):
                    st.caption("Pega aquí el código recibido al crear una cuenta o al verificar un correo asociado.")
                    with st.form("verificar_correo_acceso", clear_on_submit=True):
                        codigo_verificacion = st.text_input("Código de verificación", key="verificar_correo_acceso_codigo")
                        confirmar_correo = st.form_submit_button("Verificar correo", key="verificar_correo_acceso_enviar")
                    if confirmar_correo:
                        try:
                            db.verificar_correo(codigo_verificacion)
                            st.session_state["_acceso_aviso"] = "Correo verificado. Ya puedes iniciar sesión con tu correo y contraseña."
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                    with st.form("reenviar_codigo_alta", clear_on_submit=True):
                        correo_alta = st.text_input("Correo usado para crear la cuenta", key="reenviar_alta_correo")
                        reenviar_alta = st.form_submit_button("Reenviar código de activación", key="reenviar_alta_enviar")
                    if reenviar_alta:
                        if not smtp_configurado():
                            st.error("El envío de correo aún no está configurado. Contacta al administrador de Giovalia.")
                        else:
                            try:
                                solicitud_alta = db.reenviar_codigo_alta(correo_alta)
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                if solicitud_alta:
                                    destino_alta, codigo_alta = solicitud_alta
                                    try:
                                        enviar_codigo_seguridad(destino_alta, codigo_alta, verificacion=True)
                                    except Exception:
                                        logging.exception("Falló el reenvío de un código de activación.")
                                        db.cancelar_codigo(codigo_alta)
                                st.success("Si hay una cuenta pendiente para ese correo, recibirás un nuevo código.")
                with st.expander("¿Olvidaste tu contraseña?"):
                    st.caption("Solicita un código en el correo asociado a tu cuenta. Vence en 30 minutos.")
                    with st.form("solicitar_recuperacion", clear_on_submit=True):
                        correo = st.text_input("Correo asociado", key="recuperar_correo")
                        solicitar = st.form_submit_button("Enviar código", key="recuperar_solicitar")
                    if solicitar:
                        if not smtp_configurado():
                            st.error("La recuperación por correo aún no está configurada. Contacta al administrador de Giovalia.")
                        else:
                            try:
                                solicitud = db.solicitar_restauracion(correo)
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                if solicitud:
                                    destino, codigo = solicitud
                                    try:
                                        enviar_codigo_recuperacion(destino, codigo)
                                    except Exception:
                                        logging.exception("Falló el envío de un código de recuperación.")
                                        db.cancelar_codigo(codigo)
                                st.success("Si la dirección está asociada a una cuenta, recibirás un código de recuperación.")
                    with st.form("completar_recuperacion", clear_on_submit=True):
                        codigo = st.text_input("Código recibido por correo", key="recuperar_codigo")
                        nueva = st.text_input("Nueva contraseña (mínimo 12 caracteres, letras y números)", type="password", key="recuperar_nueva")
                        repetir = st.text_input("Repetir nueva contraseña", type="password", key="recuperar_repetir")
                        restablecer = st.form_submit_button("Restablecer contraseña", key="recuperar_completar")
                    if restablecer:
                        try:
                            if nueva != repetir:
                                raise ValueError("Las contraseñas nuevas no coinciden.")
                            db.restaurar_con_codigo(codigo, nueva)
                            st.session_state["_acceso_aviso"] = "Contraseña restablecida. Ya puedes iniciar sesión."
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                st.caption("Tu biblioteca y tus cotizaciones, en el mismo lugar.")
        if enviar:
            with st.spinner("Abriendo tu espacio…"):
                token = db.entrar(usuario, clave)
            if token:
                st.session_state["_gioval_token"] = token
                st.session_state["_gioval_confirmar_cookie"] = True
                st.session_state["_ux_notice"] = "Bienvenido a tu espacio comercial."
                st.rerun()
            st.error("No pude iniciar sesión. Revisa los datos o espera 15 minutos si hubo varios intentos fallidos.")
        st.stop()
    st.session_state["_gioval_identidad"] = identidad
    token = st.session_state["_gioval_token"] = token or cookie
    origen = st.context.headers.get("Origin") if sesion_web.activa else None
    if origen and token != cookie and st.session_state.pop("_gioval_confirmar_cookie", False):
        st.button("Reintentar", key="acceso_reintentar",
                  on_click=lambda: st.session_state.update(_gioval_confirmar_cookie=True))
        st.html(sesion_web.script_confirmar(sesion_web.preparar(token, origen)), unsafe_allow_javascript=True)
        st.stop()
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
        propia = next((u for u in db.listar() if u["usuario"] == identidad["usuario"]), None)
        if propia and propia.get("correo"):
            estado_correo = "verificado" if propia.get("correo_verificado") else "pendiente de verificación"
            st.caption(f"Correo de recuperación: {propia['correo']} · {estado_correo}")
        if propia and propia.get("correo") and not propia.get("correo_verificado"):
            st.caption("El correo asociado aún no está verificado; no se podrá usar para recuperar la contraseña.")
            with st.form("verificar_mi_correo", clear_on_submit=True):
                codigo = st.text_input("Código de verificación enviado a tu correo", key="verificar_mi_correo_codigo")
                verificar = st.form_submit_button("Verificar mi correo", key="verificar_mi_correo_enviar")
            if verificar:
                try:
                    db.verificar_correo(codigo)
                    st.success("Correo verificado. Ya puedes usarlo para recuperar tu contraseña.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    if identidad["rol"] == "admin":
        with st.expander("Administrar cuentas y correos de recuperación"):
            st.caption("Asocia un correo individual a cada cuenta. El código de recuperación solo se envía a ese correo.")
            for cuenta in db.listar():
                objetivo = cuenta["usuario"]
                propio = objetivo == identidad["usuario"]
                st.markdown(f"**{cuenta['nombre']}** · `{objetivo}`")
                with st.form(f"administrar_{objetivo}", clear_on_submit=True):
                    correo = st.text_input("Correo asociado", value=cuenta.get("correo", ""), key=f"correo_{objetivo}")
                    if propio:
                        enviar = st.form_submit_button("Guardar correo", key=f"guardar_correo_{objetivo}")
                        activo = None
                        clave = None
                    else:
                        activo = st.checkbox("Cuenta habilitada", value=bool(cuenta["activo"]), key=f"activo_{objetivo}")
                        clave = st.text_input("Nueva contraseña temporal (opcional)", type="password", key=f"clave_admin_{objetivo}")
                        enviar = st.form_submit_button("Guardar cuenta y correo", key=f"guardar_cuenta_{objetivo}")
                if enviar:
                    try:
                        if propio:
                            db.asociar_correo(st.session_state["_gioval_token"], objetivo, correo)
                        else:
                            db.administrar(st.session_state["_gioval_token"], objetivo, clave=clave or None,
                                           activo=activo, correo=correo)
                        st.session_state["_cuenta_actualizada"] = f"Cuenta de {cuenta['nombre']} actualizada."
                        st.rerun()
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
                actualizado = st.session_state.pop("_cuenta_actualizada", None)
                if actualizado:
                    st.success(actualizado)
                if cuenta.get("correo"):
                    if cuenta.get("correo_verificado"):
                        st.caption("Correo verificado para recuperación.")
                    else:
                        st.caption("Correo pendiente de verificación.")
                        if st.button("Enviar código de verificación", key=f"enviar_verificacion_{objetivo}"):
                            codigo = None
                            try:
                                if not smtp_configurado():
                                    raise CorreoNoConfigurado("SMTP no configurado")
                                destino, codigo = db.emitir_verificacion_correo(
                                    st.session_state["_gioval_token"], objetivo)
                                enviar_codigo_seguridad(destino, codigo, verificacion=True)
                                st.success(f"Código enviado a {destino}. La persona debe ingresarlo en «Mi cuenta».")
                            except CorreoNoConfigurado:
                                if codigo:
                                    db.cancelar_codigo(codigo)
                                st.error("Configura el servicio de correo del servidor antes de verificar cuentas.")
                            except Exception:
                                logging.exception("Falló el envío de un código de verificación.")
                                if codigo:
                                    db.cancelar_codigo(codigo)
                                st.error("No se pudo enviar el código. Inténtalo más tarde.")
