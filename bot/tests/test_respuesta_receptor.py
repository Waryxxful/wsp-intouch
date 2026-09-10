"""Lo que el emisor exige de la respuesta del receptor.

Un raise_for_status exitoso no prueba que el lead este en el pipeline: hay que
mirar el cuerpo. Y al reves, `dealId: null` SI es exito -- la politica del CRM
crea contactos sin oportunidad a proposito, y tratarlo como fallo dejaria
reintentando para siempre un lead que ya llego.

Los tres resultados de `_enviar_al_sink` (spec de la tarea del conflicto 409):
"ok", "conflicto" y "fallo". Se clasifican por el CÓDIGO HTTP, no por el
cuerpo -- un 409 es SIEMPRE conflicto, aunque su cuerpo no traiga el campo
`motivo`; y un 2xx con contrato incompleto es SIEMPRE fallo, aunque su
`status` diga algo distinto.
"""
import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from bot.business import lead_intouch
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


def _http_error(status, cuerpo=None, content_type="application/json"):
    """Una `urllib.error.HTTPError` como la que levanta `urlopen` real ante
    un 4xx/5xx -- lo que hoy dispara la rama de excepción de `_enviar_al_sink`.
    """
    cuerpo_bytes = b"" if cuerpo is None else (
        cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode("utf-8"))
    cabeceras = MagicMock()
    cabeceras.get.return_value = content_type
    return urllib.error.HTTPError(
        "http://crm-api:3001/api/ingest/intouch-lead", status, "error",
        cabeceras, io.BytesIO(cuerpo_bytes))


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/api/ingest/intouch-lead",
                   LEAD_SINK_TOKEN="crm_prueba")
