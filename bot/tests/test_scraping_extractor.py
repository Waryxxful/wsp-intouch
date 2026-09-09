import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from bot.scraping.extractor import (
    extract_catalog,
    _dividir_en_chunks,
    _combinar_catalogos,
    _combinar_vehiculos,
    _normalizar_clave,
    _get_llm,
    _ainvoke_with_retry,
    LLM_TIMEOUT_SECONDS,
    LLM_CONCURRENCIA_MAXIMA,
)


class DividirEnChunksTest(SimpleTestCase):
    def test_paginas_cortas_caben_en_un_solo_chunk(self):
        paginas = [
            {"url": "https://x.cl/1", "texto": "hola"},
            {"url": "https://x.cl/2", "texto": "mundo"},
        ]
        chunks = _dividir_en_chunks(paginas)
        self.assertEqual(len(chunks), 1)
        self.assertIn("hola", chunks[0])
        self.assertIn("mundo", chunks[0])

    def test_paginas_que_superan_chunk_chars_en_conjunto_generan_varios_chunks(self):
        paginas = [
            {"url": "https://x.cl/1", "texto": "a" * 7000},
            {"url": "https://x.cl/2", "texto": "b" * 7000},
        ]
        chunks = _dividir_en_chunks(paginas)
        self.assertEqual(len(chunks), 2)
        self.assertIn("a" * 7000, chunks[0])
        self.assertIn("b" * 7000, chunks[1])

    def test_una_pagina_que_excede_chunk_chars_por_si_sola_se_corta(self):
        paginas = [{"url": "https://x.cl", "texto": "z" * 20000}]
        chunks = _dividir_en_chunks(paginas)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 12000)

    def test_cada_pagina_queda_marcada_con_su_url(self):
        # Bug real detectado en produccion (wsp_demo, renault.cl): sin esta
        # marca, dos paginas /cotizar/<modelo>/<version>/ en el mismo chunk
        # quedaban con su texto pegado sin separador -- el LLM no podia saber
        # a que modelo correspondia cada precio. La URL (que suele contener
        # el modelo/version) le da al LLM el ancla que le faltaba.
        paginas = [
            {"url": "https://x.cl/cotizar/koleos/techno-2-0t/", "texto": "desde $27.990.000"},
            {"url": "https://x.cl/cotizar/master/minibus/", "texto": "desde $38.990.000"},
        ]
        chunks = _dividir_en_chunks(paginas)
        self.assertIn("[Página: https://x.cl/cotizar/koleos/techno-2-0t/]", chunks[0])
        self.assertIn("[Página: https://x.cl/cotizar/master/minibus/]", chunks[0])


class CombinarCatalogosTest(SimpleTestCase):
    def test_deduplica_por_nombre_case_insensitive_y_conserva_campos_no_nulos(self):
        catalogos = [
            {"servicios": [{"nombre": "Corte", "duracion_min": 30, "precio": None}], "sucursales": []},
            {"servicios": [{"nombre": "corte", "duracion_min": None, "precio": 15000}], "sucursales": []},
        ]
        combinado = _combinar_catalogos(catalogos)
        self.assertEqual(len(combinado["servicios"]), 1)
        self.assertEqual(combinado["servicios"][0]["duracion_min"], 30)
        self.assertEqual(combinado["servicios"][0]["precio"], 15000)

    def test_sucursales_de_distintos_chunks_sin_nombre_repetido_no_se_pierden(self):
        catalogos = [
            {"servicios": [], "sucursales": [{"nombre": "Centro", "direccion": "Av. 123", "horario_texto": None}]},
            {"servicios": [], "sucursales": [{"nombre": "Norte", "direccion": None, "horario_texto": "9-18"}]},
        ]
        combinado = _combinar_catalogos(catalogos)
        nombres = {s["nombre"] for s in combinado["sucursales"]}
        self.assertEqual(nombres, {"Centro", "Norte"})

    def test_sucursales_con_misma_direccion_y_nombre_distinto_se_fusionan(self):
        # Bug real detectado en produccion (wsp_demo, renault.cl): la misma
        # sucursal aparecia con nombres distintos en distintas paginas/
        # chunks ("centro La Dehesa AUTOKAS" vs "AUTOKAS") pero la MISMA
        # direccion -- antes se guardaban como dos sucursales separadas.
        catalogos = [
            {
                "servicios": [],
                "sucursales": [
                    {"nombre": "centro La Dehesa AUTOKAS", "direccion": "Av. X 123", "horario_texto": "9-18"}
                ],
            },
            {
                "servicios": [],
                "sucursales": [{"nombre": "AUTOKAS", "direccion": "Av. X 123", "horario_texto": None}],
            },
        ]
        combinado = _combinar_catalogos(catalogos)
        self.assertEqual(len(combinado["sucursales"]), 1)
        self.assertEqual(combinado["sucursales"][0]["nombre"], "AUTOKAS")
        self.assertEqual(combinado["sucursales"][0]["horario_texto"], "9-18")

    def test_servicios_con_acentos_y_puntuacion_distinta_se_fusionan(self):
        catalogos = [
            {"servicios": [{"nombre": "Garantía legal 3×3", "duracion_min": None, "precio": None}], "sucursales": []},
            {"servicios": [{"nombre": "Garantia legal 3x3", "duracion_min": None, "precio": 0}], "sucursales": []},
        ]
        combinado = _combinar_catalogos(catalogos)
        self.assertEqual(len(combinado["servicios"]), 1)
        self.assertEqual(combinado["servicios"][0]["precio"], 0)


