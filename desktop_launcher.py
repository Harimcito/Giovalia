"""Lanzador de escritorio para Giovalia.

En Windows abre Streamlit dentro de una ventana WebView2; no abre una pestaña
del navegador. El mismo ejecutable se relanza en modo servidor como proceso hijo.
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


APP_TITLE = "Gioval · Cotizador comercial"
SERVER_FLAG = "--gioval-server"
PORT_FLAG = "--port"


def resource_dir() -> Path:
    """Directorio de recursos empacados por PyInstaller o del proyecto en desarrollo."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """Datos escribibles por el usuario, separados de la instalación."""
    override = os.environ.get("GIOVAL_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (local_app_data / "Gioval").resolve()


def show_message(title: str, message: str, error: bool = True) -> None:
    """Muestra errores sin requerir una consola de Windows."""
    if os.name == "nt":
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    else:
        print(f"{title}\n\n{message}", file=sys.stderr if error else sys.stdout)


def choose_port() -> int:
    """Pide a Windows un puerto TCP libre para evitar conflictos con otros programas."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def read_flag_value(flag: str, default: str = "") -> str:
    try:
        index = sys.argv.index(flag)
        return sys.argv[index + 1]
    except (ValueError, IndexError):
        return default


def run_streamlit_server() -> None:
    """Punto de entrada del proceso servidor, independiente de la ventana."""
    port_text = read_flag_value(PORT_FLAG)
    if not port_text.isdigit():
        raise RuntimeError("No se recibió un puerto válido para el servidor local.")

    app_script = resource_dir() / "app.py"
    if not app_script.is_file():
        raise FileNotFoundError(f"No encuentro app.py en el paquete: {app_script}")

    writable = data_dir()
    writable.mkdir(parents=True, exist_ok=True)
    os.environ["GIOVAL_DATA_DIR"] = str(writable)
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    os.chdir(writable)

    # El servidor escucha solo en localhost: no se expone a otros equipos.
    sys.argv = [
        "streamlit",
        "run",
        str(app_script),
        "--server.address=127.0.0.1",
        f"--server.port={int(port_text)}",
        "--server.headless=true",
        "--global.developmentMode=false",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
    ]
    from streamlit.web.cli import main as streamlit_main

    streamlit_main(prog_name="streamlit")


def wait_until_ready(port: int, process: subprocess.Popen, timeout: float = 90.0) -> bool:
    """Espera a que el servidor confirme que ya atiende solicitudes locales."""
    url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return True
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.3)
    return False


def stop_server(process: subprocess.Popen | None) -> None:
    """Cierra Streamlit cuando se cierra la ventana de escritorio."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def launch_desktop() -> None:
    if os.name != "nt":
        show_message(APP_TITLE, "El paquete de escritorio está preparado para Windows.", error=False)
        return

    writable = data_dir()
    logs_dir = writable / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        writable.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        show_message(APP_TITLE, f"No puedo preparar la carpeta de datos del usuario.\n\n{exc}")
        return

    app_script = resource_dir() / "app.py"
    if not app_script.is_file():
        show_message(APP_TITLE, f"No encuentro los archivos de Giovalia.\n\nRuta esperada:\n{app_script}")
        return

    port = choose_port()
    environment = os.environ.copy()
    environment["GIOVAL_DATA_DIR"] = str(writable)
    environment["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

    if getattr(sys, "frozen", False):
        command = [sys.executable, SERVER_FLAG, PORT_FLAG, str(port)]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), SERVER_FLAG, PORT_FLAG, str(port)]

    server_log_path = logs_dir / "server.log"
    process: subprocess.Popen | None = None
    try:
        with server_log_path.open("a", encoding="utf-8") as server_log:
            server_log.write(f"\n\n--- Inicio de Giovalia: {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            server_log.flush()
            process = subprocess.Popen(
                command,
                cwd=str(writable),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )

            if not wait_until_ready(port, process):
                try:
                    detail = "\n".join(server_log_path.read_text("utf-8", errors="replace").splitlines()[-12:])
                except OSError:
                    detail = ""
                stop_server(process)
                show_message(
                    APP_TITLE,
                    "Giovalia no pudo iniciar su servidor local.\n"
                    "Revisa el registro para ver el detalle:\n"
                    f"{server_log_path}\n\n{detail[-1800:]}",
                )
                return

            try:
                import webview

                webview.create_window(
                    APP_TITLE,
                    f"http://127.0.0.1:{port}",
                    width=1440,
                    height=900,
                    min_size=(1000, 680),
                    background_color="#0A0B10",
                    text_select=True,
                )
                # Windows utiliza WebView2/Chromium dentro de una ventana nativa.
                webview.start(gui="edgechromium", debug=False)
            except Exception as exc:
                stop_server(process)
                show_message(
                    APP_TITLE,
                    "No pude abrir la ventana de escritorio.\n"
                    "Comprueba que el instalador haya configurado Microsoft Edge WebView2 Runtime.\n\n"
                    f"Detalle: {exc}\n\nRegistro: {server_log_path}",
                )
    except Exception as exc:
        stop_server(process)
        show_message(APP_TITLE, f"No pude iniciar Giovalia.\n\nDetalle: {exc}")
    finally:
        stop_server(process)


if __name__ == "__main__":
    if SERVER_FLAG in sys.argv:
        run_streamlit_server()
    else:
        launch_desktop()
