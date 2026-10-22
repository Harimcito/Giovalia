# -*- mode: python ; coding: utf-8 -*-
"""Small Windows desktop shell that opens the shared, hosted Gioval application."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).resolve()
datas = []
binaries = []
hiddenimports = [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",
    "clr_loader",
    "pythonnet",
]

# pywebview uses runtime-loaded platform modules; collect its resources and metadata.
package_datas, package_binaries, package_hiddenimports = collect_all("webview")
datas += package_datas
binaries += package_binaries
hiddenimports += package_hiddenimports
for package in ("pywebview", "pythonnet"):
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
    excludes=[
        "streamlit", "pandas", "numpy", "openai", "ddgs", "pypdfium2",
        "PySide6", "PyQt6", "PyQt5", "cefpython3",
    ],
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
