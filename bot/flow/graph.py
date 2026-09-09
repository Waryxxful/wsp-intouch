import asyncio
import contextvars
import json
import logging
import re
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openrouter import ChatOpenRouter
from langfuse import get_client
from langfuse.langchain import CallbackHandler
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from .agents import build_agent_registry
from .campaign_rules import resolve_agent_for_campaign
from .flow_data import fusionar_flow_data
from .global_prompt import get_effective_global_prompt
from .prosa import prosa_utilizable
from .respuesta import (
    RECORDATORIO_RESPUESTA, respuesta_de_tool_calls, responder, tool_calls_de_negocio,
)
from .state import BotState

logger = logging.getLogger(__name__)

# Bug real detectado en revision manual de conversacion: el LLM ponia
# modelo_imagen con solo una mencion de pasada del modelo (ej. negociando
# parte de pago o financiamiento), aunque el turno no fuera realmente sobre
# mostrar el auto -- el unico guardrail existente (_imagen_enviada_recientemente
# en handlers.py) es anti-duplicado por ventana de mensajes, no anti-irrelevancia.
# Se gatea aca contra el campo "intent" que el contrato de "ventas" ya declara
# (CONTRATO DE SALIDA, prompt en BD) pero que antes de este fix nadie leia.
INTENTS_CON_IMAGEN = {"explorar", "cotizar", "test_drive"}


async def _get_llm(reasoning: dict | None = None):
    from bot.models import get_setting
    # Migrado a OpenRouter el 2026-08-19 (incidente real: Gemini directo
    # devolviendo 503/504 "high demand", ver el comentario de
    # _MEDIA_LLM_DEADLINE_SEGUNDOS en bot/flow/media_processing.py).
    # openrouter_api_key_override: mismo override que usa el LLM de media
    # (bot/flow/media_processing.py::_get_media_llm) -- comparten una sola
    # API key de OpenRouter, solo el modelo difiere.
    model = await sync_to_async(get_setting)("openrouter_model_override", "") or settings.OPENROUTER_MODEL
    api_key = await sync_to_async(get_setting)("openrouter_api_key_override", "") or settings.OPENROUTER_API_KEY
    # timeout es en MILISEGUNDOS para ChatOpenRouter (no segundos, a diferencia
    # de ChatGoogleGenerativeAI) -- bug real encontrado al verificar esta
    # migracion: sin esto, timeout_ms=None deja la llamada SIN ningun limite en
    # la capa HTTP (ver openrouter.basesdk: "timeout = timeout_ms / 1000 if
    # timeout_ms is not None else None"), reproduciendo exactamente el
    # incidente de Gemini colgado que motivo esta migracion, solo que ahora via
    # OpenRouter. max_retries=0 porque _ainvoke_with_retry/
    # _ainvoke_messages_with_retry/_ainvoke_tools_json_with_retry YA reintentan
    # a nivel de aplicacion (hasta 3 intentos) -- el default de ChatOpenRouter
    # (max_retries=2) agrega SU PROPIO backoff interno de hasta ~300s por
    # llamada (ver su docstring), que se apilaria encima sin que nada lo frene
    # antes del --timeout 120 de gunicorn.
    #
    # CUANTO puede durar el turno NO sale de esta linea. Hasta el 2026-09-07
    # este comentario decia "30s x 3 intentos de app = 90s, con margen bajo
    # esos 120s", y era falso: contaba solo el bucle interno
    # (_ainvoke_messages_with_retry) e ignoraba que
    # _ainvoke_tools_json_with_retry lo llama hasta 3 veces ENCIMA -- peor caso
    # real 279s, mas el ruteo, o sea mas del doble del timeout de gunicorn que
    # decia respetar. El presupuesto de verdad, y la unica cuenta que vale, es
    # _PRESUPUESTO_LLM_TURNO_SEGUNDOS mas abajo: 70s para TODAS las llamadas al
    # LLM del turno juntas. Este `timeout=30_000` es solo el tope nominal por
    # llamada -- y ni siquiera lo aplica la libreria, ver _TIMEOUT_LLM_SEGUNDOS.
    # `reasoning` es override-able por llamador: el default {"effort": "medium"}
    # no es realmente soportado por OPENROUTER_MODEL actual
    # (~deepseek/deepseek-v4-flash-latest solo acepta high/xhigh) -- OpenRouter
    # lo remapea en silencio al nivel soportado mas cercano (high), asi que en
    # la practica CADA llamada razona a full esfuerzo. Auditoria de latencia
    # 2026-08-24 (Langfuse, turnos reales): eso explica turnos de 20-105s, con
    # classify-intent (bot.flow.graph.supervisor_node, una eleccion trivial
    # entre ~5 agentes) solo por si tomando 45-90s en varios casos. Se
    # desactiva ahi via este parametro; generate-response se deja como esta
    # a pedido del usuario (no tocar la calidad de la respuesta final todavia).
    # Proveedores fijados por orden en vez de `sort: latency`. Ese sort ordena
    # por TIME-TO-FIRST-TOKEN y elegia OpenInference: 662ms de TTFT (el mejor
    # del catalogo) con 14 tokens/s de throughput (el peor). Con 500-850 tokens
    # por turno entre razonamiento y salida, eso son 40-60s de generacion. Ver
    # el detalle y las mediciones en settings.OPENROUTER_PROVIDER_ORDER y en
    # docs/PENDIENTES.md #14. Medido: busqueda 17,28s -> 9,95s de mediana,
    # peor caso 35,50s -> 13,81s.
    return ChatOpenRouter(
        model=model, api_key=api_key, timeout=30_000, max_retries=0,
        reasoning=reasoning if reasoning is not None else {"effort": "medium"},
        # "provider" no es un field valido de ChatOpenRouter (genera un
        # UserWarning y termina metido a mano en model_kwargs) -- el field
        # correcto es "openrouter_provider", ver chat_models.py::_default_params.
        openrouter_provider={
            "order": settings.OPENROUTER_PROVIDER_ORDER, "allow_fallbacks": True,
        },
    )


async def _get_routing_llm():
    """Cliente para el RUTEO (`supervisor_node`), separado del conversacional.

    El ruteo elige entre ~5 slugs conocidos. Con el modelo conversacional eso
    medi­a mediana 2,78s y **maximo 62,40s** -- con `reasoning` ya desactivado,
    o sea que la cola era del proveedor, no del razonamiento. Era el peor
    componente de un turno: en un turno de 22,5s medido, el ruteo se llevo
    15,9s (71%).

    Con `settings.OPENROUTER_ROUTING_MODEL` (granite-4.2-8b, `reasoning: none`)
    la misma medicion dio mediana 0,71s y maximo 1,07s, ruteando 5/5 casos
    igual de bien. Auditoria completa en docs/PENDIENTES.md.

    Mismo patron que `bot/flow/media_processing.py::_get_media_llm`: modelo
    propio, API key compartida de OpenRouter."""
    from bot.models import get_setting
    model = (await sync_to_async(get_setting)("openrouter_routing_model_override", "")
             or settings.OPENROUTER_ROUTING_MODEL)
    api_key = (await sync_to_async(get_setting)("openrouter_api_key_override", "")
               or settings.OPENROUTER_API_KEY)
    # max_tokens explicito: el `effort` de razonamiento se calcula como un
    # PORCENTAJE de max_tokens (doc de OpenRouter), asi que sin techo el
    # presupuesto de razonamiento es el maximo del modelo. La respuesta del
    # ruteo es un JSON de dos campos.
    return ChatOpenRouter(
        model=model, api_key=api_key, timeout=15_000, max_retries=0,
        max_tokens=settings.OPENROUTER_ROUTING_MAX_TOKENS,
        reasoning={"effort": "none"},
        openrouter_provider={"sort": "latency"},
    )


# Backoff corto entre reintentos ante un error transitorio (429/5xx, o
# cualquier excepcion sin status_code -- ej. timeouts de red). No exponencial
# a proposito: el turno corre contra un presupuesto acotado
# (_PRESUPUESTO_LLM_TURNO_SEGUNDOS) y un backoff exponencial se lo comeria
# rapido, dejando sin tiempo al reintento que de verdad importa.
_BACKOFF_TRANSITORIO_SEGUNDOS = 1.5

