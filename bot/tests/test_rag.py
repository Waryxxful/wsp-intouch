import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, call, patch

from django.test import TestCase
from langchain.messages import AIMessage, ToolMessage


class GetSupabaseClientTest(TestCase):
    @patch.dict("os.environ", {
        "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "clave-test",
        "SUPABASE_ALLOW_TEST_WRITES": "1",
    })
    @patch("bot.rag.cliente.create_client")
    def test_usa_las_env_vars_para_crear_el_cliente(self, mock_create_client):
        import os
        from bot.rag.cliente import get_supabase_client
        # RAG_SCHEMA puede venir seteado del entorno real (.env.docker en
        # produccion ya tiene RAG_SCHEMA=renault) -- este test verifica el
        # default "public" cuando NO esta seteado, asi que se lo sacamos del
        # os.environ de este proceso. patch.dict restaura el valor original
        # (si habia alguno) al terminar el test, igual que ya hace el pop de
        # SUPABASE_ALLOW_TEST_WRITES mas abajo en este archivo.
        os.environ.pop("RAG_SCHEMA", None)
        mock_cliente = MagicMock()
        mock_create_client.return_value = mock_cliente

        resultado = get_supabase_client()

        mock_create_client.assert_called_once_with("https://x.supabase.co", "clave-test")
        mock_cliente.schema.assert_called_once_with("public")
        self.assertEqual(resultado, mock_cliente.schema.return_value)

    @patch.dict("os.environ", {
        "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "clave-test",
        "SUPABASE_ALLOW_TEST_WRITES": "1", "RAG_SCHEMA": "renault",
    })
    @patch("bot.rag.cliente.create_client")
    def test_usa_rag_schema_de_env_si_esta_seteado(self, mock_create_client):
        from bot.rag.cliente import get_supabase_client
        mock_cliente = MagicMock()
        mock_create_client.return_value = mock_cliente

        get_supabase_client()

        mock_cliente.schema.assert_called_once_with("renault")

    @patch.dict("os.environ", {
        "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "clave-test",
        "SUPABASE_ALLOW_TEST_WRITES": "1", "RAG_SCHEMA": "renault",
    })
    @patch("bot.rag.cliente.create_client")
    def test_cliente_explicito_gana_sobre_rag_schema_de_env(self, mock_create_client):
        # Bug real 2026-08-27/28: un scrape de Astara (--cliente astara) corrio
        # con RAG_SCHEMA=renault en el proceso (el bot en vivo seguia
        # sirviendo Renault, CLIENTE_ACTIVO nunca se flippeo) y mando 297
        # chunks de contenido de Astara al schema renault -- confirmado y
        # purgado contra el Supabase real. get_supabase_client(cliente=...)
        # explicito debe ganarle siempre a la env var del proceso, para que
        # el pipeline de scraping pueda escribir al schema correcto sin
        # depender de que alguien se acuerde de cambiar RAG_SCHEMA a mano.
        from bot.rag.cliente import get_supabase_client
        mock_cliente = MagicMock()
        mock_create_client.return_value = mock_cliente

        get_supabase_client(cliente="astara")

        mock_cliente.schema.assert_called_once_with("astara")

    @patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "clave-test"})
    def test_con_django_en_sqlite_y_sin_escape_hatch_no_crea_el_cliente(self):
        # La suite entera corre con USE_SQLITE=true: el guard hace
        # estructuralmente imposible que un test o un shell de desarrollo
        # escriba por accidente en el proyecto Supabase REAL (asi se colaron
        # 58 filas de fixture a produccion). A proposito NO se mockea
        # create_client: se prueba que revienta ANTES de llegar a esa llamada.
        import os
        from bot.rag.cliente import get_supabase_client
        os.environ.pop("SUPABASE_ALLOW_TEST_WRITES", None)

        with self.assertRaises(RuntimeError) as ctx:
            get_supabase_client()

        self.assertIn("SUPABASE_ALLOW_TEST_WRITES", str(ctx.exception))


class TextoConContextoTest(TestCase):
    def test_antepone_categoria_y_titulo(self):
        from bot.rag.indexador import _texto_con_contexto
        resultado = _texto_con_contexto({"titulo": "Garantia", "texto": "36 meses"}, "garantia")
        self.assertEqual(resultado, "[garantia]\n\nGarantia\n\n36 meses")

    def test_sin_categoria_ni_titulo_solo_devuelve_el_texto(self):
        from bot.rag.indexador import _texto_con_contexto
        resultado = _texto_con_contexto({"titulo": None, "texto": "36 meses"}, None)
        self.assertEqual(resultado, "36 meses")

    def test_solo_categoria(self):
        from bot.rag.indexador import _texto_con_contexto
        resultado = _texto_con_contexto({"titulo": None, "texto": "36 meses"}, "garantia")
        self.assertEqual(resultado, "[garantia]\n\n36 meses")


