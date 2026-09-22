"""El contrato de salida del especialista, como TOOL nativa en vez de un JSON
en texto.

POR QUE EXISTE ESTE MODULO (auditoria de latencia 2026-09-03, medido sobre 44
turnos reales de produccion en Langfuse):

El especialista tenia tools nativas bindeadas Y un contrato en el prompt que le
exigia "Responde SIEMPRE con este JSON exacto". Son dos protocolos
incompatibles: un modelo con tools esta entrenado para escribir prosa cuando NO
llama a una tool, asi que escribia la respuesta en prosa, la validacion la
rechazaba, y `_ainvoke_tools_json_with_retry` gastaba una llamada COMPLETA al
LLM en reescribir el mismo texto envuelto en JSON.

La evidencia: de los 17 pares de `generate-response` consecutivas encontrados en
14 trazas, los 17 eran ese reintento (identificados por el recordatorio literal
que solo inyecta el reintento). Incidencia 1,09 reintentos por turno, presentes
en el 95% de los turnos: 3,78s de media, el 27% del turno. Turnos con reintento
14,20s contra 7,30s los que no lo pagaban. En un turno real medido, el intento 0
escribio "¡Perfecto, Felipe! ✅ Dejamos anotado que un ejecutivo..." en prosa y
el reintento escribio EXACTAMENTE el mismo texto dentro de {"mensaje": ...},
gastando 4,12s en reformatear algo que ya estaba escrito.

La solucion no es un mejor prompt -- es cambiar el canal. Con `responder` como
tool, el modelo deja de elegir entre "prosa" y "JSON" y elige entre tools, que
es lo que sabe hacer. Medido contra el LLM real sobre la llamada post-tool (el
punto exacto donde caia el reintento): el contrato viejo fallo 2 de 8 casos por
formato, el contrato nuevo 0 de 8.

La alternativa obvia -- `response_format: {"type": "json_schema"}` -- esta
descartada, y no por gusto: combinarla con `tools` suprime el tool-calling. Ya
estaba medido en este repo (mas lento: 4,37s contra 2,43s) y ademas es una
incompatibilidad documentada aguas arriba, ver
https://github.com/OpenRouterTeam/ai-sdk-provider/issues/411 -- el modelo recibe
"responde estricto en este schema" Y "aca tenes tools", y resuelve el conflicto
tirando los argumentos de la tool como texto en `content`.

COMPATIBILIDAD: el camino viejo (JSON en `.content`) sigue vivo como fallback en
`bot/flow/graph.py::_specialist_node_con_tools`. Un especialista custom creado
desde el panel cuyo prompt en BD pida JSON a mano sigue funcionando, y los tests
que simulan el LLM devolviendo JSON en `content` siguen siendo validos.
"""

import logging

from langchain_core.tools import tool

from .flow_data import normalizar_flow_data

logger = logging.getLogger(__name__)

NOMBRE_TOOL_RESPUESTA = "responder"

# Los campos que TODO especialista puede usar. El resto se declara por
# especialista (ver campos_de) para no ensanchar el contrato de nadie: hoy solo
# "ventas" clasifica leads y manda fotos de modelos, y solo "faq" ecoa ids de
# sucursal. Un campo que llega desde un especialista que no lo declara se
# descarta en silencio -- misma politica que `_validar_choice`/
# `_validar_sucursal_ids` ya aplicaban a la salida del LLM.
CAMPOS_BASE = frozenset({
    "mensaje", "extracted_data", "next_state",
    "handoff", "handoff_reason", "requiere_revision", "motivo_revision",
})

