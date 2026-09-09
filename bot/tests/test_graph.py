import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from django.test import TestCase, TransactionTestCase
from langchain.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from bot.flow.graph import get_flow_graph, specialist_node, business_action_node, _extraer_sucursal_ids_de_tools
from bot.flow.campaign_rules import PRE_ROUTING_RULES, CampaignRule
from bot.flow.agents.agendamiento import AgendamientoAgent
from bot.models import Conversation
from bot.rag.tool import consultar_base_conocimiento


def _ventana_sin_recordatorio(mensajes):
    """Saca el RECORDATORIO_RESPUESTA con que _construir_mensajes cierra la
    ventana (ver bot/flow/respuesta.py: va ultimo a proposito, ahi baja del 25%
    al 10% las respuestas que el modelo escribe en prosa suelta).

    Los tests de abajo miden la VENTANA DE CONVERSACION -- orden y largo de
    system/historial/mensaje actual/tool_messages -- no ese recordatorio, asi
    que se lo quita para que sus posiciones y sus conteos sigan diciendo lo que
    dicen. Que el recordatorio quede ultimo lo cubre
    bot/tests/test_respuesta.py::RecordatorioAlFinalTest.
    """
    from bot.flow.respuesta import RECORDATORIO_RESPUESTA
    assert mensajes[-1].content == RECORDATORIO_RESPUESTA, "el recordatorio dejo de ir ultimo"
    return mensajes[:-1]

class GetLlmTest(TransactionTestCase):
    # TransactionTestCase, no TestCase: _get_llm() lee el Setting via
    # sync_to_async (corre en otro thread) -- con TestCase (transaccion +
    # sqlite en memoria) el Setting creado en el thread principal produce
    # "database table is locked" al leerse desde el thread de sync_to_async.
    # Mismo patron ya usado por GraphGetLlmOverrideTest mas abajo.
    @patch("bot.flow.graph.ChatOpenRouter")
    def test_usa_el_default_de_settings_sin_override(self, mock_chat_cls):
        from django.conf import settings
        from bot.flow.graph import _get_llm
        asyncio.run(_get_llm())
        mock_chat_cls.assert_called_once_with(
            model=settings.OPENROUTER_MODEL, api_key=settings.OPENROUTER_API_KEY,
            timeout=30_000, max_retries=0, reasoning={"effort": "medium"},
            # Proveedores fijados, no `sort: latency`: ese sort ordena por
            # time-to-first-token y elegia un proveedor de 14 tokens/s (ver
            # settings.OPENROUTER_PROVIDER_ORDER y docs/PENDIENTES.md #14).
            openrouter_provider={
                "order": settings.OPENROUTER_PROVIDER_ORDER, "allow_fallbacks": True,
            },
        )

    @patch("bot.flow.graph.ChatOpenRouter")
    def test_reasoning_es_override_able_por_el_llamador(self, mock_chat_cls):
        from bot.flow.graph import _get_llm
        asyncio.run(_get_llm(reasoning={"enabled": False}))
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["reasoning"], {"enabled": False})

    @patch("bot.flow.graph.ChatOpenRouter")
    def test_el_ruteo_usa_su_propio_modelo_con_techo_y_sin_razonamiento(self, mock_chat_cls):
        # Auditoria de latencia 2026-09-02: con el modelo conversacional, el
        # ruteo medi­a mediana 2,78s y MAXIMO 62,40s (con reasoning ya
        # desactivado: la cola era del proveedor). Con el modelo de ruteo:
        # mediana 0,71s, maximo 1,07s, mismo acierto (5/5 casos).
        from django.conf import settings
        from bot.flow.graph import _get_routing_llm
        asyncio.run(_get_routing_llm())
        mock_chat_cls.assert_called_once_with(
            model=settings.OPENROUTER_ROUTING_MODEL, api_key=settings.OPENROUTER_API_KEY,
            timeout=15_000, max_retries=0,
            # max_tokens explicito: el effort de razonamiento se calcula como
            # % de max_tokens, asi que sin techo el presupuesto es el maximo
            # del modelo. Sin esto el ruteo puede razonar sin limite.
            max_tokens=settings.OPENROUTER_ROUTING_MAX_TOKENS,
            reasoning={"effort": "none"},
            openrouter_provider={"sort": "latency"},
        )

    @patch("bot.flow.graph.ChatOpenRouter")
    def test_el_ruteo_comparte_la_api_key_pero_no_el_modelo_del_bot(self, mock_chat_cls):
        # Un solo saldo de OpenRouter para todo (bot, media, scraping, ruteo),
        # modelos distintos por rol.
        from django.conf import settings
        from bot.flow.graph import _get_routing_llm
        asyncio.run(_get_routing_llm())
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["api_key"], settings.OPENROUTER_API_KEY)
        self.assertNotEqual(kwargs["model"], settings.OPENROUTER_MODEL)

    @patch("bot.flow.graph.ChatOpenRouter")
    def test_el_ruteo_respeta_su_override_de_setting(self, mock_chat_cls):
        from bot.models import Setting
        Setting.objects.create(key="openrouter_routing_model_override", value="openai/gpt-oss-20b")
        from bot.flow.graph import _get_routing_llm
        asyncio.run(_get_routing_llm())
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["model"], "openai/gpt-oss-20b")

    @patch("bot.flow.graph.ChatOpenRouter")
    def test_usa_el_override_de_setting_si_existe(self, mock_chat_cls):
        from bot.models import Setting
        Setting.objects.create(key="openrouter_model_override", value="anthropic/claude-sonnet-4.5")
        from bot.flow.graph import _get_llm
        asyncio.run(_get_llm())
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["model"], "anthropic/claude-sonnet-4.5")

    @patch("bot.flow.graph.ChatOpenRouter")
    def test_timeout_en_milisegundos_y_sin_reintento_del_sdk(self, mock_chat_cls):
        # Bug real encontrado verificando la migracion a OpenRouter: `timeout`
        # de ChatOpenRouter es en MILISEGUNDOS (no segundos, a diferencia de
        # ChatGoogleGenerativeAI) -- 30_000, no 30. max_retries=0 porque
        # _ainvoke_with_retry ya reintenta a nivel de app; el default del SDK
        # (max_retries=2) agrega hasta ~300s de backoff propio por llamada,
        # que sin esto se apilaria sin ningun freno antes del --timeout 120
        # de gunicorn -- reproduciendo el mismo incidente de Gemini colgado
        # que motivo esta migracion, solo que via OpenRouter.
        from bot.flow.graph import _get_llm
        asyncio.run(_get_llm())
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["timeout"], 30_000)
        self.assertEqual(kwargs["max_retries"], 0)

    @patch("bot.flow.graph.ChatOpenRouter")
    def test_usa_el_override_de_api_key_si_existe(self, mock_chat_cls):
        from bot.models import Setting
        Setting.objects.create(key="openrouter_api_key_override", value="sk-or-override")
        from bot.flow.graph import _get_llm
        asyncio.run(_get_llm())
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["api_key"], "sk-or-override")


class ExtraerSucursalIdsDeToolsTest(TestCase):
    def test_extrae_ids_de_un_resultado_dict_con_sucursales(self):
        tool_messages = [
            ToolMessage(content=json.dumps({"ok": True, "sucursales": [{"id": 3}, {"id": 5}]}),
                        tool_call_id="1", name="buscar_sucursales_cercanas"),
        ]
        self.assertEqual(_extraer_sucursal_ids_de_tools(tool_messages), [3, 5])

    def test_no_revienta_si_una_tool_devuelve_una_lista_en_vez_de_un_dict(self):
        # consultar_disponibilidad devuelve una lista de slots, no un dict --
        # antes de este fix, data.get("sucursales") reventaba con
        # AttributeError y tumbaba el turno completo del especialista.
        tool_messages = [
            ToolMessage(content=json.dumps([{"hora": "09:00"}, {"hora": "10:00"}]),
                        tool_call_id="1", name="consultar_disponibilidad"),
        ]
        self.assertEqual(_extraer_sucursal_ids_de_tools(tool_messages), [])

    def test_ignora_una_tool_con_lista_pero_sigue_extrayendo_de_las_demas(self):
        tool_messages = [
            ToolMessage(content=json.dumps([{"hora": "09:00"}]),
                        tool_call_id="1", name="consultar_disponibilidad"),
            ToolMessage(content=json.dumps({"ok": True, "sucursales": [{"id": 7}]}),
                        tool_call_id="2", name="buscar_sucursales_cercanas"),
        ]
        self.assertEqual(_extraer_sucursal_ids_de_tools(tool_messages), [7])


