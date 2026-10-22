"""Regresión de los adaptadores de lectura independiente de Giovalia."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    from radar_comercial.integracion import BibliotecaLectura, HistorialLectura
except ModuleNotFoundError:
    from integracion import BibliotecaLectura, HistorialLectura


class IntegracionSoloLecturaTests(unittest.TestCase):
    def test_catalog_reads_index_and_optional_files_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "indice.json").write_text(json.dumps({
                "TN1060": {
                    "nombre": "Tóner TN1060",
                    "aliases": ["toner brother tn1060"],
                    "costo_neto": 6000,
                    "margen_pct": 25,
                    "precio_neto": 9500,
                }
            }), encoding="utf-8")
            carpeta = root / "TN1060"
            carpeta.mkdir()
            (carpeta / "foto.jpg").write_bytes(b"test")
            (carpeta / "ficha.pdf").write_bytes(b"test")

            catalog = BibliotecaLectura(root)
            self.assertEqual(catalog.indice["TN1060"]["nombre"], "Tóner TN1060")
            self.assertEqual(catalog.precio_sugerido("TN1060"), 7500)
            rows = catalog.filas_catalogo()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["foto"])
            self.assertTrue(rows[0]["ficha"])
            self.assertEqual(rows[0]["costo_neto"], 6000)
            self.assertFalse((root / "indice.json.tmp").exists())

    def test_history_reads_only_commercial_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "cotizador.db"
            with sqlite3.connect(db_path) as con:
                con.execute(
                    "CREATE TABLE cotizaciones (id INTEGER PRIMARY KEY, estado TEXT, total REAL, datos TEXT, cliente TEXT, rut TEXT)"
                )
                con.execute(
                    "INSERT INTO cotizaciones (estado,total,datos,cliente,rut) VALUES (?,?,?,?,?)",
                    ("Aceptada", 23800, json.dumps({"lineas":[{"descripcion":"Tóner TN1060","cantidad":2}]}), "CLIENTE PRIVADO", "RUT-PRIVADO"),
                )
            rows = HistorialLectura(root).listar()
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0]), {"estado", "total", "datos"})
            self.assertNotIn("CLIENTE PRIVADO", str(rows))
            self.assertNotIn("RUT-PRIVADO", str(rows))

    def test_missing_library_is_empty_and_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "biblioteca"
            catalog = BibliotecaLectura(root)
            history = HistorialLectura(root)
            self.assertEqual(catalog.indice, {})
            self.assertEqual(catalog.filas_catalogo(), [])
            self.assertEqual(history.listar(), [])
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