# Tope de tiempo POR INTENTO, aplicado en la aplicacion con asyncio.wait_for.
#
# Hace falta porque ChatOpenRouter NO respeta su propio limite de tiempo.
# Verificado empiricamente el 2026-09-03 contra la libreria instalada, con un
# ensayo de 900 palabras (>20s de generacion) y un guardia externo de 45s:
#
#   request_timeout=3      -> no corto, siguio hasta los 45s del guardia
#   request_timeout=3000   -> no corto, siguio hasta los 45s del guardia
#   timeout=30_000 (lo que pasabamos) -> no corto, idem
#
# El valor SI llega al objeto (`timeout=30_000` aterriza en el field
# `request_timeout`, que lee 30000): lo que no ocurre es que se aplique. Y
# `model_config extra = "ignore"`, asi que un nombre de field equivocado se
# descarta sin error ni warning -- misma clase de trampa que ya documenta
# `openrouter_provider` en _get_llm.
#
# Por que importa, y no es teorico: el contenedor corre `gunicorn --workers 2
# --timeout 120`. Una llamada colgada retiene uno de los DOS workers y a los
# 120s gunicorn lo mata (el modo de falla del incidente de worker muerto que ya
# vivio este repo). El mismo 2026-09-03 se observaron en Langfuse llamadas
# reales de 288s, 92s y 54s durante una degradacion del proveedor. Sin tope, el
# contacto no recibe nada; con tope, el reintento corre y en el peor caso sale
# el fallback -- que es la regla explicita del usuario: nunca dejar al cliente
# sin respuesta.
#
# 30s x 3 intentos + 2 backoffs de 1,5s = 93s en UN solo bucle. Eso solo no
# alcanza para quedar bajo los 120s de gunicorn, porque los bucles estan
# anidados -- ver _PRESUPUESTO_LLM_TURNO_SEGUNDOS justo abajo, que es el
# deadline que acota el total del turno. Los dos conviven: este es el techo
# por INTENTO y aquel el techo del TURNO.
_TIMEOUT_LLM_SEGUNDOS = 30
# El ruteo tiene su propio tope, mas corto: su salida es un JSON de dos campos
# y su mediana medida es 0,62s, asi que 15s ya es un outlier gigante.
_TIMEOUT_RUTEO_SEGUNDOS = 15

# Con que especialista se especula mientras corre el ruteo (docs/PENDIENTES.md
# 29.a). FIJO en "ventas" y no el `active_agent` previo: eso se midio y es
# mucho peor (51-61% de acierto contra 81,9%), porque pierde en todos los
# turnos nuevos, donde el previo es "ninguno".
_AGENTE_ESPECULADO = "ventas"
# Kill switch, apagable desde el panel sin deploy (misma convencion que
# `bot_global_on`, ver bot/api.py).
_SETTING_ESPECULACION = "especulacion_ruteo_on"

# Presupuesto de tiempo de LLM POR TURNO (docs/PENDIENTES.md #14, causa raiz 3,
# la unica de esa auditoria que quedaba abierta).
#
# El tope por intento no alcanza porque los reintentos estan ANIDADOS:
# _ainvoke_messages_with_retry hace 3 intentos de 30s con 2 backoffs de 1,5s
# (93s) y _ainvoke_tools_json_with_retry lo llama hasta 3 veces ENCIMA (279s),
# mas el ruteo (3 x 15s + 2 backoffs = 48s): peor caso ~327s en una sola
# request. El contenedor corre `gunicorn --workers 2 --timeout 120`, asi que a
# los 120s el worker muere con SIGABRT -> SystemExit, que NO es subclase de
# Exception: ni el `except Exception` de handlers.py ni ningun fallback llegan
# a correr y el contacto no recibe absolutamente nada. Es el peor modo de falla
# que tiene este bot, y ya ocurrio de verdad (ver
# _MEDIA_LLM_DEADLINE_SEGUNDOS en bot/flow/media_processing.py: mismo patron,
# mismo motivo, y el precedente directo de este deadline).
#
# La cuenta, con numeros medidos de este mismo repo:
#
#   120,00s  --timeout de gunicorn (Dockerfile) -- limite duro
#   - 36,00s peor handshake de SQL Server medido (#17). Con CONN_MAX_AGE=600
#            lo paga solo el primer request de cada worker, pero cuando pega,
#            pega dentro de la misma request que el turno.
#   -  9,00s RAG: ~3s por accion de negocio, hasta 3 acciones por turno (#14)
#   -  0,79s envio a Meta dentro del turno (medido, ver cola_envio.py)
#   -  0,02s grafo + BD + Python (#14) -- despreciable
#   = ~46s; se reserva 50s redondeando hacia arriba
#   -> 70s para TODAS las llamadas al LLM del turno juntas (ruteo +
#      especialista + reintentos de transporte + reintentos de formato).
#
# Por que 70s no recorta nada de lo que hoy funciona: el peor turno sano medido
# en produccion es de 14,53s (#14), o sea que el presupuesto es ~5x eso, y
# entran dos intentos completos de 30s con su backoff. Y desde el refactor de
# prosa el bucle externo mide 0,00 reintentos por turno: esto es robustez para
# la cola patologica, no un cambio de la latencia promedio.
_TIMEOUT_GUNICORN_SEGUNDOS = 120
_RESERVA_NO_LLM_SEGUNDOS = 50
_PRESUPUESTO_LLM_TURNO_SEGUNDOS = _TIMEOUT_GUNICORN_SEGUNDOS - _RESERVA_NO_LLM_SEGUNDOS

# Vencimiento (reloj monotonico) del turno en curso, o None cuando no hay turno
# armado -- tests, scripts y cualquier llamada suelta siguen corriendo solo con
# el tope por intento, igual que antes. Lo arma _GrafoConPresupuestoDeTurno.
_vencimiento_del_turno = contextvars.ContextVar("vencimiento_del_turno", default=None)


def _segundos_restantes_del_turno() -> float | None:
    vence = _vencimiento_del_turno.get()
    return None if vence is None else vence - time.monotonic()


def _tope_del_intento(tope_segundos: float) -> float:
    """El tope de ESTE intento: el de siempre, recortado a lo que quede del
    presupuesto del turno.

    El deadline del turno va ENCIMA del tope por intento, nunca en lugar de el:
    con presupuesto de sobra un intento sigue sin poder pasar de
    _TIMEOUT_LLM_SEGUNDOS (que es lo que evita que UNA llamada colgada retenga
    uno de los dos workers), y sobre el final del presupuesto el intento se
    acorta a lo que queda en vez de pasarse."""
    restante = _segundos_restantes_del_turno()
    return tope_segundos if restante is None else min(tope_segundos, restante)


def _alcanza_para_el_backoff() -> bool:
    """Si lo que queda del presupuesto no cubre ni el backoff, dormirlo es
    tiempo tirado: el intento siguiente se corta al entrar igual."""
    restante = _segundos_restantes_del_turno()
    return restante is None or restante > _BACKOFF_TRANSITORIO_SEGUNDOS


def _cortar_por_presupuesto(label: str, attempt: int, last_exc: Exception | None):
    """Que pasa cuando se agota el presupuesto, en los dos bucles de
    transporte: se levanta TimeoutError.

    No es "el turno se pierde": handlers.py::_run_graph ya atrapa cualquier
    excepcion del grafo, le responde al contacto el mismo
    RESPUESTA_GENERICA_JSON_INVALIDO y registra un Incident de revision (ese
    catch existe desde #16, por el 403 de prompt injection). O sea que cortar
    aca cambia "worker muerto y silencio total" por "fallback al cliente y
    aviso al operador", que es la regla explicita: nunca dejar al contacto sin
    respuesta."""
    logger.error(
        "[%s] presupuesto de %ss de LLM del turno agotado en el intento %s -- se corta antes"
        " del --timeout %ss de gunicorn",
        label, _PRESUPUESTO_LLM_TURNO_SEGUNDOS, attempt, _TIMEOUT_GUNICORN_SEGUNDOS,
    )
    raise TimeoutError(
        f"el presupuesto de {_PRESUPUESTO_LLM_TURNO_SEGUNDOS}s de LLM del turno se agoto"
    ) from last_exc


async def _ainvoke_con_tope(llm, entrada, config: dict, tope: float):
    """`llm.ainvoke` con un tope de tiempo real. Ver _TIMEOUT_LLM_SEGUNDOS.

    Convierte el corte en TimeoutError, que `_es_error_permanente` clasifica
    como transitorio (no tiene `status_code`), asi que el reintento de la capa
    de arriba corre normalmente -- igual que ante un 5xx o un error de red.
    """
    try:
        return await asyncio.wait_for(llm.ainvoke(entrada, config=config), timeout=tope)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"la llamada al LLM paso el tope de {tope}s") from exc