class RespuestaTest(TestCase):
    def _enviar(self, resp_o_error):
        with patch("urllib.request.urlopen", side_effect=resp_o_error) if isinstance(
                resp_o_error, Exception) else patch(
                "urllib.request.urlopen", return_value=resp_o_error):
            return lead_intouch._enviar_al_sink({"origen": "wsp_intouch"})

    # --- "ok": 2xx con el contrato válido -----------------------------------

    def test_un_created_con_ids_es_exito(self):
        r = self._enviar(_respuesta(
            {"status": "created", "contactId": "c1", "dealId": "d1"}))
        self.assertEqual(r["resultado"], "ok")
        self.assertEqual(r["cuerpo"]["contactId"], "c1")

    def test_un_deal_id_nulo_tambien_es_exito(self):
        # Un lead COLD entra como contacto sin oportunidad: es la politica,
        # no una falla.
        r = self._enviar(_respuesta(
            {"status": "created", "contactId": "c1", "dealId": None}))
        self.assertEqual(r["resultado"], "ok")
        self.assertEqual(r["cuerpo"]["contactId"], "c1")

    def test_un_replay_es_exito(self):
        r = self._enviar(_respuesta(
            {"status": "replayed", "contactId": "c1", "dealId": "d1"}, status=200))
        self.assertEqual(r["resultado"], "ok")
        self.assertEqual(r["cuerpo"]["status"], "replayed")

    # --- "fallo": todo lo demás, reintentable --------------------------------

    def test_un_2xx_sin_contact_id_es_fallo_de_contrato(self):
        r = self._enviar(_respuesta({"status": "created"}))
        self.assertEqual(r["resultado"], "fallo")

    def test_un_2xx_con_status_desconocido_es_fallo(self):
        r = self._enviar(_respuesta({"status": "quiza", "contactId": "c1"}))
        self.assertEqual(r["resultado"], "fallo")

    def test_un_2xx_con_html_es_fallo(self):
        # El sintoma de un redirect a la pagina de login.
        r = self._enviar(_respuesta(
            b"<html><body>Sign in</body></html>", status=200,
            content_type="text/html"))
        self.assertEqual(r["resultado"], "fallo")

    def test_un_2xx_con_json_invalido_es_fallo(self):
        r = self._enviar(_respuesta(b"{roto", status=201))
        self.assertEqual(r["resultado"], "fallo")

    def test_un_500_es_fallo(self):
        r = self._enviar(_http_error(500, {"status": "error"}))
        self.assertEqual(r["resultado"], "fallo")
        self.assertIn("500", r["motivo"])

    def test_un_503_sin_cuerpo_es_fallo(self):
        # Un timeout o un servicio caido puede no traer cuerpo -- no puede
        # reventar la clasificacion.
        r = self._enviar(_http_error(503))
        self.assertEqual(r["resultado"], "fallo")

    # --- "conflicto": 409, no se reintenta sola ------------------------------

    def test_un_409_es_conflicto_y_no_fallo(self):
        # ANTES esto se clasificaba como fallo (None): un 409 se perdia entre
        # timeouts y 500, y el barrido lo reintentaba cada 15 minutos para
        # siempre. Ahora es su propio resultado.
        r = self._enviar(_http_error(
            409, {"status": "conflict", "motivo": "el teléfono ya está en otro contacto"}))
        self.assertEqual(r["resultado"], "conflicto")
        self.assertEqual(r["motivo"], "el teléfono ya está en otro contacto")

    def test_un_409_sin_motivo_en_el_cuerpo_igual_es_conflicto(self):
        # Se clasifica por el CÓDIGO, no por el cuerpo: aunque el receptor no
        # mande el campo `motivo`, el 409 sigue siendo un conflicto y no un
        # fallo cualquiera -- sólo cambia el texto que se guarda.
        r = self._enviar(_http_error(409, cuerpo=None))
        self.assertEqual(r["resultado"], "conflicto")
        self.assertTrue(r["motivo"])

    def test_un_409_con_json_roto_igual_es_conflicto(self):
        r = self._enviar(_http_error(409, cuerpo=b"{roto"))
        self.assertEqual(r["resultado"], "conflicto")
        self.assertTrue(r["motivo"])

    # --- cabeceras y logs -----------------------------------------------------

    def test_manda_el_token_en_la_cabecera(self):
        with patch("urllib.request.urlopen") as abrir:
            abrir.return_value = _respuesta(
                {"status": "created", "contactId": "c1", "dealId": None})
            lead_intouch._enviar_al_sink({"origen": "wsp_intouch"})
        peticion = abrir.call_args[0][0]
        self.assertEqual(peticion.get_header("X-intouch-ingest-key"), "crm_prueba")

    def test_el_token_no_aparece_en_los_logs(self):
        with self.assertLogs("bot.business.lead_intouch", level="DEBUG") as registro:
            with patch("urllib.request.urlopen", side_effect=RuntimeError("caido")):
                Conversation.objects.create(wa_id="56900000030")
                lead_intouch._registrar_lead_impl("56900000030", {"empresa": "Acme SpA"})
        self.assertNotIn("crm_prueba", "\n".join(registro.output))


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/api/ingest/intouch-lead",
                   LEAD_SINK_TOKEN="crm_prueba")
class SelloTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56900000031")

    def test_un_exito_sella_y_guarda_el_id_del_crm(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "ok",
                                 "cuerpo": {"status": "created", "contactId": "c1",
                                           "dealId": "d1"}}):
            lead_intouch._registrar_lead_impl("56900000031", {"empresa": "Acme SpA"})
        lead = LeadInTouch.objects.get()
        self.assertIsNotNone(lead.despachado_en)
        self.assertEqual(lead.crm_contact_id, "c1")
        self.assertEqual(lead.crm_deal_id, "d1")

    def test_un_fallo_de_contrato_no_sella(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "fallo", "motivo": "el receptor no devolvió contactId"}):
            lead_intouch._registrar_lead_impl("56900000031", {"empresa": "Acme SpA"})
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)

    def test_un_conflicto_no_sella_y_marca_el_lead(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "conflicto",
                                 "motivo": "el teléfono ya está en otro contacto"}):
            lead_intouch._registrar_lead_impl("56900000031", {"empresa": "Acme SpA"})
        lead = LeadInTouch.objects.get()
        self.assertIsNone(lead.despachado_en)
        self.assertIsNotNone(lead.conflicto_en)
        self.assertEqual(lead.conflicto_motivo, "el teléfono ya está en otro contacto")
