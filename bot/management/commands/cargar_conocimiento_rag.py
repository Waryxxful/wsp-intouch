from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.models import CLIENTE_CHOICES, ScrapedPage, ScrapeRun, ScrapingSource

# Las bases de conocimiento de Cavem son archivos del repo, no paginas de un
# sitio: Cavem no tiene sitio que scrapear (docs/PENDIENTES.md #11) y el stock
# viene de planilla. Sin este comando, los .md quedaban escritos pero sin
# ninguna via de entrada al pipeline -- `reindexar_conocimiento_rag` indexa
# ScrapedPage, no archivos, asi que imprimia "0 paginas reindexadas" y el RAG
# quedaba vacio (hallazgo 2026-09-02).
DIRECTORIO_DEFAULT = settings.BASE_DIR / "bot" / "fixtures" / "rag"

# Prefijo de `fuente_url`. Deliberadamente NO es una URL publica: el
# `fuente_url` del chunk viaja al LLM como campo "fuente" del resultado de
# `consultar_base_conocimiento` (bot/rag/tool.py), y un https://cavem.cl/...
# inventado invita al modelo a pasarle al cliente un link que no existe. La
# ruta es la del contenedor (/app), donde el archivo realmente esta.
URL_FUENTE = "file:///app/bot/fixtures/rag"

NOMBRE_SOURCE = "Bases de conocimiento (documentos del repo)"


class Command(BaseCommand):
    help = (
        "Carga los documentos de conocimiento (.md) como ScrapedPage con es_documento=True, "
        "que es lo que `reindexar_conocimiento_rag` necesita para poder indexarlos en Supabase. "
        "Es idempotente: reescribe la misma pagina por archivo (nunca acumula duplicados) y "
        "borra las paginas cuyo archivo ya no existe. Correrlo despues de editar cualquier .md, "
        "y siempre seguido de `reindexar_conocimiento_rag --cliente <cliente>`."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cliente", default=settings.CLIENTE_ACTIVO, choices=[c[0] for c in CLIENTE_CHOICES],
            help=(
                "Cliente dueno de los documentos (default: CLIENTE_ACTIVO del proceso). "
                "Escopea la ScrapingSource, que es por donde `reindexar_conocimiento_rag` "
                "decide a que schema de Supabase escribe -- si no coincide, el conocimiento "
                "de un cliente termina en el schema de otro (bug real 2026-08-27/28)."
            ),
        )
        parser.add_argument(
            "--directorio", default=str(DIRECTORIO_DEFAULT),
            help=f"Directorio con los .md a cargar (default: {DIRECTORIO_DEFAULT}).",
        )

    def handle(self, *args, **options):
        cliente = options["cliente"]
        directorio = Path(options["directorio"])
        if not directorio.is_dir():
            raise CommandError(f"no existe el directorio {directorio}")

        archivos = sorted(directorio.glob("*.md"))
        if not archivos:
            raise CommandError(f"no hay archivos .md en {directorio}")

        # todos_los_clientes: `objects` filtra por CLIENTE_ACTIVO y este comando
        # acepta --cliente distinto (ej. cargar cavem desde un proceso renault).
        source, source_nueva = ScrapingSource.todos_los_clientes.get_or_create(
            cliente=cliente, url=f"{URL_FUENTE}/",
            defaults={"nombre": NOMBRE_SOURCE, "frecuencia_horas": 0},
        )
        # frecuencia_horas=0: estos documentos no se re-scrapean solos, se
        # recargan con este comando cuando cambia el .md.
        run, _ = ScrapeRun.objects.get_or_create(
            source=source, url=f"{URL_FUENTE}/", defaults={"estado": "ok"},
        )

        nuevas, actualizadas = 0, 0
        for archivo in archivos:
            _, creada = ScrapedPage.objects.update_or_create(
                run=run, url=f"{URL_FUENTE}/{archivo.name}",
                defaults={
                    "texto": archivo.read_text(encoding="utf-8"),
                    # secciones=[] + es_documento=True enruta el indexado por
                    # _hechos_de_documento (bot/rag/indexador.py:150): un LLM
                    # reescribe el documento como hechos atomicos
                    # autocontenidos en vez de partirlo por largo. Es la ruta
                    # que arreglo el bug de airbags del 2026-08-24, donde el
                    # chunk correcto rankeaba fuera del top-8 porque el dato
                    # venia diluido entre otros del mismo trozo.
                    "secciones": [],
                    "es_documento": True,
                    "imagenes": [],
                },
            )
            nuevas += creada
            actualizadas += not creada

        # Un .md borrado del repo dejaria su ScrapedPage viva y por lo tanto su
        # conocimiento vigente en Supabase. Al borrarla aca, el barrido de
        # huerfanas de `reindexar_conocimiento_rag` la limpia tambien alla.
        urls_vigentes = {f"{URL_FUENTE}/{archivo.name}" for archivo in archivos}
        obsoletas, _ = ScrapedPage.objects.filter(run=run).exclude(url__in=urls_vigentes).delete()

        run.estado = "ok"
        run.paginas_procesadas = len(archivos)
        run.save(update_fields=["estado", "paginas_procesadas"])

        self.stdout.write(self.style.SUCCESS(
            f"{len(archivos)} documentos cargados para '{cliente}' "
            f"({nuevas} nuevos, {actualizadas} actualizados, {obsoletas} obsoletos borrados)"
            + (f"; ScrapingSource creada: {source.nombre}" if source_nueva else "")
        ))
        self.stdout.write(
            f"Siguiente paso: manage.py reindexar_conocimiento_rag --cliente {cliente}"
        )
