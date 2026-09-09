from unittest import skip
from unittest.mock import patch

from django.test import TestCase
from bot.flow.agents.agendamiento import AgendamientoAgent, SYSTEM_PROMPT as AGENDAMIENTO_PROMPT
from bot.flow.agents.confirmacion import ConfirmacionAgent, SYSTEM_PROMPT as CONFIRMACION_PROMPT
from bot.flow.agents.faq import FaqAgent, SYSTEM_PROMPT as FAQ_PROMPT
from bot.flow.agents.custom import _simular_financiamiento_impl, _crear_lead_impl
from bot.flow.agents._common import bloque_nombre_contacto, bloque_numero_contacto


class BloqueNombreContactoTest(TestCase):
    def test_vacio_si_no_hay_nombre_en_el_state(self):
        self.assertEqual(bloque_nombre_contacto({}), "")
        self.assertEqual(bloque_nombre_contacto({"name": ""}), "")
        self.assertEqual(bloque_nombre_contacto({"name": "   "}), "")

    def test_incluye_el_nombre_si_esta_en_el_state(self):
        resultado = bloque_nombre_contacto({"name": "Felipe"})
        self.assertIn("Felipe", resultado)
        self.assertIn("WhatsApp", resultado)


class BloqueNumeroContactoTest(TestCase):
    def test_vacio_si_no_hay_wa_id_en_el_state(self):
        self.assertEqual(bloque_numero_contacto({}), "")
        self.assertEqual(bloque_numero_contacto({"wa_id": ""}), "")
        self.assertEqual(bloque_numero_contacto({"wa_id": "   "}), "")

    def test_incluye_el_numero_si_esta_en_el_state(self):
        resultado = bloque_numero_contacto({"wa_id": "56996249863"})
        self.assertIn("+56996249863", resultado)


class AgendamientoAgentTest(TestCase):
    def test_tiene_descripcion_para_el_supervisor(self):
        agent = AgendamientoAgent()
        self.assertTrue(agent.descripcion)

    def test_effective_prompt_sin_override_devuelve_el_default(self):
        agent = AgendamientoAgent()
        self.assertEqual(agent.effective_prompt(), AGENDAMIENTO_PROMPT)

    def test_effective_prompt_con_version_activa_la_devuelve(self):
        from bot.models import save_prompt_version
        save_prompt_version("agendamiento", "prompt custom de agendamiento")
        agent = AgendamientoAgent()
        self.assertEqual(agent.effective_prompt(), "prompt custom de agendamiento")


class AgendamientoToolsTest(TestCase):
    def test_business_actions_devuelve_los_9_tools(self):
        from bot.flow.agents.agendamiento import AgendamientoAgent
        agent = AgendamientoAgent()
        nombres = {t.name for t in agent.business_actions()}
        self.assertEqual(nombres, {
            "listar_catalogo", "consultar_disponibilidad", "agendar_hora",
            "buscar_reserva", "reagendar_hora", "anular_hora", "registrar_no_contactar",
            "registrar_consentimiento", "crear_caso",
        })


    def test_build_system_prompt_incluye_flow_data(self):
        from bot.flow.agents.agendamiento import AgendamientoAgent, SYSTEM_PROMPT
        agent = AgendamientoAgent()
        prompt = agent.build_system_prompt({"flow_data": {"servicio_id": 3}}, SYSTEM_PROMPT)
        self.assertIn("servicio_id", prompt)
        self.assertIn("3", prompt)

    def test_build_system_prompt_pide_handoff_reason_y_revision(self):
        agent = AgendamientoAgent()
        prompt = agent.build_system_prompt({"messages": [], "text": "x"}, agent.effective_prompt())
        self.assertIn('"handoff_reason"', prompt)
        self.assertIn('"requiere_revision"', prompt)
        self.assertIn('"motivo_revision"', prompt)

    def test_menciona_crear_caso(self):
        agent = AgendamientoAgent()
        prompt = agent.build_system_prompt({"messages": [], "text": "x"}, agent.effective_prompt())
        self.assertIn("crear_caso", prompt)

    def test_build_system_prompt_incluye_nombre_de_whatsapp_si_esta_en_el_state(self):
        agent = AgendamientoAgent()
        prompt = agent.build_system_prompt({"flow_data": {}, "name": "Felipe"}, agent.effective_prompt())
        self.assertIn("Felipe", prompt)

    def test_build_system_prompt_sin_nombre_no_agrega_el_bloque(self):
        agent = AgendamientoAgent()
        prompt = agent.build_system_prompt({"flow_data": {}}, agent.effective_prompt())
        self.assertNotIn("Nombre de perfil de WhatsApp", prompt)


class ConfirmacionAgentTest(TestCase):
    def test_tiene_descripcion_para_el_supervisor(self):
        agent = ConfirmacionAgent()
        self.assertTrue(agent.descripcion)

    def test_effective_prompt_sin_override_devuelve_el_default(self):
        agent = ConfirmacionAgent()
        self.assertEqual(agent.effective_prompt(), CONFIRMACION_PROMPT)

    def test_build_prompt_incluye_la_reserva_actual_de_flow_data(self):
        agent = ConfirmacionAgent()
        prompt = agent.build_system_prompt(
            {
                "messages": [], "text": "si, confirmo",
                "flow_data": {"reserva_actual": {"codigo": "ABC123", "servicio": "Corte"}},
                "tool_messages": [],
            },
            CONFIRMACION_PROMPT,
        )
        self.assertIn("ABC123", prompt)

    def test_business_actions_expone_reagendar_anular_y_no_contactar(self):
        agent = ConfirmacionAgent()
        self.assertEqual(
            {t.name for t in agent.business_actions()},
            {"reagendar_hora", "anular_hora", "registrar_no_contactar", "registrar_consentimiento"},
        )

    def test_build_system_prompt_pide_handoff_reason_y_revision(self):
        agent = ConfirmacionAgent()
        prompt = agent.build_system_prompt({"messages": [], "text": "x"}, agent.effective_prompt())
        self.assertIn('"handoff_reason"', prompt)
        self.assertIn('"requiere_revision"', prompt)
        self.assertIn('"motivo_revision"', prompt)


class ConfirmacionToolsTest(TestCase):
    def test_business_actions_devuelve_reagendar_anular_y_no_contactar(self):
        from bot.flow.agents.confirmacion import ConfirmacionAgent
        agent = ConfirmacionAgent()
        self.assertEqual(
            {t.name for t in agent.business_actions()},
            {"reagendar_hora", "anular_hora", "registrar_no_contactar", "registrar_consentimiento"},
        )

    def test_build_system_prompt_incluye_reserva_actual(self):
        from bot.flow.agents.confirmacion import ConfirmacionAgent, SYSTEM_PROMPT
        agent = ConfirmacionAgent()
        prompt = agent.build_system_prompt(
            {"flow_data": {"reserva_actual": {"codigo": "ABC123"}}}, SYSTEM_PROMPT,
        )
        self.assertIn("ABC123", prompt)

    def test_build_system_prompt_incluye_nombre_de_whatsapp_si_esta_en_el_state(self):
        from bot.flow.agents.confirmacion import ConfirmacionAgent, SYSTEM_PROMPT
        agent = ConfirmacionAgent()
        prompt = agent.build_system_prompt({"flow_data": {}, "name": "Felipe"}, SYSTEM_PROMPT)
        self.assertIn("Felipe", prompt)

    def test_build_system_prompt_sin_nombre_no_agrega_el_bloque(self):
        from bot.flow.agents.confirmacion import ConfirmacionAgent, SYSTEM_PROMPT
        agent = ConfirmacionAgent()
        prompt = agent.build_system_prompt({"flow_data": {}}, SYSTEM_PROMPT)
        self.assertNotIn("Nombre de perfil de WhatsApp", prompt)