def _es_error_permanente(exc: Exception) -> bool:
    """4xx que no sea 429 (rate limit) es un error de autenticacion/validacion
    -- un reintento identico no lo va a resolver, asi que conviene fallar
    rapido en vez de gastar los reintentos restantes (y su backoff) en algo
    que nunca va a cambiar de resultado. 429/5xx, y cualquier excepcion sin
    `status_code` (timeouts de red, errores de conexion), se tratan como
    transitorios."""
    status_code = getattr(exc, "status_code", None)
    return status_code is not None and 400 <= status_code < 500 and status_code != 429


async def _ainvoke_with_retry(
    llm, prompt: str, label: str = "llm", max_retries: int = 2,
    run_name: str = None, tags: list = None,
    tope_segundos: float = _TIMEOUT_RUTEO_SEGUNDOS,
) -> str:
    """`label` es solo para los logs de warning/error de abajo. `run_name`
    (nombre de la GENERATION en Langfuse) se mantiene deliberadamente
    separado y SIN valores dinamicos (ej. nombre de agente, indice de
    intento) -- ver "Choose good names" en
    https://langfuse.com/docs/observability/best-practices: un nombre con
    variables revienta el agrupamiento/filtrado por nombre en dashboards y
    evaluators. Lo variable va en `tags` en su lugar."""
    last_exc = None
    for attempt in range(max_retries + 1):
        # El ruteo gasta del MISMO presupuesto que el especialista: es la
        # primera llamada del turno y sus intentos cuentan igual contra los
        # 120s de gunicorn.
        tope = _tope_del_intento(tope_segundos)
        if tope <= 0:
            _cortar_por_presupuesto(label, attempt, last_exc)
        try:
            # CallbackHandler() fresco por intento: sin contexto propio, se
            # engancha al trace activo (ver @observe en
            # bot/whatsapp/handlers.py::_run_graph) para que las 3 llamadas
            # de un mismo turno (supervisor, specialist, business_action)
            # queden agrupadas en un solo trace de Langfuse en vez de 3 sueltos.
            result = await _ainvoke_con_tope(
                llm, prompt,
                {
                    "callbacks": [CallbackHandler()],
                    "run_name": run_name or label,
                    "metadata": {"langfuse_tags": tags} if tags else {},
                },
                tope,
            )
            return result.content
        except Exception as exc:
            last_exc = exc
            logger.warning("[%s] intento %s fallo: %s", label, attempt, exc)
            if _es_error_permanente(exc):
                raise
            if attempt < max_retries:
                if not _alcanza_para_el_backoff():
                    _cortar_por_presupuesto(label, attempt, last_exc)
                await asyncio.sleep(_BACKOFF_TRANSITORIO_SEGUNDOS)
    raise last_exc


def _texto_de_respuesta(contenido, separador: str = "") -> str:
    """`.content` de un chat model de LangChain (ej. ai_msg.content,
    result.content) puede venir como `str` plano o como una lista de
    content blocks -- esto es el path REAL del bot en produccion (no el
    simulador), y el mismo problema ya fue encontrado y arreglado antes
    para el juez del simulador en bot/simulator/judge.py::_texto_de_respuesta
    (commit 1e9cbc6): Gemini real devolvio `.content` como
    `[{"type": "text", "text": "...", "extras": {...}}]` en vez de un
    string, y eso rompia _parse_json_response con AttributeError apenas
    intentaba hacer raw.strip(). Reproducido 2/2 veces contra Gemini real
    en el turno del especialista "ventas". Se extrae el texto util de cada
    bloque tipo dict en vez de forzar a string (str(lista) da el repr de
    Python, nunca JSON valido). Este es el helper canonico: tambien lo usa
    bot/simulator/judge.py (via import) y bot/simulator/runner.py delega en
    el desde _contenido_como_texto. `separador` controla como se unen los
    bloques de texto cuando `contenido` es una lista -- por defecto "" (uso
    del juez/parseo JSON), pero runner.py lo llama con " " para matching de
    frases legibles por humanos (ej. detectar "gracias, listo" en el cierre
    del cliente simulado)."""
    if isinstance(contenido, list):
        return separador.join(
            bloque.get("text", "") if isinstance(bloque, dict) else str(bloque)
            for bloque in contenido
        )
    return str(contenido)


# Escapes que JSON acepta despues de una barra invertida. Cualquier otra cosa
# ("\\)", "\\ ", "\\$") hace fallar json.loads ENTERO, aunque el resto del
# objeto este perfecto.
_ESCAPE_INVALIDO = re.compile(r'\\(?![\\"/bfnrtu])')


def _parse_json_response(raw) -> dict:
    raw = _texto_de_respuesta(raw)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    # NOTA sobre los dos json.loads de esta funcion (este y el del rescate
    # mas abajo): json.loads devuelve el tipo JSON que venga, no
    # necesariamente un dict -- `"hola"` es un string JSON valido y da un
    # str, `[1, 2]` da una lista, `42`/`null` dan int/None. Esta funcion esta
    # anotada -> dict, asi que un tipo distinto se trata igual que JSON
    # malformado (mismo camino: loguear y devolver {}) en vez de propagar el
    # tipo equivocado a los llamadores, que hacen .get() sobre el resultado
    # sin chequear. AttributeError real reproducido en
    # _specialist_node_con_tools con el LLM devolviendo un string JSON en vez
    # de un objeto -- misma familia de bug que el shape de `.content` como
    # lista documentado arriba (_texto_de_respuesta) y que
    # _extraer_sucursal_ids_de_tools asumiendo dict en docs/PENDIENTES.md.
    # Se loguea distinto de "no es JSON valido" porque aca el JSON SI es
    # valido -- lo que no sirve es el tipo, y conviene poder distinguir los
    # dos casos en produccion.
    try:
        parseado = json.loads(raw)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(parseado, dict):
            return parseado
        logger.error(
            "[graph] respuesta del LLM es JSON valido pero no es un objeto (tipo %s): %s",
            type(parseado).__name__, raw,
        )
        return {}
    # A veces el LLM "piensa en voz alta" en texto plano y recien despues
    # escribe el JSON (bug real reproducido en produccion, wsp_demo,
    # conversacion sobre financiamiento del Koleos: el mensaje bueno con
    # precio+bono+pregunta de pie/cuotas se perdia entero porque json.loads
    # no tolera texto suelto antes del "{", y el reintento completo de
    # _ainvoke_json_with_retry generaba una respuesta nueva mucho mas pobre
    # (sin la pregunta de seguimiento) en vez de rescatar la buena. Se
    # busca el primer "{" y el ultimo "}" y se intenta parsear ese
    # subrango antes de resignarse y descartar la respuesta completa.
    inicio, fin = raw.find("{"), raw.rfind("}")
    if inicio != -1 and fin > inicio:
        try:
            parseado = json.loads(raw[inicio:fin + 1])
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parseado, dict):
                return parseado
            logger.error(
                "[graph] rescate de texto suelto dio JSON valido pero no un objeto (tipo %s): %s",
                type(parseado).__name__, raw,
            )
            return {}
    # Ultimo rescate: escapes invalidos. El LLM a veces mete una barra
    # invertida suelta en medio del texto y eso tira abajo el JSON COMPLETO
    # aunque todo lo demas este bien formado. Caso real (docs/PENDIENTES.md
    # #24): devolvio
    #
    #   {"mensaje": "...abierta este sabado de 09:00 a 13:00\\)...", ...}
    #
    # con un "\\)" que no es un escape valido. La respuesta era correcta y util,
    # y se descarto entera: los 3 intentos produjeron el mismo caracter y el
    # contacto termino viendo "Disculpa, tuve un problema para responderte".
    #
    # Se quita la barra sobrante y se reintenta. Va al final, despues de los
    # otros dos caminos, para no tocar el texto de un JSON que ya parsea bien.
    limpio = _ESCAPE_INVALIDO.sub("", raw[inicio:fin + 1] if inicio != -1 and fin > inicio else raw)
    try:
        parseado = json.loads(limpio)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(parseado, dict):
            logger.warning(
                "[graph] el JSON del LLM traia un escape invalido y se rescato limpiandolo")
            return parseado
    logger.error("[graph] respuesta del LLM no es JSON valido: %s", raw)
    return {}


