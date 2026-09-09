import asyncio
import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings

logger = logging.getLogger(__name__)

_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)

# Las categorias con que se clasifica cada chunk. La lista es TODO lo que tiene
# el clasificador, asi que una taxonomia de otro rubro le hace poner una
# etiqueta que el filtro por categoria no conoce, y el chunk queda inalcanzable.
#
# "otro" existe para que el clasificador tenga donde poner lo que no calza, en
# vez de forzar una etiqueta equivocada.
CATEGORIAS_RAG = [
    "soluciones",          # que hace cada solucion y para que sirve
    "modelos_operacion",   # humano, hibrido, automatizado
    "canales",             # WhatsApp, voz, chat, correo
    "analitica",           # dashboards, Power BI, control de calidad
    "integraciones",       # CRM, ERP, y que implica la evaluacion tecnica
    "datos_y_seguridad",   # tratamiento de datos, ley 21.719, confidencialidad
    "empresa",             # quienes son, como trabajan, cobertura
    "otro",
]

# Mismo criterio que extractor.LLM_CONCURRENCIA_MAXIMA (bot/scraping/extractor.py) --
# acota cuantas clasificaciones corren en paralelo por pagina, para no serializar
# chunk por chunk ni saturar la API con todos a la vez.
LLM_CONCURRENCIA_MAXIMA_CLASIFICACION = 5

PROMPT_CLASIFICACION = """Eres un clasificador de documentación de InTouch, una empresa que integra IA,
personas, datos, automatización, operación de Contact Center y analítica de
gestión para otras empresas.

Clasifica el fragmento en UNA de estas categorías:

- soluciones: qué hace una solución de InTouch, para qué sirve, qué problema resuelve.
- modelos_operacion: los modelos humano, híbrido o automatizado, y cuándo aplica cada uno.
- canales: WhatsApp, voz, chat o correo electrónico.
- analitica: dashboards, paneles, Power BI, analítica conversacional, control de calidad.
- integraciones: integración con CRM, ERP u otros sistemas, y qué implica la evaluación técnica.
- datos_y_seguridad: tratamiento de datos personales, confidencialidad, ley 21.719.
- empresa: quién es InTouch, cómo trabaja, su cobertura y su forma de operar.
- otro: cualquier cosa que no calce en las anteriores.

Responde solo con el nombre de la categoría, sin explicar.

Fragmento:
{fragmento}
"""


def _embeddings_client() -> GoogleGenerativeAIEmbeddings:
    # output_dimensionality=1536: gemini-embedding-2 devuelve 3072 dims por
    # defecto, pero pgvector no permite indexar (hnsw/ivfflat) columnas de
    # mas de 2000 dimensiones -- debe coincidir con vector(1536) del schema
    # (bot/rag/schema.sql) y con el mismo parametro en bot/rag/tool.py.
    return GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-2", task_type="RETRIEVAL_DOCUMENT", output_dimensionality=1536,
    )


async def _clasificar_chunk(llm, semaforo: asyncio.Semaphore, texto: str) -> str:
    from bot.flow.graph import _texto_de_respuesta  # mismo parser que tool.py --
    # tolera el mismo caso real de Gemini/DeepSeek devolviendo .content como
    # lista de content blocks en vez de string plano.
    from bot.scraping.extractor import _ainvoke_with_retry
    async with semaforo:
        prompt = PROMPT_CLASIFICACION.format(fragmento=texto[:2000])
        respuesta = await _ainvoke_with_retry(llm, prompt, label="rag-clasificador")
        raw = _texto_de_respuesta(respuesta).strip().lower()
        return raw if raw in CATEGORIAS_RAG else "otro"