class GraphSmokeTest(TestCase):
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "quiero agendar", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    # NOTA (Task 9): AgendamientoAgent.business_actions() ahora devuelve una
    # lista de tools (ver bot/flow/agents/agendamiento.py) -- specialist_node
    # lo manda por _specialist_node_con_tools, no por el camino viejo basado
    # en _ainvoke_with_retry/JSON-de-texto con "business_action". Los tests
    # de esta clase que usan "agendamiento" como especialista de ejemplo se
    # mockean via _get_llm -> bind_tools -> ainvoke (igual que
    # SpecialistNodeConToolsTest), no via _ainvoke_with_retry.

    @patch("bot.flow.graph._get_llm")
    def test_pre_routing_deterministico_evita_llamar_al_llm(self, mock_get_llm):
        # InTouch (Task 8, spec §2.2): "comercial" es el unico especialista
        # registrado -- adaptado de "agendamiento" (heredado de Cavem, ahora en
        # AGENTES_NO_REGISTRADOS). La defensa que este test cubre (el pre-ruteo
        # deterministico evita la llamada al LLM del supervisor) no depende de
        # que slug se use como destino.
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="comercial")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "listo", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(result["active_agent"], "comercial")
        llm_con_tools.ainvoke.assert_called_once()  # solo el LLM del especialista, no el del supervisor

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_sin_campaign_hint_el_supervisor_clasifica_con_el_llm(self, mock_llm, mock_get_llm):
        # InTouch (Task 8): "comercial" es el unico slug que el registro real
        # acepta -- adaptado de "agendamiento". La defensa (el supervisor usa
        # la clasificacion del LLM) no depende del slug concreto.
        mock_llm.return_value = '{"agente": "comercial"}'
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "dale, para cuando?", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state())

        self.assertEqual(result["active_agent"], "comercial")
        self.assertEqual(result["response_text"], "dale, para cuando?")
        mock_llm.assert_called_once()  # solo el supervisor
        llm_con_tools.ainvoke.assert_called_once()  # solo el especialista

    @patch("bot.flow.graph._get_llm")
    def test_especialista_pide_business_action_y_el_grafo_la_ejecuta(self, mock_get_llm):
        # InTouch (Task 8): "agendamiento" ya no esta en AGENTS, asi que el
        # registro real del grafo no lo resolveria -- se reinyecta SOLO para
        # este test via build_agent_registry parchado (mismo patron que
        # BusinessActionNodeCrearLeadTest/RegistrarNoContactarTest mas abajo en
        # este archivo), para seguir probando la ejecucion real de una tool
        # bindeada de bot.business (consultar_disponibilidad) en vez de
        # debilitar la prueba a un tool_call generico.
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.side_effect = [
            AIMessage(content="", tool_calls=[
                {"name": "consultar_disponibilidad", "args": {"servicio_id": 1, "sucursal_id": 1, "fecha": "2026-07-15"}, "id": "call_1"},
            ]),
            AIMessage(
                content='{"mensaje": "hay hora a las 9", "extracted_data": {}, "next_state": null, "handoff": false}',
                tool_calls=[],
            ),
        ]
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        # AgendamientoAgent expone la tool real de bot.business (ToolNode la
        # ejecuta directo, validando los args contra el schema real de la
        # tool antes de llegar al impl parcheado -- por eso el tool_call de
        # arriba necesita los 3 args obligatorios, a diferencia del mock
        # viejo que aceptaba cualquier cosa) -- se parcha el modulo de
        # origen de _consultar_disponibilidad_impl (bot.business.agendamiento,
        # no bot.flow.agents.agendamiento -- nombres iguales, paquetes
        # distintos), no la copia re-exportada en el barrel bot/business/__init__.py:
        # consultar_disponibilidad (definida en bot.business.agendamiento) lee
        # el nombre desde los globals de su propio modulo, asi que parchear
        # el atributo del barrel no lo intercepta.
        with patch("bot.business.agendamiento._consultar_disponibilidad_impl", return_value=[{"hora": "09:00"}]), \
             patch("bot.flow.graph.build_agent_registry", return_value={"agendamiento": AgendamientoAgent()}):
            graph = get_flow_graph()
            result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(json.loads(result["tool_messages"][-1].content), [{"hora": "09:00"}])
        self.assertEqual(result["response_text"], "hay hora a las 9")

    @patch("bot.flow.graph._get_llm")
    def test_business_action_node_no_revienta_con_llamada_sincrona_real_a_la_db(self, mock_get_llm):
        # Regresion: business_action_node llamaba fn(**params) directo dentro de un
        # nodo async. Si fn hace una consulta sincrona real a Django (como
        # BusinessClient.consultar_disponibilidad/agendar_hora vía ORM sobre 'default'),
        # BaseDatabaseWrapper.cursor() (@async_unsafe) revienta con SynchronousOnlyOperation
        # porque hay un event loop corriendo en ese thread (graph.ainvoke). Con ToolNode,
        # BaseTool.ainvoke usa run_in_executor para tools sin implementacion async nativa
        # (ver langchain_core/tools/base.py), asi que corre en otro thread -- este test
        # sigue siendo la regresion real a vigilar, solo cambia como se inyecta el fake.
        #
        # InTouch (Task 8): "agendamiento" ya no esta en AGENTS -- se reinyecta
        # via build_agent_registry parchado (igual que en el test de arriba)
        # para seguir usando el vehiculo real del fake tool sincrono.
        from langchain.tools import tool

        @tool("consultar_disponibilidad")
        def _fake_consultar_disponibilidad() -> list:
            """Fake para este test -- toca la DB de forma sincrona a proposito."""
            return list(Conversation.objects.all())

        real_business_actions = AgendamientoAgent.business_actions
        AgendamientoAgent.business_actions = lambda self: [_fake_consultar_disponibilidad]
        self.addCleanup(setattr, AgendamientoAgent, "business_actions", real_business_actions)

        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.side_effect = [
            AIMessage(content="", tool_calls=[
                {"name": "consultar_disponibilidad", "args": {}, "id": "call_1"},
            ]),
            AIMessage(
                content='{"mensaje": "hay hora a las 9", "extracted_data": {}, "next_state": null, "handoff": false}',
                tool_calls=[],
            ),
        ]
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        with patch("bot.flow.graph.build_agent_registry", return_value={"agendamiento": AgendamientoAgent()}):
            graph = get_flow_graph()
            result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(json.loads(result["tool_messages"][-1].content), [])

    @patch("bot.flow.graph._get_llm")
    def test_supervisor_node_no_revienta_con_llamada_sincrona_real_a_la_db(self, mock_get_llm):
        # Regresion: supervisor_node llamaba resolve_agent_for_campaign(...) directo
        # dentro de un nodo async. Si el CampaignRule.override de un bot real hace una
        # consulta sincrona a Django (ej. BusinessClient/ORM para chequear si la cita
        # sigue vigente antes de decidir reagendamiento), BaseDatabaseWrapper.cursor()
        # (@async_unsafe) revienta con SynchronousOnlyOperation porque hay un event
        # loop corriendo en ese thread (graph.ainvoke). Usamos la conexion 'default'
        # (sqlite) para reproducir la misma clase de error sin necesitar la conexion
        # 'business' (SQL Server).
        #
        # InTouch (Task 8): el override devuelve "comercial" (adaptado de
        # "agendamiento") -- la defensa es la consulta sincrona real dentro del
        # override, no el slug al que resuelve.
        def fake_override(state):
            list(Conversation.objects.all())
            return "comercial"

        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(
            default_agent="comercial", override=fake_override
        )
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "listo", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(result["active_agent"], "comercial")

    @patch("bot.flow.graph._get_llm")
    def test_specialist_node_acumula_extracted_data_en_flow_data(self, mock_get_llm):
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content=(
                '{"mensaje": "listo", "extracted_data": {"servicio_id": 1}, '
                '"next_state": "ESPERANDO_HORA", "handoff": false}'
            ),
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(
            campaign_hint="agendar_hora", flow_data={"contacto_previo": True},
        ))

        self.assertEqual(result["flow_data"], {"contacto_previo": True, "servicio_id": 1})
        self.assertEqual(result["flow_state"], "ESPERANDO_HORA")

    @patch("bot.flow.graph._get_llm")
    def test_specialist_node_valida_stage_y_lead_class_contra_whitelist(self, mock_get_llm):
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content=(
                '{"mensaje": "listo", "extracted_data": {}, "next_state": null, "handoff": false, '
                '"lead_class": "HOT", "stage": "cotizacion"}'
            ),
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(result["lead_class"], "HOT")
        self.assertEqual(result["stage"], "cotizacion")

    @patch("bot.flow.graph._get_llm")
    def test_specialist_node_ignora_lead_class_fuera_del_whitelist(self, mock_get_llm):
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content=(
                '{"mensaje": "listo", "extracted_data": {}, "next_state": null, "handoff": false, '
                '"lead_class": "SUPER_HOT"}'
            ),
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertIsNone(result["lead_class"])

    @patch("bot.flow.graph._get_llm")
    def test_specialist_node_propaga_handoff_reason_cuando_hay_handoff(self, mock_get_llm):
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content=(
                '{"mensaje": "te derivo", "extracted_data": {}, "next_state": null, "handoff": true, '
                '"handoff_reason": "pide firmar contrato"}'
            ),
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(result["handoff_reason"], "pide firmar contrato")

    @patch("bot.flow.graph._get_llm")
    def test_specialist_node_propaga_requiere_revision(self, mock_get_llm):
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content=(
                '{"mensaje": "entendido", "extracted_data": {}, "next_state": null, "handoff": false, '
                '"requiere_revision": true, "motivo_revision": "riesgo de seguridad"}'
            ),
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertTrue(result["requiere_revision"])
        self.assertEqual(result["motivo_revision"], "riesgo de seguridad")

    @patch("bot.flow.graph._get_llm")
    def test_specialist_node_ignora_handoff_reason_si_no_hay_handoff(self, mock_get_llm):
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content=(
                '{"mensaje": "listo", "extracted_data": {}, "next_state": null, "handoff": false, '
                '"handoff_reason": "motivo que no deberia importar"}'
            ),
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertIsNone(result["handoff_reason"])

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_supervisor_con_output_invalido_cae_a_faq_no_al_primer_agente(self, mock_llm, mock_get_llm):
        # Regresion: el fallback usaba next(iter(AGENTS)), que hoy resuelve a
        # "agendamiento" (primer key registrado) — un output invalido del LLM
        # empujaria a un usuario con una pregunta suelta a un flujo de
        # reserva no solicitado en vez del catch-all seguro "faq". "faq" esta
        # en el camino de tools (Task 7 del plan de RAG agentico) -- el
        # supervisor sigue usando _ainvoke_with_retry (un solo valor, ya no
        # 2: el segundo era para el especialista via el camino viejo).
        mock_llm.return_value = "esto no es json valido"
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "no tengo esa info todavia", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state())

        self.assertEqual(result["active_agent"], "faq")

    def _run(self, graph, state):
        import asyncio
        return asyncio.run(graph.ainvoke(state))

    @patch("bot.flow.graph._get_llm")
    def test_specialist_reintenta_si_el_llm_devuelve_json_invalido_la_primera_vez(self, mock_get_llm):
        # Reproduce el bug real: el LLM a veces devuelve un "mensaje" con
        # comillas sin escapar (ej. precios "desde" con comillas literales),
        # lo que rompe json.loads(). Antes, specialist_node se quedaba con
        # el primer intento invalido y mandaba response_text="" -- y
        # bot/whatsapp/handlers.py no envia nada si response_text queda
        # vacio, asi que el contacto se quedaba sin respuesta y sin ningun
        # error visible. Reintentar la llamada completa (no solo el parseo)
        # le da al LLM otra chance de generar JSON valido. Camino de tools
        # (agendamiento, Task 9): el reintento lo hace
        # _ainvoke_tools_json_with_retry, no _ainvoke_json_with_retry.
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.side_effect = [
            AIMessage(content='{"mensaje": "el arkana hybrid tiene precios "desde" varios", "handoff": false}', tool_calls=[]),
            AIMessage(
                content='{"mensaje": "el arkana hybrid parte desde $24.490.000", "extracted_data": {}, "next_state": null, "handoff": false}',
                tool_calls=[],
            ),
        ]
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(result["response_text"], "el arkana hybrid parte desde $24.490.000")
        self.assertEqual(llm_con_tools.ainvoke.call_count, 2)

    @patch("bot.flow.graph._get_llm")
    def test_specialist_con_json_invalido_persistente_responde_mensaje_generico_no_vacio(self, mock_get_llm):
        # Regresion del mismo bug: el contacto igual debe recibir algun texto
        # (no un response_text vacio que handlers.py directamente no envia).
        #
        # ACTUALIZADO el 2026-09-03: ya no se reintenta 3 veces. Desde el
        # refactor del canal de salida, un texto sin "{" y sin preambulo de
        # instruccion ES una respuesta valida (ver bot/flow/prosa.py y el
        # spec), asi que se manda en el PRIMER intento. Lo que este test sigue
        # protegiendo -- y es lo que importaba -- es que el contacto nunca
        # quede sin texto; ahora ademas sin gastar 3 llamadas al LLM.
        # El caso de JSON roto de verdad (que empieza con "{") lo cubre
        # test_specialist_reintenta_si_el_llm_devuelve_json_invalido_la_primera_vez.
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.side_effect = [
            AIMessage(content="esto no es json", tool_calls=[]),
            AIMessage(content="esto tampoco es json", tool_calls=[]),
            AIMessage(content="y esto menos", tool_calls=[]),
        ]
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertTrue(result["response_text"])
        self.assertEqual(llm_con_tools.ainvoke.call_count, 1)


class GraphGetLlmOverrideTest(TransactionTestCase):
    # TransactionTestCase (no TestCase) a proposito: este test escribe un
    # Setting via ORM en el thread principal y despues lo lee dentro del
    # grafo via sync_to_async, en OTRO thread (thread-sensitive de asgiref).
    # Con TestCase (que envuelve cada test en una transaccion atomica sin
    # commitear), sqlite en modo memoria compartida (mode=memory&cache=shared,
    # el que usa el test runner) bloquea esa lectura entre threads con
    # "database table is locked: bot_setting" — no es SynchronousOnlyOperation,
    # es un locking real de sqlite en shared-cache entre dos conexiones
    # mientras la escritura del thread principal sigue sin commitear.
    # TransactionTestCase commitea de verdad (y trunca tablas entre tests),
    # asi que la escritura es visible sin locks para el thread del grafo.
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "quiero agendar", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def _run(self, graph, state):
        import asyncio
        return asyncio.run(graph.ainvoke(state))

    @patch("bot.flow.graph._ainvoke_messages_with_retry", new_callable=AsyncMock)
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_get_llm_usa_el_override_de_setting_si_existe(self, mock_llm, mock_llm_tools):
        from bot.models import Setting
        Setting.objects.create(key="openrouter_model_override", value="anthropic/claude-sonnet-4.5")
        # "faq" ya esta en el camino de tools (Task 7 del plan de RAG
        # agentico) -- se mockean AMBAS llamadas (supervisor via
        # _ainvoke_with_retry, specialist via _ainvoke_messages_with_retry)
        # porque el objetivo de este test es que _get_llm() no reviente con
        # SynchronousOnlyOperation al leer el Setting dentro del nodo async,
        # sin importar que camino tome el especialista elegido.
        mock_llm.return_value = '{"agente": "faq"}'
        mock_llm_tools.return_value = AIMessage(
            content='{"mensaje": "...", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )

        graph = get_flow_graph()
        # No hace falta inspeccionar el modelo real usado (no invocamos el LLM
        # de verdad, esta mockeado) — el objetivo es que _get_llm() no reviente
        # con SynchronousOnlyOperation al leer el Setting dentro del nodo async.
        result = self._run(graph, self._initial_state())
        self.assertIsNotNone(result["active_agent"])


class GraphCustomSpecialistTest(TransactionTestCase):
    # TransactionTestCase (no TestCase) por el mismo motivo documentado en
    # GraphGetLlmOverrideTest: este test escribe un CustomSpecialist via ORM
    # en el thread principal y despues lo lee dentro del grafo via
    # sync_to_async (build_agent_registry), en OTRO thread. TestCase
    # (transaccion sin commitear) bloquearia esa lectura en sqlite shared-cache.
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "donde esta mi pedido?", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def _run(self, graph, state):
        return asyncio.run(graph.ainvoke(state))

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_supervisor_rutea_a_un_especialista_personalizado(self, mock_llm, mock_get_llm):
        # "envios" (CustomPromptAgent con business_actions() == []) entra al
        # camino de tool-calling en specialist_node (ver comentario en
        # SpecialistNodeModeloImagenTest._mock_get_llm_con_respuesta) aunque
        # nunca use tools de verdad -- el supervisor si sigue el camino viejo
        # (_ainvoke_with_retry, sin tools), asi que este test necesita AMBOS
        # mocks: uno para la clasificacion del supervisor, otro para la
        # respuesta del especialista.
        from bot.models import CustomSpecialist, save_prompt_version
        CustomSpecialist.objects.create(
            slug="envios", label="Envíos", descripcion="Responde sobre el estado de un envio.",
        )
        save_prompt_version("custom:envios", "Sos el especialista de envios. Responde con el estado del pedido.")
        mock_llm.side_effect = ['{"agente": "envios"}']
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "tu pedido esta en camino", '
                    '"extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state())

        self.assertEqual(result["active_agent"], "envios")
        self.assertEqual(result["response_text"], "tu pedido esta en camino")


class SpecialistNodeGlobalPromptTest(TransactionTestCase):
    # TransactionTestCase por el mismo motivo que las demas clases de este
    # archivo que escriben PromptVersion via ORM y lo leen dentro del grafo
    # via sync_to_async, en otro thread real.
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "hola", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    @patch("bot.flow.graph._get_llm")
    def test_prompt_global_llega_al_llm_sin_importar_el_especialista_activo(self, mock_get_llm):
        # "faq" (Task 7 del plan de RAG agentico) y "agendamiento" estan
        # ambos en el camino de tools -- un solo mock (_get_llm -> bind_tools
        # -> ainvoke) alcanza para los dos, inspeccionando el SystemMessage
        # real que le llega al LLM.
        from bot.models import save_prompt_version
        save_prompt_version("global", "MARCADOR_GLOBAL_TEST: nunca hables mal de la competencia.")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "...", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        with self.subTest(agente="faq"):
            state = self._initial_state(active_agent="faq", text="hola")
            asyncio.run(specialist_node(state))
            mensajes_enviados = llm_con_tools.ainvoke.call_args[0][0]
            system_prompt = mensajes_enviados[0].content
            self.assertIn("MARCADOR_GLOBAL_TEST", system_prompt)

        with self.subTest(agente="agendamiento"):
            state = self._initial_state(active_agent="agendamiento", text="hola")
            asyncio.run(specialist_node(state))
            mensajes_enviados = llm_con_tools.ainvoke.call_args[0][0]
            system_prompt = mensajes_enviados[0].content
            self.assertIn("MARCADOR_GLOBAL_TEST", system_prompt)

    @patch("bot.flow.graph._get_llm")
    def test_prompt_global_llega_al_llm_con_un_especialista_custom(self, mock_get_llm):
        # El caso real que motiva esta feature es un especialista custom
        # (p.ej. "ventas"), no solo los estaticos -- esta prueba lo cubre
        # en vez de dejar la afirmacion "aplica a cualquier especialista,
        # incluidos los custom" solo como argumento de arquitectura.
        # "ventas" tiene tools reales (business_actions() devuelve una lista
        # no vacia, Task 5) asi que specialist_node lo manda por el camino
        # de tool-calling (_specialist_node_con_tools) -- el mock ya no es
        # sobre _ainvoke_with_retry (nunca se llama en ese camino) sino
        # sobre _get_llm -> bind_tools -> ainvoke, igual que en
        # SpecialistNodeConToolsTest.
        from bot.models import CustomSpecialist, save_prompt_version
        CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "Sos el especialista de ventas.")
        save_prompt_version("global", "MARCADOR_GLOBAL_TEST: nunca hables mal de la competencia.")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "...", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        state = self._initial_state(active_agent="ventas", text="hola")
        asyncio.run(specialist_node(state))
        mensajes_enviados = llm_con_tools.ainvoke.call_args[0][0]
        system_prompt = mensajes_enviados[0].content
        self.assertIn("MARCADOR_GLOBAL_TEST", system_prompt)

    @patch("bot.flow.graph._get_llm")
    def test_prompt_global_va_antes_del_prompt_del_especialista(self, mock_get_llm):
        from bot.models import save_prompt_version
        save_prompt_version("global", "MARCADOR_GLOBAL_TEST")
        save_prompt_version("faq", "MARCADOR_FAQ_TEST")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "...", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        state = self._initial_state(active_agent="faq", text="hola")
        asyncio.run(specialist_node(state))

        mensajes_enviados = llm_con_tools.ainvoke.call_args[0][0]
        system_prompt = mensajes_enviados[0].content
        self.assertLess(
            system_prompt.index("MARCADOR_GLOBAL_TEST"),
            system_prompt.index("MARCADOR_FAQ_TEST"),
        )


