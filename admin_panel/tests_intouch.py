"""El panel de leads. Es la vía por la que el equipo comercial ve las
oportunidades mientras el endpoint del orquestador no exista (spec §7.4).
"""
import json

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