class IndexarPaginaEnSupabaseTest(TestCase):
    def _pagina_fake(self, texto="Hola, este es un texto de prueba corto.", secciones=None, es_documento=False):
        pagina = MagicMock()
        pagina.url = "https://renault.cl/pagina-test/"
        pagina.id = 42
        pagina.texto = texto
        pagina.secciones = secciones if secciones is not None else []
        pagina.es_documento = es_documento
        return pagina

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_indexa_los_chunks_con_sus_embeddings(self, mock_embeddings_client, mock_get_client):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente

        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(self._pagina_fake(), cliente="renault")

        self.assertEqual(
            mock_cliente.table.call_args_list,
            [call("documentos_conocimiento"), call("documentos_conocimiento")],
        )
        filas = mock_cliente.table.return_value.insert.call_args[0][0]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["fuente_url"], "https://renault.cl/pagina-test/")
        self.assertEqual(filas[0]["scraped_page_id"], 42)
        self.assertEqual(filas[0]["contenido"], "Hola, este es un texto de prueba corto.")
        self.assertEqual(filas[0]["embedding"], [0.1, 0.2, 0.3])
        mock_cliente.table.return_value.insert.return_value.execute.assert_called_once()

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_propaga_el_cliente_a_get_supabase_client(self, mock_embeddings_client, mock_get_client):
        # Regresion directa del bug real 2026-08-27/28 (docs/PENDIENTES.md):
        # un scrape de Astara mando 297 chunks al schema renault porque nada
        # en el camino de indexacion sabia el cliente real de la pagina.
        # cliente es obligatorio (sin default) a proposito, mismo patron que
        # _upsert_catalogo -- fuerza a cada call site a pensarlo.
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_get_client.return_value = MagicMock()

        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(self._pagina_fake(), cliente="astara")

        mock_get_client.assert_called_once_with(cliente="astara")

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_texto_vacio_no_llama_a_supabase(self, mock_embeddings_client, mock_get_client):
        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(self._pagina_fake(texto=""), cliente="renault")
        mock_get_client.assert_not_called()

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_borra_las_filas_previas_de_esa_fuente_url_antes_de_insertar(self, mock_embeddings_client, mock_get_client):
        # Sin esto, cada re-scrape de una pagina ya indexada apilaba chunks
        # obsoletos junto a los vigentes (el hook de scraping llama a esta
        # funcion directo, no pasa por el comando de backfill).
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente

        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(self._pagina_fake(), cliente="renault")

        borrado = mock_cliente.table.return_value.delete.return_value
        borrado.eq.assert_called_once_with("fuente_url", "https://renault.cl/pagina-test/")
        borrado.eq.return_value.execute.assert_called_once()

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_el_borrado_ocurre_despues_de_calcular_los_embeddings(self, mock_embeddings_client, mock_get_client):
        # Orden a proposito: si el embed es lento o falla, la pagina conserva
        # sus filas viejas (siguen siendo buscables) en vez de quedar vacia.
        orden = []
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.side_effect = lambda chunks: orden.append("embed") or [[0.1]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_cliente.table.return_value.delete.return_value.eq.return_value.execute.side_effect = (
            lambda: orden.append("delete")
        )
        mock_get_client.return_value = mock_cliente

        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(self._pagina_fake(), cliente="renault")

        self.assertEqual(orden, ["embed", "delete"])

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_fallo_al_calcular_embeddings_se_loguea_y_no_propaga(self, mock_embeddings_client, mock_get_client):
        # Contrato best-effort: el hook de scraping no debe marcar todo el run
        # como fallido (ni saltarse la extraccion de catalogo) porque el RAG
        # no pudo indexar.
        mock_embeddings_client.side_effect = KeyError("GOOGLE_API_KEY")

        from bot.rag.indexador import indexar_pagina_en_supabase
        with self.assertLogs("bot.rag.indexador", level="ERROR") as logs:
            indexar_pagina_en_supabase(self._pagina_fake(), cliente="renault")

        self.assertIn("https://renault.cl/pagina-test/", "\n".join(logs.output))
        # get_supabase_client() ahora corre ANTES del calculo de embeddings (guard-first,
        # ver bot/rag/indexador.py) -- si el embedding falla, el cliente ya se creo.
        mock_get_client.assert_called_once()

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_fallo_de_supabase_se_loguea_y_no_propaga(self, mock_embeddings_client, mock_get_client):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_get_client.side_effect = KeyError("SUPABASE_URL")

        from bot.rag.indexador import indexar_pagina_en_supabase
        with self.assertLogs("bot.rag.indexador", level="ERROR") as logs:
            indexar_pagina_en_supabase(self._pagina_fake(), cliente="renault")

        self.assertIn("SUPABASE_URL", "\n".join(logs.output))

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_fallo_del_insert_se_loguea_y_no_propaga(self, mock_embeddings_client, mock_get_client):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1, 0.2, 0.3]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_cliente.table.return_value.insert.return_value.execute.side_effect = RuntimeError("timeout de red")
        mock_get_client.return_value = mock_cliente

        from bot.rag.indexador import indexar_pagina_en_supabase
        with self.assertLogs("bot.rag.indexador", level="ERROR") as logs:
            resultado = indexar_pagina_en_supabase(self._pagina_fake(), cliente="renault")

        self.assertIn("timeout de red", "\n".join(logs.output))
        self.assertFalse(resultado)

    def _pagina_fake_con_secciones(self, secciones):
        pagina = MagicMock()
        pagina.url = "https://renault.cl/garantia/"
        pagina.id = 99
        pagina.texto = "\n\n".join(s["texto"] for s in secciones)
        pagina.secciones = secciones
        pagina.es_documento = False
        return pagina

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_con_secciones_antepone_el_titulo_al_texto_embebido(
        self, mock_embeddings_client, mock_get_client, mock_ainvoke, mock_get_llm,
    ):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.return_value = "soluciones"

        pagina = self._pagina_fake_con_secciones([{"titulo": "Garantia", "texto": "Dura 36 meses."}])
        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(pagina, cliente="renault")

        filas = mock_cliente.table.return_value.insert.call_args[0][0]
        # El "contenido" almacenado NUNCA lleva el tag [categoria] -- eso es
        # solo para el modelo de embeddings (ver test_el_contenido_almacenado_...
        # y test_el_texto_embebido_incluye_la_categoria_ya_clasificada mas abajo).
        self.assertEqual(filas[0]["contenido"], "Garantia\n\nDura 36 meses.")
        self.assertEqual(filas[0]["categoria"], "soluciones")

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_el_texto_embebido_incluye_la_categoria_ya_clasificada(
        self, mock_embeddings_client, mock_get_client, mock_get_llm,
    ):
        pagina = self._pagina_fake_con_secciones([{"titulo": "Garantia", "texto": "36 meses de cobertura"}])
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1]]
        mock_embeddings_client.return_value = mock_embeddings

        from bot.rag.indexador import indexar_pagina_en_supabase
        with patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = "soluciones"
            indexar_pagina_en_supabase(pagina, cliente="renault")

        textos_embebidos = mock_embeddings.embed_documents.call_args[0][0]
        self.assertTrue(textos_embebidos[0].startswith("[soluciones]"))

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_el_contenido_almacenado_no_incluye_el_tag_de_categoria(
        self, mock_embeddings_client, mock_get_client, mock_get_llm,
    ):
        # El tag [categoria] de Task 6 solo debe llegar al modelo de
        # embeddings -- nunca a lo que se guarda como "contenido" (eso es lo
        # que indexa el FTS y lo que el LLM conversacional/el cliente por
        # WhatsApp terminan viendo).
        pagina = self._pagina_fake_con_secciones([{"titulo": "Garantia", "texto": "36 meses de cobertura"}])
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente

        from bot.rag.indexador import indexar_pagina_en_supabase
        with patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = "soluciones"
            indexar_pagina_en_supabase(pagina, cliente="renault")

        filas = mock_cliente.table.return_value.insert.call_args[0][0]
        self.assertEqual(filas[0]["contenido"], "Garantia\n\n36 meses de cobertura")
        self.assertNotIn("[soluciones]", filas[0]["contenido"])
        # pero el embedding SI se calculo sobre el texto con el tag
        textos_embebidos = mock_embeddings.embed_documents.call_args[0][0]
        self.assertTrue(textos_embebidos[0].startswith("[soluciones]"))

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_respuesta_fuera_de_taxonomia_cae_a_otro(
        self, mock_embeddings_client, mock_get_client, mock_ainvoke, mock_get_llm,
    ):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.return_value = "categoria_inventada_por_el_llm"

        pagina = self._pagina_fake_con_secciones([{"titulo": "Garantia", "texto": "Dura 36 meses."}])
        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(pagina, cliente="renault")

        filas = mock_cliente.table.return_value.insert.call_args[0][0]
        self.assertEqual(filas[0]["categoria"], "otro")

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_sin_secciones_no_llama_al_clasificador_y_categoria_queda_none(
        self, mock_embeddings_client, mock_get_client, mock_get_llm,
    ):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente

        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(self._pagina_fake(), cliente="renault")  # secciones=[] por default

        mock_get_llm.assert_not_called()
        filas = mock_cliente.table.return_value.insert.call_args[0][0]
        self.assertIsNone(filas[0]["categoria"])
        self.assertFalse(filas[0]["clasificacion_fallo"])

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_fallo_al_clasificar_un_chunk_cae_a_otro_sin_abortar_la_pagina(
        self, mock_embeddings_client, mock_get_client, mock_ainvoke, mock_get_llm,
    ):
        # Bug real: sin return_exceptions=True, un solo chunk que agota sus
        # reintentos de clasificacion (ej. DeepSeek) abortaba el gather entero
        # y ninguna fila se borraba/insertaba para la pagina completa, aunque
        # el resto de los chunks se hubieran clasificado bien.
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1], [0.2]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.side_effect = [RuntimeError("agotados los reintentos"), "canales"]

        pagina = self._pagina_fake_con_secciones([
            {"titulo": "Garantia", "texto": "Dura 36 meses."},
            {"titulo": "Sucursales", "texto": "Providencia y Las Condes."},
        ])
        from bot.rag.indexador import indexar_pagina_en_supabase
        with self.assertLogs("bot.rag.indexador", level="WARNING") as logs:
            resultado = indexar_pagina_en_supabase(pagina, cliente="renault")

        filas = mock_cliente.table.return_value.insert.call_args[0][0]
        self.assertEqual({f["categoria"] for f in filas}, {"otro", "canales"})
        fila_fallo = next(f for f in filas if f["categoria"] == "otro")
        fila_ok = next(f for f in filas if f["categoria"] == "canales")
        self.assertTrue(fila_fallo["clasificacion_fallo"])
        self.assertFalse(fila_ok["clasificacion_fallo"])
        self.assertIn("fallo al clasificar", "\n".join(logs.output))
        mock_cliente.table.return_value.insert.return_value.execute.assert_called_once()
        self.assertTrue(resultado)

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    def test_respuesta_fuera_de_taxonomia_no_es_fallo_de_clasificacion(
        self, mock_embeddings_client, mock_get_client, mock_ainvoke, mock_get_llm,
    ):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.return_value = "categoria_inventada_por_el_llm"

        pagina = self._pagina_fake_con_secciones([{"titulo": "Garantia", "texto": "Dura 36 meses."}])
        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(pagina, cliente="renault")

        filas = mock_cliente.table.return_value.insert.call_args[0][0]
        self.assertEqual(filas[0]["categoria"], "otro")
        self.assertFalse(filas[0]["clasificacion_fallo"])

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    @patch("bot.rag.indexador._chunks_de_pagina")
    def test_extraccion_vacia_con_texto_original_no_vacio_loguea_advertencia(
        self, mock_chunks_de_pagina, mock_embeddings_client, mock_get_client,
    ):
        # Bug real: una pagina cuya extraccion de chunks da vacio (ej. contenido
        # solo en div/span, o pagina JS-only) mantenia sus filas viejas de
        # Supabase en silencio, sin ninguna linea de log para notarlo.
        mock_chunks_de_pagina.return_value = []

        from bot.rag.indexador import indexar_pagina_en_supabase
        with self.assertLogs("bot.rag.indexador", level="WARNING") as logs:
            indexar_pagina_en_supabase(self._pagina_fake(texto="Texto real de la pagina, no vacio."), cliente="renault")

        self.assertIn("extraccion de chunks dio vacio", "\n".join(logs.output))
        mock_get_client.assert_not_called()

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    @patch("bot.rag.indexador._hechos_de_documento", new_callable=AsyncMock)
    def test_es_documento_usa_hechos_de_documento_en_vez_del_splitter(
        self, mock_hechos, mock_embeddings_client, mock_get_client,
    ):
        mock_hechos.return_value = [
            {"texto": "InTouch: agentes conversacionales en WhatsApp, voz y chat.", "categoria": "soluciones"},
            {"texto": "InTouch: paneles de supervisión y dashboards en Power BI.", "categoria": "analitica"},
        ]
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1], [0.2]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente

        pagina = self._pagina_fake(texto="texto crudo del pdf", es_documento=True)
        from bot.rag.indexador import indexar_pagina_en_supabase
        resultado = indexar_pagina_en_supabase(pagina, cliente="renault")

        mock_hechos.assert_awaited_once_with("texto crudo del pdf")
        filas = mock_cliente.table.return_value.insert.call_args[0][0]
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0]["contenido"], "InTouch: agentes conversacionales en WhatsApp, voz y chat.")
        self.assertEqual(filas[0]["categoria"], "soluciones")
        self.assertEqual(filas[1]["categoria"], "analitica")
        self.assertTrue(resultado)

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    @patch("bot.rag.indexador._hechos_de_documento", new_callable=AsyncMock)
    def test_es_documento_no_llama_al_clasificador_por_chunk(
        self, mock_hechos, mock_embeddings_client, mock_get_client, mock_get_llm,
    ):
        # La categoria de un documento ya viene puesta por _hechos_de_documento --
        # llamar tambien a _clasificar_chunk (que usa _get_llm) seria clasificar
        # dos veces el mismo contenido, gastando LLM de mas.
        mock_hechos.return_value = [{"texto": "un hecho", "categoria": "soluciones"}]
        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1]]
        mock_embeddings_client.return_value = mock_embeddings
        mock_get_client.return_value = MagicMock()

        pagina = self._pagina_fake(texto="texto crudo", es_documento=True)
        from bot.rag.indexador import indexar_pagina_en_supabase
        indexar_pagina_en_supabase(pagina, cliente="renault")

        mock_get_llm.assert_not_called()

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.indexador._embeddings_client")
    @patch("bot.rag.indexador._hechos_de_documento", new_callable=AsyncMock)
    def test_hechos_de_documento_falla_no_borra_ni_inserta_en_supabase(
        self, mock_hechos, mock_embeddings_client, mock_get_client,
    ):
        # El test mas importante de este set: un fallo transitorio de LLM en
        # _hechos_de_documento NO debe vaciar el conocimiento ya indexado de
        # ese documento -- indexar_pagina_en_supabase_async debe abortar ANTES
        # de llegar al delete()+insert() por fuente_url.
        mock_hechos.side_effect = RuntimeError("agotados los reintentos")
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente

        pagina = self._pagina_fake(texto="texto crudo del pdf", es_documento=True)
        from bot.rag.indexador import indexar_pagina_en_supabase
        with self.assertLogs("bot.rag.indexador", level="ERROR"):
            resultado = indexar_pagina_en_supabase(pagina, cliente="renault")

        self.assertFalse(resultado)
        mock_get_client.assert_not_called()
        mock_embeddings_client.assert_not_called()
        mock_cliente.table.return_value.delete.assert_not_called()
        mock_cliente.table.return_value.insert.assert_not_called()


