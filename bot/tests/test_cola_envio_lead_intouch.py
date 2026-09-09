"""El lead se escribe DESPUÉS de que el mensaje salió, y su fallo no cuesta
los metadatos del turno.

El orden importa: los metadatos de la conversación se guardan primero y el
lead va en su propio try. Si se invirtiera, un fallo escribiendo el lead
costaría el handoff -- que es la función más delicada del bot.
"""
from unittest.mock import patch

from django.test import TestCase

from bot.models import Conversation, LeadInTouch
from bot.whatsapp.cola_envio import _persistir_metadatos


class OrdenYAislamientoTest(TestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56966666666")

    def test_el_lead_se_escribe_desde_los_metadatos(self):
        _persistir_metadatos(self.conv, {
            "stage": "diagnostico",
            "lead": {"empresa": "Acme SpA", "necesidad_principal": "Ordenar la atención.",
                     "senales": {"encaje_con_oferta": True, "necesidad_concreta": True,
                                 "interes_evaluar": True}},
        })
        lead = LeadInTouch.objects.get()
        self.assertEqual(lead.empresa, "Acme SpA")
        self.assertEqual(lead.lead_score, "WARM")

    def test_un_fallo_del_lead_no_pierde_los_metadatos(self):
        with patch("bot.business.lead_intouch._registrar_lead_impl",
                   side_effect=RuntimeError("BD caída")):
            _persistir_metadatos(self.conv, {
                "stage": "diagnostico",
                "lead": {"empresa": "Acme SpA"},
            })
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.stage, "diagnostico")
        self.assertEqual(LeadInTouch.objects.count(), 0)

    def test_sin_lead_en_los_metadatos_no_pasa_nada(self):
        _persistir_metadatos(self.conv, {"stage": "nuevo"})
        self.assertEqual(LeadInTouch.objects.count(), 0)

    def test_el_handoff_sigue_generando_incidente(self):
        # Defensa heredada: no puede perderse al cambiar el lead.
        from bot.models import Incident

        _persistir_metadatos(self.conv, {
            "handoff": True, "handoff_reason": "El contacto pidió hablar con alguien.",
        })
        self.assertTrue(Incident.objects.filter(kind="handoff").exists())
