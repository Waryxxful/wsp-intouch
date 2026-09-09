import json
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, TestCase
from langchain.messages import AIMessage, ToolMessage

from bot.models import Conversation
from bot.simulator.app_wrapper import _acciones_de_tool_messages, create_app
from bot.simulator.marking import TEST_WA_ID_PREFIJO


class CreateAppTest(TestCase):
    # Lead vive en la BD "qaintouch" (ver BusinessActionNodeCrearLeadTest en
    # test_graph.py para el mismo patron) -- sin esto Django bloquea la
    # query de leads.models.Lead con DatabaseOperationForbidden.
    databases = {"default", "qaintouch"}

    def setUp(self):
        self.mock_wa = MagicMock()
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client", return_value=self.mock_wa)
        self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_un_turno_crea_una_conversacion_de_test_y_devuelve_la_respuesta_del_bot(self, mock_llm, mock_get_llm):
        # "faq" esta en el camino de tools (migracion a RAG agentico) --
        # necesita el mock de _get_llm ademas del de _ainvoke_with_retry (que
        # solo cubre la clasificacion del supervisor).
        mock_llm.return_value = '{"agente": "faq", "intencion_compra_real": false}'
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "El Arkana cuesta $24.490.000", "intent": "cotizar", '
                    '"extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        app, capturas = create_app(persona="Cliente indeciso", mock_wa=self.mock_wa)

        salida = app({"role": "user", "content": "cuanto cuesta el arkana"}, thread_id="hilo-1")

        self.assertEqual(salida["role"], "assistant")
        self.assertIn("Arkana", salida["content"])
        conv = Conversation.objects.get()
        self.assertTrue(conv.wa_id.startswith("TEST"))
        self.assertEqual(conv.name, "[PRUEBA] Cliente indeciso")
        self.assertEqual(len(capturas["hilo-1"]), 1)
        self.assertEqual(capturas["hilo-1"][0]["intent"], "cotizar")
        self.assertEqual(capturas["hilo-1"][0]["acciones"], [])

    @patch("bot.flow.graph._get_llm")
    @patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock)
    def test_dos_turnos_del_mismo_hilo_reusan_la_misma_conversacion(self, mock_llm, mock_get_llm):
        # Bug real de flakiness confirmado 2026-08-28 (docs/PENDIENTES.md,
        # reportado independientemente por dos sesiones): este test no
        # mockeaba _get_llm (a diferencia del test de arriba, que si lo hace
        # y explica por que hace falta) -- "faq" esta en el camino de tools,
        # y sin este mock el especialista llamaba a un LLM real, que a veces
        # decidia invocar consultar_base_conocimiento de verdad y reventaba
        # contra el guard de sqlite (USE_SQLITE=true) en vez de fallar
        # siempre igual. Mismo mock que el test de arriba, sin tool_calls.
        mock_llm.return_value = '{"agente": "faq", "intencion_compra_real": false}'
        llm_con_tools = AsyncMock()
        llm_con_tools.ainvoke.return_value = AIMessage(
            content='{"mensaje": "ok", "intent": null, '
                    '"extracted_data": {}, "next_state": null, "handoff": false}',
            tool_calls=[],
        )
        llm_base = MagicMock()
        llm_base.bind_tools.return_value = llm_con_tools
        mock_get_llm.return_value = llm_base

        app, capturas = create_app(persona="Cliente indeciso", mock_wa=self.mock_wa)

        app({"role": "user", "content": "hola"}, thread_id="hilo-1")
        app({"role": "user", "content": "quiero mas info"}, thread_id="hilo-1")

        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(len(capturas["hilo-1"]), 2)

    @patch("bot.flow.graph._ainvoke_tools_json_with_retry", new_callable=AsyncMock)
    def test_una_tool_de_negocio_queda_registrada_en_las_acciones_del_turno(self, mock_llm_tools):
        """Reemplaza al test de "crear_lead marca el lead con prefijo [TEST]".

        Cavem no usa crear_lead: esa tool escribia en el CRM real (BD
        qaintouch) y por eso habia que marcar la fila para poder limpiarla
        despues. Ahora las escrituras van a tablas del propio bot, colgadas de
        la Conversation con CASCADE -- no hace falta marcar nada, borrar la
        conversacion de test se lleva la fila.

        Usa `registrar_parte_pago` y no `registrar_datos_lead`: esa segunda ya
        no esta bindeada al especialista desde el 2026-09-07 (docs/PENDIENTES.md
        32.a), asi que un tool_call suyo no lo ejecuta nadie y el test media el
        vacio. `registrar_parte_pago` sirve igual y de yapa cubre que su
        escritura interna del lead siga viva.

        Lo que este test cuida es lo que de verdad importa del wrapper: que la
        accion de negocio quede capturada en el turno para que el juez y los
        evaluadores de codigo puedan calificarla.
        """
        from langchain.messages import AIMessage

        from bot.models import CustomSpecialist, LeadComercial, save_prompt_version

        # "ventas" solo existe en el registro si hay un CustomSpecialist real
        # con ese slug (ver supervisor_node) -- en produccion lo crea el seed.
        CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")

        mock_llm_tools.side_effect = [
            AIMessage(content="", tool_calls=[{
                "name": "registrar_parte_pago", "id": "call_1",
                "args": {"marca_modelo": "Hyundai Tucson", "anio": 2023},
            }]),
            AIMessage(content=(
                '{"mensaje": "Listo, un ejecutivo lo contactara", "intent": "reservar", '
                '"extracted_data": {}, "next_state": null, "handoff": false}'
            )),
        ]
        with patch("bot.flow.graph._ainvoke_with_retry", new_callable=AsyncMock) as mock_llm_supervisor:
            mock_llm_supervisor.return_value = '{"agente": "ventas", "intencion_compra_real": true}'
            app, capturas = create_app(persona="Cliente decidido", mock_wa=self.mock_wa)
            app({"role": "user", "content": "quiero comprar, aca mis datos"}, thread_id="hilo-1")

        # registrar_parte_pago escribe el lead por su llamada interna a
        # _registrar_datos_lead_impl, que 32.a dejo intacta.
        lead = LeadComercial.objects.get()
        self.assertEqual(lead.vehiculo_actual, "Hyundai Tucson")
        self.assertTrue(lead.tiene_parte_pago)
        # El lead cuelga de la conversacion de test: cleanup_test_conversations
        # se lo lleva por cascada sin necesidad de marcarlo.
        self.assertTrue(lead.conversation.wa_id.startswith(TEST_WA_ID_PREFIJO))

        turno = capturas["hilo-1"][0]
        self.assertEqual(len(turno["acciones"]), 1)
        accion = turno["acciones"][0]
        self.assertEqual(accion["nombre"], "registrar_parte_pago")
        self.assertEqual(accion["params"]["marca_modelo"], "Hyundai Tucson")
        self.assertTrue(accion["resultado"]["ok"])