class FaqAgentTest(TestCase):
    def test_tiene_descripcion_para_el_supervisor(self):
        agent = FaqAgent()
        self.assertTrue(agent.descripcion)

    def test_effective_prompt_sin_override_devuelve_el_default(self):
        agent = FaqAgent()
        self.assertEqual(agent.effective_prompt(), FAQ_PROMPT)

    def test_business_actions_devuelve_consultar_base_conocimiento_y_crear_caso(self):
        agent = FaqAgent()
        nombres = {t.name for t in agent.business_actions()}
        self.assertEqual(nombres, {"consultar_base_conocimiento", "crear_caso", "buscar_sucursales_cercanas"})

    def test_build_system_prompt_incluye_flow_data(self):
        agent = FaqAgent()
        prompt = agent.build_system_prompt({"flow_data": {"modelo": "arkana"}}, FAQ_PROMPT)
        self.assertIn("modelo", prompt)
        self.assertIn("arkana", prompt)

    def test_build_system_prompt_sin_flow_data_no_agrega_el_bloque(self):
        agent = FaqAgent()
        prompt = agent.build_system_prompt({}, FAQ_PROMPT)
        self.assertNotIn("Datos ya conocidos", prompt)

    def test_build_system_prompt_pide_handoff_reason_y_revision(self):
        agent = FaqAgent()
        prompt = agent.build_system_prompt({"messages": [], "text": "x"}, agent.effective_prompt())
        self.assertIn('"handoff_reason"', prompt)
        self.assertIn('"requiere_revision"', prompt)
        self.assertIn('"motivo_revision"', prompt)

    def test_menciona_crear_caso(self):
        agent = FaqAgent()
        prompt = agent.build_system_prompt({"messages": [], "text": "x"}, agent.effective_prompt())
        self.assertIn("crear_caso", prompt)

    def test_build_system_prompt_incluye_nombre_de_whatsapp_si_esta_en_el_state(self):
        agent = FaqAgent()
        prompt = agent.build_system_prompt({"name": "Felipe"}, FAQ_PROMPT)
        self.assertIn("Felipe", prompt)

    def test_build_system_prompt_sin_nombre_no_agrega_el_bloque(self):
        agent = FaqAgent()
        prompt = agent.build_system_prompt({}, FAQ_PROMPT)
        self.assertNotIn("Nombre de perfil de WhatsApp", prompt)


