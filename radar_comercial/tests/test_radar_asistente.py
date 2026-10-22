"""Pruebas del asistente; no realizan llamadas reales a OpenAI."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from radar_comercial.asistente import GUIAS_RAPIDAS, buscar_api_key, responder_con_ia, responder_local
except ModuleNotFoundError:
    from asistente import GUIAS_RAPIDAS, buscar_api_key, responder_con_ia, responder_local


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text="Respuesta de prueba")


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


class AsistenteRadarTests(unittest.TestCase):
    def test_guides_and_local_answer_work_without_ai(self):
        self.assertIn("Guardar evaluación compartida", GUIAS_RAPIDAS["Evaluar rentabilidad"])
        self.assertIn("XLSX o CSV", GUIAS_RAPIDAS["Importar Excel o CSV"])
        answer = responder_local("¿Por qué sale el margen incompleto?", "Evaluar rentabilidad")
        self.assertIn("netos", answer.lower())

    def test_environment_key_has_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text("OPENAI_API_KEY=archivo\n", encoding="utf-8")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "entorno"}):
                key, source = buscar_api_key(tmp)
        self.assertEqual((key, source), ("entorno", "variable de entorno"))

    def test_dotenv_key_can_be_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text("export OPENAI_API_KEY='local'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                key, source = buscar_api_key(tmp)
        self.assertEqual((key, source), ("local", "archivo .env"))

    def test_ia_receives_only_chat_and_does_not_store_response(self):
        client = FakeClient()
        answer = responder_con_ia("¿Cómo guardo?", "Evaluar rentabilidad", "fake", cliente=client, modelo="gpt-6-luna")
        self.assertEqual(answer, "Respuesta de prueba")
        self.assertEqual(client.responses.kwargs["model"], "gpt-6-luna")
        self.assertFalse(client.responses.kwargs["store"])
        self.assertIn("Evaluar rentabilidad", client.responses.kwargs["instructions"])
        self.assertEqual(client.responses.kwargs["input"][-1]["content"], "¿Cómo guardo?")
        self.assertNotIn("catalogo", str(client.responses.kwargs["input"]).lower())


if __name__ == "__main__":
    unittest.main()
