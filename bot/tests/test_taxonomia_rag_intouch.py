"""La taxonomía con que se clasifica cada chunk del RAG.

Importa porque el clasificador es un LLM y la lista de categorías es todo lo
que tiene: una taxonomía de otro rubro le hace poner "financiamiento" a un
párrafo sobre Contact Center, y después el filtro por categoría no encuentra
nada.
"""
import re
from pathlib import Path

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

    def test_los_prompts_del_indexador_no_vosean(self):
        # El prompt global prohíbe vosear, así que el corpus no puede vosear:
        # el modelo imita su corpus, no sólo lo obedece. `_PROMPT_HECHOS_DOCUMENTO`
        # traía "inferilo del título" y "a cada hecho asignale" en las líneas
        # nuevas, y es un camino que sí se usa (cada documento indexado).
        from bot.rag.indexador import _PROMPT_HECHOS_DOCUMENTO, PROMPT_CLASIFICACION

        for prompt in (_PROMPT_HECHOS_DOCUMENTO, PROMPT_CLASIFICACION):
            texto = prompt.lower()
            for forma in ("inferilo", "asignale", "asignalo", "clasificalo",
                          "tenes", "podes", "debes vos", "reescribi ",
                          "agrupa vos", "separa vos"):
                with self.subTest(forma=forma):
                    self.assertNotIn(forma, texto)

    def test_el_prompt_de_clasificacion_lista_las_categorias(self):
        # Si el prompt y la constante divergen, el LLM devuelve una categoría
        # que el filtro no conoce y el chunk queda inalcanzable.
        from bot.rag.indexador import CATEGORIAS_RAG, PROMPT_CLASIFICACION

        for categoria in CATEGORIAS_RAG:
            self.assertIn(categoria, PROMPT_CLASIFICACION, categoria)


class NingunFixtureUsaUnaCategoriaQueNoExisteTest(SimpleTestCase):
    """El invariante que este proyecto ya pagó una vez.

    Cuando la taxonomía pasó de automotriz a B2B quedaron ~20 fixtures con las
    categorías viejas (`vehiculo_specs`, `precio_financiamiento`, `garantia`,
    `sucursales`). NINGUNO fallaba, y por eso pasaron la review anterior: el
    chunk se inserta igual, el rerank ordena igual, y una categoría que el
    filtro no conoce no levanta nada. Lo que sí dejaron fue dos clases
    mentirosas -- `HechosDeDocumentoTest` pasaba por el camino de coerción a
    "otro" creyendo probar el camino feliz, y otro test afirmaba que una
    categoría imposible llega a Supabase.

    Este test es lo que faltaba: sin él, la próxima vez que la taxonomía cambie
    la deriva vuelve a entrar en silencio.

    Se lee el CÓDIGO FUENTE y no los objetos porque los fixtures viven dentro
    de métodos y mocks: no hay forma de enumerarlos por introspección sin
    correrlos. Sólo se mira la forma de diccionario -- la clave `categoria`
    entre comillas, seguida de dos puntos y un literal -- que es la de un chunk
    del RAG; la forma de kwarg (`categoria=`) es la de un modelo de Django, y
    `SolucionInTouch.categoria` tiene su propia taxonomía casi homónima que no
    es esta.

    Para un fixture deliberadamente inválido, el marcador va en LA MISMA LÍNEA:
    `taxonomia-invalida-a-proposito`.
    """

    MARCADOR = "taxonomia-invalida-a-proposito"
    _RE_CATEGORIA = re.compile(r'"categoria"\s*:\s*"([^"]*)"')

    def _archivos(self):
        raiz = Path(__file__).resolve().parent.parent
        return sorted(raiz.glob("tests/*.py")) + sorted(raiz.glob("rag_eval/*.py"))

    def test_toda_categoria_literal_de_la_suite_esta_en_categorias_rag(self):
        from bot.rag.indexador import CATEGORIAS_RAG

        validas = set(CATEGORIAS_RAG)
        revisados = 0
        for archivo in self._archivos():
            lineas = archivo.read_text(encoding="utf-8").splitlines()
            for numero, linea in enumerate(lineas):
                for categoria in self._RE_CATEGORIA.findall(linea):
                    if self.MARCADOR in linea:
                        continue
                    revisados += 1
                    with self.subTest(archivo=archivo.name, linea=numero + 1):
                        self.assertIn(
                            categoria, validas,
                            f"{archivo.name}:{numero + 1} usa la categoría "
                            f"'{categoria}', que no está en CATEGORIAS_RAG. Un "
                            f"chunk con esa etiqueta queda inalcanzable para el "
                            f"filtro por categoría, y ningún test lo grita.")
        # Si el escáner deja de encontrar fixtures (un refactor de rutas, un
        # cambio de forma), este test pasaría vacío y el invariante quedaría sin
        # dueño otra vez.
        self.assertGreater(revisados, 10)


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
