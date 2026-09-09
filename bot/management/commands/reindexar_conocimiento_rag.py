from django.core.management.base import BaseCommand
from django.db.models import Q

from bot.models import CLIENTE_CHOICES, ScrapedPage
from bot.rag.cliente import get_supabase_client
from bot.rag.indexador import indexar_pagina_en_supabase

# Limite por defecto de filas que devuelve una query de supabase-py; se pagina
# a mano con .range() para no perder filas si la tabla crece.
_PAGINA_SUPABASE = 1000


def _ids_indexados(cliente) -> list:
    """Todos los scraped_page_id presentes hoy en documentos_conocimiento (con
    repeticion: hay una fila por chunk)."""
    ids, offset = [], 0
    while True:
        resp = (
            cliente.table("documentos_conocimiento")
            .select("scraped_page_id")
            .order("id")
            .range(offset, offset + _PAGINA_SUPABASE - 1)
            .execute()
        )
        filas = resp.data or []
        ids.extend(fila.get("scraped_page_id") for fila in filas)
        if len(filas) < _PAGINA_SUPABASE:
            return ids
        offset += _PAGINA_SUPABASE


def _borrar_filas_huerfanas(paginas: list, cliente: str) -> int:
    """Borra en Supabase las filas cuyo scraped_page_id ya no corresponde a
    ninguna ScrapedPage viva en Django (paginas borradas, o basura de tests
    que se escribio al proyecto real). Devuelve cuantas filas se borraron.
    `cliente` escopea a QUE schema de Supabase se conecta -- ver
    docs/PENDIENTES.md, bug real 2026-08-27/28 de contaminacion cruzada."""
    supabase = get_supabase_client(cliente=cliente)
    vigentes = {pagina.id for pagina in paginas}
    ids = _ids_indexados(supabase)
    huerfanos = {i for i in ids if i is not None and i not in vigentes}
    if not huerfanos:
        return 0
    supabase.table("documentos_conocimiento").delete().in_("scraped_page_id", sorted(huerfanos)).execute()
    return sum(1 for i in ids if i in huerfanos)


class Command(BaseCommand):
    help = (
        "Reindexa en Supabase todas las ScrapedPage ya guardadas del cliente indicado "
        "(el indexador borra las filas previas de cada fuente_url antes de reinsertar, "
        "asi correrlo N veces no duplica) y barre las filas huerfanas cuyo scraped_page_id "
        "ya no existe en Django. El RAG es exclusivamente para contenido no estructurado -- "
        "Sucursal, Servicio y VehiculoCatalogo nunca tienen chunk RAG, viven solo como datos "
        "estructurados (bot/business/catalogo.py, bot/business/ventas.py)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cliente", default="renault", choices=[c[0] for c in CLIENTE_CHOICES],
            help=(
                "Cliente a reindexar (default: renault). Escopea DOS cosas a la vez: "
                "que ScrapedPage se leen de Django (por el cliente de su fuente) y a que "
                "schema de Supabase se escribe -- nunca deben ir por separado, es "
                "exactamente el bug que este flag existe para prevenir (ver docs/PENDIENTES.md)."
            ),
        )

    def handle(self, *args, **options):
        cliente = options["cliente"]
        # Con el default "renault": incluye tambien las ScrapedPage cuyo
        # run.source es null -- mismo criterio ya establecido en
        # bot/scraping/runner.py ("todo el catalogo historico anterior al
        # campo cliente es renault"). Para cualquier otro cliente (ej.
        # "astara") no hay pagina historica sin source que le pertenezca.
        if cliente == "renault":
            paginas = list(ScrapedPage.objects.filter(
                Q(run__source__cliente="renault") | Q(run__source__isnull=True)
            ))
        else:
            paginas = list(ScrapedPage.objects.filter(run__source__cliente=cliente))

        paginas_ok = sum(1 for pagina in paginas if indexar_pagina_en_supabase(pagina, cliente=cliente))
        paginas_fallidas = len(paginas) - paginas_ok

        huerfanas = _borrar_filas_huerfanas(paginas, cliente=cliente)
        self.stdout.write(self.style.SUCCESS(
            f"{len(paginas)} paginas reindexadas ({paginas_ok} ok, {paginas_fallidas} fallaron), "
            f"{huerfanas} filas huerfanas borradas."
        ))
