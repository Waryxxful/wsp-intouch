"""El score del lead se calcula en CÓDIGO, no lo pone el LLM (spec §7.3).

Motivo: es reproducible y auditable. Cuando alguien pregunte "y de dónde sale
que este lead es HOT", la respuesta es esta función y no el humor del modelo en
ese turno. Mismo criterio que `calcular_lead_score` de Cavem.

La precedencia es la del prompt §6, en ese orden: HOT, luego WARM, luego COLD,
si no NO_CALIFICADO.
"""
from django.test import SimpleTestCase, TestCase

from bot.models import Conversation, LeadInTouch, SenalesLead, calcular_lead_score


class PrecedenciaDelScoreTest(SimpleTestCase):
    def test_hot_necesita_necesidad_siguiente_paso_e_intencion(self):
        senales = SenalesLead(
            encaje_con_oferta=True, necesidad_concreta=True,
            solicita_siguiente_paso=True, intencion_avanzar_declarada=True,
        )
        self.assertEqual(calcular_lead_score(senales), "HOT")

    def test_hot_tambien_con_plazo_cercano_en_vez_de_intencion(self):
        senales = SenalesLead(
            encaje_con_oferta=True, necesidad_concreta=True,
            solicita_siguiente_paso=True, plazo_cercano_declarado=True,
        )
        self.assertEqual(calcular_lead_score(senales), "HOT")

    def test_pedir_reunion_sin_intencion_ni_plazo_no_es_hot(self):
        # El prompt §6 pide las dos cosas para HOT: pidió un siguiente paso Y
        # declaró intención de avanzar o un plazo cercano.
        senales = SenalesLead(
            encaje_con_oferta=True, necesidad_concreta=True,
            solicita_siguiente_paso=True, interes_evaluar=True,
        )
        self.assertEqual(calcular_lead_score(senales), "WARM")

    def test_warm_es_necesidad_concreta_con_interes_en_evaluar(self):
        senales = SenalesLead(
            encaje_con_oferta=True, necesidad_concreta=True, interes_evaluar=True,
        )
        self.assertEqual(calcular_lead_score(senales), "WARM")

    def test_cold_es_interes_exploratorio_sin_necesidad_concreta(self):
        senales = SenalesLead(encaje_con_oferta=True, interes_exploratorio=True)
        self.assertEqual(calcular_lead_score(senales), "COLD")

    def test_sin_encaje_con_la_oferta_no_califica_aunque_haya_urgencia(self):
        # Prompt §6: "o la necesidad conocida no encaja con la oferta". Alguien
        # que necesita urgente algo que InTouch no hace no es un lead HOT.
        senales = SenalesLead(
            encaje_con_oferta=False, necesidad_concreta=True,
            solicita_siguiente_paso=True, intencion_avanzar_declarada=True,
        )
        self.assertEqual(calcular_lead_score(senales), "NO_CALIFICADO")

    def test_sin_ninguna_senal_no_califica(self):
        self.assertEqual(calcular_lead_score(SenalesLead()), "NO_CALIFICADO")

    def test_es_reproducible(self):
        senales = SenalesLead(encaje_con_oferta=True, interes_exploratorio=True)
        self.assertEqual(
            {calcular_lead_score(senales) for _ in range(20)}, {"COLD"},
        )


class ConsistenciaDelContactCenterTest(TestCase):
    def test_no_tiene_fuerza_el_tipo_a_no_tiene(self):
        # Regla del prompt §8, validada en código y no confiada al prompt.
        conv = Conversation.objects.create(wa_id="56900000001")
        lead = LeadInTouch(conversation=conv, situacion_contact_center="no_tiene",
                           tipo_contact_center="propio")
        lead.save()
        lead.refresh_from_db()
        self.assertEqual(lead.tipo_contact_center, "no_tiene")

    def test_tiene_con_modalidad_desconocida_deja_el_tipo_vacio(self):
        conv = Conversation.objects.create(wa_id="56900000002")
        lead = LeadInTouch(conversation=conv, situacion_contact_center="tiene",
                           tipo_contact_center="")
        lead.save()
        lead.refresh_from_db()
        self.assertEqual(lead.situacion_contact_center, "tiene")
        self.assertEqual(lead.tipo_contact_center, "")

    def test_sin_confirmar_nada_los_dos_quedan_vacios(self):
        conv = Conversation.objects.create(wa_id="56900000003")
        lead = LeadInTouch(conversation=conv)
        lead.save()
        lead.refresh_from_db()
        self.assertEqual(lead.situacion_contact_center, "")
        self.assertEqual(lead.tipo_contact_center, "")


class UnLeadPorConversacionTest(TestCase):
    def test_es_onetoone(self):
        from django.db import IntegrityError

        conv = Conversation.objects.create(wa_id="56900000004")
        LeadInTouch.objects.create(conversation=conv)
        with self.assertRaises(IntegrityError):
            LeadInTouch.objects.create(conversation=conv)
