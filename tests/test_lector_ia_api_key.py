"""Pruebas locales para guardar de forma consistente la clave de OpenAI."""
import os
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import lector_ia


class GuardarApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(lector_ia, "sys", SimpleNamespace(platform="linux")))

    def registro_windows(self, sistema=None, usuario=None):
        valores = {"sistema": sistema, "usuario": usuario}
        registro = MagicMock(HKEY_LOCAL_MACHINE="sistema", HKEY_CURRENT_USER="usuario")
        registro.OpenKey.side_effect = lambda raiz, ruta: nullcontext((raiz, ruta))

        def leer(clave, nombre):
            self.assertEqual(nombre, "OPENAI_API_KEY")
            valor = valores[clave[0]]
            if isinstance(valor, OSError):
                raise valor
            if valor is None:
                raise FileNotFoundError
            return valor, 1

        registro.QueryValueEx.side_effect = leer
        self.enterContext(patch.dict("sys.modules", {"winreg": registro}))
        self.enterContext(patch.object(lector_ia, "sys", SimpleNamespace(platform="win32")))
        return valores

    def test_system_key_overrides_stale_process_user_and_dotenv(self):
        self.registro_windows("  sk-sistema-vigente  ", "sk-usuario-vieja")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, OPENAI_API_KEY="sk-proceso-vieja"):
            Path(tmp, ".env").write_text("OPENAI_API_KEY=sk-archivo-vieja\n", encoding="utf-8")
            self.assertEqual(lector_ia.buscar_api_key(tmp),
                             ("sk-sistema-vigente", "variable del sistema de Windows"))
            self.assertEqual(lector_ia.cargar_api_key(tmp), "sk-sistema-vigente")

    def test_user_key_is_used_when_system_key_is_empty(self):
        self.registro_windows("  ", "sk-usuario-vigente")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, OPENAI_API_KEY="sk-heredada"):
            self.assertEqual(lector_ia.buscar_api_key(tmp),
                             ("sk-usuario-vigente", "variable del usuario de Windows"))

    def test_registry_access_denied_falls_back_to_process(self):
        self.registro_windows(PermissionError(), FileNotFoundError())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, OPENAI_API_KEY="sk-proceso"):
            self.assertEqual(lector_ia.buscar_api_key(tmp), ("sk-proceso", "variable de entorno"))

    def test_changed_windows_key_is_read_before_each_request(self):
        valores = self.registro_windows("sk-nueva-1")
        respuesta = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "productos": [{"cantidad": 2, "descripcion": "Papel"}],
            "resultados": [{"indice": 0, "clave": "PAPEL", "certeza": "alta"}],
        })))])
        with patch.dict(os.environ, OPENAI_API_KEY="sk-proceso-vieja"), patch("openai.OpenAI") as sdk:
            sdk.return_value.chat.completions.create.return_value = respuesta
            self.assertEqual(lector_ia.leer_texto_orden_ia("2 Papel", "sk-ajustes-vieja")[0]["cantidad"], 2)
            self.assertEqual(sdk.call_args.kwargs["api_key"], "sk-nueva-1")
            valores["sistema"] = "sk-nueva-2"
            resultados = lector_ia.entender_productos(
                ["Papel"], "sk-sesion-vieja", {"PAPEL": {"nombre": "Papel"}},
            )
            self.assertEqual(resultados[0]["clave"], "PAPEL")
            self.assertEqual(sdk.call_args.kwargs["api_key"], "sk-nueva-2")

    def test_process_key_has_priority_on_other_platforms(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, OPENAI_API_KEY="sk-proceso"):
            with patch.object(lector_ia, "sys", SimpleNamespace(platform="linux")):
                Path(tmp, ".env").write_text("OPENAI_API_KEY=sk-archivo\n", encoding="utf-8")
                self.assertEqual(lector_ia.buscar_api_key(tmp), ("sk-proceso", "variable de entorno"))
                with patch("openai.OpenAI") as sdk:
                    lector_ia.cliente_openai("sk-ajustes", 12)
                    self.assertEqual(sdk.call_args.kwargs["api_key"], "sk-proceso")

    def test_dotenv_fallback_handles_export_and_whitespace(self):
        self.registro_windows()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, OPENAI_API_KEY="   "):
            Path(tmp, ".env").write_text('export OPENAI_API_KEY="sk-archivo"\n', encoding="utf-8")
            self.assertEqual(lector_ia.buscar_api_key(tmp), ("sk-archivo", "archivo .env"))

    def test_saves_key_and_can_read_it_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                path = lector_ia.guardar_api_key(tmp, "sk-test-demo")
                self.assertEqual(path, (Path(tmp) / ".env").resolve())
                key, origin = lector_ia.buscar_api_key(tmp)
                self.assertEqual(key, "sk-test-demo")
                self.assertEqual(origin, "archivo .env")

    def test_preserves_other_environment_settings_and_replaces_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "# Configuración local\nOTRA_VARIABLE=valor\n"
                "OPENAI_API_KEY=vieja\nexport OPENAI_API_KEY='duplicada'\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                lector_ia.guardar_api_key(tmp, "sk-nueva")
                key, origin = lector_ia.buscar_api_key(tmp)
            contenido = env.read_text(encoding="utf-8")
            self.assertEqual(key, "sk-nueva")
            self.assertEqual(origin, "archivo .env")
            self.assertIn("# Configuración local", contenido)
            self.assertIn("OTRA_VARIABLE=valor", contenido)
            self.assertEqual(contenido.count("OPENAI_API_KEY="), 1)
            self.assertNotIn("vieja", contenido)
            self.assertNotIn("duplicada", contenido)

    def test_rejects_empty_or_multiline_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                lector_ia.guardar_api_key(tmp, " ")
            with self.assertRaises(ValueError):
                lector_ia.guardar_api_key(tmp, "clave\nOTRA=variable")
            self.assertFalse((Path(tmp) / ".env").exists())

    def test_replaces_key_when_saved_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                lector_ia.guardar_api_key(tmp, "sk-antigua")
                lector_ia.guardar_api_key(tmp, "sk-actual")
                key, _ = lector_ia.buscar_api_key(tmp)
            self.assertEqual(key, "sk-actual")


if __name__ == "__main__":
    unittest.main()