class CustomPromptAgentTest(TestCase):
    def _make_row(self, **overrides):
        from bot.models import CustomSpecialist, save_prompt_version
        prompt = overrides.pop("prompt", "Sos el especialista de envios. Responde con el estado del pedido.")
        defaults = dict(slug="envios", label="Envíos", descripcion="Responde sobre el estado de un envio.")
        defaults.update(overrides)
        row = CustomSpecialist.objects.create(**defaults)
        save_prompt_version(f"custom:{row.slug}", prompt)
        return row

    def test_name_y_descripcion_vienen_de_la_fila(self):
        from bot.flow.agents.custom import CustomPromptAgent
        row = self._make_row()
        agent = CustomPromptAgent(row)
        self.assertEqual(agent.name, "envios")
        self.assertEqual(agent.descripcion, "Responde sobre el estado de un envio.")

    def test_effective_prompt_devuelve_la_version_activa(self):
        from bot.flow.agents.custom import CustomPromptAgent
        row = self._make_row(prompt="prompt especifico de este test")
        agent = CustomPromptAgent(row)
        self.assertEqual(agent.effective_prompt(), "prompt especifico de este test")

    def test_build_prompt_arma_el_contrato_json_estandar(self):
        from bot.flow.agents.custom import CustomPromptAgent
        row = self._make_row()
        agent = CustomPromptAgent(row)
        prompt = agent.build_system_prompt({"messages": [], "text": "donde esta mi pedido?"}, agent.effective_prompt())
        self.assertIn("Sos el especialista de envios.", prompt)

    def test_business_actions_vacio(self):
        from bot.flow.agents.custom import CustomPromptAgent
        agent = CustomPromptAgent(self._make_row())
        self.assertEqual(agent.business_actions(), [])

    def test_build_prompt_incluye_flow_data_si_esta_en_el_state(self):
        # Bug real: extracted_data se mergea en flow_data (graph.py::specialist_node)
        # pero build_prompt nunca lo leia de vuelta -- el modelo de interes u otros
        # datos ya capturados se perdian turno a turno sin que el LLM los viera de
        # nuevo (ver docs/superpowers/specs/... revision manual de conversacion 14).
        from bot.flow.agents.custom import CustomPromptAgent
        row = self._make_row()
        agent = CustomPromptAgent(row)
        prompt = agent.build_system_prompt(
            {"messages": [], "text": "algo", "flow_data": {"modelo": "arkana"}},
            agent.effective_prompt(),
        )
        self.assertIn("Datos ya conocidos", prompt)
        self.assertIn("arkana", prompt)

    def test_build_prompt_sin_flow_data_no_agrega_el_bloque(self):
        from bot.flow.agents.custom import CustomPromptAgent
        row = self._make_row()
        agent = CustomPromptAgent(row)
        prompt = agent.build_system_prompt({"messages": [], "text": "algo"}, agent.effective_prompt())
        self.assertNotIn("Datos ya conocidos", prompt)

    def test_business_actions_solo_las_tiene_el_especialista_ventas(self):
        # Este test cuida la REGLA (solo "ventas" recibe tools; el resto de
        # especialistas creados desde el panel son puramente conversacionales),
        # no la lista exacta -- de esa se ocupa
        # VentasToolsTest.test_business_actions_devuelve_los_tools_de_ventas_de_cavem.
        # Duplicar la lista en dos tests obliga a editar los dos cada vez que se
        # agrega una tool, y fue justo lo que paso al armar el bot de Cavem.
        from bot.flow.agents.custom import CustomPromptAgent
        agent_ventas = CustomPromptAgent(self._make_row(slug="ventas", label="Ventas"))
        agent_otro = CustomPromptAgent(self._make_row(slug="soporte", label="Soporte"))
        nombres = {t.name for t in agent_ventas.business_actions()}
        self.assertIn("buscar_vehiculos", nombres)
        self.assertIn("simular_financiamiento", nombres)
        self.assertEqual(agent_otro.business_actions(), [])

    # Los cuatro tests de abajo cuidaban el mismo invariante contra el JSON
    # literal que build_system_prompt escribia a mano ('"intent"',
    # '"modelo_imagen": null|"<slug>"', ...). Desde el 2026-09-03 el contrato de
    # salida es la tool `responder` (ver bot/flow/respuesta.py y la auditoria de
    # latencia: el JSON en texto costaba un reintento de LLM completo en el 95%
    # de los turnos), asi que el invariante sigue siendo el mismo -- "solo ventas
    # clasifica leads y manda fotos" -- pero su fuente de verdad se movio a
    # CAMPOS_EXTRA_POR_AGENTE. Se asertan las DOS puntas: la declaracion de
    # campos y el bloque de prompt que se genera de ella, porque el bug que esto
    # previene es justo que se desincronicen.
    def test_solo_ventas_declara_modelo_imagen(self):
        from bot.flow.respuesta import campos_de
        self.assertIn("modelo_imagen", campos_de("ventas"))
        self.assertNotIn("modelo_imagen", campos_de("soporte"))

    def test_solo_ventas_declara_intent(self):
        # graph.py lee parsed.get("intent") para decidir si adjunta imagen
        # (INTENTS_CON_IMAGEN) -- ese campo debe estar garantizado por codigo,
        # no depender de la prosa del prompt en BD (que ya se ha regenerado a
        # mano mas de una vez en este proyecto).
        from bot.flow.respuesta import campos_de
        self.assertIn("intent", campos_de("ventas"))
        self.assertNotIn("intent", campos_de("soporte"))

    def test_solo_ventas_declara_lead_class_y_stage(self):
        from bot.flow.respuesta import campos_de
        self.assertIn("lead_class", campos_de("ventas"))
        self.assertIn("stage", campos_de("ventas"))
        self.assertNotIn("lead_class", campos_de("soporte"))
        self.assertNotIn("stage", campos_de("soporte"))

    def test_ningun_prompt_pide_campos_de_metadatos(self):
        # ACTUALIZADO el 2026-09-03: los metadatos ya NO se piden en el prompt,
        # los saca bot/flow/extractor_metadatos.py despues del envio. Pedirlos
        # aca era justo lo que hacia que el modelo los restateara dentro de su
        # respuesta al cliente (45 de 84 llamadas medidas en Langfuse).
        #
        # El invariante que estos tests protegian -- "solo ventas clasifica
        # leads y manda fotos" -- sigue verificado, pero en su unica fuente de
        # verdad: CAMPOS_EXTRA_POR_AGENTE, cubierto por CamposDeTest en
        # bot/tests/test_respuesta.py.
        from bot.flow.agents.custom import CustomPromptAgent
        for slug in ("ventas", "soporte"):
            agent = CustomPromptAgent(self._make_row(slug=slug, label=slug))
            prompt = agent.build_system_prompt({"messages": [], "text": "x"}, agent.effective_prompt())
            for campo in ("intent", "lead_class", "stage", "modelo_imagen",
                          "extracted_data", "handoff_reason"):
                self.assertNotIn(campo, prompt, msg=f"{slug} todavia pide {campo}")

    def test_todos_los_custom_piden_la_respuesta_en_texto_normal(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.flow.respuesta import campos_de
        for slug in ("ventas", "soporte"):
            agent = CustomPromptAgent(self._make_row(slug=slug, label=slug))
            prompt = agent.build_system_prompt({"messages": [], "text": "x"}, agent.effective_prompt())
            self.assertIn("texto normal", prompt)
            self.assertNotIn("JSON exacto", prompt)
            # El contrato de campos sigue existiendo en codigo, aunque el
            # prompt ya no lo mencione: es lo que filtra la salida del extractor.
            for campo in ("handoff_reason", "requiere_revision", "motivo_revision"):
                self.assertIn(campo, campos_de(slug))

    def test_build_prompt_incluye_nombre_de_whatsapp_si_esta_en_el_state(self):
        from bot.flow.agents.custom import CustomPromptAgent
        agent = CustomPromptAgent(self._make_row())
        prompt = agent.build_system_prompt({"messages": [], "text": "x", "name": "Felipe"}, agent.effective_prompt())
        self.assertIn("Felipe", prompt)

    def test_build_prompt_sin_nombre_no_agrega_el_bloque(self):
        from bot.flow.agents.custom import CustomPromptAgent
        agent = CustomPromptAgent(self._make_row())
        prompt = agent.build_system_prompt({"messages": [], "text": "x"}, agent.effective_prompt())
        self.assertNotIn("Nombre de perfil de WhatsApp", prompt)


class ConsolidarCandidatosTest(TestCase):
    # Bug real confirmado (revision manual + analisis de FB 2026-08-27):
    # VehiculoCatalogo tenia 9 filas fragmentadas para "Arkana" (mismas 2
    # versiones reales descritas con distinto detalle por scrapes de
    # paginas distintas) -- consultar_especificaciones_vehiculo devolvia
    # las 9 sueltas, y el LLM elegia cual citar sin garantia de elegir la
    # misma entre pruebas, explicando consumos reportados distintos en
    # pruebas de dias distintos para el "mismo" dato real.
    def test_filas_con_el_mismo_precio_se_consolidan_en_una_sola(self):
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Arkana", version="hybrid", precio=24_490_000, specs={})
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="hybrid intens turbo", precio=24_490_000,
            specs={"consumo_mixto": "17,6 km/l", "potencia": "140hp"},
        )
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="intens turbo", precio=24_490_000, specs={"motor": "turbo"},
        )
        resultado = _buscar_en_catalogo("Arkana")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].version, "hybrid intens turbo")  # el string mas largo
        self.assertEqual(
            resultado[0].specs,
            {"consumo_mixto": "17,6 km/l", "potencia": "140hp", "motor": "turbo"},
        )

    def test_filas_con_precios_distintos_no_se_mezclan(self):
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Master", version="furgón l2h2", precio=29_990_000)
        VehiculoCatalogo.objects.create(modelo="Master", version="furgón l3h2", precio=31_990_000)
        VehiculoCatalogo.objects.create(modelo="Master", version="minibus", precio=38_990_000)
        resultado = _buscar_en_catalogo("Master")
        self.assertEqual(sorted(v.precio for v in resultado), [29_990_000, 31_990_000, 38_990_000])

    def test_filas_sin_precio_se_descartan_si_hay_otra_con_precio(self):
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Arkana", version="", precio=None, specs={})
        VehiculoCatalogo.objects.create(modelo="Arkana", version="hybrid intens turbo", precio=24_490_000, specs={})
        resultado = _buscar_en_catalogo("Arkana")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].version, "hybrid intens turbo")

    def test_todas_sin_precio_no_se_descarta_ninguna(self):
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Duster", version="", precio=None)
        VehiculoCatalogo.objects.create(modelo="Duster", version="2024", precio=None)
        resultado = _buscar_en_catalogo("Duster")
        self.assertEqual(len(resultado), 2)

    def test_url_fuente_pdf_del_grupo_gana_sobre_una_pagina_web(self):
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="hybrid intens turbo", precio=24_490_000,
            url_fuente="https://renault.cl/cotizar/arkana/",
        )
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="Intens", precio=24_490_000,
            url_fuente="https://renault.cl/wp-content/uploads/ficha_arkana.pdf",
        )
        resultado = _buscar_en_catalogo("Arkana")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].url_fuente, "https://renault.cl/wp-content/uploads/ficha_arkana.pdf")

    def test_pedido_sin_marca_matchea_fila_con_marca_incluida_en_el_modelo(self):
        # Bug real confirmado 2026-08-28 (docs/PENDIENTES.md): el LLM extrae
        # "modelo" de forma inconsistente segun la pagina de origen -- con
        # marca incluida en unas ("JMC Grand Avenue", pagina /jmc/) y sin
        # marca en otras, donde la marca viene en una columna separada
        # ("Grand Avenue", pagina /ofertas/). Sin match difuso, preguntar
        # por "Grand Avenue" (la forma natural en que un cliente lo dice)
        # solo encontraba la fila SIN precio numerico -- la real, con
        # precio, quedaba invisible por tener "JMC" de mas en el modelo.
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Grand Avenue", version="4X4 MT", precio=None)
        VehiculoCatalogo.objects.create(modelo="JMC Grand Avenue", version="4X4 MT", precio=21_990_000)
        resultado = _buscar_en_catalogo("Grand Avenue", "4X4 MT")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].precio, 21_990_000)

    def test_pedido_con_marca_matchea_fila_sin_marca_en_el_modelo(self):
        # Direccion inversa del mismo bug -- el LLM (o el cliente) puede
        # tambien nombrar la marca de mas cuando la fila real en la BD no
        # la tiene.
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Grand Avenue", version="4X4 MT", precio=21_990_000)
        resultado = _buscar_en_catalogo("JMC Grand Avenue", "4X4 MT")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].precio, 21_990_000)

    def test_match_difuso_de_modelo_no_mezcla_modelos_no_relacionados(self):
        # Ejercita el fallback difuso de verdad (sin match exacto para
        # "Avenue" solo) y confirma que no se cuela un modelo distinto que
        # comparte una palabra ("Grand") con el candidato real.
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Grand Avenue", version="4X4 MT", precio=21_990_000)
        VehiculoCatalogo.objects.create(modelo="Grand Cherokee", version="Limited", precio=45_990_000)
        resultado = _buscar_en_catalogo("Avenue")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].modelo, "Grand Avenue")

    def test_modelo_base_no_matchea_variante_distinta_del_mismo_nombre(self):
        # Bug real confirmado en produccion (auditoria conversacion 14,
        # 2026-08-31): "Outlander" es subconjunto de palabras de "Outlander
        # Phev" -- el match difuso anterior (subconjunto en CUALQUIER
        # direccion, sin distinguir "el cliente omitio una palabra por
        # comodidad" de "el cliente nombra un producto genuinamente
        # distinto") dejaba que una consulta por el Outlander normal
        # devolviera precio/specs del Outlander PHEV (motor hibrido
        # enchufable, precio y ficha tecnica distintos) y viceversa -- el
        # bot llego a confirmarle a un cliente real que estaba comprando un
        # PHEV citando el precio del Outlander sin PHEV. Confirmado
        # sistemico, no un caso aislado: 159 colisiones de este tipo en el
        # catalogo real de Astara (Vigus/Vigus EV, Touring/Touring Cargo,
        # Torres/Torres EVX, etc.) via grep de _normalizar_clave sobre los
        # modelos reales.
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Mitsubishi Outlander", version="4X2 AT GL", precio=28_990_000,
        )
        VehiculoCatalogo.objects.create(
            modelo="Mitsubishi Outlander Phev", version="4x4 AT GLS", precio=42_990_000,
        )
        resultado = _buscar_en_catalogo("Outlander")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].modelo, "Mitsubishi Outlander")
        self.assertEqual(resultado[0].precio, 28_990_000)

    def test_modelo_variante_no_matchea_el_modelo_base_por_accidente(self):
        # Direccion inversa del test de arriba: preguntar especificamente
        # por el PHEV no debe resolver al modelo base sin PHEV.
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Mitsubishi Outlander", version="4X2 AT GL", precio=28_990_000,
        )
        VehiculoCatalogo.objects.create(
            modelo="Mitsubishi Outlander Phev", version="4x4 AT GLS", precio=42_990_000,
        )
        resultado = _buscar_en_catalogo("Outlander Phev")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].modelo, "Mitsubishi Outlander Phev")
        self.assertEqual(resultado[0].precio, 42_990_000)

    def test_prefijo_de_marca_sigue_matcheando_igual_con_match_exacto(self):
        # Regresion: el fix de arriba (preferir match EXACTO por modelo
        # canonico antes de caer al difuso) no debe romper la flexibilidad
        # de marca ya cubierta por test_pedido_sin_marca_matchea_fila_con_marca_incluida_en_el_modelo/
        # test_pedido_con_marca_matchea_fila_sin_marca_en_el_modelo -- ambas
        # formas del mismo modelo (con o sin marca) deben seguir
        # consolidando en una sola fila via match EXACTO tras quitar la
        # marca conocida de ambos lados, no solo via el fallback dificuso.
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Outlander", version="4X2 AT GL", precio=28_990_000)
        resultado = _buscar_en_catalogo("Mitsubishi Outlander", "4X2 AT GL")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].precio, 28_990_000)

    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_condicion_0km_excluye_seminuevo_del_mismo_nombre(self):
        # Bug real confirmado en la auditoria previa a la prueba de Astara
        # del 2026-09-01 (docs/PENDIENTES.md): el listado de seminuevos del
        # sitio usa nombres genericos ("Compass") que matchean por
        # subconjunto de palabras contra el 0km real ("Jeep Compass"). Un
        # caller que SI necesita un precio inequivoco (ej.
        # _resolver_precio_catalogo, usado por simular_financiamiento) debe
        # poder pedir condicion="0km" explicito y no ver el seminuevo.
        from bot.business.ventas import _buscar_en_catalogo, _resolver_precio_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Jeep Avenger", version="ALTITUDE 1.2 HÍBRIDO AT",
            precio=24_990_000, condicion="0km",
        )
        VehiculoCatalogo.objects.create(
            modelo="AVENGER", version="ALTITUDE 1.2 HÍBRIDO AT",
            precio=17_990_000, condicion="usado",
        )
        resultado = _buscar_en_catalogo("Avenger", "ALTITUDE 1.2 HÍBRIDO AT", condicion="0km")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].precio, 24_990_000)
        # _resolver_precio_catalogo default a "0km" sin que el caller lo pida --
        # simular_financiamiento nunca debe anclar un precio de usado sin que
        # el cliente lo haya pedido explicitamente.
        self.assertEqual(_resolver_precio_catalogo("Avenger", "ALTITUDE 1.2 HÍBRIDO AT"), 24_990_000)

    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_prefiere_fila_con_tiers_de_precio_sobre_fila_con_precio_ambiguo(self):
        # Bug real encontrado en el re-scrape de verificacion (2026-08-31,
        # docs/PENDIENTES.md): la misma version real quedaba duplicada entre
        # la pagina dedicada (que desglosa precio/precio_contado/
        # precio_financiado) y otra pagina (home, /ofertas/) que solo
        # muestra UN numero sin desglosar -- y ese numero coincidia
        # exactamente con el precio_contado real, no con el de lista. Ambas
        # filas son 0km (no es el caso seminuevos) y comparten la misma
        # version exacta, asi que sin este fix _consolidar_candidatos las
        # trataba como 2 versiones reales distintas y el MINIMO ganaba.
        from bot.business.ventas import _buscar_en_catalogo, _resolver_precio_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Ssangyong Actyon", version="DLX",
            precio=34_990_000, precio_contado=31_990_000, precio_financiado=30_990_000,
        )
        VehiculoCatalogo.objects.create(modelo="Actyon", version="DLX", precio=31_990_000)

        resultado = _buscar_en_catalogo("Actyon", "DLX")
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].precio, 34_990_000)
        self.assertEqual(_resolver_precio_catalogo("Actyon", "DLX"), 34_990_000)

    def test_sin_condicion_devuelve_0km_y_usado_mezclados(self):
        # Direccion inversa del test de arriba: sin condicion explicita
        # (el caso de consultar_especificaciones_vehiculo, que SI quiere ver
        # ambas opciones para poder preguntarle al cliente cual prefiere),
        # _buscar_en_catalogo no filtra por condicion.
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Jeep Avenger", version="ALTITUDE 1.2 HÍBRIDO AT",
            precio=24_990_000, condicion="0km",
        )
        VehiculoCatalogo.objects.create(
            modelo="AVENGER", version="ALTITUDE 1.2 HÍBRIDO AT",
            precio=17_990_000, condicion="usado",
        )
        resultado = _buscar_en_catalogo("Avenger", "ALTITUDE 1.2 HÍBRIDO AT")
        self.assertEqual({v.condicion for v in resultado}, {"0km", "usado"})

    def test_solo_hay_seminuevo_condicion_0km_no_devuelve_nada(self):
        # Ninguna tool de ventas de hoy ofrece seminuevos por default -- si
        # el unico candidato es "usado" y el caller pidio "0km" explicito
        # (ej. _resolver_precio_catalogo), el resultado debe quedar vacio en
        # vez de devolver un precio de un auto que no es el que se ofrece.
        from bot.business.ventas import _buscar_en_catalogo
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Compass", version="", precio=15_990_000, condicion="usado")
        self.assertEqual(_buscar_en_catalogo("Compass", condicion="0km"), [])

    def test_consultar_especificaciones_avisa_si_hay_0km_y_usado_sin_condicion(self):
        # A pedido explicito del usuario (auditoria previa a la prueba de
        # Astara del 2026-09-01): si el cliente no especifico 0km o usado y
        # existen ambas, la tool debe devolver un aviso para que el LLM le
        # pregunte al cliente en vez de elegir una por su cuenta.
        from bot.business.ventas import _consultar_especificaciones_vehiculo_impl
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Jeep Compass", version="Sport", precio=25_990_000, condicion="0km")
        VehiculoCatalogo.objects.create(modelo="Compass", version="Sport", precio=15_990_000, condicion="usado")

        sin_condicion = _consultar_especificaciones_vehiculo_impl("Compass", "Sport")
        self.assertIn("aviso", sin_condicion)
        self.assertEqual(len(sin_condicion["vehiculos"]), 2)

        con_condicion = _consultar_especificaciones_vehiculo_impl("Compass", "Sport", condicion="0km")
        self.assertNotIn("aviso", con_condicion)
        self.assertEqual(len(con_condicion["vehiculos"]), 1)
        self.assertEqual(con_condicion["vehiculos"][0]["precio"], 25_990_000)

    def test_vehiculo_a_dict_expone_los_3_tiers_de_precio(self):
        from bot.business.ventas import _consultar_especificaciones_vehiculo_impl
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Jeep Compass", version="Sport", precio=25_990_000,
            precio_contado=23_990_000, precio_financiado=21_990_000,
        )
        resultado = _consultar_especificaciones_vehiculo_impl("Jeep Compass", "Sport")
        vehiculo = resultado["vehiculos"][0]
        self.assertEqual(vehiculo["precio"], 25_990_000)
        self.assertEqual(vehiculo["precio_contado"], 23_990_000)
        self.assertEqual(vehiculo["precio_financiado"], 21_990_000)

    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_simular_financiamiento_no_ancla_precio_de_usado_sin_pedirlo(self):
        # Mismo espiritu que el test de _resolver_precio_catalogo de arriba,
        # pero ejercitando el impl completo de la tool de financiamiento.
        from bot.business.ventas import _simular_financiamiento_impl
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Jeep Compass", version="Sport", precio=25_990_000, condicion="0km")
        VehiculoCatalogo.objects.create(modelo="Compass", version="Sport", precio=15_990_000, condicion="usado")

        resultado = _simular_financiamiento_impl(
            precio=1, pie=6_000_000, plazo_meses=36, modelo="Compass", version="Sport",
        )
        self.assertEqual(resultado["precio"], 25_990_000)

        resultado_usado = _simular_financiamiento_impl(
            precio=1, pie=6_000_000, plazo_meses=36, modelo="Compass", version="Sport", condicion="usado",
        )
        self.assertEqual(resultado_usado["precio"], 15_990_000)


