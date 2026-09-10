"""Lo que el emisor exige de la respuesta del receptor.

Un raise_for_status exitoso no prueba que el lead este en el pipeline: hay que
mirar el cuerpo. Y al reves, `dealId: null` SI es exito -- la politica del CRM
crea contactos sin oportunidad a proposito, y tratarlo como fallo dejaria
reintentando para siempre un lead que ya llego.
"""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from bot.business.lead_intouch import _enviar_al_sink, _registrar_lead_impl
from bot.models import Conversation, LeadInTouch


def _respuesta(cuerpo, status=201, content_type="application/json"):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = (
        cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode("utf-8"))
    resp.headers = {"Content-Type": content_type}
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: None
    return resp


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/api/ingest/intouch-lead",
                   LEAD_SINK_TOKEN="crm_prueba")
class RespuestaTest(TestCase):
    def _enviar(self, resp):
        with patch("urllib.request.urlopen", return_value=resp):
            return _enviar_al_sink({"origen": "wsp_intouch"})

    def test_un_created_con_ids_es_exito(self):
        r = self._enviar(_respuesta(
            {"status": "created", "contactId": "c1", "dealId": "d1"}))
        self.assertEqual(r["contactId"], "c1")

    def test_un_deal_id_nulo_tambien_es_exito(self):
        # Un lead COLD entra como contacto sin oportunidad: es la politica,
        # no una falla.
        r = self._enviar(_respuesta(
            {"status": "created", "contactId": "c1", "dealId": None}))
        self.assertEqual(r["contactId"], "c1")

    def test_un_replay_es_exito(self):
        r = self._enviar(_respuesta(
            {"status": "replayed", "contactId": "c1", "dealId": "d1"}, status=200))
        self.assertEqual(r["status"], "replayed")

    def test_un_2xx_sin_contact_id_es_fallo_de_contrato(self):
        self.assertIsNone(self._enviar(_respuesta({"status": "created"})))

    def test_un_2xx_con_status_desconocido_es_fallo(self):
        self.assertIsNone(self._enviar(_respuesta(
            {"status": "quiza", "contactId": "c1"})))

    def test_un_2xx_con_html_es_fallo(self):
        # El sintoma de un redirect a la pagina de login.
        self.assertIsNone(self._enviar(_respuesta(
            b"<html><body>Sign in</body></html>", status=200,
            content_type="text/html")))

    def test_un_2xx_con_json_invalido_es_fallo(self):
        self.assertIsNone(self._enviar(_respuesta(b"{roto", status=201)))

    def test_un_conflicto_es_fallo_y_no_se_sella(self):
        self.assertIsNone(self._enviar(_respuesta(
            {"status": "conflict", "motivo": "otro contenido"}, status=409)))

    def test_manda_el_token_en_la_cabecera(self):
        with patch("urllib.request.urlopen") as abrir:
            abrir.return_value = _respuesta(
                {"status": "created", "contactId": "c1", "dealId": None})
            _enviar_al_sink({"origen": "wsp_intouch"})
        peticion = abrir.call_args[0][0]
        self.assertEqual(peticion.get_header("X-intouch-ingest-key"), "crm_prueba")

    def test_el_token_no_aparece_en_los_logs(self):
        with self.assertLogs("bot.business.lead_intouch", level="DEBUG") as registro:
            with patch("urllib.request.urlopen", side_effect=RuntimeError("caido")):
                conv = Conversation.objects.create(wa_id="56900000030")
                _registrar_lead_impl("56900000030", {"empresa": "Acme SpA"})
        self.assertNotIn("crm_prueba", "\n".join(registro.output))


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/api/ingest/intouch-lead",
                   LEAD_SINK_TOKEN="crm_prueba")
class SelloTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56900000031")

    def test_un_exito_sella_y_guarda_el_id_del_crm(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "created", "contactId": "c1", "dealId": "d1"}):
            _registrar_lead_impl("56900000031", {"empresa": "Acme SpA"})
        lead = LeadInTouch.objects.get()
        self.assertIsNotNone(lead.despachado_en)
        self.assertEqual(lead.crm_contact_id, "c1")
        self.assertEqual(lead.crm_deal_id, "d1")

    def test_un_fallo_de_contrato_no_sella(self):
        with patch("bot.business.lead_intouch._enviar_al_sink", return_value=None):
            _registrar_lead_impl("56900000031", {"empresa": "Acme SpA"})
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)