# Extras por slug de especialista. Un campo que llega desde un especialista que
# no lo declara se descarta en silencio.
#
# `comercial` es el único especialista de este bot (spec §2.2). Declara `lead`
# porque es quien califica oportunidades, y `lead` entra por los dos caminos de
# salida: lo llena el especialista si responde por `responder`, y el extractor
# si responde en prosa (que es el camino normal desde el refactor de prosa).
#
# Las entradas de `ventas` y `faq` se conservan para los especialistas
# heredados desregistrados: sus tests siguen corriendo y este módulo genera a
# la vez el bloque de prompt y el filtro de campos, así que borrarlas
# rompería la suite sin ganar nada.
CAMPOS_EXTRA_POR_AGENTE = {
    "comercial": frozenset({"intent", "stage", "lead"}),
    "ventas": frozenset({"intent", "modelo_imagen", "lead_class", "stage", "lead"}),
    "faq": frozenset({"sucursal_direccion_ids"}),
}


def campos_de(nombre_agente: str) -> frozenset:
    return CAMPOS_BASE | CAMPOS_EXTRA_POR_AGENTE.get(nombre_agente, frozenset())


# EL DOCSTRING DE ABAJO ES CORPUS, no documentación: `responder` se bindea a
# TODOS los especialistas (graph.py::_specialist_node_con_tools), así que su
# texto y la descripción de cada argumento entran en el payload de cada turno.
# Está escrito para el vertical de ESTE bot (hallazgo I1 de la review final de
# rama): hasta el 2026-09-09 documentaba `vehiculo_interes`, `presupuesto`,
# `cuota_objetivo` y un enum de `intent` automotriz -- campos que no existen en
# el contrato de InTouch -- y encima sin una sola tilde y con voseo, que es
# exactamente el corpus del que el modelo copia su registro (biblia §III.3
# ley 5).
#
# Se mantiene UN solo docstring y no uno por especialista a propósito: el
# esquema de la tool se construye una vez al importar el módulo, y filtrarlo
# por especialista significaría recortar el `args_schema` en cada bind -- un
# cambio en el payload real que se le manda al LLM, sin forma de medirlo contra
# el modelo en esta sesión. Los tres campos que sólo usan los especialistas
# heredados desregistrados (`modelo_imagen`, `lead_class`,
# `sucursal_direccion_ids`) siguen en el esquema porque su cobertura los exige,
# pero su descripción ya no nombra el negocio de otro bot y declara su propia
# precondición. Lo que queda sin resolver es el NOMBRE de
# `sucursal_direccion_ids`, que sólo se arregla recortando el esquema.
@tool(parse_docstring=True)
async def responder(
    mensaje: str,
    extracted_data: dict | None = None,
    next_state: str = "",
    handoff: bool = False,
    handoff_reason: str = "",
    requiere_revision: bool = False,
    motivo_revision: str = "",
    intent: str = "",
    modelo_imagen: str = "",
    lead_class: str = "",
    stage: str = "",
    sucursal_direccion_ids: list[int] | None = None,
    lead: dict | None = None,
) -> dict:
    """Entrega tu respuesta final al contacto. Es el ÚNICO canal de salida: todo
    lo que quieras que el contacto lea va en el argumento `mensaje` de esta
    herramienta, nunca como texto suelto.

    Llámala recién cuando no necesites ninguna otra herramienta en este turno:
    si primero tienes que consultar o registrar algo, llama a esa herramienta y
    responde en la vuelta siguiente, ya con el resultado.

    Args:
        mensaje: el texto exacto que se le manda al contacto por WhatsApp
        extracted_data: datos nuevos del contacto que convenga recordar, como pares clave/valor (ej. {"empresa": "Acme SpA"})
        next_state: estado del flujo para el próximo turno, vacío si no aplica
        handoff: true si esta conversación tiene que pasar a una persona ahora
        handoff_reason: por qué se deriva, obligatorio si handoff es true
        requiere_revision: true si una persona debería revisar este turno (riesgo de seguridad, reclamo grave, solicitud legal sobre datos personales)
        motivo_revision: por qué necesita revisión, obligatorio si requiere_revision es true
        intent: informarse, diagnosticar, cotizar, agendar_reunion, soporte, otro_asunto, cortesia o handoff
        lead_class: HOT, WARM o COLD según qué tan cerca de concretar está el contacto
        stage: nuevo, descubrimiento, diagnostico, recomendacion, calificacion, siguiente_paso, handoff o cerrado
        modelo_imagen: slug del elemento cuya imagen conviene mandar en este turno, vacío si ninguna
        sucursal_direccion_ids: sólo aplica si compartes direcciones de sedes físicas; si no es tu caso, déjalo vacío
        lead: antecedentes que el contacto HAYA DICHO, para que el equipo comercial retome el caso: nombre_completo, correo, empresa, industria, cargo, pais_ciudad, situacion_contact_center, tipo_contact_center, usa_ia_actualmente, canales_actuales, volumen_interacciones, necesidad_principal, soluciones_interes, intencion, plazo_proyecto, preferencia_horaria, solicita_consultoria, solicita_contacto_humano, resumen_conversacion, siguiente_accion_recomendada. Nada de deducir ni estimar: un campo vacío se puede preguntar después, uno inventado se le entrega al ejecutivo como si fuera cierto.
    """
    # Esta funcion no se ejecuta en el camino normal: `_specialist_node_con_tools`
    # intercepta el tool_call de `responder` y lo trata como la respuesta final,
    # sin pasarlo por el ToolNode. El cuerpo existe por si acaso -- y porque un
    # @tool necesita uno -- y es deliberadamente inofensivo.
    return {"ok": True}


