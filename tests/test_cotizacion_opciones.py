from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pypdf import PdfReader, PdfWriter

from cotizador import Biblioteca
from cotizacion_opciones import GRUPO, anexar_opciones, buscar_compatibles, faltantes, normalizar_alternativas
from cotizacion_opciones_ui import cargar_vistas
from historial import Historial


class OpcionesCotizacionTests(TestCase):
    def test_modelo_confirmado_y_fronteras(self):
        catalogo = {
            "TONER": {"compatibilidad": "Brother HL-1212W, DCP-1617NW", "tipo_insumo": "Original"},
            "ALT": {"compatibilidad": "HL1212W", "tipo_insumo": "Alternativo"},
            "OTRO": {"compatibilidad": "HL1212WX"},
            "LIBRE": {"nombre": "Para HL-1212W"},
        }
        self.assertEqual([r[0] for r in buscar_compatibles("Brother HL 1212W", catalogo)], ["TONER", "ALT"])
        self.assertEqual(buscar_compatibles("HL-121", catalogo), [])

    def test_existencia_se_suma_y_status_anterior_sigue_por_confirmar(self):
        with TemporaryDirectory() as raiz:
            bib = Biblioteca(raiz)
            for clave in ("ABC", "OTRO"):
                bib.registrar(clave, clave)
            bib.actualizar("ABC", stock_cantidad=3, stock_confirmado_el=date.today().isoformat(),
                           stock_fuente="Sucursal Arica", estado_stock="En stock", reposicion_dias=4)
            bib.actualizar("OTRO", estado_stock="En stock", stock_confirmado_el=date.today().isoformat())
            def linea(clave, cantidad, **kwargs):
                return {"clave_auto": clave, "cantidad": cantidad, **kwargs}
            pendientes = faltantes([linea("ABC", 2), linea("ABC", 3), linea("ABC", 5, incluir=False),
                                    linea("OTRO", 1)], bib)
            self.assertEqual(pendientes[0]["Faltante"], 2)
            self.assertEqual(pendientes[0]["Reposición (días)"], 4)
            self.assertIsNone(pendientes[1]["Disponible confirmado"])

    def test_alternativas_excluyentes_aun_en_edicion_multiple_y_anexo_pdf(self):
        lineas = [{GRUPO: "test", "descripcion": "Original", "cantidad": 2, "unitario": 100},
                  {GRUPO: "test", "descripcion": "Compatible & genérico", "cantidad": 2, "unitario": 50,
                   "plazo_dias": 4}]
        lineas = normalizar_alternativas(lineas)
        self.assertTrue(lineas[0].get("incluir", True))
        self.assertFalse(lineas[1]["incluir"])
        pdf = PdfWriter()
        pdf.add_blank_page(width=400, height=600)
        salida = BytesIO()
        pdf.write(salida)
        bytes_finales, paginas = anexar_opciones(salida.getvalue(), lineas)
        self.assertEqual(paginas, 1)
        texto = PdfReader(BytesIO(bytes_finales)).pages[1].extract_text()
        self.assertIn("Compatible & genérico", texto)
        self.assertIn("$50", texto)
        self.assertIn("4 días", " ".join(texto.split()))
        self.assertNotIn(GRUPO, normalizar_alternativas(lineas[:1])[0])

    def test_vistas_persisten_y_se_aislan_por_perfil(self):
        with TemporaryDirectory() as raiz:
            hist = Historial(Path(raiz))
            clave, previo, _ = cargar_vistas(hist, "cuenta_a")
            hist.set_config(clave, '{"Mi búsqueda": ["0001", "Todos", "Todas las fechas", false]}',
                            anterior=previo)
            self.assertIn("Mi búsqueda", cargar_vistas(Historial(raiz), "cuenta_a")[2])
            self.assertNotIn("Mi búsqueda", cargar_vistas(hist, "cuenta_b")[2])
