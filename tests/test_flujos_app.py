import os
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import catalogo_clientes as catalogo
from openpyxl import load_workbook
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

from cotizador import Biblioteca
from flujo_cotizacion import buscar
from historial import Historial
from interfaz_giovalia import fragmento_pdf
from experiencia_giovalia import normalizar_preferencias
from usuarios import Usuarios


APP = Path(__file__).resolve().parents[1] / "app.py"


class FlujosAppTests(unittest.TestCase):
    def setUp(self):
        directorio = self.enterContext(TemporaryDirectory(ignore_cleanup_errors=True))
        self.raiz = Path(directorio)
        self.enterContext(patch.dict(os.environ, {
            "GIOVAL_DATA_DIR": directorio, "GIOVAL_REQUIRE_ACCESS": "false",
            "GIOVAL_ACCESS_PASSWORD": "", "OPENAI_API_KEY": "",
        }))

    def iniciar(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        self.assertFalse(app.exception, app.exception)
        return app

    def editar_tabla(self, app, clave, cambios):
        app.session_state[clave] = {"edited_rows": {"0": cambios}, "added_rows": [], "deleted_rows": []}
        self.recargar(app)

    def recargar(self, app):
        app.run()
        self.assertFalse(app.exception, app.exception)

    def pulsar(self, app, etiqueta):
        next(b for b in app.button if b.label.startswith(etiqueta)).click()
        self.recargar(app)

    def test_preferencias_ux_se_guardan_por_perfil(self):
        usuarios = Usuarios(self.raiz / "usuarios.db")
        ajustes = normalizar_preferencias({"texto": "Muy grande", "contraste": True, "mostrador": True})
        usuarios.guardar_preferencias_ux("harim", ajustes)
        usuarios.guardar_preferencias_ux("giovanna", normalizar_preferencias(None))
        self.assertEqual(Usuarios(self.raiz / "usuarios.db").leer_preferencias_ux("harim"), ajustes)
        self.assertEqual(usuarios.leer_preferencias_ux("giovanna")["texto"], "Normal")

    def test_editar_cotizacion_y_generar_pdf_excel(self):
        biblioteca = Biblioteca(self.raiz / "biblioteca")
        biblioteca.registrar("RESMA-CARTA", "Resma carta", precio_neto=500)
        app = self.iniciar()
        app.button(key="inicio_nueva_cotizacion").click()
        self.recargar(app)
        app.text_area(key="orden_texto").set_value("2 Resma carta")
        self.pulsar(app, "Leer el pedido")
        version = app.session_state["orden_editor_version"]
        self.editar_tabla(app, f"orden_editor_{version}__clp",
                          {"Producto": "Resma carta editada", "Precio unitario": "$1.190"})
        self.pulsar(app, "Guardar pedido")
        self.assertEqual(app.session_state["lineas"][0]["descripcion"], "Resma carta editada")
        version = app.session_state["editor_version"]
        self.editar_tabla(app, f"editor_productos_{version}__clp",
                          {"Cantidad": 3, "Producto": "Segunda descripción", "Precio unitario": "$2.380",
                           "Disponibilidad": "A pedido", "Plazo (días)": 5})
        self.pulsar(app, "Actualizar totales")
        self.pulsar(app, "Siguiente: fotos")
        self.pulsar(app, "Siguiente: revisar")
        if any(c.key == "ok_riesgo" for c in app.checkbox):
            app.checkbox(key="ok_riesgo").check()
            self.recargar(app)
        self.pulsar(app, "Crear el PDF")
        previo = app.session_state["pdf_bytes"]
        resumen = app.session_state["informe"]["resumen"]
        self.assertEqual(resumen["total"], 7140)
        self.assertEqual(resumen["entrega"], "5 días")
        self.assertEqual(resumen["numero"], app.session_state["cot_numero"])
        secciones = app.session_state["informe"]["secciones"]
        self.assertEqual([s[0] for s in secciones], ["Cotización", "Fotografías"])
        _, desde, hasta = secciones[1]
        self.assertEqual(len(PdfReader(BytesIO(fragmento_pdf(previo, desde, hasta))).pages), hasta - desde)
        app.selectbox(key=f"seccion_pdf_{app.session_state['cot_id']}").set_value(2)
        self.recargar(app)
        self.assertEqual(app.session_state["pdf_bytes"], previo)
        self.assertFalse(app.session_state["pdf_confirmado"])
        sin_confirmar = Historial(self.raiz / "biblioteca").obtener(app.session_state["cot_id"])
        self.assertIsNone(sin_confirmar["pdf"])
        if any(c.key == "ok_revision_pdf" for c in app.checkbox):
            app.checkbox(key="ok_revision_pdf").check()
            self.recargar(app)
        self.pulsar(app, "Confirmar versión")
        self.assertEqual(app.session_state["pdf_bytes"], previo)
        pdf = PdfReader(BytesIO(previo))
        texto_pdf = " ".join(p.extract_text() for p in pdf.pages)
        self.assertIn("Segunda descripción", texto_pdf)
        self.assertIn("A pedido, 5 días", texto_pdf)
        registro = Historial(self.raiz / "biblioteca").obtener(app.session_state["cot_id"])
        self.assertEqual(Path(registro["pdf"]).read_bytes(), previo)
        self.assertEqual(registro["total"], 7140)
        self.assertEqual(registro["datos"]["lineas"][0]["plazo_dias"], 5)
        biblioteca.refrescar_si_cambio()
        self.assertEqual(biblioteca.indice["RESMA-CARTA"]["precio_neto"], 500)
        self.assertEqual(Historial(self.raiz / "biblioteca").versiones(registro["id"])[0]["etiqueta"],
                         "PDF confirmado")
        with Path(registro["xlsx"]).open("rb") as archivo:
            wb = load_workbook(archivo, read_only=True)
            try:
                self.assertTrue(any("Segunda descripción · A pedido, 5 días" in fila for fila in wb.active.values))
            finally:
                wb.close()

    def test_revision_galeria_y_regreso_al_historial(self):
        bib = Biblioteca(self.raiz / "biblioteca")
        bib.registrar("TN1060", "Tóner Brother")
        hist = Historial(self.raiz / "biblioteca")
        lineas = [{"cantidad": 1, "descripcion": "Tóner TN1060", "clave_auto": "TN1060"}]
        cot_id, numero = hist.crear({}, lineas, {})
        hist.actualizar(cot_id, datos={"lineas": lineas, "paso": 2})
        app = self.iniciar()
        app.button(key="inicio_ultimo_borrador").click()
        self.recargar(app)
        app.button(key="revision_campo_error_0").click()
        self.recargar(app)
        self.assertEqual(app.session_state["linea_editor"], 0)
        self.assertTrue(any('data-selector=' in e.proto.body for e in app.get("html")))
        app.session_state["vista"] = "Biblioteca"
        self.recargar(app)
        app.button(key="biblioteca_indicador_Sin foto").click()
        self.recargar(app)
        self.assertEqual(app.session_state["p_filtro_estado"], "Sin foto")
        app.session_state["biblioteca_modo"] = "Galería"
        self.recargar(app)
        app.button(key="biblioteca_galeria_TN1060").click()
        self.recargar(app)
        self.assertEqual(app.session_state["p_sel"], "TN1060")
        app.button(key="biblioteca_limpiar").click()
        self.recargar(app)
        self.assertEqual(app.session_state["p_filtro_estado"], "Todos")
        app.session_state["vista"] = "Historial"
        self.recargar(app)
        app.text_input(key="h_texto").set_value(numero)
        self.recargar(app)
        app.button(key=f"edi_{cot_id}").click()
        self.recargar(app)
        app.button(key="editor_regresar_historial").click()
        self.recargar(app)
        self.assertEqual(app.text_input(key="h_texto").value, numero)

    def test_busqueda_editor_y_bandeja_comparten_datos(self):
        bib = Biblioteca(self.raiz / "biblioteca")
        bib.registrar("TN1060", "Tóner Brother", alias="cartucho negro")
        hist = Historial(self.raiz / "biblioteca")
        hist.guardar_cliente({"nombre": "Empresa de Arica", "rut": "12345678-5"})
        lineas = [
            {"cantidad": 1, "descripcion": "Tóner Brother TN1060", "clave_auto": "TN1060",
             "unitario": 1000, "disponibilidad": "A pedido", "plazo_dias": 5},
            {"cantidad": 2, "descripcion": "Resma oficio", "clave_auto": "RESMA", "unitario": 1000,
             "disponibilidad": "En stock"},
        ]
        cot_id, _ = hist.crear({"nombre": "Empresa de Arica"}, lineas, {})
        hist.actualizar(cot_id, datos={"lineas": lineas, "paso": 2})
        app = self.iniciar()
        app.session_state["busqueda_global_popover"] = True
        self.recargar(app)
        app.text_input(key="consulta_global").set_value("12345678-5")
        self.recargar(app)
        self.assertTrue(any("Empresa de Arica" in boton.label for boton in app.button))
        self.assertEqual(buscar("Empresa de Arica", bib, hist, privado=True), ([], [], []))
        self.assertEqual(buscar("cartucho negro", bib, hist)[0], [("TN1060", "Tóner Brother")])
        app.text_input(key="consulta_global").set_value("TN-1060")
        self.recargar(app)
        self.assertTrue(app.button(key="buscar_producto_TN1060"))
        app.button(key=f"buscar_cotizacion_{cot_id}").click()
        self.recargar(app)
        self.assertEqual(app.session_state["paso"], 2)
        self.assertTrue(any("entregas parciales" in e.label for e in app.expander))
        app.text_input(key="linea_nueva").set_value("Carpeta simple")
        self.recargar(app)
        self.assertEqual(len(app.session_state["lineas"]), 3)
        precio = next(c for c in app.text_input if c.label == "Precio de la línea seleccionada")
        precio.set_value("$2.380")
        app.button(key="siguiente_precio").click()
        self.recargar(app)
        self.assertEqual(app.session_state["linea_editor"], 1)
        self.assertEqual(hist.obtener(cot_id)["datos"]["lineas"][0]["unitario"], 2000)
        self.pulsar(app, "← Volver")
        app.checkbox(key="ok_sin_precio").check()
        self.recargar(app)
        for verificacion in app.checkbox:
            if verificacion.key and verificacion.key.startswith("verificar_importacion_"):
                verificacion.check()
                self.recargar(app)
                break
        self.pulsar(app, "Guardar pedido")
        self.assertEqual(app.session_state["lineas"][0]["plazo_dias"], 5)
        self.assertEqual(app.session_state["lineas"][1]["disponibilidad"], "En stock")
        app.session_state["vista"] = "Datos pendientes"
        self.recargar(app)
        next(w for w in app.number_input if w.label == "Costo neto").set_value(6000.0)
        next(w for w in app.selectbox if w.label == "Stock en biblioteca").set_value("En stock")
        self.pulsar(app, "Guardar costo y stock")
        bib.refrescar_si_cambio()
        self.assertEqual(bib.indice["TN1060"]["estado_stock"], "En stock")
        self.assertEqual(bib.indice["TN1060"]["costo_neto"], 6000)
        next(c for c in app.checkbox if c.label.startswith("Este producto no requiere")).check()
        self.recargar(app)
        self.assertTrue(any("No hay productos pendientes" in s.value for s in app.success))

    def test_borrar_primera_linea_conserva_precio_y_plazo_de_la_siguiente(self):
        hist = Historial(self.raiz / "biblioteca")
        lineas = [
            {"cantidad": 1, "descripcion": "Tóner", "clave_auto": "TONER", "unitario": 1000.1,
             "disponibilidad": "En stock"},
            {"cantidad": 2, "descripcion": "Resma", "clave_auto": "RESMA", "unitario": 1000.3,
             "disponibilidad": "A pedido", "plazo_dias": 7},
        ]
        cot_id, _ = hist.crear({}, lineas, {})
        hist.actualizar(cot_id, datos={"lineas": lineas, "paso": 2})
        app = self.iniciar()
        app.session_state["busqueda_global_popover"] = True
        self.recargar(app)
        app.text_input(key="consulta_global").set_value("RESMA")
        self.recargar(app)
        app.button(key=f"buscar_cotizacion_{cot_id}").click()
        self.recargar(app)
        version = app.session_state["editor_version"]
        app.session_state[f"editor_productos_{version}__clp"] = {
            "edited_rows": {}, "added_rows": [], "deleted_rows": [0],
        }
        self.recargar(app)
        guardadas = hist.obtener(cot_id)["datos"]["lineas"]
        self.assertEqual(len(guardadas), 1)
        self.assertEqual(guardadas[0]["unitario"], 1000.3)
        self.assertEqual(guardadas[0]["plazo_dias"], 7)

    def test_deshacer_por_linea_preserva_ediciones_de_otras_y_recupera_duplicados(self):
        hist = Historial(self.raiz / "biblioteca")
        lineas = [
            {"cantidad": 1, "descripcion": "Tóner pedido A", "clave_auto": "TN1060", "unitario": 1000},
            {"cantidad": 2, "descripcion": "Tóner pedido B", "clave_auto": "TN1060", "unitario": 2000},
        ]
        cot_id, _ = hist.crear({}, lineas, {})
        hist.actualizar(cot_id, datos={"lineas": lineas, "paso": 2})
        app = self.iniciar()
        app.session_state["busqueda_global_popover"] = True
        self.recargar(app)
        app.text_input(key="consulta_global").set_value("TN1060")
        self.recargar(app)
        app.button(key=f"buscar_cotizacion_{cot_id}").click()
        self.recargar(app)
        version = app.session_state["editor_version"]
        clave = f"editor_productos_{version}__clp"
        app.session_state[clave] = {
            "edited_rows": {"0": {"Cantidad": 3}, "1": {"Precio unitario": "$3.570"}},
            "added_rows": [], "deleted_rows": [],
        }
        self.recargar(app)
        self.pulsar(app, "Revertir edición · Tóner pedido A")
        guardadas = hist.obtener(cot_id)["datos"]["lineas"]
        self.assertEqual([ln["cantidad"] for ln in guardadas], [1, 2])
        self.assertEqual(guardadas[1]["unitario"], 3000)
        self.assertTrue(all("_editor_id" not in ln for ln in guardadas))
        version = app.session_state["editor_version"]
        app.session_state[f"editor_productos_{version}__clp"] = {
            "edited_rows": {}, "added_rows": [], "deleted_rows": [0],
        }
        self.recargar(app)
        self.pulsar(app, "Recuperar · Tóner pedido A")
        guardadas = hist.obtener(cot_id)["datos"]["lineas"]
        self.assertEqual([ln["descripcion"] for ln in guardadas], ["Tóner pedido A", "Tóner pedido B"])
        self.assertEqual(guardadas[1]["unitario"], 3000)
        version = app.session_state["editor_version"]
        app.session_state[f"editor_productos_{version}__clp"] = {
            "edited_rows": {}, "deleted_rows": [1],
            "added_rows": [{"Cantidad": 1, "Producto": "Carpeta", "Precio unitario": "$1.190"}],
        }
        self.recargar(app)
        self.pulsar(app, "Recuperar · Tóner pedido B")
        guardadas = hist.obtener(cot_id)["datos"]["lineas"]
        self.assertEqual([ln["descripcion"] for ln in guardadas], ["Tóner pedido A", "Tóner pedido B", "Carpeta"])
        self.pulsar(app, "Quitar línea agregada · Carpeta")
        self.assertEqual(len(hist.obtener(cot_id)["datos"]["lineas"]), 2)

        precio = next(c for c in app.text_input if c.label == "Precio de la línea seleccionada")
        precio.set_value("abc")
        plazo = next(c for c in app.number_input if c.label == "Plazo (días)")
        plazo.set_value(5)
        app.button(key="aplicar_precio").click()
        self.recargar(app)
        self.assertTrue(any("pesos enteros" in el.value for el in app.error))
        self.assertEqual(app.text_input(key=precio.key).value, "abc")
        self.assertEqual(app.number_input(key=plazo.key).value, 5)

    def test_historial_compara_y_restaura_una_version(self):
        hist = Historial(self.raiz / "biblioteca")
        cot_id, numero = hist.crear({}, [], {})
        base = {"lineas": [{"cantidad": 2, "descripcion": "Resma original", "unitario": 1000,
                            "clave_auto": "RESMA", "es_producto": True, "incluir": True}]}
        hist.actualizar(cot_id, datos=base, total=2380)
        hist.guardar_version(cot_id, hist.obtener(cot_id)["actualizado"], "Original")
        version = hist.versiones(cot_id)[0]["id"]
        hist.actualizar(cot_id, datos={"lineas": [{**base["lineas"][0], "cantidad": 3}]}, total=3570)
        app = self.iniciar()
        app.session_state["vista"] = "Historial"
        self.recargar(app)
        app.session_state[f"versiones_{cot_id}"] = True
        self.recargar(app)
        app.checkbox(key=f"version_seguro_{cot_id}_{version}").check()
        self.recargar(app)
        app.button(key=f"restaurar_version_{cot_id}").click()
        self.recargar(app)
        self.assertEqual(app.session_state["cot_numero"], numero)
        self.assertEqual(app.session_state["lineas"][0]["cantidad"], 2)
        self.assertEqual(hist.versiones(cot_id)[0]["contenido"]["total"], 3570)

    def test_gestion_comercial_abre_seguimiento_seo_y_proveedores(self):
        bib = Biblioteca(self.raiz / "biblioteca")
        bib.registrar("RESMA-500", "Resma carta 500 hojas", precio_neto=1000)
        hist = Historial(self.raiz / "biblioteca")
        cot_id, _ = hist.crear({"nombre": "Cliente prueba"}, [], {"total": 1190})
        hist.guardar_seguimiento(cot_id, "2026-12-01", None)
        app = self.iniciar()
        app.session_state["vista"] = "Gestión comercial"
        self.recargar(app)
        self.assertTrue(any("Cliente prueba" in el.value for el in app.text_area))
        app.session_state["gestion_vista"] = "Vista SEO"
        self.recargar(app)
        self.assertTrue(any("Resma carta" in el.value for el in app.subheader))
        app.session_state["gestion_vista"] = "Proveedores"
        self.recargar(app)
        self.assertTrue(any(el.label == "Proveedor" for el in app.text_input))

    def test_modo_compartido_pide_clave_y_cierra_sesion(self):
        with patch.dict(os.environ, {"GIOVAL_REQUIRE_ACCESS": "true", "GIOVAL_ACCESS_PASSWORD": "clave-de-prueba"}):
            app = self.iniciar()
            self.assertFalse((self.raiz / "biblioteca" / "cotizador.db").exists())
            app.text_input(key="gioval_clave").set_value("equivocada")
            self.pulsar(app, "Entrar a Gioval")
            self.assertTrue(any("no es correcta" in e.value for e in app.error))
            app.text_input(key="gioval_clave").set_value("clave-de-prueba")
            self.pulsar(app, "Entrar a Gioval")
            self.assertTrue(app.session_state["gioval_autenticado"])
            self.assertTrue(app.button(key="inicio_nueva_cotizacion"))
            app.button(key="gioval_cerrar_sesion").click()
            self.recargar(app)
            self.assertTrue(app.text_input(key="gioval_clave"))

    def test_catalogo_abre_editor_y_seccion_seo(self):
        biblioteca = Biblioteca(self.raiz / "biblioteca")
        biblioteca.registrar("RESMA-CARTA", "Resma carta", precio_neto=1000)
        app = self.iniciar()
        app.session_state["vista"] = "Catálogo PDF"
        self.recargar(app)
        self.assertTrue(app.button(key="catalogo_cliente_generar").disabled)
        app.session_state["catalogo_vista"] = "SEO y GEO"
        self.recargar(app)
        self.assertTrue(any(h.value == "Preparación SEO + GEO" for h in app.subheader))
        self.assertEqual(len(app.get("download_button")), 2)

    def test_abre_borrador_y_pdf_de_version_anterior_del_catalogo(self):
        biblioteca = Biblioteca(self.raiz / "biblioteca")
        biblioteca.registrar("RESMA-CARTA", "Resma carta", precio_neto=1000)
        path = biblioteca.dir / "catalogos_clientes.json"
        archivo = "gioval_catalogo_anterior.pdf"
        pdf = catalogo.generar_pdf([{"clave": "RESMA-CARTA", "nombre": "Resma carta",
                                    "precio_neto": 1000}], "Catálogo anterior", "", "Gioval")
        carpeta = biblioteca.dir / "catalogos_emitidos"
        carpeta.mkdir()
        (carpeta / archivo).write_bytes(pdf)
        catalogo._json_guardar(path, {
            "selecciones": {"Junio": ["RESMA-CARTA"]},
            "emitidos": [{"archivo": archivo, "nombre": "Catálogo anterior", "claves": ["RESMA-CARTA"]}],
        })
        app = self.iniciar()
        app.session_state["vista"] = "Catálogo PDF"
        self.recargar(app)
        app.selectbox(key="catalogo_cliente_seleccion").set_value("Junio")
        self.recargar(app)
        self.assertIn("RESMA-CARTA", next(w.value for w in app.multiselect if w.label == "Productos incluidos"))
        app.session_state["catalogo_vista"] = "Versiones emitidas"
        self.recargar(app)
        self.assertEqual(app.selectbox(key="catalogo_archivo").value, archivo)
        self.assertEqual(len(app.get("download_button")), 1)

    def test_precios_y_campos_seo_se_guardan_desde_biblioteca(self):
        biblioteca = Biblioteca(self.raiz / "biblioteca")
        biblioteca.registrar("TN1060", "Tóner TN1060", precio_neto=10000)
        app = self.iniciar()
        app.session_state["vista"] = "Biblioteca"
        self.recargar(app)
        app.session_state["edit_producto_TN1060"] = True
        self.recargar(app)
        for campo in app.text_input:
            if campo.label == "Título SEO":
                campo.set_value("Tóner TN1060 para impresoras | Gioval")
        self.pulsar(app, "Guardar producto")
        biblioteca.refrescar_si_cambio()
        self.assertEqual(biblioteca.indice["TN1060"]["seo_titulo"], "Tóner TN1060 para impresoras | Gioval")
        self.assertEqual(biblioteca.indice["TN1060"]["precio_neto"], 10000)
