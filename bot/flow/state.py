from typing import Annotated, Optional, TypedDict

from langgraph.graph import add_messages


class BotState(TypedDict):
    wa_id: str
    text: str
    name: str
    messages: list                  # ventana de contexto acotada por sesión (ver context_window.py)
    flow_state: str
    flow_data: dict
    active_agent: Optional[str]
    campaign_hint: Optional[str]
    # True cuando bot/whatsapp/handlers.py ya le mando la bienvenida a este
    # contacto en ESTE turno (saludo interceptado antes del grafo). El
    # especialista tiene que continuar, no volver a saludar -- ver
    # bloque_ya_saludado en bot/flow/agents/_common.py.
    ya_saludamos: bool
    response_text: str
    # True cuando la respuesta salio como PROSA: los metadatos del CRM los saca
    # el extractor en la cola de envio, no el especialista (ver
    # bot/flow/extractor_metadatos.py y el spec del 2026-09-03). El grafo no
    # los trae en el resultado, asi que handlers.py necesita esta senal para
    # saber que faltan y encolar la extraccion.
    metadatos_pendientes: bool
    # El mensaje del cliente de este turno. Se propaga porque el extractor lo
    # necesita como contexto y corre FUERA del grafo, en la cola de envio.
    mensaje_cliente: str
    interactive_buttons: Optional[list]
    interactive_list: Optional[list]
    interactive_options: Optional[list]
    list_button_text: Optional[str]
    # Acumula los AIMessage(tool_calls=[...])/ToolMessage reales de este
    # turno via el reducer add_messages de LangGraph (concatena en vez de
    # reemplazar) -- reemplaza al slot unico pending_tool_call/
    # business_result/business_action_completed (Tasks 1/7/8), que se pisaba
    # si el LLM encadenaba 2+ tools en el mismo turno (bug real encontrado en
    # la revision final del plan: agendamiento describe explicitamente una
    # cadena de 3 acciones en su propio prompt). El dedup "no repitas una
    # accion ya completada con exito" (antes un campo aparte) ahora se deriva
    # escaneando este mismo canal -- ver business_action_node.
    tool_messages: Annotated[list, add_messages]
    # Solo el especialista "ventas" lo setea (ver custom.py::build_prompt) --
    # el resto de agentes deja este campo en None.
    modelo_imagen: Optional[str]
    # Solo el especialista "faq" lo setea (ver faq.py) -- ids de las Sucursal
    # cuya direccion se compartio en este turno (puede ser mas de una, ej.
    # al ofrecer opciones cercanas), ya validados contra la BD (existen +
    # tienen lat/lng) en _validar_sucursal_ids (graph.py). Gatea el envio de
    # un location message por cada una en handlers.py.
    sucursal_direccion_ids: Optional[list]
    # Intent crudo que devolvio el LLM del especialista en el ultimo turno
    # (seccion 11 del contrato de "ventas"). Produccion solo lo usa para
    # gatear modelo_imagen (ver specialist_node) -- se propaga en el state
    # para que el simulador de pruebas (bot/simulator/) pueda auditarlo sin
    # re-derivarlo del texto.
    intent: Optional[str]
    # Ver Conversation.LEAD_CLASS_CHOICES/STAGE_CHOICES (bot/models.py) --
    # solo el especialista "ventas" los pide hoy (build_system_prompt en
    # custom.py), specialist_node los valida contra ese whitelist antes de
    # persistir (nunca se confia en el string crudo del LLM para una
    # columna con choices).
    lead_class: Optional[str]
    stage: Optional[str]
    # Antecedentes comerciales del turno (docx S8), con los nombres de campo de
    # LeadComercial. Los llena el especialista al responder por la tool
    # `responder`; en el camino de prosa NO viaja por aca -- ahi los saca el
    # extractor ya dentro de la cola de envio. Los escribe
    # bot/business/prospeccion.py::registrar_lead_de_metadatos en los dos casos.
    # TIENE que estar declarado aca: sin esto LangGraph lo descarta del
    # state-merge y el lead se pierde sin error visible (mismo bug que el
    # comentario de _dispatch documenta mas abajo). Ver docs/PENDIENTES.md 32.a.
    lead: Optional[dict]
    # La gen#1 que `supervisor_node` adelanto en paralelo al ruteo
    # (docs/PENDIENTES.md 29.a). Es un AIMessage crudo, sin interpretar, y solo
    # se escribe cuando el ruteo CONFIRMO el especialista especulado -- una
    # especulacion equivocada se cancela y nunca llega al estado.
    # `specialist_node` la consume y la limpia.
    #
    # TIENE que estar declarado aca: sin esto LangGraph lo descarta del
    # state-merge y la especulacion se perderia sin error visible, o sea que el
    # ahorro seria cero y nadie se enteraria (mismo bug que documentan `lead` y
    # `_dispatch`).
    gen_especulativa: Optional[object]
    # Antecedentes del lead que todavia faltan, ya en texto legible
    # (bot/flow/agents/_common.py::antecedentes_que_faltan). Se calcula al
    # armar el state, en contexto sincrono: el bloque del prompt que lo usa
    # corre dentro de un nodo async y ahi una query revienta.
    lead_faltante: Optional[list]
    # Motivo libre que el LLM da junto a handoff=true -- se persiste como
    # Incident(kind="handoff"), no como columna (ver bot/models.py::registrar_incidente).
    handoff_reason: Optional[str]
    # Flag universal (todos los especialistas) para "esto necesita ojo
    # humano" -- colapsa deliberadamente los 4 sub-flags de
    # compliance_flags/qa_flags del prompt viejo en uno solo: ningun
    # consumidor distingue "legal" de "QA" hoy.
    requiere_revision: Optional[bool]
    motivo_revision: Optional[str]
    reply_to: Optional[str]
    # Routing del grafo: el valor que devuelve cada nodo aqui es lo que
    # los conditional_edges leen para decidir el siguiente nodo. Tiene que
    # estar declarado en el TypedDict o LangGraph lo descarta del state-merge
    # (ver comentario equivalente en wsp_pompeyo/bot/flow/state.py — bug ya
    # sufrido ahi, se documenta aqui para no repetirlo).
    _dispatch: Optional[str]
