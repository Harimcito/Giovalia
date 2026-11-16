import unittest
from unittest.mock import Mock, patch

from mercado_publico import ErrorMercadoPublico, consultar_codigo


class ConsultaMercadoPublicoTests(unittest.TestCase):
    codigo = "635-659-COT26"

    def test_compra_agil_importa_productos_y_adjuntos(self):
        payload = {
            "codigo": self.codigo,
            "institucion": {"organismo_comprador": "Municipalidad"},
            "productos_solicitados": [{"nombre": "Resma", "cantidad": "2"}],
            "documentos": [{"id": 9, "nombre": "Bases.pdf"}],
            "presupuesto": {"presupuesto_estimado": "15000"},
            "proveedor": {"rut": "dato ajeno"},
        }
        respuesta = Mock(status_code=200, ok=True)
        respuesta.json.return_value = {"success": "OK", "payload": payload}
        with patch("compra_agil.requests.get", return_value=respuesta) as peticion:
            resultado = consultar_codigo(self.codigo, "ticket-prueba")

        self.assertTrue(peticion.call_args.args[0].endswith("/" + self.codigo))
        self.assertEqual(peticion.call_args.kwargs["headers"], {"ticket": "ticket-prueba"})
        self.assertEqual(resultado["institucion"], "Municipalidad")
        self.assertEqual(resultado["productos"][0]["cantidad"], 2)
        self.assertEqual(resultado["documentos"], [{"id": "9", "nombre": "Bases.pdf", "url": ""}])
        self.assertEqual(resultado["presupuesto"], 15000)
        self.assertNotIn("dato ajeno", str(resultado))

    def test_timeout_del_detalle_recupera_datos_parciales_por_codigo(self):
        gateway = Mock(status_code=504)
        listado = Mock(status_code=200, ok=True)
        listado.json.return_value = {"payload": {"items": [
            {"codigo": "111-11-COT26", "nombre": "Otro proceso"},
            {"codigo": self.codigo.lower(), "nombre": "Resmas",
             "montos": {"monto_disponible_clp": 20000}},
        ]}}
        with (
            patch("compra_agil.requests.get", side_effect=[gateway, gateway, listado]) as peticion,
            patch("compra_agil.time.sleep"),
        ):
            resultado = consultar_codigo(self.codigo, "ticket-prueba")

        self.assertEqual(peticion.call_count, 3)
        self.assertEqual(peticion.call_args.kwargs["params"], {"id": self.codigo, "tamano_pagina": 10})
        self.assertEqual(resultado["nombre"], "Resmas")
        self.assertEqual(resultado["presupuesto"], 20000)
        self.assertTrue(resultado["detalle_incompleto"])
        self.assertEqual(resultado["productos"], [])

    def test_ticket_rechazado_no_solicita_listado(self):
        with patch("compra_agil.requests.get", return_value=Mock(status_code=401)) as peticion:
            with self.assertRaisesRegex(ErrorMercadoPublico, "HTTP 401"):
                consultar_codigo(self.codigo, "ticket-prueba")
        peticion.assert_called_once()

    def test_orden_compra_usa_el_endpoint_de_ordenes(self):
        codigo = "635-659-AG26"
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {"Listado": [{
            "Codigo": codigo,
            "Items": {"Listado": [{"NombreProducto": "Papel carta", "Cantidad": 3}]},
        }]}
        with patch("mercado_publico.requests.get", return_value=respuesta) as peticion:
            resultado = consultar_codigo(codigo, "ticket-prueba")
        self.assertIn("ordenesdecompra", peticion.call_args.args[0])
        self.assertEqual(resultado["codigo"], codigo)
        self.assertEqual(resultado["productos"][0]["descripcion"], "Papel carta")
