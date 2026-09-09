import time

from django.core.management.base import BaseCommand

from bot.scraping.crawler import crawl
from bot.scraping.extractor import extract_catalog


class Command(BaseCommand):
    help = (
        "Diagnostico rapido de scraping: crawlea una URL con limites chicos "
        "(por defecto 3 paginas, profundidad 1, timeout 5s) para saber en "
        "segundos si hay conectividad/DNS y si el crawler puede parsear el "
        "sitio, sin esperar el scrape completo (que con los defaults de "
        "produccion -- 60 paginas, profundidad 4 -- puede tardar varios "
        "minutos incluso cuando todo funciona bien). No crea ni modifica "
        "ScrapingSource/ScrapeRun: es de solo lectura, no persiste nada."
    )

    def add_arguments(self, parser):
        parser.add_argument("url", help="URL a probar")
        parser.add_argument("--max-pages", type=int, default=3)
        parser.add_argument("--max-depth", type=int, default=1)
        parser.add_argument("--timeout", type=int, default=5)
        parser.add_argument(
            "--con-llm",
            action="store_true",
            help=(
                "Ademas del crawl, manda el texto obtenido al LLM extractor "
                "(DeepSeek). Mas lento (segundos-minutos segun la API) -- "
                "sirve para probar la API key/conexion del LLM por separado "
                "del crawl."
            ),
        )

    def handle(self, *args, **options):
        url = options["url"]
        max_pages = options["max_pages"]
        max_depth = options["max_depth"]
        timeout = options["timeout"]

        self.stdout.write(
            f"Probando {url} (max_pages={max_pages}, max_depth={max_depth}, timeout={timeout}s)..."
        )
        inicio = time.monotonic()
        paginas, errores = crawl(url, max_depth=max_depth, max_pages=max_pages, timeout=timeout)
        elapsed = time.monotonic() - inicio

        self.stdout.write(
            self.style.SUCCESS(
                f"Crawl terminado en {elapsed:.1f}s — {len(paginas)} pagina(s), {len(errores)} error(es)."
            )
        )
        for p in paginas:
            self.stdout.write(f"  OK  {p['url']} ({len(p['texto'])} chars)")
        for e in errores:
            self.stdout.write(self.style.WARNING(f"  ERR {e['url']}: {e['error']}"))

        if not options["con_llm"]:
            return
        if not paginas:
            self.stdout.write(self.style.WARNING("Sin paginas obtenidas, se omite la prueba del LLM."))
            return

        self.stdout.write("Probando extraccion LLM (DeepSeek)...")
        inicio_llm = time.monotonic()
        try:
            catalogo = extract_catalog(paginas)
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"LLM ERROR tras {time.monotonic() - inicio_llm:.1f}s: {exc}")
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"LLM OK en {time.monotonic() - inicio_llm:.1f}s — "
                f"{len(catalogo.get('servicios', []))} servicio(s), "
                f"{len(catalogo.get('sucursales', []))} sucursal(es)."
            )
        )
