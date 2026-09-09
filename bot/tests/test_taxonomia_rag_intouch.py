"""La taxonomía con que se clasifica cada chunk del RAG.

Importa porque el clasificador es un LLM y la lista de categorías es todo lo
que tiene: una taxonomía de otro rubro le hace poner "financiamiento" a un
párrafo sobre Contact Center, y después el filtro por categoría no encuentra
nada.
"""
from django.test import SimpleTestCase


class CategoriasTest(SimpleTestCase):
    def test_no_quedan_categorias_de_autos(self):
        from bot.rag.indexador import CATEGORIAS_RAG

        crudo = " ".join(CATEGORIAS_RAG).lower()
        for palabra in ("vehículo", "vehiculo", "financiamiento", "taller",
                        "usados", "patente"):
            self.assertNotIn(palabra, crudo, palabra)

    def test_estan_las_categorias_b2b(self):
        from bot.rag.indexador import CATEGORIAS_RAG

        self.assertEqual(set(CATEGORIAS_RAG), {
            "soluciones", "modelos_operacion", "canales", "analitica",
            "integraciones", "datos_y_seguridad", "empresa", "otro",
        })

    def test_el_prompt_de_clasificacion_no_habla_de_una_concesionaria(self):
        from bot.rag.indexador import PROMPT_CLASIFICACION

        self.assertNotIn("concesionaria", PROMPT_CLASIFICACION.lower())
        self.assertIn("InTouch", PROMPT_CLASIFICACION)

    def test_el_prompt_de_clasificacion_lista_las_categorias(self):
        # Si el prompt y la constante divergen, el LLM devuelve una categoría
        # que el filtro no conoce y el chunk queda inalcanzable.
        from bot.rag.indexador import CATEGORIAS_RAG, PROMPT_CLASIFICACION

        for categoria in CATEGORIAS_RAG:
            self.assertIn(categoria, PROMPT_CLASIFICACION, categoria)


class ExtractorDeScrapingTest(SimpleTestCase):
    def test_el_prompt_extrae_soluciones_y_no_vehiculos(self):
        from bot.scraping.extractor import EXTRACTOR_PROMPT

        self.assertNotIn("vehículo", EXTRACTOR_PROMPT.lower())
        self.assertNotIn("precio", EXTRACTOR_PROMPT.lower())
        self.assertIn("InTouch", EXTRACTOR_PROMPT)