class GraphStaticAgentPromptOverrideTest(TransactionTestCase):
    # TransactionTestCase (no TestCase) por el mismo motivo documentado en
    # GraphGetLlmOverrideTest: este test escribe un Setting via ORM en el
    # thread principal y despues lo lee dentro del grafo (effective_prompt()
    # -> get_setting()) via sync_to_async, en OTRO thread real. Con TestCase
    # (transaccion sin commitear) sqlite en modo memoria compartida bloquearia
    # esa lectura entre threads con "database table is locked: bot_setting".
    # TransactionTestCase commitea de verdad, asi que la escritura es visible
    # sin locks para el thread del grafo.
    #
    # Regresion cubierta: los overrides de prompt para los 3 especialistas
    # estaticos (agendamiento/confirmacion/faq) se guardaban desde el panel
    # de admin pero nunca se leian en runtime. effective_prompt() ya se
    # arreglo para leer el Setting, y hay un test unitario de effective_prompt()
    # en aislamiento (test_agents.py) y un test de grafo end-to-end para un
    # especialista CUSTOM (GraphCustomSpecialistTest), pero faltaba un test de
    # grafo end-to-end que probara que un override guardado para un
    # especialista ESTATICO efectivamente llega al LLM cuando un mensaje real
    # se enruta por el grafo compilado.
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "quiero agendar", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def _run(self, graph, state):
        return asyncio.run(graph.ainvoke(state))

    @patch("bot.flow.graph._get_llm")
    def test_override_de_prompt_de_agente_estatico_llega_al_llm(self, mock_get_llm):
        # agendamiento (Task 9) ya esta en el camino de tools -- el override
        # de prompt llega en el SystemMessage, no en un prompt de texto
        # plano via _ainvoke_with_retry.
        from bot.models import save_prompt_version
        save_prompt_version("agendamiento", "MARCADOR_OVERRIDE_TEST: atende agendamientos solo por telefono.")
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="agendamiento")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "listo", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(result["active_agent"], "agendamiento")
        llm_con_tools.ainvoke.assert_called_once()  # ruteo deterministico, sin LLM del supervisor
        mensajes_enviados = llm_con_tools.ainvoke.call_args[0][0]
        system_prompt = mensajes_enviados[0].content
        self.assertIn("MARCADOR_OVERRIDE_TEST", system_prompt)


# CustomAgentBusinessResultRoundTripTest (que vivia aca) se borro en Task 7:
# probaba que agent.build_prompt() incluyera el ultimo business_result como
# texto interpolado en el prompt -- ese metodo ya no existe en
# CustomPromptAgent (Task 5 lo reemplazo por build_system_prompt, que
# deliberadamente NO incluye el business_result en texto, ver su docstring).
# Bajo tool-calling nativo el resultado viaja como ToolMessage real
# construido por _construir_mensajes, no como texto -- ese comportamiento ya
# esta cubierto, con mas precision (assertions sobre tipo/contenido de
# mensaje, no un diff de strings), por
# ConstruirMensajesTest.test_con_tool_messages_resueltos_agrega_aimessage_y_toolmessage
# (Task 2, renombrado en Task 11 al migrar de pending_tool_call/business_result
# a tool_messages) y por
# SpecialistNodeConToolsTest.test_segunda_vuelta_con_resultado_real_incluye_toolmessage_en_los_mensajes
# (este mismo Task 7), asi que no hace falta reemplazarlo por un test
# equivalente -- seria una duplicacion de esos dos.


class BusinessActionNodeNoReejecutaAccionCompletadaTest(TestCase):
    """Fix 4 de la revision final: defensa en profundidad en business_action_node
    contra que el LLM pida la MISMA business_action dos veces dentro del mismo
    turno despues de que ya devolvio ok=true (ver comentario en
    BotState.tool_messages -- el dedup se deriva escaneando ese canal desde
    la Task 11, ya no via un campo business_action_completed aparte)."""

    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _state(self, **overrides):
        state = {
            "wa_id": "1", "text": "x", "name": "n", "messages": [],
            "flow_state": "IDLE", "flow_data": {}, "active_agent": "prueba",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def test_repetir_la_misma_accion_ya_completada_con_exito_no_reejecuta_fn(self):
        from langchain.tools import tool
        from bot.flow.graph import business_action_node

        llamadas = []

        @tool
        def hacer_algo() -> dict:
            """Hace algo de prueba."""
            llamadas.append({})
            return {"ok": True, "valor": 42}

        class AgentePrueba:
            name = "prueba"
            def business_actions(self):
                return [hacer_algo]

        state = self._state(tool_messages=[
            AIMessage(content="", tool_calls=[{"name": "hacer_algo", "args": {}, "id": "call_prev"}]),
            ToolMessage(content=json.dumps({"ok": True, "valor": 42}), tool_call_id="call_prev", name="hacer_algo"),
            AIMessage(content="", tool_calls=[{"name": "hacer_algo", "args": {}, "id": "call_1"}]),
        ])

        with patch("bot.flow.graph.build_agent_registry", return_value={"prueba": AgentePrueba()}):
            result = asyncio.run(business_action_node(state))

        self.assertEqual(llamadas, [])  # fn NUNCA se volvio a invocar
        self.assertEqual(json.loads(result["tool_messages"][-1].content), {"ok": True, "valor": 42})
        self.assertEqual(result["tool_messages"][-1].name, "hacer_algo")

    def test_una_accion_distinta_a_la_completada_se_ejecuta_normalmente(self):
        from langchain.tools import tool
        from bot.flow.graph import business_action_node

        llamadas = []

        @tool
        def accion_a() -> dict:
            """Accion A de prueba."""
            llamadas.append("a")
            return {"ok": True, "quien": "a"}

        @tool
        def accion_b() -> dict:
            """Accion B de prueba."""
            llamadas.append("b")
            return {"ok": True, "quien": "b"}

        class AgentePrueba:
            name = "prueba"
            def business_actions(self):
                return [accion_a, accion_b]

        state = self._state(tool_messages=[
            AIMessage(content="", tool_calls=[{"name": "accion_a", "args": {}, "id": "call_prev"}]),
            ToolMessage(content=json.dumps({"ok": True, "quien": "a"}), tool_call_id="call_prev", name="accion_a"),
            AIMessage(content="", tool_calls=[{"name": "accion_b", "args": {}, "id": "call_1"}]),
        ])

        with patch("bot.flow.graph.build_agent_registry", return_value={"prueba": AgentePrueba()}):
            result = asyncio.run(business_action_node(state))

        self.assertEqual(llamadas, ["b"])  # accion_b si se ejecuto
        self.assertEqual(json.loads(result["tool_messages"][-1].content), {"ok": True, "quien": "b"})
        self.assertEqual(result["tool_messages"][-1].name, "accion_b")

    def test_primera_ejecucion_de_una_accion_se_ejecuta_normalmente(self):
        from langchain.tools import tool
        from bot.flow.graph import business_action_node

        llamadas = []

        @tool
        def hacer_algo() -> dict:
            """Hace algo de prueba."""
            llamadas.append({})
            return {"ok": True, "valor": 1}

        class AgentePrueba:
            name = "prueba"
            def business_actions(self):
                return [hacer_algo]

        state = self._state(tool_messages=[
            AIMessage(content="", tool_calls=[{"name": "hacer_algo", "args": {}, "id": "call_1"}]),
        ])

        with patch("bot.flow.graph.build_agent_registry", return_value={"prueba": AgentePrueba()}):
            result = asyncio.run(business_action_node(state))

        self.assertEqual(len(llamadas), 1)
        self.assertEqual(result["tool_messages"][-1].name, "hacer_algo")
        self.assertTrue(json.loads(result["tool_messages"][-1].content)["ok"])

    def test_repetir_la_misma_accion_con_resultado_previo_ok_false_si_reejecuta(self):
        # Solo se corta el circuito cuando el resultado previo fue ok=true --
        # si fue ok=false (params invalidos, fallo de negocio), el LLM
        # legitimamente puede estar reintentando con datos corregidos.
        from langchain.tools import tool
        from bot.flow.graph import business_action_node

        llamadas = []

        @tool
        def hacer_algo() -> dict:
            """Hace algo de prueba."""
            llamadas.append({})
            return {"ok": True, "valor": 99}

        class AgentePrueba:
            name = "prueba"
            def business_actions(self):
                return [hacer_algo]

        state = self._state(tool_messages=[
            AIMessage(content="", tool_calls=[{"name": "hacer_algo", "args": {}, "id": "call_prev"}]),
            ToolMessage(content=json.dumps({"ok": False, "motivo": "dato invalido"}), tool_call_id="call_prev", name="hacer_algo"),
            AIMessage(content="", tool_calls=[{"name": "hacer_algo", "args": {}, "id": "call_1"}]),
        ])

        with patch("bot.flow.graph.build_agent_registry", return_value={"prueba": AgentePrueba()}):
            result = asyncio.run(business_action_node(state))

        self.assertEqual(len(llamadas), 1)
        self.assertEqual(json.loads(result["tool_messages"][-1].content), {"ok": True, "valor": 99})

    def test_la_misma_tool_con_args_distintos_si_se_reejecuta(self):
        # consultar_base_conocimiento existe para llamarse varias veces por
        # turno con terminos distintos ("garantia" y "sucursales" en el mismo
        # mensaje, o una reformulacion). Deduplicando solo por nombre, la
        # segunda consulta recibia los chunks de la primera reetiquetados y el
        # modelo contestaba desde el contexto equivocado.
        from langchain.tools import tool
        from bot.flow.graph import business_action_node

        consultas = []

        @tool
        def consultar_base_conocimiento(query: str) -> dict:
            """Busca en la base de conocimiento."""
            consultas.append(query)
            return {"ok": True, "chunks": [f"info de {query}"]}

        class AgentePrueba:
            name = "prueba"
            def business_actions(self):
                return [consultar_base_conocimiento]

        state = self._state(tool_messages=[
            AIMessage(content="", tool_calls=[{"name": "consultar_base_conocimiento", "args": {"query": "garantia"}, "id": "call_prev"}]),
            ToolMessage(
                content=json.dumps({"ok": True, "chunks": ["info de garantia"]}),
                tool_call_id="call_prev", name="consultar_base_conocimiento",
            ),
            AIMessage(content="", tool_calls=[{"name": "consultar_base_conocimiento", "args": {"query": "sucursales"}, "id": "call_1"}]),
        ])

        with patch("bot.flow.graph.build_agent_registry", return_value={"prueba": AgentePrueba()}):
            result = asyncio.run(business_action_node(state))

        self.assertEqual(consultas, ["sucursales"])  # SI se ejecuto la segunda busqueda
        self.assertEqual(
            json.loads(result["tool_messages"][-1].content), {"ok": True, "chunks": ["info de sucursales"]},
        )


class FiltrarAccionesRepetidasTest(TestCase):
    def _previos(self, args_previos, args_nuevos):
        llamada_previa = AIMessage(content="", tool_calls=[
            {"name": "consultar_base_conocimiento", "args": args_previos, "id": "call_prev"},
        ])
        resultado_previo = ToolMessage(
            content=json.dumps({"ok": True, "chunks": ["algo"]}),
            tool_call_id="call_prev", name="consultar_base_conocimiento",
        )
        nueva = {"name": "consultar_base_conocimiento", "args": args_nuevos, "id": "call_1"}
        llamada_nueva = AIMessage(content="", tool_calls=[nueva])
        return [nueva], [llamada_previa, resultado_previo, llamada_nueva]

    def test_args_distintos_quedan_pendientes(self):
        from bot.flow.graph import _filtrar_acciones_repetidas
        tool_calls, previos = self._previos({"query": "garantia"}, {"query": "sucursales"})

        ya_resueltas, pendientes = _filtrar_acciones_repetidas(tool_calls, previos)

        self.assertEqual(ya_resueltas, [])
        self.assertEqual(pendientes, tool_calls)

    def test_args_iguales_se_cortan_en_corto(self):
        from bot.flow.graph import _filtrar_acciones_repetidas
        tool_calls, previos = self._previos({"query": "garantia"}, {"query": "garantia"})

        ya_resueltas, pendientes = _filtrar_acciones_repetidas(tool_calls, previos)

        self.assertEqual(pendientes, [])
        self.assertEqual(len(ya_resueltas), 1)
        self.assertEqual(ya_resueltas[0].tool_call_id, "call_1")
        self.assertEqual(json.loads(ya_resueltas[0].content)["chunks"], ["algo"])

    def test_args_por_tool_call_id_mapea_todas_las_ai_messages(self):
        from bot.flow.graph import _args_por_tool_call_id
        mensajes = [
            AIMessage(content="", tool_calls=[
                {"name": "a", "args": {"x": 1}, "id": "1"},
                {"name": "b", "args": {"y": 2}, "id": "2"},
            ]),
            ToolMessage(content="{}", tool_call_id="1", name="a"),
            AIMessage(content="sin tool calls"),
        ]
        self.assertEqual(_args_por_tool_call_id(mensajes), {"1": {"x": 1}, "2": {"y": 2}})


class BusinessActionNodeParamsInvalidosTest(TestCase):
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def test_params_con_clave_faltante_no_crashea_devuelve_resultado_de_error(self):
        # ToolNode valida los argumentos con Pydantic y devuelve su propio
        # mensaje de error en texto plano (status="error"), distinto del
        # JSON {"ok": false, "motivo": ...} que devuelven las tools cuando
        # SI se ejecutan pero fallan por una regla de negocio.
        from langchain.tools import tool
        from bot.flow.graph import business_action_node

        @tool
        def hacer_algo(a: int, b: int) -> dict:
            """Hace algo de prueba."""
            return {"ok": True}

        class AgentePrueba:
            name = "prueba"
            def business_actions(self):
                return [hacer_algo]

        state = {
            "wa_id": "1", "text": "x", "name": "n", "messages": [],
            "flow_state": "IDLE", "flow_data": {}, "active_agent": "prueba",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": [AIMessage(content="", tool_calls=[
                {"name": "hacer_algo", "args": {"a": 1, "clave_de_mas": 2}, "id": "call_1"},
            ])],
            "reply_to": None, "_dispatch": None,
        }
        with patch("bot.flow.graph.build_agent_registry", return_value={"prueba": AgentePrueba()}):
            result = asyncio.run(business_action_node(state))

        mensaje = result["tool_messages"][-1]
        self.assertEqual(mensaje.status, "error")
        self.assertIn("b", mensaje.content)
        self.assertIn("Field required", mensaje.content)


