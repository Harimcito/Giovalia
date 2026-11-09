"""Supervisor único: reinicia GiovalIA y respalda a diario, sin actualizar en plena cotización."""
import json
import logging
import msvcrt
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from urllib.request import urlopen
from respaldo_servidor import respaldar


def main():
    base = Path(os.environ["LOCALAPPDATA"]) / "Gioval"
    logs = base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=logs / "servidor-principal.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    lock = (base / "servidor-principal.lock").open("a+b")
    lock.seek(0)
    if not lock.read(1):
        lock.write(b"1")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return  # Ya existe un supervisor para esta biblioteca.
    config = json.loads((base / "servidor-principal.json").read_text("utf-8"))
    env = dict(os.environ, GIOVAL_DATA_DIR=str(base / "Datos"), GIOVAL_PROFILES="true",
               GIOVAL_USERS_DB=str(base / "Acceso" / "usuarios.db"),
               GIOVAL_REQUIRE_ACCESS="false", GIOVAL_ALLOW_INITIAL_IMPORT="false", PYTHONUTF8="1")
    codigo = Path(__file__).resolve().parent
    procesos = {}
    fallos = {}
    ultimo_respaldo = ""
    estado_respaldo = base / "ultimo-respaldo-diario.txt"
    if estado_respaldo.exists():
        ultimo_respaldo = estado_respaldo.read_text().strip()
    logging.info("Supervisor iniciado; biblioteca=%s", base / "Datos" / "biblioteca")
    try:
        while True:
            for nombre, app, puerto in [("cotizador", codigo / "app.py", config.get("port", 8501)),
                                         ("radar", codigo / "radar_comercial" / "app.py", 8502)]:
                if nombre == "radar" and not config.get("radar", False):
                    continue
                proc = procesos.get(nombre)
                if proc is None or proc.poll() is not None:
                    if proc is not None:
                        logging.warning("%s terminó con código %s; reiniciando", nombre, proc.returncode)
                    salida = (logs / f"principal-{nombre}.log").open("a", encoding="utf-8")
                    try:
                        procesos[nombre] = subprocess.Popen(
                            [sys.executable, "-m", "streamlit", "run", str(app),
                             "--server.address", config.get("bind", "127.0.0.1"), "--server.port", str(puerto),
                             "--server.headless", "true", "--global.developmentMode", "false",
                             "--server.fileWatcherType", "none", "--browser.gatherUsageStats", "false"],
                            cwd=codigo, env=env, stdout=salida, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW)
                    finally:
                        salida.close()
                    continue
                try:
                    with urlopen(f"http://127.0.0.1:{puerto}/_stcore/health", timeout=5) as r:
                        if r.status != 200:
                            raise RuntimeError("salud no disponible")
                    fallos[nombre] = 0
                except Exception:
                    fallos[nombre] = fallos.get(nombre, 0) + 1
                    if fallos[nombre] >= 3:
                        logging.error("%s no responde; reiniciando", nombre)
                        proc.terminate()
            hoy = date.today().isoformat()
            if ultimo_respaldo != hoy:
                try:
                    archivo = respaldar(base, base / "Backups" / "Servidor")
                    ultimo_respaldo = hoy
                    estado_respaldo.write_text(hoy)
                    logging.info("Respaldo verificado: %s", archivo)
                except Exception:
                    logging.exception("No se pudo completar el respaldo diario")
            time.sleep(30)
    finally:
        for proc in procesos.values():
            if proc.poll() is None:
                proc.terminate()
        lock.close()


if __name__ == "__main__":
    main()
