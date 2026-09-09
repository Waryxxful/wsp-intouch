from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bot.models import ScrapingSource
from bot.scraping.scheduler import tick


class TickTest(TestCase):
    def _source(self, frecuencia_horas, url="https://x.cl/"):
        return ScrapingSource.objects.create(url=url, frecuencia_horas=frecuencia_horas)

    def _run_started_hace(self, source, horas, estado="ok"):
        # started_at es auto_now_add: hay que pisarlo con un UPDATE aparte.
        run = source.runs.create(url=source.url, estado=estado)
        source.runs.filter(pk=run.pk).update(started_at=timezone.now() - timedelta(hours=horas))
        return run

    @patch("bot.scraping.scheduler.run_scrape")
    def test_frecuencia_desactivada_no_corre(self, mock_run):
        self._source(frecuencia_horas=0)
        tick()
        mock_run.assert_not_called()

    @patch("bot.scraping.scheduler.run_scrape")
    def test_sin_fuentes_no_corre(self, mock_run):
        tick()
        mock_run.assert_not_called()

    @patch("bot.scraping.scheduler.run_scrape")
    def test_nunca_corrio_antes_corre_ahora(self, mock_run):
        source = self._source(frecuencia_horas=6)
        tick()
        mock_run.assert_called_once_with(source)

    @patch("bot.scraping.scheduler.run_scrape")
    def test_ultimo_run_reciente_no_corre(self, mock_run):
        source = self._source(frecuencia_horas=6)
        self._run_started_hace(source, 1)
        tick()
        mock_run.assert_not_called()

    @patch("bot.scraping.scheduler.run_scrape")
    def test_ultimo_run_vencido_corre(self, mock_run):
        source = self._source(frecuencia_horas=6)
        self._run_started_hace(source, 7)
        tick()
        mock_run.assert_called_once_with(source)

    @patch("bot.scraping.scheduler.run_scrape")
    def test_scrape_ya_corriendo_no_corre_de_nuevo(self, mock_run):
        # "corriendo" reciente (5 min) bloquea el reintento -- distinto del caso
        # de una corrida "corriendo" vieja/huerfana, ver test_corriendo_vieja_no_bloquea_reintento.
        source = self._source(frecuencia_horas=6)
        self._run_started_hace(source, 5 / 60, estado="corriendo")
        tick()
        mock_run.assert_not_called()

    @patch("bot.scraping.scheduler.run_scrape")
    def test_corriendo_vieja_no_bloquea_reintento(self, mock_run):
        # frecuencia_horas chica (6 min) para que, una vez que el "corriendo"
        # viejo deja de bloquear, tampoco bloquee el chequeo de frecuencia
        # (la corrida "corriendo" de 65 min ya supera ambos umbrales --
        # _CORRIENDO_STALE_MINUTOS=60 y la frecuencia de 6 min).
        source = self._source(frecuencia_horas=0.1)
        self._run_started_hace(source, 65 / 60, estado="corriendo")
        tick()
        mock_run.assert_called_once_with(source)

    @patch("bot.scraping.scheduler.run_scrape")
    def test_multiples_fuentes_evaluadas_independientemente(self, mock_run):
        vencida = self._source(frecuencia_horas=6, url="https://a.cl/")
        self._run_started_hace(vencida, 7)
        reciente = self._source(frecuencia_horas=6, url="https://b.cl/")
        self._run_started_hace(reciente, 1)
        tick()
        mock_run.assert_called_once_with(vencida)