class BusinessActionNodeCrearLeadTest(TransactionTestCase):
    databases = {"default", "qaintouch"}

    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _state(self, business_params):
        return {
            "wa_id": "1", "text": "x", "name": "n", "messages": [],
            "flow_state": "IDLE", "flow_data": {}, "active_agent": "ventas",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": [AIMessage(content="", tool_calls=[
                {"name": "registrar_parte_pago", "args": business_params, "id": "call_1"},
            ])],
            "reply_to": None, "_dispatch": None,
        }

    def test_params_incompletos_no_crashea_devuelve_resultado_de_error(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.flow.graph import business_action_node
        from bot.models import CustomSpecialist, save_prompt_version

        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        agent = CustomPromptAgent(row)
        state = self._state({"anio": 2019})  # falta marca_modelo, que es obligatorio

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": agent}):
            result = asyncio.run(business_action_node(state))

        mensaje = result["tool_messages"][-1]
        self.assertEqual(mensaje.status, "error")
        self.assertIn("Field required", mensaje.content)

    def test_params_completos_escriben_el_registro_de_verdad(self):
        # Cavem reemplazo crear_lead (que escribia en el CRM real, BD
        # qaintouch) por las tools propias del bot -- no corresponde meter
        # datos ficticios de una demo en el CRM productivo.
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.flow.graph import business_action_node
        from bot.models import Conversation, CustomSpecialist, VehiculoPartePago, save_prompt_version

        Conversation.objects.create(wa_id="1", name="Juan Perez")
        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        agent = CustomPromptAgent(row)
        state = self._state({"marca_modelo": "Mazda CX-5", "anio": 2019, "km": 80000})

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": agent}):
            result = asyncio.run(business_action_node(state))

        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertTrue(contenido["ok"])
        self.assertTrue(VehiculoPartePago.objects.filter(pk=contenido["id"]).exists())


class BusinessActionNodeRegistrarNoContactarTest(TransactionTestCase):
    """El wa_id de "registrar_no_contactar" es un dato de compliance: debe
    venir siempre del estado real de la conversacion (business_action_node),
    nunca de lo que el LLM haya puesto en tool_calls[0].args -- ver el
    comentario en graph.py::business_action_node."""
    databases = {"default", "qaintouch"}

    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _state(self, wa_id, tool_call_args):
        return {
            "wa_id": wa_id, "text": "no me contacten mas", "name": "n", "messages": [],
            "flow_state": "IDLE", "flow_data": {}, "active_agent": "ventas",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": [AIMessage(content="", tool_calls=[
                {"name": "registrar_no_contactar", "args": tool_call_args, "id": "call_1"},
            ])],
            "reply_to": None, "_dispatch": None,
        }

    def test_usa_el_wa_id_del_estado_no_del_llm(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.flow.graph import business_action_node
        from bot.models import CustomSpecialist, save_prompt_version, esta_optout

        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        agent = CustomPromptAgent(row)
        # El LLM no deberia poder mandar wa_id (el @tool no lo expone), pero
        # si algun dia alguien lo agregara por error, el del estado real
        # tiene que ganar igual.
        state = self._state("56900000001", {"motivo": "no quiere mas mensajes", "wa_id": "otro-numero"})

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": agent}):
            result = asyncio.run(business_action_node(state))

        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertTrue(contenido["ok"])
        self.assertTrue(esta_optout("56900000001"))
        self.assertFalse(esta_optout("otro-numero"))


class BusinessActionNodeCrearCasoTest(TransactionTestCase):
    databases = {"default", "qaintouch"}

    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _state(self, wa_id, tool_call_args):
        return {
            "wa_id": wa_id, "text": "tengo un reclamo de garantia", "name": "n", "messages": [],
            "flow_state": "IDLE", "flow_data": {}, "active_agent": "faq",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": [AIMessage(content="", tool_calls=[
                {"name": "crear_caso", "args": tool_call_args, "id": "call_1"},
            ])],
            "reply_to": None, "_dispatch": None,
        }

    def test_tipo_valido_crea_caso_ligado_al_wa_id_del_estado(self):
        from bot.flow.graph import business_action_node
        from bot.models import Incident

        conv = Conversation.objects.create(wa_id="56900000003")
        state = self._state("56900000003", {"tipo": "garantia", "resumen": "motor con ruido raro", "wa_id": "otro-numero"})

        result = asyncio.run(business_action_node(state))

        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertTrue(contenido["ok"])
        caso = Incident.objects.get(pk=contenido["caso_id"])
        self.assertEqual(caso.kind, "garantia")
        self.assertEqual(caso.conversation_id, conv.id)

    def test_disponible_tambien_en_agendamiento(self):
        from bot.flow.graph import business_action_node
        from bot.models import Incident

        Conversation.objects.create(wa_id="56900000004")
        state = self._state("56900000004", {"tipo": "repuesto", "resumen": "necesita parachoques"})
        state["active_agent"] = "agendamiento"
        state["tool_messages"][0].tool_calls[0]["name"] = "crear_caso"

        result = asyncio.run(business_action_node(state))

        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertTrue(contenido["ok"])
        self.assertTrue(Incident.objects.filter(pk=contenido["caso_id"], kind="repuesto").exists())


class BusinessActionNodeAgendarHoraTest(TransactionTestCase):
    """El contacto de una reserva es un dato de identidad, igual que el
    wa_id de "registrar_no_contactar"/"crear_caso": debe venir siempre del
    estado real de la conversacion, nunca de lo que el LLM haya puesto en
    tool_calls[0].args -- ver BusinessActionNodeRegistrarNoContactarTest."""
    databases = {"default", "qaintouch"}

    def setUp(self):
        PRE_ROUTING_RULES.clear()
        from bot.models import Servicio, Sucursal
        self.servicio = Servicio.objects.create(nombre="Corte", duracion_min=30)
        self.sucursal = Sucursal.objects.create(nombre="Centro", horario_texto="9:00-18:00")

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _state(self, wa_id, tool_call_args):
        return {
            "wa_id": wa_id, "text": "agendame una hora", "name": "n", "messages": [],
            "flow_state": "IDLE", "flow_data": {}, "active_agent": "agendamiento",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": [AIMessage(content="", tool_calls=[
                {"name": "agendar_hora", "args": tool_call_args, "id": "call_1"},
            ])],
            "reply_to": None, "_dispatch": None,
        }

    def test_usa_el_wa_id_del_estado_no_el_contacto_del_llm(self):
        from bot.flow.graph import business_action_node
        from bot.models import Reserva

        state = self._state("56900000005", {
            "servicio_id": self.servicio.id, "sucursal_id": self.sucursal.id,
            "fecha": "2026-09-01", "hora": "10:00", "contacto": "otro-numero", "nombre": "Ana",
        })

        result = asyncio.run(business_action_node(state))

        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertTrue(contenido["ok"])
        reserva = Reserva.objects.get(codigo=contenido["codigo"])
        self.assertEqual(reserva.contacto, "56900000005")


