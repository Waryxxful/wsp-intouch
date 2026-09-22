"""Saca los metadatos del CRM de la prosa que ya escribió el especialista.

POR QUÉ EXISTE (auditoría de latencia 2026-09-03, spec completo en
docs/superpowers/specs/2026-09-03-canal-de-salida-prosa-natural-design.md):

El especialista tenía que entregar su respuesta llamando a la tool `responder`,
y cuando escribía prosa suelta se gastaba una llamada COMPLETA al LLM en
reintentarlo — 24% de las llamadas de respuesta de `ventas`, y hasta 64% cuando
el resultado de la tool venía bueno. La causa raíz: las tools existen para
ejecutar acciones, y responder no es una acción; un modelo con tools está
entrenado para contestar en prosa después de recibir el resultado de una.

Ahora la prosa ES la respuesta y se le manda al cliente de inmediato. Los
metadatos los saca este módulo, con un modelo chico, y corre en el thread de
bot/whatsapp/cola_envio.py — o sea FUERA de la latencia que el cliente siente.

Detalle que importa y que es la razón de que esto sea un módulo aparte: acá SÍ
se puede usar `response_format: json_schema`, porque esta llamada NO tiene
tools. Combinar los dos suprime el tool-calling (incompatibilidad documentada
aguas arriba, OpenRouterTeam/ai-sdk-provider#411), que es justo por lo que el
especialista no puede usarlo.
"""

import asyncio
import json
import logging
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain.messages import HumanMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter
from langfuse.langchain import CallbackHandler

from .flow_data import normalizar_flow_data
from .respuesta import campos_de

logger = logging.getLogger(__name__)

# Tope propio, aplicado con asyncio.wait_for igual que en bot/flow/graph.py:
# ChatOpenRouter no respeta su `request_timeout` (verificado el 2026-09-03 con
# 3, 3000 y 30000: ninguno cortó).
#
# 25s y no los 15s originales. Medido el 2026-09-07 sobre 84 llamadas reales de
# esta misma variante (docs/PENDIENTES.md 33): la distribución es una cola
# derecha continua, sin cluster de cuelgues, y la cobertura de UN intento es
#
#     tope 15s -> 84,5%    tope 20s -> 92,9%    tope 25s -> 96,4%
#
# o sea que con 15s el 15,5% de los turnos quedaba sin evaluar `handoff` ni
# `requiere_revision`. La ventana de medición generó a ~35 tok/s contra los
# 42-85 tok/s de producción, así que esos porcentajes son pesimistas.
_TIMEOUT_SEGUNDOS = 25

# Techo para TODOS los intentos juntos, y la razón de que exista es el thread:
# `bot/whatsapp/cola_envio.py::_EJECUTOR` es un ThreadPoolExecutor con
# **max_workers=1**, así que esta llamada no retiene "un" thread de la cola --
# retiene el ÚNICO, y las partes 2..N de los mensajes de todos los demás
# contactos esperan detrás (bloqueo de cabeza de línea). El tope por intento no
# alcanza como única defensa: 3 x 25s serían 75s de cola parada.
#
# 35s deja entrar un intento completo de 25s más un reintento corto, y de ahí
# sale una propiedad deliberada: un fallo RÁPIDO (429 a los 0,5s) deja 34s de
# presupuesto y se reintenta de verdad, mientras que un timeout de 25s deja
# ~8,5s y el reintento se corta al entrar. Es exactamente el reparto que quiere
# la medición -- reintentar un timeout es mal negocio, ver el comentario de
# _invocar_con_reintento -- y sale del presupuesto, no de un caso especial.
_PRESUPUESTO_TOTAL_SEGUNDOS = 35

# Dos reintentos, igual que los tres wrappers de bot/flow/graph.py. En la
# práctica el presupuesto de arriba deja usar los dos solo cuando los fallos son
# rápidos, que es cuando sirven.
_MAX_REINTENTOS = 2

