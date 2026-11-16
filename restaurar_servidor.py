"""Restaura únicamente en una carpeta NUEVA; nunca sobrescribe una biblioteca activa."""
import argparse
import json
import sqlite3
import stat
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath, PureWindowsPath


def restaurar(archivo: Path, base: Path):
    base = Path(base).resolve()
    if base.exists():
        raise ValueError("El destino debe ser una carpeta nueva. No se modificó nada.")
    with zipfile.ZipFile(archivo) as z:
        for item in z.infolist():
            partes = PurePosixPath(item.filename.replace("\\", "/")).parts
            if not partes or any(p in {"..", "."} or ":" in p for p in partes) or item.filename.startswith("/"):
                raise ValueError("El respaldo contiene rutas no permitidas.")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("El respaldo contiene enlaces no permitidos.")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("version") != 1 or z.testzip():
            raise ValueError("El respaldo no superó la verificación.")
        base.mkdir(parents=True)
        for item in z.infolist():
            if item.is_dir() or item.filename == "manifest.json":
                continue
            nombre = PurePosixPath(item.filename)
            if nombre.parts[0] == "biblioteca":
                destino = base / "Datos" / Path(*nombre.parts)
            elif nombre.parts[0] == "acceso":
                destino = base / "Acceso" / Path(*nombre.parts[1:])
            elif item.filename == "radar_comercial.db":
                destino = base / "Datos" / "radar_comercial.db"
            else:
                raise ValueError("Archivo de respaldo no reconocido.")
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(z.read(item))
    biblioteca = base / "Datos" / "biblioteca"
    with closing(sqlite3.connect(biblioteca / "cotizador.db")) as c:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("La base restaurada no es válida.")
        for ident, xlsx, pdf in c.execute("SELECT id,xlsx,pdf FROM cotizaciones").fetchall():
            nuevos = []
            for ruta in (xlsx, pdf):
                if not ruta:
                    nuevos.append(None)
                    continue
                # Los archivos siempre se archivan bajo cotizaciones/<carpeta>/<archivo>.
                partes = PureWindowsPath(ruta).parts if "\\" in ruta else PurePosixPath(ruta).parts
                if "cotizaciones" not in partes:
                    raise ValueError("Una ruta archivada no pertenece a cotizaciones.")
                relativa = partes[partes.index("cotizaciones"):]
                destino = biblioteca.joinpath(*relativa)
                if not destino.is_relative_to(biblioteca) or not destino.is_file():
                    raise ValueError("Falta un documento archivado en el respaldo.")
                nuevos.append(str(destino))
            c.execute("UPDATE cotizaciones SET xlsx=?,pdf=? WHERE id=?", (*nuevos, ident))
        c.commit()
    return base


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archivo", type=Path)
    parser.add_argument("destino_nuevo", type=Path)
    args = parser.parse_args()
    print(restaurar(args.archivo, args.destino_nuevo))
