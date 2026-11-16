import io
import tempfile
import unittest

from PIL import Image
from reportlab.pdfgen import canvas

import anexos_cotizacion as anexos
import historial as historial_mod
import revision_cotizacion as revision
from cotizador import Biblioteca


def imagen(color):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, "PNG")
    return buffer.getvalue()


class VersionesCotizacionTests(unittest.TestCase):
    def test_anexos_personalizados_y_congelados_no_cambian_la_biblioteca(self):
        with tempfile.TemporaryDirectory() as tmp:
            biblioteca = Biblioteca(tmp)
            biblioteca.registrar("RESMA")
            biblioteca.guardar_archivo("RESMA", "foto", "foto.png", imagen("red"))
            originales = anexos.congelar(biblioteca, ["RESMA"], {})
            personalizada = anexos.guardar(biblioteca, "foto", imagen("blue"))
            azul = anexos.resolver(biblioteca, "RESMA", {"foto": personalizada})[0]
            self.assertEqual(azul.read_bytes(), imagen("blue"))
            self.assertEqual(biblioteca.archivos("RESMA")[0].read_bytes(), imagen("red"))
            biblioteca.guardar_archivo("RESMA", "foto", "foto.png", imagen("green"))
            self.assertEqual(anexos.resolver(biblioteca, "RESMA", originales["RESMA"])[0].read_bytes(),
                             imagen("red"))
            self.assertIsNone(anexos.resolver(biblioteca, "RESMA", {"foto": None})[0])
            self.assertFalse(anexos.resolver(biblioteca, "RESMA", originales["RESMA"])[2])

    def test_restaurar_compara_y_conserva_una_copia_del_estado_actual(self):
        with tempfile.TemporaryDirectory() as tmp:
            historial = historial_mod.Historial(tmp)
            cid, numero = historial.crear({}, [], {})
            foto = "a" * 64 + ".jpg"
            datos = {"lineas": [{"descripcion": "Resma", "cantidad": 2, "unitario": 1000}],
                     "anexos": {"RESMA": {"foto": foto}}}
            historial.actualizar(cid, datos=datos, total=2380)
            primero = historial.obtener(cid)
            historial.guardar_version(cid, primero["actualizado"], "Oferta original")
            version = historial.versiones(cid)[0]["id"]
            historial.actualizar(cid, datos={"lineas": [{"descripcion": "Resma", "cantidad": 3}]}, total=0)
            segundo = historial.obtener(cid)
            cambios = revision.comparar(primero, segundo)
            self.assertIn("cantidad", {item["Campo"] for item in cambios})
            self.assertIn("foto", {item["Campo"] for item in cambios})
            with self.assertRaises(historial_mod.ConflictoActualizacion):
                historial.restaurar_version(cid, version, primero["actualizado"])
            historial.restaurar_version(cid, version, segundo["actualizado"])
            actual = historial.obtener(cid)
            self.assertEqual(actual["numero"], numero)
            self.assertEqual(actual["total"], 2380)
            self.assertEqual(actual["datos"]["anexos"], datos["anexos"])
            self.assertEqual(actual["estado"], "Borrador")
            self.assertEqual(historial.versiones(cid)[0]["contenido"]["datos"]["lineas"][0]["cantidad"], 3)

    def test_revision_identifica_texto_fuera_de_la_pagina(self):
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer)
        pdf.drawString(40, 100, "Texto visible")
        pdf.drawString(-40, 100, "Texto recortado")
        pdf.save()
        self.assertIn("fuera del área visible", revision.revisar_paginas(buffer.getvalue(), 1)[0])
