"""Regresiones de privacidad para el modo biblioteca compartida de Gioval."""
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys


class SharedLibraryPrivacyTests(unittest.TestCase):
    def reload_history(self, enabled):
        if enabled:
            os.environ["GIOVAL_REQUIRE_ACCESS"] = "true"
        else:
            os.environ.pop("GIOVAL_REQUIRE_ACCESS", None)
        if "historial" in sys.modules:
            return importlib.reload(sys.modules["historial"])
        import historial
        return historial

    def test_shared_mode_never_stores_client_fields_or_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = self.reload_history(True)
            root = Path(tmp) / "biblioteca"
            history = mod.Historial(root)
            client = {
                "nombre": "PERSONA_PRIVADA_TEST",
                "rut": "RUT_PRIVADO_TEST",
                "direccion": "DIRECCION_PRIVADA_TEST",
                "telefono": "TELEFONO_PRIVADO_TEST",
                "email": "CORREO_PRIVADO_TEST@example.invalid",
            }
            cot_id, _ = history.crear(
                client,
                [{"cantidad": 2, "descripcion": "Toner CF283A", "precio_neto": 100,
                  "dato_cliente": "NO_DEBE_GUARDARSE"}],
                {"neto": 200, "iva": 38, "total": 238},
            )
            history.actualizar(
                cot_id,
                datos={
                    "lineas": [{"cantidad": 2, "descripcion": "Toner CF283A", "precio_neto": 100,
                                "dato_cliente": "NO_DEBE_GUARDARSE"}],
                    "cliente": client,
                    "orden_texto": "TEXTO_PRIVADO_TEST",
                    "fuente_orden": "ORIGEN_PRIVADO_TEST",
                    "orden_df": [{"Incluir": True, "Cantidad": 2, "Producto": "Toner CF283A",
                                  "Precio unitario": 119, "Rut": "RUT_PRIVADO_TEST"}],
                },
                cliente=client["nombre"],
                rut=client["rut"],
                notas="NOTA_PRIVADA_TEST",
            )
            history.guardar_cliente(client)
            fuente = Path(tmp) / "con-datos.xlsx"
            fuente.write_bytes(b"contenido de prueba")
            history.guardar_archivos(cot_id, xlsx=fuente, pdf=b"PDF_PRIVADO_TEST")

            registro = history.obtener(cot_id)
            self.assertEqual(registro["cliente"], "")
            self.assertEqual(registro["rut"], "")
            self.assertEqual(registro["notas"], "")
            self.assertIsNone(registro["xlsx"])
            self.assertIsNone(registro["pdf"])
            self.assertEqual(history.clientes(), [])
            self.assertEqual(list((root / "cotizaciones").glob("**/*")), [])

            serializado = json.dumps(registro["datos"], ensure_ascii=False)
            for secreto in (
                "PERSONA_PRIVADA_TEST", "RUT_PRIVADO_TEST", "DIRECCION_PRIVADA_TEST",
                "TELEFONO_PRIVADO_TEST", "CORREO_PRIVADO_TEST", "TEXTO_PRIVADO_TEST",
                "NO_DEBE_GUARDARSE",
            ):
                self.assertNotIn(secreto, serializado)
            self.assertIn("lineas", registro["datos"])

    def test_migration_sanitizes_existing_shared_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "biblioteca"
            mod_local = self.reload_history(False)
            history_local = mod_local.Historial(root)
            cot_id, _ = history_local.crear(
                {"nombre": "MIGRAR_PERSONA_PRIVADA", "rut": "MIGRAR_RUT_PRIVADO",
                 "direccion": "MIGRAR_DIRECCION_PRIVADA"},
                [{"cantidad": 1, "descripcion": "Resma oficio", "precio_neto": 1000}],
                {"neto": 1000, "iva": 190, "total": 1190},
            )
            history_local.actualizar(
                cot_id,
                datos={"lineas": [{"cantidad": 1, "descripcion": "Resma oficio", "precio_neto": 1000}],
                       "cliente": {"nombre": "MIGRAR_PERSONA_PRIVADA"},
                       "orden_texto": "MIGRAR_TEXTO_PRIVADO"},
                notas="MIGRAR_NOTA_PRIVADA",
            )
            history_local.guardar_cliente({"nombre": "MIGRAR_PERSONA_PRIVADA",
                                           "rut": "MIGRAR_RUT_PRIVADO"})
            origen = Path(tmp) / "viejo.xlsx"
            origen.write_bytes(b"contenido antiguo")
            history_local.guardar_archivos(cot_id, xlsx=origen, pdf=b"PDF_ANTIGUO")

            mod_shared = self.reload_history(True)
            history_shared = mod_shared.Historial(root)
            registro = history_shared.obtener(cot_id)
            self.assertEqual(registro["cliente"], "")
            self.assertEqual(registro["rut"], "")
            self.assertEqual(registro["notas"], "")
            self.assertIsNone(registro["xlsx"])
            self.assertIsNone(registro["pdf"])
            self.assertEqual(history_shared.clientes(), [])
            self.assertEqual(list((root / "cotizaciones").glob("**/*")), [])
            serializado = json.dumps(registro["datos"], ensure_ascii=False)
            for secreto in ("MIGRAR_PERSONA_PRIVADA", "MIGRAR_RUT_PRIVADO",
                            "MIGRAR_DIRECCION_PRIVADA", "MIGRAR_TEXTO_PRIVADO"):
                self.assertNotIn(secreto, serializado)
            self.assertIn("Resma oficio", serializado)

    def tearDown(self):
        # Tests are isolated from the environment of subsequent test cases.
        os.environ.pop("GIOVAL_REQUIRE_ACCESS", None)
        if "historial" in sys.modules:
            importlib.reload(sys.modules["historial"])


if __name__ == "__main__":
    unittest.main()