class BuscarEnSupabaseTest(TestCase):
    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.tool._cliente_embeddings")
    def test_k_por_defecto_es_20_y_manda_el_texto_de_la_query(self, mock_embeddings_class, mock_get_client):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query = MagicMock(return_value=[0.1])
        mock_embeddings_class.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_cliente.rpc.return_value.execute.return_value.data = []
        mock_get_client.return_value = mock_cliente

        from bot.rag.tool import _buscar_en_supabase
        asyncio.run(_buscar_en_supabase("cuanto cuesta el arkana"))

        mock_cliente.rpc.assert_called_once_with(
            "match_documentos",
            {"query_embedding": [0.1], "query_texto": "cuanto cuesta el arkana", "match_count": 20},
        )

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.tool._cliente_embeddings")
    def test_k_explicito_se_respeta(self, mock_embeddings_class, mock_get_client):
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query = MagicMock(return_value=[0.1])
        mock_embeddings_class.return_value = mock_embeddings
        mock_cliente = MagicMock()
        mock_cliente.rpc.return_value.execute.return_value.data = []
        mock_get_client.return_value = mock_cliente

        from bot.rag.tool import _buscar_en_supabase
        asyncio.run(_buscar_en_supabase("query", k=5))

        args, _ = mock_cliente.rpc.call_args
        self.assertEqual(args[1]["match_count"], 5)


