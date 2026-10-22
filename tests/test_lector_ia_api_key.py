"""Pruebas locales para guardar de forma consistente la clave de OpenAI."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lector_ia


class GuardarApiKeyTests(unittest.TestCase):
    def test_saves_key_and_can_read_it_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                path = lector_ia.guardar_api_key(tmp, "sk-test-demo")
                self.assertEqual(path, Path(tmp) / ".env")
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
