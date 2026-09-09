from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase


class EvaluarRelevanciaTest(TestCase):
    @patch("bot.rag_eval.judge.init_chat_model")
    def test_parsea_el_puntaje_de_la_respuesta_del_juez(self, mock_init_chat_model):
        from bot.rag_eval.judge import evaluar_relevancia
        mock_modelo = MagicMock()
        mock_modelo.invoke.return_value = MagicMock(
            content='{"relevancia": {"razonamiento": "cubre la pregunta", "valor": 4}}'
        )
        mock_init_chat_model.return_value = mock_modelo

        resultado = evaluar_relevancia("cuanto dura la garantia", ["la garantia dura 36 meses"])

        self.assertEqual(resultado, 4)

    @patch("bot.rag_eval.judge.init_chat_model")
    def test_respuesta_no_json_devuelve_none(self, mock_init_chat_model):
        from bot.rag_eval.judge import evaluar_relevancia
        mock_modelo = MagicMock()
        mock_modelo.invoke.return_value = MagicMock(content="esto no es json")
        mock_init_chat_model.return_value = mock_modelo

        resultado = evaluar_relevancia("query", ["chunk"])
        self.assertIsNone(resultado)

    def test_sin_chunks_devuelve_none_sin_llamar_al_juez(self):
        from bot.rag_eval.judge import evaluar_relevancia
        with patch("bot.rag_eval.judge.init_chat_model") as mock_init_chat_model:
            resultado = evaluar_relevancia("query", [])
        self.assertIsNone(resultado)
        mock_init_chat_model.assert_not_called()

    @patch("bot.rag_eval.judge.init_chat_model")
    def test_solo_fence_de_apertura_sin_contenido_devuelve_none_sin_reventar(self, mock_init_chat_model):
        # Bug real: "```" (sin newline despues) hace que
        # bruto.split("\n", 1)[1] levante IndexError, que escapaba del
        # except mas angosto que existia antes de este fix -- rompiendo el
        # contrato "nunca levanta" documentado en el docstring.
        from bot.rag_eval.judge import evaluar_relevancia
        mock_modelo = MagicMock()
        mock_modelo.invoke.return_value = MagicMock(content="```")
        mock_init_chat_model.return_value = mock_modelo

        resultado = evaluar_relevancia("query", ["chunk"])
        self.assertIsNone(resultado)


from io import StringIO

from django.core.management import call_command

from bot.rag_eval.models import PreguntaEvaluacionRag, ResultadoEvaluacionRag


class EvaluarRagCommandTest(TestCase):
    @patch("bot.rag_eval.management.commands.evaluar_rag.evaluar_relevancia")
    @patch("bot.rag_eval.management.commands.evaluar_rag._rerankear", new_callable=AsyncMock)
    @patch("bot.rag_eval.management.commands.evaluar_rag._buscar_en_supabase", new_callable=AsyncMock)
    def test_recall_ok_si_la_fuente_esperada_aparece(self, mock_buscar, mock_rerank, mock_juez):
        PreguntaEvaluacionRag.objects.all().delete()
        pregunta = PreguntaEvaluacionRag.objects.create(
            query="cuanto dura la garantia", fuente_esperada="https://renault.cl/garantia/",
        )
        chunk = {"contenido": "modalidad hibrida", "fuente_url": "https://renault.cl/garantia/",
                 "categoria": "modelos_operacion"}
        mock_buscar.return_value = [chunk]
        mock_rerank.return_value = [chunk]
        mock_juez.return_value = 5

        out = StringIO()
        call_command("evaluar_rag", stdout=out)

        resultado = ResultadoEvaluacionRag.objects.get(pregunta=pregunta)
        self.assertTrue(resultado.recall_ok)
        self.assertEqual(resultado.relevancia_score, 5)
        self.assertIn("recall@k", out.getvalue())
        # El juez se llama desde handle() (sync), no desde dentro de
        # _evaluar_una (la coroutine) -- ver docstring de _evaluar_una.
        mock_juez.assert_called_once_with(pregunta.query, [chunk["contenido"]])

    @patch("bot.rag_eval.management.commands.evaluar_rag.evaluar_relevancia")
    @patch("bot.rag_eval.management.commands.evaluar_rag._rerankear", new_callable=AsyncMock)
    @patch("bot.rag_eval.management.commands.evaluar_rag._buscar_en_supabase", new_callable=AsyncMock)
    def test_recall_falso_si_la_fuente_esperada_no_aparece(self, mock_buscar, mock_rerank, mock_juez):
        PreguntaEvaluacionRag.objects.all().delete()
        pregunta = PreguntaEvaluacionRag.objects.create(
            query="cuanto dura la garantia", fuente_esperada="https://renault.cl/garantia/",
        )
        mock_buscar.return_value = []
        mock_rerank.return_value = []
        mock_juez.return_value = None

        call_command("evaluar_rag", stdout=StringIO())

        resultado = ResultadoEvaluacionRag.objects.get(pregunta=pregunta)
        self.assertFalse(resultado.recall_ok)
        self.assertIsNone(resultado.relevancia_score)

    def test_pregunta_inactiva_no_se_evalua(self):
        PreguntaEvaluacionRag.objects.all().delete()
        PreguntaEvaluacionRag.objects.create(
            query="inactiva", fuente_esperada="https://x/", activo=False,
        )
        call_command("evaluar_rag", stdout=StringIO())
        self.assertEqual(ResultadoEvaluacionRag.objects.count(), 0)