class ResolverFichaTecnicaUrlTest(TestCase):
    def test_devuelve_la_url_si_es_un_pdf(self):
        from bot.business.ventas import _resolver_ficha_tecnica_url
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="Intens", precio=24_490_000,
            url_fuente="https://renault.cl/wp-content/uploads/ficha_arkana.pdf",
        )
        self.assertEqual(
            _resolver_ficha_tecnica_url("Arkana"), "https://renault.cl/wp-content/uploads/ficha_arkana.pdf",
        )

    def test_devuelve_none_si_ninguna_fila_tiene_pdf(self):
        from bot.business.ventas import _resolver_ficha_tecnica_url
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="Intens", precio=24_490_000, url_fuente="https://renault.cl/cotizar/arkana/",
        )
        self.assertIsNone(_resolver_ficha_tecnica_url("Arkana"))

    def test_devuelve_none_si_el_modelo_no_existe(self):
        from bot.business.ventas import _resolver_ficha_tecnica_url
        self.assertIsNone(_resolver_ficha_tecnica_url("Duster"))


class EnviarFichaTecnicaImplTest(TestCase):
    def _conv_con_ficha(self, wa_id="56911112222", url="https://renault.cl/ficha_arkana.pdf"):
        from bot.models import Conversation, VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Arkana", version="Intens", precio=24_490_000, url_fuente=url)
        return Conversation.objects.create(wa_id=wa_id)

    def test_falta_wa_id_falla(self):
        from bot.business.ventas import _enviar_ficha_tecnica_impl
        resultado = _enviar_ficha_tecnica_impl(wa_id="", modelo="Arkana")
        self.assertFalse(resultado["ok"])
        self.assertIn("wa_id", resultado["motivo"])

    def test_sin_pdf_disponible_falla_con_motivo(self):
        from bot.business.ventas import _enviar_ficha_tecnica_impl
        from bot.models import Conversation
        Conversation.objects.create(wa_id="56911112222")
        resultado = _enviar_ficha_tecnica_impl(wa_id="56911112222", modelo="Duster")
        self.assertFalse(resultado["ok"])
        self.assertIn("Duster", resultado["motivo"])

    def test_con_pdf_disponible_manda_documento_y_guarda_marcador(self):
        from bot.business.ventas import _enviar_ficha_tecnica_impl
        conv = self._conv_con_ficha()
        with patch("bot.whatsapp.client.get_wa_client") as mock_get_client:
            mock_get_client.return_value.send_document.return_value = True
            resultado = _enviar_ficha_tecnica_impl(wa_id="56911112222", modelo="Arkana")
        self.assertTrue(resultado["ok"])
        mock_get_client.return_value.send_document.assert_called_once_with(
            "56911112222", "https://renault.cl/ficha_arkana.pdf", filename="Ficha tecnica Arkana.pdf",
        )
        self.assertTrue(conv.messages.filter(content="[ficha_enviada:Arkana]").exists())

    def test_fallo_al_enviar_devuelve_ok_false_y_no_guarda_marcador(self):
        from bot.business.ventas import _enviar_ficha_tecnica_impl
        conv = self._conv_con_ficha()
        with patch("bot.whatsapp.client.get_wa_client") as mock_get_client:
            mock_get_client.return_value.send_document.return_value = False
            resultado = _enviar_ficha_tecnica_impl(wa_id="56911112222", modelo="Arkana")
        self.assertFalse(resultado["ok"])
        self.assertFalse(conv.messages.filter(content="[ficha_enviada:Arkana]").exists())

    def test_ya_enviada_en_la_sesion_activa_no_se_reenvia(self):
        from bot.business.ventas import _enviar_ficha_tecnica_impl
        from bot.models import Message
        conv = self._conv_con_ficha()
        Message.objects.create(conversation=conv, role="assistant", content="[ficha_enviada:Arkana]")
        with patch("bot.whatsapp.client.get_wa_client") as mock_get_client:
            resultado = _enviar_ficha_tecnica_impl(wa_id="56911112222", modelo="Arkana")
        self.assertTrue(resultado["ok"])
        mock_get_client.return_value.send_document.assert_not_called()

    def test_sin_conversation_existente_igual_intenta_mandar(self):
        from bot.business.ventas import _enviar_ficha_tecnica_impl
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="Intens", precio=24_490_000, url_fuente="https://renault.cl/ficha.pdf",
        )
        with patch("bot.whatsapp.client.get_wa_client") as mock_get_client:
            mock_get_client.return_value.send_document.return_value = True
            resultado = _enviar_ficha_tecnica_impl(wa_id="56999998888", modelo="Arkana")
        self.assertTrue(resultado["ok"])