# Los antecedentes del contrato del prompt §8, con los MISMOS nombres de campo
# que escribe bot/business/lead_intouch.py::CAMPOS_ESCRIBIBLES: un nombre
# distinto acá no da error, el campo simplemente no se escribe y el dato se
# pierde en silencio. Hay un test que ancla la correspondencia.
#
# `lead_score` NO se le pide al modelo (spec §7.3): lo calcula
# `calcular_score_intouch` desde las señales de abajo. Pedírselo además sería
# tener dos escritores del mismo dato, y el del modelo no es reproducible.
#
# El teléfono tampoco: llega de los metadatos de WhatsApp y el prompt prohíbe
# pedírselo al contacto.
#
# Los enums existen por la misma razón que en el bot anterior: en texto libre
# el modelo chico escribe su propio vocabulario, y esas columnas las muestra el
# panel. `volumen_interacciones` sí es texto libre a propósito -- el prompt §8
# pide conservar período y unidad tal como los dijo el contacto.
_SENALES_PROPIEDADES = {
    "encaje_con_oferta": {"type": "boolean"},
    "necesidad_concreta": {"type": "boolean"},
    "interes_evaluar": {"type": "boolean"},
    "solicita_siguiente_paso": {"type": "boolean"},
    "intencion_avanzar_declarada": {"type": "boolean"},
    "plazo_cercano_declarado": {"type": "boolean"},
    "interes_exploratorio": {"type": "boolean"},
}

LEAD_PROPIEDADES = {
    "nombre_completo": {"type": "string"},
    "correo": {"type": "string"},
    "empresa": {"type": "string"},
    "industria": {"type": "string"},
    "subtipo_automotriz": {"type": "string", "enum": [
        "importador", "concesionario", "automotora", "servicio_tecnico",
        "rent_a_car", "financiera", "otro", ""]},
    "cargo": {"type": "string"},
    "pais_ciudad": {"type": "string"},
    "situacion_contact_center": {"type": "string", "enum": ["tiene", "no_tiene", ""]},
    "tipo_contact_center": {"type": "string", "enum": [
        "propio", "externalizado", "mixto", "no_tiene", ""]},
    # TRES ESTADOS, NO DOS: `sí`, `no` y "no tengo evidencia" (cadena vacía).
    # Eran `boolean` y eso costó datos reales: `strict: true` obliga al modelo a
    # mandar la clave en todos los turnos, el prompt le decía que mandara
    # `false` en lo que no se habló, y `False` no lo atrapa el centinela de
    # vacío de bot/business/lead_intouch.py -- así que un
    # `solicita_contacto_humano` capturado en el turno N volvía a `False` en el
    # N+1. Se perdía justamente la señal de que el contacto pidió hablar con una
    # persona. Y `usa_ia_actualmente` es `null=True` en la BD para distinguir
    # "no se sabe" de "no usa": con un booleano puro el NULL era inalcanzable y
    # el panel le mostraba "no usa IA" al ejecutivo sobre una empresa a la que
    # nadie le preguntó.
    "usa_ia_actualmente": {"type": "string", "enum": ["si", "no", ""]},
    "canales_actuales": {"type": "array", "items": {"type": "string"}},
    "volumen_interacciones": {"type": "string"},
    "necesidad_principal": {"type": "string"},
    "soluciones_interes": {"type": "array", "items": {"type": "string"}},
    "intencion": {"type": "string"},
    "plazo_proyecto": {"type": "string"},
    # Día u horario que el contacto dijo preferir. NO es una hora agendada: no
    # hay agenda integrada y el prompt prohíbe comprometer una. Se guarda para
    # que el ejecutivo llame cuando al contacto le sirve.
    "preferencia_horaria": {"type": "string"},
    # Tres estados por lo mismo que `usa_ia_actualmente`, arriba.
    "solicita_consultoria": {"type": "string", "enum": ["si", "no", ""]},
    "solicita_contacto_humano": {"type": "string", "enum": ["si", "no", ""]},
    "resumen_conversacion": {"type": "string"},
    "siguiente_accion_recomendada": {"type": "string"},
    # Las señales que alimentan el score. Son observaciones, no un veredicto.
    "senales": {
        "type": "object",
        "additionalProperties": False,
        "properties": _SENALES_PROPIEDADES,
        "required": list(_SENALES_PROPIEDADES),
    },
}
LEAD_PROPIEDADES_WRAPPER = {
    "type": "object",
    "additionalProperties": False,
    "properties": LEAD_PROPIEDADES,
    # `strict: true` exige todas las claves; el vacio es la forma de decir "no
    # tengo evidencia de esto", igual que en el resto del schema.
    "required": list(LEAD_PROPIEDADES),
}

