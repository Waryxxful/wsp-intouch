import asyncio
import logging

import httpx
from functools import lru_cache
from django.conf import settings
from langchain.tools import ToolRuntime, tool
from langchain.messages import ToolMessage
from langfuse import get_client

logger = logging.getLogger(__name__)

_MAX_INTENTOS_POR_TURNO = 3

_RERANK_MODEL = "cohere/rerank-v3.5"
_RERANK_TOP_N = 5
# Sin calibrar contra datos reales todavia -- primer valor de partida, a
# ajustar con bot/rag_eval (Task 8) una vez que haya resultados. A diferencia
# del _SIMILARITY_MINIMA que reemplaza (coseno crudo), este es un score
# calibrado (0-1, probabilidad de relevancia) de un cross-encoder real.
_RELEVANCIA_MINIMA = 0.3

# El rerank es una llamada de red y hasta el 2026-09-07 era la unica del turno
# SIN reintento. Las del LLM ya lo tenian (`_ainvoke_with_retry` en
# bot/flow/graph.py, mismo backoff de 1,5s y mismo criterio de "permanente").
#
# Hallazgo que lo motivo: midiendo latencia, TRES consultas de RAG en un minuto
# agotaron el limite de peticiones de Cohere y OpenRouter devolvio
# `HTTP 429: You are past the per minute request limit`. Con varias
# conversaciones en paralelo eso se toca de forma rutinaria.
_RERANK_MAX_INTENTOS = 3
_RERANK_BACKOFF_SEGUNDOS = 1.5


def _rerank_es_permanente(exc: Exception) -> bool:
    """Que NO se arregla reintentando, y por lo tanto no merece gastar backoff.

    Dos familias, por razones distintas:

    1. Un 4xx que no sea 429 -- key invalida, payload mal armado. Mismo
       criterio que bot/flow/graph.py::_es_error_permanente.
    2. Un error de PARSEO (KeyError/IndexError/TypeError/ValueError): la
       respuesta llego pero no tiene la forma que esperamos. Si OpenRouter
       cambia el contrato, reintentar tres veces solo le agrega 3s de backoff
       a cada consulta y termina en el mismo lugar. Se cae al fallback de una.

    Transitorios, o sea que SI se reintentan: 429 (el caso que motivo todo
    esto), 5xx, y cualquier error de red sin status (timeouts, cortes)."""
    if isinstance(exc, (KeyError, IndexError, TypeError, ValueError)) and not isinstance(exc, httpx.HTTPError):
        return True
    respuesta = getattr(exc, "response", None)
    status = getattr(respuesta, "status_code", None)
    return status is not None and 400 <= status < 500 and status != 429


def _contar_intentos_previos(tool_messages: list) -> int:
    return sum(
        1 for m in tool_messages
        if isinstance(m, ToolMessage) and m.name == "consultar_base_conocimiento"
    )


@lru_cache(maxsize=1)
def _cliente_embeddings():
    """El cliente de embeddings, construido UNA VEZ por proceso.

    output_dimensionality=1536: debe coincidir con vector(1536) del schema
    (bot/rag/schema.sql) y con bot/rag/indexador.py::_embeddings_client --
    embeddings de distinta dimension no son comparables por coseno.

    POR QUE ES SINCRONO Y CACHEADO (medido el 2026-09-22 contra el servicio
    real, no estimado):

    - Construirlo en cada consulta costaba **1336ms de media** por embedding.
      Reusandolo baja a **737ms**: 599ms de los que casi todo es rehacer la
      conexion TLS, no calcular el vector.
    - **El cliente ASYNC no se puede cachear**, y falla de la peor manera.
      `async_to_sync` (bot/whatsapp/webhooks.py) crea un event loop NUEVO en
      cada request, asi que un cliente async guardado a nivel proceso queda
      atado a un loop ya cerrado: medido, "Event loop is closed" en 2 de 4
      requests -- INTERMITENTE, que es indistinguible de una caida del
      proveedor. Un cliente sincrono no tiene nada atado a un loop.
    - Por eso el llamador lo corre con `asyncio.to_thread`: sincrono, pero
      fuera del event loop del turno, que ademas es lo que hay que hacer para
      no bloquear a los otros contactos.
    """
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-2", task_type="RETRIEVAL_QUERY", output_dimensionality=1536,
    )


@lru_cache(maxsize=1)
def _cliente_http() -> httpx.Client:
    """El cliente HTTP del rerank, construido UNA VEZ por proceso.

    Mismo motivo que `_cliente_embeddings`: sincrono para sobrevivir a los
    loops de `async_to_sync`, y cacheado para conservar la conexion. Medido el
    2026-09-22: el rerank baja de 484ms a 371ms.

    `httpx.AsyncClient` por consulta era ademas la razon por la que el timeout
    de 10s no acotaba nada util -- ver el comentario de _RERANK_MAX_INTENTOS.
    """
    return httpx.Client(timeout=10.0)


def _rpc_busqueda_hibrida(query: str, vector: list, k: int) -> list[dict]:
    """La llamada bloqueante a Supabase, aislada para correrla en un thread.

    Estaba tal cual dentro de una funcion `async`: el cliente de Supabase es
    sincrono, asi que `.execute()` bloqueaba el event loop del turno ~459ms de
    media. Con un solo contacto no se nota; con varios en paralelo, cada RPC
    congela a todos los demas.
    """
    from bot.rag.cliente import get_supabase_client

    return get_supabase_client().rpc(
        "match_documentos",
        {"query_embedding": vector, "query_texto": query, "match_count": k},
    ).execute().data