class SimularFinanciamientoTest(TestCase):
    def _agent_ventas(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.models import CustomSpecialist, save_prompt_version
        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        return CustomPromptAgent(row)

    def _cuota_esperada(self, monto_total_financiado, plazo_meses, tasa=0.0199):
        return monto_total_financiado * (tasa * (1 + tasa) ** plazo_meses) / ((1 + tasa) ** plazo_meses - 1)

    def test_caso_normal_pie_exactamente_20_porciento(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=36)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 20_000_000)
        self.assertEqual(resultado["pie"], 4_000_000)
        self.assertEqual(resultado["porcentaje_pie"], 20.0)
        self.assertEqual(resultado["monto_base_a_financiar"], 16_000_000)
        self.assertEqual(resultado["monto_total_financiado"], 16_000_000)
        self.assertEqual(resultado["tasa_mensual_referencial"], 0.0199)
        cuota_sin_redondear = self._cuota_esperada(16_000_000, 36)
        self.assertEqual(resultado["cuota_mensual_estimada"], round(cuota_sin_redondear))
        total_esperado = cuota_sin_redondear * 36
        self.assertEqual(resultado["total_pagado_en_cuotas"], round(total_esperado))
        self.assertEqual(resultado["intereses_totales"], round(total_esperado - 16_000_000))
        self.assertEqual(resultado["costo_total_operacion"], round(4_000_000 + total_esperado))

    def test_gastos_adicionales_se_suman_al_monto_financiado(self):
        agent = self._agent_ventas()
        sin_gastos = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=36)
        con_gastos = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=36, gastos_adicionales=500_000)
        self.assertTrue(con_gastos["ok"])
        self.assertEqual(con_gastos["monto_total_financiado"], 16_500_000)
        self.assertGreater(con_gastos["cuota_mensual_estimada"], sin_gastos["cuota_mensual_estimada"])

    def test_pie_por_debajo_del_20_porciento_devuelve_ok_false_con_minimo_exigido(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=2_000_000, plazo_meses=36)
        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)
        self.assertEqual(resultado["pie_minimo_exigido"], 4_000_000)

    def test_pie_igual_o_mayor_al_precio_devuelve_ok_false(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=10_000_000, pie=10_000_000, plazo_meses=12)
        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)

    def test_plazo_bajo_el_minimo_devuelve_ok_false(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=5)
        self.assertFalse(resultado["ok"])

    def test_plazo_sobre_el_maximo_devuelve_ok_false(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=61)
        self.assertFalse(resultado["ok"])

    @skip("Cavem: el docx S7 fija el financiamiento entre 12 y 60 meses, asi que "
          "_PLAZO_MIN_MESES paso de 6 a 12 y este test afirma que 6 es valido. "
          "Cubierto por SimulacionAncladaTest.test_plazo_de_12_y_de_60_son_validos.")
    def test_plazo_en_los_limites_inclusive_es_valido(self):
        agent = self._agent_ventas()
        self.assertTrue(_simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=6)["ok"])
        self.assertTrue(_simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=60)["ok"])

    def test_gastos_adicionales_negativos_devuelve_ok_false(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=36, gastos_adicionales=-1)
        self.assertFalse(resultado["ok"])

    def test_precio_cero_devuelve_ok_false(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=0, pie=0, plazo_meses=12)
        self.assertFalse(resultado["ok"])

    def test_plazo_meses_como_string_no_crashea_devuelve_ok_true(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses="36")
        self.assertTrue(resultado["ok"])  # "36" coerciona limpio a 36, es valido

    def test_precio_no_numerico_devuelve_ok_false_sin_crashear(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio="veinte millones", pie=4_000_000, plazo_meses=36)
        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)

    # Bug real confirmado en revision manual de conversacion: el LLM simulo el
    # financiamiento del Arkana con $34.990.000 (precio real del Koleos Full
    # Hybrid E-Tech Esprit Alpine, no del Arkana) porque VehiculoCatalogo estaba
    # vacia y nada validaba el precio contra un dato estructurado -- confiaba
    # ciegamente en el numero que puso el LLM en business_params. A partir de
    # aca, si hay match en VehiculoCatalogo, la BD manda sobre lo que puso el LLM.

    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_precio_se_reemplaza_por_el_del_catalogo_si_hay_match_exacto_de_modelo_y_version(self):
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Arkana", version="Intens Turbo", precio=24_490_000)
        VehiculoCatalogo.objects.create(modelo="Koleos", version="Full Hybrid E-Tech Esprit Alpine", precio=34_990_000)
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(
            modelo="Arkana", version="Intens Turbo", precio=34_990_000, pie=6_998_000, plazo_meses=36,
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 24_490_000)

    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_precio_del_catalogo_sin_version_toma_el_minimo_entre_versiones(self):
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Arkana", version="Intens Turbo", precio=24_490_000)
        VehiculoCatalogo.objects.create(modelo="Arkana", version="Esprit Alpine Turbo", precio=26_990_000)
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(
            modelo="Arkana", precio=34_990_000, pie=6_998_000, plazo_meses=36,
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 24_490_000)

    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_matching_de_modelo_es_insensible_a_mayusculas_y_acentos(self):
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Koleos", version="Full Hybrid E-Tech Esprit Alpine", precio=34_990_000)
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(
            modelo="koleos", version="full hybrid e-tech esprit alpine", precio=1, pie=7_000_000, plazo_meses=36,
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 34_990_000)

    # Bug real confirmado en revision manual 2026-08-27: al simular DOS
    # versiones del mismo modelo en un turno, la version que el LLM mando
    # para la segunda ("Esprit Alpine Hybrid") no matcheaba EXACTO contra el
    # string real del catalogo ("Full Hybrid E-Tech Esprit Alpine") -- sin
    # match exacto, _resolver_precio_catalogo caia en silencio al precio
    # MINIMO de todo el modelo (el del Techno, mas barato), sin ningun
    # aviso. Verificado matematicamente contra la cuota real que vio el
    # cliente: $533.900 correspondia al precio del Techno ($27.990.000),
    # no al del Esprit Alpine ($34.990.000) que el propio bot habia
    # cotizado dos turnos antes.
    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_precio_del_catalogo_con_version_parafraseada_matchea_por_subconjunto_de_palabras(self):
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Koleos", version="Techno 2.0T", precio=27_990_000)
        VehiculoCatalogo.objects.create(modelo="Koleos", version="Full Hybrid E-Tech Esprit Alpine", precio=34_990_000)
        resultado = _simular_financiamiento_impl(
            modelo="Koleos", version="Esprit Alpine Hybrid", precio=1, pie=13_121_250, plazo_meses=41,
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 34_990_000)

    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_precio_del_catalogo_con_version_parafraseada_no_matchea_una_version_distinta(self):
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Koleos", version="Techno 2.0T", precio=27_990_000)
        VehiculoCatalogo.objects.create(modelo="Koleos", version="Full Hybrid E-Tech Esprit Alpine", precio=34_990_000)
        resultado = _simular_financiamiento_impl(
            modelo="Koleos", version="Techno", precio=1, pie=10_496_250, plazo_meses=41,
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 27_990_000)

    @skip("Cavem: _resolver_precio_catalogo ahora lee VehiculoUsado (planilla), no VehiculoCatalogo (scraper). Cubierto por bot/tests/test_usados_cavem.py.")
    def test_precio_del_catalogo_version_sin_ningun_token_en_comun_cae_al_minimo_del_modelo(self):
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Koleos", version="Techno 2.0T", precio=27_990_000)
        VehiculoCatalogo.objects.create(modelo="Koleos", version="Full Hybrid E-Tech Esprit Alpine", precio=34_990_000)
        resultado = _simular_financiamiento_impl(
            modelo="Koleos", version="RS Line", precio=1, pie=6_000_000, plazo_meses=36,
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 27_990_000)

    def test_precio_del_llm_se_mantiene_si_el_modelo_no_esta_en_el_catalogo(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(
            modelo="Duster", precio=20_000_000, pie=4_000_000, plazo_meses=36,
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 20_000_000)

    def test_precio_del_llm_se_mantiene_sin_modelo_igual_que_antes_del_fix(self):
        agent = self._agent_ventas()
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=36)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["precio"], 20_000_000)

    # Bug real confirmado en conversacion 14 (2026-08-31, docs/PENDIENTES.md
    # "Pie/monto financiado inconsistente"): el bot uso $11.995.000 como pie
    # con un precio de $23.990.000 (50% correcto) y 4 minutos despues, con un
    # precio distinto de $24.990.000, reutilizo el mismo monto de pie en vez
    # de recalcular 50% del precio nuevo -- porque el pie es un monto en
    # pesos que el LLM tiene que recordar y recalcular de memoria entre
    # turnos. porcentaje_pie mueve ese calculo a la tool: el mismo porcentaje
    # aplicado a un precio distinto SIEMPRE da el monto correcto, sin
    # depender de que el LLM haga la cuenta bien.
    def test_porcentaje_pie_calcula_el_monto_contra_el_precio_de_esta_llamada(self):
        primera = _simular_financiamiento_impl(precio=23_990_000, porcentaje_pie=0.5, plazo_meses=36)
        segunda = _simular_financiamiento_impl(precio=24_990_000, porcentaje_pie=0.5, plazo_meses=36)
        self.assertTrue(primera["ok"])
        self.assertTrue(segunda["ok"])
        self.assertEqual(primera["pie"], 11_995_000)
        self.assertEqual(segunda["pie"], 12_495_000)

    def test_porcentaje_pie_reproduce_el_caso_real_de_la_conversacion_14(self):
        resultado = _simular_financiamiento_impl(precio=24_990_000, porcentaje_pie=0.48018, plazo_meses=36)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["monto_base_a_financiar"], round(24_990_000 * (1 - 0.48018)))

    def test_pie_monto_sigue_funcionando_igual_que_antes(self):
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=36)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["pie"], 4_000_000)

    def test_pie_y_porcentaje_pie_juntos_devuelve_ok_false(self):
        resultado = _simular_financiamiento_impl(
            precio=20_000_000, pie=4_000_000, porcentaje_pie=0.2, plazo_meses=36,
        )
        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)

    def test_ni_pie_ni_porcentaje_pie_devuelve_ok_false(self):
        resultado = _simular_financiamiento_impl(precio=20_000_000, plazo_meses=36)
        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)

    def test_porcentaje_pie_no_numerico_devuelve_ok_false_sin_crashear(self):
        resultado = _simular_financiamiento_impl(precio=20_000_000, porcentaje_pie="cincuenta", plazo_meses=36)
        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)

    def test_porcentaje_pie_por_debajo_del_minimo_devuelve_ok_false(self):
        resultado = _simular_financiamiento_impl(precio=20_000_000, porcentaje_pie=0.1, plazo_meses=36)
        self.assertFalse(resultado["ok"])
        self.assertEqual(resultado["pie_minimo_exigido"], 4_000_000)

    # tasa_mensual: mismo problema de fondo, pero para la TASA en vez del
    # pie -- sin esto, ante una pregunta de cuota con la tasa de un banco
    # externo (no Astara), el LLM no tiene ninguna tool que la use y termina
    # calculando la cuota "de memoria" en texto plano (confirmado en
    # Langfuse para la misma conversacion 14: el turno de las 20:05:49 no
    # invoca ningun tool_call).
    def test_tasa_mensual_custom_se_usa_en_el_calculo_de_la_cuota(self):
        resultado = _simular_financiamiento_impl(
            precio=20_000_000, pie=4_000_000, plazo_meses=36, tasa_mensual=0.012,
        )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["tasa_mensual_referencial"], 0.012)
        cuota_esperada = self._cuota_esperada(16_000_000, 36, tasa=0.012)
        self.assertEqual(resultado["cuota_mensual_estimada"], round(cuota_esperada))
        # la tasa de Astara (1,99%) siempre da una cuota mayor que 1,2% para
        # el mismo monto/plazo -- confirma que efectivamente uso la tasa
        # custom y no ignoro el argumento silenciosamente.
        cuota_astara = self._cuota_esperada(16_000_000, 36)
        self.assertLess(resultado["cuota_mensual_estimada"], round(cuota_astara))

    def test_tasa_mensual_default_sigue_siendo_la_de_astara_si_no_se_informa(self):
        resultado = _simular_financiamiento_impl(precio=20_000_000, pie=4_000_000, plazo_meses=36)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["tasa_mensual_referencial"], 0.0199)

    def test_tasa_mensual_no_numerica_devuelve_ok_false_sin_crashear(self):
        resultado = _simular_financiamiento_impl(
            precio=20_000_000, pie=4_000_000, plazo_meses=36, tasa_mensual="uno coma dos",
        )
        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)

    def test_tasa_mensual_cero_o_negativa_devuelve_ok_false(self):
        resultado_cero = _simular_financiamiento_impl(
            precio=20_000_000, pie=4_000_000, plazo_meses=36, tasa_mensual=0,
        )
        resultado_negativa = _simular_financiamiento_impl(
            precio=20_000_000, pie=4_000_000, plazo_meses=36, tasa_mensual=-0.01,
        )
        self.assertFalse(resultado_cero["ok"])
        self.assertFalse(resultado_negativa["ok"])


