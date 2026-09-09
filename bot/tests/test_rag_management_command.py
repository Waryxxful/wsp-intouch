from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from bot.models import ScrapedPage, ScrapeRun, ScrapingSource


def _cliente_con_ids(ids):
    """Mock de supabase cuyo select(...).range(...).execute().data devuelve una
    fila por cada id de `ids` (y nada mas alla de la primera pagina)."""
    cliente = MagicMock()
    filas = [{"scraped_page_id": i} for i in ids]
    tabla = cliente.table.return_value
    tabla.select.return_value.order.return_value.range.return_value.execute.side_effect = [
        MagicMock(data=filas), MagicMock(data=[]),
    ]
    return cliente


class ReindexarConocimientoRagCommandTest(TestCase):
    def _pagina(self, url="https://renault.cl/koleos/", texto="info del koleos"):
        source = ScrapingSource.objects.create(url="https://renault.cl/")
        run = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        run.finished_at = timezone.now()
        run.save()
        return ScrapedPage.objects.create(run=run, url=url, texto=texto)

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    @patch("bot.management.commands.reindexar_conocimiento_rag.indexar_pagina_en_supabase")
    def test_reindexa_cada_pagina_delegando_el_borrado_al_indexador(self, mock_indexar, mock_get_client):
        # El delete-by-fuente_url ya NO vive aca: lo hace el indexador, asi el
        # hook de scraping hereda el mismo invariante (antes solo lo tenia
        # este comando y cada re-scrape duplicaba chunks).
        pagina = self._pagina()
        mock_get_client.return_value = _cliente_con_ids([pagina.id])

        out = StringIO()
        call_command("reindexar_conocimiento_rag", stdout=out)

        mock_indexar.assert_called_once_with(pagina, cliente="renault")
        cliente = mock_get_client.return_value
        cliente.table.return_value.delete.return_value.eq.assert_not_called()
        self.assertIn("1 paginas reindexadas", out.getvalue())
        self.assertIn("0 filas huerfanas", out.getvalue())

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    @patch("bot.management.commands.reindexar_conocimiento_rag.indexar_pagina_en_supabase")
    def test_sin_paginas_no_falla(self, mock_indexar, mock_get_client):
        mock_get_client.return_value = _cliente_con_ids([])
        out = StringIO()
        call_command("reindexar_conocimiento_rag", stdout=out)
        mock_indexar.assert_not_called()

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    @patch("bot.management.commands.reindexar_conocimiento_rag.indexar_pagina_en_supabase")
    def test_barre_las_filas_huerfanas_y_lo_reporta(self, mock_indexar, mock_get_client):
        pagina = self._pagina()
        mock_get_client.return_value = _cliente_con_ids([pagina.id, 111, 111, 999])

        out = StringIO()
        call_command("reindexar_conocimiento_rag", stdout=out)

        cliente = mock_get_client.return_value
        borrado = cliente.table.return_value.delete.return_value
        borrado.in_.assert_called_once_with("scraped_page_id", [111, 999])
        borrado.in_.return_value.execute.assert_called_once()
        self.assertIn("3 filas huerfanas borradas", out.getvalue())

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    @patch("bot.management.commands.reindexar_conocimiento_rag.indexar_pagina_en_supabase")
    def test_reporta_cuantas_paginas_fallaron_al_indexar(self, mock_indexar, mock_get_client):
        # Necesitamos dos paginas de dos fuentes diferentes para no violar unique constraint
        source1 = ScrapingSource.objects.create(url="https://renault.cl/")
        run1 = ScrapeRun.objects.create(source=source1, url=source1.url, estado="ok")
        run1.finished_at = timezone.now()
        run1.save()
        pagina_ok = ScrapedPage.objects.create(run=run1, url="https://renault.cl/ok/", texto="ok")

        source2 = ScrapingSource.objects.create(url="https://peugeot.cl/")
        run2 = ScrapeRun.objects.create(source=source2, url=source2.url, estado="ok")
        run2.finished_at = timezone.now()
        run2.save()
        pagina_mala = ScrapedPage.objects.create(run=run2, url="https://peugeot.cl/mala/", texto="mala")

        mock_get_client.return_value = _cliente_con_ids([pagina_ok.id, pagina_mala.id])
        mock_indexar.side_effect = lambda p, cliente: p.pk != pagina_mala.pk

        out = StringIO()
        call_command("reindexar_conocimiento_rag", stdout=out)

        self.assertIn("(1 ok, 1 fallaron)", out.getvalue())

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    @patch("bot.management.commands.reindexar_conocimiento_rag.indexar_pagina_en_supabase")
    def test_cliente_astara_solo_reindexa_paginas_de_fuentes_astara(self, mock_indexar, mock_get_client):
        # Regresion directa del bug real 2026-08-27/28 (docs/PENDIENTES.md):
        # este comando iteraba TODAS las ScrapedPage de TODOS los clientes
        # sin ningun filtro, y las mandaba al schema de Supabase que sea que
        # RAG_SCHEMA (env var global de proceso) tuviera seteado en ese
        # momento -- una via directa para recrear la contaminacion cruzada
        # ya confirmada y purgada contra el proyecto real.
        pagina_renault = self._pagina()
        source_astara = ScrapingSource.todos_los_clientes.create(url="https://astararetail.cl/", cliente="astara")
        run_astara = ScrapeRun.objects.create(source=source_astara, url=source_astara.url, estado="ok")
        run_astara.finished_at = timezone.now()
        run_astara.save()
        pagina_astara = ScrapedPage.objects.create(run=run_astara, url="https://astararetail.cl/jeep/", texto="jeep")
        mock_get_client.return_value = _cliente_con_ids([pagina_astara.id])

        out = StringIO()
        call_command("reindexar_conocimiento_rag", "--cliente", "astara", stdout=out)

        mock_indexar.assert_called_once_with(pagina_astara, cliente="astara")
        mock_get_client.assert_called_once_with(cliente="astara")
        self.assertIn("1 paginas reindexadas", out.getvalue())

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    @patch("bot.management.commands.reindexar_conocimiento_rag.indexar_pagina_en_supabase")
    def test_default_sin_cliente_incluye_paginas_huerfanas_de_source_nulo(self, mock_indexar, mock_get_client):
        # Convencion ya establecida en bot/scraping/runner.py: todo el
        # catalogo historico anterior al campo `cliente` es renault -- un
        # ScrapedPage cuyo run.source es null (dato viejo) debe seguir
        # entrando en el reindexado por default, no quedar huerfano.
        run_sin_source = ScrapeRun.objects.create(source=None, url="https://x.cl/", estado="ok")
        run_sin_source.finished_at = timezone.now()
        run_sin_source.save()
        pagina_huerfana = ScrapedPage.objects.create(run=run_sin_source, url="https://x.cl/vieja/", texto="vieja")
        mock_get_client.return_value = _cliente_con_ids([pagina_huerfana.id])

        out = StringIO()
        call_command("reindexar_conocimiento_rag", stdout=out)

        mock_indexar.assert_called_once_with(pagina_huerfana, cliente="renault")


