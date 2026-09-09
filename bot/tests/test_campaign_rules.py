from django.test import TestCase
from bot.flow.campaign_rules import CampaignRule, PRE_ROUTING_RULES, resolve_agent_for_campaign


class ResolveAgentForCampaignTest(TestCase):
    def setUp(self):
        self._original_rules = dict(PRE_ROUTING_RULES)
        PRE_ROUTING_RULES.clear()
        PRE_ROUTING_RULES["confirmar_agenda"] = CampaignRule(
            default_agent="confirmacion",
            override=lambda state: "reagendamiento" if state.get("cita_cancelada") else None,
        )
        PRE_ROUTING_RULES["agendar_hora"] = CampaignRule(
            default_agent="agendamiento",
            override=lambda state: "confirmacion" if state.get("ya_tiene_cita") else None,
        )

    def tearDown(self):
        PRE_ROUTING_RULES.clear()
        PRE_ROUTING_RULES.update(self._original_rules)

    def test_sin_campaign_hint_devuelve_none(self):
        self.assertIsNone(resolve_agent_for_campaign(None, {}))

    def test_campaign_hint_sin_regla_devuelve_none(self):
        self.assertIsNone(resolve_agent_for_campaign("promocion_desconocida", {}))

    def test_devuelve_agente_por_defecto_sin_override(self):
        self.assertEqual(resolve_agent_for_campaign("confirmar_agenda", {}), "confirmacion")

    def test_override_de_confirmar_agenda_cuando_la_cita_se_cancelo(self):
        self.assertEqual(
            resolve_agent_for_campaign("confirmar_agenda", {"cita_cancelada": True}),
            "reagendamiento",
        )

    def test_override_de_agendar_hora_cuando_ya_tiene_cita(self):
        self.assertEqual(
            resolve_agent_for_campaign("agendar_hora", {"ya_tiene_cita": True}),
            "confirmacion",
        )


class ProductionRoutingRulesTest(TestCase):
    def test_recordatorio_24h_rutea_a_confirmacion(self):
        self.assertEqual(resolve_agent_for_campaign("recordatorio_24h", {}), "confirmacion")

    def test_las_campanas_de_encuesta_de_renault_no_tienen_regla_en_cavem(self):
        # Una regla que apunta a un agente fuera del registro no es inocua:
        # specialist_node cae a "faq" en silencio, que es peor que no tenerla.
        self.assertIsNone(resolve_agent_for_campaign("encuesta_servicio_tecnico", {}))
        self.assertIsNone(resolve_agent_for_campaign("encuesta_venta_auto_nuevo", {}))

    def test_las_campanas_comerciales_de_cavem_si_rutean(self):
        self.assertEqual(resolve_agent_for_campaign("cyber_auto_demo", {}), "ventas")
        self.assertEqual(resolve_agent_for_campaign("renueva_tu_auto", {}), "ventas")
        self.assertEqual(
            resolve_agent_for_campaign("servicio_tecnico_mantencion", {}), "agendamiento")