class BusinessActionNodeRegistrarConsentimientoTest(TransactionTestCase):
    databases = {"default", "qaintouch"}

    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _state(self, wa_id, tool_call_args):
        return {
            "wa_id": wa_id, "text": "acepto que usen mis datos", "name": "n", "messages": [],
            "flow_state": "IDLE", "flow_data": {}, "active_agent": "ventas",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": [AIMessage(content="", tool_calls=[
                {"name": "registrar_consentimiento", "args": tool_call_args, "id": "call_1"},
            ])],
            "reply_to": None, "_dispatch": None,
        }

    def test_usa_el_wa_id_del_estado_no_del_llm(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.flow.graph import business_action_node
        from bot.models import CustomSpecialist, save_prompt_version, tiene_consentimiento

        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        agent = CustomPromptAgent(row)
        state = self._state("56900000002", {"otorgado": True, "motivo": "acepta seguimiento", "wa_id": "otro-numero"})

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": agent}):
            result = asyncio.run(business_action_node(state))

        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertTrue(contenido["ok"])
        self.assertTrue(tiene_consentimiento("56900000002"))
        self.assertIsNone(tiene_consentimiento("otro-numero"))


class BusinessActionNodeConsultarBaseConocimientoTest(TransactionTestCase):
    databases = {"default", "qaintouch"}

    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    class _AgenteDePrueba:
        name = "faq"

        def business_actions(self):
            return [consultar_base_conocimiento]

    def _state(self, tool_messages_previos):
        return {
            "wa_id": "56911112222", "text": "cual es la garantia?", "name": "Juan", "messages": [],
            "flow_state": "IDLE", "flow_data": {}, "active_agent": "faq",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": tool_messages_previos,
            "reply_to": None, "_dispatch": None,
        }

    @patch("bot.rag.tool._rerankear", new_callable=AsyncMock)
    @patch("bot.rag.tool._buscar_en_supabase", new_callable=AsyncMock)
    def test_resultado_relevante_devuelve_ok_true(self, mock_buscar, mock_rerank):
        chunk = {
            "contenido": "la garantia es de 3 anios", "fuente_url": "https://renault.cl/garantia/",
            "categoria": "garantia", "similarity": 0.8,
        }
        mock_buscar.return_value = [chunk]
        mock_rerank.return_value = [chunk]
        tool_messages_previos = [AIMessage(content="", tool_calls=[
            {"name": "consultar_base_conocimiento", "args": {"query": "garantia"}, "id": "call_1"},
        ])]
        with patch("bot.flow.graph.build_agent_registry", return_value={"faq": self._AgenteDePrueba()}):
            result = asyncio.run(business_action_node(self._state(tool_messages_previos)))
        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertTrue(contenido["ok"])
        self.assertEqual(contenido["resultados"][0]["texto"], "la garantia es de 3 anios")
        self.assertEqual(contenido["resultados"][0]["categoria"], "garantia")

    @patch("bot.rag.tool._rerankear", new_callable=AsyncMock)
    @patch("bot.rag.tool._buscar_en_supabase", new_callable=AsyncMock)
    def test_resultado_no_relevante_devuelve_ok_false(self, mock_buscar, mock_rerank):
        mock_buscar.return_value = [{
            "contenido": "texto no relacionado", "fuente_url": "https://renault.cl/x/",
            "categoria": "otro", "similarity": 0.8,
        }]
        mock_rerank.return_value = []
        tool_messages_previos = [AIMessage(content="", tool_calls=[
            {"name": "consultar_base_conocimiento", "args": {"query": "garantia"}, "id": "call_1"},
        ])]
        with patch("bot.flow.graph.build_agent_registry", return_value={"faq": self._AgenteDePrueba()}):
            result = asyncio.run(business_action_node(self._state(tool_messages_previos)))
        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertFalse(contenido["ok"])

    @patch("bot.rag.tool._rerankear", new_callable=AsyncMock)
    @patch("bot.rag.tool._buscar_en_supabase", new_callable=AsyncMock)
    def test_sin_candidatos_no_llama_al_rerank(self, mock_buscar, mock_rerank):
        # Reemplaza al viejo test_chunk_bajo_el_umbral_de_similitud_no_llega_al_grading:
        # ese umbral pre-rerank ya no existe (ver "Hallazgo de esta investigacion" al
        # inicio de este plan) -- ahora el unico corte antes de llamar al rerank es
        # que no haya NINGUN candidato del retrieval hibrido.
        mock_buscar.return_value = []
        tool_messages_previos = [AIMessage(content="", tool_calls=[
            {"name": "consultar_base_conocimiento", "args": {"query": "garantia"}, "id": "call_1"},
        ])]
        with patch("bot.flow.graph.build_agent_registry", return_value={"faq": self._AgenteDePrueba()}):
            result = asyncio.run(business_action_node(self._state(tool_messages_previos)))
        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertFalse(contenido["ok"])
        mock_rerank.assert_not_called()

    @patch("bot.rag.tool._rerankear", new_callable=AsyncMock)
    @patch("bot.rag.tool._buscar_en_supabase", new_callable=AsyncMock)
    def test_freno_anti_loop_no_llama_a_supabase_al_cuarto_intento(self, mock_buscar, mock_rerank):
        previos_ya_intentados = [
            ToolMessage(content='{"ok": false, "motivo": "no encontre nada"}', tool_call_id=f"call_{i}", name="consultar_base_conocimiento")
            for i in range(3)
        ]
        nueva_llamada = AIMessage(content="", tool_calls=[
            {"name": "consultar_base_conocimiento", "args": {"query": "garantia"}, "id": "call_nuevo"},
        ])
        tool_messages_previos = previos_ya_intentados + [nueva_llamada]
        with patch("bot.flow.graph.build_agent_registry", return_value={"faq": self._AgenteDePrueba()}):
            result = asyncio.run(business_action_node(self._state(tool_messages_previos)))
        mock_buscar.assert_not_called()
        contenido = json.loads(result["tool_messages"][-1].content)
        self.assertFalse(contenido["ok"])
        self.assertIn("No sigas buscando", contenido["motivo"])


class BusinessActionNodeMultiplesToolCallsTest(TransactionTestCase):
    databases = {"default", "qaintouch"}

    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def test_ejecuta_las_dos_tool_calls_de_un_mismo_turno(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.flow.graph import business_action_node
        from bot.models import CustomSpecialist, save_prompt_version
        from bot.tests.test_usados_cavem import crear_vehiculo

        crear_vehiculo(codigo="US001", marca="Hyundai", modelo="Tucson", precio_oferta=19_990_000)
        crear_vehiculo(codigo="US004", marca="Kia", modelo="Sportage", precio_oferta=18_990_000)
        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        agent = CustomPromptAgent(row)

        state = {
            "wa_id": "56900000001", "text": "dame specs de la Tucson y de la Sportage", "name": "n",
            "messages": [], "flow_state": "IDLE", "flow_data": {}, "active_agent": "ventas",
            "campaign_hint": None, "response_text": "", "interactive_buttons": None,
            "interactive_list": None, "interactive_options": None, "list_button_text": None,
            "tool_messages": [AIMessage(content="", tool_calls=[
                {"name": "consultar_ficha_vehiculo", "args": {"referencia": "Tucson"}, "id": "call_1"},
                {"name": "consultar_ficha_vehiculo", "args": {"referencia": "Sportage"}, "id": "call_2"},
            ])],
            "reply_to": None, "_dispatch": None,
        }

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": agent}):
            result = asyncio.run(business_action_node(state))

        mensajes = result["tool_messages"]
        self.assertEqual(len(mensajes), 2)
        ids = {m.tool_call_id for m in mensajes}
        self.assertEqual(ids, {"call_1", "call_2"})
        contenido_call1 = json.loads(next(m.content for m in mensajes if m.tool_call_id == "call_1"))
        contenido_call2 = json.loads(next(m.content for m in mensajes if m.tool_call_id == "call_2"))
        self.assertTrue(contenido_call1["ok"])
        self.assertTrue(contenido_call2["ok"])


class BusinessActionNodeConPendingToolCallTest(TransactionTestCase):
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _agente_ventas(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.models import CustomSpecialist, save_prompt_version
        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="x")
        save_prompt_version("custom:ventas", "prompt")
        return CustomPromptAgent(row)

    def _state(self, **overrides):
        state = {
            "active_agent": "ventas", "tool_messages": [],
        }
        state.update(overrides)
        return state

    def test_ejecuta_la_impl_correcta_y_devuelve_el_resultado_real(self):
        from bot.flow.graph import business_action_node
        estado = self._state(tool_messages=[AIMessage(content="", tool_calls=[{
            "name": "simular_financiamiento",
            "args": {"precio": 20_000_000, "pie": 4_000_000, "plazo_meses": 36},
            "id": "call_1",
        }])])
        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(business_action_node(estado))
        nuevo = resultado["tool_messages"][-1]
        self.assertTrue(json.loads(nuevo.content)["ok"])
        self.assertEqual(nuevo.name, "simular_financiamiento")
        self.assertEqual(resultado["_dispatch"], "ventas")

    def test_accion_desconocida_emite_toolmessage_de_error_sin_crashear(self):
        # Antes de esta task, action desconocida devolvia business_result=None
        # (bug corregido aca -- ver BusinessActionNodeEmiteToolMessageSiempreTest):
        # ahora siempre hay un ToolMessage, aunque sea de error. Con ToolNode,
        # una accion que no esta en la lista de tools del agente devuelve su
        # propio mensaje de error en texto plano (status="error"), no el JSON
        # {"ok": false, ...} que devuelven las tools reales al fallar por una
        # regla de negocio.
        from bot.flow.graph import business_action_node
        estado = self._state(tool_messages=[AIMessage(content="", tool_calls=[
            {"name": "accion_que_no_existe", "args": {}, "id": "call_1"},
        ])])
        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(business_action_node(estado))
        nuevo = resultado["tool_messages"][-1]
        self.assertIsInstance(nuevo, ToolMessage)
        self.assertEqual(nuevo.status, "error")

    def test_params_invalidos_devuelve_error_sin_crashear(self):
        from bot.flow.graph import business_action_node
        estado = self._state(tool_messages=[AIMessage(content="", tool_calls=[
            {"name": "simular_financiamiento", "args": {"parametro_mal_escrito": 1}, "id": "call_1"},
        ])])
        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(business_action_node(estado))
        self.assertEqual(resultado["tool_messages"][-1].status, "error")

    def test_no_repite_una_accion_ya_completada_con_exito_en_este_turno(self):
        from bot.flow.graph import business_action_node
        estado = self._state(tool_messages=[
            AIMessage(content="", tool_calls=[{"name": "simular_financiamiento", "args": {"precio": 1, "pie": 1, "plazo_meses": 1}, "id": "call_prev"}]),
            ToolMessage(content=json.dumps({"ok": True, "cuota_mensual_estimada": 999}), tool_call_id="call_prev", name="simular_financiamiento"),
            AIMessage(content="", tool_calls=[{"name": "simular_financiamiento", "args": {"precio": 1, "pie": 1, "plazo_meses": 1}, "id": "call_1"}]),
        ])
        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(business_action_node(estado))
        self.assertEqual(json.loads(resultado["tool_messages"][-1].content), {"ok": True, "cuota_mensual_estimada": 999})


class SpecialistNodeModeloImagenTest(TransactionTestCase):
    # TransactionTestCase (no TestCase) por el mismo motivo documentado en
    # GraphGetLlmOverrideTest/GraphCustomSpecialistTest: _agente_envios()
    # escribe CustomSpecialist/PromptVersion via ORM en el thread principal y
    # specialist_node los lee despues (agent.effective_prompt()) via
    # sync_to_async, en OTRO thread. TestCase (transaccion sin commitear)
    # bloquea esa lectura en sqlite shared-cache con "database table is
    # locked". El slug "envios" en vez de "ventas" sigue siendo a proposito
    # (ver el texto del plan): evita la lectura cruzada ADICIONAL via
    # get_conocimiento_del_sitio que si necesitan los tests de "ventas"/"faq",
    # pero no evita esta otra causa de cruce de threads, comun a cualquier
    # especialista custom con prompt guardado en BD.
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "hola", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": "envios", "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def _agente_envios(self):
        from bot.models import CustomSpecialist, save_prompt_version
        from bot.flow.agents.custom import CustomPromptAgent
        row = CustomSpecialist.objects.create(slug="envios", label="Envios", descripcion="estado de pedidos")
        save_prompt_version("custom:envios", "prompt de envios")
        return CustomPromptAgent(row)

    def _mock_get_llm_con_respuesta(self, mock_get_llm, content: str):
        # "envios" (CustomPromptAgent, slug != "ventas") tiene
        # business_actions() == [] -- una LISTA vacia, no un dict -- asi que
        # specialist_node lo manda por el camino nuevo de tool-calling
        # (_specialist_node_con_tools), aunque nunca reciba tool_calls de
        # verdad (Task 5: CustomPromptAgent ya no tiene build_prompt para
        # NINGUN slug, solo build_system_prompt). Por eso el mock ya no es
        # sobre _ainvoke_with_retry (esa funcion no se llama nunca en este
        # camino) sino sobre _get_llm -> bind_tools -> ainvoke, igual que en
        # SpecialistNodeConToolsTest.
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(content=content, tool_calls=[])
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

    @patch("bot.flow.graph._get_llm")
    def test_modelo_imagen_se_propaga_si_el_intent_es_de_exhibicion(self, mock_get_llm):
        self._mock_get_llm_con_respuesta(mock_get_llm, (
            '{"mensaje": "te muestro el Koleos", '
            '"extracted_data": {}, "next_state": null, "handoff": false, "modelo_imagen": "koleos", '
            '"intent": "explorar"}'
        ))
        with patch("bot.flow.graph.build_agent_registry", return_value={"envios": self._agente_envios()}):
            result = asyncio.run(specialist_node(self._initial_state()))
        self.assertEqual(result["modelo_imagen"], "koleos")

    @patch("bot.flow.graph._get_llm")
    def test_modelo_imagen_se_propaga_en_el_return_de_handoff(self, mock_get_llm):
        self._mock_get_llm_con_respuesta(mock_get_llm, (
            '{"mensaje": "te derivo", '
            '"extracted_data": {}, "next_state": null, "handoff": true, "modelo_imagen": "arkana", '
            '"intent": "test_drive"}'
        ))
        with patch("bot.flow.graph.build_agent_registry", return_value={"envios": self._agente_envios()}):
            result = asyncio.run(specialist_node(self._initial_state()))
        self.assertEqual(result["modelo_imagen"], "arkana")

    @patch("bot.flow.graph._get_llm")
    def test_sin_modelo_imagen_en_la_respuesta_el_campo_queda_none(self, mock_get_llm):
        self._mock_get_llm_con_respuesta(mock_get_llm, (
            '{"mensaje": "hola", '
            '"extracted_data": {}, "next_state": null, "handoff": false, "intent": "explorar"}'
        ))
        with patch("bot.flow.graph.build_agent_registry", return_value={"envios": self._agente_envios()}):
            result = asyncio.run(specialist_node(self._initial_state()))
        self.assertIsNone(result["modelo_imagen"])

    @patch("bot.flow.graph._get_llm")
    def test_modelo_imagen_se_suprime_si_el_intent_no_es_de_exhibicion(self, mock_get_llm):
        # Bug real detectado en revision manual: el LLM mencionaba "Arkana" de
        # pasada (ej. negociando parte de pago) y marcaba modelo_imagen igual,
        # aunque el turno no fuera realmente sobre mostrar el auto. El unico
        # guardrail existente (_imagen_enviada_recientemente en handlers.py) es
        # anti-duplicado por ventana de mensajes, no anti-irrelevancia -- asi
        # que la imagen se mandaba igual apenas pasaban 20 mensajes.
        self._mock_get_llm_con_respuesta(mock_get_llm, (
            '{"mensaje": "el pie se calcula sobre el valor de tasacion de su auto actual", '
            '"extracted_data": {}, '
            '"next_state": null, "handoff": false, "modelo_imagen": "arkana", "intent": "financiar"}'
        ))
        with patch("bot.flow.graph.build_agent_registry", return_value={"envios": self._agente_envios()}):
            result = asyncio.run(specialist_node(self._initial_state()))
        self.assertIsNone(result["modelo_imagen"])

    @patch("bot.flow.graph._get_llm")
    def test_modelo_imagen_se_suprime_si_falta_el_intent(self, mock_get_llm):
        # Ante una respuesta malformada/sin "intent" (ej. otro CustomSpecialist
        # sin este campo en su contrato), el default seguro es NO mandar la
        # imagen -- preferible perder un envio valido a mandar uno irrelevante.
        self._mock_get_llm_con_respuesta(mock_get_llm, (
            '{"mensaje": "hola", '
            '"extracted_data": {}, "next_state": null, "handoff": false, "modelo_imagen": "arkana"}'
        ))
        with patch("bot.flow.graph.build_agent_registry", return_value={"envios": self._agente_envios()}):
            result = asyncio.run(specialist_node(self._initial_state()))
        self.assertIsNone(result["modelo_imagen"])


# SpecialistNodeSinToolsBridgeTest (que vivia aca) se borra en Task 11: probaba
# el bridge transicional de la Task 8 en _specialist_node_sin_tools que
# traducia business_action/business_params (JSON de texto) a un
# pending_tool_call sintetico para agendamiento/confirmacion antes de que
# migraran a tool-calling nativo. Esas migraciones ya se hicieron (Tasks 9/10)
# y el bridge en si se borro en esta misma task (ver docstring de
# _specialist_node_sin_tools) -- no queda ningun consumidor real que probar.


class SpecialistNodeConToolsTest(TransactionTestCase):
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "hola", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": "ventas", "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [], "modelo_imagen": None,
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def _agente_ventas(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.models import CustomSpecialist, save_prompt_version
        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="x")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        return CustomPromptAgent(row)

    @patch("bot.flow.graph._get_llm")
    def test_con_tool_call_anexa_aimessage_al_canal_y_despacha_a_business_action(self, mock_get_llm):
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content="", tool_calls=[{"name": "simular_financiamiento", "args": {"precio": 100, "pie": 20, "plazo_meses": 36}, "id": "call_1"}],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(specialist_node(self._initial_state()))

        # Nota: langchain.messages.AIMessage normaliza cada tool_call agregando
        # "type": "tool_call" (no documentado en el brief original, que
        # esperaba el dict tal cual venia del constructor) -- se incluye aca
        # porque es el shape real que devuelve la libreria, no un cambio de
        # intencion de lo que se esta probando (que el canal capture el
        # tool_call correcto).
        nuevo = resultado["tool_messages"][-1]
        self.assertIsInstance(nuevo, AIMessage)
        self.assertEqual(nuevo.tool_calls, [{
            "name": "simular_financiamiento", "args": {"precio": 100, "pie": 20, "plazo_meses": 36},
            "id": "call_1", "type": "tool_call",
        }])
        self.assertEqual(resultado["_dispatch"], "business_action")

    @patch("bot.flow.graph._get_llm")
    def test_con_varios_tool_calls_los_pasa_todos_a_business_action(self, mock_get_llm):
        # ToolNode y el subgrafo interno de business_action ya operan sobre
        # listas de tool_calls (ver _filtrar_acciones_repetidas/_construir_subgrafo_tools),
        # asi que truncar a [0] descartaba silenciosamente el resto sin darle
        # ninguna senal de vuelta al LLM -- una pregunta con dos acciones en
        # el mismo mensaje se resolvia solo a medias.
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content="", tool_calls=[
                {"name": "simular_financiamiento", "args": {}, "id": "call_1"},
                {"name": "crear_lead", "args": {}, "id": "call_2"},
            ],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(specialist_node(self._initial_state()))

        ids = [tc["id"] for tc in resultado["tool_messages"][-1].tool_calls]
        self.assertEqual(ids, ["call_1", "call_2"])

    @patch("bot.flow.graph._get_llm")
    def test_sin_tool_calls_parsea_el_json_final_y_termina_el_turno(self, mock_get_llm):
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "hola, en que le ayudo?", "intent": "explorar", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(specialist_node(self._initial_state()))

        self.assertEqual(resultado["response_text"], "hola, en que le ayudo?")
        self.assertEqual(resultado["_dispatch"], "END")
        # El return normal (sin tool_calls) no toca el canal -- no hay ninguna
        # accion nueva que anexar (ver comentario en _specialist_node_con_tools).
        self.assertNotIn("tool_messages", resultado)

    @patch("bot.flow.graph._get_llm")
    def test_segunda_vuelta_con_resultado_real_incluye_toolmessage_en_los_mensajes(self, mock_get_llm):
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "la cuota estimada es $500.000", "intent": "financiar", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        estado = self._initial_state(tool_messages=[
            AIMessage(content="", tool_calls=[{"name": "simular_financiamiento", "args": {"precio": 100}, "id": "call_1"}]),
            ToolMessage(content=json.dumps({"ok": True, "cuota_mensual_estimada": 500000}), tool_call_id="call_1", name="simular_financiamiento"),
        ])
        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(specialist_node(estado))

        self.assertEqual(resultado["response_text"], "la cuota estimada es $500.000")
        mensajes_enviados = llm_con_tools.ainvoke.call_args[0][0]
        self.assertTrue(any(isinstance(m, ToolMessage) and m.tool_call_id == "call_1" for m in mensajes_enviados))

    @patch("bot.flow.graph._get_llm")
    def test_content_json_valido_pero_no_dict_no_rompe_el_turno(self, mock_get_llm):
        # Reproduce el crash real: el LLM devolvio un string JSON valido
        # (comillas incluidas) en vez de un objeto. Antes del guard en
        # _parse_json_response, esto propagaba un `str` hasta
        # `parsed.get("extracted_data")` y reventaba con
        # "AttributeError: 'str' object has no attribute 'get'" -- y como
        # bot/whatsapp/handlers.py solo atrapa GraphRecursionError, el
        # cliente se quedaba sin ninguna respuesta, ni siquiera el fallback
        # generico.
        from bot.flow.graph import RESPUESTA_GENERICA_JSON_INVALIDO
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(content='"hola"', tool_calls=[])
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(specialist_node(self._initial_state()))

        self.assertEqual(resultado["response_text"], RESPUESTA_GENERICA_JSON_INVALIDO)
        self.assertEqual(resultado["_dispatch"], "END")


class SupervisorIntencionCompraTest(TransactionTestCase):
    # TransactionTestCase por el mismo motivo que GraphCustomSpecialistTest:
    # este test escribe un CustomSpecialist via ORM en el thread principal y
    # despues lo lee dentro del grafo via sync_to_async (build_agent_registry),
    # en OTRO thread. TestCase (transaccion sin commitear) bloquearia esa
    # lectura en sqlite shared-cache.
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "quiero avanzar", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def _run(self, graph, state):
        return asyncio.run(graph.ainvoke(state))

    def _crear_ventas(self):
        from bot.models import CustomSpecialist, save_prompt_version
        CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_intencion_compra_real_true_promueve_a_ventas_desde_agente_activo(self, mock_llm, mock_get_llm):
        # Promociona a "ventas", que tiene tools reales -> specialist_node lo
        # manda por el camino de tool-calling (necesita el mock de _get_llm
        # ademas del de _ainvoke_with_retry, que solo cubre la clasificacion
        # del supervisor). Ver comentario en GraphCustomSpecialistTest.
        self._crear_ventas()
        mock_llm.side_effect = ['{"agente": "agendamiento", "intencion_compra_real": true}']
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "listo, avancemos", '
                    '"extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(active_agent="agendamiento"))

        self.assertEqual(result["active_agent"], "ventas")
        self.assertEqual(result["response_text"], "listo, avancemos")

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_intencion_compra_real_true_sin_ventas_en_registro_cae_a_chosen_normal(self, mock_llm, mock_get_llm):
        # No se crea CustomSpecialist "ventas" -- el registro solo tiene los
        # agentes estaticos (agendamiento, confirmacion, faq). "faq" esta en
        # el camino de tools (Task 7 del plan de RAG agentico) -- necesita el
        # mock de _get_llm ademas del de _ainvoke_with_retry (que solo cubre
        # la clasificacion del supervisor).
        mock_llm.return_value = '{"agente": "faq", "intencion_compra_real": true}'
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "aca tiene la info", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state())

        self.assertEqual(result["active_agent"], "faq")

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_intencion_compra_real_false_sin_cambio(self, mock_llm, mock_get_llm):
        # El agente elegido/mantenido es "agendamiento" (Task 9, camino de
        # tools) -- necesita el mock de _get_llm ademas del de
        # _ainvoke_with_retry (que solo cubre la clasificacion del
        # supervisor), igual que en test_intencion_compra_real_true_promueve...
        self._crear_ventas()
        mock_llm.side_effect = ['{"agente": "agendamiento", "intencion_compra_real": false}']
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "dale, para cuando?", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(active_agent="agendamiento"))

        self.assertEqual(result["active_agent"], "agendamiento")

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_intencion_compra_real_ausente_sin_cambio(self, mock_llm, mock_get_llm):
        self._crear_ventas()
        mock_llm.side_effect = ['{"agente": "agendamiento"}']
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "dale, para cuando?", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(active_agent="agendamiento"))

        self.assertEqual(result["active_agent"], "agendamiento")

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_agente_invalido_con_intencion_compra_real_true_termina_en_ventas(self, mock_llm, mock_get_llm):
        self._crear_ventas()
        mock_llm.side_effect = ['{"agente": "no_existe", "intencion_compra_real": true}']
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "listo, avancemos", '
                    '"extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state())

        self.assertEqual(result["active_agent"], "ventas")