# `lead_class` NO está en este schema, y es a propósito: HOT/WARM/COLD es
# exactamente el veredicto que `calcular_score_intouch` produce en código desde
# las `senales`. Pedírselo además al modelo son dos escritores del mismo dato, y
# el del modelo no es reproducible -- el mismo lead salía HOT o WARM según el
# turno. `bot/flow/respuesta.py::campos_de` ya lo descartaba al escribir para
# "comercial", pero se le seguía pidiendo al LLM en cada turno del bot.
#
# El lector de `lead_class` de `_metadatos_desde` sigue en pie: el especialista
# heredado "ventas" lo declara en CAMPOS_EXTRA_POR_AGENTE y su cobertura prueba
# defensas reales de este stack.
SCHEMA_METADATOS = {
    "name": "metadatos_del_turno",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {"type": "string", "enum": [
                "informarse", "diagnosticar", "cotizar", "agendar_reunion",
                "soporte", "otro_asunto", "cortesia", "handoff", ""]},
            "stage": {"type": "string", "enum": [
                "nuevo", "descubrimiento", "diagnostico", "recomendacion",
                "calificacion", "siguiente_paso", "handoff", "cerrado", ""]},
            "handoff": {"type": "boolean"},
            "handoff_reason": {"type": "string"},
            "requiere_revision": {"type": "boolean"},
            "motivo_revision": {"type": "string"},
            "next_state": {"type": "string"},
            "extracted_data": {"type": "object", "additionalProperties": True},
            "lead": LEAD_PROPIEDADES_WRAPPER,
        },
        # Todos requeridos porque `strict: true` lo exige; el valor vacío es la
        # forma de decir "no tengo evidencia de esto".
        "required": ["intent", "stage", "handoff", "handoff_reason",
                     "requiere_revision", "motivo_revision", "next_state",
                     "extracted_data", "lead"],
    },
}

