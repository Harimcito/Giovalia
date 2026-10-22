# -*- mode: python ; coding: utf-8 -*-
"""Build Windows onedir bundle for the Gioval desktop launcher."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).resolve()
datas = [
    (str(ROOT / "app.py"), "."),
    (str(ROOT / "plantilla_cotizacion.xlsx"), "."),
]
binaries = []
hiddenimports = [
    "buscador",
    "cotizador",
    "historial",
    "lector_ia",
    "pdf_cotizacion",
    "revisor",
    "streamlit.web.cli",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",
    "clr_loader",
]

# Streamlit needs frontend/static resources; these packages also use runtime-loaded
# modules and resources in the bundled desktop executable.
for package in ("streamlit", "webview", "openai", "ddgs", "pypdfium2", "PIL"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for package in ("streamlit", "pywebview", "openai", "ddgs", "pypdfium2", "Pillow"):
    try:
        datas += copy_metadata(package, recursive=True)
    except Exception:
        pass

a = Analysis(
    ["desktop_launcher.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "PyQt6", "PyQt5", "cefpython3"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Gioval",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Gioval",
)
