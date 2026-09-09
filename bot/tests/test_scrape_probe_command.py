from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class ScrapeProbeCommandTest(SimpleTestCase):
    @patch("bot.management.commands.scrape_probe.crawl")
    def test_reporta_paginas_y_errores_con_limites_chicos_por_defecto(self, mock_crawl):
        mock_crawl.return_value = (
            [{"url": "https://ejemplo.cl/", "texto": "hola"}],
            [{"url": "https://ejemplo.cl/roto", "error": "timeout"}],
        )
        out = StringIO()
        call_command("scrape_probe", "https://ejemplo.cl/", stdout=out)
        salida = out.getvalue()

        self.assertIn("1 pagina", salida)
        self.assertIn("1 error", salida)
        self.assertIn("https://ejemplo.cl/roto", salida)
        self.assertIn("timeout", salida)

        _, kwargs = mock_crawl.call_args
        self.assertEqual(kwargs["max_pages"], 3)
        self.assertEqual(kwargs["max_depth"], 1)
        self.assertEqual(kwargs["timeout"], 5)

    @patch("bot.management.commands.scrape_probe.extract_catalog")
    @patch("bot.management.commands.scrape_probe.crawl")
    def test_no_llama_al_llm_sin_el_flag(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://ejemplo.cl/", "texto": "hola"}], [])
        call_command("scrape_probe", "https://ejemplo.cl/", stdout=StringIO())
        mock_extract.assert_not_called()

    @patch("bot.management.commands.scrape_probe.extract_catalog")
    @patch("bot.management.commands.scrape_probe.crawl")
    def test_con_llm_prueba_la_extraccion(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([{"url": "https://ejemplo.cl/", "texto": "hola"}], [])
        mock_extract.return_value = {"servicios": [{"nombre": "corte"}], "sucursales": []}
        out = StringIO()
        call_command("scrape_probe", "https://ejemplo.cl/", "--con-llm", stdout=out)
        mock_extract.assert_called_once()
        self.assertIn("1 servicio", out.getvalue())

    @patch("bot.management.commands.scrape_probe.extract_catalog")
    @patch("bot.management.commands.scrape_probe.crawl")
    def test_con_llm_sin_paginas_no_llama_al_llm(self, mock_crawl, mock_extract):
        mock_crawl.return_value = ([], [])
        out = StringIO()
        call_command("scrape_probe", "https://ejemplo.cl/", "--con-llm", stdout=out)
        mock_extract.assert_not_called()
        self.assertIn("se omite", out.getvalue())

    @patch("bot.management.commands.scrape_probe.crawl")
    def test_permite_overridear_limites(self, mock_crawl):
        mock_crawl.return_value = ([], [])
        call_command(
            "scrape_probe", "https://ejemplo.cl/",
            "--max-pages", "10", "--max-depth", "2", "--timeout", "8",
            stdout=StringIO(),
        )
        _, kwargs = mock_crawl.call_args
        self.assertEqual(kwargs["max_pages"], 10)
        self.assertEqual(kwargs["max_depth"], 2)
        self.assertEqual(kwargs["timeout"], 8)
