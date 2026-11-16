import json
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4

import catalogo_clientes as catalogo
import catalogo_publico
import seo_geo
from cotizador import Biblioteca


class CatalogoTests(unittest.TestCase):
    def test_seleccion_antigua_y_conflicto_entre_sesiones(self):
        with TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "catalogos_clientes.json"
            anterior = ["RESMA-CARTA"]
            catalogo._json_guardar(ruta, {
                "selecciones": {"Junio": anterior},
                "producto_meta": {"RESMA-CARTA": {"categoria": "Papel"}},
            })
            self.assertEqual(catalogo._borrador(anterior), {"claves": anterior})
            nuevo = {"claves": anterior, "vigencia": "30 de junio"}
            catalogo.guardar_borrador(ruta, "Junio", nuevo, anterior="Junio", version=anterior)
            with self.assertRaises(catalogo.ConflictoCatalogo):
                catalogo.guardar_borrador(ruta, "Junio", {}, anterior="Junio", version=anterior)
            registro = catalogo._json_leer(ruta)
            self.assertEqual(registro["selecciones"]["Junio"], nuevo)
            self.assertEqual(registro["producto_meta"]["RESMA-CARTA"]["categoria"], "Papel")

    def test_emision_pdf_a4_con_precio_e_ilustracion_referencial(self):
        with TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "catalogos_clientes.json"
            productos = [{"clave": "RESMA-CARTA", "nombre": "Resma carta",
                          "rubro": "Papel", "precio_neto": 1000}]
            nombre = catalogo.emitir(ruta, productos, "Precios de prueba", "", "Gioval")
            contenido = (ruta.parent / "catalogos_emitidos" / nombre).read_bytes()
            pdf = PdfReader(BytesIO(contenido))
            self.assertAlmostEqual(float(pdf.pages[0].mediabox.width), A4[0], places=2)
            self.assertAlmostEqual(float(pdf.pages[0].mediabox.height), A4[1], places=2)
            texto = " ".join(pagina.extract_text() for pagina in pdf.pages)
            self.assertIn("Resma carta", texto)
            self.assertIn("1.000", texto)
            self.assertIn("IMAGEN REFERENCIAL", texto)
            self.assertEqual(catalogo._json_leer(ruta)["emitidos"][0]["claves"], ["RESMA-CARTA"])

    def test_codigo_antiguo_y_carpetas_reservadas(self):
        with TemporaryDirectory() as temporal:
            biblioteca = Biblioteca(temporal)
            biblioteca.registrar("RESMA-CARTA")
            biblioteca.renombrar("RESMA-CARTA", "PAPEL-CARTA")
            self.assertEqual(biblioteca.clave_vigente("RESMA-CARTA"), "PAPEL-CARTA")
            for clave in ("..", "CON", "COTIZACIONES", "CATALOGOS_EMITIDOS"):
                with self.subTest(clave=clave), self.assertRaises(ValueError):
                    biblioteca.renombrar("PAPEL-CARTA", clave)
            self.assertEqual(list(biblioteca.indice), ["PAPEL-CARTA"])

    def test_seo_edicion_con_version_y_campos_independientes(self):
        with TemporaryDirectory() as temporal:
            primera = Biblioteca(temporal)
            primera.registrar("TN1060", precio_neto=10000)
            segunda = Biblioteca(temporal)
            ficha_anterior = segunda.indice["TN1060"].copy()
            primera.actualizar("TN1060", seo_titulo="Tóner TN1060", seo_precio_publico=12990)
            with self.assertRaisesRegex(ValueError, "Otra sesión"):
                segunda.actualizar("TN1060", version_esperada=ficha_anterior, nombre="Obsoleto")
            ficha = Biblioteca(temporal).indice["TN1060"]
            self.assertEqual(ficha["seo_titulo"], "Tóner TN1060")
            self.assertEqual(ficha["seo_precio_publico"], 12990)
            self.assertEqual(ficha["precio_neto"], 10000)


class SeoTests(unittest.TestCase):
    def test_exportacion_aprobada_preserva_uuid_y_excluye_datos_internos(self):
        with TemporaryDirectory() as temporal:
            biblioteca = Biblioteca(temporal)
            biblioteca.registrar("TN1060", precio_neto=15000)
            ficha = biblioteca.indice["TN1060"]
            identificador = ficha["id_global"]
            hoy = date.today().isoformat()
            biblioteca.actualizar("TN1060", publicacion={
                "estado": "aprobado", "slug": "toner-tn1060", "nombre": "Tóner TN1060",
                "descripcion": "Cartucho negro TN1060",
                "fuentes": [{"campo": campo, "referencia": "Referencia privada", "verificado_el": hoy}
                            for campo in ("nombre", "descripcion")],
                "imagenes": [{"url": "https://gioval.cl/tn1060.jpg", "alt": "Tóner",
                              "aprobada": True, "derechos": "propia"}],
            })
            biblioteca.renombrar("TN1060", "TONER-TN1060")
            self.assertEqual(biblioteca.indice["TONER-TN1060"]["id_global"], identificador)
            biblioteca.registrar("PENDIENTE", precio_neto=20000)
            exportacion, errores = catalogo_publico.exportar_catalogo(biblioteca.indice, "https://gioval.cl")
            self.assertEqual(len(exportacion["@graph"]), 1)
            self.assertEqual(errores, [])
            self.assertNotIn("offers", exportacion["@graph"][0])
            self.assertNotIn("Referencia privada", json.dumps(exportacion))

    def test_jsonld_solo_incluye_precio_explicito_y_enlaces_validos(self):
        ficha = {"clave": "TN1060", "nombre": "Tóner TN1060", "gtin": "4006381333931", "precio_neto": 10000,
                 "seo_disponibilidad": "En stock", "seo_url_publica": "https://localhost/tn1060",
                 "seo_imagen_url": "https://gioval.cl/img/tn1060.jpg"}
        datos = json.loads(seo_geo.paquete_jsonld([ficha]))
        producto = datos["@graph"][1]
        self.assertEqual(datos["@graph"][0]["@type"], "Store")
        self.assertEqual(producto["gtin13"], ficha["gtin"])
        self.assertNotIn("offers", producto)
        self.assertNotIn("url", producto)
        self.assertEqual(producto["image"], ficha["seo_imagen_url"])
        ficha.update(seo_precio_publico=12990, seo_url_publica="https://gioval.cl/tn1060")
        producto = seo_geo.producto_jsonld("TN1060", ficha)
        self.assertEqual(producto["offers"]["price"], "12990")
        self.assertEqual(producto["offers"]["priceCurrency"], "CLP")
        self.assertEqual(producto["offers"]["availability"], "https://schema.org/InStock")

    def test_registro_heredado_con_datos_invalidos_sigue_exportando(self):
        ficha = {"clave": "T1", "seo_precio_publico": float("inf"), "gtin": "GTIN4006381333931",
                 "seo_url_publica": "https://gioval.cl:invalid/producto"}
        self.assertIsNone(seo_geo.gtin_schema(ficha["gtin"]))
        self.assertNotIn("offers", seo_geo.producto_jsonld("T1", ficha))
        editorial = json.loads(seo_geo.paquete_editorial([ficha]))["productos"][0]
        self.assertIsNone(editorial["venta_publica"]["precio_iva_incluido_clp"])
        self.assertIsNone(editorial["seo"]["url_publica"])