_PROMPT_HECHOS_DOCUMENTO = """Este es el texto extraído de un documento (ficha técnica u otro archivo) de InTouch, una empresa que integra IA, personas, datos, automatización, operación de Contact Center y analítica de gestión para otras empresas. Puede tener columnas o tablas desordenadas por la extracción automática.

Tu tarea: reescribir el contenido como una lista de HECHOS independientes, cada uno autocontenido (se entiende sin leer los demás). Reglas:

- Cada hecho debe mencionar explícitamente la solución, canal, modelo de operación o tema al que se refiere (ej. "Analítica de InTouch: dashboards en Power BI con control de calidad..."), aunque el texto original no lo repita en cada línea -- infiérelo del título/contexto del documento.
- No inventes ni completes datos que no estén en el texto. Si un dato está en el original, se preserva literal.
- Agrupa detalles muy relacionados en un mismo hecho (ej. los canales que cubre una misma solución van juntos), pero separa temas distintos (soluciones, modelos de operación, canales, analítica, integraciones, datos y seguridad) en hechos distintos.
- A cada hecho asígnale UNA categoría de esta lista exacta: {categorias}

Texto:
{texto}

Responde SOLO con JSON: {{"hechos": [{{"texto": "...", "categoria": "..."}}]}}"""


async def _hechos_de_documento(texto: str) -> list[dict]:
    """Reescribe el texto crudo de un documento (PDF/Word/Excel, tipicamente
    desordenado por la extraccion automatica) como una lista de hechos
    autocontenidos, cada uno con su categoria ya asignada. Contrato: NUNCA
    devuelve [] silenciosamente en caso de fallo -- siempre levanta, para
    que el caller (_indexar_pagina_en_supabase_async) aborte el reindexado
    de ESTE documento sin borrar las filas viejas que ya tenia en Supabase.
    Distinto a proposito de _calificar_relevancia (que si traga a [] en
    fallo): ese es tiempo de conversacion real con un cliente esperando,
    este es indexado -- silenciar aca borraria conocimiento bueno."""
    from bot.flow.graph import _parse_json_response
    from bot.scraping.extractor import _get_llm, _ainvoke_with_retry

    llm = await _get_llm()
    # _get_llm() no fija max_tokens (el default de DeepSeek es ~4K), pero la salida JSON
    # restructurada aca es del mismo orden de magnitud que el texto de entrada -- un documento
    # grande (ficha tecnica extensa) puede truncar el JSON a mitad de un objeto y hacer que
    # _parse_json_response/json.loads reviente. Se acota solo esta llamada, no _get_llm().
    llm = llm.bind(max_tokens=16000)
    prompt = _PROMPT_HECHOS_DOCUMENTO.format(categorias=", ".join(CATEGORIAS_RAG), texto=texto)
    raw = await _ainvoke_with_retry(llm, prompt, label="rag-hechos-documento")
    hechos_crudos = _parse_json_response(raw).get("hechos")
    if not hechos_crudos:
        raise ValueError("la restructuracion LLM no devolvio hechos (JSON invalido o lista vacia)")

    hechos = [
        {"texto": h["texto"], "categoria": h["categoria"] if h.get("categoria") in CATEGORIAS_RAG else "otro"}
        for h in hechos_crudos if h.get("texto")
    ]
    if not hechos:
        raise ValueError("la restructuracion LLM devolvio hechos pero todos sin texto valido (JSON invalido)")

    # Visibilidad solamente: un hecho > ~4000 chars no se trunca ni se rechaza aca --
    # gemini-embedding-2 tiene su propio limite de tokens de entrada y podria truncarlo o
    # rechazarlo en silencio; este warning es lo que deja rastro en los logs si eso pasa.
    for h in hechos:
        if len(h["texto"]) > 4000:
            logger.warning(
                "[rag] hecho de %d chars (> 4000) en la restructuracion LLM, "
                "riesgo de truncado/rechazo por gemini-embedding-2: %r...",
                len(h["texto"]), h["texto"][:200],
            )

    logger.info(
        "[rag] restructuracion LLM: %d hechos, %d chars de salida (entrada: %d chars)",
        len(hechos), sum(len(h["texto"]) for h in hechos), len(texto),
    )
    return hechos


