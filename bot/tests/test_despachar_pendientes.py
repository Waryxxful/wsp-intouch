"""El barrido que recupera los leads que no se pudieron despachar.

Sin esto, el despacho solo ocurre cuando llega un turno nuevo: si el envio
falla y la conversacion termina ahi, el lead no llega nunca. Es la falla que
el spec §5 punto 4 y las pruebas I09/I10 cubren.
"""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from bot.models import Conversation, LeadInTouch


def _lead(wa_id, **kwargs):
    conv = Conversation.objects.create(wa_id=wa_id)
    return LeadInTouch.objects.create(conversation=conv, **kwargs)


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x",
                   LEAD_SINK_TOKEN="secreto_prueba")
class PendientesTest(TestCase):
    def test_despacha_el_que_quedo_sin_sellar(self):
        _lead("56900000040", empresa="Acme SpA")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "ok",
                                 "cuerpo": {"status": "created", "contactId": "c1",
                                           "dealId": None}}) as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_called_once()
        self.assertIsNotNone(LeadInTouch.objects.get().despachado_en)

    def test_no_toca_el_que_ya_se_despacho(self):
        _lead("56900000041", empresa="Acme SpA", despachado_en=timezone.now(),
              evento_id="ya", payload_hash="x", revision=1)
        with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_not_called()

    def test_reintenta_con_el_mismo_evento_id(self):
        # El punto de I09: si el CRM ya hizo commit y se perdio la respuesta,
        # reintentar con la MISMA clave devuelve el mismo id en vez de duplicar.
        lead = _lead("56900000042", empresa="Acme SpA")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "fallo", "motivo": "el receptor respondió 500"}):
            call_command("despachar_leads_pendientes", stdout=StringIO())
        lead.refresh_from_db()
        primero = lead.evento_id
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "ok",
                                 "cuerpo": {"status": "replayed", "contactId": "c1",
                                           "dealId": None}}) as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviado = enviar.call_args[0][0]
        self.assertEqual(enviado["evento_id"], primero)

    def test_un_fallo_no_detiene_a_los_demas(self):
        _lead("56900000043", empresa="Una SpA")
        _lead("56900000044", empresa="Otra SpA")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   side_effect=[RuntimeError("caido"),
                                {"resultado": "ok",
                                 "cuerpo": {"status": "created", "contactId": "c2",
                                           "dealId": None}}]):
            call_command("despachar_leads_pendientes", stdout=StringIO())
        sellados = LeadInTouch.objects.exclude(despachado_en=None).count()
        self.assertEqual(sellados, 1)

    def test_un_conflicto_se_marca_no_se_sella_y_se_cuenta_en_la_salida(self):
        _lead("56900000047", empresa="Acme SpA")
        salida = StringIO()
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"resultado": "conflicto",
                                 "motivo": "el teléfono ya está en otro contacto"}):
            call_command("despachar_leads_pendientes", stdout=salida)
        lead = LeadInTouch.objects.get()
        self.assertIsNone(lead.despachado_en)
        self.assertIsNotNone(lead.conflicto_en)
        self.assertEqual(lead.conflicto_motivo, "el teléfono ya está en otro contacto")
        self.assertIn("conflicto", salida.getvalue().lower())
        self.assertIn("1", salida.getvalue())

    def test_no_reintenta_un_lead_ya_marcado_en_conflicto(self):
        # El punto central del arreglo: un conflicto no se resuelve
        # reintentando, asi que el barrido no puede seguir mandandolo cada
        # vez que corre -- eso era exactamente lo que pasaba antes, un 409
        # cada 15 minutos para siempre.
        _lead("56900000048", empresa="Acme SpA", conflicto_en=timezone.now(),
              conflicto_motivo="el correo ya está en otro contacto",
              evento_id="ya", payload_hash="x", revision=1)
        with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_not_called()

    def test_un_lead_en_conflicto_se_reporta_aunque_no_haya_pendientes(self):
        # Un conflicto silencioso es peor que uno ruidoso: el barrido no
        # puede decir "0 pendientes" mientras hay leads que nadie trabaja.
        _lead("56900000049", empresa="Acme SpA", conflicto_en=timezone.now(),
              conflicto_motivo="motivo", evento_id="ya", payload_hash="x", revision=1)
        salida = StringIO()
        with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
            call_command("despachar_leads_pendientes", stdout=salida)
        enviar.assert_not_called()
        self.assertIn("conflicto", salida.getvalue().lower())

    def test_con_el_sink_apagado_no_hace_nada(self):
        _lead("56900000045", empresa="Acme SpA")
        with override_settings(LEAD_SINK="none"):
            with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
                call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_not_called()

    def test_no_despacha_un_lead_sin_ningun_antecedente(self):
        # Una fila vacia no es un lead que valga la pena mandar.
        _lead("56900000046")
        with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_not_called()
