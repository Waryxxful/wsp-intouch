"""La taxonomía con que se clasifica cada chunk del RAG.

Importa porque el clasificador es un LLM y la lista de categorías es todo lo
que tiene: una taxonomía de otro rubro le hace poner "financiamiento" a un
párrafo sobre Contact Center, y después el filtro por categoría no encuentra
nada.
"""
from django.test import SimpleTestCase, override_settings


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


class ExtractCatalogNoImplementadoTest(SimpleTestCase):
    """El pipeline de scraping estructurado (extract_catalog) sigue devolviendo
    servicios/sucursales/vehiculos -- la forma automotriz heredada -- y
    EXTRACTOR_PROMPT ya no le pide esa forma al LLM. Adaptarlo a B2B es
    trabajo de diseño propio (ver el spec, §12.6), fuera de esta task. Sin
    este guard, alguien que configure una ScrapingSource para este bot se
    topa con un ValueError que suena a falla del proveedor ("el LLM no
    devolvio JSON valido") en vez de la verdad ("esto no esta implementado
    para este vertical").
    """

    @override_settings(CLIENTE_ACTIVO="intouch")
    def test_bajo_el_cliente_real_de_este_bot_levanta_notimplementederror(self):
        from bot.scraping.extractor import extract_catalog

        with self.assertRaises(NotImplementedError) as ctx:
            extract_catalog([{"url": "https://intouch.cl", "texto": "algo"}])

        mensaje = str(ctx.exception).lower()
        self.assertIn("no esta adaptado", mensaje)
        self.assertIn("cargar_conocimiento_rag", mensaje)

    @override_settings(CLIENTE_ACTIVO="renault")
    def test_bajo_un_cliente_automotriz_heredado_no_se_activa(self):
        # La suite heredada (test_scraping_extractor.py) corre con
        # CLIENTE_ACTIVO=renault y sigue llamando a extract_catalog de
        # verdad -- el guard no debe interponerse en ese camino.
        from unittest.mock import AsyncMock, MagicMock, patch

        from bot.scraping.extractor import extract_catalog

        with patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock) as mock_get_llm, \
                patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock) as mock_invoke:
            mock_get_llm.return_value = MagicMock()
            mock_invoke.return_value = '{"servicios": [], "sucursales": []}'
            catalogo = extract_catalog([{"url": "https://x.cl", "texto": "algo"}])

        self.assertEqual(catalogo["servicios"], [])