class SpecialistNodePropagaIntentTest(TestCase):
    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "cuanto cuesta", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": "faq", "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    @patch("bot.flow.graph._get_llm")
    def test_propaga_el_intent_crudo_del_llm_en_la_rama_normal(self, mock_get_llm):
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content=(
                '{"mensaje": "el Arkana cuesta $24.490.000", "intent": "cotizar", '
                '"extracted_data": {}, "next_state": null, "handoff": false}'
            ),
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        result = asyncio.run(specialist_node(self._initial_state()))
        self.assertEqual(result["intent"], "cotizar")

    @patch("bot.flow.graph._get_llm")
    def test_propaga_el_intent_crudo_del_llm_en_la_rama_de_handoff(self, mock_get_llm):
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "listo, ¿algo mas?", "intent": "cortesia", "handoff": true}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        result = asyncio.run(specialist_node(self._initial_state()))
        self.assertEqual(result["intent"], "cortesia")


class _FakeHTTPError(Exception):
    """Duck-type minimo de openrouter.errors.OpenRouterError -- ambos exponen
    `.status_code`, y el codigo de graph.py solo lee ese atributo (no importa
    el tipo real del SDK), asi que alcanza para probar la logica de
    backoff/fail-fast sin construir un httpx.Response real."""
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class AinvokeWithRetryBackoffTest(TestCase):
    # PENDIENTES.md: los reintentos no distinguian un 429/503 transitorio de
    # un error permanente (4xx de autenticacion/validacion) -- eran hasta 3
    # llamadas inmediatas sin backoff ante CUALQUIER Exception. Ahora: 429/5xx
    # (o cualquier excepcion sin status_code, ej. timeouts de red) reintenta
    # con un backoff corto; un 4xx que no sea 429 falla rapido, sin gastar los
    # reintentos restantes.
    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_429_reintenta_con_backoff_y_devuelve_el_resultado(self, mock_cb, mock_sleep):
        from bot.flow.graph import _ainvoke_with_retry
        llm = AsyncMock()
        bueno = MagicMock(content="ok")
        llm.ainvoke.side_effect = [_FakeHTTPError(429), bueno]
        resultado = asyncio.run(_ainvoke_with_retry(llm, "prompt", label="t"))
        self.assertEqual(resultado, "ok")
        self.assertEqual(llm.ainvoke.call_count, 2)
        mock_sleep.assert_awaited_once()

    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_503_reintenta_con_backoff(self, mock_cb, mock_sleep):
        from bot.flow.graph import _ainvoke_with_retry
        llm = AsyncMock()
        bueno = MagicMock(content="ok")
        llm.ainvoke.side_effect = [_FakeHTTPError(503), bueno]
        resultado = asyncio.run(_ainvoke_with_retry(llm, "prompt", label="t"))
        self.assertEqual(resultado, "ok")
        mock_sleep.assert_awaited_once()

    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_error_sin_status_code_se_trata_como_transitorio(self, mock_cb, mock_sleep):
        # Timeout de red (httpx.ReadTimeout, TimeoutError, etc.) no trae
        # status_code -- debe reintentar igual que un 5xx, no fallar rapido.
        from bot.flow.graph import _ainvoke_with_retry
        llm = AsyncMock()
        bueno = MagicMock(content="ok")
        llm.ainvoke.side_effect = [TimeoutError("timeout de red"), bueno]
        resultado = asyncio.run(_ainvoke_with_retry(llm, "prompt", label="t"))
        self.assertEqual(resultado, "ok")
        mock_sleep.assert_awaited_once()

    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_401_falla_rapido_sin_gastar_reintentos(self, mock_cb, mock_sleep):
        from bot.flow.graph import _ainvoke_with_retry
        llm = AsyncMock()
        llm.ainvoke.side_effect = [_FakeHTTPError(401), MagicMock(content="nunca llega")]
        with self.assertRaises(_FakeHTTPError):
            asyncio.run(_ainvoke_with_retry(llm, "prompt", label="t", max_retries=2))
        self.assertEqual(llm.ainvoke.call_count, 1)
        mock_sleep.assert_not_awaited()

    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_400_falla_rapido_sin_gastar_reintentos(self, mock_cb, mock_sleep):
        from bot.flow.graph import _ainvoke_with_retry
        llm = AsyncMock()
        llm.ainvoke.side_effect = _FakeHTTPError(400)
        with self.assertRaises(_FakeHTTPError):
            asyncio.run(_ainvoke_with_retry(llm, "prompt", label="t", max_retries=2))
        self.assertEqual(llm.ainvoke.call_count, 1)

    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_429_persistente_agota_reintentos_y_propaga(self, mock_cb, mock_sleep):
        from bot.flow.graph import _ainvoke_with_retry
        llm = AsyncMock()
        llm.ainvoke.side_effect = _FakeHTTPError(429)
        with self.assertRaises(_FakeHTTPError):
            asyncio.run(_ainvoke_with_retry(llm, "prompt", label="t", max_retries=2))
        self.assertEqual(llm.ainvoke.call_count, 3)
        self.assertEqual(mock_sleep.await_count, 2)


class AinvokeMessagesWithRetryBackoffTest(TestCase):
    # Mismo comportamiento que AinvokeWithRetryBackoffTest, pero para el path
    # de tool-calling (_ainvoke_messages_with_retry devuelve el AIMessage
    # completo, no solo .content).
    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_429_reintenta_con_backoff_y_devuelve_el_aimessage(self, mock_cb, mock_sleep):
        from bot.flow.graph import _ainvoke_messages_with_retry
        llm = AsyncMock()
        bueno = AIMessage(content="ok", tool_calls=[])
        llm.ainvoke.side_effect = [_FakeHTTPError(429), bueno]
        resultado = asyncio.run(_ainvoke_messages_with_retry(llm, [], label="t"))
        self.assertIs(resultado, bueno)
        mock_sleep.assert_awaited_once()

    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_403_falla_rapido_sin_gastar_reintentos(self, mock_cb, mock_sleep):
        from bot.flow.graph import _ainvoke_messages_with_retry
        llm = AsyncMock()
        llm.ainvoke.side_effect = _FakeHTTPError(403)
        with self.assertRaises(_FakeHTTPError):
            asyncio.run(_ainvoke_messages_with_retry(llm, [], label="t", max_retries=2))
        self.assertEqual(llm.ainvoke.call_count, 1)
        mock_sleep.assert_not_awaited()


class AinvokeToolsJsonWithRetryTest(TestCase):
    @patch("bot.flow.graph.CallbackHandler")
    def test_devuelve_el_aimessage_directo_si_trae_tool_calls(self, mock_cb):
        from bot.flow.graph import _ainvoke_tools_json_with_retry
        ai_msg = AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}])
        llm = AsyncMock()
        llm.ainvoke.return_value = ai_msg
        resultado = asyncio.run(_ainvoke_tools_json_with_retry(llm, [], label="t"))
        self.assertIs(resultado, ai_msg)
        llm.ainvoke.assert_called_once()

    @patch("bot.flow.graph.CallbackHandler")
    def test_devuelve_el_aimessage_directo_si_el_content_es_json_valido(self, mock_cb):
        from bot.flow.graph import _ainvoke_tools_json_with_retry
        ai_msg = AIMessage(content='{"mensaje": "hola"}', tool_calls=[])
        llm = AsyncMock()
        llm.ainvoke.return_value = ai_msg
        resultado = asyncio.run(_ainvoke_tools_json_with_retry(llm, [], label="t"))
        self.assertIs(resultado, ai_msg)
        llm.ainvoke.assert_called_once()

    @patch("bot.flow.graph.CallbackHandler")
    def test_reintenta_si_no_hay_tool_calls_y_el_content_no_es_json(self, mock_cb):
        from bot.flow.graph import _ainvoke_tools_json_with_retry
        malo = AIMessage(content="no es json", tool_calls=[])
        bueno = AIMessage(content='{"mensaje": "ok"}', tool_calls=[])
        llm = AsyncMock()
        llm.ainvoke.side_effect = [malo, bueno]
        resultado = asyncio.run(_ainvoke_tools_json_with_retry(llm, [], label="t", max_retries=1))
        self.assertIs(resultado, bueno)
        self.assertEqual(llm.ainvoke.call_count, 2)