def _texto_limpio(valor) -> str:
    return valor.strip() if isinstance(valor, str) else ""


def respuesta_de_tool_calls(ai_msg, nombre_agente: str) -> dict | None:
    """Traduce el tool_call de `responder` al mismo shape de dict que
    `_parse_json_response` devolvia, para que el resto de
    `_specialist_node_con_tools` no cambie.

    Devuelve None si el LLM no llamo a `responder`, o si lo llamo sin un
    `mensaje` util -- en los dos casos el llamador tiene que seguir con su
    camino de reintento/fallback en vez de mandarle un mensaje vacio al cliente.
    """
    permitidos = campos_de(nombre_agente)
    for tc in (getattr(ai_msg, "tool_calls", None) or []):
        if tc.get("name") != NOMBRE_TOOL_RESPUESTA:
            continue
        args = tc.get("args")
        if not isinstance(args, dict):
            logger.warning("[respuesta] tool_call de responder con args no-dict: %r", args)
            continue
        mensaje = _texto_limpio(args.get("mensaje"))
        if not mensaje:
            logger.warning("[respuesta] responder llamado sin mensaje util, se trata como invalido")
            continue
        # Los strings vacios que el modelo manda por los defaults del schema se
        # normalizan a None: el resto del grafo distingue "el LLM no dijo nada de
        # este campo" (None -> conserva el valor previo del state) de "el LLM
        # dijo algo", y un "" pisaria el valor previo con nada. Ver el uso de
        # `or state.get(...)` en _specialist_node_con_tools.
        parsed = {"mensaje": mensaje}
        for campo in ("next_state", "handoff_reason", "motivo_revision",
                      "intent", "modelo_imagen", "lead_class", "stage"):
            if campo not in permitidos:
                continue
            valor = _texto_limpio(args.get(campo))
            parsed[campo] = valor or None
        if "extracted_data" in permitidos:
            # Mismo motivo que en bot/flow/extractor_metadatos.py: se canoniza
            # ANTES del `{**flow_data, **extracted_data}` que hace
            # _specialist_node_con_tools, para que el dato nuevo pise al viejo
            # del mismo concepto en vez de sumarle una clave sinonima.
            parsed["extracted_data"] = normalizar_flow_data(args.get("extracted_data"))
        for campo in ("handoff", "requiere_revision"):
            if campo in permitidos:
                parsed[campo] = bool(args.get(campo))
        if "lead" in permitidos:
            # Crudo, igual que en el extractor: la limpieza y las validaciones
            # las hace el que escribe (registrar_lead_de_metadatos), asi los
            # dos caminos de salida tienen las mismas garantias.
            crudo = args.get("lead")
            parsed["lead"] = crudo if isinstance(crudo, dict) else {}
        if "sucursal_direccion_ids" in permitidos:
            ids = args.get("sucursal_direccion_ids")
            parsed["sucursal_direccion_ids"] = ids if isinstance(ids, list) else None
        return parsed
    return None