def _construir_mensajes(state: BotState, system_prompt: str) -> list:
    """El historial de tool-calling de este turno (AIMessage(tool_calls=...)
    seguido de su ToolMessage) se acumula en state["tool_messages"] via el
    reducer add_messages de LangGraph (concatena en vez de reemplazar) --
    ver comentario en BotState.tool_messages para el bug real que esto
    reemplaza."""
    mensajes = [SystemMessage(content=system_prompt)]
    for m in state.get("messages", []):
        if m["role"] == "user":
            cls = HumanMessage
        elif m["role"] == "system":
            # Marcador de historial truncado (bot/flow/context_window.py) --
            # no es un turno real de user/assistant, es una nota de contexto.
            cls = SystemMessage
        else:
            cls = AIMessage
        mensajes.append(cls(content=m["content"]))
    mensajes.append(HumanMessage(content=state["text"]))
    mensajes.extend(state.get("tool_messages") or [])
    # El recordatorio del canal de salida va SIEMPRE ultimo, despues de los
    # tool_messages: es lo que el modelo tiene mas fresco al generar. Ver
    # RECORDATORIO_RESPUESTA en bot/flow/respuesta.py para la medicion (25% de
    # respuestas en prosa sin esto, 10% con esto).
    mensajes.append(SystemMessage(content=RECORDATORIO_RESPUESTA))
    return mensajes


# Lapsus real confirmado (analisis de FB 2026-08-27): estaba en "usted"
# ("Disculpe... responderle... su pregunta"), inconsistente con el resto
# del bot, que siempre usa "tu" -- justo se notaba en las conversaciones
# donde el LLM se confundia (ej. el intercambio sobre JSON de la seccion
# 14) porque ese es el escenario real que dispara este fallback.
RESPUESTA_GENERICA_JSON_INVALIDO = "Disculpa, tuve un problema para responderte. ¿Me repites tu pregunta?"


async def _ainvoke_messages_with_retry(
    llm, mensajes: list, label: str, max_retries: int = 2,
    run_name: str = None, tags: list = None,
    tope_segundos: float = _TIMEOUT_LLM_SEGUNDOS,
):
    """Como _ainvoke_with_retry, pero para el path de tool-calling: recibe y
    devuelve mensajes/AIMessage de LangChain en vez de un string -- hace
    falta el AIMessage completo (no solo .content) para leer .tool_calls."""
    last_exc = None
    for attempt in range(max_retries + 1):
        tope = _tope_del_intento(tope_segundos)
        if tope <= 0:
            _cortar_por_presupuesto(label, attempt, last_exc)
        try:
            result = await _ainvoke_con_tope(
                llm, mensajes,
                {
                    "callbacks": [CallbackHandler()],
                    "run_name": run_name or label,
                    "metadata": {"langfuse_tags": tags} if tags else {},
                },
                tope,
            )
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning("[%s] intento %s fallo: %s", label, attempt, exc)
            if _es_error_permanente(exc):
                raise
            if attempt < max_retries:
                if not _alcanza_para_el_backoff():
                    _cortar_por_presupuesto(label, attempt, last_exc)
                await asyncio.sleep(_BACKOFF_TRANSITORIO_SEGUNDOS)
    raise last_exc


def _salida_utilizable(ai_msg, nombre_agente: str) -> bool:
    """Si esta respuesta del LLM se puede usar tal cual, sin gastar otra llamada.

    Cuatro formas validas, en orden de preferencia:
      1. pidio una accion de negocio -> la respuesta final se redacta en la
         vuelta siguiente, no hay nada que validar todavia;
      2. llamo a `responder` con un mensaje util -> es la respuesta final
         (camino nuevo, ver bot/flow/respuesta.py);
      3. escribio el JSON del contrato viejo en .content -> fallback de
         compatibilidad para prompts custom en BD que lo pidan a mano;
      4. escribio PROSA utilizable -> es la respuesta, tal cual. Los metadatos
         los saca el extractor despues (bot/flow/extractor_metadatos.py).
    """
    if tool_calls_de_negocio(ai_msg):
        return True
    if respuesta_de_tool_calls(ai_msg, nombre_agente) is not None:
        return True
    # La PROSA se chequea ANTES del JSON viejo, y el orden importa por una
    # razon practica: _parse_json_response loguea un logger.error con el texto
    # completo cuando no parsea, y desde el 2026-09-03 no parsear es lo NORMAL
    # (la prosa no es JSON). Chequeandolo despues, cada turno sano dejaba 2-3
    # errores en el log con la respuesta del cliente adentro -- ruido que
    # taparia un problema de verdad. Es seguro invertirlo porque
    # prosa_utilizable rechaza todo lo que empiece con "{" o con "```", que es
    # la unica forma en que llega el contrato viejo.
    if prosa_utilizable(_texto_de_respuesta(ai_msg.content)):
        return True
    return bool(_parse_json_response(ai_msg.content))


async def _ainvoke_tools_json_with_retry(
    llm, mensajes: list, label: str, max_retries: int = 2,
    run_name: str = None, tags: list = None, nombre_agente: str = "",
):
    """Analogo a _ainvoke_json_with_retry para el path de tool-calling: se
    reintenta la llamada completa solo cuando la respuesta no es utilizable de
    ninguna de las tres formas que describe _salida_utilizable.

    Patron real detectado en Langfuse (visto tras run-business-action con
    resultado negativo, en "faq" y ya documentado antes en "ventas" --
    docs/PENDIENTES.md): el LLM se "traba" en modo conversacional en texto
    plano, y reintentar con exactamente los mismos mensajes no lo saca de
    ahi -- las 3 respuestas fallidas de un intento real fueron casi
    identicas. Por eso cada reintento agrega la respuesta mala + un
    recordatorio explicito de formato en vez de repetir la llamada a ciegas.

    Desde el 2026-09-03 este reintento deberia ser RARO, no la norma: era el
    27% del turno (3,78s de media, en el 95% de los turnos) porque el contrato
    de salida se pedia como JSON en texto mientras el modelo tenia tools
    bindeadas. Ahora el contrato es la tool `responder`. Ver el docstring de
    bot/flow/respuesta.py para la medicion completa. Si vuelve a subir, es
    senal de que algo desalineo el prompt y la tool."""
    ai_msg = None
    mensajes_intento = mensajes
    for attempt in range(max_retries + 1):
        ai_msg = await _ainvoke_messages_with_retry(llm, mensajes_intento, label=label, run_name=run_name, tags=tags)
        if _salida_utilizable(ai_msg, nombre_agente):
            return ai_msg
        # A diferencia de los bucles de transporte, aca el presupuesto agotado
        # NO levanta TimeoutError: se devuelve la ultima respuesta aunque sea
        # inutilizable. specialist_node la convierte en
        # RESPUESTA_GENERICA_JSON_INVALIDO, o sea que el cliente recibe un
        # mensaje -- exactamente el mismo final que ya tiene hoy agotar los
        # reintentos de formato. Cortar con excepcion aca seria cambiar un
        # camino que ya termina bien por uno peor.
        if _segundos_restantes_del_turno() is not None and _segundos_restantes_del_turno() <= 0:
            logger.error(
                "[%s] presupuesto del turno agotado tras el intento %s de formato: se devuelve "
                "la ultima respuesta y el turno cae al mensaje generico", label, attempt,
            )
            return ai_msg
        logger.warning("[%s] intento %s: no llamo a responder ni devolvio JSON valido, reintentando", label, attempt)
        mensajes_intento = mensajes + [
            AIMessage(content=_texto_de_respuesta(ai_msg.content)),
            HumanMessage(content=(
                "Tu respuesta anterior no siguio el formato pedido -- escribiste el mensaje como "
                "texto suelto en vez de entregarlo por la herramienta `responder`. Volve a "
                "responder a mi ultimo mensaje, esta vez llamando a `responder` con el texto en "
                "su argumento `mensaje`."
            )),
        ]
    return ai_msg