class CombinarVehiculosTest(SimpleTestCase):
    def test_deduplica_por_modelo_y_version_y_conserva_specs_no_nulos(self):
        catalogos = [
            {"vehiculos": [{"modelo": "Koleos", "version": "techno 2.0T", "precio": None, "specs": {"motor": "2.0 turbo"}}]},
            {"vehiculos": [{"modelo": "koleos", "version": "Techno 2.0t", "precio": 27990000, "specs": {"torque_nm": "325 Nm"}}]},
        ]
        combinado = _combinar_catalogos(catalogos)
        self.assertEqual(len(combinado["vehiculos"]), 1)
        vehiculo = combinado["vehiculos"][0]
        self.assertEqual(vehiculo["precio"], 27990000)
        self.assertEqual(vehiculo["specs"], {"motor": "2.0 turbo", "torque_nm": "325 Nm"})

    def test_mismo_modelo_con_version_distinta_no_colapsa(self):
        catalogos = [
            {"vehiculos": [
                {"modelo": "Koleos", "version": "techno 2.0T", "precio": 27990000, "specs": {}},
                {"modelo": "Koleos", "version": "full hybrid e-tech esprit alpine", "precio": 34990000, "specs": {}},
            ]},
        ]
        combinado = _combinar_catalogos(catalogos)
        self.assertEqual(len(combinado["vehiculos"]), 2)

    def test_vehiculo_sin_modelo_se_descarta(self):
        catalogos = [{"vehiculos": [{"modelo": "", "version": "x", "precio": None, "specs": {}}]}]
        combinado = _combinar_catalogos(catalogos)
        self.assertEqual(combinado["vehiculos"], [])


class NormalizarClaveTest(SimpleTestCase):
    def test_ignora_mayusculas_acentos_y_puntuacion(self):
        self.assertEqual(_normalizar_clave("Garantía legal 3×3"), _normalizar_clave("Garantia legal 3x3"))
        self.assertEqual(_normalizar_clave("Cotización"), _normalizar_clave("cotizacion"))
        self.assertEqual(_normalizar_clave("AUTOKAS"), _normalizar_clave(" autokas "))

    def test_nombres_genuinamente_distintos_no_colapsan(self):
        self.assertNotEqual(_normalizar_clave("Koleos Techno 2.0T"), _normalizar_clave("Koleos Full Hybrid"))


