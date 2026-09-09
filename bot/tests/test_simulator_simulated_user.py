from unittest.mock import patch

from django.test import SimpleTestCase

from bot.simulator.simulated_user import construir_system_prompt, crear_cliente_simulado


class ConstruirSystemPromptTest(SimpleTestCase):
    def test_incluye_persona_y_objetivo(self):
        prompt = construir_system_prompt(persona="Cliente ansioso", objetivo="Quiere agendar un test drive")
        self.assertIn("Cliente ansioso", prompt)
        self.assertIn("Quiere agendar un test drive", prompt)

    def test_incluye_la_instruccion_de_datos_ficticios(self):
        self.assertIn("ficticios", construir_system_prompt(persona="x", objetivo="y"))

    def test_incluye_la_instruccion_de_parafrasear(self):
        self.assertIn("propias palabras", construir_system_prompt(persona="x", objetivo="y"))


class CrearClienteSimuladoTest(SimpleTestCase):
    @patch("bot.simulator.simulated_user.create_llm_simulated_user")
    def test_usa_el_modelo_configurado_en_settings(self, mock_create):
        with self.settings(SIMULATED_USER_MODEL="google_genai:gemini-3.5-flash-lite"):
            crear_cliente_simulado(persona="x", objetivo="y")

        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs["model"], "google_genai:gemini-3.5-flash-lite")