def tool_calls_de_negocio(ai_msg) -> list:
    """Las tool_calls que SI hay que ejecutar: todas menos `responder`.

    Importa mas de lo que parece: `business_action_node` arma su subgrafo con
    `agent.business_actions()`, que no incluye `responder`, asi que un tool_call
    de `responder` que se colara ahi haria reventar al ToolNode con una tool
    inexistente.
    """
    return [tc for tc in (getattr(ai_msg, "tool_calls", None) or [])
            if tc.get("name") != NOMBRE_TOOL_RESPUESTA]


# Recordatorio que va como ULTIMO mensaje de la ventana, no dentro del system
# prompt. La posicion es el punto: con el contrato solo en el system prompt (a
# ~10.000 caracteres de distancia del final) el modelo entregaba el 25% de sus
# respuestas como prosa suelta y cada una costaba un reintento completo;
# repitiendolo aca al final baja al 10%. Medido contra el LLM real, mismo grafo
# y mismos 8 turnos con historial acumulado, A/B dentro de la misma corrida:
# 24 llamadas al especialista / 6 en prosa sin esto, contra 20 / 2 con esto.
#
# De paso descarto la hipotesis con la que empece: NO es que el modelo evite
# poner texto largo en un argumento de tool. En esa medicion el mensaje tenia
# la misma longitud por los dos canales (mediana 310 caracteres via `responder`
# contra 296 en prosa), asi que lo que fallaba era el recuerdo de la
# instruccion, no su contenido.
#
# Va como SystemMessage real en la lista de mensajes, no aplanado como texto
# dentro de otro prompt -- aplanar una linea "system: ..." dentro de un prompt
# es lo que OpenRouter bloqueo con 403 "prompt injection" y dejo al bot mudo
# pasados 20 mensajes (ver supervisor_node en bot/flow/graph.py).
RECORDATORIO_RESPUESTA = (
    "Recordatorio para este turno: responde en texto normal, como le "
    "escribirías al cliente por WhatsApp. Si necesitas ejecutar una acción, "
    "llama a la herramienta correspondiente en vez de responder."
)


# Deliberadamente corto. El hallazgo que motiva todo este refactor es que el
# modelo repite nuestras instrucciones dentro de su respuesta al cliente (45 de
# 84 llamadas medidas), asi que cada linea de mas en este bloque es superficie
# de contaminacion. No dice "nada de JSON" ni "no repitas estas instrucciones":
# nombrar un formato para prohibirlo lo mete igual en el contexto, y prohibir
# repetir instrucciones es en si misma una instruccion repetible.
#
# ESTE ES EL HEREDADO y queda intacto: lo comparten los especialistas
# desregistrados del dominio automotriz, cuya cobertura prueba defensas reales
# de este stack. Los ejemplos de accion que enumera son los de ESE bot.
_BLOQUE_PROSA = """

## RESPUESTA
Responde al cliente en texto normal, como le escribirías por WhatsApp.

Si en este turno necesitas ejecutar una acción (buscar en el stock, simular un
financiamiento, agendar, registrar algo), llama a la herramienta que
corresponda y responde recién en la vuelta siguiente, cuando ya tengas el
resultado."""