class PresupuestoDelTurnoTest(TestCase):
    """Deadline por turno sobre el tiempo de LLM (docs/PENDIENTES.md #14,
    causa raiz 3 -- la unica que quedo abierta).

    El bug que estos tests cierran: _ainvoke_messages_with_retry hace 3
    intentos de 30s con 2 backoffs de 1,5s (93s) y _ainvoke_tools_json_with_retry
    lo llama hasta 3 veces ENCIMA (279s), mas el ruteo (48s). Todo eso pasa
    dentro de UNA request de gunicorn, que corre con --timeout 120: pasado ese
    plazo el worker muere y el contacto no recibe absolutamente nada -- el peor
    modo de falla del bot.
    """

    def _armar_presupuesto(self, segundos: float):
        """Arma el vencimiento del turno a `segundos` de ahora y lo desarma al
        terminar el test (un ContextVar seteado a mano se filtra al resto de
        los tests del mismo hilo si no se resetea)."""
        import time
        from bot.flow.graph import _vencimiento_del_turno
        token = _vencimiento_del_turno.set(time.monotonic() + segundos)
        self.addCleanup(_vencimiento_del_turno.reset, token)

    def test_el_presupuesto_de_llm_mas_la_reserva_entran_en_el_timeout_de_gunicorn(self):
        # Guarda de aritmetica, no de comportamiento: es exactamente la cuenta
        # que el docstring de _get_llm declaraba mal (decia 90s cuando el peor
        # caso real eran 279s). Si alguien sube el presupuesto o baja la
        # reserva hasta pasarse de los 120s del Dockerfile, esto se cae.
        from bot.flow.graph import (
            _PRESUPUESTO_LLM_TURNO_SEGUNDOS, _RESERVA_NO_LLM_SEGUNDOS,
            _TIMEOUT_GUNICORN_SEGUNDOS, _TIMEOUT_LLM_SEGUNDOS,
        )
        self.assertLessEqual(
            _PRESUPUESTO_LLM_TURNO_SEGUNDOS + _RESERVA_NO_LLM_SEGUNDOS,
            _TIMEOUT_GUNICORN_SEGUNDOS,
        )
        # Y el presupuesto tiene que dar para al menos un intento completo:
        # el deadline nuevo va ENCIMA del tope por intento, no en lugar de el.
        self.assertGreater(_PRESUPUESTO_LLM_TURNO_SEGUNDOS, _TIMEOUT_LLM_SEGUNDOS)

    def test_sin_presupuesto_armado_el_tope_por_intento_es_el_de_siempre(self):
        # Fuera de un turno (tests, scripts, el simulador) no hay deadline y el
        # tope por intento tiene que seguir valiendo tal cual.
        from bot.flow.graph import _TIMEOUT_LLM_SEGUNDOS, _tope_del_intento
        self.assertEqual(_tope_del_intento(_TIMEOUT_LLM_SEGUNDOS), _TIMEOUT_LLM_SEGUNDOS)

    def test_con_presupuesto_de_sobra_el_tope_por_intento_sigue_siendo_el_techo(self):
        from bot.flow.graph import _TIMEOUT_LLM_SEGUNDOS, _tope_del_intento
        self._armar_presupuesto(1000)
        self.assertEqual(_tope_del_intento(_TIMEOUT_LLM_SEGUNDOS), _TIMEOUT_LLM_SEGUNDOS)

    def test_el_tope_por_intento_se_recorta_a_lo_que_queda_del_presupuesto(self):
        from bot.flow.graph import _TIMEOUT_LLM_SEGUNDOS, _tope_del_intento
        self._armar_presupuesto(5)
        tope = _tope_del_intento(_TIMEOUT_LLM_SEGUNDOS)
        self.assertLessEqual(tope, 5)
        self.assertGreater(tope, 4)

    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_presupuesto_agotado_no_gasta_ni_una_llamada_mas_en_el_bucle_interno(self, mock_cb, mock_sleep):
        from bot.flow.graph import _ainvoke_messages_with_retry
        self._armar_presupuesto(-1)
        llm = AsyncMock()
        llm.ainvoke.return_value = AIMessage(content="nunca llega", tool_calls=[])
        with self.assertRaises(TimeoutError):
            asyncio.run(_ainvoke_messages_with_retry(llm, [], label="t"))
        self.assertEqual(llm.ainvoke.call_count, 0)

    @patch("bot.flow.graph.asyncio.sleep", new_callable=AsyncMock)
    @patch("bot.flow.graph.CallbackHandler")
    def test_presupuesto_agotado_tambien_corta_el_ruteo(self, mock_cb, mock_sleep):
        # El ruteo gasta del MISMO presupuesto: es la primera llamada del turno
        # y sus 3 intentos de 15s tambien cuentan contra los 120s de gunicorn.
        from bot.flow.graph import _ainvoke_with_retry
        self._armar_presupuesto(-1)
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="nunca llega")
        with self.assertRaises(TimeoutError):
            asyncio.run(_ainvoke_with_retry(llm, "prompt", label="t"))
        self.assertEqual(llm.ainvoke.call_count, 0)

    @patch("bot.flow.graph.CallbackHandler")
    def test_el_bucle_interno_devuelve_el_control_dentro_del_presupuesto_real(self, mock_cb):
        # Sin mockear asyncio.sleep ni el reloj: se mide el tiempo de pared de
        # verdad. Con 0,3s de presupuesto y un LLM que tarda 5s, la funcion
        # tiene que volver en ~0,3s y no en 5s -- ni gastar los backoffs que ya
        # no entran en el presupuesto.
        import time
        from bot.flow.graph import _ainvoke_messages_with_retry

        async def _lento(*args, **kwargs):
            await asyncio.sleep(5)
            return AIMessage(content="tarde", tool_calls=[])

        self._armar_presupuesto(0.3)
        llm = AsyncMock()
        llm.ainvoke.side_effect = _lento
        arranque = time.monotonic()
        with self.assertRaises(TimeoutError):
            asyncio.run(_ainvoke_messages_with_retry(llm, [], label="t"))
        self.assertLess(time.monotonic() - arranque, 2)

    @patch("bot.flow.graph.CallbackHandler")
    def test_el_bucle_externo_deja_de_reintentar_formato_al_agotarse_el_presupuesto(self, mock_cb):
        # Con el presupuesto gastado, el reintento de formato NO arranca otro
        # ciclo: devuelve la ultima respuesta inutilizable, que es como
        # specialist_node termina emitiendo RESPUESTA_GENERICA_JSON_INVALIDO.
        # Mismo final que ya tiene hoy agotar los reintentos de formato: el
        # cliente recibe un mensaje, no silencio.
        import time
        from bot.flow.graph import _ainvoke_tools_json_with_retry, _vencimiento_del_turno
        malo = AIMessage(content="no es json ni prosa {", tool_calls=[])

        async def _gasta_el_presupuesto(*args, **kwargs):
            _vencimiento_del_turno.set(time.monotonic() - 1)
            return malo

        self._armar_presupuesto(60)
        llm = AsyncMock()
        llm.ainvoke.side_effect = _gasta_el_presupuesto
        resultado = asyncio.run(_ainvoke_tools_json_with_retry(llm, [], label="t", max_retries=2))
        self.assertIs(resultado, malo)
        self.assertEqual(llm.ainvoke.call_count, 1)

    def test_el_grafo_arma_el_presupuesto_antes_de_correr_los_nodos(self):
        # LangGraph corre cada nodo en su propia asyncio.Task y una Task COPIA
        # el contextvar al crearse: un .set() hecho adentro de un nodo no lo ve
        # el nodo siguiente (verificado contra la libreria instalada). Por eso
        # el presupuesto se arma en el borde de entrada del grafo, que es lo
        # unico que corre en el contexto del llamador.
        from bot.flow.graph import (
            _PRESUPUESTO_LLM_TURNO_SEGUNDOS, _GrafoConPresupuestoDeTurno,
            _segundos_restantes_del_turno,
        )
        visto = {}

        class _GrafoFalso:
            marca = "delegado"

            async def ainvoke(self, entrada, config=None):
                visto["restante"] = _segundos_restantes_del_turno()
                return {"ok": True}

        envuelto = _GrafoConPresupuestoDeTurno(_GrafoFalso())
        # Fuera del turno no hay presupuesto armado.
        self.assertIsNone(_segundos_restantes_del_turno())
        self.assertEqual(asyncio.run(envuelto.ainvoke({}, config={})), {"ok": True})
        self.assertIsNotNone(visto["restante"])
        self.assertLessEqual(visto["restante"], _PRESUPUESTO_LLM_TURNO_SEGUNDOS)
        self.assertGreater(visto["restante"], _PRESUPUESTO_LLM_TURNO_SEGUNDOS - 1)
        # Y se desarma al terminar el turno: el presupuesto es por turno, no
        # del proceso.
        self.assertIsNone(_segundos_restantes_del_turno())
        # Todo lo que no sea ainvoke se delega al grafo real sin tocar.
        self.assertEqual(envuelto.marca, "delegado")

    def test_get_flow_graph_devuelve_el_grafo_con_presupuesto(self):
        from bot.flow.graph import _GrafoConPresupuestoDeTurno, get_flow_graph
        self.assertIsInstance(get_flow_graph(), _GrafoConPresupuestoDeTurno)


class ConstruirMensajesTest(TestCase):
    def test_arma_system_historial_y_mensaje_actual_en_orden(self):
        from bot.flow.graph import _construir_mensajes
        state = {
            "messages": [
                {"role": "user", "content": "hola"},
                {"role": "assistant", "content": "hola, en que le ayudo?"},
            ],
            "text": "quiero cotizar un auto",
            "tool_messages": [],
        }
        mensajes = _ventana_sin_recordatorio(_construir_mensajes(state, "Eres un asistente de ventas."))
        self.assertEqual(len(mensajes), 4)
        self.assertIsInstance(mensajes[0], SystemMessage)
        self.assertEqual(mensajes[0].content, "Eres un asistente de ventas.")
        self.assertIsInstance(mensajes[1], HumanMessage)
        self.assertEqual(mensajes[1].content, "hola")
        self.assertIsInstance(mensajes[2], AIMessage)
        self.assertEqual(mensajes[2].content, "hola, en que le ayudo?")
        self.assertIsInstance(mensajes[3], HumanMessage)
        self.assertEqual(mensajes[3].content, "quiero cotizar un auto")

    def test_con_tool_messages_resueltos_agrega_aimessage_y_toolmessage(self):
        from bot.flow.graph import _construir_mensajes
        state = {
            "messages": [],
            "text": "cotiza el Arkana",
            "tool_messages": [
                AIMessage(content="", tool_calls=[{"name": "simular_financiamiento", "args": {"precio": 100}, "id": "call_1"}]),
                ToolMessage(content=json.dumps({"ok": True, "cuota_mensual_estimada": 500000}), tool_call_id="call_1", name="simular_financiamiento"),
            ],
        }
        mensajes = _ventana_sin_recordatorio(_construir_mensajes(state, "system"))
        self.assertEqual(len(mensajes), 4)  # system, human(text), ai(tool_call), tool(resultado)
        self.assertIsInstance(mensajes[2], AIMessage)
        # Verificar que el tool_call contiene los campos esperados (LangChain puede agregar 'type')
        self.assertEqual(len(mensajes[2].tool_calls), 1)
        self.assertEqual(mensajes[2].tool_calls[0]["name"], "simular_financiamiento")
        self.assertEqual(mensajes[2].tool_calls[0]["id"], "call_1")
        self.assertEqual(mensajes[2].tool_calls[0]["args"], {"precio": 100})
        self.assertIsInstance(mensajes[3], ToolMessage)
        self.assertEqual(mensajes[3].tool_call_id, "call_1")
        self.assertEqual(mensajes[3].name, "simular_financiamiento")
        self.assertIn("500000", mensajes[3].content)

    def test_sin_tool_messages_no_agrega_aimessage_ni_toolmessage(self):
        from bot.flow.graph import _construir_mensajes
        mensajes = _ventana_sin_recordatorio(_construir_mensajes(
            {"messages": [], "text": "hola", "tool_messages": []},
            "system",
        ))
        self.assertEqual(len(mensajes), 2)  # system, human


class ConstruirMensajesAcumulaHistorialTest(TestCase):
    def test_tool_messages_se_incluyen_todos_no_solo_el_ultimo(self):
        from bot.flow.graph import _construir_mensajes
        primera_llamada = AIMessage(content="", tool_calls=[{"name": "listar_catalogo", "args": {}, "id": "call_1"}])
        primer_resultado = ToolMessage(content='{"ok": true, "servicios": []}', tool_call_id="call_1", name="listar_catalogo")
        segunda_llamada = AIMessage(content="", tool_calls=[{"name": "consultar_disponibilidad", "args": {"servicio_id": 1}, "id": "call_2"}])
        segundo_resultado = ToolMessage(content='{"ok": true, "horas": []}', tool_call_id="call_2", name="consultar_disponibilidad")
        state = {
            "messages": [], "text": "agendame una hora",
            "tool_messages": [primera_llamada, primer_resultado, segunda_llamada, segundo_resultado],
        }
        mensajes = _ventana_sin_recordatorio(_construir_mensajes(state, "system"))
        # system, human(text), + los 4 mensajes de tool-calling acumulados, EN ORDEN
        self.assertEqual(len(mensajes), 6)
        self.assertIs(mensajes[2], primera_llamada)
        self.assertIs(mensajes[3], primer_resultado)
        self.assertIs(mensajes[4], segunda_llamada)
        self.assertIs(mensajes[5], segundo_resultado)

    def test_sin_tool_messages_no_agrega_nada_extra(self):
        from bot.flow.graph import _construir_mensajes
        mensajes = _ventana_sin_recordatorio(
            _construir_mensajes({"messages": [], "text": "hola", "tool_messages": []}, "system"))
        self.assertEqual(len(mensajes), 2)