# El criterio de "intencion_compra_real" de abajo debe mantenerse alineado
# con la seccion de disparo de crear_lead en el prompt guardado de
# custom:ventas (dato en BD, no en este archivo) -- ver
# docs/superpowers/specs/2026-08-06-supervisor-ruteo-intencion-compra-design.md.
SUPERVISOR_PROMPT = """Eres el supervisor de un bot de WhatsApp con estos especialistas disponibles:
{descripciones}

Agente activo previo (si aplica, mantenlo salvo que el contacto cambie claramente de tema): {active_agent}

Historial reciente:
{historial}

Mensaje actual: {text}

Ademas de elegir el agente, evalua si el mensaje actual muestra intencion real
de compra: el cliente quiere avanzar MAS ALLA de la informacion y simulacion
que el bot ya le puede dar (precio del auto, simulacion de financiamiento) --
es decir, agendar un test drive, pedir una cotizacion formal de la
concesionaria (no una simulacion de financiamiento), o reservar. Pedir el
precio, pedir que se simule el credito, o pedir mas detalles del auto NO es
intencion real de compra por si solo, aunque se repita varias veces. Marca
este campo true solo por el hecho observado en el mensaje, sin importar cual
sea el agente activo previo o el agente que elijas en esta misma respuesta.

Responde SOLO con JSON: {{"agente": "<uno de {agentes}>", "intencion_compra_real": true|false}}
"""


async def _lanzar_especulacion(state: BotState, registry: dict):
    """Dispara gen#1 con el prompt de `_AGENTE_ESPECULADO` en paralelo al
    ruteo. Devuelve la Task, o None si no corresponde especular.

    `asyncio.create_task` y NO un fan-out de LangGraph: su doc dice que **no
    cancela ramas**, asi que un fan-out correria las dos hasta el final y el
    caso de fallo quedaria +1,6s PEOR que hoy en vez de igual. La Task vive en
    el mismo event loop del turno, se consume o se cancela dentro de
    `supervisor_node` y por lo tanto no cruza a los threads de
    bot/whatsapp/cola_envio.py, no agrega conexiones a SQL Server y no consume
    slots de gunicorn.

    Hereda el contexto del llamador, asi que el `_vencimiento_del_turno` que
    puso `_GrafoConPresupuestoDeTurno` tambien la acota: una especulacion no
    puede pasarse del presupuesto del turno.
    """
    if _AGENTE_ESPECULADO not in registry:
        return None
    # Defensivo: hoy `supervisor_node` corre una sola vez por turno (ninguna
    # arista vuelve a el), asi que `tool_messages` esta vacio siempre. Si eso
    # cambiara, especular con el historial de tools a medio hacer generaria
    # una respuesta que ignora el resultado que la motivo.
    if state.get("tool_messages"):
        return None
    from bot.models import get_setting
    if await sync_to_async(get_setting)(_SETTING_ESPECULACION, "true") != "true":
        return None
    agent = registry[_AGENTE_ESPECULADO]
    # Mismo `reasoning` que usaria la llamada real: `supervisor_node` corre con
    # `tool_messages` vacio, o sea que la gen#1 que estamos adelantando es
    # exactamente la que `specialist_node` haria con effort "none" (ver el
    # comentario de `primera_llamada_del_turno` mas abajo).
    llm = await _get_llm(reasoning={"effort": "none"})
    return asyncio.create_task(
        _generar_del_especialista(
            state, agent, llm, agent.business_actions(),
            # Nombre propio en Langfuse: compartir `generate-response` rompe
            # todo analisis de "la primera generate-response del turno", del
            # que ya dependen las mediciones de latencia de este repo.
            run_name="generate-response-especulativa",
        )
    )


async def _cosechar_especulacion(tarea):
    """El AIMessage de la especulacion, o None si fallo.

    Un fallo NO tumba el turno: se cae al camino normal y `specialist_node`
    genera de nuevo. Perder la especulacion cuesta los 0,81s de ahorro, no la
    respuesta."""
    try:
        return await tarea
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("[especulacion] la generacion especulativa fallo, "
                       "se genera de nuevo en el especialista: %s", exc)
        return None


async def supervisor_node(state: BotState) -> dict:
    deterministic = await sync_to_async(resolve_agent_for_campaign)(state.get("campaign_hint"), state)
    if deterministic:
        # Pre-ruteo deterministico: no hay llamada de ruteo con la que
        # solapar, asi que no hay nada que especular.
        return {"active_agent": deterministic, "_dispatch": deterministic}

    registry = await sync_to_async(build_agent_registry)()
    tarea = await _lanzar_especulacion(state, registry)
    cosechada = False
    try:
        chosen = await _rutear_con_el_llm(state, registry)
        if tarea is not None and chosen == _AGENTE_ESPECULADO:
            gen = await _cosechar_especulacion(tarea)
            cosechada = True
            if gen is not None:
                # La clave solo se escribe cuando el ruteo CONFIRMO el
                # especialista especulado: si eligio otro, nada de lo
                # especulado entra al estado (capa 3 de la barrera de 29.a).
                return {"active_agent": chosen, "_dispatch": chosen,
                        "gen_especulativa": gen}
        return {"active_agent": chosen, "_dispatch": chosen}
    finally:
        # OBLIGATORIO, y en `finally` para cubrir tambien el camino de
        # excepcion: el 403 del supervisor del 02-09 (docs/PENDIENTES.md #16)
        # prueba que ese camino ocurre de verdad. Sin cancelar, el caso de
        # fallo cuesta +1,6s; con cancelacion cuesta lo mismo que hoy.
        if tarea is not None and not cosechada:
            tarea.cancel()


async def _rutear_con_el_llm(state: BotState, registry: dict) -> str:
    """El slug del especialista que elige el LLM de ruteo.

    Extraido de `supervisor_node` para que este pueda envolver el ruteo en el
    try/finally que cancela la especulacion, sin re-indentar toda esta logica
    (29.a). El comportamiento no cambio al extraerse."""
    # Modelo propio para el ruteo, no el conversacional: ver _get_routing_llm
    # (mediana 2,78s -> 0,71s, maximo 62,40s -> 1,07s, medido 2026-09-02).
    llm = await _get_routing_llm()
    # Se saltean los mensajes de rol "system": el unico que existe es
    # _MARCADOR_HISTORIAL_TRUNCADO (bot/flow/context_window.py), una nota para
    # que el ESPECIALISTA no niegue algo que quedo fuera de la ventana -- al
    # supervisor, que solo elige a que agente rutear, no le aporta nada.
    #
    # Y aplanarlo aca rompia el bot entero. Este prompt se manda como texto, y
    # una linea "system: (no asumas que nunca paso ni lo niegues; pedile que te
    # lo recuerde...)" dentro de un prompt es la firma de un prompt injection:
    # OpenRouter la bloqueaba con 403 "prompt injection patterns detected".
    # Como el marcador se agrega recien al pasar MAX_TURNS mensajes, cualquier
    # conversacion de mas de 20 mensajes dejaba al supervisor fallando SIEMPRE,
    # con HTTP 500 en el webhook y silencio total para el contacto. Caso real
    # 2026-09-02 (docs/PENDIENTES.md #16), reproducido con los dos modelos.
    historial = "\n".join(
        f"{m['role']}: {m['content']}"
        for m in state.get("messages", [])
        if m.get("role") != "system"
    )
    descripciones = "\n".join(f"- {slug}: {agent.descripcion}" for slug, agent in registry.items())
    prompt = SUPERVISOR_PROMPT.format(
        agentes=list(registry.keys()),
        descripciones=descripciones,
        active_agent=state.get("active_agent") or "ninguno",
        historial=historial or "(sin historial)",
        text=state["text"],
    )
    raw = await _ainvoke_with_retry(llm, prompt, label="supervisor", run_name="classify-intent")
    parsed = _parse_json_response(raw)
    if parsed.get("agente") in registry:
        chosen = parsed["agente"]
    else:
        # Output invalido/agente inexistente del LLM -> catch-all seguro
        # ("faq") en vez del primer especialista registrado (que podria
        # ser uno de negocio como "agendamiento" y empujar a un usuario
        # con una pregunta suelta a un flujo de reserva no solicitado).
        chosen = "faq" if "faq" in registry else next(iter(registry))
    if parsed.get("intencion_compra_real") and "ventas" in registry:
        # Se evalua DESPUES de resolver chosen (incluido el fallback) para
        # que una intencion real de compra llegue a "ventas" incluso si el
        # campo "agente" vino invalido en la misma respuesta.
        # Trade-off aceptado: este override no mira flow_state/flow_data, asi
        # que si el contacto esta a mitad de un flujo con estado propio (ej.
        # agendamiento con datos ya capturados) y el LLM lee intencion real de
        # compra en el mismo turno, ese estado en progreso queda invisible para
        # "ventas" (CustomPromptAgent.build_prompt no consume flow_data). El
        # spec de este cambio solo definio "pegajosidad" (no quedarse pegado al
        # agente previo), no la perdida de flow_data -- se documenta aca en vez
        # de agregar acoplamiento nuevo al override.
        logger.info("[supervisor] intencion_compra_real=true -- promuevo de %s a ventas", chosen)
        chosen = "ventas"
    return chosen