class VentasToolsTest(TestCase):
    def _agent_ventas(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.models import CustomSpecialist, save_prompt_version
        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        return CustomPromptAgent(row)

    def test_business_actions_devuelve_los_tools_de_ventas_de_cavem(self):
        # Cambio respecto de wsp_demo: las tres tools que leian
        # VehiculoCatalogo (salida del scraper) se reemplazaron por sus
        # equivalentes sobre VehiculoUsado (planilla de Cavem), se sumaron
        # buscar_vehiculos / simular_por_cuota / registrar_parte_pago, y se
        # saco enviar_ficha_tecnica (los links de la planilla son ficticios).
        #
        # registrar_datos_lead NO esta: se desbindeo el 2026-09-07 porque el
        # especialista la pedia en una segunda ronda de herramientas -- una
        # llamada entera al LLM en el 17,3% de los turnos. Los mismos campos
        # los captura ahora el extractor / el argumento `lead` de `responder`,
        # fuera de la latencia percibida (docs/PENDIENTES.md 32.a y
        # bot/tests/test_lead_sin_tool.py).
        agent = self._agent_ventas()
        nombres = {t.name for t in agent.business_actions()}
        self.assertEqual(nombres, {
            "buscar_vehiculos", "consultar_ficha_vehiculo", "comparar_vehiculos_usados",
            "simular_financiamiento", "simular_por_cuota",
            "registrar_parte_pago",
            "registrar_no_contactar", "registrar_consentimiento",
            "consultar_base_conocimiento", "buscar_sucursales_cercanas",
        })

    def test_agente_no_ventas_no_tiene_tools(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.models import CustomSpecialist, save_prompt_version
        row = CustomSpecialist.objects.create(slug="soporte", label="Soporte", descripcion="x")
        save_prompt_version("custom:soporte", "prompt")
        agent = CustomPromptAgent(row)
        self.assertEqual(agent.business_actions(), [])

    def test_consultar_especificaciones_vehiculo_con_match(self):
        from bot.flow.agents.custom import _consultar_especificaciones_vehiculo_impl
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Arkana", version="Intens Turbo", precio=24_490_000, specs={"consumo": "17.6 km/l"})
        resultado = _consultar_especificaciones_vehiculo_impl(modelo="Arkana", version="Intens Turbo")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["vehiculos"][0]["precio"], 24_490_000)
        self.assertEqual(resultado["vehiculos"][0]["specs"]["consumo"], "17.6 km/l")

    def test_consultar_especificaciones_vehiculo_incluye_ficha_tecnica_url_si_es_pdf(self):
        from bot.flow.agents.custom import _consultar_especificaciones_vehiculo_impl
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="Intens Turbo", precio=24_490_000,
            url_fuente="https://renault.cl/wp-content/uploads/ficha_arkana.pdf",
        )
        resultado = _consultar_especificaciones_vehiculo_impl(modelo="Arkana")
        self.assertEqual(
            resultado["vehiculos"][0]["ficha_tecnica_url"],
            "https://renault.cl/wp-content/uploads/ficha_arkana.pdf",
        )

    def test_consultar_especificaciones_vehiculo_ficha_tecnica_url_none_si_no_es_pdf(self):
        from bot.flow.agents.custom import _consultar_especificaciones_vehiculo_impl
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(
            modelo="Arkana", version="Intens Turbo", precio=24_490_000,
            url_fuente="https://renault.cl/cotizar/arkana/",
        )
        resultado = _consultar_especificaciones_vehiculo_impl(modelo="Arkana")
        self.assertIsNone(resultado["vehiculos"][0]["ficha_tecnica_url"])

    def test_consultar_especificaciones_vehiculo_sin_match(self):
        from bot.flow.agents.custom import _consultar_especificaciones_vehiculo_impl
        resultado = _consultar_especificaciones_vehiculo_impl(modelo="Duster")
        self.assertFalse(resultado["ok"])
        self.assertIn("motivo", resultado)

    def test_comparar_vehiculos_separa_encontrados_de_no_encontrados(self):
        from bot.flow.agents.custom import _comparar_vehiculos_impl
        from bot.models import VehiculoCatalogo
        VehiculoCatalogo.objects.create(modelo="Arkana", version="Intens Turbo", precio=24_490_000)
        resultado = _comparar_vehiculos_impl(modelos=["Arkana", "Duster"])
        self.assertTrue(resultado["ok"])
        self.assertIn("Arkana", resultado["vehiculos"])
        self.assertEqual(resultado["vehiculos"]["Arkana"][0]["precio"], 24_490_000)
        self.assertEqual(resultado["no_encontrados"], ["Duster"])

    def test_comparar_vehiculos_tipa_modelos_como_lista_de_strings(self):
        # `modelos: list` sin parametrizar generaba un schema Pydantic mas
        # debil para el LLM (`items: {}` en vez de `items: {"type": "string"}`)
        # que el resto de las tools del repo, que si tipan sus argumentos.
        from bot.business.ventas import comparar_vehiculos
        self.assertEqual(comparar_vehiculos.args["modelos"]["items"], {"type": "string"})