# El prompt NO pide el mensaje: ese ya está escrito y es intocable (spec R1).
# Las reglas de handoff y requiere_revisión están redactadas para errar del lado
# conservador a propósito: un handoff inventado interrumpe una conversación
# sana, mientras uno que se pierde lo agarra el turno siguiente.
#
# EL HISTORIAL Y POR QUÉ ESTÁ ACÁ (docs/PENDIENTES.md 33, punto (b)):
# `resumen` y `proxima_accion` describen el CASO, no el turno, y el extractor
# veía un turno suelto. Consecuencia medida en producción el 2026-09-07: el
# turno 2 escribió "busca camioneta, tope 27M, quiere ahorrar" y el turno 5 lo
# reemplazó por "pregunta dónde ver las camionetas"; y el turno 3 escribió
# "No ha dicho presupuesto ni plazo", FALSO -- el cliente lo había dicho un
# turno antes. 4 de 35 resúmenes (11%) afirmaban cosas sobre el caso completo
# viendo un solo turno. Con el historial a la vista, 0 de 5 (n=5, medido).
#
# Cuesta ~0: el historial completo de una conversación real de 19 mensajes son
# 796 tokens de input, contra los ~790 que el extractor ya gastaba. Y no crece
# sin techo -- `build_context_window` corta en MAX_TURNS=20.
#
# EL RIESGO QUE INTRODUCE, y por eso la instrucción de abajo es explícita: con
# el historial delante, un modelo chico puede marcar `handoff` por un pedido de
# hace cinco turnos que ya se resolvió. Los campos POR TURNO se acotan al turno
# palabra por palabra; el historial es contexto solo para el resumen.
PROMPT_EXTRACTOR = """Eres un clasificador. Recibes una conversación entre una empresa interesada
y un asesor comercial, y el último turno de esa conversación. Tu única tarea es
rellenar los metadatos en el JSON pedido.

NO reescribas la respuesta. NO agregues texto. Solo clasificas lo que ya pasó.

Reglas que no se negocian:
- "handoff", "requiere_revision" e "intent" hablan SOLO de este turno: el que
  aparece abajo bajo "EL TURNO A CLASIFICAR". La conversación previa es
  contexto para el resumen, NO para estos campos: un pedido de hablar con una
  persona que ya se resolvió hace cinco turnos no es un handoff de ahora.
- "handoff" es true SOLO si la respuesta del asesor dice explícitamente que una
  persona va a tomar el caso, o si el contacto pidió hablar con alguien. Si
  tienes dudas, es false. Nunca lo inventes.
- "requiere_revision" es true si hay un reclamo grave, una amenaza de acción
  legal, o CUALQUIER pedido del contacto sobre sus datos personales (que los
  borren, que no lo contacten más, que le digan qué información tienen de él).
  Ante la duda en estos casos, márcalo en true: que una persona revise de más
  no cuesta nada, que no revise un caso legal sí.
- "extracted_data" son datos concretos del contacto que convenga recordar. Si
  no hay ninguno, un objeto vacío.
- "lead" son los antecedentes comerciales, para que un ejecutivo retome el
  caso. Llena solo los campos que el contacto HAYA DICHO en este turno, o que
  la respuesta del asesor confirme; el resto va en cadena vacía o lista vacía.
  No deduzcas ni estimes: un campo vacío se puede preguntar después, uno
  inventado se le entrega al ejecutivo como si fuera cierto.
  - No deduzcas la empresa, la industria, el cargo ni la ubicación a partir del
    correo, del número de teléfono o del nombre.
  - "correo": cópialo tal como lo escribió el contacto. No lo corrijas.
  - "volumen_interacciones": cópialo con sus palabras, conservando el período y
    la unidad que dijo ("unas 3.000 al mes", "200 llamadas diarias"). Si no dio
    período, no lo inventes.
  - "situacion_contact_center" y "tipo_contact_center": si dijo que no tiene
    Contact Center, ambos van en "no_tiene". Si tiene pero no dijo la
    modalidad, "tiene" y el tipo vacío. Si combina operación propia con una
    externalizada, el tipo es "mixto".
  - "soluciones_interes" son las soluciones que le interesan al CONTACTO. No
    confundas con las que el asesor le ofreció.
  - "usa_ia_actualmente", "solicita_consultoria" y "solicita_contacto_humano"
    tienen TRES valores y no dos, porque "no se sabe" y "no" son cosas
    distintas: "si", "no" y la cadena vacía. La cadena vacía es la única
    respuesta correcta cuando el tema no se habló en toda la conversación -- no
    pongas "no" para rellenar. Un "no" afirma que el contacto lo negó, y así se
    lo entregamos al ejecutivo.
    - "usa_ia_actualmente": "si" si dijo que ya usa alguna herramienta de IA,
      "no" si dijo que no usa ninguna, vacío si nadie lo mencionó.
    - "solicita_consultoria" y "solicita_contacto_humano": "si" SOLO si el
      contacto lo pidió o lo aceptó explícitamente; que haya entregado sus
      datos no significa que pidió una reunión. "no" sólo si lo rechazó.
      Vacío si no se habló.
  - "subtipo_automotriz": solo si la industria es automotriz y el contacto
    confirmó cuál. "otro" significa que dijo una categoría distinta de las
    seis; si no lo dijo, va vacío.
  - "resumen_conversacion" y "siguiente_accion_recomendada" son los ÚNICOS
    campos que describen el CASO COMPLETO y no este turno: úsalos con toda la
    conversación previa a la vista. El resumen es un párrafo breve para que un
    ejecutivo entienda el caso sin releer el chat: qué necesita, en qué
    contexto, qué evidencia hay de su interés, qué datos faltan y en qué
    quedaron. Distingue los hechos de las recomendaciones. No afirmes que falta
    un dato sin revisar la conversación completa: si el contacto lo dijo antes,
    ahí está.
  - "senales" son observaciones sobre el caso completo, para calificar la
    oportunidad. Cada una es true solo con evidencia en la conversación:
    - "encaje_con_oferta": lo que necesita se parece a lo que hace InTouch
      (contactabilidad, experiencia de cliente, Contact Center, automatización,
      agentes conversacionales, analítica).
    - "necesidad_concreta": describió una necesidad concreta, no solo curiosidad.
    - "interes_evaluar": quiere evaluar una solución.
    - "solicita_siguiente_paso": pidió o aceptó una reunión, una demo, una
      consultoría o que lo contacte una persona.
    - "intencion_avanzar_declarada": dijo que quiere avanzar.
    - "plazo_cercano_declarado": dio un plazo cercano explícito.
    - "interes_exploratorio": hay interés comercial pero sin necesidad concreta
      ni intención de avanzar ahora.
    No estimes presupuesto, autoridad de compra ni urgencia que no haya dicho.
- Cualquier campo del que no tengas evidencia va en cadena vacía o lista vacía.
  Los booleanos de "senales" son la excepción: ahí false significa "no observé
  esto", que es lo mismo que no tener evidencia.

Conversación previa (contexto para "resumen_conversacion",
"siguiente_accion_recomendada" y "senales"):
{historial}

=== EL TURNO A CLASIFICAR ===

Mensaje del contacto:
{mensaje_cliente}

Respuesta que el asesor ya le mandó:
{prosa}
"""

