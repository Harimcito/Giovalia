"""Respaldo privado y restaurable, sin claves API ni sesiones de acceso."""
import argparse
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path


def respaldar(base: Path, destino: Path) -> Path:
    base, destino = Path(base).resolve(), Path(destino).resolve()
    biblioteca = base / "Datos" / "biblioteca"
    if not (biblioteca / "cotizador.db").is_file():
        raise FileNotFoundError("No existe la biblioteca activa; no se creó un respaldo vacío.")
    destino.mkdir(parents=True, exist_ok=True)
    archivo = destino / f"giovalia-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
    temporal_zip = archivo.with_suffix(".zip.tmp")
    with tempfile.TemporaryDirectory(prefix="giovalia-backup-") as carpeta:
        stage = Path(carpeta)
        bases = [(biblioteca / "cotizador.db", "biblioteca/cotizador.db"),
                 (base / "Datos" / "radar_comercial.db", "radar_comercial.db"),
                 (base / "Acceso" / "usuarios.db", "acceso/usuarios.db")]
        copias = {}
        for origen, nombre in bases:
            if not origen.is_file():
                continue
            copia = stage / origen.name
            with closing(sqlite3.connect(origen.as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(copia)) as dst:
                    src.backup(dst)
            with closing(sqlite3.connect(copia)) as c:
                if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("El respaldo de SQLite no superó su comprobación.")
                if nombre.startswith("acceso/"):
                    c.execute("DELETE FROM sesiones")
                    c.commit()
            copias[nombre] = copia
        with zipfile.ZipFile(temporal_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for nombre, copia in copias.items():
                z.write(copia, nombre)
            for ruta in biblioteca.rglob("*"):
                if ruta.is_file() and ruta.name not in {"cotizador.db", "cotizador.db-wal", "cotizador.db-shm"}:
                    if ruta.suffix.lower() not in {".tmp", ".log", ".pyc"}:
                        z.write(ruta, "biblioteca/" + ruta.relative_to(biblioteca).as_posix())
            z.writestr("manifest.json", json.dumps({"version": 1, "creado": datetime.now().isoformat(),
                        "restaurar_biblioteca_en": str(biblioteca), "sesiones_excluidas": True}))
        with zipfile.ZipFile(temporal_zip) as z:
            if z.testzip():
                raise ValueError("El ZIP de respaldo no superó su comprobación.")
        temporal_zip.replace(archivo)
    return archivo


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("destino", type=Path)
    args = parser.parse_args()
    print(respaldar(args.base, args.destino))