class ExtractCatalogTest(TestCase):
    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_parsea_servicios_y_sucursales(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = (
            '{"servicios": [{"nombre": "Corte", "duracion_min": 30, "precio": null}], '
            '"sucursales": [{"nombre": "Centro", "direccion": "Av. Siempre Viva 123", "horario_texto": null}]}'
        )
        catalogo = extract_catalog([{"url": "https://x.cl", "texto": "Cortamos pelo, Corte 30 min."}])
        self.assertEqual(catalogo["servicios"][0]["nombre"], "Corte")
        self.assertEqual(catalogo["servicios"][0]["duracion_min"], 30)
        self.assertIsNone(catalogo["servicios"][0]["precio"])
        self.assertEqual(catalogo["sucursales"][0]["direccion"], "Av. Siempre Viva 123")

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_json_invalido_levanta_valueerror(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = "esto no es json"
        with self.assertRaises(ValueError):
            extract_catalog([{"url": "https://x.cl", "texto": "algo"}])

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_respuesta_con_fence_markdown_se_parsea_igual(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = '```json\n{"servicios": [{"nombre": "Corte"}], "sucursales": []}\n```'
        catalogo = extract_catalog([{"url": "https://x.cl", "texto": "algo"}])
        self.assertEqual(catalogo["servicios"][0]["nombre"], "Corte")

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_respuesta_con_fence_sin_lenguaje_se_parsea_igual(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = '```\n{"servicios": [], "sucursales": [{"nombre": "Centro"}]}\n```'
        catalogo = extract_catalog([{"url": "https://x.cl", "texto": "algo"}])
        self.assertEqual(catalogo["sucursales"][0]["nombre"], "Centro")

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_faltan_claves_devuelve_listas_vacias(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = "{}"
        catalogo = extract_catalog([{"url": "https://x.cl", "texto": "algo"}])
        self.assertEqual(catalogo["servicios"], [])
        self.assertEqual(catalogo["sucursales"], [])

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_texto_que_supera_chunk_chars_genera_varias_llamadas_al_llm(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = '{"servicios": [], "sucursales": []}'
        paginas = [
            {"url": "https://x.cl/1", "texto": "a" * 7000},
            {"url": "https://x.cl/2", "texto": "b" * 7000},
        ]
        extract_catalog(paginas)
        self.assertEqual(mock_invoke.call_count, 2)

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_mas_de_max_chunks_solo_procesa_los_primeros_y_loguea_warning(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = '{"servicios": [], "sucursales": []}'
        paginas = [{"url": f"https://x.cl/{i}", "texto": "a" * 12000} for i in range(25)]
        with self.assertLogs("bot.scraping.extractor", level="WARNING") as cm:
            extract_catalog(paginas)
        self.assertEqual(mock_invoke.call_count, 20)
        self.assertIn("25", cm.output[0])

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_un_chunk_con_json_invalido_levanta_valueerror_y_no_sigue_con_los_demas(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.side_effect = ['{"servicios": [], "sucursales": []}', "esto no es json"]
        paginas = [
            {"url": "https://x.cl/1", "texto": "a" * 7000},
            {"url": "https://x.cl/2", "texto": "b" * 7000},
        ]
        with self.assertRaises(ValueError):
            extract_catalog(paginas)

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    def test_los_chunks_se_procesan_en_paralelo_no_secuencial(self, mock_get_llm):
        # Bug real detectado en produccion (wsp_demo, renault.cl): antes los
        # chunks se mandaban a DeepSeek uno por uno -- con chunks de ~180s
        # (visto en produccion) y hasta MAX_CHUNKS=20, un scrape podia
        # demorar 20+ minutos solo en esta etapa aunque el crawl ya hubiera
        # terminado en segundos. El tiempo total con N chunks lentos debe
        # ser proporcional a N/LLM_CONCURRENCIA_MAXIMA, no a N.
        mock_get_llm.return_value = MagicMock()

        async def _invoke_lento(llm, prompt, label="x"):
            await asyncio.sleep(0.2)
            return '{"servicios": [], "sucursales": []}'

        paginas = [{"url": f"https://x.cl/{i}", "texto": "a" * 12000} for i in range(10)]
        with patch("bot.scraping.extractor._ainvoke_with_retry", new=_invoke_lento):
            inicio = time.monotonic()
            extract_catalog(paginas)
            elapsed = time.monotonic() - inicio
        # 10 chunks / LLM_CONCURRENCIA_MAXIMA=5 = 2 tandas * 0.2s = ~0.4s.
        # Secuencial habria sido ~2.0s (10 * 0.2s) -- margen generoso.
        self.assertLess(elapsed, 1.0)

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_vehiculos_faltantes_devuelve_default_vacio(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = "{}"
        catalogo = extract_catalog([{"url": "https://x.cl", "texto": "algo"}])
        self.assertEqual(catalogo["vehiculos"], [])

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_parsea_vehiculos(self, mock_invoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = (
            '{"servicios": [], "sucursales": [], '
            '"vehiculos": [{"modelo": "Koleos", "version": "techno 2.0T", "precio": 27990000, "specs": {"motor": "2.0 turbo"}}]}'
        )
        catalogo = extract_catalog([{"url": "https://x.cl", "texto": "algo"}])
        self.assertEqual(catalogo["vehiculos"][0]["modelo"], "Koleos")
        self.assertEqual(catalogo["vehiculos"][0]["specs"]["motor"], "2.0 turbo")

    def test_llm_concurrencia_maxima_es_positiva(self):
        self.assertGreater(LLM_CONCURRENCIA_MAXIMA, 0)


class GetLlmOpenRouterTest(TestCase):
    @override_settings(OPENROUTER_API_KEY="test-dummy-key")
    @patch("bot.scraping.extractor.ChatOpenRouter")
    def test_get_llm_construye_cliente_openrouter_con_parametros_correctos(self, mock_chat_cls):
        # Migrado el 2026-09-02 desde la API directa de DeepSeek, que se quedo
        # sin saldo y devolvio 402 al indexar el RAG de Cavem.
        asyncio.run(_get_llm())
        mock_chat_cls.assert_called_once_with(
            model=settings.OPENROUTER_SCRAPING_MODEL,
            api_key="test-dummy-key",
            # En MILISEGUNDOS: ChatOpenRouter no toma segundos como ChatOpenAI,
            # y con timeout_ms=None la llamada queda sin limite HTTP.
            timeout=LLM_TIMEOUT_SECONDS * 1000,
            max_retries=0,
            # Este modelo razona a esfuerzo `high` si no se le dice lo
            # contrario, y reestructurar texto a JSON no lo necesita: son hasta
            # MAX_CHUNKS=20 llamadas por corrida de scraping.
            reasoning={"enabled": False},
            openrouter_provider={
                "order": settings.OPENROUTER_PROVIDER_ORDER, "allow_fallbacks": True,
            },
        )

    @override_settings(OPENROUTER_API_KEY="")
    def test_get_llm_sin_api_key_levanta_runtimeerror_explicito(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(_get_llm())

    @override_settings(OPENROUTER_API_KEY="test-dummy-key")
    def test_get_llm_desactiva_el_retry_del_sdk(self):
        # Bug real detectado en produccion: sin max_retries=0 aca, regia el
        # default del SDK (max_retries=2, 3 requests HTTP por invocacion) por
        # encima del retry de aplicacion (_ainvoke_with_retry, 4 intentos con
        # backoff [2, 4, 8]) -- ScrapeRun 86 (2026-09-01) midio 289 de 455
        # requests como reintentos (~39%), todos exitosos una vez completados
        # (no eran 429/5xx del proveedor), hasta 12 requests HTTP de 90s cada
        # uno por una sola llamada logica.
        llm = asyncio.run(_get_llm())
        self.assertEqual(llm.max_retries, 0)


class GetLlmOverrideTest(TransactionTestCase):
    # TransactionTestCase (no TestCase) a proposito: mismo motivo que
    # bot.tests.test_graph.GraphGetLlmOverrideTest -- el Setting se escribe
    # via ORM en el thread principal y _get_llm lo lee via sync_to_async en
    # otro thread; con TestCase (transaccion sin commitear) sqlite en modo
    # shared-cache bloquea esa lectura entre threads.
    @override_settings(OPENROUTER_API_KEY="test-dummy-key")
    @patch("bot.scraping.extractor.ChatOpenRouter")
    def test_get_llm_usa_el_override_de_setting_si_existe(self, mock_chat_cls):
        from bot.models import Setting
        Setting.objects.create(
            key="openrouter_scraping_model_override", value="deepseek/deepseek-v4-flash-0731")
        asyncio.run(_get_llm())
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["model"], "deepseek/deepseek-v4-flash-0731")

    @patch("bot.scraping.extractor.ChatOpenRouter")
    def test_get_llm_usa_el_override_de_api_key_de_setting_si_existe(self, mock_chat_cls):
        # La key es la MISMA que usan el LLM conversacional y el de media:
        # un solo proveedor, un solo override (`openrouter_api_key_override`).
        from bot.models import Setting
        Setting.objects.create(key="openrouter_api_key_override", value="override-dummy-key")
        asyncio.run(_get_llm())
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["api_key"], "override-dummy-key")


class AinvokeWithRetryBackoffTest(TestCase):
    @patch("bot.scraping.extractor.asyncio.sleep", new_callable=AsyncMock)
    def test_reintenta_con_backoff_exponencial_y_devuelve_resultado(self, mock_sleep):
        llm = MagicMock()
        ok_result = MagicMock(content="ok")
        llm.ainvoke = AsyncMock(side_effect=[Exception("fail1"), Exception("fail2"), ok_result])
        result = asyncio.run(_ainvoke_with_retry(llm, "prompt"))
        self.assertEqual(result, "ok")
        self.assertEqual(llm.ainvoke.call_count, 3)
        mock_sleep.assert_has_calls([call(2), call(4)])

    @patch("bot.scraping.extractor.asyncio.sleep", new_callable=AsyncMock)
    def test_agota_los_4_intentos_y_relanza_la_ultima_excepcion(self, mock_sleep):
        llm = MagicMock()
        exc = Exception("rate limit persistente")
        llm.ainvoke = AsyncMock(side_effect=[exc, exc, exc, exc])
        with self.assertRaises(Exception) as ctx:
            asyncio.run(_ainvoke_with_retry(llm, "prompt"))
        self.assertIs(ctx.exception, exc)
        self.assertEqual(llm.ainvoke.call_count, 4)
        self.assertEqual(mock_sleep.call_count, 3)
        mock_sleep.assert_has_calls([call(2), call(4), call(8)])