async def specialist_node(state: BotState) -> dict:
    registry = await sync_to_async(build_agent_registry)()
    if state["active_agent"] in registry:
        agent = registry[state["active_agent"]]
    else:
        # El slug activo ya no existe en el registro (p.ej. una regla de
        # campania apunta a un especialista custom borrado desde el panel,
        # o una carrera admin-borra-mientras-el-turno-corre) -> catch-all
        # seguro en vez de un KeyError que tumba el turno.
        fallback = "faq" if "faq" in registry else next(iter(registry))
        state = {**state, "active_agent": fallback}
        agent = registry[fallback]
    # Este nodo corre DOS veces por turno y las dos llamadas hacen trabajos
    # distintos: la primera elige una herramienta -- su salida entera es un
    # tool_call que el cliente nunca ve -- y la segunda, ya con el resultado
    # en la mano, redacta la respuesta. `tool_messages` vacio es exactamente
    # esa frontera: se puebla recien al pasar por business_action_node.
    #
    # El razonamiento se apaga SOLO en la primera. Medido el 2026-09-03 contra
    # el LLM real (12 turnos x 3 repeticiones, con control de llamada minima
    # intercalado): la primera llamada gastaba 78 tokens de razonamiento sobre
    # 129 de salida y median 1,68s; con `none` mide 1,21s (-0,47s) y gasta 0.
    # En las trazas de produccion el desperdicio es mayor -- 150 tokens de
    # razonamiento sobre 266 de salida -- porque ahi los turnos son mas largos.
    # Elige la MISMA herramienta en los 10 turnos del banco que necesitan una,
    # incluidos los casos que se equivocan facil (comparar dos autos contra
    # pedir una ficha, busqueda contra ficha puntual, simulacion por cuota).
    #
    # La segunda NO se toca, y es deliberado: es la que escribe lo que el
    # cliente lee. Bajarle el esfuerzo ya se probo y se descarto con evidencia
    # en docs/PENDIENTES.md #14 (derivaba a voseo contra la regla de tuteo
    # chileno, aparecian erratas, y perdia el desglose del monto financiado que
    # el §9 del Word exige mostrar). Esta separacion respeta esa decision en
    # vez de contradecirla: distingue la llamada que piensa QUE consultar de
    # la que escribe QUE decir.
    #
    # Nota sobre el flag: el docstring de _get_llm advierte que el modelo no
    # soporta "medium" y que OpenRouter lo remapea a "high" en silencio. `none`
    # SI se honra -- verificado en el mismo banco, los tokens de razonamiento
    # bajaron a cero.
    primera_llamada_del_turno = not state.get("tool_messages")

    # Especulacion del ruteo (docs/PENDIENTES.md 29.a): si `supervisor_node`
    # ya adelanto esta misma generacion, se interpreta y se ahorra la llamada.
    #
    # `primera_llamada_del_turno` es la guarda que importa: `gen_especulativa`
    # SOBREVIVE en el state, asi que sin ella la segunda vuelta (la de despues
    # de un business_action) reusaria la respuesta de gen#1 e ignoraria el
    # resultado de la tool que acababa de pedir -- o volveria a pedirla, que es
    # el loop del incidente de tool_choice=required. Se limpia igual al
    # consumirla, para no depender de una sola guarda.
    gen = state.get("gen_especulativa")
    if gen is not None and primera_llamada_del_turno and agent.name == _AGENTE_ESPECULADO:
        salida = await _interpretar_salida_del_especialista(state, agent, gen)
        salida["gen_especulativa"] = None
        return salida

    llm = await _get_llm(reasoning={"effort": "none"} if primera_llamada_del_turno else None)
    return await _specialist_node_con_tools(state, agent, llm, agent.business_actions())


def _validar_choice(valor, choices: list[tuple[str, str]]) -> str | None:
    validos = {c[0] for c in choices}
    return valor if valor in validos else None


def _validar_sucursal_ids(valores) -> list[int]:
    """Valida una lista de ids de Sucursal contra la BD (existe + tiene
    coordenadas), preservando el orden y sin duplicados. Ids invalidos o
    alucinados por el LLM se descartan en silencio en vez de reventar."""
    from bot.models import Sucursal
    if not isinstance(valores, list):
        return []
    ids_pedidos = []
    for v in valores:
        try:
            ids_pedidos.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids_pedidos:
        return []
    existentes = set(Sucursal.objects.filter(
        pk__in=ids_pedidos, latitud__isnull=False, longitud__isnull=False,
    ).values_list("pk", flat=True))
    vistos, resultado = set(), []
    for i in ids_pedidos:
        if i in existentes and i not in vistos:
            vistos.add(i)
            resultado.append(i)
    return resultado


_RE_SUCURSAL_FUENTE = re.compile(r"catalogo://sucursal/(\d+)")


def _extraer_sucursal_ids_de_tools(tool_messages: list) -> list[int]:
    """Fallback deterministico: el LLM deberia ecoar sucursal_direccion_ids
    (ver faq.py) pero en la practica lo omite seguido (visto en Langfuse: la
    herramienta trae la sucursal correcta y el mensaje cita su direccion,
    pero el JSON de salida no trae el campo). Junta los ids de sucursal que
    de verdad aparecieron en resultados de herramientas este turno --
    "fuente": "catalogo://sucursal/<id>" de consultar_base_conocimiento, o
    la lista "sucursales": [{"id": ...}] de buscar_sucursales_cercanas --
    en vez de confiar ciegamente en lo que el LLM haya copiado."""
    ids, vistos = [], set()
    for m in tool_messages or []:
        contenido = getattr(m, "content", None)
        if not isinstance(contenido, str):
            continue
        for i in _RE_SUCURSAL_FUENTE.findall(contenido):
            i = int(i)
            if i not in vistos:
                vistos.add(i)
                ids.append(i)
        try:
            data = json.loads(contenido)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            # Varias tools devuelven una lista en vez de un dict (ej.
            # consultar_disponibilidad -> [{"hora": ...}, ...]) -- nada que
            # ver con sucursales, pero data.get() reventaria el turno entero.
            continue
        for s in (data.get("sucursales") or []):
            if not isinstance(s, dict):
                continue
            try:
                i = int(s.get("id"))
            except (TypeError, ValueError):
                continue
            if i not in vistos:
                vistos.add(i)
                ids.append(i)
    return ids


async def _specialist_node_con_tools(state: BotState, agent, llm, tools: list) -> dict:
    """Camino normal del especialista: genera e interpreta.

    Las dos mitades viven separadas para que la especulacion del ruteo
    (docs/PENDIENTES.md 29.a) pueda generar sin interpretar; esta funcion las
    compone y es lo que corre cuando no hubo especulacion aprovechable."""
    ai_msg = await _generar_del_especialista(state, agent, llm, tools)
    return await _interpretar_salida_del_especialista(state, agent, ai_msg)


async def _generar_del_especialista(
    state: BotState, agent, llm, tools: list,
    run_name: str = "generate-response",
) -> AIMessage:
    """SOLO la llamada al LLM del especialista: bindea tools, arma el prompt e
    invoca. Devuelve el AIMessage crudo, sin interpretarlo.

    Esta mitad esta separada de la interpretacion para que la ejecucion
    especulativa del ruteo (docs/PENDIENTES.md 29.a) pueda dispararla sola,
    en paralelo con el ruteo, y descartar el resultado si el ruteo eligio otro
    especialista. Es un refactor mecanico: el codigo no cambio de
    comportamiento al partirse.

    NO lee `state["active_agent"]` -- ni aca ni en `build_system_prompt` ni en
    ningun `bloque_*` (verificado por grep el 2026-09-08). Eso es lo que hace
    valida la especulacion: el prompt que arma es identico se llame antes o
    despues del ruteo, asi que la llamada especulativa es la MISMA que se iba a
    hacer igual. Si algun bloque pasara a leer `active_agent`, la premisa de
    29.a se cae y hay que revisarla.
    """
    # `responder` se bindea junto a las tools de negocio: es el canal de salida
    # del especialista, no una accion (ver bot/flow/respuesta.py). Va aca y no
    # en agent.business_actions() a proposito -- business_action_node arma su
    # ToolNode desde business_actions(), y `responder` nunca debe ejecutarse
    # ahi: lo intercepta este nodo.
    llm_con_tools = llm.bind_tools([*tools, responder])
    effective_prompt = await sync_to_async(agent.effective_prompt)()
    global_prompt = await sync_to_async(get_effective_global_prompt)()
    system_prompt = agent.build_system_prompt(state, f"{global_prompt}\n\n---\n\n{effective_prompt}")
    mensajes = _construir_mensajes(state, system_prompt)

    return await _ainvoke_tools_json_with_retry(
        llm_con_tools, mensajes, label=f"specialist:{agent.name}",
        run_name=run_name, tags=[f"agent:{agent.name}"],
        nombre_agente=agent.name,
    )


