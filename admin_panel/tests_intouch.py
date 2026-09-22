"""El panel de leads. Es la vía por la que el equipo comercial ve las
oportunidades mientras el endpoint del orquestador no exista (spec §7.4).
"""
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from bot.models import Conversation, LeadInTouch


# DEBUG=True + sesion Django (no JWT del Orquestador): mismo patron de auth
# que las demas vistas del panel (ver admin_panel/auth_helpers.py y
# tests_cavem.py) -- api_leads_intouch usa el mismo @login_required que sus
# vecinas, porque expone datos de contacto reales (correo, empresa).
@override_settings(DEBUG=True)
class ApiLeadsTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "admin123")
        self.client.login(username="admin", password="admin123")
        conv = Conversation.objects.create(wa_id="56900000020", name="Ana")
        LeadInTouch.objects.create(
            conversation=conv, empresa="Acme SpA", correo="ana@acme.cl",
            industria="Retail", necesidad_principal="Ordenar la atención.",
            lead_score="HOT", canales_actuales=["whatsapp", "voz"],
            solicita_contacto_humano=True)
        LeadInTouch.objects.create(
            conversation=Conversation.objects.create(wa_id="56900000021"),
            empresa="Otra SpA", lead_score="COLD")

    def test_incluye_la_preferencia_horaria(self):
        lead = LeadInTouch.objects.get(empresa="Acme SpA")
        lead.preferencia_horaria = "martes por la mañana"
        lead.save(update_fields=["preferencia_horaria"])
        resp = self.client.get("/demo/api/leads")
        self.assertEqual(resp.status_code, 200)
        datos = json.loads(resp.content)
        primero = next(l for l in datos["leads"] if l["empresa"] == "Acme SpA")
        self.assertEqual(primero["preferencia_horaria"], "martes por la mañana")

    def test_devuelve_los_leads_con_su_telefono(self):
        resp = self.client.get("/demo/api/leads")
        self.assertEqual(resp.status_code, 200)
        datos = json.loads(resp.content)
        self.assertEqual(len(datos["leads"]), 2)
        primero = next(l for l in datos["leads"] if l["empresa"] == "Acme SpA")
        self.assertEqual(primero["telefono"], "56900000020")
        self.assertEqual(primero["canales_actuales"], ["whatsapp", "voz"])

    def test_filtra_por_score(self):
        resp = self.client.get("/demo/api/leads?lead_score=HOT")
        datos = json.loads(resp.content)
        self.assertEqual([l["empresa"] for l in datos["leads"]], ["Acme SpA"])

    def test_ordena_por_actualizacion_descendente(self):
        resp = self.client.get("/demo/api/leads")
        datos = json.loads(resp.content)
        self.assertEqual(datos["leads"][0]["empresa"], "Otra SpA")

    def test_muestra_si_falta_despachar(self):
        # Un lead sin despachar tiene que ser visible: es la única forma de
        # saber cuáles quedaron afuera cuando el sink esté encendido.
        resp = self.client.get("/demo/api/leads")
        datos = json.loads(resp.content)
        self.assertFalse(datos["leads"][0]["despachado"])

    @override_settings(LEAD_SINK="none")
    def test_sink_apagado_se_expone_en_la_respuesta(self):
        resp = self.client.get("/demo/api/leads")
        datos = json.loads(resp.content)
        self.assertFalse(datos["sink_activo"])

    @override_settings(LEAD_SINK="http")
    def test_sink_prendido_se_expone_en_la_respuesta(self):
        resp = self.client.get("/demo/api/leads")
        datos = json.loads(resp.content)
        self.assertTrue(datos["sink_activo"])