def _chunks_de_pagina(pagina) -> list[dict]:
    """Devuelve [{"titulo": str|None, "texto": str}] -- un chunk por cada
    trozo de una seccion real (o de todo el texto plano si la pagina no
    tiene `secciones`, fallback para paginas scrapeadas antes de este
    cambio o para PDF/Word/Excel, que no tienen estructura HTML)."""
    if not pagina.secciones:
        return [{"titulo": None, "texto": t} for t in _SPLITTER.split_text(pagina.texto)]
    resultado = []
    for seccion in pagina.secciones:
        for trozo in _SPLITTER.split_text(seccion["texto"]):
            resultado.append({"titulo": seccion.get("titulo"), "texto": trozo})
    return resultado


def _texto_con_contexto(chunk: dict, categoria: str | None) -> str:
    """Antepone la categoria (si ya se conoce) y el titulo de seccion al
    texto del chunk antes de embeberlo -- "contextual retrieval": ayuda al
    embedding a capturar de que trata el chunk sin depender de que quede
    claro solo por el texto aislado. No agrega ninguna llamada LLM nueva --
    la categoria ya se clasifica en este mismo indexado, solo cambia el
    orden en que se usa (ver Step 3 de abajo)."""
    partes = []
    if categoria:
        partes.append(f"[{categoria}]")
    if chunk.get("titulo"):
        partes.append(chunk["titulo"])
    partes.append(chunk["texto"])
    return "\n\n".join(partes)


async def _indexar_pagina_en_supabase_async(pagina, cliente: str) -> None:
    from bot.rag.cliente import get_supabase_client
    from bot.scraping.extractor import _get_llm

    if pagina.es_documento and not pagina.secciones:
        hechos = await _hechos_de_documento(pagina.texto)  # levanta si falla -- no seguir
        textos_contenido = [h["texto"] for h in hechos]
        textos_embedding = textos_contenido
        categorias = [h["categoria"] for h in hechos]
        fallos_clasificacion = [False] * len(hechos)
    else:
        chunks = _chunks_de_pagina(pagina)
        if not chunks:
            if pagina.texto:
                logger.warning(
                    "[rag] %s tiene texto (%d chars) pero la extraccion de chunks dio vacio -- "
                    "se mantienen las filas viejas de Supabase para esta url, si las hay",
                    pagina.url, len(pagina.texto),
                )
            return

        # Clasificar ANTES de armar textos_embedding: _texto_con_contexto
        # necesita la categoria ya resuelta para poder antepornerla.
        #
        # Solo se clasifican los chunks que vienen de `secciones` -- los del
        # fallback plano (pagina.secciones vacio, paginas no re-scrapeadas
        # todavia) quedan con categoria=None sin gastar una llamada LLM: son
        # contenido transitorio que este mismo indexado va a reemplazar en
        # cuanto el sitio se re-scrapee.
        if pagina.secciones:
            llm_clasificador = await _get_llm()
            semaforo = asyncio.Semaphore(LLM_CONCURRENCIA_MAXIMA_CLASIFICACION)
            # return_exceptions=True: si UN chunk agota sus reintentos de clasificacion
            # (ej. DeepSeek), el resto de la pagina no debe perder su reindexado entero --
            # a ~114 paginas x varios chunks cada una, un solo fallo de clasificacion es
            # un caso esperado, no una cola rara. El chunk que fallo cae a "otro" Y se
            # marca clasificacion_fallo=True, para distinguirlo en Supabase de un "otro"
            # genuino (el LLM respondio algo fuera de la taxonomia, sin haber fallado).
            categorias_o_excepciones = await asyncio.gather(
                *(_clasificar_chunk(llm_clasificador, semaforo, c["texto"]) for c in chunks),
                return_exceptions=True,
            )
            categorias = []
            fallos_clasificacion = []
            for categoria in categorias_o_excepciones:
                if isinstance(categoria, str):
                    categorias.append(categoria)
                    fallos_clasificacion.append(False)
                else:
                    logger.warning(
                        "[rag] fallo al clasificar un chunk de %s, cae a 'otro': %s", pagina.url, categoria,
                    )
                    categorias.append("otro")
                    fallos_clasificacion.append(True)
        else:
            categorias = [None] * len(chunks)
            fallos_clasificacion = [False] * len(chunks)

        textos_embedding = [
            _texto_con_contexto(c, categoria) for c, categoria in zip(chunks, categorias)
        ]
        # textos_contenido: lo que se guarda (contenido/FTS/lo que ve el LLM y
        # potencialmente el cliente por WhatsApp) -- SIN el tag [categoria], que
        # solo debe llegar al modelo de embeddings, nunca a lo persistido.
        textos_contenido = [
            f"{c['titulo']}\n\n{c['texto']}" if c.get("titulo") else c["texto"] for c in chunks
        ]

    supabase = get_supabase_client(cliente=cliente)
    vectores = _embeddings_client().embed_documents(textos_embedding)

    supabase.table("documentos_conocimiento").delete().eq("fuente_url", pagina.url).execute()
    filas = [
        {
            "fuente_url": pagina.url, "scraped_page_id": pagina.id,
            "contenido": texto_contenido, "embedding": vector, "categoria": categoria,
            "clasificacion_fallo": fallo,
        }
        for texto_contenido, vector, categoria, fallo in zip(
            textos_contenido, vectores, categorias, fallos_clasificacion,
        )
    ]
    supabase.table("documentos_conocimiento").insert(filas).execute()