# La variante de este bot. Existe porque el bloque de arriba se le pegaba al
# prompt de `comercial` EN CADA TURNO ofreciéndole tres acciones que ninguna
# tool suya puede hacer -- "buscar en el stock", "simular un financiamiento",
# "agendar" -- y el spec §6.2 las lista explícitamente fuera del binding
# (hallazgo I2 de la review final de rama). Los ejemplos que enumera acá son
# los de las tools que `comercial` sí tiene bindeadas
# (bot/flow/agents/comercial.py::business_actions), sin nombrarlas: nombrar la
# tool en el prompt es lo que ya cría el falso positivo de `responder` en el
# doctor, y el docstring de cada tool lleva su propio contrato.
_BLOQUE_PROSA_COMERCIAL = """

## RESPUESTA
Responde al contacto en texto normal, como le escribirías por WhatsApp.

Si en este turno necesitas ejecutar una acción (consultar el catálogo de
soluciones, buscar en la base de conocimiento, derivar un caso, registrar algo
de compliance), llama a la herramienta que corresponda y responde recién en la
vuelta siguiente, cuando ya tengas el resultado."""

# Bloque de prosa por especialista. Un slug sin entrada acá se queda con el
# heredado: es lo que corresponde para los desregistrados y para cualquier
# CustomSpecialist creado desde el panel, que no tiene tools propias.
_BLOQUES_PROSA_POR_AGENTE = {
    "comercial": _BLOQUE_PROSA_COMERCIAL,
}


def bloque_contrato_respuesta(nombre_agente: str, modo: str = "prosa") -> str:
    """El bloque "## RESPUESTA" del system prompt.

    `modo="prosa"` (el default desde el 2026-09-03) pide texto normal: es el
    canal natural del modelo y ya no hay contrato que fallar. Los metadatos los
    saca bot/flow/extractor_metadatos.py despues del envio, asi que este bloque
    NO menciona ningun campo -- pedirlos era justo lo que hacia que el modelo
    restateara instrucciones dentro de su respuesta al cliente (45 de 84
    llamadas medidas en Langfuse).

    `modo="tool"` conserva el contrato de `responder` para compatibilidad
    (spec R6): un CustomSpecialist creado desde el panel cuyo prompt en BD lo
    pida a mano sigue funcionando.

    En modo prosa el bloque es POR ESPECIALISTA
    (`_BLOQUES_PROSA_POR_AGENTE`): sus ejemplos de accion tienen que ser cosas
    que las tools de ESE especialista puedan hacer. Ofrecerle "buscar en el
    stock" a un bot B2B que no tiene stock es prometerle al modelo una
    capacidad inexistente en cada turno.

    El texto del bloque va CON tildes y sin voseo a proposito: todo lo que el
    modelo lee es corpus del que imita su registro, y un corpus sin tildes le
    ensena a escribir sin tildes -- hallazgo de la sesion del 2026-09-03, mismo
    origen que el "1 ano" que le llego a un cliente real.
    """
    if modo == "prosa":
        return _BLOQUES_PROSA_POR_AGENTE.get(nombre_agente, _BLOQUE_PROSA)
    permitidos = campos_de(nombre_agente)
    lineas = [
        "\n\n## RESPUESTA",
        "Para responderle al cliente llama SIEMPRE a la herramienta `responder`.",
        "Nunca escribas el mensaje como texto suelto ni como JSON: el unico canal",
        "de salida es esa herramienta. Si en este turno primero necesitas ejecutar",
        "otra accion, llamala a ella y usa `responder` recien en la vuelta",
        "siguiente, cuando ya tengas el resultado.",
    ]
    if "intent" in permitidos:
        lineas.append(
            "Completa tambien `intent`, `lead_class` y `stage` en cada respuesta, y "
            "`modelo_imagen` solo cuando corresponda mandar la foto de un modelo."
        )
    if "sucursal_direccion_ids" in permitidos:
        lineas.append(
            "Si en tu mensaje compartis la direccion de una o mas sucursales, pasa "
            "todos sus ids en `sucursal_direccion_ids`."
        )
    return "\n".join(lineas)