async def _buscar_en_supabase(query: str, k: int = 20) -> list[dict]:
    lf = get_client()
    with lf.start_as_current_observation(name="rag-embedding", as_type="span"):
        vector = await asyncio.to_thread(_cliente_embeddings().embed_query, query)
    with lf.start_as_current_observation(name="rag-busqueda-hibrida", as_type="span"):
        return await asyncio.to_thread(_rpc_busqueda_hibrida, query, vector, k)


async def _rerankear(query: str, chunks: list[dict]) -> list[dict]:
    """Reemplaza el grading por LLM generativo: usa el endpoint de rerank de
    OpenRouter (cross-encoder proposito-especifico, no un chat model pensando
    en voz alta) sobre los candidatos del retrieval hibrido. Confirmado
    contra la documentacion real de OpenRouter (cookbook "Evaluate and
    Optimize RAG", 2026-08-25): POST /api/v1/rerank, body {model, query,
    documents, top_n}, respuesta {"results": [{"index", "relevance_score", ...}]}."""
    if not chunks:
        return []

    def _pedir():
        """El POST bloqueante, para correrlo fuera del event loop."""
        resp = _cliente_http().post(
            "https://openrouter.ai/api/v1/rerank",
            headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
            json={
                "model": _RERANK_MODEL,
                "query": query,
                "documents": [c["contenido"] for c in chunks],
                "top_n": min(_RERANK_TOP_N, len(chunks)),
            },
        )
        resp.raise_for_status()
        return resp.json()["results"]

    with get_client().start_as_current_observation(name="rag-rerank", as_type="span"):
        for intento in range(_RERANK_MAX_INTENTOS):
            try:
                resultados = await asyncio.to_thread(_pedir)
                # Una lista vacia ACA es legitima: el rerank corrio y dijo que
                # nada supera el umbral. Eso NO se puede confundir con el
                # fallback de abajo, o volvemos al bug que este bloque arregla.
                return [
                    chunks[r["index"]] for r in resultados
                    if r["relevance_score"] >= _RELEVANCIA_MINIMA
                ]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                ultimo = intento == _RERANK_MAX_INTENTOS - 1
                if _rerank_es_permanente(exc) or ultimo:
                    # `exc_info` no es cosmetico: antes esto era un warning mudo
                    # que atrapaba cinco tipos de excepcion sin decir cual, asi
                    # que un 429 del proveedor se veia en los logs igual que un
                    # cambio de formato en la respuesta. Estuvo pasando en
                    # produccion sin que nadie pudiera diagnosticarlo.
                    logger.error(
                        "[rag] rerank fallido para %r tras %s intento(s); se devuelven "
                        "los %s candidatos del retrieval hibrido SIN reordenar",
                        query, intento + 1, min(_RERANK_TOP_N, len(chunks)),
                        exc_info=exc,
                    )
                    # Devolver [] aca seria mentirle al cliente: aguas arriba se
                    # convierte en ok:false -> "no tengo ese dato", teniendo los
                    # chunks recuperados en la mano. Un fallo de infraestructura
                    # quedaba indistinguible de una base de conocimiento vacia,
                    # que es la misma familia que el `except Exception: pass` de
                    # docs/PENDIENTES.md #27.
                    #
                    # Los candidatos del hibrido ya vienen ordenados por RRF
                    # (bot/rag/schema.sql::match_documentos), asi que esto es
                    # una degradacion de PRECISION, no de veracidad: el modelo
                    # lee los chunks y decide si le sirven.
                    return chunks[:_RERANK_TOP_N]
                logger.warning(
                    "[rag] rerank fallo para %r (intento %s de %s), reintento: %r",
                    query, intento + 1, _RERANK_MAX_INTENTOS, exc,
                )
                await asyncio.sleep(_RERANK_BACKOFF_SEGUNDOS)


async def _consultar_base_conocimiento_impl(query: str, tool_messages: list) -> dict:
    intentos_previos = _contar_intentos_previos(tool_messages)
    if intentos_previos >= _MAX_INTENTOS_POR_TURNO:
        return {
            "ok": False,
            "motivo": "Ya se intento buscar varias veces sin exito. No sigas buscando: "
                      "dile al cliente honestamente que no tienes ese dato y ofrece derivar.",
        }

    chunks_crudos = await _buscar_en_supabase(query)
    logger.info("[rag] query=%r candidatos_hibridos=%d", query, len(chunks_crudos))
    chunks_relevantes = await _rerankear(query, chunks_crudos) if chunks_crudos else []
    if not chunks_relevantes:
        return {
            "ok": False,
            "motivo": "No encontre informacion relevante para esa busqueda. "
                      "Intenta con otros terminos antes de responder, o si ya lo intentaste, "
                      "dile al cliente honestamente que no tienes ese dato.",
        }
    return {
        "ok": True,
        "resultados": [
            {"texto": c["contenido"], "fuente": c["fuente_url"], "categoria": c.get("categoria")}
            for c in chunks_relevantes
        ],
    }


@tool(parse_docstring=True)
async def consultar_base_conocimiento(query: str, runtime: ToolRuntime) -> dict:
    """Busca información de fondo sobre InTouch en la base de conocimiento
    vectorial: cómo funciona una solución, los modelos de operación, los
    canales de atención, analítica y calidad, integraciones con CRM o ERP,
    tratamiento de datos y seguridad, y quién es InTouch. Formula la query con
    tus propios términos, no necesariamente los mismos que usó el contacto --
    puedes reformular si un intento anterior no trajo nada útil.

    No busques precios, tarifas, plazos ni casos de éxito: eso no está en la
    base de conocimiento y no existe una respuesta que puedas dar por buena.

    Args:
        query: los términos de búsqueda (puedes reformularlos, no tienen que ser literales del mensaje del contacto)
    """
    return await _consultar_base_conocimiento_impl(query, runtime.state.get("tool_messages") or [])
