"""Cliente de escritorio de Giovalia.

La ventana se conecta a la aplicación central alojada en la nube. No inicia una
base de datos local: así todos los usuarios trabajan sobre la misma biblioteca.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen


APP_TITLE = "Gioval · Cotizador comercial"
DEFAULT_CLOUD_URL = "https://gioval-cotizador-compartido.onrender.com"


def data_dir() -> Path:
    """Directorio local exclusivo para preferencia de servidor; los datos comerciales son remotos."""
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (local_app_data / "Gioval").resolve()


def show_message(title: str, message: str, error: bool = True) -> None:
    """Muestra avisos nativos sin requerir una consola de Windows."""
    if os.name == "nt":
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    else:
        print(f"{title}\n\n{message}", file=sys.stderr if error else sys.stdout)


def cloud_url() -> str:
    """Lee la URL central, permitiendo sobreescribirla para desarrollo o cambios de dominio."""
    value = os.environ.get("GIOVAL_CLOUD_URL", "").strip()
    if not value:
        config = data_dir() / "desktop.json"
        try:
            stored = json.loads(config.read_text("utf-8"))
            value = str(stored.get("url", "")).strip()
        except (OSError, json.JSONDecodeError, AttributeError):
            value = ""
    value = (value or DEFAULT_CLOUD_URL).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("La dirección de Giovalia debe usar HTTPS.")
    if not parsed.netloc:
        raise ValueError("La dirección central de Giovalia no es válida.")
    return value


def check_package() -> None:
    """Verifica dependencias clave durante la compilación sin abrir una ventana."""
    import webview

    if not callable(webview.create_window) or not callable(webview.start):
        raise RuntimeError("El paquete de escritorio no contiene las funciones de ventana necesarias.")
    url = cloud_url()
    parsed = urlparse(url)
    if not parsed.hostname:
        raise RuntimeError("No se configuró el servidor central de Giovalia.")


def wait_until_ready(url: str, timeout: float = 120.0) -> bool:
    """Espera a que la aplicación central responda antes de abrir la ventana."""
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
        show_message(APP_TITLE, f"No pude leer la dirección del espacio compartido.\n\nDetalle: {exc}")
        return

    if not wait_until_ready(url):
        show_message(
            APP_TITLE,
            "No pude conectar con la biblioteca compartida de Gioval.\n\n"
            f"Servidor: {url}\n\n"
            "Comprueba tu conexión a internet y que el servicio central esté publicado. "
            "Los datos no se guardarán en este computador mientras el servidor no esté disponible.",
        )
        return

    try:
        import webview

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
