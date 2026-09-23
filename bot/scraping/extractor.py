import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_openrouter import ChatOpenRouter
from langfuse.langchain import CallbackHandler

from .normalizar import _normalizar_clave

logger = logging.getLogger(__name__)

CHUNK_CHARS = 12000
MAX_CHUNKS = 20

# Cuantos chunks se mandan al LLM EN PARALELO. Antes se mandaban uno por
# uno: con chunks de ~180s (visto en produccion, wsp_demo/renault.cl) y hasta
# MAX_CHUNKS=20, un scrape podia pasar 20+ minutos solo en esta etapa aunque
# el crawl ya hubiera terminado en segundos. Acotado (no "todos a la vez")
# para no gatillar rate-limiting de la API con corridas de muchos chunks.
LLM_CONCURRENCIA_MAXIMA = 5

LLM_TIMEOUT_SECONDS = 90
LLM_BACKOFF_SECONDS = [2, 4, 8]
LLM_MAX_RETRIES = len(LLM_BACKOFF_SECONDS)  # 4 intentos totales (1 inicial + 3 reintentos)


async def _get_llm():
    """Cliente LLM de extraccion, enrutado por OpenRouter.

    Migrado el 2026-09-02 desde la API directa de DeepSeek. Este era el ultimo
    componente fuera de OpenRouter (la migracion del 2026-08-19 lo dejo afuera
    a proposito) y su cuenta se quedo sin saldo: el primer indexado del RAG de
    Cavem murio con `402 Insufficient Balance` en las 5 bases de conocimiento.
    El modelo es el mismo, y ahora hay una sola API key y un solo saldo que
    vigilar para bot, media y scraping.

    La API key se comparte con el LLM conversacional y el de media, asi que el
    override es el mismo (`openrouter_api_key_override`); el modelo tiene su
    propio override, como el de media."""
    from bot.models import get_setting
    model = (await sync_to_async(get_setting)("openrouter_scraping_model_override", "")
             or settings.OPENROUTER_SCRAPING_MODEL)
    api_key = (await sync_to_async(get_setting)("openrouter_api_key_override", "")
               or settings.OPENROUTER_API_KEY)
    if not api_key:
        raise RuntimeError(
            "falta la API key de OpenRouter: setear OPENROUTER_API_KEY en .env.docker "
            "o el override en Configuración → LLM"
        )
    # max_retries=0 porque _ainvoke_with_retry mas abajo YA reintenta a nivel
    # de aplicacion (4 intentos totales, backoff explicito [2, 4, 8] con
    # logging propio) -- el default del SDK (max_retries=2) se apilaria
    # encima sin que nada lo frene, dando hasta 12 requests HTTP de 90s cada
    # uno por una sola llamada logica. Bug real detectado en produccion:
    # ScrapeRun 86 (2026-09-01) midio 455 requests a la API de las cuales 289
    # eran reintentos (~39%), todos exitosos una vez completados (no eran
    # 429/5xx del proveedor, morian antes de recibir respuesta y se
    # rehacian) -- el run tardo 183 minutos contra ~50 de corridas anteriores
    # comparables.
    # timeout en MILISEGUNDOS (ChatOpenRouter, no segundos como ChatOpenAI):
    # con timeout_ms=None la llamada queda sin limite en la capa HTTP, ver el
    # comentario largo en bot/flow/graph.py::_get_llm.
    #
    # reasoning desactivado: reestructurar contenido a JSON no es una tarea de
    # razonamiento, y el modelo razona a esfuerzo `high` si no se le dice lo
    # contrario (auditoria de latencia del 2026-08-24). En scraping eso se
    # multiplica por hasta MAX_CHUNKS=20 chunks por corrida.
    #
    # Mismos proveedores fijados que el bot (mismo modelo, mismo mecanismo): la
    # auditoria del 2026-09-02 mostro que el ruteo por defecto puede caer en un
    # proveedor de 14 tokens/s, y aca cada documento genera miles de tokens --
    # los 4 documentos del RAG de Cavem tardaron 55-82s cada uno. No es un turno
    # de conversacion, pero un reindexado de 100 paginas lo siente. Ver
    # settings.OPENROUTER_PROVIDER_ORDER.
    return ChatOpenRouter(
        model=model, api_key=api_key, timeout=LLM_TIMEOUT_SECONDS * 1000, max_retries=0,
        reasoning={"enabled": False},
        openrouter_provider={
            "order": settings.OPENROUTER_PROVIDER_ORDER, "allow_fallbacks": True,
        },
    )


