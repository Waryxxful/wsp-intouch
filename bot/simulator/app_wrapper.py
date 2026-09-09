import json
import uuid

from asgiref.sync import async_to_sync
from langchain.messages import AIMessage, ToolMessage

from bot.simulator.marking import generar_wa_id_test, nombre_contacto_test
from bot.whatsapp.handlers import handle_message


def _acciones_de_tool_messages(tool_messages: list) -> list[dict]:
    """Reconstruye la lista de acciones de negocio ejecutadas en UN turno a
    partir de `tool_messages` (AIMessage(tool_calls=[...]) + ToolMessage
    reales de LangChain, acumulados por el reducer `add_messages` de
    LangGraph -- reemplaza a business_action/business_params/business_result
    de la arquitectura vieja). Empareja cada ToolMessage con la tool_call
    que lo origino por `tool_call_id`, para soportar que un mismo turno
    externo encadene mas de una accion -- algo que la arquitectura vieja no
    podia representar (un solo slot que se pisaba con la ultima)."""
    llamadas_por_id = {}
    for msg in tool_messages or []:
        if isinstance(msg, AIMessage):
            for tc in (msg.tool_calls or []):
                llamadas_por_id[tc.get("id")] = {"nombre": tc.get("name"), "params": tc.get("args") or {}}
    acciones = []
    for msg in tool_messages or []:
        if isinstance(msg, ToolMessage):
            llamada = llamadas_por_id.get(msg.tool_call_id, {})
            try:
                resultado = json.loads(msg.content)
            except (json.JSONDecodeError, TypeError):
                resultado = {}
            # No todas las implementaciones reales de una business action
            # devuelven un dict -- consultar_disponibilidad (ver
            # bot/business/__init__.py) devuelve una lista, que
            # business_action_node serializa sin cambios en el content del
            # ToolMessage. Si dejamos pasar esa lista tal cual,
            # codigo_reserva_no_inventado y _construir_transcript (que hacen
            # `accion.get("resultado") or {}` y luego `.get(...)`) crashean
            # con AttributeError porque una lista no tiene `.get`. Envolver
            # el valor no-dict preserva el dato original bajo la key
            # "resultado" para quien lo necesite, y deja que el `.get("ok")`
            # / `.get("codigo")` de los evaluadores degrade a None en vez de
            # reventar.
            if not isinstance(resultado, dict):
                resultado = {"resultado": resultado}
            acciones.append({
                "nombre": llamada.get("nombre", msg.name),
                "params": llamada.get("params", {}),
                "resultado": resultado,
            })
    # Dedup: business_action_node (bot/flow/graph.py, comentario "Dedup:"
    # cerca de la linea 467) tiene un corto-circuito que, si el LLM vuelve a
    # pedir en el MISMO turno externo una accion que ya se ejecuto con
    # exito, no la vuelve a correr -- solo re-emite un ToolMessage nuevo con
    # el MISMO content (mismo resultado) bajo otro tool_call_id. Sin este
    # paso, esa replay aparece aca como una segunda entrada identica en
    # `acciones`, y _construir_transcript la muestra como si se hubiera
    # ejecutado dos veces (ej. dos leads creados en vez de uno), lo que
    # confunde al juez LLM. Se conserva solo la primera ocurrencia de cada
    # (nombre, resultado) exactamente igual; `resultado` es un dict, asi que
    # se compara serializando a JSON con claves ordenadas.
    vistos = set()
    acciones_dedup = []
    for accion in acciones:
        clave = (accion["nombre"], json.dumps(accion["resultado"], sort_keys=True))
        if clave in vistos:
            continue
        vistos.add(clave)
        acciones_dedup.append(accion)
    return acciones_dedup


def create_app(persona: str, mock_wa):
    """Devuelve (app, capturas). `app` es el callable que espera
    `openevals.simulators.run_multiturn_simulation` (firma sincrona: usa
    `async_to_sync` internamente, nunca se llama desde un contexto async).
    `capturas` es un dict thread_id -> list[turno] que se llena turno a
    turno; cada `turno` trae `acciones` (ver `_acciones_de_tool_messages`)
    con TODAS las acciones de negocio reales ejecutadas en ese turno."""
    wa_ids: dict[str, str] = {}
    capturas: dict[str, list[dict]] = {}

    def app(inputs, *, thread_id: str, **kwargs):
        wa_id = wa_ids.setdefault(thread_id, generar_wa_id_test())
        nombre = nombre_contacto_test(persona)
        texto_cliente = inputs.get("content") if isinstance(inputs, dict) else inputs.content

        resultados_grafo = []
        textos_antes = len(mock_wa.send_text.call_args_list)
        imagenes_antes = len(mock_wa.send_image.call_args_list)

        # envio_inline=True: justo abajo se arma `bot_responde` leyendo las
        # llamadas a mock_wa.send_text de ESTE turno. Con la cola de envio en
        # background (bot/whatsapp/cola_envio.py) las partes 2..N todavia no
        # habrian salido cuando se leen, y el juez evaluaria una respuesta
        # truncada a su primer parrafo.
        async_to_sync(handle_message)(
            wa_id, nombre, texto_cliente, str(uuid.uuid4()),
            on_graph_result=resultados_grafo.append,
            envio_inline=True,
        )

        textos_nuevos = [c.args[1] for c in mock_wa.send_text.call_args_list[textos_antes:]]
        imagen_enviada = len(mock_wa.send_image.call_args_list) > imagenes_antes
        resultado = resultados_grafo[0] if resultados_grafo else {}
        acciones = _acciones_de_tool_messages(resultado.get("tool_messages"))

        # Ya no se marca ningun lead aca. En wsp_demo, "crear_lead" escribia en
        # el CRM real (BD qaintouch) y habia que marcarlo para poder limpiarlo
        # despues. Cavem usa "registrar_datos_lead", que escribe LeadComercial
        # en la BD del propio bot, colgado de la Conversation por OneToOne con
        # CASCADE: borrar la conversacion de test se lleva el lead con ella
        # (ver cleanup_test_conversations).

        # `intent` y `modelo_imagen` YA NO vienen del grafo: desde el refactor
        # de prosa (2026-09-03) los produce el extractor, despues del turno
        # (bot/flow/extractor_metadatos.py). Leerlos solo del resultado dejaba
        # `intent=None` en TODOS los turnos, y con eso el evaluador
        # `imagen_solo_con_intent_correcto` marcaba violacion falsa en cada foto
        # enviada -- o sea que el chequeo dejo de chequear. Es la misma familia
        # que el mock.patch que dejo de interceptar tras un refactor.
        #
        # El grafo primero y el extractor como respaldo: el camino JSON viejo
        # todavia los pone en el resultado, y ahi son los suyos.
        from bot.whatsapp.cola_envio import ultima_extraccion
        extraidos = ultima_extraccion(wa_id)

        turno = {
            "cliente_dice": texto_cliente,
            "bot_responde": "\n\n".join(textos_nuevos),
            "intent": resultado.get("intent") or extraidos.get("intent"),
            "active_agent": resultado.get("active_agent"),
            "acciones": acciones,
            "modelo_imagen": resultado.get("modelo_imagen") or extraidos.get("modelo_imagen"),
            "imagen_enviada": imagen_enviada,
            "flow_data": resultado.get("flow_data") or {},
        }
        capturas.setdefault(thread_id, []).append(turno)

        return {"role": "assistant", "content": turno["bot_responde"]}

    return app, capturas
