from django.test import TestCase

from bot.models import Conversation
from bot.simulator.marking import (
    LEAD_RAZON_PREFIJO,
    generar_wa_id_test,
    marcar_lead_de_test,
    nombre_contacto_test,
)


class GenerarWaIdTestTest(TestCase):
    def test_primer_wa_id_de_test_es_correlativo_uno(self):
        self.assertEqual(generar_wa_id_test(), "TEST000001")

    def test_wa_id_de_test_continua_desde_el_maximo_existente(self):
        Conversation.objects.create(wa_id="TEST000007")
        Conversation.objects.create(wa_id="56911112222")  # conversacion real, no debe contar
        self.assertEqual(generar_wa_id_test(), "TEST000008")

    def test_wa_id_de_test_no_supera_20_caracteres(self):
        self.assertLessEqual(len(generar_wa_id_test()), 20)


class NombreContactoTestTest(TestCase):
    def test_antepone_el_prefijo_de_prueba(self):
        self.assertEqual(nombre_contacto_test("Cliente indeciso"), "[PRUEBA] Cliente indeciso")

    def test_trunca_la_persona_a_40_caracteres(self):
        nombre = nombre_contacto_test("x" * 100)
        self.assertEqual(nombre, "[PRUEBA] " + "x" * 40)


class MarcarLeadDeTestTest(TestCase):
    databases = {"default", "qaintouch"}

    def test_antepone_el_prefijo_a_razon_interes(self):
        from leads.models import Lead

        lead = Lead.objects.create(
            rut="11.111.111-1", nombre="Prueba", telefono="+56900000000",
            razon_interes="Quiere cotizar",
        )
        marcar_lead_de_test(lead.id)
        lead.refresh_from_db()
        self.assertEqual(lead.razon_interes, f"{LEAD_RAZON_PREFIJO}Quiere cotizar")

    def test_no_duplica_el_prefijo_si_ya_esta(self):
        from leads.models import Lead

        lead = Lead.objects.create(
            rut="11.111.111-1", nombre="Prueba", telefono="+56900000000",
            razon_interes=f"{LEAD_RAZON_PREFIJO}Quiere cotizar",
        )
        marcar_lead_de_test(lead.id)
        lead.refresh_from_db()
        self.assertEqual(lead.razon_interes, f"{LEAD_RAZON_PREFIJO}Quiere cotizar")
