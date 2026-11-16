from decimal import Decimal
from io import BytesIO
from unittest import TestCase

from reportlab.pdfgen.canvas import Canvas

from canastas_cotizacion import Opcion, Pedido, costo_linea, planificar
from evidencia_cotizacion import coincidencias, dividir_requisitos, leer_paginas


def opcion(codigo, costo, existencias=None):
    return Opcion(codigo, Decimal(costo), None if existencias is None else Decimal(existencias), {})


class PlanificacionCotizacionTests(TestCase):
    def test_planifica_tres_canastas_y_respeta_cargos(self):
        pedidos = [
            Pedido(True, Decimal(1), [opcion("PEDIDO", "100"), opcion("HOY", "125", 1)]),
            Pedido(False, Decimal(1), [opcion("EXTRA", "25", 1)]),
        ]
        resultados = planificar(pedidos, 180, base=Decimal(20))
        self.assertEqual(resultados["Menor costo"].total, 143)
        self.assertEqual(resultados["Entrega hoy"].elegidas[0].codigo, "HOY")
        self.assertEqual(resultados["Entrega hoy"].cubiertas, 1)
        self.assertEqual(resultados["Mayor cobertura"].cubiertas, 2)
        self.assertEqual(resultados["Mayor cobertura"].total, 173)
        self.assertEqual(planificar(pedidos, 142, base=Decimal(20))["Mayor cobertura"].imprescindibles, 0)

    def test_una_misma_existencia_no_cubre_dos_lineas(self):
        pedidos = [
            Pedido(True, Decimal(2), [opcion("A", "20", 2), opcion("B", "41", 2)]),
            Pedido(True, Decimal(2), [opcion("A", "20", 2)]),
        ]
        hoy = planificar(pedidos, 80)["Entrega hoy"]
        self.assertEqual([o.codigo for o in hoy.elegidas], ["B", "A"])
        self.assertEqual(hoy.total, 73)
        self.assertEqual(planificar(pedidos, 72)["Entrega hoy"].cubiertas, 1)

    def test_precio_ausente_no_se_interpreta_como_gratis(self):
        self.assertIsNone(costo_linea({"cantidad": 3}))
        self.assertEqual(costo_linea({"cantidad": 3, "unitario": 0}), Decimal(0))
        pedido = Pedido(True, Decimal(1), [opcion("A", "0")])
        self.assertIsNone(planificar([pedido], 1)["Entrega hoy"])
        self.assertEqual(planificar([pedido], 1)["Menor costo"].total, 0)

    def test_extrae_pagina_y_distingue_cantidades_parecidas(self):
        documento = BytesIO()
        canvas = Canvas(documento)
        canvas.drawString(55, 720, "Presentación de 1500 hojas")
        canvas.showPage()
        canvas.drawString(55, 720, "Papel oficio. Gramaje 75 g/m².")
        canvas.save()
        paginas, aviso = leer_paginas(documento.getvalue())
        self.assertEqual(aviso, "")
        requisitos = dividir_requisitos("75 g/m²\n500 hojas\nOficio\n500 hojas")
        visto = coincidencias(requisitos, paginas)
        self.assertEqual(visto["75 g/m²"][0][0], 2)
        self.assertEqual(visto["Oficio"][0][0], 2)
        self.assertEqual(visto["500 hojas"], [])

    def test_ficha_sin_texto_y_archivo_ilegible(self):
        documento = BytesIO()
        canvas = Canvas(documento)
        canvas.rect(20, 20, 100, 120)
        canvas.save()
        self.assertIn("sin texto", leer_paginas(documento.getvalue())[1])
        self.assertIn("No se pudo", leer_paginas(b"otro formato")[1])
