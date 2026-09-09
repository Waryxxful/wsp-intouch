import json

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from bot.models import Campana, CampaignSend, Conversation, LeadComercial, registrar_optout


@override_settings(DEBUG=True)
class PanelCavemTestCase(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")

    def crear_lead(self, wa_id="56911111111", **kwargs):
        conv = Conversation.objects.create(wa_id=wa_id)
        datos = {"nombre": "Felipe Rojas", "telefono": wa_id,
                 "vehiculo_interes": "Hyundai Tucson 2023", "temperatura": "HOT",
                 "lead_score": 92}
        datos.update(kwargs)
        return LeadComercial.objects.create(conversation=conv, **datos)


@override_settings(DEBUG=True)
class LeadsEndpointTest(PanelCavemTestCase):
    def test_devuelve_los_antecedentes_del_docx(self):
        self.crear_lead(presupuesto=20000000, pie_disponible=5000000,
                        plazo_compra="30 dias", proxima_accion="contactar hoy")
        datos = json.loads(self.client.get("/demo/api/admin/leads").content)
        self.assertEqual(len(datos), 1)
        for campo in ("nombre", "telefono", "vehiculo_interes", "presupuesto",
                      "pie_disponible", "cuota_objetivo", "tiene_parte_pago",
                      "plazo_compra", "comuna", "intencion", "temperatura",
                      "lead_score", "proxima_accion", "ultima_interaccion"):
            self.assertIn(campo, datos[0])

    def test_ordena_por_lead_score_descendente(self):
        # El panel existe para saber a quien llamar primero, no para listar por fecha.
        self.crear_lead(wa_id="56911111111", lead_score=40, temperatura="WARM")
        self.crear_lead(wa_id="56922222222", lead_score=95, temperatura="HOT")
        datos = json.loads(self.client.get("/demo/api/admin/leads").content)
        self.assertEqual([l["lead_score"] for l in datos], [95, 40])

    def test_filtra_por_temperatura(self):
        self.crear_lead(wa_id="56911111111", temperatura="HOT", lead_score=90)
        self.crear_lead(wa_id="56922222222", temperatura="COLD", lead_score=10)
        datos = json.loads(self.client.get("/demo/api/admin/leads?temperatura=HOT").content)
        self.assertEqual(len(datos), 1)
        self.assertEqual(datos[0]["temperatura"], "HOT")

    def test_busca_por_nombre_o_vehiculo(self):
        self.crear_lead(wa_id="56911111111", nombre="Felipe Rojas")
        self.crear_lead(wa_id="56922222222", nombre="Ana Soto", vehiculo_interes="Kia Sportage")
        datos = json.loads(self.client.get("/demo/api/admin/leads?q=sportage").content)
        self.assertEqual([l["nombre"] for l in datos], ["Ana Soto"])

    def test_requiere_sesion(self):
        self.client.logout()
        self.assertNotEqual(self.client.get("/demo/api/admin/leads").status_code, 200)


@override_settings(DEBUG=True)
class CampanasEndpointTest(PanelCavemTestCase):
    def setUp(self):
        super().setUp()
        self.campana = Campana.objects.create(
            nombre="Cyber Auto Demo", campaign_type="cyber_auto_demo",
            template="cyber_auto_demo", palabra_clave="QUIERO",
            cliente=settings.CLIENTE_ACTIVO)

    def test_devuelve_el_embudo_del_docx(self):
        datos = json.loads(self.client.get("/demo/api/admin/campanas").content)
        for campo in ("nombre", "contactos", "enviados", "entregados", "leidos",
                      "respuestas", "leads", "conversiones"):
            self.assertIn(campo, datos[0])

    def test_entregados_y_leidos_van_en_null_no_inventados(self):
        # Esos dos numeros los sabe Meta, no nosotros. El panel los completa
        # con template-stats; devolverlos inventados seria justo lo que el
        # docx S15 prohibe.
        datos = json.loads(self.client.get("/demo/api/admin/campanas").content)
        self.assertIsNone(datos[0]["entregados"])
        self.assertIsNone(datos[0]["leidos"])

    def test_cuenta_envios_respuestas_y_leads_reales(self):
        conv = Conversation.objects.create(wa_id="56911111111")
        CampaignSend.objects.create(contacto="56911111111", campaign_type="cyber_auto_demo",
                                    template="cyber_auto_demo", conversation=conv, respondido=True)
        CampaignSend.objects.create(contacto="56922222222", campaign_type="cyber_auto_demo",
                                    template="cyber_auto_demo")
        LeadComercial.objects.create(conversation=conv, nombre="Felipe", temperatura="HOT")
        datos = json.loads(self.client.get("/demo/api/admin/campanas").content)[0]
        self.assertEqual(datos["contactos"], 2)
        self.assertEqual(datos["enviados"], 2)
        self.assertEqual(datos["respuestas"], 1)
        self.assertEqual(datos["leads"], 1)
        self.assertEqual(datos["conversiones"], 1)


@override_settings(DEBUG=True)
class EnviarCampanaTest(PanelCavemTestCase):
    def setUp(self):
        super().setUp()
        Campana.objects.create(nombre="Cyber Auto Demo", campaign_type="cyber_auto_demo",
                               template="cyber_auto_demo", cliente=settings.CLIENTE_ACTIVO)

    def _post(self, **body):
        return self.client.post("/demo/api/admin/campanas/enviar",
                                data=json.dumps(body), content_type="application/json")

    def test_campana_inexistente_se_rechaza(self):
        self.assertEqual(self._post(campaign_type="no_existe", csv_text="wa_id\n1").status_code, 400)

    def test_campana_inactiva_se_rechaza(self):
        Campana.objects.update(activa=False)
        self.assertEqual(
            self._post(campaign_type="cyber_auto_demo", csv_text="wa_id\n1").status_code, 400)

    def test_campana_sin_plantilla_se_rechaza(self):
        Campana.objects.update(template="")
        self.assertEqual(
            self._post(campaign_type="cyber_auto_demo", csv_text="wa_id\n1").status_code, 400)

    def test_csv_sin_columna_wa_id_se_rechaza(self):
        self.assertEqual(
            self._post(campaign_type="cyber_auto_demo", csv_text="telefono\n1").status_code, 400)

    def test_mas_de_200_filas_se_rechaza(self):
        # Un CSV pegado por error no puede disparar miles de mensajes.
        filas = "wa_id\n" + "\n".join(str(569_0000000 + i) for i in range(201))
        self.assertEqual(
            self._post(campaign_type="cyber_auto_demo", csv_text=filas).status_code, 400)

    def test_salta_los_contactos_con_opt_out(self):
        registrar_optout("56911111111", "no quiere")
        with self.settings(WHATSAPP_TOKEN="x", WHATSAPP_PHONE_ID="y"):
            from unittest.mock import patch
            with patch("bot.whatsapp.client.get_wa_client") as get_wa:
                get_wa.return_value.send_template.return_value = True
                resp = self._post(campaign_type="cyber_auto_demo", csv_text="wa_id\n56911111111")
                get_wa.return_value.send_template.assert_not_called()
        self.assertEqual(json.loads(resp.content)["optout_saltados"], 1)

    def test_un_envio_fallido_no_deja_al_contacto_marcado_como_contactado(self):
        # Si el CampaignSend se creara antes del envio, un fallo dejaria al
        # contacto excluido para siempre de una campana que nunca recibio.
        from unittest.mock import patch
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_template.return_value = False
            resp = self._post(campaign_type="cyber_auto_demo", csv_text="wa_id\n56911111111")
        self.assertEqual(json.loads(resp.content)["enviados"], 0)
        self.assertFalse(CampaignSend.objects.exists())

    def test_envio_exitoso_registra_el_campaign_send(self):
        from unittest.mock import patch
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_template.return_value = True
            resp = self._post(campaign_type="cyber_auto_demo", csv_text="wa_id\n56911111111")
        self.assertEqual(json.loads(resp.content)["enviados"], 1)
        self.assertEqual(CampaignSend.objects.get().campaign_type, "cyber_auto_demo")

    def test_no_reenvia_a_quien_todavia_no_responde(self):
        CampaignSend.objects.create(contacto="56911111111", campaign_type="cyber_auto_demo",
                                    template="cyber_auto_demo", respondido=False)
        from unittest.mock import patch
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_template.return_value = True
            resp = self._post(campaign_type="cyber_auto_demo", csv_text="wa_id\n56911111111")
            get_wa.return_value.send_template.assert_not_called()
        self.assertEqual(json.loads(resp.content)["enviados"], 0)


@override_settings(DEBUG=True)
class DashboardProyeccionTest(PanelCavemTestCase):
    def test_por_defecto_devuelve_datos_reales(self):
        # Si el default fuera la proyeccion, alguien podria abrir el panel un
        # dia cualquiera y leer cifras inventadas creyendo que son medidas.
        datos = json.loads(self.client.get("/demo/api/admin/dashboard").content)
        self.assertFalse(datos["es_proyeccion"])
        self.assertEqual(datos["conv_count"], 0)

    def test_modo_demo_devuelve_las_cifras_del_docx(self):
        datos = json.loads(self.client.get("/demo/api/admin/dashboard?modo=demo").content)
        self.assertTrue(datos["es_proyeccion"])
        self.assertEqual(datos["conv_count"], 1284)
        self.assertEqual(datos["kpis"]["tiempo_respuesta_promedio_seg"], 3)

    def test_el_embudo_de_proyeccion_sigue_el_docx(self):
        datos = json.loads(self.client.get("/demo/api/admin/dashboard?modo=demo").content)
        self.assertEqual([e["count"] for e in datos["funnel"]], [1284, 386, 214, 97, 31])

    def test_el_modo_demo_no_escribe_nada_en_la_base(self):
        # Las cifras son constantes en codigo: mezclarlas con filas reales
        # obligaria despues a limpiarlas de una BD que tambien tiene las
        # conversaciones de prueba.
        self.client.get("/demo/api/admin/dashboard?modo=demo")
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(LeadComercial.objects.count(), 0)

    def test_la_proyeccion_mantiene_la_forma_de_la_respuesta_real(self):
        # El panel usa el mismo componente para las dos vistas: si a la
        # proyeccion le falta una clave, el dashboard revienta al togglear.
        real = json.loads(self.client.get("/demo/api/admin/dashboard").content)
        demo = json.loads(self.client.get("/demo/api/admin/dashboard?modo=demo").content)
        self.assertTrue(set(real) <= set(demo), f"faltan en la proyeccion: {set(real) - set(demo)}")
        self.assertEqual(set(real["kpis"]), set(demo["kpis"]))


@override_settings(DEBUG=True)
class ConversacionesPanelTest(PanelCavemTestCase):
    """docx S18 pide Cliente, Telefono, Fecha, Intencion, Resumen IA, Estado y
    Lead score en la tabla de conversaciones."""

    def test_expone_intencion_resumen_y_lead_score(self):
        conv = Conversation.objects.create(wa_id="56911111111", name="Felipe", stage="cotizacion")
        LeadComercial.objects.create(
            conversation=conv, nombre="Felipe Rojas", intencion="compra vehiculo",
            resumen="Busca SUV usado, presupuesto 20 millones.", lead_score=92,
            temperatura="HOT")
        item = json.loads(self.client.get("/demo/api/conversations").content)["items"][0]
        self.assertEqual(item["intencion"], "compra vehiculo")
        self.assertEqual(item["lead_score"], 92)
        self.assertEqual(item["temperatura"], "HOT")
        self.assertIn("SUV usado", item["resumen"])
        self.assertEqual(item["stage"], "cotizacion")

    def test_una_conversacion_sin_lead_no_revienta(self):
        # La mayoria de las conversaciones no llegan a generar un lead.
        Conversation.objects.create(wa_id="56922222222", name="Ana")
        item = json.loads(self.client.get("/demo/api/conversations").content)["items"][0]
        self.assertEqual(item["intencion"], "")
        self.assertIsNone(item["lead_score"])

    def _queries_para(self, cantidad):
        Conversation.objects.all().delete()
        for i in range(cantidad):
            conv = Conversation.objects.create(wa_id=f"5690000{i:04d}")
            LeadComercial.objects.create(conversation=conv, nombre=f"C{i}")
        with CaptureQueriesContext(connection) as capturadas:
            self.client.get("/demo/api/conversations")
        return len(capturadas)

    def test_el_costo_en_queries_no_crece_con_la_cantidad_de_leads(self):
        # Se afirma que el numero NO CRECE, no un numero exacto: el exacto es
        # fragil (sesion, usuario, count, filas, incidents, leads) y se rompe
        # con cualquier middleware nuevo sin que haya un problema real. Lo que
        # importa es que el lookup de leads sea bulk, igual que el de incidents.
        self.assertEqual(self._queries_para(5), self._queries_para(20))


@override_settings(DEBUG=True)
class ReservasEndpointTest(PanelCavemTestCase):
    """docx S11: la reserva tiene que quedar visible en la plataforma con
    nombre, telefono, email, vehiculo, patente, servicio, fecha, hora y estado.
    No existia ningun endpoint de listado."""

    def setUp(self):
        super().setUp()
        from bot.models import Reserva, Servicio, Sucursal
        servicio = Servicio.objects.create(
            nombre="Mantencion 20.000 km", duracion_min=180, precio=219900,
            cliente=settings.CLIENTE_ACTIVO)
        sucursal = Sucursal.objects.create(
            nombre="Cavem La Reina", direccion="Av. Bilbao 1234",
            cliente=settings.CLIENTE_ACTIVO)
        Reserva.objects.create(
            codigo="ABC123", contacto="56911111111", cliente_nombre="Felipe Rojas",
            cliente_email="felipe@test.cl", vehiculo="Hyundai Tucson", patente="JKLM12",
            vehiculo_anio=2023, vehiculo_km=31800, servicio=servicio, sucursal=sucursal,
            fecha="2026-09-04", hora="11:30")

    def test_expone_todos_los_campos_del_docx(self):
        datos = json.loads(self.client.get("/demo/api/admin/reservas").content)
        self.assertEqual(len(datos), 1)
        for campo in ("cliente", "telefono", "email", "vehiculo", "patente",
                      "servicio", "fecha", "hora", "estado"):
            self.assertIn(campo, datos[0])
        self.assertEqual(datos[0]["vehiculo"], "Hyundai Tucson")
        self.assertEqual(datos[0]["patente"], "JKLM12")
        self.assertEqual(datos[0]["hora"], "11:30")

    def test_el_telefono_va_enmascarado_como_en_el_resto_del_panel(self):
        datos = json.loads(self.client.get("/demo/api/admin/reservas").content)
        self.assertNotEqual(datos[0]["telefono"], "56911111111")

    def test_filtra_por_estado(self):
        from bot.models import Reserva
        Reserva.objects.update(estado="cancelada")
        self.assertEqual(len(json.loads(
            self.client.get("/demo/api/admin/reservas?estado=activa").content)), 0)
        self.assertEqual(len(json.loads(
            self.client.get("/demo/api/admin/reservas?estado=cancelada").content)), 1)

    def test_requiere_sesion(self):
        self.client.logout()
        self.assertNotEqual(self.client.get("/demo/api/admin/reservas").status_code, 200)
