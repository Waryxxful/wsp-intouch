from django.test import TestCase

from bot.models import ScrapeRun, ScrapingSource


class ScrapingSourceModelTest(TestCase):
    def test_str_usa_nombre_si_existe(self):
        source = ScrapingSource.objects.create(url="https://chery.cl/", nombre="Chery")
        self.assertEqual(str(source), "Chery")

    def test_str_usa_url_si_no_hay_nombre(self):
        source = ScrapingSource.objects.create(url="https://chery.cl/")
        self.assertEqual(str(source), "https://chery.cl/")

    def test_scraperun_puede_asociarse_a_una_fuente(self):
        source = ScrapingSource.objects.create(url="https://chery.cl/")
        run = ScrapeRun.objects.create(source=source, url=source.url, estado="ok")
        self.assertEqual(source.runs.count(), 1)
        self.assertEqual(run.source, source)

    def test_scraperun_tiene_catalogo_extraido_y_paginas_con_error_por_defecto(self):
        run = ScrapeRun.objects.create(url="https://chery.cl/", estado="ok")
        self.assertEqual(run.catalogo_extraido, {})
        self.assertEqual(run.paginas_con_error, [])