class BusinessActionNodeEmiteToolMessageSiempreTest(TransactionTestCase):
    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _agente_ventas(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.models import CustomSpecialist, save_prompt_version
        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="x")
        save_prompt_version("custom:ventas", "prompt")
        return CustomPromptAgent(row)

    def test_accion_ejecutada_anexa_un_toolmessage_al_canal(self):
        from bot.flow.graph import business_action_node
        llamada = AIMessage(content="", tool_calls=[{
            "name": "simular_financiamiento",
            "args": {"precio": 20_000_000, "pie": 4_000_000, "plazo_meses": 36},
            "id": "call_1",
        }])
        estado = {"active_agent": "ventas", "tool_messages": [llamada]}
        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(business_action_node(estado))
        nuevos = resultado["tool_messages"]
        self.assertEqual(len(nuevos), 1)
        self.assertIsInstance(nuevos[0], ToolMessage)
        self.assertEqual(nuevos[0].tool_call_id, "call_1")
        # El brief original comparaba contra '"ok": true' luego de stripear
        # espacios del content -- una comparacion que nunca podia matchear
        # (el espacio que busca ya fue eliminado). Se parsea el JSON en vez
        # de comparar substrings: mismo intento (ok=true), mecanica corregida.
        self.assertIs(json.loads(nuevos[0].content).get("ok"), True)

    def test_accion_desconocida_igual_emite_un_toolmessage_con_error(self):
        # Antes de esta task, action=None resultaba en business_result=None y
        # _construir_mensajes OMITIA el par entero (el LLM nunca se enteraba
        # de que algo fallo) -- ahora siempre hay un ToolMessage, aunque sea
        # de error, para que el LLM tenga contexto real en vez de nada.
        from bot.flow.graph import business_action_node
        llamada = AIMessage(content="", tool_calls=[{"name": "accion_que_no_existe", "args": {}, "id": "call_1"}])
        estado = {"active_agent": "ventas", "tool_messages": [llamada]}
        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(business_action_node(estado))
        nuevos = resultado["tool_messages"]
        self.assertEqual(len(nuevos), 1)
        self.assertIsInstance(nuevos[0], ToolMessage)
        self.assertEqual(nuevos[0].status, "error")

    def test_no_repite_una_accion_ya_completada_con_exito_este_turno(self):
        from bot.flow.graph import business_action_node
        llamada_previa = AIMessage(content="", tool_calls=[{"name": "simular_financiamiento", "args": {"precio": 1, "pie": 1, "plazo_meses": 1}, "id": "call_1"}])
        resultado_previo = ToolMessage(content='{"ok": true, "cuota_mensual_estimada": 999}', tool_call_id="call_1", name="simular_financiamiento")
        llamada_repetida = AIMessage(content="", tool_calls=[{"name": "simular_financiamiento", "args": {"precio": 1, "pie": 1, "plazo_meses": 1}, "id": "call_2"}])
        estado = {"active_agent": "ventas", "tool_messages": [llamada_previa, resultado_previo, llamada_repetida]}
        with patch("bot.flow.graph.build_agent_registry", return_value={"ventas": self._agente_ventas()}):
            resultado = asyncio.run(business_action_node(estado))
        nuevos = resultado["tool_messages"]
        self.assertEqual(len(nuevos), 1)
        self.assertIn("999", nuevos[0].content)


class ParseJsonResponseContenidoListaTest(TestCase):
    def test_parsea_content_shape_lista_de_gemini_real(self):
        # ai_msg.content de ChatGoogleGenerativeAI puede venir como lista de
        # content blocks (no un str plano) -- confirmado 2/2 veces contra
        # Gemini real, ver bot/simulator/judge.py::_texto_de_respuesta
        # (commit 1e9cbc6) para el mismo bug ya encontrado en el simulador.
        from bot.flow.graph import _parse_json_response
        raw = [{"type": "text", "text": '{"mensaje": "hola", "intent": null}', "extras": {}}]
        self.assertEqual(_parse_json_response(raw), {"mensaje": "hola", "intent": None})

    def test_sigue_funcionando_con_un_str_plano_sin_regresion(self):
        from bot.flow.graph import _parse_json_response
        self.assertEqual(_parse_json_response('{"a": 1}'), {"a": 1})

    def test_json_valido_pero_no_dict_string_devuelve_dict_vacio(self):
        # json.loads('"hola"') es JSON perfectamente valido, pero devuelve un
        # str, no un dict -- _parse_json_response esta anotada -> dict, asi
        # que debe tratar esto como respuesta invalida en vez de propagar un
        # str a los llamadores (AttributeError real reproducido en
        # _specialist_node_con_tools, bot/flow/graph.py:478, ver tambien el
        # test de integracion en SpecialistNodeConToolsTest mas abajo).
        from bot.flow.graph import _parse_json_response
        self.assertEqual(_parse_json_response('"hola"'), {})

    def test_json_valido_pero_no_dict_lista_devuelve_dict_vacio(self):
        from bot.flow.graph import _parse_json_response
        self.assertEqual(_parse_json_response('[1, 2]'), {})

    def test_json_valido_pero_no_dict_numero_devuelve_dict_vacio(self):
        from bot.flow.graph import _parse_json_response
        self.assertEqual(_parse_json_response('42'), {})

    def test_json_valido_pero_no_dict_null_devuelve_dict_vacio(self):
        from bot.flow.graph import _parse_json_response
        self.assertEqual(_parse_json_response('null'), {})

    def test_bloque_con_fence_json_sigue_funcionando(self):
        from bot.flow.graph import _parse_json_response
        raw = '```json\n{"a": 1}\n```'
        self.assertEqual(_parse_json_response(raw), {"a": 1})

    def test_rescate_de_texto_suelto_antes_del_json_sigue_funcionando(self):
        # Bug real documentado en el comentario de la funcion (financiamiento
        # del Koleos): el LLM piensa en voz alta antes de escribir el JSON.
        from bot.flow.graph import _parse_json_response
        raw = 'pensando en voz alta... {"a": 1}'
        self.assertEqual(_parse_json_response(raw), {"a": 1})

    def test_rescate_con_json_malformado_no_revienta(self):
        # El rescate toma el substring entre el primer "{" y el ULTIMO "}" --
        # si eso tampoco es JSON valido, debe caer al mismo guard sin
        # reventar (no es especificamente el guard de tipo, ya que el
        # substring siempre queda delimitado por "{"..."}" literales y por lo
        # tanto solo puede parsear como dict o fallar -- pero confirma que el
        # camino completo de la funcion sigue siendo prolijo).
        from bot.flow.graph import _parse_json_response
        raw = 'texto antes {"a": 1} y despues {"b": [1, 2, 3]} y mas texto'
        self.assertEqual(_parse_json_response(raw), {})


class CampanaComercialRoutingTest(TestCase):
    # Esta clase NO prueba que la entrada real este registrada en
    # bot/flow/campaign_rules.py -- eso es responsabilidad de
    # ProductionRoutingRulesTest en bot/tests/test_campaign_rules.py (esa
    # clase no tiene setUp/tearDown propios, asi que si alguien borra o
    # tipea mal la entrada real, ese test la detecta). Lo que esta clase
    # verifica es el plumbing del grafo dado un campaign_hint: que el nodo
    # supervisor NO se llame y que el active_agent resultante sea el
    # especialista correcto. Por eso fija la regla explicitamente en
    # setUp/tearDown en vez de depender del dict global de produccion
    # (varias clases mas arriba en este mismo archivo hacen
    # PRE_ROUTING_RULES.clear() en su tearDown sin restaurar el dict
    # original, asi que depender del estado ambiente seria no
    # determinista segun el orden de ejecucion).
    def setUp(self):
        PRE_ROUTING_RULES["servicio_tecnico_mantencion"] = CampaignRule(default_agent="agendamiento")

    def tearDown(self):
        PRE_ROUTING_RULES.pop("servicio_tecnico_mantencion", None)

    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "9", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": "servicio_tecnico_mantencion",
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [],
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def _run(self, graph, state):
        import asyncio
        return asyncio.run(graph.ainvoke(state))

    @patch("bot.flow.graph._get_llm")
    def test_campaign_hint_rutea_al_especialista_sin_llamar_al_supervisor(self, mock_get_llm):
        # Se usa un especialista de CODIGO (agendamiento) y no uno de BD: crear
        # el CustomSpecialist aca hace que el grafo, que corre sus nodos en otro
        # hilo via sync_to_async, choque con la transaccion del TestCase
        # ("database table is locked" en SQLite).
        from bot.flow.graph import get_flow_graph
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "hola", "extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state())

        self.assertEqual(result["active_agent"], "agendamiento")
        llm_con_tools.ainvoke.assert_called_once()


class ProsaConservaLosIdsDeSucursalTest(TransactionTestCase):
    """REGRESION real del refactor del canal de prosa (2026-09-03).

    El camino de prosa retornaba temprano sin `sucursal_direccion_ids`, asi que
    handlers.py recibia una lista vacia y el pin de ubicacion de WhatsApp NO se
    enviaba NUNCA. Reportado por otra sesion, que lo reprodujo contra el LLM
    real: `buscar_sucursales_cercanas` se llamaba 3/3 veces y el campo salia
    None las tres; antes del refactor daba [1] y el pin salia.

    El extractor de metadatos no lo recupera a proposito: los ids salen de los
    RESULTADOS de las tools, no de lo que el LLM escriba, asi que corresponde
    resolverlos de forma deterministica (_extraer_sucursal_ids_de_tools) y no
    pedirselos a un modelo.
    """

    def setUp(self):
        PRE_ROUTING_RULES.clear()
        from django.conf import settings as dj_settings
        from bot.models import Sucursal
        # cliente=CLIENTE_ACTIVO y no "cavem" fijo: el manager filtra por
        # cliente activo y la suite corre como renault (ver CLAUDE.md).
        self.sucursal = Sucursal.objects.create(
            nombre="Cavem La Reina", direccion="Av. Bilbao 1234",
            latitud=-33.44, longitud=-70.53,
            cliente=dj_settings.CLIENTE_ACTIVO,
        )

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    # Helpers propios en vez de heredar de GraphSmokeTest: esa clase es un
    # TestCase (envuelve cada test en una transaccion) y _validar_sucursal_ids
    # corre via sync_to_async en OTRO thread, asi que con sqlite la tabla queda
    # lockeada ("database table is locked: bot_sucursal"). TransactionTestCase
    # commitea de verdad y el thread ve la fila. Es el mismo motivo por el que
    # los otros tests del grafo que tocan la BD desde un nodo async ya usan
    # TransactionTestCase.
    def _initial_state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "donde queda la sucursal?", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [], "reply_to": None, "_dispatch": None,
            "metadatos_pendientes": False, "mensaje_cliente": "",
        }
        state.update(overrides)
        return state

    def _run(self, graph, state):
        import asyncio
        return asyncio.run(graph.ainvoke(state))

    @patch("bot.flow.graph._get_llm")
    def test_la_prosa_conserva_los_ids_que_devolvio_la_tool(self, mock_get_llm):
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="faq")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content=f"Nuestra sucursal Cavem La Reina queda en Av. Bilbao 1234. Te dejo la ubicacion.",
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        estado = self._initial_state(campaign_hint="agendar_hora")
        estado["tool_messages"] = [
            AIMessage(content="", tool_calls=[{
                "name": "buscar_sucursales_cercanas",
                "args": {"lugar": "La Reina"}, "id": "call_s1"}]),
            ToolMessage(
                content=json.dumps({"ok": True, "sucursales": [{"id": self.sucursal.pk}]}),
                tool_call_id="call_s1", name="buscar_sucursales_cercanas"),
        ]

        graph = get_flow_graph()
        result = self._run(graph, estado)

        self.assertTrue(result.get("metadatos_pendientes"), "deberia haber salido por prosa")
        self.assertEqual(result.get("sucursal_direccion_ids"), [self.sucursal.pk])

    @patch("bot.flow.graph._get_llm")
    def test_sin_tools_de_sucursal_la_lista_queda_vacia(self, mock_get_llm):
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(default_agent="faq")
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content="La garantia de los usados es de 6 meses o 10.000 km.", tool_calls=[])
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        graph = get_flow_graph()
        result = self._run(graph, self._initial_state(campaign_hint="agendar_hora"))

        self.assertEqual(result.get("sucursal_direccion_ids"), [])


class RazonamientoPorLlamadaTest(TransactionTestCase):
    """El especialista corre DOS veces por turno y las dos llamadas tienen
    trabajos distintos: la primera elige una herramienta (su salida entera es
    un tool_call que el cliente nunca ve) y la segunda redacta la respuesta.

    Medido el 2026-09-03 contra el LLM real, 12 turnos x 3 repeticiones:
    con el `effort` de hoy la primera llamada gasta 78 tokens de razonamiento
    de 129 de salida (150 de 266 en las trazas de produccion) y mide 1,68s;
    con `effort: none` mide 1,21s (-0,47s) y elige la MISMA herramienta en los
    10 turnos que necesitan una, incluidos los casos finos (comparar dos autos,
    ficha puntual contra busqueda, simulacion por cuota).

    La segunda llamada NO se toca: es la que escribe lo que el cliente lee, y
    bajarle el esfuerzo ya se probo y se descarto en docs/PENDIENTES.md #14
    (derivaba a voseo, erratas, y perdia el desglose del monto financiado).
    """

    def setUp(self):
        PRE_ROUTING_RULES.clear()

    def tearDown(self):
        PRE_ROUTING_RULES.clear()

    def _state(self, **overrides):
        state = {
            "wa_id": "56911112222", "text": "tienen suv bajo 15 millones?", "name": "Juan",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": "ventas", "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "tool_messages": [], "modelo_imagen": None,
            "reply_to": None, "_dispatch": None,
        }
        state.update(overrides)
        return state

    def _mock_llm(self, mock_get_llm):
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content="Tengo tres SUV en ese rango.", tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

    @patch("bot.flow.graph._get_llm")
    def test_primera_llamada_del_turno_no_razona(self, mock_get_llm):
        from bot.flow.graph import specialist_node
        from bot.models import CustomSpecialist, save_prompt_version
        CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="x")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        self._mock_llm(mock_get_llm)

        asyncio.run(specialist_node(self._state(tool_messages=[])))

        self.assertEqual(
            mock_get_llm.call_args.kwargs.get("reasoning"), {"effort": "none"},
            "la llamada que solo elige una herramienta no debe gastar razonamiento",
        )

    @patch("bot.flow.graph._get_llm")
    def test_la_llamada_que_redacta_conserva_su_razonamiento(self, mock_get_llm):
        from bot.flow.graph import specialist_node
        from bot.models import CustomSpecialist, save_prompt_version
        CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="x")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        self._mock_llm(mock_get_llm)

        # tool_messages poblado = ya paso por business_action, esta redactando
        asyncio.run(specialist_node(self._state(tool_messages=[
            AIMessage(content="", tool_calls=[{"name": "buscar_vehiculos", "args": {}, "id": "c1"}]),
            ToolMessage(content='{"ok": true, "vehiculos": []}', tool_call_id="c1", name="buscar_vehiculos"),
        ])))

        self.assertIsNone(
            mock_get_llm.call_args.kwargs.get("reasoning"),
            "la llamada que escribe la respuesta al cliente conserva el esfuerzo por defecto",
        )