class ClientesDeProcesoTest(TestCase):
    """Los clientes del RAG se construyen UNA VEZ por proceso, y sincronos.

    Las dos propiedades se prueban juntas porque una sin la otra es peor que
    ninguna: cachear un cliente ASYNC es exactamente el bug que esto evita.
    `async_to_sync` (bot/whatsapp/webhooks.py) crea un event loop nuevo en cada
    request, asi que un cliente async guardado a nivel proceso queda atado a un
    loop cerrado -- medido el 2026-09-22: "Event loop is closed" en 2 de 4
    requests, o sea un fallo INTERMITENTE indistinguible de una caida del
    proveedor. El cliente sincrono no tiene nada atado a un loop.

    El ahorro que esto persigue, medido contra el servicio real el 2026-09-22:
    embedding 1336ms -> 737ms, rerank 484ms -> 371ms.
    """

    def setUp(self):
        from bot.rag.tool import _cliente_embeddings, _cliente_http
        _cliente_embeddings.cache_clear()
        _cliente_http.cache_clear()

    tearDown = setUp

    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_el_cliente_de_embeddings_se_construye_una_sola_vez(self, mock_clase):
        from bot.rag.tool import _cliente_embeddings

        primero = _cliente_embeddings()
        segundo = _cliente_embeddings()

        self.assertIs(primero, segundo)
        mock_clase.assert_called_once()

    def test_el_cliente_http_se_construye_una_sola_vez_y_es_sincrono(self):
        import httpx
        from bot.rag.tool import _cliente_http

        self.assertIs(_cliente_http(), _cliente_http())
        # Sincrono, NO httpx.AsyncClient: ver el docstring de la clase.
        self.assertIsInstance(_cliente_http(), httpx.Client)
        self.assertNotIsInstance(_cliente_http(), httpx.AsyncClient)

    @patch("bot.rag.cliente.get_supabase_client")
    @patch("bot.rag.tool._cliente_embeddings")
    def test_el_trabajo_bloqueante_no_corre_en_el_event_loop(self, mock_emb, mock_get_client):
        """El embedding y el RPC corren en OTRO thread que el event loop.

        No se afirma "usa asyncio.to_thread" (eso seria probar la
        implementacion): se comprueba la propiedad que importa, que es que el
        thread del loop quede libre. Sin esto, cada consulta al RAG congela
        ~459ms + el embedding a TODOS los contactos que comparten el proceso.
        """
        import threading

        hilos = {}

        def _embed(query):
            hilos["embedding"] = threading.get_ident()
            return [0.1]

        def _rpc(*args, **kwargs):
            hilos["rpc"] = threading.get_ident()
            resultado = MagicMock()
            resultado.execute.return_value.data = []
            return resultado

        mock_emb.return_value = MagicMock(embed_query=_embed)
        mock_get_client.return_value = MagicMock(rpc=_rpc)

        from bot.rag.tool import _buscar_en_supabase

        async def _correr():
            hilos["loop"] = threading.get_ident()
            return await _buscar_en_supabase("cómo operan el contact center")

        asyncio.run(_correr())

        self.assertNotEqual(hilos["embedding"], hilos["loop"],
                            "el embedding corrio en el thread del event loop")
        self.assertNotEqual(hilos["rpc"], hilos["loop"],
                            "el RPC a Supabase corrio en el thread del event loop")