_SIN_HISTORIAL = "(no hay conversación previa: este es el primer turno)"


def formatear_historial(historial) -> str:
    """El historial de `build_context_window` como texto para el prompt.

    Se descarta el ÚLTIMO turno si coincide con el que se va a clasificar: la
    ventana lo incluye (handlers.py persiste el mensaje del cliente antes de
    invocar el grafo) y repetirlo le da al modelo chico dos copias del mismo
    texto, que es justo lo que lo confunde sobre qué es "ahora".
    """
    if not historial:
        return _SIN_HISTORIAL
    lineas = []
    for m in historial:
        if not isinstance(m, dict):
            continue
        contenido = (m.get("content") or "").strip()
        if not contenido:
            continue
        rol = {"user": "Cliente", "assistant": "Asesor"}.get(m.get("role"), "Sistema")
        lineas.append(f"{rol}: {contenido}")
    return "\n".join(lineas) if lineas else _SIN_HISTORIAL


async def _get_extractor_llm():
    """Modelo chico y barato, mismo patrón que bot/flow/graph.py::_get_routing_llm.

    `reasoning: none` porque clasificar no necesita razonar en voz alta, y el
    razonamiento es justo lo que hacía lento al ruteo antes de separarlo.
    `response_format` viaja en model_kwargs porque no es un field propio de
    ChatOpenRouter (su `model_config` es `extra="ignore"`, así que un nombre
    equivocado se descartaría sin error ni warning).
    """
    from bot.models import get_setting
    model = (await sync_to_async(get_setting)("openrouter_extractor_model_override", "")
             or settings.OPENROUTER_ROUTING_MODEL)
    api_key = (await sync_to_async(get_setting)("openrouter_api_key_override", "")
               or settings.OPENROUTER_API_KEY)
    return ChatOpenRouter(
        model=model, api_key=api_key, max_retries=0,
        reasoning={"effort": "none"},
        openrouter_provider={"sort": "latency"},
        model_kwargs={"response_format": {"type": "json_schema",
                                          "json_schema": SCHEMA_METADATOS}},
    )


def _limpio(valor) -> str | None:
    """"" -> None. El resto del sistema hace `meta.get(x) or valor_previo`, así
    que un string vacío pisaría con nada un valor que el turno anterior sí
    sabía."""
    if not isinstance(valor, str):
        return None
    return valor.strip() or None