class ToolSchemasNoExponenDatosInyectadosTest(TestCase):
    """Ya no hace falta comparar el schema de cada tool contra un dict
    tool_impls() mantenido a mano -- LangChain deriva el schema de la tool
    directo de la firma real de la funcion, asi que esa clase de bug
    (schema y ejecucion desincronizados) ya no es posible por construccion.
    Lo que SI sigue siendo responsabilidad de un test es que un dato de
    compliance como wa_id (inyectado via ToolRuntime) nunca quede expuesto
    como argumento que el LLM pueda llenar."""

    def test_registrar_no_contactar_solo_expone_motivo(self):
        from bot.business import registrar_no_contactar
        self.assertEqual(set(registrar_no_contactar.args.keys()), {"motivo"})

    def test_todos_los_agentes_exponen_tools_reales(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.models import CustomSpecialist, save_prompt_version

        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")

        for agent in (AgendamientoAgent(), ConfirmacionAgent(), CustomPromptAgent(row), FaqAgent()):
            for tool_fn in agent.business_actions():
                self.assertTrue(hasattr(tool_fn, "invoke"), f"{tool_fn} no es una tool real invocable")


class BuildAgentRegistryTest(TestCase):
    def test_incluye_los_3_estaticos(self):
        from bot.flow.agents import build_agent_registry
        registry = build_agent_registry()
        self.assertEqual(
            {"agendamiento", "confirmacion", "faq"} & set(registry.keys()),
            {"agendamiento", "confirmacion", "faq"},
        )

    def test_incluye_especialistas_personalizados_de_la_bd(self):
        from bot.models import CustomSpecialist, save_prompt_version
        from bot.flow.agents import build_agent_registry
        CustomSpecialist.objects.create(slug="envios", label="Envíos", descripcion="d")
        save_prompt_version("custom:envios", "p")
        registry = build_agent_registry()
        self.assertIn("envios", registry)
        self.assertEqual(registry["envios"].name, "envios")

    def test_se_recalcula_en_cada_llamada_sin_cache(self):
        from bot.models import CustomSpecialist, save_prompt_version
        from bot.flow.agents import build_agent_registry
        self.assertNotIn("nuevo", build_agent_registry())
        CustomSpecialist.objects.create(slug="nuevo", label="Nuevo", descripcion="d")
        save_prompt_version("custom:nuevo", "p")
        self.assertIn("nuevo", build_agent_registry())


class CrearLeadTest(TestCase):
    def _agent_ventas(self):
        from bot.flow.agents.custom import CustomPromptAgent
        from bot.models import CustomSpecialist, save_prompt_version
        row = CustomSpecialist.objects.create(slug="ventas", label="Ventas", descripcion="Ventas de autos")
        save_prompt_version("custom:ventas", "prompt de ventas de prueba")
        return CustomPromptAgent(row)

    databases = {"default", "qaintouch"}

    def test_crea_el_lead_con_los_4_campos_y_devuelve_ok_y_lead_id(self):
        from leads.models import Lead
        agent = self._agent_ventas()
        resultado = _crear_lead_impl(
            rut="11.111.111-1", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="quiere agendar un test drive",
        )
        self.assertTrue(resultado["ok"])
        self.assertIn("lead_id", resultado)
        lead = Lead.objects.using("qaintouch").get(pk=resultado["lead_id"])
        self.assertEqual(lead.rut, "11.111.111-1")
        self.assertEqual(lead.nombre, "Juan Perez")
        self.assertEqual(lead.telefono, "+56911112222")
        self.assertEqual(lead.razon_interes, "quiere agendar un test drive")

    def test_rut_y_telefono_sobredimensionados_se_truncan_en_vez_de_reventar(self):
        # Lead.rut es max_length=12 -- exactamente el largo de un RUT bien
        # formado ("11.111.111-1"), asi que cualquier decoracion extra del
        # LLM desborda la columna. Confirmado contra SQL Server real:
        # ProgrammingError "String or binary data would be truncated".
        from leads.models import Lead
        agent = self._agent_ventas()
        resultado = _crear_lead_impl(
            rut="RUT: 11.111.111-1 (verificado)",  # 32 caracteres, > 12
            nombre="Juan Perez",
            telefono="+56 9 1111 2222 (whatsapp)",  # > 20 caracteres
            razon_interes="quiere agendar un test drive",
        )
        self.assertTrue(resultado["ok"])
        lead = Lead.objects.using("qaintouch").get(pk=resultado["lead_id"])
        self.assertEqual(len(lead.rut), 12)
        self.assertEqual(len(lead.telefono), 20)
        self.assertEqual(lead.rut, "RUT: 11.111.111-1 (verificado)"[:12])
        self.assertEqual(lead.telefono, "+56 9 1111 2222 (whatsapp)"[:20])

    def test_fallo_de_bd_al_crear_el_lead_degrada_a_ok_false_en_vez_de_propagar(self):
        # business_action_node solo atrapa TypeError -- cualquier otra
        # excepcion de _crear_lead llegaria sin atrapar hasta el webhook (sin
        # except), produciendo un 500 y dejando al contacto sin respuesta.
        from unittest.mock import patch
        from leads.models import Lead
        agent = self._agent_ventas()
        with patch.object(Lead.objects, "create", side_effect=Exception("timeout de conexion")):
            resultado = _crear_lead_impl(
                rut="11.111.111-1", nombre="Juan Perez", telefono="+56911112222",
                razon_interes="quiere agendar un test drive",
            )
        self.assertEqual(resultado, {"ok": False, "motivo": "no se pudo registrar el lead."})

    def test_reintento_en_el_mismo_turno_con_razon_interes_reformulada_no_duplica(self):
        # bot/flow/graph.py::_filtrar_acciones_repetidas dedupea por args
        # EXACTOS -- si el LLM reintenta crear_lead en el mismo turno con
        # razon_interes reformulado, esos args ya no matchean y la llamada
        # llega igual hasta aca. La idempotencia real tiene que vivir en el
        # impl: mismo rut, ventana chica de tiempo -> mismo lead.
        from leads.models import Lead
        primero = _crear_lead_impl(
            rut="11.111.111-1", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="quiere agendar un test drive",
        )
        segundo = _crear_lead_impl(
            rut="11.111.111-1", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="quiere agendar un test drive este fin de semana",
        )
        self.assertTrue(primero["ok"])
        self.assertTrue(segundo["ok"])
        self.assertEqual(primero["lead_id"], segundo["lead_id"])
        self.assertEqual(Lead.objects.using("qaintouch").filter(rut="11.111.111-1").count(), 1)

    def test_rut_distinto_igual_crea_un_lead_nuevo(self):
        from leads.models import Lead
        _crear_lead_impl(
            rut="11.111.111-1", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="quiere agendar un test drive",
        )
        _crear_lead_impl(
            rut="22.222.222-2", nombre="Maria Soto", telefono="+56933334444",
            razon_interes="quiere cotizar financiamiento",
        )
        self.assertEqual(Lead.objects.using("qaintouch").count(), 2)

    def test_sin_rut_no_deduplica_por_no_tener_identidad_confiable(self):
        from leads.models import Lead
        primero = _crear_lead_impl(
            rut="", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="quiere agendar un test drive",
        )
        segundo = _crear_lead_impl(
            rut="", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="quiere agendar un test drive este fin de semana",
        )
        self.assertNotEqual(primero["lead_id"], segundo["lead_id"])
        self.assertEqual(Lead.objects.using("qaintouch").filter(rut="").count(), 2)

    def test_fuera_de_la_ventana_de_dedup_crea_un_lead_nuevo(self):
        from datetime import timedelta
        from django.utils import timezone
        from leads.models import Lead
        primero = _crear_lead_impl(
            rut="11.111.111-1", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="quiere agendar un test drive",
        )
        Lead.objects.using("qaintouch").filter(pk=primero["lead_id"]).update(
            created_at=timezone.now() - timedelta(minutes=10),
        )
        segundo = _crear_lead_impl(
            rut="11.111.111-1", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="volvio a preguntar dias despues",
        )
        self.assertNotEqual(primero["lead_id"], segundo["lead_id"])
        self.assertEqual(Lead.objects.using("qaintouch").filter(rut="11.111.111-1").count(), 2)


class EncuestaServicioTecnicoAgentTest(TestCase):
    def test_tiene_descripcion_para_el_supervisor(self):
        from bot.flow.agents.encuesta_servicio_tecnico import EncuestaServicioTecnicoAgent
        agent = EncuestaServicioTecnicoAgent()
        self.assertTrue(agent.descripcion)

    def test_effective_prompt_sin_override_devuelve_el_default(self):
        from bot.flow.agents.encuesta_servicio_tecnico import EncuestaServicioTecnicoAgent, SYSTEM_PROMPT
        agent = EncuestaServicioTecnicoAgent()
        self.assertEqual(agent.effective_prompt(), SYSTEM_PROMPT)

    def test_business_actions_expone_las_2_tools(self):
        from bot.flow.agents.encuesta_servicio_tecnico import EncuestaServicioTecnicoAgent
        agent = EncuestaServicioTecnicoAgent()
        self.assertEqual(
            {t.name for t in agent.business_actions()},
            {"registrar_respuesta_encuesta_servicio_tecnico", "registrar_no_contactar"},
        )

    def test_build_system_prompt_incluye_nombre_de_whatsapp_si_esta_en_el_state(self):
        from bot.flow.agents.encuesta_servicio_tecnico import EncuestaServicioTecnicoAgent, SYSTEM_PROMPT
        agent = EncuestaServicioTecnicoAgent()
        prompt = agent.build_system_prompt({"name": "Felipe"}, SYSTEM_PROMPT)
        self.assertIn("Felipe", prompt)

    def test_build_system_prompt_sin_nombre_no_agrega_el_bloque(self):
        from bot.flow.agents.encuesta_servicio_tecnico import EncuestaServicioTecnicoAgent, SYSTEM_PROMPT
        agent = EncuestaServicioTecnicoAgent()
        prompt = agent.build_system_prompt({}, SYSTEM_PROMPT)
        self.assertNotIn("Nombre de perfil de WhatsApp", prompt)


class EncuestaVentaAutoNuevoAgentTest(TestCase):
    def test_tiene_descripcion_para_el_supervisor(self):
        from bot.flow.agents.encuesta_venta_auto_nuevo import EncuestaVentaAutoNuevoAgent
        agent = EncuestaVentaAutoNuevoAgent()
        self.assertTrue(agent.descripcion)

    def test_effective_prompt_sin_override_devuelve_el_default(self):
        from bot.flow.agents.encuesta_venta_auto_nuevo import EncuestaVentaAutoNuevoAgent, SYSTEM_PROMPT
        agent = EncuestaVentaAutoNuevoAgent()
        self.assertEqual(agent.effective_prompt(), SYSTEM_PROMPT)

    def test_business_actions_expone_las_3_tools(self):
        from bot.flow.agents.encuesta_venta_auto_nuevo import EncuestaVentaAutoNuevoAgent
        agent = EncuestaVentaAutoNuevoAgent()
        self.assertEqual(
            {t.name for t in agent.business_actions()},
            {"registrar_respuesta_encuesta_venta_auto_nuevo", "consultar_base_conocimiento", "registrar_no_contactar"},
        )

    def test_build_system_prompt_incluye_nombre_de_whatsapp_si_esta_en_el_state(self):
        from bot.flow.agents.encuesta_venta_auto_nuevo import EncuestaVentaAutoNuevoAgent, SYSTEM_PROMPT
        agent = EncuestaVentaAutoNuevoAgent()
        prompt = agent.build_system_prompt({"name": "Felipe"}, SYSTEM_PROMPT)
        self.assertIn("Felipe", prompt)

    def test_build_system_prompt_sin_nombre_no_agrega_el_bloque(self):
        from bot.flow.agents.encuesta_venta_auto_nuevo import EncuestaVentaAutoNuevoAgent, SYSTEM_PROMPT
        agent = EncuestaVentaAutoNuevoAgent()
        prompt = agent.build_system_prompt({}, SYSTEM_PROMPT)
        self.assertNotIn("Nombre de perfil de WhatsApp", prompt)


class BuildAgentRegistryEncuestasTest(TestCase):
    """Cavem no registra los especialistas de encuesta de Renault: el supervisor solo puede
    rutear a lo que ve, y una queja de taller calza semanticamente con una encuesta de
    satisfaccion. Ver el comentario en bot/flow/agents/__init__.py."""

    def test_no_registra_los_especialistas_de_encuesta(self):
        from bot.flow.agents import build_agent_registry
        registry = build_agent_registry()
        self.assertNotIn("encuesta_servicio_tecnico", registry)
        self.assertNotIn("encuesta_venta_auto_nuevo", registry)

    def test_el_codigo_de_los_especialistas_sigue_existiendo_por_linaje(self):
        # Se sacaron del registro, no del repo: si Cavem alguna vez corre una
        # campana de encuesta, se reactivan agregandolos de vuelta a AGENTS.
        from bot.flow.agents import AGENTES_NO_REGISTRADOS
        self.assertEqual(
            set(AGENTES_NO_REGISTRADOS),
            {"encuesta_servicio_tecnico", "encuesta_venta_auto_nuevo"},
        )