class ContarIntentosPreviosTest(TestCase):
    def test_cuenta_solo_tool_messages_de_esta_tool(self):
        from bot.rag.tool import _contar_intentos_previos
        mensajes = [
            AIMessage(content="", tool_calls=[{"name": "consultar_base_conocimiento", "args": {}, "id": "1"}]),
            ToolMessage(content="{}", tool_call_id="1", name="consultar_base_conocimiento"),
            ToolMessage(content="{}", tool_call_id="2", name="registrar_no_contactar"),
        ]
        self.assertEqual(_contar_intentos_previos(mensajes), 1)

    def test_lista_vacia_es_cero(self):
        from bot.rag.tool import _contar_intentos_previos
        self.assertEqual(_contar_intentos_previos([]), 0)


class ConsultarBaseConocimientoSchemaTest(TestCase):
    def test_solo_expone_query_como_argumento_del_llm(self):
        from bot.rag.tool import consultar_base_conocimiento
        self.assertEqual(set(consultar_base_conocimiento.args.keys()), {"query"})


class ConsultarBaseConocimientoImplTest(TestCase):
    @patch("bot.rag.tool._rerankear", new_callable=AsyncMock)
    @patch("bot.rag.tool._buscar_en_supabase", new_callable=AsyncMock)
    def test_ok_true_con_los_chunks_que_sobrevivieron_el_rerank(self, mock_buscar, mock_rerank):
        from bot.rag.tool import _consultar_base_conocimiento_impl
        mock_buscar.return_value = [
            {"contenido": "el Contact Center se opera en modalidad hibrida",
             "fuente_url": "https://in-touch.cl/modelos-de-operacion/", "categoria": "modelos_operacion"},
        ]
        mock_rerank.return_value = mock_buscar.return_value

        resultado = asyncio.run(_consultar_base_conocimiento_impl("como operan el contact center", []))

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["resultados"][0]["texto"], "el Contact Center se opera en modalidad hibrida")
        self.assertEqual(resultado["resultados"][0]["fuente"], "https://in-touch.cl/modelos-de-operacion/")

    @patch("bot.rag.tool._rerankear", new_callable=AsyncMock)
    @patch("bot.rag.tool._buscar_en_supabase", new_callable=AsyncMock)
    def test_ok_false_si_el_rerank_no_deja_nada(self, mock_buscar, mock_rerank):
        from bot.rag.tool import _consultar_base_conocimiento_impl
        mock_buscar.return_value = [{"contenido": "algo", "fuente_url": "https://x/", "categoria": None}]
        mock_rerank.return_value = []

        resultado = asyncio.run(_consultar_base_conocimiento_impl("pregunta sin relacion", []))

        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)

    @patch("bot.rag.tool._rerankear", new_callable=AsyncMock)
    @patch("bot.rag.tool._buscar_en_supabase", new_callable=AsyncMock)
    def test_sin_candidatos_no_llama_al_rerank(self, mock_buscar, mock_rerank):
        from bot.rag.tool import _consultar_base_conocimiento_impl
        mock_buscar.return_value = []

        resultado = asyncio.run(_consultar_base_conocimiento_impl("pregunta", []))

        self.assertFalse(resultado["ok"])
        mock_rerank.assert_not_called()

    @patch("bot.rag.tool._buscar_en_supabase", new_callable=AsyncMock)
    def test_freno_anti_loop_no_llama_a_buscar(self, mock_buscar):
        from bot.rag.tool import _consultar_base_conocimiento_impl
        previos = [
            ToolMessage(content="{}", tool_call_id=str(i), name="consultar_base_conocimiento")
            for i in range(3)
        ]

        resultado = asyncio.run(_consultar_base_conocimiento_impl("otra vez", previos))

        self.assertFalse(resultado["ok"])
        mock_buscar.assert_not_called()


