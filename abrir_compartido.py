"""Abre siempre el servidor central; no crea bibliotecas ni aplica actualizaciones."""
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

base = Path(os.environ["LOCALAPPDATA"]) / "Gioval"
codigo = Path(__file__).resolve().parent
subprocess.Popen([sys.executable, str(codigo / "servidor_principal.py")],
                 cwd=codigo, creationflags=subprocess.CREATE_NO_WINDOW)
for _ in range(30):
    try:
        with urlopen("http://127.0.0.1:8501/_stcore/health", timeout=2):
            break
    except Exception:
        time.sleep(1)
webbrowser.open("http://localhost:8501")
