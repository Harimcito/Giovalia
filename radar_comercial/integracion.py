"""Lectura desacoplada de la biblioteca Gioval.

Radar puede ejecutarse sin importar código de Giovalia. Si existe una biblioteca
compatible, lee únicamente indice.json y resúmenes comerciales del SQLite; nunca
escribe en cotizador.db ni migra su esquema.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path


EXT_FOTO = (".jpg", ".jpeg", ".png", ".webp")


class BibliotecaLectura:
    """Vista de solo lectura del catálogo que Giovalia guarda en biblioteca/indice.json."""

    def __init__(self, carpeta: str | Path = "biblioteca"):
        self.dir = Path(carpeta)
        ruta = self.dir / "indice.json"
        if not ruta.exists():
            self.indice: dict = {}
            return
        try:
            indice = json.loads(ruta.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"No pude leer el índice de productos: {ruta}") from exc
        if not isinstance(indice, dict):
            raise ValueError(f"El índice de productos no es válido: {ruta}")
        self.indice = indice

    @staticmethod
    def _slug(clave: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", str(clave).strip().upper())

    def precio_sugerido(self, clave: str) -> float | None:
        ficha = self.indice.get(str(clave or "").strip().upper(), {})
        try:
            costo = float(ficha.get("costo_neto"))
            margen = float(ficha.get("margen_pct"))
        except (TypeError, ValueError, AttributeError):
            return None
        if costo and margen is not None:
            return costo * (1 + margen / 100)
        return None

    def filas_catalogo(self) -> list[dict]:
        filas = []
        for clave, ficha in sorted(self.indice.items()):
            if not isinstance(ficha, dict):
                ficha = {}
            carpeta_producto = self.dir / self._slug(clave)
            foto = any((carpeta_producto / f"foto{ext}").is_file() for ext in EXT_FOTO)
            pdf = (carpeta_producto / "ficha.pdf").is_file()
            filas.append({
                "clave": clave,
                "nombre": ficha.get("nombre", clave),
                "foto": foto,
                "ficha": pdf,
                "ficha_no_requerida": bool(ficha.get("ficha_no_requerida", False)),
                "precio_neto": ficha.get("precio_neto"),
                "costo_neto": ficha.get("costo_neto"),
                "margen_pct": ficha.get("margen_pct"),
                "sugerido_neto": self.precio_sugerido(clave),
                "notas": ficha.get("notas", ""),
            })
        return filas


class HistorialLectura:
    """Lee únicamente columnas comerciales agregadas desde cotizador.db en modo SQLite read-only."""

    def __init__(self, carpeta: str | Path = "biblioteca"):
        self.dir = Path(carpeta)
        self.db = self.dir / "cotizador.db"

    def listar(self, texto: str = "", estado: str | None = None) -> list[dict]:
        # Sin base de datos no hay historial; no creamos un archivo vacío ni tablas.
        if not self.db.is_file():
            return []
        uri = self.db.resolve().as_uri() + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=3)
            con.row_factory = sqlite3.Row
            try:
                existe = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cotizaciones'"
                ).fetchone()
                if not existe:
                    return []
                filas = con.execute(
                    "SELECT estado, total, datos FROM cotizaciones ORDER BY id DESC"
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error as exc:
            raise RuntimeError("No pude leer el historial del cotizador en modo solo lectura.") from exc

        salida = []
        for fila in filas:
            registro = {"estado": fila["estado"], "total": fila["total"]}
            if estado and estado != "Todos" and registro.get("estado") != estado:
                continue

            # El JSON de una cotización puede contener datos del cliente. Devuelve solo
            # las líneas comerciales consumidas por las métricas del Radar.
            try:
                datos = json.loads(fila["datos"] or "{}")
            except (TypeError, json.JSONDecodeError):
                datos = {}
            lineas_seguras = []
            for linea in datos.get("lineas") or [] if isinstance(datos, dict) else []:
                if not isinstance(linea, dict):
                    continue
                descripcion = str(linea.get("descripcion") or "").strip()
                if not descripcion:
                    continue
                lineas_seguras.append({
                    "descripcion": descripcion[:300],
                    "cantidad": linea.get("cantidad", 0),
                })
            registro["datos"] = json.dumps({"lineas": lineas_seguras}, ensure_ascii=False)

            if texto.strip():
                buscable = f"{registro.get('estado', '')} {registro['datos']}"
                if texto.casefold() not in buscable.casefold():
                    continue
            salida.append(registro)
        return salida
