from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase

from bot.models import ScrapeRun, ScrapingSource


class ScrapeBusinessSiteCommandTest(TestCase):
    @patch("bot.management.commands.scrape_business_site.run_scrape")
    def test_command_usa_la_url_del_argumento(self, mock_run_scrape):
        mock_run_scrape.return_value = ScrapeRun.objects.create(
            url="https://ejemplo.cl", estado="ok", paginas_procesadas=5,
        )
        out = StringIO()
        call_command("scrape_business_site", "https://ejemplo.cl", stdout=out)
        source_arg = mock_run_scrape.call_args[0][0]
        self.assertEqual(source_arg.url, "https://ejemplo.cl")
        self.assertIn("OK", out.getvalue())
        self.assertIn("5", out.getvalue())

    @patch("bot.management.commands.scrape_business_site.run_scrape")
    def test_command_reporta_error(self, mock_run_scrape):
        mock_run_scrape.return_value = ScrapeRun.objects.create(
            url="https://ejemplo.cl", estado="error", error_detalle="IP no permitida",
        )
        out = StringIO()
        call_command("scrape_business_site", "https://ejemplo.cl", stdout=out)
        self.assertIn("ERROR", out.getvalue())
        self.assertIn("IP no permitida", out.getvalue())

    def test_command_sin_url_ni_setting_levanta_error(self):
        with self.assertRaises(CommandError):
            call_command("scrape_business_site")

    @patch("bot.management.commands.scrape_business_site.run_scrape")
    def test_command_sin_cliente_crea_la_fuente_como_renault(self, mock_run_scrape):
        mock_run_scrape.return_value = ScrapeRun.objects.create(url="https://ejemplo.cl", estado="ok")
        call_command("scrape_business_site", "https://ejemplo.cl", stdout=StringIO())
        self.assertEqual(ScrapingSource.todos_los_clientes.get(url="https://ejemplo.cl").cliente, "renault")

    @patch("bot.management.commands.scrape_business_site.run_scrape")
    def test_command_con_cliente_astara_crea_la_fuente_como_astara(self, mock_run_scrape):
        mock_run_scrape.return_value = ScrapeRun.objects.create(url="https://astararetail.cl", estado="ok")
        call_command(
            "scrape_business_site", "https://astararetail.cl", "--cliente", "astara", stdout=StringIO(),
        )
        self.assertEqual(ScrapingSource.todos_los_clientes.get(url="https://astararetail.cl").cliente, "astara")
        source_arg = mock_run_scrape.call_args[0][0]
        self.assertEqual(source_arg.cliente, "astara")

    @patch("bot.management.commands.scrape_business_site.run_scrape")
    def test_command_misma_url_para_dos_clientes_no_colisiona(self, mock_run_scrape):
        ScrapingSource.todos_los_clientes.create(url="https://x.cl", cliente="renault")
        mock_run_scrape.return_value = ScrapeRun.objects.create(url="https://x.cl", estado="ok")
        call_command("scrape_business_site", "https://x.cl", "--cliente", "astara", stdout=StringIO())
        self.assertEqual(ScrapingSource.todos_los_clientes.filter(url="https://x.cl").count(), 2)

    def test_command_cliente_invalido_levanta_error(self):
        with self.assertRaises(CommandError):
            call_command("scrape_business_site", "https://ejemplo.cl", "--cliente", "mazda")
