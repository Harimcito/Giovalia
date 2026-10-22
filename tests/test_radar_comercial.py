"""Tests de los servicios del Radar Comercial sin realizar llamadas reales a ChileCompra."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from radar_comercial import servicio
from radar_comercial.servicio import (
    RadarStore,
    evaluar_rentabilidad,
    normalizar_detalle,
    normalizar_importacion,
    parsear_lineas_productos,
    registro_desde_api,
    resumen_historial_cotizador,
    sugerir_productos_catalogo,
)


class RadarComercialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RadarStore(Path(self.tmp.name) / "radar_comercial.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_api_listing_is_projected_to_safe_commercial_fields(self):
        registro = registro_desde_api({
            "codigo": "100-22-COT26",
            "nombre": "Suministro de tóner y resmas",
            "estado": {"codigo": "publicada", "glosa": "Publicada"},
            "convocatoria": {"estado_convocatoria": 1},
            "fechas": {"fecha_publicacion": "2026-10-21T11:00:00Z", "fecha_cierre": "2026-10-23T18:00:00Z"},
            "institucion": {
                "organismo_comprador": "Municipalidad de Prueba",
                "region": 15,
                "nombre_region": "Arica y Parinacota",
                "rut": "RUT-INSTITUCION",
            },
            "montos": {"moneda": "CLP", "monto_disponible_clp": 500000},
            "proveedores_cotizando": [{"razon_social": "Proveedor Test", "rut_proveedor": "RUT-PROVEEDOR"}],
        })
        self.assertEqual(registro["codigo"], "100-22-COT26")
        self.assertEqual(registro["region"], 15)
        self.assertEqual(registro["presupuesto_clp"], 500000)
        self.assertNotIn("proveedores_cotizando", registro)
        self.assertNotIn("rut", registro.get("institucion", {}))

    def test_detail_whitelist_excludes_provider_information(self):
        detail = normalizar_detalle({
            "codigo": "100-22-COT26",
            "nombre": "Suministro de tóner",
            "descripcion": "Tóner compatible y original",
            "institucion": {"organismo_comprador": "Municipalidad X", "rut": "RUT-ORGANISMO"},
            "productos_solicitados": [
                {"codigo_producto": 123, "nombre": "Tóner CF283A", "descripcion": "Tóner CF283A", "cantidad": 2, "unidad_medida": "EA"}
            ],
            "proveedores_cotizando": [{"razon_social": "EMPRESA AJENA", "rut_proveedor": "RUT-AJENO"}],
        })
        serializado = json.dumps(detail, ensure_ascii=False)
        self.assertIn("Tóner CF283A", serializado)
        self.assertNotIn("EMPRESA AJENA", serializado)
        self.assertNotIn("RUT-AJENO", serializado)
        self.assertNotIn("RUT-ORGANISMO", serializado)
        self.assertEqual(normalizar_detalle({}), {})

    def test_catalog_match_is_a_suggestion_not_automatic_certification(self):
        catalog = {
            "TN1060": {"nombre": "Tóner Brother TN1060", "aliases": ["BROTHER TN-1060"], "costo_neto": 6000},
            "TN450": {"nombre": "Tóner Brother TN450", "aliases": [], "costo_neto": 7000},
        }
        matches = sugerir_productos_catalogo("Toner Brother TN1060 original", catalog)
        self.assertTrue(matches)
        self.assertEqual(matches[0]["clave"], "TN1060")
        self.assertGreater(matches[0]["score"], 0.8)

    def test_profitability_requires_verified_equivalence_and_extra_costs(self):
        catalog = {
            "TN1060": {"nombre": "Tóner TN1060", "costo_neto": 11900, "precio_neto": 17850},
        }
        line = {
            "descripcion_solicitada": "Tóner Brother TN1060",
            "cantidad": 2,
            "codigo_catalogo": "TN1060",
            "precio_oferta_unitario_neto": 15000,
            "equivalencia_confirmada": True,
            "incluir": True,
        }
        result = evaluar_rentabilidad(
            [line], catalog, "Incluyen IVA",
            despacho_neto=1000, otros_costos_netos=500,
            margen_objetivo_pct=15, costos_adicionales_confirmados=True,
        )
        self.assertTrue(result["costos_completos"])
        self.assertTrue(result["precios_completos"])
        self.assertEqual(result["ingresos_netos"], 30000)
        self.assertEqual(result["costo_productos_parcial_neto"], 20000)
        self.assertEqual(result["costo_estimado_parcial_neto"], 21500)
        self.assertEqual(result["beneficio_estimado_neto"], 8500)
        self.assertAlmostEqual(result["margen_sobre_venta_pct"], 28.33, places=2)
        self.assertIsNotNone(result["precio_minimo_neto_objetivo"])

        line["equivalencia_confirmada"] = False
        result_pending = evaluar_rentabilidad(
            [line], catalog, "Incluyen IVA",
            despacho_neto=1000, otros_costos_netos=500,
            margen_objetivo_pct=15, costos_adicionales_confirmados=True,
        )
        self.assertFalse(result_pending["costos_completos"])
        self.assertIsNone(result_pending["margen_sobre_venta_pct"])
        self.assertTrue(result_pending["faltan_equivalencias"])

    def test_store_preserves_follow_up_and_attaches_saved_evaluation(self):
        record = {
            "codigo": "120-1-COT26",
            "nombre": "Compra de artículos de oficina",
            "estado_fuente": "publicada",
            "region": 15,
            "region_nombre": "Arica y Parinacota",
            "presupuesto_clp": 200000,
            "fuente": "Manual",
            "detalle": {
                "codigo": "120-1-COT26",
                "nombre": "Compra de artículos de oficina",
                "productos_solicitados": [
                    {"nombre": "Resma oficio", "descripcion": "Resma oficio", "cantidad": 10, "unidad_medida": "resma"}
                ],
            },
        }
        self.store.guardar_oportunidad(record)
        self.store.actualizar_seguimiento("120-1-COT26", "Cotizar", "Verificar costo de despacho")
        self.store.guardar_oportunidad({
            **record,
            "nombre": "Compra de artículos de oficina (actualizada)",
            "detalle": {},
            "fuente": "API Compra Ágil",
        })
        self.store.guardar_evaluacion("120-1-COT26", {
            "lineas": [{"descripcion_solicitada": "Resma oficio", "cantidad": 10, "codigo_catalogo": "PAPEL"}],
            "despacho_neto": 500,
            "resultado_calculo": {"resultado": "Incompleto"},
            "api_ticket": "NO-GUARDAR-ESTE-TICKET",
        })
        with patch.object(self.store, "evaluacion", side_effect=AssertionError("N+1 evaluation query")):
            listed = self.store.listar()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["decision"], "Cotizar")
        self.assertEqual(listed[0]["notas"], "Verificar costo de despacho")
        self.assertEqual(listed[0]["detalle"]["productos_solicitados"][0]["nombre"], "Resma oficio")
        self.assertEqual(listed[0]["evaluacion"]["resultado_calculo"]["resultado"], "Incompleto")
        self.assertNotIn("api_ticket", json.dumps(listed[0]["evaluacion"]))

    def test_api_search_reports_progress_once_per_term_and_deduplicates(self):
        events = []

        def fake_request(ticket, url, params):
            termino = params["q"]
            return {
                "success": "OK",
                "payload": {
                    "items": [{
                        "codigo": f"COD-{termino.upper()}",
                        "nombre": f"Compra de {termino}",
                        "estado": {"codigo": "publicada", "glosa": "Publicada"},
                        "institucion": {"organismo_comprador": "Organismo de prueba", "region": 15,
                                        "nombre_region": "Arica y Parinacota"},
                        "montos": {"moneda": "CLP", "monto_disponible_clp": 100000},
                    }]
                }
            }

        with patch.object(servicio, "_request_json", side_effect=fake_request):
            results, errors, calls = servicio.buscar_compras_agiles(
                ticket="ticket-no-persistido",
                palabras=["toner", "tinta", "toner", "  tinta  "],
                regiones=[15],
                estados=["publicada"],
                on_progress=lambda actual, total, termino: events.append((actual, total, termino)),
            )

        self.assertEqual(calls, 2)
        self.assertEqual(len(results), 2)
        self.assertEqual(errors, [])
        self.assertEqual(events, [(1, 2, "toner"), (2, 2, "tinta")])

    def test_manual_import_and_line_parser(self):
        record = normalizar_importacion({
            "Código Compra Ágil": "1500-2-COT26",
            "Nombre": "Tóner y resmas",
            "Organismo comprador": "Municipalidad de Arica",
            "Región": "Arica y Parinacota",
            "Presupuesto estimado": 450000,
            "Estado": "Publicada",
        })
        self.assertEqual(record["codigo"], "1500-2-COT26")
        self.assertEqual(record["region"], 15)
        self.assertEqual(record["presupuesto_clp"], 450000)
        lines = parsear_lineas_productos("2 | Tóner CF283A | unidad\n10 | Resma oficio | resma")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["cantidad"], 2)
        self.assertEqual(lines[1]["nombre"], "Resma oficio")

    def test_backup_zip_contains_radar_database_and_readme(self):
        self.store.guardar_oportunidad({
            "codigo": "BACKUP-1",
            "nombre": "Oportunidad para respaldo",
            "fuente": "Manual",
            "detalle": {},
        })
        raw = self.store.crear_respaldo_zip()
        with zipfile.ZipFile(io.BytesIO(raw)) as backup:
            self.assertIn("radar_comercial.db", backup.namelist())
            self.assertIn("LEEME.txt", backup.namelist())
            self.assertIn("Respaldo de Radar Comercial", backup.read("LEEME.txt").decode("utf-8"))
            db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            try:
                db.write(backup.read("radar_comercial.db"))
                db.close()
                import sqlite3
                with sqlite3.connect(db.name) as con:
                    row = con.execute(
                        "SELECT nombre FROM oportunidades WHERE codigo='BACKUP-1'"
                    ).fetchone()
                self.assertEqual(row[0], "Oportunidad para respaldo")
            finally:
                Path(db.name).unlink(missing_ok=True)

    def test_quote_history_summary_does_not_return_client_identifiers(self):
        summary = resumen_historial_cotizador([
            {
                "estado": "Aceptada",
                "total": 23800,
                "cliente": "CLIENTE PRIVADO",
                "rut": "RUT PRIVADO",
                "datos": json.dumps({
                    "cliente": {"nombre": "CLIENTE PRIVADO", "rut": "RUT PRIVADO"},
                    "lineas": [{"descripcion": "Tóner CF283A", "cantidad": 2}],
                }),
            },
        ])
        self.assertEqual(summary["cotizaciones"], 1)
        self.assertEqual(summary["aceptadas"], 1)
        self.assertEqual(summary["productos_frecuentes"], [("Tóner CF283A", 2)])
        self.assertNotIn("cliente", summary)
        self.assertNotIn("rut", summary)


if __name__ == "__main__":
    unittest.main()
