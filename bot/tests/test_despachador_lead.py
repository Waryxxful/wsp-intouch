"""El adaptador de salida hacia el endpoint de leads del orquestador.

Arranca apagado (LEAD_SINK=none) porque ese endpoint es el spec B y todavía no
existe. Lo que se prueba acá es que cuando exista, conmutarlo sea sólo
configuración -- y que la clave de idempotencia esté lista, que es el punto que
el prompt de origen pedía y que la instrucción al modelo no puede cumplir
frente a reentregas de WhatsApp.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from bot.business.lead_intouch import (
    _registrar_lead_impl, clave_idempotencia, payload_del_lead,
)
from bot.models import Conversation, LeadInTouch


class ApagadoTest(TestCase):
    @override_settings(LEAD_SINK="none")
    def test_con_none_no_sale_ninguna_peticion(self):
        Conversation.objects.create(wa_id="56900000010")
        with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
            _registrar_lead_impl("56900000010", {"empresa": "Acme SpA"})
        enviar.assert_not_called()
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://orquestador:9000/api/leads")
class EncendidoTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56900000011")

    def test_despacha_y_sella(self):
        with patch("bot.business.lead_intouch._enviar_al_sink", return_value=True):
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertIsNotNone(LeadInTouch.objects.get().despachado_en)

    def test_un_fallo_deja_el_lead_sin_sellar_para_reintentarlo(self):
        # Un lead sin despachar tiene que ser VISIBLE: el sello es la única
        # forma de saber cuáles quedaron afuera.
        with patch("bot.business.lead_intouch._enviar_al_sink", return_value=False):
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)

    def test_un_fallo_no_tumba_la_escritura(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   side_effect=RuntimeError("endpoint caído")):
            resultado = _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertTrue(resultado["ok"])
        self.assertEqual(LeadInTouch.objects.get().empresa, "Acme SpA")

    def test_no_despacha_dos_veces_el_mismo_lead(self):
        with patch("bot.business.lead_intouch._enviar_al_sink", return_value=True) as enviar:
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
            _registrar_lead_impl("56900000011", {"cargo": "Gerente"})
        self.assertEqual(enviar.call_count, 1)

    def test_un_sink_desconocido_no_despacha_y_deja_ruido(self):
        with override_settings(LEAD_SINK="ftp"):
            with self.assertLogs("bot.business.lead_intouch", level="WARNING"):
                _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)


class IdempotenciaTest(TestCase):
    def test_la_clave_es_estable_entre_llamadas(self):
        conv = Conversation.objects.create(wa_id="56900000012")
        lead = LeadInTouch.objects.create(conversation=conv, empresa="Acme SpA")
        self.assertEqual(clave_idempotencia(lead), clave_idempotencia(lead))

    def test_la_clave_es_distinta_por_conversacion(self):
        primera = LeadInTouch.objects.create(
            conversation=Conversation.objects.create(wa_id="56900000013"))
        segunda = LeadInTouch.objects.create(
            conversation=Conversation.objects.create(wa_id="56900000014"))
        self.assertNotEqual(clave_idempotencia(primera), clave_idempotencia(segunda))

    def test_la_clave_no_expone_el_telefono(self):
        # Viaja a otro sistema: no tiene por qué llevar un dato personal en
        # claro cuando un hash cumple la misma función.
        conv = Conversation.objects.create(wa_id="56900000015")
        lead = LeadInTouch.objects.create(conversation=conv)
        self.assertNotIn("56900000015", clave_idempotencia(lead))


class PayloadTest(TestCase):
    def test_lleva_los_campos_del_contrato_y_la_clave(self):
        conv = Conversation.objects.create(wa_id="56900000016")
        lead = LeadInTouch.objects.create(
            conversation=conv, empresa="Acme SpA", correo="ana@acme.cl",
            lead_score="WARM", canales_actuales=["whatsapp"])
        payload = payload_del_lead(lead)
        self.assertEqual(payload["empresa"], "Acme SpA")
        self.assertEqual(payload["lead_score"], "WARM")
        self.assertEqual(payload["canales_actuales"], ["whatsapp"])
        self.assertIn("clave_idempotencia", payload)
        self.assertEqual(payload["origen"], "wsp_intouch")

    def test_lleva_el_telefono_del_wa_id(self):
        # El contrato del prompt §8 no lo incluye porque el LLM no debe
        # pedirlo, pero el equipo comercial necesita a quién llamar: lo agrega
        # la plataforma desde metadatos confiables, no el modelo.
        conv = Conversation.objects.create(wa_id="56900000017")
        lead = LeadInTouch.objects.create(conversation=conv)
        self.assertEqual(payload_del_lead(lead)["telefono"], "56900000017")
