import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

import operacion_comercial as op
import historial as hs
from cotizador import Biblioteca


class OperacionComercialTests(unittest.TestCase):
    def test_presentacion_y_compatibilidad(self):
        self.assertTrue(op.conflicto_presentacion("Resma Oficio de 500 hojas", {"nombre": "Resma oficio 250 hojas"}))
        self.assertTrue(op.conflicto_presentacion("Tóner original", {"nombre": "Tóner alternativo"}))
        self.assertTrue(op.conflicto_presentacion("Pack 4 unidades", {"nombre": "Pack 2 unidades"}))
        self.assertFalse(op.conflicto_presentacion("10 resmas de 500 hojas", {"nombre": "Resma 500 hojas"}))

    def test_margen_descuento_y_costos_faltantes(self):
        lineas = [{"clave_auto": "PAPEL", "cantidad": 2, "unitario": 1000}]
        result = op.calcular_margen(lineas, {"PAPEL": {"costo_neto": 400}},
                                    despacho=200, descuentos=100, otros=100, objetivo=25)
        self.assertEqual(result["venta"], 1900)
        self.assertEqual(result["utilidad"], 800)
        self.assertEqual(result["minimo"], 1467)
        incompleto = op.calcular_margen(lineas, {})
        self.assertIsNone(incompleto["minimo"])
        self.assertIsNone(incompleto["margen"])

    def test_lista_de_proveedor_y_comparacion(self):
        nueva = op.cargar_costos("precios.csv", b"codigo;costo_neto\nT1;1.200\n")
        filas = op.comparar_costos({"T1": {"costo_neto": 1050, "margen_pct": 20}}, nueva, {"T1": 1100})
        self.assertEqual(filas[0]["Variación proveedor"], 100)
        self.assertEqual(filas[0]["Sugerido nuevo neto"], 1440)
        with self.assertRaises(ValueError):
            op.cargar_costos("precios.csv", b"codigo;costo_neto\nT1;1\nT1;2\n")
        with TemporaryDirectory() as tmp:
            hist = hs.Historial(tmp)
            hist.guardar_lista_proveedor("proveedor", nueva, {})
            with self.assertRaises(hs.ConflictoActualizacion):
                hist.guardar_lista_proveedor("proveedor", {"T1": 1400}, {})
            self.assertEqual(hist.lista_proveedor("proveedor"), nueva)

    def test_seguimiento_y_auditoria_por_usuario(self):
        with TemporaryDirectory() as tmp:
            hist = hs.Historial(tmp)
            hist.actor = "Operadora"
            cot, numero = hist.crear({}, [], {})
            hist.guardar_seguimiento(cot, "2026-12-02", None)
            hist.actualizar(cot, total=1500)
            bib = Biblioteca(tmp)
            bib.registrar_cambio = hist.registrar_cambio
            bib.registrar("PAPEL", precio_neto=100)
            bib.actualizar("PAPEL", precio_neto=120)
            self.assertEqual(hist.seguimientos()[cot]["vence"], "2026-12-02")
            cambios = hist.cambios_comerciales()
            self.assertTrue(all(c["actor"] == "Operadora" for c in cambios))
            self.assertTrue(any(c["objeto"] == numero and c["campo"] == "total" for c in cambios))
            self.assertEqual(cambios[0]["anterior"], "100.0")
            self.assertEqual(cambios[0]["nuevo"], "120.0")

    def test_respaldo_temporal_detecta_anexos_faltantes_y_no_toca_original(self):
        with TemporaryDirectory() as tmp:
            hist = hs.Historial(tmp)
            cot, _ = hist.crear({}, [], {})
            hist.actualizar(cot, datos={"anexos": {"PAPEL": {"foto": "a" * 64 + ".png"}}})
            original = Path(hist.db).read_bytes()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as z:
                z.write(hist.db, "biblioteca/cotizador.db")
            resultado = op.verificar_respaldo(buffer.getvalue())
            self.assertEqual(resultado["cotizaciones"], 1)
            self.assertEqual(resultado["faltantes"], ["PAPEL: falta foto personalizado"])
            self.assertEqual(Path(hist.db).read_bytes(), original)
            with zipfile.ZipFile(buffer, "a") as z:
                z.writestr("../fuera", b"invalido")
            with self.assertRaises(ValueError):
                op.verificar_respaldo(buffer.getvalue())
