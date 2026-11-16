import importlib
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from usuarios import Usuarios


class PerfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Usuarios(Path(self.tmp.name) / "usuarios.db")
        self.db.crear("harim", "Harim", "admin", "Temporal-prueba-1234")
        self.db.crear("giovanna", "Giovanna", "trabajador", "Temporal-prueba-5678")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cambio_clave_revoca_y_no_guarda_texto(self):
        token = self.db.entrar("HARIM", "Temporal-prueba-1234")
        self.assertEqual(self.db.identidad(token)["rol"], "admin")
        with self.assertRaises(ValueError):
            self.db.cambiar_clave(token, "Temporal-prueba-1234", "123456")
        with self.assertRaises(ValueError):
            self.db.cambiar_clave(token, "Temporal-prueba-1234", "abcdef")
        self.db.cambiar_clave(token, "Temporal-prueba-1234", "Abc123")
        self.assertIsNone(self.db.identidad(token))
        self.assertIsNone(self.db.entrar("harim", "Temporal-prueba-1234"))
        self.assertNotIn(b"Abc123", self.db.archivo.read_bytes())
        self.assertIsNotNone(self.db.entrar("harim", "Abc123"))

    def test_bloqueo_intentos(self):
        for _ in range(5):
            self.assertIsNone(self.db.entrar("harim", "incorrecta"))
        self.assertIsNone(self.db.entrar("harim", "Temporal-prueba-1234"))

    def test_solo_administrador_puede_revocar_otra_cuenta(self):
        h = self.db.entrar("harim", "Temporal-prueba-1234")
        self.db.cambiar_clave(h, "Temporal-prueba-1234", "Nueva1234")
        h = self.db.entrar("harim", "Nueva1234")
        g = self.db.entrar("giovanna", "Temporal-prueba-5678")
        with self.assertRaises(PermissionError):
            self.db.administrar(g, "harim", activo=False)
        self.db.administrar(h, "giovanna", activo=False)
        self.assertIsNone(self.db.identidad(g))
        self.assertIsNone(self.db.entrar("giovanna", "Temporal-prueba-5678"))


class BibliotecaPerfilesTests(unittest.TestCase):
    def test_datos_documentos_y_concurrencia(self):
        import historial
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, GIOVAL_PROFILES="true", GIOVAL_REQUIRE_ACCESS="true"):
            hs = importlib.reload(historial)
            try:
                a, b = hs.Historial(tmp), hs.Historial(tmp)
                cli = {"nombre": "Cliente preservado", "rut": "123", "email": "prueba@example.invalid"}
                a.guardar_cliente(cli)
                ident, _ = a.crear(cli, [], {"total": 100})
                version = a.obtener(ident)["actualizado"]
                a.guardar_archivos(ident, pdf=b"documento-original", version_esperada=version)
                ruta = Path(b.obtener(ident)["pdf"])
                self.assertEqual(ruta.read_bytes(), b"documento-original")
                self.assertEqual(b.obtener(ident)["cliente"], cli["nombre"])
                self.assertEqual(len(b.clientes()), 1)
                with self.assertRaises(hs.ConflictoActualizacion):
                    b.guardar_archivos(ident, pdf=b"sobrescritura", version_esperada=version)
                with self.assertRaises(hs.ConflictoActualizacion):
                    b.actualizar_si_version(ident, version, cliente="obsoleto")
                version = b.obtener(ident)["actualizado"]
                b.renumerar(ident, "NUEVO-NUMERO", version_esperada=version)
                self.assertTrue(Path(b.obtener(ident)["pdf"]).is_file())
                def crear(_):
                    return hs.Historial(tmp).crear(cli, [], {})[1]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    numeros = list(pool.map(crear, range(12)))
                self.assertEqual(len(set(numeros)), 12)
            finally:
                os.environ.pop("GIOVAL_PROFILES", None)
                importlib.reload(historial)

    def test_ui_perfiles_no_expone_datos_antes_de_cambiar_clave(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "usuarios.db"
            db = Usuarios(archivo)
            db.crear("harim", "Harim", "admin", "Temporal-prueba-1234")
            app = "import streamlit as st; import acceso; acceso.exigir_perfil(); st.title('DATOS PROTEGIDOS')"
            with patch.dict(os.environ, GIOVAL_USERS_DB=str(archivo)):
                at = AppTest.from_string(app).run()
                self.assertFalse(at.exception)
                at.text_input(key="acceso_usuario").set_value("harim")
                at.text_input(key="acceso_clave").set_value("Temporal-prueba-1234")
                at.button(key="acceso_ingresar").click().run()
                self.assertFalse(at.exception)
                self.assertFalse(any(t.value == "DATOS PROTEGIDOS" for t in at.title))
                self.assertTrue(any("Hola, Harim" == t.value for t in at.title))
                at.text_input(key="clave_actual").set_value("Temporal-prueba-1234")
                at.text_input(key="clave_nueva").set_value("Nueva1234")
                at.text_input(key="clave_repetida").set_value("Nueva1234")
                at.button(key="guardar_mi_clave").click().run()
                at.text_input(key="acceso_usuario").set_value("harim")
                at.text_input(key="acceso_clave").set_value("Nueva1234")
                at.button(key="acceso_ingresar").click().run()
                self.assertFalse(at.exception)
                self.assertTrue(any(t.value == "DATOS PROTEGIDOS" for t in at.title))


if __name__ == "__main__":
    unittest.main()