async def _ainvoke_with_retry(llm, prompt: str, label: str = "scraping-extractor") -> str:
    last_exc = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            # Sin turno de WhatsApp activo (corre en background, ver
            # scraping/runner.py) -- cada llamada queda como su propio trace
            # en Langfuse, tageado "scraping" para distinguirlo de las
            # trazas conversacionales de bot/flow/graph.py. `run_name` es
            # estable ("extract-structured-data"); `label` puede traer un
            # indice de chunk dinamico (ver llamador mas abajo) y por eso
            # solo se usa en los logs y como metadata, nunca como nombre de
            # la GENERATION (ver el mismo criterio en bot/flow/graph.py).
            result = await llm.ainvoke(
                prompt,
                config={
                    "callbacks": [CallbackHandler()],
                    "run_name": "extract-structured-data",
                    "metadata": {"langfuse_tags": ["scraping"], "chunk_label": label},
                },
            )
            return result.content
        except Exception as exc:
            last_exc = exc
            logger.warning("[%s] intento %s fallo: %s", label, attempt, exc)
            if attempt < LLM_MAX_RETRIES:
                await asyncio.sleep(LLM_BACKOFF_SECONDS[attempt])
    raise last_exc

EXTRACTOR_PROMPT = """Recibes el contenido de una página del sitio de InTouch, una empresa que
provee soluciones de contactabilidad, experiencia de cliente, Contact Center,
automatización y agentes conversacionales con IA para otras empresas.

Extrae los hechos que sirvan para responderle a una empresa interesada, como
una lista de afirmaciones atómicas y autocontenidas. Cada afirmación tiene que
entenderse sola, sin el resto de la página: menciona explícitamente de qué
solución, canal o modelo de operación habla.

Reglas:
- No inventes nada. Si la página no lo dice, no lo escribas.
- No extraigas montos, tarifas, costos, plazos de implementación ni cifras de
  resultados, aunque aparezcan: no se le pueden afirmar a un contacto sin
  material comercial aprobado.
- No extraigas nombres de clientes ni casos de éxito.
- Omite la navegación, los formularios, los pies de página y el texto legal
  del sitio.
- Escribe en español correcto, con tildes.

Contenido de la página:
{contenido}
"""


def _dividir_en_chunks(paginas: list[dict]) -> list[str]:
    """Agrupa el texto de las paginas en bloques de hasta CHUNK_CHARS
    caracteres, sin cortar una pagina a la mitad salvo que una sola pagina
    ya exceda CHUNK_CHARS por si sola (en ese caso se corta esa pagina,
    igual que el comportamiento de truncado anterior).

    Cada pagina se marca con "[Página: <url>]" antes de su texto. Bug real
    detectado en produccion (wsp_demo, renault.cl): sin esta marca, cuando
    dos paginas caian en el mismo chunk sus textos quedaban pegados sin
    ningun separador, y el LLM no tenia forma de saber a que pagina (y por
    lo tanto a que modelo/version) correspondia cada precio -- las paginas
    tipo /cotizar/<modelo>/<version>/ ademas listan TODOS los modelos como
    opciones de un selector, asi que sin la URL como ancla el precio de una
    quedaba asociado al modelo equivocado o se perdia."""
    chunks: list[str] = []
    actual = ""
    for pagina in paginas:
        texto = f"[Página: {pagina['url']}]\n{pagina['texto']}"
        if len(texto) > CHUNK_CHARS:
            if actual:
                chunks.append(actual)
                actual = ""
            chunks.append(texto[:CHUNK_CHARS])
            continue
        candidato = f"{actual}\n\n{texto}" if actual else texto
        if len(candidato) > CHUNK_CHARS:
            chunks.append(actual)
            actual = texto
        else:
            actual = candidato
    if actual:
        chunks.append(actual)
    return chunks


