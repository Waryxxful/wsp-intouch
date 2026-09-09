"""El lead comercial se captura FUERA del camino critico (docs/PENDIENTES.md 32.a).

Hasta el 2026-09-07 lo escribia la tool `registrar_datos_lead`, que el
especialista pedia en una SEGUNDA ronda de herramientas: una llamada entera al
LLM, 4,53s de mediana, en el 17,3% de los turnos medidos en Langfuse (n=324).

Lo que estos tests protegen:
  1. Que la tool ya NO este bindeada al especialista (si vuelve, vuelve la
     segunda ronda y nadie se entera por los tests).
  2. Que los DOS caminos de salida escriban el lead -- prosa (extractor) y
     `responder` (el especialista). Cubrir solo uno perderia el lead en
     silencio en el otro, que es el modo de falla de este cambio.
  3. Que reusar `_registrar_datos_lead_impl` conserve sus tres garantias: la
     validacion de `cuando_compra`, la derivacion de `vehiculo_codigo` y que un
     vacio no pise lo ya capturado.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, TransactionTestCase

from bot.business.prospeccion import registrar_lead_de_metadatos
from bot.flow.extractor_metadatos import SCHEMA_METADATOS
from bot.flow.respuesta import campos_de
from bot.models import Conversation, LeadComercial, Message, VehiculoUsado


class SchemaDelLeadTest(SimpleTestCase):
    def test_el_extractor_pide_el_lead(self):
        props = SCHEMA_METADATOS["schema"]["properties"]
        self.assertIn("lead", props)
        self.assertIn("lead", SCHEMA_METADATOS["schema"]["required"])

    def test_los_nombres_son_los_mismos_que_escribe_la_bd(self):
        # Un nombre distinto aca no da error: el campo simplemente no se
        # escribe y el dato se pierde en silencio.
        from bot.business.prospeccion import _CAMPOS_ESCRIBIBLES

        campos = set(SCHEMA_METADATOS["schema"]["properties"]["lead"]["properties"])
        # `cuando_compra` es el nombre que ve el modelo; la columna es plazo_compra.
        traducidos = (campos - {"cuando_compra"}) | {"plazo_compra"}
        self.assertTrue(traducidos <= _CAMPOS_ESCRIBIBLES, traducidos - _CAMPOS_ESCRIBIBLES)

    def test_los_montos_se_piden_como_texto(self):
        # Medido contra el LLM real el 2026-09-07: pidiendolos como entero,
        # "15 millones" y "20 palos" volvian en 0 (3 de 21 perdidos). Como
        # texto, 21 de 21 -- la conversion la hace el codigo.
        props = SCHEMA_METADATOS["schema"]["properties"]["lead"]["properties"]
        for campo in ("presupuesto", "pie_disponible", "cuota_objetivo"):
            self.assertEqual(props[campo]["type"], "string", campo)

    def test_strict_exige_todos_los_campos(self):
        lead = SCHEMA_METADATOS["schema"]["properties"]["lead"]
        self.assertEqual(set(lead["required"]), set(lead["properties"]))
        self.assertFalse(lead["additionalProperties"])

    def test_solo_ventas_declara_el_lead(self):
        self.assertIn("lead", campos_de("ventas"))
        self.assertNotIn("lead", campos_de("faq"))


class LaToolYaNoEstaBindeadaTest(SimpleTestCase):
    def test_ventas_no_tiene_registrar_datos_lead(self):
        from bot.flow.agents.custom import _TOOLS_VENTAS

        nombres = {t.name for t in _TOOLS_VENTAS}
        self.assertNotIn("registrar_datos_lead", nombres)

    def test_registrar_parte_pago_si_se_queda(self):
        # Escribe VehiculoPartePago con campos que el extractor no modela
        # (patente, km, estado, deuda) y dedup por marca_modelo: no se mueve.
        from bot.flow.agents.custom import _TOOLS_VENTAS

        self.assertIn("registrar_parte_pago", {t.name for t in _TOOLS_VENTAS})


class EscrituraDesdeMetadatosTest(TestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56922220001", name="Tomas")
        VehiculoUsado.objects.create(
            codigo="US011", marca="Suzuki", modelo="Swift", version="1.2 GLX CVT",
            anio=2023, km=22400, precio_lista=12490000, tipo_vehiculo="Hatchback",
            combustible="Gasolina", transmision="Automática CVT", es_automatico=True,
            disponibilidad="Disponible",
        )

    def test_escribe_los_campos_del_turno(self):
        registrar_lead_de_metadatos("56922220001", {
            "nombre": "Tomas Perez", "comuna": "Ñuñoa",
            "vehiculo_interes": "Suzuki Swift 2023",
            "intencion": "compra vehículo", "sentimiento": "positivo",
        })
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(lead.nombre, "Tomas Perez")
        self.assertEqual(lead.comuna, "Ñuñoa")
        self.assertEqual(lead.intencion, "compra vehículo")

    def test_los_montos_coloquiales_llegan_como_numero(self):
        registrar_lead_de_metadatos("56922220001", {
            "presupuesto": "15 millones", "pie_disponible": "$3.000.000",
            "cuota_objetivo": "500 mil",
        })
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(lead.presupuesto, 15000000)
        self.assertEqual(lead.pie_disponible, 3000000)
        self.assertEqual(lead.cuota_objetivo, 500000)

    def test_un_monto_ilegible_se_descarta_y_no_revienta(self):
        # La columna es IntegerField: escribir el string ahi pasa en SQLite y
        # revienta en SQL Server, o sea que el test verde mentiria.
        registrar_lead_de_metadatos("56922220001", {
            "presupuesto": "lo que sea necesario", "nombre": "Tomas"})
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertIsNone(lead.presupuesto)
        self.assertEqual(lead.nombre, "Tomas")

    def test_deriva_el_codigo_del_stock(self):
        registrar_lead_de_metadatos("56922220001", {"vehiculo_interes": "Suzuki Swift 2023"})
        self.assertEqual(LeadComercial.objects.get(conversation=self.conv).vehiculo_codigo, "US011")

    def test_rechaza_el_plazo_del_credito_como_fecha_de_compra(self):
        # El bug de la conversacion 29: "24 cuotas" es el plazo del CREDITO y
        # vale 18 puntos del score. La validacion vive en el impl, y este
        # camino la conserva porque lo reusa en vez de duplicarlo.
        registrar_lead_de_metadatos("56922220001", {"cuando_compra": "24 cuotas"})
        self.assertEqual(LeadComercial.objects.get(conversation=self.conv).plazo_compra, "")

    def test_acepta_la_fecha_si_el_cliente_la_dijo(self):
        Message.objects.create(conversation=self.conv, role="user",
                               content="quiero comprar este viernes")
        registrar_lead_de_metadatos("56922220001", {"cuando_compra": "este viernes"})
        self.assertEqual(
            LeadComercial.objects.get(conversation=self.conv).plazo_compra, "este viernes")

    def test_un_turno_sin_datos_nuevos_no_borra_lo_capturado(self):
        registrar_lead_de_metadatos("56922220001", {"nombre": "Tomas", "presupuesto": "15 millones"})
        registrar_lead_de_metadatos("56922220001", {"nombre": "", "presupuesto": ""})
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(lead.nombre, "Tomas")
        self.assertEqual(lead.presupuesto, 15000000)

    def test_calcula_el_score_y_la_temperatura(self):
        registrar_lead_de_metadatos("56922220001", {
            "nombre": "Tomas", "vehiculo_interes": "Suzuki Swift 2023",
            "presupuesto": "15 millones", "pie_disponible": "3 millones"})
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertGreater(lead.lead_score, 0)
        self.assertIn(lead.temperatura, ("HOT", "WARM", "COLD"))


class ElLeadEntraPorLosDosCaminosTest(TransactionTestCase):
    """Prosa (extractor, dentro de la cola) y `responder` (el especialista)."""

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56933330001", name="Tomas")

    def test_camino_de_prosa_escribe_el_lead(self):
        from bot.whatsapp.cola_envio import _enviar

        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value={"lead": {"nombre": "Tomas Perez",
                                          "presupuesto": "18 millones"}}):
            _enviar(self.conv.pk, self.conv.wa_id, [], None, [], MagicMock(),
                    metadatos={"prosa": "Te dejo las opciones.",
                               "mensaje_cliente": "tengo 18 millones",
                               "nombre_agente": "ventas"})
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(lead.nombre, "Tomas Perez")
        self.assertEqual(lead.presupuesto, 18000000)

    def test_una_lectura_de_la_conversacion_sola_no_abre_un_lead(self):
        # El extractor clasifica TODOS los turnos: llena resumen y
        # proxima_accion en el 100% y intencion en el 93% (n=58). Si eso abriera
        # la fila, un "hola" crearia un lead, y Campana.metricas() cuenta
        # LeadComercial como conversiones -> 100% de conversion en toda campana.
        from bot.whatsapp.cola_envio import _enviar

        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value={"lead": {"intencion": "compra vehículo",
                                          "sentimiento": "neutro",
                                          "resumen": "Cliente saluda.",
                                          "proxima_accion": "esperar respuesta"}}):
            _enviar(self.conv.pk, self.conv.wa_id, [], None, [], MagicMock(),
                    metadatos={"prosa": "¡Hola! ¿En qué te ayudo?",
                               "mensaje_cliente": "hola", "nombre_agente": "ventas"})
        self.assertFalse(LeadComercial.objects.filter(conversation=self.conv).exists())

    def test_sobre_un_lead_que_ya_existe_si_actualiza_la_lectura(self):
        # Mantener el resumen fresco es justamente para lo que sirve.
        from bot.whatsapp.cola_envio import _enviar

        LeadComercial.objects.create(conversation=self.conv, nombre="Tomas")
        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value={"lead": {"resumen": "Quiere un Swift.",
                                          "urgencia": "alta"}}):
            _enviar(self.conv.pk, self.conv.wa_id, [], None, [], MagicMock(),
                    metadatos={"prosa": "Te muestro el Swift.",
                               "mensaje_cliente": "y el swift?",
                               "nombre_agente": "ventas"})
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(lead.resumen, "Quiere un Swift.")
        self.assertEqual(lead.urgencia, "alta")

    def test_un_antecedente_del_cliente_si_abre_el_lead(self):
        from bot.whatsapp.cola_envio import _enviar

        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value={"lead": {"comuna": "Ñuñoa",
                                          "resumen": "Vive en Ñuñoa."}}):
            _enviar(self.conv.pk, self.conv.wa_id, [], None, [], MagicMock(),
                    metadatos={"prosa": "Perfecto.", "mensaje_cliente": "vivo en Nunoa",
                               "nombre_agente": "ventas"})
        self.assertEqual(LeadComercial.objects.get(conversation=self.conv).comuna, "Ñuñoa")

    def test_un_lead_vacio_no_crea_la_fila(self):
        from bot.whatsapp.cola_envio import _enviar

        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value={"lead": {"nombre": "", "presupuesto": ""}}):
            _enviar(self.conv.pk, self.conv.wa_id, [], None, [], MagicMock(),
                    metadatos={"prosa": "Hola.", "mensaje_cliente": "hola",
                               "nombre_agente": "ventas"})
        self.assertFalse(LeadComercial.objects.filter(conversation=self.conv).exists())

    def test_un_fallo_al_escribir_el_lead_no_tumba_el_resto(self):
        # El lead es lo ultimo del turno y ya nadie lo espera: su fallo no
        # puede costar los metadatos que ya estaban guardados.
        from bot.whatsapp.cola_envio import _enviar

        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value={"stage": "cotizacion", "lead": {"nombre": "Tomas"}}), \
             patch("bot.business.prospeccion.registrar_lead_de_metadatos",
                   side_effect=RuntimeError("BD caida")):
            _enviar(self.conv.pk, self.conv.wa_id, [], None, [], MagicMock(),
                    metadatos={"prosa": "Hola.", "mensaje_cliente": "hola",
                               "nombre_agente": "ventas"})
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.stage, "cotizacion")

    def test_camino_responder_escribe_el_lead(self):
        # `responder` no pasa por el extractor: sus metadatos ya vienen en los
        # args del tool_call, asi que el lead se escribe en el bloque
        # post-grafo de handlers.py. 3,9% de las llamadas medidas hoy.
        from bot.flow.respuesta import respuesta_de_tool_calls

        ai = MagicMock()
        ai.tool_calls = [{"name": "responder", "args": {
            "mensaje": "Perfecto, Tomas.",
            "lead": {"nombre": "Tomas Perez", "presupuesto": "12 millones"},
        }}]
        parsed = respuesta_de_tool_calls(ai, "ventas")
        self.assertEqual(parsed["lead"]["nombre"], "Tomas Perez")
        registrar_lead_de_metadatos(self.conv.wa_id, parsed["lead"])
        lead = LeadComercial.objects.get(conversation=self.conv)
        self.assertEqual(lead.nombre, "Tomas Perez")
        self.assertEqual(lead.presupuesto, 12000000)

    def test_el_lead_viaja_declarado_en_el_state(self):
        # Una clave no declarada en BotState LangGraph la descarta del
        # state-merge sin error -- bug ya sufrido en wsp_pompeyo y documentado
        # en state.py.
        from bot.flow.state import BotState

        self.assertIn("lead", BotState.__annotations__)
        self.assertIn("lead_faltante", BotState.__annotations__)


class BloqueDeAntecedentesTest(TestCase):
    """El feedback que la tool devolvia en `datos_que_faltan`, ahora
    deterministico y leido del state."""

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56944440001", name="Tomas")

    def test_sin_lead_todavia_no_dicta_nada(self):
        from bot.flow.agents._common import antecedentes_que_faltan

        self.assertEqual(antecedentes_que_faltan(self.conv), [])

    def test_lista_lo_que_falta_y_omite_lo_capturado(self):
        from bot.flow.agents._common import antecedentes_que_faltan

        LeadComercial.objects.create(conversation=self.conv, nombre="Tomas",
                                     vehiculo_interes="Swift")
        faltan = antecedentes_que_faltan(self.conv)
        self.assertNotIn("su nombre", faltan)
        self.assertNotIn("qué vehículo le interesa", faltan)
        self.assertIn("cuánto tiene pensado gastar", faltan)

    def test_el_bloque_no_toca_la_bd(self):
        # Corre dentro de un nodo async: una query aca levanta
        # SynchronousOnlyOperation (ya paso con bloque_sucursal_unica).
        from bot.flow.agents._common import bloque_datos_del_lead

        texto = bloque_datos_del_lead({"lead_faltante": ["su nombre"]})
        self.assertIn("su nombre", texto)
        self.assertEqual(bloque_datos_del_lead({}), "")

    def test_el_bloque_va_con_tildes(self):
        from bot.flow.agents._common import bloque_datos_del_lead

        texto = bloque_datos_del_lead({"lead_faltante": ["cuánto tiene pensado gastar"]})
        self.assertIn("todavía", texto)
        self.assertIn("conversación", texto)
