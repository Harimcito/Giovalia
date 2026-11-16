"""Entrega al navegador una sesión revocable mediante una cookie HttpOnly."""
import json
import secrets
import threading
import time

from starlette.responses import Response

from usuarios import DURACION_SESION

RUTA = "/api/giovalia/sesion"
COOKIE = "giovalia_sesion"
activa = False
_pendientes = {}
_lock = threading.Lock()


def preparar(token, origen):
    """Crea un comprobante de un solo uso sin enviar el token a JavaScript."""
    ahora = time.monotonic()
    clave = secrets.token_urlsafe(24)
    with _lock:
        for anterior, (_, _, vence) in tuple(_pendientes.items()):
            if vence <= ahora:
                _pendientes.pop(anterior)
        _pendientes[clave] = (token, origen, ahora + 45)
    return clave


async def confirmar(request):
    origen = request.headers.get("origin")
    if origen != f"{request.url.scheme}://{request.url.netloc}":
        return Response(status_code=403)
    clave = request.headers.get("x-giovalia-acceso", "")
    if len(clave) != 32:
        return Response(status_code=400)
    with _lock:
        registro = _pendientes.pop(clave, None)
    if not registro or registro[1] != origen or registro[2] <= time.monotonic():
        return Response(status_code=403)
    respuesta = Response(status_code=204, headers={"Cache-Control": "no-store"})
    respuesta.set_cookie(COOKIE, registro[0], max_age=DURACION_SESION,
                         httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return respuesta


def script_confirmar(clave):
    ruta = json.dumps(RUTA.lstrip("/"))
    comprobante = json.dumps(clave)
    return f"""<p id="gv-confirmando-sesion" role="status">Abriendo tu espacio…</p>
    <script>
      fetch(new URL({ruta}, window.location.href), {{
        method: "POST", credentials: "same-origin", headers: {{"X-Giovalia-Acceso": {comprobante}}}
      }}).then(respuesta => {{
        if (!respuesta.ok) throw new Error("Sesión pendiente");
        window.location.reload();
      }}).catch(() => {{
        const mensaje = document.getElementById("gv-confirmando-sesion");
        if (mensaje) mensaje.textContent = "No pude guardar la sesión en el navegador. Pulsa Reintentar.";
      }});
    </script>"""
