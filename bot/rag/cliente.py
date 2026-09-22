import os
from functools import lru_cache

from django.conf import settings
from supabase import create_client, Client


def get_supabase_client(cliente: str | None = None) -> Client:
    """Cliente de Supabase (proyecto REAL, no hay uno de test).

    El guard de sqlite existe porque escribir al proyecto real desde un shell
    o un test es un error silencioso y persistente: asi se colaron 58 filas de
    fixture ("hola", https://x.cl/) a la base de conocimiento de produccion,
    donde ganaban retrieval real. USE_SQLITE=true es el marcador que este
    codebase ya usa en todos lados para "contexto de test/desarrollo", asi que
    se toma el engine efectivo de Django como senal. El escape hatch mantiene
    posibles los write reales intencionales (ej. el rollout de
    reindexar_conocimiento_rag).

    El parametro `cliente` (ej. "astara") resuelve el schema explicitamente,
    ganandole a RAG_SCHEMA -- lo usa el pipeline de SCRAPING (que conoce el
    cliente real de cada ScrapingSource) para nunca depender de que el
    proceso tenga la env var correcta seteada. Sin `cliente` (default, usado
    por el RETRIEVAL en vivo del bot, bot/rag/tool.py), sigue resolviendo por
    RAG_SCHEMA -- resuelto una sola vez por proceso, nunca por request (ver
    docs/superpowers/specs/2026-08-25-rag-hibrido-rerank-multimarca-design.md),
    porque el bot en vivo sirve un unico cliente por proceso (CLIENTE_ACTIVO).
    Bug real 2026-08-27/28 (docs/PENDIENTES.md): un scrape de Astara corrido
    con RAG_SCHEMA=renault (el bot en vivo seguia sirviendo Renault) mando
    297 chunks de contenido de Astara al schema renault -- confirmado y
    purgado contra el Supabase real. Este parametro cierra ese gap."""
    if (settings.DATABASES["default"]["ENGINE"].endswith("sqlite3")
            and os.environ.get("SUPABASE_ALLOW_TEST_WRITES") != "1"):
        raise RuntimeError(
            "get_supabase_client() llamado con Django apuntando a sqlite (USE_SQLITE=true) -- "
            "esto escribiria en el proyecto Supabase REAL desde un contexto de test/desarrollo. "
            "Si esto es intencional, seteá SUPABASE_ALLOW_TEST_WRITES=1."
        )
    # default "public" fuera de produccion (tests, un dev sin la env var seteada).
    schema = cliente if cliente is not None else os.environ.get("RAG_SCHEMA", "public")
    return _cliente_cacheado(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"], schema)


@lru_cache(maxsize=8)
def _cliente_cacheado(url: str, clave: str, schema: str) -> Client:
    """El cliente real, construido UNA VEZ por (url, clave, schema).

    Antes se llamaba a `create_client` en cada consulta del RAG: 108ms de
    media medidos el 2026-09-22, en el camino critico de un turno. No es solo
    el objeto -- adentro hay un `httpx.Client` con su pool, y rehacerlo tira
    la conexion TLS ya establecida, que es lo que de verdad se paga.

    La guarda de sqlite queda AFUERA de la cache a proposito: es una defensa
    (evita escribir en el Supabase real desde un test) y tiene que evaluarse
    en cada llamada, no una sola vez por proceso.

    Es seguro cachearlo aunque cada request corra en su propio event loop
    (`async_to_sync` en bot/whatsapp/webhooks.py crea uno nuevo cada vez):
    este cliente es SINCRONO, asi que no tiene nada atado a un loop. Un
    cliente async cacheado si rompe, y de la peor forma -- medido el
    2026-09-22: "Event loop is closed" de forma INTERMITENTE, 2 de 4 requests.

    maxsize=8 y no 1: el pipeline de scraping pide schemas distintos
    (`cliente="astara"`) en el mismo proceso, y con maxsize=1 cada alternancia
    reconstruiria el cliente -- justo lo que esto viene a evitar."""
    return create_client(url, clave).schema(schema)
