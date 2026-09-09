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

# Extras por slug de especialista. Espeja exactamente lo que cada
# build_system_prompt ofrecia en su bloque "## RESPUESTA" antes de este cambio,
# para no alterar comportamiento junto con la latencia:
#   - ventas (bot/flow/agents/custom.py): intent, modelo_imagen, lead_class, stage
#   - faq (bot/flow/agents/faq.py): sucursal_direccion_ids
# "ventas" NO declara sucursal_direccion_ids a proposito: nunca lo tuvo en su
# contrato, y sus ids de sucursal se resuelven por el fallback deterministico
# `_extraer_sucursal_ids_de_tools` (graph.py), que sigue corriendo igual.
CAMPOS_EXTRA_POR_AGENTE = {
    # `lead` son los antecedentes comerciales del docx S8. Va en "ventas" y no
    # en CAMPOS_BASE porque es el unico especialista que califica leads, y
    # entra por los DOS caminos de salida: lo llena el especialista cuando
    # responde por `responder`, y el extractor cuando responde en prosa. Ver
    # docs/PENDIENTES.md 32.a.
    "ventas": frozenset({"intent", "modelo_imagen", "lead_class", "stage", "lead"}),
    "faq": frozenset({"sucursal_direccion_ids"}),
}


def campos_de(nombre_agente: str) -> frozenset:
    return CAMPOS_BASE | CAMPOS_EXTRA_POR_AGENTE.get(nombre_agente, frozenset())


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
    """Entrega tu respuesta final al cliente. Es el UNICO canal de salida: todo
    lo que quieras que el cliente lea va en el argumento `mensaje` de esta
    herramienta, nunca como texto suelto.

    Llamala recien cuando no necesites ninguna otra herramienta en este turno:
    si primero tenes que consultar o registrar algo, llama a esa herramienta y
    responde en la vuelta siguiente, ya con el resultado.

    Args:
        mensaje: el texto exacto que se le manda al cliente por WhatsApp
        extracted_data: datos nuevos del cliente que convenga recordar, como pares clave/valor (ej. {"presupuesto": 18000000})
        next_state: estado del flujo para el proximo turno, vacio si no aplica
        handoff: true si esta conversacion tiene que pasar a un humano ahora
        handoff_reason: por que se deriva, obligatorio si handoff es true
        requiere_revision: true si un humano deberia revisar este turno (riesgo de seguridad, reclamo grave, solicitud legal sobre datos personales)
        motivo_revision: por que necesita revision, obligatorio si requiere_revision es true
        intent: explorar, cotizar, financiar, test_drive, reservar, objecion, winback, handoff o cortesia
        lead_class: HOT, WARM o COLD segun que tan cerca de comprar esta el cliente
        stage: nuevo, descubrimiento, calificacion, cotizacion, simulacion, agenda, handoff, seguimiento, reclamo o cerrado
        modelo_imagen: slug del modelo cuya foto conviene mandar en este turno, vacio si ninguna
        sucursal_direccion_ids: ids de las sucursales cuya direccion estas compartiendo en este mensaje, vacio si ninguna
        lead: antecedentes comerciales que el cliente HAYA DICHO, para que el vendedor retome el caso: nombre, email, comuna, vehiculo_interes, presupuesto, pie_disponible, cuota_objetivo, cuando_compra (la fecha en que quiere comprar, NO el plazo del credito), intencion, sentimiento, urgencia, proxima_accion, resumen. Los montos van como los dijo el cliente ("15 millones", "$8.000.000"): el sistema los pasa a numero. Nada de deducir ni estimar.
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
_BLOQUE_PROSA = """

## RESPUESTA
Responde al cliente en texto normal, como le escribirías por WhatsApp.

Si en este turno necesitas ejecutar una acción (buscar en el stock, simular un
financiamiento, agendar, registrar algo), llama a la herramienta que
corresponda y responde recién en la vuelta siguiente, cuando ya tengas el
resultado."""


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

    El texto del bloque va CON tildes y sin voseo a proposito: todo lo que el
    modelo lee es corpus del que imita su registro, y un corpus sin tildes le
    ensena a escribir sin tildes -- hallazgo de la sesion del 2026-09-03, mismo
    origen que el "1 ano" que le llego a un cliente real.
    """
    if modo == "prosa":
        return _BLOQUE_PROSA
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
