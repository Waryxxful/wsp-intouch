import logging
import sys
import threading
from datetime import timedelta

from django.utils import timezone

from bot.models import ScrapingSource

from .runner import _CORRIENDO_STALE_MINUTOS, run_scrape

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 5 * 60
# _CORRIENDO_STALE_MINUTOS vive en runner.py (execute_scrape tambien lo
# necesita, ver ese modulo) -- aca decide cuando un ScrapeRun que quedo en
# estado="corriendo" por un restart del contenedor a mitad de un scrape deja
# de ser bloqueante. No representa la duracion esperada de una corrida real
# (un crawl+extract normal termina en pocos minutos, pero con el crawler en
# max_pages=120 y hasta MAX_CHUNKS=20 llamadas secuenciales al LLM en la
# extraccion, dejamos margen holgado antes de considerar la corrida colgada).
_started = False


def start_scheduler() -> None:
    """Arranca el chequeo periodico en un thread daemon -- mismo patron
    fire-and-forget que utils.dios_registration, sin Celery/cron (no hay
    infra de colas en este proyecto). Se salta en `manage.py test` para no
    dejar un thread vivo pegandole a la DB durante la suite.

    # ponytail: gunicorn corre con 2 workers, cada uno con su propio thread
    # (esta bandera es por-proceso). Dos ticks casi simultaneos en el
    # arranque podrian ambos ver "due" antes de que el primer ScrapeRun
    # quede en estado=corriendo y disparar 2 scrapes iguales -- el upsert de
    # runner.py es idempotente, y la purga de ScrapedPage de runs anteriores
    # del mismo source (ver execute_scrape) excluye explicitamente los runs
    # que todavia estan en estado="corriendo", para no borrarle las paginas
    # a un scrape hermano en curso (incluido el camino mas ancho de
    # _tick_source arrancando un segundo run tras _CORRIENDO_STALE_MINUTOS
    # si el primero sigue vivo). Upgrade si molesta: lock (select_for_update)
    # antes de crear el ScrapeRun.
    """
    global _started
    if _started or "test" in sys.argv:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:
            logger.exception("[scraping-scheduler] tick fallo")
        threading.Event().wait(_CHECK_INTERVAL_SECONDS)


def tick() -> None:
    """Corre un scrape por cada ScrapingSource cuya frecuencia configurada
    ya se cumplio. Cada fuente se evalua de forma independiente -- una
    fuente con frecuencia_horas=0 nunca se dispara sola (solo manual, ver
    admin_panel/views.py::api_scraping_source_run)."""
    for source in ScrapingSource.objects.filter(frecuencia_horas__gt=0):
        _tick_source(source)


def _tick_source(source: ScrapingSource) -> None:
    last_run = source.runs.order_by("-started_at").first()
    if last_run and last_run.estado == "corriendo":
        if timezone.now() - last_run.started_at < timedelta(minutes=_CORRIENDO_STALE_MINUTOS):
            return
        # corrida "corriendo" hace mas de _CORRIENDO_STALE_MINUTOS -- asumimos que
        # quedo huerfana por un restart del contenedor a mitad de un scrape, no la
        # tratamos como bloqueante
    if last_run and timezone.now() - last_run.started_at < timedelta(hours=source.frecuencia_horas):
        return
    run_scrape(source)