class ConsultarBaseConocimientoToolTest(TestCase):
    @patch("bot.rag.tool._consultar_base_conocimiento_impl", new_callable=AsyncMock)
    def test_delega_al_impl_con_el_tool_messages_del_runtime(self, mock_impl):
        from bot.rag.tool import consultar_base_conocimiento
        from langchain.tools import ToolRuntime
        mock_impl.return_value = {"ok": True, "resultados": []}
        # ToolRuntime real (no MagicMock): el args_schema generado por @tool
        # valida "runtime" como instancia real de ToolRuntime via pydantic
        # (isinstance + model_dump de todos sus campos), lo que un MagicMock
        # -- con o sin spec -- no satisface (le faltan campos del dataclass
        # que no tienen default, como context/config/stream_writer).
        runtime = ToolRuntime(
            state={"tool_messages": ["algo"]}, context=None, config={},
            stream_writer=lambda x: None, tool_call_id=None, store=None, tools=[],
        )

        resultado = asyncio.run(consultar_base_conocimiento.ainvoke({"query": "q", "runtime": runtime}))

        mock_impl.assert_called_once_with("q", ["algo"])
        self.assertEqual(resultado, {"ok": True, "resultados": []})


class RerankearTest(TestCase):
    def _chunks(self):
        return [
            {"contenido": "el Contact Center se opera en modalidad hibrida",
             "fuente_url": "https://in-touch.cl/modelos-de-operacion/", "categoria": "modelos_operacion"},
            {"contenido": "paneles de supervision y dashboards en Power BI",
             "fuente_url": "https://in-touch.cl/analitica/", "categoria": "analitica"},
            {"contenido": "integracion con el CRM y el ERP de la empresa",
             "fuente_url": "https://in-touch.cl/integraciones/", "categoria": "integraciones"},
        ]

    def _mock_response(self, mock_post, resultados):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": resultados}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

    @patch("httpx.Client.post")
    def test_devuelve_los_chunks_en_el_orden_del_rerank_filtrando_por_umbral(self, mock_post):
        from bot.rag.tool import _rerankear
        self._mock_response(mock_post, [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.6},
            {"index": 1, "relevance_score": 0.05},
        ])

        resultado = asyncio.run(_rerankear("se integra con mi CRM", self._chunks()))

        self.assertEqual([c["categoria"] for c in resultado], ["integraciones", "modelos_operacion"])

    @patch("httpx.Client.post")
    def test_manda_el_payload_esperado_a_openrouter(self, mock_post):
        from bot.rag.tool import _rerankear, _RERANK_MODEL
        self._mock_response(mock_post, [])

        asyncio.run(_rerankear("se integra con mi CRM", self._chunks()))

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["model"], _RERANK_MODEL)
        self.assertEqual(kwargs["json"]["query"], "se integra con mi CRM")
        self.assertEqual(kwargs["json"]["documents"], [c["contenido"] for c in self._chunks()])
        self.assertIn("top_n", kwargs["json"])
        self.assertIn("Bearer", kwargs["headers"]["Authorization"])

    @patch("httpx.Client.post")
    def test_todos_bajo_el_umbral_devuelve_lista_vacia(self, mock_post):
        from bot.rag.tool import _rerankear
        self._mock_response(mock_post, [{"index": 0, "relevance_score": 0.01}])

        resultado = asyncio.run(_rerankear("pregunta sin relacion", self._chunks()))
        self.assertEqual(resultado, [])

    @patch("bot.rag.tool.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.Client.post")
    def test_error_http_persistente_cae_a_los_candidatos_del_hibrido(self, mock_post, _sleep):
        """CAMBIO DELIBERADO DE COMPORTAMIENTO (2026-09-07).

        Antes devolvia [] y aguas arriba eso se convertia en ok:false, o sea
        que el bot le decia al cliente "no tengo ese dato" TENIENDO los chunks
        recuperados en la mano. Un fallo de infraestructura era indistinguible
        de una base de conocimiento vacia -- la misma familia que el
        `except Exception: pass` de docs/PENDIENTES.md #27.

        Ahora cae a los candidatos del retrieval hibrido, que ya vienen
        ordenados por RRF. Es una degradacion de precision, no de veracidad:
        el modelo lee los chunks y decide, en vez de que le mintamos con un
        "no hay informacion"."""
        import httpx
        from bot.rag.tool import _rerankear, _RERANK_TOP_N
        mock_post.side_effect = httpx.HTTPError("boom")

        resultado = asyncio.run(_rerankear("cualquier pregunta", self._chunks()))
        self.assertEqual(resultado, self._chunks()[:_RERANK_TOP_N])

    @patch("httpx.Client.post")
    def test_respuesta_sin_results_cae_al_fallback_sin_reintentar(self, mock_post):
        """Una respuesta malformada tambien es "el rerank no pudo correr", no
        "nada es relevante", asi que corresponde el fallback -- pero sin gastar
        reintentos: un cambio de contrato no se arregla insistiendo."""
        from bot.rag.tool import _rerankear
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "something"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        resultado = asyncio.run(_rerankear("se integra con mi CRM", self._chunks()))
        self.assertEqual(resultado, self._chunks())
        self.assertEqual(mock_post.call_count, 1)

    @patch("httpx.Client.post")
    def test_index_fuera_de_rango_cae_al_fallback_sin_reintentar(self, mock_post):
        from bot.rag.tool import _rerankear
        self._mock_response(mock_post, [{"index": 99, "relevance_score": 0.9}])

        resultado = asyncio.run(_rerankear("se integra con mi CRM", self._chunks()))
        self.assertEqual(resultado, self._chunks())
        self.assertEqual(mock_post.call_count, 1)

    def test_lista_de_chunks_vacia_no_llama_a_openrouter(self):
        from bot.rag.tool import _rerankear
        with patch("httpx.Client.post") as mock_post:
            resultado = asyncio.run(_rerankear("pregunta", []))
        self.assertEqual(resultado, [])
        mock_post.assert_not_called()


