"""Cliente de escritorio de Gioval.

La ventana abre el servidor central configurado por el administrador. Puede
conectarse a una URL HTTPS (acceso remoto) o a HTTP dentro de una red local
privada. El cliente no guarda la biblioteca comercial en este computador.
"""
from __future__ import annotations

import ctypes
import ipaddress
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen


APP_TITLE = "Gioval · Cotizador comercial"
# No se fija una URL de alojamiento: el usuario configura el servidor real.


def data_dir() -> Path:
    """Directorio local exclusivo para preferencias del cliente, no datos comerciales."""
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (local_app_data / "Gioval").resolve()


def show_message(title: str, message: str, error: bool = True) -> None:
    """Muestra avisos nativos sin requerir una consola de Windows."""
    if os.name == "nt":
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    else:
        print(f"{title}\n\n{message}", file=sys.stderr if error else sys.stdout)


def configured_url() -> str:
    """Lee la URL desde una variable de entorno o desde desktop.json."""
    value = os.environ.get("GIOVAL_CLOUD_URL", "").strip()
    if not value:
        config = data_dir() / "desktop.json"
        try:
            stored = json.loads(config.read_text("utf-8"))
            value = str(stored.get("url", "")).strip()
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            value = ""
    return value.rstrip("/")


def is_private_http_host(hostname: str) -> bool:
    """Permite HTTP solo para loopback o equipos de una red privada."""
    host = (hostname or "").strip().lower()
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        private_ranges = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        return any(address in network for network in private_ranges)
    return address in ipaddress.ip_network("fc00::/7")


def validate_url(value: str) -> str:
    """Exige HTTPS para internet y permite HTTP únicamente para redes privadas."""
    parsed = urlparse(value)
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("La dirección central de Gioval no es válida.")
    if parsed.scheme == "https":
        return value
    if parsed.scheme == "http" and is_private_http_host(parsed.hostname):
        return value
    raise ValueError(
        "Usa una dirección HTTPS para acceso por internet. HTTP se permite "
        "solo en localhost o una dirección de red privada (por ejemplo, "
        "http://192.168.1.20:8501)."
    )


def cloud_url() -> str:
    """Devuelve la URL configurada o explica cómo configurarla."""
    value = configured_url()
    if not value:
        raise ValueError(
            "Todavía no configuraste el servidor central. Abre en el menú Inicio "
            "de Windows «Configurar conexión de Gioval» y escribe la dirección "
            "que te entregue el administrador."
        )
    return validate_url(value)


def check_package() -> None:
    """Verifica pywebview/WebView2 sin exigir que ya se haya configurado un servidor."""
    from importlib import import_module
    import webview

    if not callable(webview.create_window) or not callable(webview.start):
        raise RuntimeError("El paquete de escritorio no contiene las funciones de ventana necesarias.")
    # Carga explícitamente el backend Windows para que PyInstaller no deje fuera
    # módulos que pywebview importa de forma dinámica.
    import_module("webview.platforms.edgechromium")
    value = configured_url()
    if value:
        validate_url(value)


def wait_until_ready(url: str, timeout: float = 120.0) -> bool:
    """Espera a que el servidor central responda antes de abrir la ventana."""
    health_url = f"{url}/_stcore/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(health_url, timeout=5) as response:
                if response.status == 200:
                    return True
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(1.5)
    return False


def launch_desktop() -> None:
    try:
        url = cloud_url()
    except Exception as exc:
        show_message(APP_TITLE, str(exc))
        return

    if not wait_until_ready(url):
        show_message(
            APP_TITLE,
            "No pude conectar con la biblioteca compartida de Gioval.\n\n"
            f"Servidor: {url}\n\n"
            "Comprueba que el computador central esté encendido y que el servidor "
            "Gioval esté ejecutándose. Si estás fuera de la oficina, comprueba "
            "también la conexión a internet.",
        )
        return

    try:
        import webview

        # Streamlit serves generated PDFs through a browser download link. pywebview
        # disables downloads by default, so enable them for the desktop client.
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window(
            APP_TITLE,
            url,
            width=1440,
            height=900,
            min_size=(1000, 680),
            background_color="#0A0B10",
            text_select=True,
        )
        webview.start(gui="edgechromium", debug=False)
    except Exception as exc:
        show_message(
            APP_TITLE,
            "No pude abrir la ventana de escritorio.\n"
            "Comprueba que Microsoft Edge WebView2 Runtime esté instalado.\n\n"
            f"Detalle: {exc}",
        )


if __name__ == "__main__":
    if "--check-package" in sys.argv:
        check_package()
        raise SystemExit(0)
    launch_desktop()
