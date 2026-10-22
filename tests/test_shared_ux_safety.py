"""Regression tests for shared quotation concurrency and product completeness metadata."""
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class SharedQuotationConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.original_access = os.environ.get("GIOVAL_REQUIRE_ACCESS")
        os.environ["GIOVAL_REQUIRE_ACCESS"] = "true"
        import historial
        self.historial = importlib.reload(historial)

    def tearDown(self):
        if self.original_access is None:
            os.environ.pop("GIOVAL_REQUIRE_ACCESS", None)
        else:
            os.environ["GIOVAL_REQUIRE_ACCESS"] = self.original_access
        import historial
        importlib.reload(historial)

    def test_stale_editor_cannot_overwrite_another_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "biblioteca"
            session_a = self.historial.Historial(root)
            quote_id, _ = session_a.crear(
                {"nombre": "CLIENTE_PRIVADO", "rut": "RUT_PRIVADO"},
                [{"cantidad": 1, "descripcion": "Toner CF283A", "precio_neto": 100}],
                {"neto": 100, "iva": 19, "total": 119},
            )
            session_b = self.historial.Historial(root)
            version_a = session_a.obtener(quote_id)["actualizado"]
            version_b = session_b.obtener(quote_id)["actualizado"]
            self.assertEqual(version_a, version_b)

            session_a.actualizar_si_version(quote_id, version_a, total=150)
            with self.assertRaises(self.historial.ConflictoActualizacion):
                session_b.actualizar_si_version(quote_id, version_b, total=999)

            actual = session_a.obtener(quote_id)
            self.assertEqual(actual["total"], 150)
            self.assertEqual(actual["cliente"], "")
            self.assertEqual(actual["rut"], "")
            self.assertNotIn("CLIENTE_PRIVADO", str(actual["datos"]))
            self.assertNotIn("RUT_PRIVADO", str(actual["datos"]))

    def test_product_can_explicitly_skip_a_technical_datasheet(self):
        from cotizador import Biblioteca

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "biblioteca"
            catalog = Biblioteca(root)
            catalog.registrar("CARPETA-SIMPLE", nombre="Carpeta simple")
            catalog.actualizar("CARPETA-SIMPLE", ficha_no_requerida=True)

            reloaded = Biblioteca(root)
            product = next(row for row in reloaded.filas_catalogo()
                           if row["clave"] == "CARPETA-SIMPLE")
            self.assertTrue(product["ficha_no_requerida"])
            self.assertFalse(product["ficha"])

            reloaded.actualizar("CARPETA-SIMPLE", ficha_no_requerida=False)
            final = Biblioteca(root).indice["CARPETA-SIMPLE"]
            self.assertFalse(final["ficha_no_requerida"])


if __name__ == "__main__":
    unittest.main()