class BorrarFilasHuerfanasTest(TestCase):
    def _pagina_fake(self, pk):
        pagina = MagicMock()
        pagina.id = pk
        return pagina

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    def test_borra_solo_los_ids_que_no_estan_en_el_set_de_paginas(self, mock_get_client):
        from bot.management.commands.reindexar_conocimiento_rag import _borrar_filas_huerfanas
        mock_get_client.return_value = _cliente_con_ids([7, 7, 8, 1])

        borradas = _borrar_filas_huerfanas([self._pagina_fake(7), self._pagina_fake(8)], cliente="renault")

        self.assertEqual(borradas, 1)
        borrado = mock_get_client.return_value.table.return_value.delete.return_value
        borrado.in_.assert_called_once_with("scraped_page_id", [1])

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    def test_sin_huerfanas_no_borra_nada(self, mock_get_client):
        from bot.management.commands.reindexar_conocimiento_rag import _borrar_filas_huerfanas
        mock_get_client.return_value = _cliente_con_ids([7, 7, 8])

        borradas = _borrar_filas_huerfanas([self._pagina_fake(7), self._pagina_fake(8)], cliente="renault")

        self.assertEqual(borradas, 0)
        mock_get_client.return_value.table.return_value.delete.assert_not_called()

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    def test_ignora_filas_con_scraped_page_id_nulo(self, mock_get_client):
        # NULL no es huerfano identificable: no se puede borrar por id y
        # borrarlo "por si acaso" seria destruir filas ajenas al mapeo.
        from bot.management.commands.reindexar_conocimiento_rag import _borrar_filas_huerfanas
        mock_get_client.return_value = _cliente_con_ids([None, 7])

        borradas = _borrar_filas_huerfanas([self._pagina_fake(7)], cliente="renault")

        self.assertEqual(borradas, 0)
        mock_get_client.return_value.table.return_value.delete.assert_not_called()

    @patch("bot.management.commands.reindexar_conocimiento_rag.get_supabase_client")
    def test_pagina_las_lecturas_hasta_agotar_la_tabla(self, mock_get_client):
        from bot.management.commands.reindexar_conocimiento_rag import _PAGINA_SUPABASE, _borrar_filas_huerfanas
        cliente = MagicMock()
        primera = [{"scraped_page_id": 1}] * _PAGINA_SUPABASE
        cliente.table.return_value.select.return_value.order.return_value.range.return_value.execute.side_effect = [
            MagicMock(data=primera), MagicMock(data=[{"scraped_page_id": 2}]),
        ]
        mock_get_client.return_value = cliente

        borradas = _borrar_filas_huerfanas([], cliente="renault")

        self.assertEqual(borradas, _PAGINA_SUPABASE + 1)
        cliente.table.return_value.select.return_value.order.assert_called_with("id")
        rangos = [
            c.args for c in cliente.table.return_value.select.return_value.order.return_value.range.call_args_list
        ]
        self.assertEqual(rangos, [(0, _PAGINA_SUPABASE - 1), (_PAGINA_SUPABASE, 2 * _PAGINA_SUPABASE - 1)])