def indexar_pagina_en_supabase(pagina, cliente: str) -> bool:
    """Indexa (re-indexa) una pagina scrapeada en Supabase. Contrato:
    best-effort, NUNCA levanta -- cualquier fallo (env vars de Supabase
    ausentes, cuota de Gemini/DeepSeek, red) se loguea y se traga, porque
    los dos call sites tienen trabajo propio que no depende del RAG.
    Devuelve True si no hubo excepcion (incluye el caso "sin chunks, nada
    que indexar"), False si se atrapo una excepcion real -- lo usa
    reindexar_conocimiento_rag para reportar cuantas paginas fallaron."""
    try:
        asyncio.run(_indexar_pagina_en_supabase_async(pagina, cliente))
        return True
    except Exception:
        logger.exception("[rag] fallo al indexar %s en Supabase -- se continua sin indexar esta pagina", pagina.url)
        return False


async def _borrar_chunks_de_paginas_purgadas_async(scraped_page_ids: list, cliente: str) -> None:
    from bot.rag.cliente import get_supabase_client

    supabase = get_supabase_client(cliente=cliente)
    supabase.table("documentos_conocimiento").delete().in_("scraped_page_id", scraped_page_ids).execute()


def borrar_chunks_de_paginas_purgadas(scraped_page_ids: list, cliente: str) -> bool:
    """Borra en Supabase los chunks de las ScrapedPage que bot/scraping/runner.py
    ya purgo de Django (runs anteriores del mismo source, o un run que quedo
    colgado en estado="corriendo" mas alla de lo razonable) -- sin esto, esas
    filas quedaban huerfanas en documentos_conocimiento hasta que alguien
    corria reindexar_conocimiento_rag a mano (ver docs/PENDIENTES.md). Mismo
    contrato best-effort que el resto del indexador: NUNCA levanta.

    `cliente` es obligatorio (sin default), mismo patron que _upsert_catalogo --
    bug real 2026-08-27/28 (docs/PENDIENTES.md): sin esto, un scrape de
    Astara podia borrar/reindexar contra el schema de Supabase equivocado
    con solo tener RAG_SCHEMA mal seteado en el proceso."""
    try:
        asyncio.run(_borrar_chunks_de_paginas_purgadas_async(scraped_page_ids, cliente))
        return True
    except Exception:
        logger.exception(
            "[rag] fallo al borrar los chunks de %d paginas purgadas en Supabase", len(scraped_page_ids),
        )
        return False