class HechosDeDocumentoTest(TestCase):
    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_hace_bind_de_max_tokens_para_no_truncar_documentos_grandes(self, mock_ainvoke, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm
        mock_ainvoke.return_value = json.dumps({
            "hechos": [{"texto": "hecho", "categoria": "soluciones"}],
        })

        from bot.rag.indexador import _hechos_de_documento
        asyncio.run(_hechos_de_documento("texto crudo"))

        mock_llm.bind.assert_called_once_with(max_tokens=16000)
        # el LLM invocado debe ser el resultado del bind, no el mock crudo sin acotar
        mock_ainvoke.assert_called_once()
        llm_usado = mock_ainvoke.call_args[0][0]
        self.assertIs(llm_usado, mock_llm.bind.return_value)

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_hecho_sobre_4000_chars_loguea_advertencia(self, mock_ainvoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        texto_largo = "x" * 4001
        mock_ainvoke.return_value = json.dumps({
            "hechos": [{"texto": texto_largo, "categoria": "soluciones"}],
        })

        from bot.rag.indexador import _hechos_de_documento
        with self.assertLogs("bot.rag.indexador", level="WARNING") as logs:
            resultado = asyncio.run(_hechos_de_documento("texto crudo"))

        self.assertTrue(any("4001" in mensaje for mensaje in logs.output))
        # visibilidad solamente: el hecho no se trunca ni se modifica
        self.assertEqual(resultado[0]["texto"], texto_largo)
        # La categoria del fixture tiene que estar en CATEGORIAS_RAG, o
        # `_hechos_de_documento` la coacciona a "otro" y este test pasa por el
        # camino de ERROR creyendo probar el feliz -- era el caso con
        # "vehiculo_specs", heredado del bot automotriz.
        self.assertEqual(resultado[0]["categoria"], "soluciones")

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_hecho_bajo_4000_chars_no_loguea_advertencia(self, mock_ainvoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        texto_corto = "x" * 100
        mock_ainvoke.return_value = json.dumps({
            "hechos": [{"texto": texto_corto, "categoria": "soluciones"}],
        })

        from bot.rag.indexador import _hechos_de_documento
        with self.assertLogs("bot.rag.indexador", level="INFO") as logs:
            asyncio.run(_hechos_de_documento("texto crudo"))

        self.assertFalse(any("WARNING" in registro for registro in logs.output))

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_json_valido_devuelve_lista_de_hechos_con_categoria(self, mock_ainvoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.return_value = json.dumps({
            "hechos": [
                {"texto": "InTouch: analítica conversacional con dashboards en Power BI.", "categoria": "analitica"},
                {"texto": "InTouch: atención por WhatsApp, voz, chat y correo.", "categoria": "canales"},
            ]
        })

        from bot.rag.indexador import _hechos_de_documento
        resultado = asyncio.run(_hechos_de_documento("texto crudo del pdf"))

        self.assertEqual(len(resultado), 2)
        self.assertEqual(
            resultado[0],
            {"texto": "InTouch: analítica conversacional con dashboards en Power BI.", "categoria": "analitica"},
        )
        self.assertEqual(resultado[1]["categoria"], "canales")

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_categoria_invalida_en_un_hecho_cae_a_otro_sin_abortar_el_resto(self, mock_ainvoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.return_value = json.dumps({
            "hechos": [
                {"texto": "hecho valido", "categoria": "soluciones"},
                # Este test prueba justamente la coercion a "otro" de una
                # categoria que el filtro no conoce, asi que el fixture tiene
                # que ser invalido a proposito.
                {"texto": "hecho con categoria inventada", "categoria": "categoria_que_no_existe"},  # taxonomia-invalida-a-proposito
            ]
        })

        from bot.rag.indexador import _hechos_de_documento
        resultado = asyncio.run(_hechos_de_documento("texto crudo"))

        self.assertEqual(resultado[0]["categoria"], "soluciones")
        self.assertEqual(resultado[1]["categoria"], "otro")

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_json_sin_clave_hechos_levanta_valueerror(self, mock_ainvoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.return_value = json.dumps({"algo_distinto": []})

        from bot.rag.indexador import _hechos_de_documento
        with self.assertRaises(ValueError):
            asyncio.run(_hechos_de_documento("texto crudo"))

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_lista_de_hechos_vacia_levanta_valueerror(self, mock_ainvoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.return_value = json.dumps({"hechos": []})

        from bot.rag.indexador import _hechos_de_documento
        with self.assertRaises(ValueError):
            asyncio.run(_hechos_de_documento("texto crudo"))

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_reintentos_agotados_propaga_la_excepcion(self, mock_ainvoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.side_effect = RuntimeError("agotados los reintentos")

        from bot.rag.indexador import _hechos_de_documento
        with self.assertRaises(RuntimeError):
            asyncio.run(_hechos_de_documento("texto crudo"))

    @patch("bot.scraping.extractor._get_llm", new_callable=AsyncMock)
    @patch("bot.scraping.extractor._ainvoke_with_retry", new_callable=AsyncMock)
    def test_hechos_sin_texto_levanta_valueerror(self, mock_ainvoke, mock_get_llm):
        mock_get_llm.return_value = MagicMock()
        mock_ainvoke.return_value = json.dumps({
            "hechos": [
                {"categoria": "soluciones"},
                {"categoria": "analitica"},
            ]
        })

        from bot.rag.indexador import _hechos_de_documento
        with self.assertRaises(ValueError):
            asyncio.run(_hechos_de_documento("texto crudo"))


class BorrarChunksDePaginasPurgadasTest(TestCase):
    @patch("bot.rag.cliente.get_supabase_client")
    def test_borra_por_lista_de_scraped_page_ids(self, mock_get_client):
        mock_cliente = MagicMock()
        mock_get_client.return_value = mock_cliente

        from bot.rag.indexador import borrar_chunks_de_paginas_purgadas
        resultado = borrar_chunks_de_paginas_purgadas([3, 7], cliente="renault")

        mock_cliente.table.return_value.delete.return_value.in_.assert_called_once_with(
            "scraped_page_id", [3, 7]
        )
        mock_cliente.table.return_value.delete.return_value.in_.return_value.execute.assert_called_once()
        self.assertTrue(resultado)

    @patch("bot.rag.cliente.get_supabase_client")
    def test_nunca_levanta_si_supabase_falla(self, mock_get_client):
        mock_get_client.side_effect = Exception("sin credenciales")

        from bot.rag.indexador import borrar_chunks_de_paginas_purgadas
        resultado = borrar_chunks_de_paginas_purgadas([3], cliente="renault")

        self.assertFalse(resultado)

    @patch("bot.rag.cliente.get_supabase_client")
    def test_propaga_el_cliente_a_get_supabase_client(self, mock_get_client):
        mock_get_client.return_value = MagicMock()

        from bot.rag.indexador import borrar_chunks_de_paginas_purgadas
        borrar_chunks_de_paginas_purgadas([3], cliente="astara")

        mock_get_client.assert_called_once_with(cliente="astara")


class RerankReintentoTest(TestCase):
    """El rerank no tenia ningun reintento, a diferencia de las llamadas al LLM
    (`_ainvoke_with_retry` en bot/flow/graph.py, que trata el 429 como
    transitorio con backoff).

    Hallazgo real del 2026-09-07: midiendo latencia, tres consultas de RAG en
    un minuto agotaron el limite de peticiones de Cohere y OpenRouter devolvio
    `HTTP 429: You are past the per minute request limit`. El codigo lo trataba
    igual que a un fallo permanente -- devolvia [] sin reintentar y sin
    registrar la causa (el `logger.warning` no llevaba `exc_info` ni el status),
    asi que el sintoma era el bot diciendole al cliente que no tenia el dato
    sobre la garantia, con la informacion indexada y ya recuperada, y nada
    diagnosticable en los logs."""

    def _chunks(self):
        return [
            {"contenido": "el tratamiento de datos cumple la ley 21.719",
             "fuente_url": "https://in-touch.cl/datos-y-seguridad/", "categoria": "datos_y_seguridad"},
            {"contenido": "paneles de supervision y dashboards",
             "fuente_url": "https://in-touch.cl/analitica/", "categoria": "analitica"},
        ]

    def _respuesta_ok(self, resultados):
        resp = MagicMock()
        resp.json.return_value = {"results": resultados}
        resp.raise_for_status = MagicMock()
        return resp

    def _respuesta_error(self, status):
        import httpx
        resp = MagicMock()
        resp.status_code = status
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=resp,
        )
        return resp

    @patch("bot.rag.tool.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.Client.post")
    def test_429_se_reintenta_y_el_segundo_intento_sirve(self, mock_post, _sleep):
        from bot.rag.tool import _rerankear
        mock_post.side_effect = [
            self._respuesta_error(429),
            self._respuesta_ok([{"index": 0, "relevance_score": 0.9}]),
        ]

        resultado = asyncio.run(_rerankear("como tratan mis datos", self._chunks()))

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual([c["categoria"] for c in resultado], ["datos_y_seguridad"])

    @patch("bot.rag.tool.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.Client.post")
    def test_4xx_permanente_no_gasta_reintentos(self, mock_post, _sleep):
        """Una key invalida no se arregla reintentando: mismo criterio que
        `_es_error_permanente` en bot/flow/graph.py."""
        from bot.rag.tool import _rerankear
        mock_post.return_value = self._respuesta_error(401)

        asyncio.run(_rerankear("cualquier cosa", self._chunks()))

        self.assertEqual(mock_post.call_count, 1)

    @patch("bot.rag.tool.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.Client.post")
    def test_429_persistente_cae_a_los_candidatos_en_vez_de_mentir(self, mock_post, _sleep):
        from bot.rag.tool import _rerankear, _RERANK_MAX_INTENTOS
        mock_post.return_value = self._respuesta_error(429)

        resultado = asyncio.run(_rerankear("como tratan mis datos", self._chunks()))

        self.assertEqual(mock_post.call_count, _RERANK_MAX_INTENTOS)
        self.assertEqual(resultado, self._chunks())

    @patch("bot.rag.tool.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.Client.post")
    def test_el_fallo_queda_registrado_con_su_causa(self, mock_post, _sleep):
        """El defecto que hizo esto invisible: un warning sin `exc_info` ni
        status. Un fallo que no grita se convierte en un dato falso (#27)."""
        from bot.rag.tool import _rerankear
        mock_post.return_value = self._respuesta_error(429)

        with self.assertLogs("bot.rag.tool", level="ERROR") as capturado:
            asyncio.run(_rerankear("como tratan mis datos", self._chunks()))

        registro = "\n".join(capturado.output)
        self.assertIn("429", registro)
        self.assertIn("HTTPStatusError", registro)

    @patch("bot.rag.tool.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.Client.post")
    def test_un_rerank_exitoso_sin_relevantes_sigue_devolviendo_vacio(self, mock_post, _sleep):
        """La distincion que el bug borraba: "el rerank dice que nada sirve"
        (vacio, legitimo) contra "el rerank no pudo correr" (fallback). No se
        pueden confundir o volvemos al punto de partida."""
        from bot.rag.tool import _rerankear
        mock_post.return_value = self._respuesta_ok([{"index": 0, "relevance_score": 0.01}])

        resultado = asyncio.run(_rerankear("pregunta sin relacion", self._chunks()))

        self.assertEqual(resultado, [])
        self.assertEqual(mock_post.call_count, 1)