def _parsear_respuesta(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[scraping-extractor] respuesta del LLM no es JSON valido: %s", raw)
        raise ValueError("el LLM no devolvio JSON valido") from exc
    return {
        "servicios": parsed.get("servicios") or [],
        "sucursales": parsed.get("sucursales") or [],
        "vehiculos": parsed.get("vehiculos") or [],
    }


def _combinar_una_lista(listas: list[list[dict]], clave_extra: str | None = None) -> list[dict]:
    """Combina items de todas las listas, deduplicando por nombre
    normalizado (ver _normalizar_clave). Si clave_extra se especifica (ej.
    "direccion") y dos items tienen el MISMO valor no vacio en ese campo
    (tambien normalizado), se tratan como el mismo item aunque el nombre
    difiera -- una direccion identica es una senal mas confiable de que es
    el mismo lugar que el nombre que el LLM le puso en cada chunk."""
    combinados: dict[str, dict] = {}
    por_clave_extra: dict[str, str] = {}
    for lista in listas:
        for item in lista:
            nombre = (item.get("nombre") or "").strip()
            if not nombre:
                continue
            valor_extra = _normalizar_clave(item.get(clave_extra) or "") if clave_extra else ""
            clave = por_clave_extra.get(valor_extra) if valor_extra else None
            if clave is None:
                clave = _normalizar_clave(nombre)
            existente = combinados.get(clave)
            if existente is None:
                combinados[clave] = dict(item)
            else:
                for campo, valor in item.items():
                    if valor is not None:
                        existente[campo] = valor
            if valor_extra:
                por_clave_extra[valor_extra] = clave
    return list(combinados.values())


def _combinar_vehiculos(listas: list[list[dict]]) -> list[dict]:
    """Combina vehiculos de todos los chunks, deduplicando por (modelo,
    version) normalizados -- mismo criterio de matching que usa
    runner.py::_upsert_catalogo para Servicio/Sucursal. El chunk que
    aparece despues pisa los campos no-nulos que aporte; "specs" hace
    merge superficial (no deep-merge) en vez de reemplazo completo, para
    no perder specs ya encontrados en un chunk anterior."""
    combinados: dict[str, dict] = {}
    for lista in listas:
        for item in lista:
            modelo = (item.get("modelo") or "").strip()
            if not modelo:
                continue
            version = (item.get("version") or "").strip()
            clave = f"{_normalizar_clave(modelo)}|{_normalizar_clave(version)}"
            existente = combinados.get(clave)
            if existente is None:
                nuevo = dict(item)
                nuevo["specs"] = dict(item.get("specs") or {})
                combinados[clave] = nuevo
                continue
            for campo, valor in item.items():
                if campo == "specs":
                    existente["specs"].update({k: v for k, v in (valor or {}).items() if v is not None})
                elif valor is not None:
                    existente[campo] = valor
    return list(combinados.values())


def _combinar_catalogos(catalogos: list[dict]) -> dict:
    """Junta servicios/sucursales/vehiculos de todos los catalogos, deduplicando
    por nombre normalizado -- mismo criterio que usa runner.py::_upsert_catalogo
    (comparten _normalizar_clave). Las sucursales ademas deduplican por direccion
    normalizada; los vehiculos por (modelo, version). Si dos catalogos mencionan
    el mismo item, el que aparece despues pisa los campos no-nulos que aporte."""
    return {
        "servicios": _combinar_una_lista([c.get("servicios", []) for c in catalogos]),
        "sucursales": _combinar_una_lista(
            [c.get("sucursales", []) for c in catalogos], clave_extra="direccion"
        ),
        "vehiculos": _combinar_vehiculos([c.get("vehiculos", []) for c in catalogos]),
    }


async def _extraer_un_chunk(llm, semaforo: asyncio.Semaphore, indice: int, chunk: str) -> dict:
    async with semaforo:
        prompt = EXTRACTOR_PROMPT.format(contenido=chunk)
        raw = await _ainvoke_with_retry(llm, prompt, label=f"scraping-extractor[{indice}]")
        return _parsear_respuesta(raw)


async def _extract_catalog_async(paginas: list[dict]) -> dict:
    chunks = _dividir_en_chunks(paginas)
    if len(chunks) > MAX_CHUNKS:
        logger.warning(
            "[scraping-extractor] %s chunks generados, se procesan solo los primeros %s",
            len(chunks), MAX_CHUNKS,
        )
        chunks = chunks[:MAX_CHUNKS]
    llm = await _get_llm()
    semaforo = asyncio.Semaphore(LLM_CONCURRENCIA_MAXIMA)
    # gather (no return_exceptions) preserva el comportamiento de antes: si UN
    # chunk agota sus reintentos y levanta, se propaga y aborta el resto -- no
    # se guarda un catalogo parcial. El orden de "catalogos" queda igual al
    # orden de "chunks" (gather lo garantiza), asi que _combinar_catalogos
    # sigue resolviendo conflictos entre chunks de la misma forma que antes
    # (el chunk que aparece despues en el texto original pisa al anterior).
    catalogos = await asyncio.gather(
        *(_extraer_un_chunk(llm, semaforo, i, chunk) for i, chunk in enumerate(chunks))
    )
    return _combinar_catalogos(list(catalogos))


def catalogo_estructurado_disponible() -> bool:
    """¿El vertical de este bot tiene un catálogo estructurado que sacar del
    sitio? Las verticales automotrices heredadas (renault/astara/cavem, con las
    que corre la suite) sí: servicios, sucursales y vehículos. InTouch no:
    `_parsear_respuesta`/`_combinar_catalogos` siguen esperando la forma
    automotriz y adaptarlas a B2B es trabajo de diseño propio (spec
    2026-09-09-bot-intouch-comercial-design.md §12.6).

    Es la ÚNICA fuente de esa decisión: runner.py la consulta para saltarse el
    paso del catálogo, y extract_catalog la usa como defensa. Cuando exista el
    paquete `verticals/` de la biblia §VI.5, esto pasa a ser un atributo del
    vertical y no una comparación con CLIENTE_ACTIVO."""
    return settings.CLIENTE_ACTIVO != "intouch"


def extract_catalog(paginas: list[dict]) -> dict:
    """Entry point sincronico -- runner.py queda 100% sincronico de punta
    a punta, corra desde un thread (vista del panel) o desde el hilo
    principal (management command).

    Sin catálogo estructurado para el vertical (ver
    catalogo_estructurado_disponible) levanta NotImplementedError ANTES de
    invocar al LLM. runner.py ya no llega acá en ese caso -- se salta el
    paso --, así que esto es la defensa para cualquier otro llamador
    (scrape_probe, un test): sin ella se toparía con ValueError("el LLM no
    devolvio JSON valido"), que suena a falla del proveedor cuando en realidad
    es "esto no esta implementado para este vertical"."""
    if not catalogo_estructurado_disponible():
        raise NotImplementedError(
            "el scraping estructurado (extract_catalog) no esta adaptado al "
            "vertical de InTouch -- sigue devolviendo servicios/sucursales/"
            "vehiculos, la forma heredada del negocio automotriz. Las paginas "
            "scrapeadas si se indexan en el RAG; lo que no existe es el "
            "catalogo estructurado. Los .md de bot/fixtures/rag/ entran via "
            "'manage.py cargar_conocimiento_rag'. Deuda anotada en "
            "docs/superpowers/specs/2026-09-09-bot-intouch-comercial-design.md §12.6."
        )
    return asyncio.run(_extract_catalog_async(paginas))