async def _interpretar_salida_del_especialista(
    state: BotState, agent, ai_msg: AIMessage,
) -> dict:
    """El AIMessage del especialista -> update del state. Sin I/O de LLM.

    La otra mitad de `_specialist_node_con_tools`. Se la llama con el AIMessage
    recien generado o con el que dejo la especulacion del ruteo (29.a): en los
    dos casos se interpreta con el state POST-ruteo, que es el que tiene el
    `active_agent` definitivo."""
    acciones = tool_calls_de_negocio(ai_msg)
    if acciones:
        # Si pidio `responder` en la MISMA respuesta que una accion, la
        # respuesta se descarta y la accion gana: contestarle al cliente antes
        # de ejecutar es exactamente el "prometer sin ejecutar" que los prompts
        # de los especialistas prohiben. El texto se vuelve a redactar en la
        # vuelta siguiente, ya con el resultado real de la accion.
        if len(acciones) != len(ai_msg.tool_calls or []):
            logger.info(
                "[graph] %s pidio responder junto con %s accion(es); primero la accion",
                agent.name, len(acciones),
            )
        nueva_llamada = AIMessage(content="", tool_calls=acciones)
        return {
            "active_agent": state["active_agent"],
            "tool_messages": [nueva_llamada],
            "_dispatch": "business_action",
        }

    # Orden: tool `responder` (explicito) -> JSON viejo (compatibilidad) ->
    # PROSA (el camino principal desde el 2026-09-03).
    parsed = respuesta_de_tool_calls(ai_msg, agent.name)
    if parsed is None:
        # Prosa: no se reescribe ni se reintenta. Se manda tal cual y los
        # metadatos los saca el extractor en la cola de envio, fuera de la
        # latencia que el cliente siente. `metadatos_pendientes` es la senal
        # que handlers.py necesita para encolar esa extraccion.
        #
        # Los campos de estado se devuelven con su valor PREVIO (no None): el
        # bloque post-grafo de handlers.py hace `result.get(x)` y un None
        # borraria lo que el turno anterior ya sabia. El extractor los va a
        # actualizar despues, si tiene evidencia.
        texto = _texto_de_respuesta(ai_msg.content).strip()
        if prosa_utilizable(texto):
            # Los ids de sucursal se resuelven ACA y no en el extractor, a
            # proposito: salen de los RESULTADOS de las tools de este turno, no
            # de lo que el LLM haya escrito, asi que corresponde resolverlos de
            # forma deterministica y no pedirselos a un modelo. Son dos
            # funciones locales sin I/O de red: no agregan latencia percibida.
            #
            # REGRESION REAL que esto arregla (2026-09-03, reportada por otra
            # sesion y reproducida contra el LLM real): sin esto el camino de
            # prosa retornaba sin el campo, handlers.py recibia una lista vacia
            # y el pin de ubicacion de WhatsApp NO se enviaba NUNCA.
            # `buscar_sucursales_cercanas` se llamaba 3/3 veces y el campo
            # salia None las tres.
            candidatos = _extraer_sucursal_ids_de_tools(state.get("tool_messages"))
            sucursal_ids = await sync_to_async(_validar_sucursal_ids)(candidatos)
            return {
                "active_agent": state["active_agent"],
                "response_text": texto,
                "metadatos_pendientes": True,
                "mensaje_cliente": state.get("text") or "",
                "flow_data": state.get("flow_data") or {},
                "flow_state": state.get("flow_state"),
                "lead_class": state.get("lead_class"),
                "stage": state.get("stage"),
                "sucursal_direccion_ids": sucursal_ids,
                "_dispatch": "END",
            }
        # Ni tool ni prosa utilizable: recien aca vale intentar el JSON viejo,
        # y si tampoco parsea, el logger.error de _parse_json_response SI es
        # informativo -- ahi algo raro paso de verdad.
        parsed = _parse_json_response(ai_msg.content)

    flow_data = fusionar_flow_data(state.get("flow_data"), parsed.get("extracted_data"))
    flow_state = parsed.get("next_state") or state.get("flow_state")
    modelo_imagen = parsed.get("modelo_imagen") if parsed.get("intent") in INTENTS_CON_IMAGEN else None

    from bot.models import Conversation
    lead_class = _validar_choice(parsed.get("lead_class"), Conversation.LEAD_CLASS_CHOICES) or state.get("lead_class")
    stage = _validar_choice(parsed.get("stage"), Conversation.STAGE_CHOICES) or state.get("stage")
    handoff_reason = parsed.get("handoff_reason") if parsed.get("handoff") else None
    requiere_revision = bool(parsed.get("requiere_revision"))
    motivo_revision = parsed.get("motivo_revision") if requiere_revision else None
    sucursal_direccion_ids = await sync_to_async(_validar_sucursal_ids)(parsed.get("sucursal_direccion_ids"))
    if not sucursal_direccion_ids:
        candidatos = _extraer_sucursal_ids_de_tools(state.get("tool_messages"))
        sucursal_direccion_ids = await sync_to_async(_validar_sucursal_ids)(candidatos)

    if parsed.get("handoff"):
        return {
            "active_agent": None,
            "response_text": parsed.get("mensaje", "¿En qué más le puedo ayudar?"),
            "flow_data": flow_data, "flow_state": flow_state,
            "modelo_imagen": modelo_imagen,
            "intent": parsed.get("intent"),
            "lead_class": lead_class, "stage": stage,
            "handoff_reason": handoff_reason,
            "requiere_revision": requiere_revision, "motivo_revision": motivo_revision,
            "sucursal_direccion_ids": sucursal_direccion_ids,
            # Los antecedentes comerciales de este turno. Solo viajan por este
            # camino (el de `responder`): en el de prosa los saca el extractor
            # ya dentro de la cola. handlers.py los escribe con la MISMA
            # funcion en los dos casos -- ver docs/PENDIENTES.md 32.a.
            "lead": parsed.get("lead") or {},
            "_dispatch": "END",
        }
    return {
        "active_agent": state["active_agent"],
        "response_text": parsed.get("mensaje") or RESPUESTA_GENERICA_JSON_INVALIDO,
        "flow_data": flow_data, "flow_state": flow_state,
        "modelo_imagen": modelo_imagen,
        "intent": parsed.get("intent"),
        "lead_class": lead_class, "stage": stage,
        "handoff_reason": handoff_reason,
        "requiere_revision": requiere_revision, "motivo_revision": motivo_revision,
        "sucursal_direccion_ids": sucursal_direccion_ids,
        "lead": parsed.get("lead") or {},
        "_dispatch": "END",
    }


def _resultado_ok(contenido: str) -> bool:
    try:
        return json.loads(contenido).get("ok") is True
    except (json.JSONDecodeError, AttributeError):
        return False


class _ToolsSubgraphState(MessagesState):
    wa_id: str
    tool_messages: list


def _construir_subgrafo_tools(tools: list):
    """ToolNode necesita correr DENTRO de un StateGraph compilado para que
    ToolRuntime (usado por registrar_no_contactar para leer wa_id sin
    exponerlo al LLM) reciba el state -- invocado suelto, ToolNode.ainvoke
    revienta con "Missing required config key" porque esa inyeccion depende
    de una clave interna que solo la maquinaria de ejecucion del grafo
    llena (validado empiricamente antes de este cambio)."""
    builder = StateGraph(_ToolsSubgraphState)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile()


