"""El golden set es el único número objetivo que este bot va a tener.

Sin un recall de partida anotado, "el RAG está mejor" es una impresión
(biblia §I.4 paso 17). Se siembra por migración para que exista desde el
primer deploy y no "cuando haya tiempo" -- que es lo que no pasó en Cavem.
"""
from django.test import TestCase

from bot.rag_eval.models import PreguntaEvaluacionRag


class GoldenSetTest(TestCase):
    def test_hay_al_menos_quince_preguntas(self):
        # Menos que eso no distingue una mejora real del ruido.
        self.assertGreaterEqual(PreguntaEvaluacionRag.objects.count(), 15)

    def test_no_quedan_preguntas_de_otro_cliente(self):
        crudo = " ".join(
            PreguntaEvaluacionRag.objects.values_list("query", flat=True)).lower()
        for palabra in ("auto", "vehículo", "taller", "camioneta", "financiamiento"):
            self.assertNotIn(palabra, crudo, palabra)

    def test_cada_pregunta_declara_su_fuente_esperada(self):
        # Sin fuente esperada no hay recall que medir.
        sin_fuente = PreguntaEvaluacionRag.objects.filter(fuente_esperada="")
        self.assertFalse(list(sin_fuente))

    def test_cubre_todas_las_categorias_del_conocimiento(self):
        fuentes = " ".join(
            PreguntaEvaluacionRag.objects.values_list("fuente_esperada", flat=True))
        for doc in ("soluciones", "modelos-de-operacion", "canales",
                    "analitica-y-calidad", "integraciones", "datos-y-seguridad",
                    "sobre-intouch"):
            self.assertIn(doc, fuentes, doc)

    def test_hay_preguntas_escritas_como_las_escribiria_un_contacto(self):
        # El retrieval híbrido existe porque el coseno solo falla con la forma
        # en que la gente escribe de verdad: siglas, abreviaturas, minúsculas.
        queries = list(PreguntaEvaluacionRag.objects.values_list("query", flat=True))
        self.assertTrue(any(q.lower() == q for q in queries))
        self.assertTrue(any("?" not in q for q in queries))
