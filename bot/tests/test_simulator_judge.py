import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from bot.simulator.judge import CRITERIOS_GENERICOS, _construir_prompt, _parsear_respuesta, evaluar_conversacion


class ConstruirPromptTest(SimpleTestCase):
    def test_incluye_el_transcript_y_los_nombres_de_criterio(self):
        prompt = _construir_prompt("Cliente: hola\nBot: hola", CRITERIOS_GENERICOS)
        self.assertIn("Cliente: hola", prompt)
        self.assertIn("no_repregunta_dato_conocido", prompt)


class ParsearRespuestaTest(SimpleTestCase):
    def test_parsea_json_plano(self):
        bruto = '{"a": {"razonamiento": "x", "valor": true}}'
        self.assertEqual(_parsear_respuesta(bruto), {"a": {"razonamiento": "x", "valor": True}})

    def test_parsea_json_envuelto_en_bloque_de_codigo(self):
        bruto = '```json\n{"a": {"valor": true}}\n```'
        self.assertEqual(_parsear_respuesta(bruto), {"a": {"valor": True}})


class EvaluarConversacionTest(SimpleTestCase):
    @patch("bot.simulator.judge.init_chat_model")
    def test_hace_un_unico_llamado_al_modelo_configurado(self, mock_init):
        mock_modelo = MagicMock()
        mock_modelo.invoke.return_value = MagicMock(content=json.dumps({
            "no_repregunta_dato_conocido": {"razonamiento": "ok", "valor": True},
            "avanza_hacia_objetivo": {"razonamiento": "ok", "valor": 4},
            "tono_apropiado_whatsapp": {"razonamiento": "ok", "valor": 5},
            "criterio_escenario_1": {"razonamiento": "ok", "valor": True},
        }))
        mock_init.return_value = mock_modelo

        with self.settings(JUDGE_MODEL="google_genai:gemini-3.6-flash"):
            resultado, nombres_criterios = evaluar_conversacion("Cliente: hola\nBot: hola", ["el bot debe saludar"])

        mock_init.assert_called_once_with("google_genai:gemini-3.6-flash")
        self.assertEqual(mock_modelo.invoke.call_count, 1)
        self.assertIn("criterio_escenario_1", resultado)
        # `nombres_criterios` es la lista COMPLETA de nombres esperados
        # (genericos + de escenario), independiente de lo que el juez haya
        # respondido -- el caller la usa para detectar respuestas
        # incompletas (Hallazgo 4).
        self.assertEqual(
            nombres_criterios,
            ["no_repregunta_dato_conocido", "avanza_hacia_objetivo", "tono_apropiado_whatsapp", "criterio_escenario_1"],
        )

    @patch("bot.simulator.judge.init_chat_model")
    def test_devuelve_dict_vacio_si_la_respuesta_no_es_json_valido(self, mock_init):
        mock_modelo = MagicMock()
        mock_modelo.invoke.return_value = MagicMock(content="no es json")
        mock_init.return_value = mock_modelo

        resultado, nombres_criterios = evaluar_conversacion("transcript", [])

        self.assertEqual(resultado, {})
        # Aun con parseo fallido, se devuelven los nombres esperados: el
        # caller los necesita igual para poder registrar el score
        # "juez_respondio=0.0" del Hallazgo 10.
        self.assertEqual(
            nombres_criterios,
            ["no_repregunta_dato_conocido", "avanza_hacia_objetivo", "tono_apropiado_whatsapp"],
        )

    @patch("bot.simulator.judge.init_chat_model")
    def test_extrae_y_parsea_json_de_una_respuesta_en_bloques_de_contenido(self, mock_init):
        mock_modelo = MagicMock()
        # Forma real observada contra Gemini 3.6 Flash (JUDGE_MODEL por
        # defecto) en la primera corrida real del simulador: `content` viene
        # como una lista de content blocks, con el JSON util adentro del
        # bloque de texto (envuelto en fences de markdown) y metadata extra
        # (ej. "extras") que no debe formar parte del texto parseado.
        mock_modelo.invoke.return_value = MagicMock(content=[{
            "type": "text",
            "text": '```json\n{"criterio_escenario_1": {"razonamiento": "ok", "valor": true}}\n```',
            "extras": {"signature": "irrelevante"},
        }])
        mock_init.return_value = mock_modelo

        resultado, _ = evaluar_conversacion("transcript", ["algun criterio"])

        self.assertEqual(resultado, {"criterio_escenario_1": {"razonamiento": "ok", "valor": True}})

    @patch("bot.simulator.judge.init_chat_model")
    def test_devuelve_dict_vacio_si_los_bloques_de_contenido_no_traen_json_valido(self, mock_init):
        mock_modelo = MagicMock()
        mock_modelo.invoke.return_value = MagicMock(content=[{"type": "text", "text": "no es json"}])
        mock_init.return_value = mock_modelo

        resultado, _ = evaluar_conversacion("transcript", [])

        self.assertEqual(resultado, {})