@override_settings(DEBUG=True)
class EstadoDespachoTest(TestCase):
    """Que el panel muestre si el lead llego al CRM, y deje reintentarlo.

    Es lo que vuelve accionable un fallo de despacho: sin esto, un lead que no
    llego solo se puede ver por SQL.

    DEBUG=True a proposito, mismo motivo que ApiLeadsTest arriba: el comando
    `manage.py test` fuerza DEBUG=False salvo `--debug-mode`, y sin DEBUG=True
    `grancrm_login_required` no acepta la sesion de Django de estos tests (no
    hay JWT del Orquestador aca), y las vistas redirigen (302) a login.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        User.objects.create_user("panel", password="x")
        self.client.login(username="panel", password="x")

    def _lead(self, wa_id, **kwargs):
        from bot.models import Conversation, LeadInTouch

        conv = Conversation.objects.create(wa_id=wa_id)
        return LeadInTouch.objects.create(
            conversation=conv, empresa="Acme SpA", **kwargs)

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_un_lead_despachado_trae_los_ids_del_crm(self):
        from django.utils import timezone

        self._lead("56900000050", despachado_en=timezone.now(),
                   crm_contact_id="c1", crm_deal_id="d1")
        datos = json.loads(self.client.get("/demo/api/leads").content)
        fila = datos["leads"][0]
        self.assertEqual(fila["estado_despacho"], "despachado")
        self.assertEqual(fila["crm_contact_id"], "c1")
        self.assertEqual(fila["crm_deal_id"], "d1")

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_un_lead_sin_sellar_queda_pendiente(self):
        self._lead("56900000051")
        datos = json.loads(self.client.get("/demo/api/leads").content)
        self.assertEqual(datos["leads"][0]["estado_despacho"], "pendiente")

    @override_settings(LEAD_SINK="none")
    def test_con_el_sink_apagado_no_dice_pendiente(self):
        # Con el destino apagado TODOS los leads estarian "pendientes" y la
        # señal se pierde: un estado propio evita el falso rojo.
        self._lead("56900000052")
        datos = json.loads(self.client.get("/demo/api/leads").content)
        self.assertEqual(datos["leads"][0]["estado_despacho"], "sin_destino")

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_el_reintento_despacha_y_devuelve_el_estado_nuevo(self):
        lead = self._lead("56900000053")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "ok",
                                 "cuerpo": {"status": "created", "contactId": "c9",
                                           "dealId": None}}):
            resp = self.client.post(f"/demo/api/leads/{lead.id}/reintentar")
        self.assertEqual(resp.status_code, 200)
        datos = json.loads(resp.content)
        self.assertEqual(datos["estado_despacho"], "despachado")
        self.assertEqual(datos["crm_contact_id"], "c9")

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_un_reintento_que_falla_lo_dice_sin_reventar(self):
        lead = self._lead("56900000054")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "fallo", "motivo": "el receptor respondió 500"}):
            resp = self.client.post(f"/demo/api/leads/{lead.id}/reintentar")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["estado_despacho"], "pendiente")

    def test_el_reintento_exige_POST(self):
        lead = self._lead("56900000055")
        self.assertEqual(
            self.client.get(f"/demo/api/leads/{lead.id}/reintentar").status_code, 405)

    def test_un_lead_que_no_existe_da_404(self):
        self.assertEqual(
            self.client.post("/demo/api/leads/999999/reintentar").status_code, 404)

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_un_lead_en_conflicto_aparece_con_su_propio_estado(self):
        # El cuarto estado del panel: distinto de "pendiente" porque un
        # conflicto no se arregla solo, necesita una persona en el CRM.
        from django.utils import timezone

        self._lead("56900000056", conflicto_en=timezone.now(),
                   conflicto_motivo="el teléfono ya está en otro contacto")
        datos = json.loads(self.client.get("/demo/api/leads").content)
        fila = datos["leads"][0]
        self.assertEqual(fila["estado_despacho"], "conflicto")
        self.assertEqual(fila["conflicto_motivo"], "el teléfono ya está en otro contacto")

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_el_reintento_manual_sigue_funcionando_sobre_un_conflicto(self):
        # Es la ÚNICA vía por la que un conflicto vuelve al circuito: el bot
        # no tiene forma de enterarse solo de que una persona lo resolvió del
        # lado del CRM, así que el reintento manual no puede estar bloqueado
        # por la marca de conflicto.
        from django.utils import timezone

        lead = self._lead("56900000057", conflicto_en=timezone.now(),
                          conflicto_motivo="el teléfono ya está en otro contacto")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "ok",
                                 "cuerpo": {"status": "created", "contactId": "c10",
                                           "dealId": None}}):
            resp = self.client.post(f"/demo/api/leads/{lead.id}/reintentar")
        self.assertEqual(resp.status_code, 200)
        datos = json.loads(resp.content)
        self.assertEqual(datos["estado_despacho"], "despachado")
        lead.refresh_from_db()
        self.assertIsNone(lead.conflicto_en)
        self.assertEqual(lead.conflicto_motivo, "")

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_el_reintento_manual_puede_volver_a_marcar_conflicto(self):
        # Si la persona todavía no arregló la identidad del lado del CRM, el
        # reintento manual puede volver a chocar con el mismo 409 -- y eso es
        # correcto, no un bug: se lo dice tal cual.
        lead = self._lead("56900000058")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "conflicto",
                                 "motivo": "el correo ya está en otro contacto"}):
            resp = self.client.post(f"/demo/api/leads/{lead.id}/reintentar")
        self.assertEqual(resp.status_code, 200)
        datos = json.loads(resp.content)
        self.assertEqual(datos["estado_despacho"], "conflicto")
        self.assertEqual(datos["conflicto_motivo"], "el correo ya está en otro contacto")