async def _invocar_con_reintento(llm, mensajes: list):
    """`ainvoke` con tope por intento, presupuesto total y reintento.

    POR QUE EXISTE (docs/PENDIENTES.md 33): este era el ÚNICO camino LLM del
    repo con `max_retries=0` y sin wrapper propio. Los otros cuatro clientes
    ponen `max_retries=0` a propósito -- el default del SDK apila su propio
    backoff de hasta ~300s por llamada -- y encima le montan un wrapper con
    tope y logging (`graph.py::_ainvoke_with_retry` y sus dos hermanos). Acá
    faltaba la segunda mitad: un solo `wait_for` y, si se pasaba, el turno
    quedaba sin evaluar `handoff` ni `requiere_revision` sin que nada avisara
    (la racha de `cola_envio` solo salta a los 5 fallos SEGUIDOS).

    LO QUE EL REINTENTO NO ARREGLA, y conviene tenerlo escrito para no volver a
    proponerlo: reintentar un TIMEOUT es mal negocio. Medido sobre las mismas
    84 llamadas, un intento largo le gana a dos cortos en todos los
    presupuestos, incluso suponiendo fallos independientes (que no lo son):

        2 x 10s (20s) -> 87,2%   contra   1 x 20s -> 92,9%
        2 x 12s (24s) -> 94,9%   contra   1 x 24s -> 96,4%
        2 x 15s (30s) -> 97,6%   contra   1 x 30s -> 98,8%

    La razón es que un timeout nuestro no es un fallo del proveedor: la
    respuesta venía en camino y `asyncio.wait_for` corta la petición, así que
    el reintento rehace la generación entera contra la misma cola derecha. Por
    eso el arreglo de la cola es el TOPE (15s -> 25s) y el reintento queda para
    lo que sí es transitorio e independiente: 429, 5xx y errores de red, que
    fallan rápido y dejan presupuesto de sobra.

    Reusa de `graph.py` las piezas que ya resuelven problemas conocidos:
    `_ainvoke_con_tope` (el `wait_for` que convierte el corte en TimeoutError),
    `_es_error_permanente` (un 4xx que no sea 429 no se reintenta) y
    `_BACKOFF_TRANSITORIO_SEGUNDOS`. NO reusa `_tope_del_intento` ni
    `_cortar_por_presupuesto`: esos leen el contextvar `_vencimiento_del_turno`,
    que **no cruza al thread de la cola** -- verificado, devuelve None ahí -- así
    que serían código que parece hacer algo y no hace nada. Y además miden otra
    cosa: aquel presupuesto protege el `--timeout` de gunicorn del request, y
    esto corre después de que el request terminó. El techo de acá es el thread
    de envío, que es un recurso distinto.

    El import es local por la misma razón que el de `_texto_de_respuesta` más
    abajo: `graph.py` arrastra langgraph y el registro de agentes, y este módulo
    lo importa la cola de envío.
    """
    from .graph import (
        _BACKOFF_TRANSITORIO_SEGUNDOS, _ainvoke_con_tope, _es_error_permanente,
    )

    vence = time.monotonic() + _PRESUPUESTO_TOTAL_SEGUNDOS
    ultima_exc = None
    for intento in range(_MAX_REINTENTOS + 1):
        restante = vence - time.monotonic()
        if restante <= 0:
            logger.warning(
                "[extractor] presupuesto de %ss agotado antes del intento %s",
                _PRESUPUESTO_TOTAL_SEGUNDOS, intento,
            )
            break
        try:
            # CallbackHandler() fresco por intento, igual que en graph.py: uno
            # reusado entre intentos rompe el agrupamiento del trace.
            return await _ainvoke_con_tope(
                llm, mensajes,
                {"callbacks": [CallbackHandler()], "run_name": "extract-metadata"},
                min(_TIMEOUT_SEGUNDOS, restante),
            )
        except Exception as exc:
            ultima_exc = exc
            logger.warning("[extractor] intento %s fallo: %s", intento, exc)
            if _es_error_permanente(exc):
                raise
            if intento < _MAX_REINTENTOS:
                if vence - time.monotonic() <= _BACKOFF_TRANSITORIO_SEGUNDOS:
                    break
                await asyncio.sleep(_BACKOFF_TRANSITORIO_SEGUNDOS)
    raise ultima_exc if ultima_exc else TimeoutError(
        f"el presupuesto de {_PRESUPUESTO_TOTAL_SEGUNDOS}s del extractor se agoto"
    )