def _args_por_tool_call_id(tool_messages_previos: list) -> dict:
    """tool_call_id -> args de la llamada que lo origino. Un ToolMessage no
    carga los args de su llamada, pero el canal siempre trae tambien la
    AIMessage(tool_calls=[...]) que la pidio, correlacionada por id."""
    mapa = {}
    for m in tool_messages_previos:
        if isinstance(m, AIMessage):
            for tc in (m.tool_calls or []):
                mapa[tc["id"]] = tc["args"]
    return mapa


def _filtrar_acciones_repetidas(tool_calls: list, tool_messages_previos: list) -> tuple[list, list]:
    """Separa las tool_calls que ya se ejecutaron con exito en este turno
    (un ToolMessage previo en el canal con ese nombre, los MISMOS args y
    ok=true) de las que de verdad hay que ejecutar. Antes esto solo se
    chequeaba para una tool_call a la vez; ahora se extiende a las N que haya
    pedido el LLM en la misma respuesta.

    Los args son parte del criterio porque hay tools que legitimamente se
    llaman varias veces por turno con argumentos distintos:
    consultar_base_conocimiento existe para eso (el cliente pregunta por
    "garantia" y "sucursales" en el mismo mensaje, o el LLM reformula la
    busqueda). Deduplicando solo por nombre, la segunda consulta recibia los
    chunks de la primera reetiquetados como si fueran su respuesta, y el
    modelo contestaba con seguridad desde el contexto equivocado. Los
    reintentos de las tools de escritura (crear_lead, registrar_no_contactar,
    agendar_hora) llegan con los mismos args, asi que siguen cortandose; a
    consultar_base_conocimiento la acota su propio tope de intentos por turno
    (_MAX_INTENTOS_POR_TURNO), no este dedup."""
    args_previos = _args_por_tool_call_id(tool_messages_previos)
    ya_resueltas, pendientes = [], []
    for tc in tool_calls:
        previo_ok = next(
            (m for m in reversed(tool_messages_previos)
             if isinstance(m, ToolMessage) and m.name == tc["name"] and _resultado_ok(m.content)
             and args_previos.get(m.tool_call_id) == tc["args"]),
            None,
        )
        if previo_ok is not None:
            logger.warning("[graph] business_action %s repetida tras resultado ok=true -- corto-circuito", tc["name"])
            ya_resueltas.append(ToolMessage(content=previo_ok.content, tool_call_id=tc["id"], name=tc["name"]))
        else:
            pendientes.append(tc)
    return ya_resueltas, pendientes


def _ordenar_por_tool_call_id(mensajes: list, tool_calls_originales: list) -> list:
    orden = {tc["id"]: i for i, tc in enumerate(tool_calls_originales)}
    return sorted(mensajes, key=lambda m: orden.get(m.tool_call_id, len(orden)))


def _normalizar_contenido_string(mensajes: list) -> list:
    """ToolNode serializa un dict devuelto por una tool a JSON string, pero
    si la tool devuelve una list (ej. _consultar_disponibilidad_impl,
    _listar_catalogo_impl) la pasa tal cual como content -- ToolMessage.content
    acepta list como tipo valido, asi que no hay error, pero rompe el
    contrato de que el resto del sistema (_resultado_ok, prompts) siempre
    puede hacer json.loads(content) esperando un string. Se normaliza aca,
    en un solo lugar, en vez de forzar a cada _impl a devolver ya
    serializado (eso rompería los tests que llaman a los _impl directo)."""
    normalizados = []
    for m in mensajes:
        if isinstance(m, ToolMessage) and not isinstance(m.content, str):
            m = ToolMessage(
                content=json.dumps(m.content, default=str, ensure_ascii=False),
                tool_call_id=m.tool_call_id, name=m.name,
                status=getattr(m, "status", "success"),
            )
        normalizados.append(m)
    return normalizados


async def business_action_node(state: BotState) -> dict:
    registry = await sync_to_async(build_agent_registry)()
    if state["active_agent"] in registry:
        agent = registry[state["active_agent"]]
    else:
        fallback = "faq" if "faq" in registry else next(iter(registry))
        state = {**state, "active_agent": fallback}
        agent = registry[fallback]

    tool_messages_previos = state.get("tool_messages") or []
    # specialist_node solo anexa una AIMessage(tool_calls=[...]) cuando decide
    # ejecutar una accion, y este nodo siempre responde de inmediato (nunca
    # deja pasar un turno sin resolverla) -- asi que en cualquier visita a
    # este nodo, la ULTIMA entrada del canal es siempre esa AIMessage
    # pendiente de resolver (con 1 o mas tool_calls).
    ultimo = tool_messages_previos[-1] if tool_messages_previos else None
    tool_calls = ultimo.tool_calls if isinstance(ultimo, AIMessage) and ultimo.tool_calls else []

    ya_resueltas, pendientes = _filtrar_acciones_repetidas(tool_calls, tool_messages_previos)

    ejecutadas = []
    if pendientes:
        with get_client().start_as_current_observation(
            as_type="tool", name="run-business-action",
            input=pendientes, metadata={"business_actions": [tc["name"] for tc in pendientes]},
        ) as tool_span:
            subgrafo = _construir_subgrafo_tools(agent.business_actions())
            ai_msg_filtrada = AIMessage(content="", tool_calls=pendientes)
            resultado = await subgrafo.ainvoke({
                "messages": [ai_msg_filtrada],
                "wa_id": state.get("wa_id", ""),
                "tool_messages": tool_messages_previos,
            })
            ejecutadas = _normalizar_contenido_string(resultado["messages"][1:])  # [0] es el AIMessage de entrada, eco del framework
            tool_span.update(output=[m.content for m in ejecutadas])

    todas = _ordenar_por_tool_call_id(ya_resueltas + ejecutadas, tool_calls)
    return {
        "active_agent": state["active_agent"],
        "tool_messages": todas,
        "_dispatch": state["active_agent"],
    }


def _route_after_supervisor(state: BotState) -> str:
    return "specialist" if state.get("_dispatch") else "END"


def _route_after_specialist(state: BotState) -> str:
    return state.get("_dispatch") or "END"


def _route_after_business_action(state: BotState) -> str:
    return "specialist" if state.get("_dispatch") else "END"


def build_graph():
    graph = StateGraph(BotState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("specialist", specialist_node)
    graph.add_node("business_action", business_action_node)

    graph.add_conditional_edges("supervisor", _route_after_supervisor, {"specialist": "specialist", "END": END})
    graph.add_conditional_edges("specialist", _route_after_specialist, {"business_action": "business_action", "END": END})
    graph.add_conditional_edges("business_action", _route_after_business_action, {"specialist": "specialist", "END": END})

    graph.add_edge(START, "supervisor")
    return graph.compile()


class _GrafoConPresupuestoDeTurno:
    """Arma el vencimiento del turno alrededor de `ainvoke` y delega todo lo
    demas al grafo compilado.

    POR QUE UN ENVOLTORIO Y NO UN `.set()` DENTRO DE UN NODO: LangGraph corre
    cada nodo en su propia `asyncio.Task`, y una Task COPIA el contexto al
    crearse -- un `.set()` hecho adentro de un nodo se pierde al terminar ese
    nodo y el siguiente no lo ve (verificado contra la libreria instalada). El
    unico punto que corre en el contexto del llamador, y por lo tanto el unico
    desde donde el valor alcanza a todos los nodos, es el borde de entrada.

    Se resetea siempre en el `finally`: el presupuesto es del TURNO, no del
    proceso. Sin eso, el vencimiento de un turno se filtraria al siguiente que
    corriera en el mismo hilo y lo cortaria apenas empezado."""

    def __init__(self, grafo):
        self._grafo = grafo

    async def ainvoke(self, entrada, config=None):
        token = _vencimiento_del_turno.set(
            time.monotonic() + _PRESUPUESTO_LLM_TURNO_SEGUNDOS
        )
        try:
            return await self._grafo.ainvoke(entrada, config)
        finally:
            _vencimiento_del_turno.reset(token)

    def __getattr__(self, nombre):
        # Todo lo que no sea ainvoke (astream, get_graph, los helpers que use
        # el simulador) sigue llegando al grafo real sin intermediarios.
        return getattr(self._grafo, nombre)


_graph = None


def get_flow_graph():
    global _graph
    if _graph is None:
        _graph = _GrafoConPresupuestoDeTurno(build_graph())
    return _graph
