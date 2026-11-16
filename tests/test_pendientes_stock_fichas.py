"""Stock confirmado, actualización masiva y diagnóstico de fichas de biblioteca."""
import os
import unittest
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from reportlab.pdfgen.canvas import Canvas
from streamlit.testing.v1 import AppTest

import fichas_catalogo as fichas
import stock_catalogo as stock
from cotizador import Biblioteca


APP = Path(__file__).resolve().parents[1] / "app.py"


def pdf_con_texto(texto):
    contenido = BytesIO()
    pdf = Canvas(contenido, pageCompression=1)
    if texto:
        pdf.drawString(40, 750, texto)
    pdf.showPage()
    pdf.save()
    return contenido.getvalue()


class PendientesStockFichaTests(unittest.TestCase):
    def setUp(self):
        directorio = self.enterContext(TemporaryDirectory(ignore_cleanup_errors=True))
        self.raiz = Path(directorio)
        self.bib = Biblioteca(self.raiz / "biblioteca")
        self.enterContext(patch.dict(os.environ, {
            "GIOVAL_DATA_DIR": directorio, "GIOVAL_REQUIRE_ACCESS": "false", "GIOVAL_PROFILES": "false",
        }))
        self.ayer = date.today() - timedelta(days=1)

    def test_estados_ficha_y_reemplazo_de_pdf(self):
        ficha = self.raiz / "ficha.pdf"
        self.assertEqual(fichas.problema_ficha(None, ["TN1060"]), fichas.FALTANTE)
        self.assertEqual(fichas.problema_ficha(None, ["TN1060"], opcional=True), "")
        for contenido, esperado in (
            (b"%PDF-archivo trunco", fichas.ILEGIBLE),
            (pdf_con_texto("Ficha modelo TN10600"), fichas.SIN_CODIGO),
            (pdf_con_texto(""), fichas.SIN_TEXTO),
            (pdf_con_texto("Ficha modelo TN-1060"), ""),
        ):
            with self.subTest(esperado=esperado):
                temporal = self.raiz / "siguiente.pdf"
                temporal.write_bytes(contenido)
                temporal.replace(ficha)
                self.assertEqual(fichas.problema_ficha(ficha, ["TN1060"]), esperado)
        self.assertEqual(fichas.problema_ficha(ficha, ["X123", "TN1060"]), "")

    def test_lote_no_aplica_cambios_parciales_si_una_ficha_quedo_desactualizada(self):
        for codigo in ("TN1060", "CF283A", "T664"):
            self.bib.registrar(codigo, codigo)
            self.bib.actualizar(codigo, costo_neto=1000)
        versiones = dict(self.bib.indice)
        otra = Biblioteca(self.bib.dir)
        otra.actualizar("CF283A", costo_neto=2000)
        campos = stock.campos_stock("En stock", self.ayer, "Sucursal centro")
        with self.assertRaisesRegex(ValueError, "CF283A"):
            self.bib.actualizar_varios(["TN1060", "CF283A"], campos, versiones)
        guardado = Biblioteca(self.bib.dir)
        self.assertNotIn("estado_stock", guardado.indice["TN1060"])
        self.assertEqual(guardado.indice["CF283A"]["costo_neto"], 2000)
        self.assertEqual(self.bib.actualizar_varios(["TN1060", "CF283A"], campos, guardado.indice), 2)
        cargado = Biblioteca(self.bib.dir)
        for codigo in ("TN1060", "CF283A"):
            self.assertEqual(stock.datos_stock(cargado.indice[codigo]), {
                "estado_stock": "En stock", "stock_confirmado_el": self.ayer, "stock_fuente": "Sucursal centro",
            })
        self.assertNotIn("estado_stock", cargado.indice["T664"])
        cargado.actualizar("TN1060", **stock.campos_stock("Por confirmar", None, "Sucursal centro"))
        self.assertNotIn("stock_confirmado_el", cargado.indice["TN1060"])
        self.assertNotIn("stock_fuente", cargado.indice["TN1060"])

    def test_bandeja_detecta_fichas_y_confirma_stock_para_dos_productos(self):
        for codigo in ("CF283A", "TN1060", "T664"):
            self.bib.registrar(codigo, codigo)
            self.bib.actualizar(codigo, costo_neto=1200)
        self.bib.guardar_archivo("CF283A", "ficha", "ficha.pdf", pdf_con_texto("Modelo CF283A"))
        self.bib.guardar_archivo("TN1060", "ficha", "ficha.pdf", pdf_con_texto("Modelo TN760"))
        rota = self.bib.carpeta("T664") / "ficha.pdf"
        rota.parent.mkdir(parents=True, exist_ok=True)
        rota.write_bytes(b"%PDF-archivo trunco")

        app = AppTest.from_file(str(APP), default_timeout=30).run()
        app.session_state["vista"] = "Datos pendientes"
        app.run()
        self.assertFalse(app.exception, app.exception)
        app.session_state["pendientes_filtro"] = "Ficha incompleta"
        app.run()
        seleccion = next(w for w in app.selectbox if w.label == "Producto pendiente")
        self.assertNotIn("CF283A · CF283A", seleccion.options)
        self.assertIn("TN1060 · TN1060", seleccion.options)
        self.assertIn("T664 · T664", seleccion.options)
        seleccion.set_value("TN1060")
        app.run()
        self.assertTrue(any("código, modelo ni alias" in aviso.value for aviso in app.warning))

        app.session_state["pendientes_filtro"] = "Stock sin confirmar"
        app.session_state["stock_masivo_panel"] = True
        app.run()
        next(w for w in app.multiselect if w.label == "Productos para actualizar").set_value(["CF283A", "TN1060"])
        app.selectbox(key="stock_masivo_estado").set_value("A pedido")
        app.date_input(key="stock_masivo_fecha").set_value(self.ayer)
        app.text_input(key="stock_masivo_fuente").set_value("Proveedor Arica")
        app.button(key="stock_masivo_aplicar").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertTrue(any("Stock actualizado en 2" in aviso.value for aviso in app.success))
        self.bib.refrescar_si_cambio()
        for codigo in ("CF283A", "TN1060"):
            confirmacion = stock.datos_stock(self.bib.indice[codigo])
            self.assertEqual(confirmacion["estado_stock"], "A pedido")
            self.assertEqual(confirmacion["stock_confirmado_el"], self.ayer)
            self.assertEqual(confirmacion["stock_fuente"], "Proveedor Arica")
        self.assertNotIn("estado_stock", self.bib.indice["T664"])
        app.session_state["pendientes_filtro"] = "Ficha incompleta"
        app.run()
        next(w for w in app.selectbox if w.label == "Producto pendiente").set_value("TN1060")
        app.run()
        self.assertTrue(any("Proveedor Arica" in aviso.value for aviso in app.caption))
        app.session_state["vista"] = "Biblioteca"
        app.session_state["p_sel"] = "CF283A"
        app.session_state["biblioteca_stock_panel"] = True
        app.run()
        self.assertFalse(app.exception, app.exception)
        self.assertTrue(any("Proveedor Arica" in aviso.value for aviso in app.caption))
        next(w for w in app.selectbox if w.label == "Stock en biblioteca").set_value("En stock")
        next(w for w in app.date_input if w.label == "Fecha de confirmación").set_value(date.today())
        next(w for w in app.text_input if w.label == "Proveedor o sucursal").set_value("Sucursal centro")
        next(w for w in app.button if w.label == "Guardar stock").click().run()
        self.assertFalse(app.exception, app.exception)
        self.bib.refrescar_si_cambio()
        self.assertEqual(stock.datos_stock(self.bib.indice["CF283A"]), {
            "estado_stock": "En stock", "stock_confirmado_el": date.today(), "stock_fuente": "Sucursal centro",
        })


if __name__ == "__main__":
    unittest.main()