async def extraer_metadatos(prosa: str, mensaje_cliente: str, nombre_agente: str,
                            historial=None) -> dict:
    """Metadatos del turno, con el mismo shape que `respuesta_de_tool_calls`.

    Devuelve `{}` ante cualquier falla: el mensaje ya se le mandó al cliente,
    así que un extractor caído no puede tumbar el turno (spec R5). Los campos
    que el especialista no declara se descartan, misma política que
    bot/flow/respuesta.py.

    `historial` es la ventana de `bot/flow/context_window.py::build_context_window`
    y es OPCIONAL a propósito: sin ella el extractor sigue funcionando como
    antes (clasificando un turno suelto), así que ningún llamador queda roto.
    Con ella, `resumen` y `proxima_accion` describen el caso -- ver el
    comentario de PROMPT_EXTRACTOR. NO se construye acá: esta función es async
    y `build_context_window` consulta la BD, que desde un contexto async
    levanta SynchronousOnlyOperation (mismo motivo que
    `bloque_datos_del_lead`). La arma el llamador sincrono.
    """
    permitidos = campos_de(nombre_agente)
    prompt = PROMPT_EXTRACTOR.format(
        mensaje_cliente=mensaje_cliente, prosa=prosa,
        historial=formatear_historial(historial),
    )
    try:
        llm = await _get_extractor_llm()
        ai = await _invocar_con_reintento(
            llm,
            [SystemMessage(content=prompt),
             HumanMessage(content="Devuelve solo el JSON de metadatos.")],
        )
    except Exception:
        # error y no warning: cuando esto pasa, este turno NO evaluo handoff ni
        # requiere_revision -- la funcion mas delicada del bot (spec R3) quedo
        # sin correr y nadie se entera por el chat, que sale igual de bien. El
        # ruido no es un riesgo aca como si lo fue con _parse_json_response
        # (docs/PENDIENTES.md #28): esta llamada usa response_format json_schema
        # y sin tools, asi que fallar NO es lo normal. La racha la vigila
        # bot/whatsapp/cola_envio.py::_vigilar_racha_del_extractor, que es quien
        # tiene BD y conversacion a mano.
        logger.error("[extractor] fallo la extraccion: este turno no evaluo handoff "
                     "ni requiere_revision", exc_info=True)
        return {}

    from .graph import _texto_de_respuesta
    crudo = _texto_de_respuesta(ai.content).strip()
    try:
        datos = json.loads(crudo)
    except json.JSONDecodeError:
        # Mismo criterio de nivel que el except de arriba: el turno se quedo sin
        # handoff ni revision evaluados.
        logger.error("[extractor] respuesta no es JSON valido: %r", crudo[:200])
        return {}
    if not isinstance(datos, dict):
        logger.error("[extractor] JSON valido pero no es un objeto: %r", crudo[:200])
        return {}

    salida = {}
    for campo in ("intent", "lead_class", "stage", "next_state",
                  "handoff_reason", "motivo_revision"):
        if campo in permitidos:
            salida[campo] = _limpio(datos.get(campo))
    for campo in ("handoff", "requiere_revision"):
        if campo in permitidos:
            salida[campo] = bool(datos.get(campo))
    if "lead" in permitidos:
        # Se pasa el dict crudo: la limpieza (vacios que no pisan, montos a
        # numero, validacion de cuando_compra) la hace un solo lugar, el que
        # escribe -- bot/business/prospeccion.py::registrar_lead_de_metadatos --
        # para que los dos caminos (prosa y `responder`) tengan exactamente las
        # mismas garantias.
        crudo = datos.get("lead")
        salida["lead"] = crudo if isinstance(crudo, dict) else {}
    if "extracted_data" in permitidos:
        # Se normaliza ACA, antes de que bot/whatsapp/cola_envio.py haga
        # `{**flow_previo, **extracted_data}`. Ese orden es el punto: con la
        # clave ya canonizada, un `plazo_actual: 12` nuevo PISA al `plazo: 24`
        # viejo en vez de convivir con el (que es exactamente lo que le paso a
        # la conversacion 29). Normalizar despues del merge no alcanzaria: ahi
        # ya no se sabe cual de los dos valores es el reciente.
        salida["extracted_data"] = normalizar_flow_data(datos.get("extracted_data"))
    return salida
