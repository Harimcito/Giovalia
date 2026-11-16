"""Entrega de códigos de recuperación por SMTP, configurado fuera del código."""
from __future__ import annotations

from email.message import EmailMessage
import os
import smtplib
import ssl


class CorreoNoConfigurado(RuntimeError):
    pass


def smtp_configurado() -> bool:
    try:
        int(os.environ.get("GIOVAL_SMTP_PORT", "587"))
    except ValueError:
        return False
    return bool(
        os.environ.get("GIOVAL_SMTP_HOST", "").strip()
        and os.environ.get("GIOVAL_SMTP_USER", "").strip()
        and os.environ.get("GIOVAL_SMTP_PASSWORD", "")
        and os.environ.get("GIOVAL_SMTP_FROM", os.environ.get("GIOVAL_SMTP_USER", "")).strip()
        and os.environ.get("GIOVAL_SMTP_SECURITY", "starttls").strip().lower() in {"starttls", "ssl"}
    )


def enviar_codigo_seguridad(destino: str, codigo: str, *, verificacion: bool = False) -> None:
    host = os.environ.get("GIOVAL_SMTP_HOST", "").strip()
    usuario = os.environ.get("GIOVAL_SMTP_USER", "").strip()
    clave = os.environ.get("GIOVAL_SMTP_PASSWORD", "")
    remitente = os.environ.get("GIOVAL_SMTP_FROM", usuario).strip()
    seguridad = os.environ.get("GIOVAL_SMTP_SECURITY", "starttls").strip().lower()
    try:
        puerto = int(os.environ.get("GIOVAL_SMTP_PORT", "587"))
    except ValueError as exc:
        raise CorreoNoConfigurado("GIOVAL_SMTP_PORT debe ser un número.") from exc
    if not host or not usuario or not clave or not remitente or seguridad not in {"starttls", "ssl"}:
        raise CorreoNoConfigurado("Configura las variables GIOVAL_SMTP_* del servidor.")

    mensaje = EmailMessage()
    mensaje["Subject"] = ("Verifica tu correo para Giovalia" if verificacion
                          else "Código para restablecer tu contraseña de Giovalia")
    mensaje["From"] = remitente
    mensaje["To"] = destino
    if verificacion:
        texto = (
            "Usa este código para verificar tu dirección de correo en Giovalia:\n\n"
            f"{codigo}\n\n"
            "Si estás creando una cuenta, pega el código en «Verificar correo» en la pantalla de acceso; "
            "si estás verificando una cuenta existente, ingrésalo en «Mi cuenta». Vence en 30 minutos y "
            "solo puede usarse una vez. "
            "Si no solicitaste el código, ignora este mensaje."
        )
    else:
        texto = (
            "Recibimos una solicitud para restablecer la contraseña de tu cuenta de Giovalia.\n\n"
            f"Tu código de recuperación es:\n{codigo}\n\n"
            "Pégalo en la pantalla de inicio de sesión para elegir una contraseña nueva. "
            "El código vence en 30 minutos y solo puede usarse una vez. Si no solicitaste "
            "este cambio, ignora este mensaje."
        )
    mensaje.set_content(texto)

    contexto = ssl.create_default_context()
    if seguridad == "ssl":
        with smtplib.SMTP_SSL(host, puerto, timeout=15, context=contexto) as servidor:
            servidor.login(usuario, clave)
            servidor.send_message(mensaje)
    else:
        with smtplib.SMTP(host, puerto, timeout=15) as servidor:
            servidor.ehlo()
            servidor.starttls(context=contexto)
            servidor.ehlo()
            servidor.login(usuario, clave)
            servidor.send_message(mensaje)


def enviar_codigo_recuperacion(destino: str, codigo: str) -> None:
    enviar_codigo_seguridad(destino, codigo)
