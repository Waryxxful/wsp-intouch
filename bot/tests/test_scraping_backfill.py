from django.test import TestCase

from bot.models import ScrapeRun, ScrapingSource, Setting
from bot.scraping.backfill import agrupar_runs_en_fuentes


class BackfillTest(TestCase):
    def test_agrupa_runs_por_url_en_fuentes_distintas(self):
        ScrapeRun.objects.create(url="https://chery.cl/", estado="ok")
        ScrapeRun.objects.create(url="https://chery.cl/", estado="error")
        ScrapeRun.objects.create(url="https://toyota.cl/", estado="ok")

        creadas = agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting)

        self.assertEqual(creadas, 2)
        chery = ScrapingSource.objects.get(url="https://chery.cl/")
        toyota = ScrapingSource.objects.get(url="https://toyota.cl/")
        self.assertEqual(chery.runs.count(), 2)
        self.assertEqual(toyota.runs.count(), 1)

    def test_usa_frecuencia_del_setting_para_la_url_activa(self):
        Setting.objects.create(key="scraping_target_url", value="https://chery.cl/")
        Setting.objects.create(key="scraping_frecuencia_horas", value="12")
        ScrapeRun.objects.create(url="https://chery.cl/", estado="ok")
        ScrapeRun.objects.create(url="https://toyota.cl/", estado="ok")

        agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting)

        self.assertEqual(ScrapingSource.objects.get(url="https://chery.cl/").frecuencia_horas, 12)
        self.assertEqual(ScrapingSource.objects.get(url="https://toyota.cl/").frecuencia_horas, 0)

    def test_url_activa_sin_runs_previos_crea_fuente_igual(self):
        Setting.objects.create(key="scraping_target_url", value="https://nueva.cl/")
        creadas = agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting)
        self.assertEqual(creadas, 1)
        self.assertTrue(ScrapingSource.objects.filter(url="https://nueva.cl/").exists())

    def test_sin_runs_ni_setting_no_crea_nada(self):
        creadas = agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting)
        self.assertEqual(creadas, 0)

    def test_default_estampa_cliente_renault(self):
        # cliente="renault" default a proposito, no settings.CLIENTE_ACTIVO --
        # esta migracion es anterior a multi-cliente, los ScrapeRun que
        # backfillea son inequivocamente de esa era.
        ScrapeRun.objects.create(url="https://chery.cl/", estado="ok")
        agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting)
        self.assertEqual(ScrapingSource.objects.get(url="https://chery.cl/").cliente, "renault")

    def test_cliente_explicito_gana_sobre_el_default(self):
        ScrapeRun.objects.create(url="https://astararetail.cl/", estado="ok")
        agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting, cliente="astara")
        fuente = ScrapingSource.todos_los_clientes.get(url="https://astararetail.cl/")
        self.assertEqual(fuente.cliente, "astara")

    def test_correr_dos_veces_no_duplica_fuentes(self):
        ScrapeRun.objects.create(url="https://chery.cl/", estado="ok")
        ScrapeRun.objects.create(url="https://toyota.cl/", estado="ok")
        Setting.objects.create(key="scraping_target_url", value="https://nueva.cl/")

        primera = agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting)
        segunda = agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting)

        self.assertEqual(primera, 3)
        self.assertEqual(segunda, 0)
        self.assertEqual(ScrapingSource.objects.count(), 3)
