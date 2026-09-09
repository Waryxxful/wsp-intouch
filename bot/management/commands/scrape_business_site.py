from django.core.management.base import BaseCommand

from bot.models import CLIENTE_CHOICES, ScrapingSource
from bot.scraping.runner import run_scrape


class Command(BaseCommand):
    help = "Corre un scrape manual de una fuente (por URL) y muestra el resultado."

    def add_arguments(self, parser):
        parser.add_argument("url", help="URL de la fuente a scrapear (se crea si no existe todavia)")
        parser.add_argument(
            "--cliente", default="renault", choices=[c[0] for c in CLIENTE_CHOICES],
            help="Cliente al que pertenece la fuente (default: renault). La unicidad de la "
                 "URL es por cliente, asi que la misma URL puede existir para dos clientes.",
        )

    def handle(self, *args, **options):
        url = options["url"]
        cliente = options["cliente"]
        # todos_los_clientes, no objects: el manager filtrado por defecto solo ve
        # el CLIENTE_ACTIVO del proceso, que puede no ser el cliente pedido acá.
        source, created = ScrapingSource.todos_los_clientes.get_or_create(url=url, cliente=cliente)
        if created:
            self.stdout.write(f"Fuente nueva creada para {url} (cliente={cliente}, solo manual, frecuencia_horas=0).")
        self.stdout.write(f"Scrapeando {url}...")
        run = run_scrape(source)
        if run.estado == "ok":
            self.stdout.write(self.style.SUCCESS(f"OK — {run.paginas_procesadas} paginas procesadas."))
        else:
            self.stdout.write(self.style.ERROR(f"ERROR — {run.error_detalle}"))