class AccionesDeToolMessagesTest(SimpleTestCase):
    """Prueba directa de `_acciones_de_tool_messages` (funcion pura, sin
    necesidad de BD ni de correr el grafo completo) -- Hallazgo 2 de la
    revision final: no habia ningun test que la ejercitara en aislamiento."""

    def test_dos_acciones_encadenadas_en_un_mismo_turno_se_emparejan_correctamente(self):
        tool_messages = [
            AIMessage(content="", tool_calls=[{
                "name": "buscar_reserva", "id": "call_1", "args": {"codigo": "ABC123"},
            }]),
            ToolMessage(content=json.dumps({"ok": True, "codigo": "ABC123"}), tool_call_id="call_1", name="buscar_reserva"),
            AIMessage(content="", tool_calls=[{
                "name": "reagendar_hora", "id": "call_2", "args": {"codigo": "ABC123", "nueva_fecha": "2026-08-20"},
            }]),
            ToolMessage(content=json.dumps({"ok": True}), tool_call_id="call_2", name="reagendar_hora"),
        ]

        acciones = _acciones_de_tool_messages(tool_messages)

        self.assertEqual(len(acciones), 2)
        self.assertEqual(acciones[0]["nombre"], "buscar_reserva")
        self.assertEqual(acciones[0]["params"], {"codigo": "ABC123"})
        self.assertEqual(acciones[0]["resultado"], {"ok": True, "codigo": "ABC123"})
        self.assertEqual(acciones[1]["nombre"], "reagendar_hora")
        self.assertEqual(acciones[1]["params"], {"codigo": "ABC123", "nueva_fecha": "2026-08-20"})
        self.assertEqual(acciones[1]["resultado"], {"ok": True})

    def test_tool_call_id_huerfano_cae_a_msg_name_y_params_vacios(self):
        # Ningun AIMessage con tool_calls precede a este ToolMessage -- el
        # emparejamiento por tool_call_id no encuentra nada en
        # llamadas_por_id, y debe recurrir a los datos que trae el propio
        # ToolMessage en vez de reventar.
        tool_messages = [
            ToolMessage(content=json.dumps({"ok": True}), tool_call_id="call_huerfano", name="crear_lead"),
        ]

        acciones = _acciones_de_tool_messages(tool_messages)

        self.assertEqual(len(acciones), 1)
        self.assertEqual(acciones[0]["nombre"], "crear_lead")
        self.assertEqual(acciones[0]["params"], {})
        self.assertEqual(acciones[0]["resultado"], {"ok": True})

    def test_resultado_no_dict_se_envuelve_en_dict_en_vez_de_perderse_o_crashear(self):
        # consultar_disponibilidad (bot/business/__init__.py) devuelve una
        # lista real, no un dict -- ver Hallazgo 1 de la revision final.
        disponibilidad = [{"hora": "10:00"}, {"hora": "11:00"}]
        tool_messages = [
            AIMessage(content="", tool_calls=[{
                "name": "consultar_disponibilidad", "id": "call_1",
                "args": {"servicio_id": 1, "sucursal_id": 1, "fecha": "2026-08-20"},
            }]),
            ToolMessage(content=json.dumps(disponibilidad), tool_call_id="call_1", name="consultar_disponibilidad"),
        ]

        acciones = _acciones_de_tool_messages(tool_messages)

        self.assertEqual(len(acciones), 1)
        self.assertEqual(acciones[0]["resultado"], {"resultado": disponibilidad})

    def test_replay_del_corto_circuito_de_dedup_no_duplica_la_accion(self):
        # business_action_node (bot/flow/graph.py, comentario "Dedup:") no
        # re-ejecuta una accion ya exitosa en el mismo turno externo -- solo
        # re-emite un ToolMessage nuevo con el MISMO content bajo otro
        # tool_call_id. Debe verse como UNA sola accion, no dos.
        tool_messages = [
            AIMessage(content="", tool_calls=[{
                "name": "crear_lead", "id": "call_1", "args": {"telefono": "+56911111111"},
            }]),
            ToolMessage(content=json.dumps({"ok": True, "lead_id": 42}), tool_call_id="call_1", name="crear_lead"),
            AIMessage(content="", tool_calls=[{
                "name": "crear_lead", "id": "call_2", "args": {"telefono": "+56911111111"},
            }]),
            ToolMessage(content=json.dumps({"ok": True, "lead_id": 42}), tool_call_id="call_2", name="crear_lead"),
        ]

        acciones = _acciones_de_tool_messages(tool_messages)

        self.assertEqual(len(acciones), 1)
        self.assertEqual(acciones[0]["nombre"], "crear_lead")
        self.assertEqual(acciones[0]["resultado"], {"ok": True, "lead_id": 42})

    def test_acciones_distintas_no_se_confunden_con_el_dedup(self):
        # El dedup debe ser exacto (nombre Y resultado iguales), nunca
        # basado solo en el nombre -- dos crear_lead con resultados
        # distintos (dos leads reales creados) no deben colapsarse, y dos
        # acciones de nombre distinto tampoco.
        tool_messages = [
            AIMessage(content="", tool_calls=[{
                "name": "crear_lead", "id": "call_1", "args": {"telefono": "+56911111111"},
            }]),
            ToolMessage(content=json.dumps({"ok": True, "lead_id": 42}), tool_call_id="call_1", name="crear_lead"),
            AIMessage(content="", tool_calls=[{
                "name": "crear_lead", "id": "call_2", "args": {"telefono": "+56922222222"},
            }]),
            ToolMessage(content=json.dumps({"ok": True, "lead_id": 43}), tool_call_id="call_2", name="crear_lead"),
            AIMessage(content="", tool_calls=[{
                "name": "consultar_disponibilidad", "id": "call_3",
                "args": {"servicio_id": 1, "sucursal_id": 1, "fecha": "2026-08-20"},
            }]),
            ToolMessage(content=json.dumps({"ok": True, "lead_id": 42}), tool_call_id="call_3", name="consultar_disponibilidad"),
        ]

        acciones = _acciones_de_tool_messages(tool_messages)

        self.assertEqual(len(acciones), 3)
        self.assertEqual(acciones[0]["resultado"], {"ok": True, "lead_id": 42})
        self.assertEqual(acciones[1]["resultado"], {"ok": True, "lead_id": 43})
        self.assertEqual(acciones[2]["nombre"], "consultar_disponibilidad")
