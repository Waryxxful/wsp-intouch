from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bot import seguimiento
from bot.models import CampaignSend, Conversation, LeadComercial, Message, Setting, registrar_optout


class VentanaConfigurableTest(TestCase):
    def test_sin_setting_usa_24_horas(self):
        self.assertEqual(seguimiento.ventana_minutos(), 1440)

    def test_el_panel_puede_bajarla_a_40_minutos_para_la_demo(self):
        Setting.objects.create(key="followup_ventana_minutos", value="40")
        self.assertEqual(seguimiento.ventana_minutos(), 40)

    def test_un_valor_invalido_cae_al_default_en_vez_de_reventar(self):
        Setting.objects.create(key="followup_ventana_minutos", value="cuarenta")
        self.assertEqual(seguimiento.ventana_minutos(), 1440)

    def test_un_valor_no_positivo_cae_al_default(self):
        # Un 0 mandaria seguimiento a todo el mundo en el proximo tick.
        Setting.objects.create(key="followup_ventana_minutos", value="0")
        self.assertEqual(seguimiento.ventana_minutos(), 1440)


class CandidatasTest(TestCase):
    def setUp(self):
        Setting.objects.create(key="followup_ventana_minutos", value="40")
        self.ahora = timezone.now()

    def _conversacion(self, wa_id="56911111111", vehiculo="Hyundai Tucson 2023",
                      antiguedad_min=90, rol_ultimo="user", **kwargs):
        conv = Conversation.objects.create(wa_id=wa_id, name="Felipe", **kwargs)
        msg = Message.objects.create(conversation=conv, role=rol_ultimo, content="hola")
        viejo = self.ahora - timedelta(minutes=antiguedad_min)
        Message.objects.filter(pk=msg.pk).update(created_at=viejo)
        Conversation.objects.filter(pk=conv.pk).update(updated_at=viejo)
        if vehiculo:
            LeadComercial.objects.create(conversation=conv, vehiculo_interes=vehiculo)
        conv.refresh_from_db()
        return conv

    def _codigos(self):
        return {c.wa_id for c, _, _ in seguimiento._candidatas(self.ahora)}

    def test_conversacion_vieja_con_vehiculo_de_interes_es_candidata(self):
        self._conversacion()
        self.assertEqual(self._codigos(), {"56911111111"})

    def test_conversacion_reciente_no_es_candidata(self):
        self._conversacion(antiguedad_min=5)
        self.assertEqual(self._codigos(), set())

    def test_sin_vehiculo_de_interes_no_se_hace_seguimiento(self):
        # Sin algo concreto de que hablar, el mensaje es spam.
        self._conversacion(vehiculo="")
        self.assertEqual(self._codigos(), set())

    def test_si_el_ultimo_mensaje_es_del_bot_no_se_insiste(self):
        self._conversacion(rol_ultimo="assistant")
        self.assertEqual(self._codigos(), set())

    def test_no_interrumpe_una_conversacion_tomada_por_un_humano(self):
        conv = self._conversacion()
        conv.flow_data = {"modo": "HUMAN"}
        conv.save()
        self.assertEqual(self._codigos(), set())

    def test_respeta_el_opt_out(self):
        conv = self._conversacion()
        registrar_optout(conv.wa_id, "no quiere mensajes")
        self.assertEqual(self._codigos(), set())

    def test_no_se_manda_dos_veces(self):
        conv = self._conversacion()
        CampaignSend.objects.create(
            contacto=conv.wa_id, campaign_type="seguimiento_vehiculo", template="x")
        self.assertEqual(self._codigos(), set())

    def test_una_conversacion_ya_cerrada_no_recibe_seguimiento(self):
        self._conversacion(stage="cerrado")
        self.assertEqual(self._codigos(), set())

    def test_una_conversacion_archivada_no_recibe_seguimiento(self):
        self._conversacion(archived=True)
        self.assertEqual(self._codigos(), set())


class EnvioTest(TestCase):
    def setUp(self):
        Setting.objects.create(key="followup_ventana_minutos", value="40")
        self.ahora = timezone.now()
        self.conv = Conversation.objects.create(wa_id="56911111111", name="Felipe Rojas")
        self.lead = LeadComercial.objects.create(
            conversation=self.conv, nombre="Felipe Rojas", vehiculo_interes="Toyota RAV4 2023")

    def _ultimo(self, horas_atras):
        msg = Message.objects.create(conversation=self.conv, role="user", content="hola")
        Message.objects.filter(pk=msg.pk).update(
            created_at=self.ahora - timedelta(hours=horas_atras))
        return Message.objects.get(pk=msg.pk)

    def test_dentro_de_las_24h_manda_texto_libre_sin_plantilla(self):
        # A 40 minutos la ventana de servicio de WhatsApp sigue abierta: la
        # demo no depende de que Meta apruebe la plantilla.
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_text.return_value = True
            self.assertTrue(seguimiento.enviar_seguimiento(
                self.conv, self.lead, self._ultimo(1), self.ahora))
            get_wa.return_value.send_text.assert_called_once()
            get_wa.return_value.send_template.assert_not_called()
        texto = get_wa.return_value.send_text.call_args[0][1]
        self.assertIn("Felipe", texto)
        self.assertIn("Toyota RAV4 2023", texto)

    def test_pasadas_las_24h_usa_la_plantilla_configurada(self):
        Setting.objects.create(key="seguimiento_vehiculo_template", value="seguimiento_vehiculo")
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_template.return_value = True
            self.assertTrue(seguimiento.enviar_seguimiento(
                self.conv, self.lead, self._ultimo(30), self.ahora))
            get_wa.return_value.send_text.assert_not_called()
            nombre_plantilla = get_wa.return_value.send_template.call_args[0][1]
        self.assertEqual(nombre_plantilla, "seguimiento_vehiculo")

    def test_pasadas_las_24h_sin_plantilla_no_manda_nada(self):
        # Mandar texto libre aca solo produce un rechazo de Meta y un registro
        # de "enviado" que el cliente nunca recibio.
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            with self.assertRaises(seguimiento.SinPlantilla):
                seguimiento.enviar_seguimiento(
                    self.conv, self.lead, self._ultimo(30), self.ahora)
            get_wa.return_value.send_text.assert_not_called()
            get_wa.return_value.send_template.assert_not_called()
        self.assertFalse(CampaignSend.objects.exists())

    def test_un_segundo_worker_no_manda_el_mismo_seguimiento(self):
        # Gunicorn corre varios workers y cada uno tiene su propio thread de
        # seguimiento: sin el claim atomico, ambos pasaban el chequeo de "ya se
        # le mando?" y el cliente recibia el mensaje dos veces.
        ultimo = self._ultimo(1)
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_text.return_value = True
            primero = seguimiento.enviar_seguimiento(self.conv, self.lead, ultimo, self.ahora)
            segundo = seguimiento.enviar_seguimiento(self.conv, self.lead, ultimo, self.ahora)
            self.assertEqual(get_wa.return_value.send_text.call_count, 1)
        self.assertTrue(primero)
        self.assertFalse(segundo)
        self.assertEqual(CampaignSend.objects.count(), 1)

    def test_si_el_envio_revienta_el_claim_se_libera_para_reintentar(self):
        # Si el claim quedara puesto, la conversacion quedaria excluida para
        # siempre de un seguimiento que nunca recibio.
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_text.side_effect = RuntimeError("timeout de red")
            with self.assertRaises(RuntimeError):
                seguimiento.enviar_seguimiento(
                    self.conv, self.lead, self._ultimo(1), self.ahora)
        self.assertFalse(CampaignSend.objects.exists())

    def test_tick_avisa_una_sola_vez_por_corrida_y_no_por_contacto(self):
        Conversation.objects.all().delete()
        for i in range(3):
            conv = Conversation.objects.create(wa_id=f"5691111111{i}", name="X")
            msg = Message.objects.create(conversation=conv, role="user", content="hola")
            viejo = self.ahora - timedelta(days=3)
            Message.objects.filter(pk=msg.pk).update(created_at=viejo)
            Conversation.objects.filter(pk=conv.pk).update(updated_at=viejo)
            LeadComercial.objects.create(conversation=conv, vehiculo_interes="Tucson")
        with self.assertLogs("bot.seguimiento", level="WARNING") as capturado:
            self.assertEqual(seguimiento.tick(self.ahora), 0)
        self.assertEqual(len(capturado.records), 1)
        self.assertIn("3 contactos", capturado.output[0])

    def test_un_envio_exitoso_registra_el_campaign_send_y_marca_la_etapa(self):
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_text.return_value = True
            seguimiento.enviar_seguimiento(self.conv, self.lead, self._ultimo(1), self.ahora)
        envio = CampaignSend.objects.get()
        self.assertEqual(envio.campaign_type, "seguimiento_vehiculo")
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.stage, "seguimiento")

    def test_si_el_envio_falla_no_se_registra_como_enviado(self):
        # Sin esto la conversacion quedaria bloqueada para siempre: el filtro
        # de "ya se le mando" la excluiria aunque nunca haya recibido nada.
        with patch("bot.whatsapp.client.get_wa_client") as get_wa:
            get_wa.return_value.send_text.return_value = False
            seguimiento.enviar_seguimiento(self.conv, self.lead, self._ultimo(1), self.ahora)
        self.assertFalse(CampaignSend.objects.exists())


class PreRoutingTest(TestCase):
    def setUp(self):
        # Varios tests heredados vacian PRE_ROUTING_RULES para aislarse y no la
        # restauran, asi que estos pasaban solos y fallaban dentro de la suite.
        from bot.flow.campaign_rules import restaurar_reglas_por_defecto
        restaurar_reglas_por_defecto()

    def test_responder_una_campana_comercial_entra_directo_a_ventas(self):
        from bot.flow.campaign_rules import resolve_agent_for_campaign
        for campana in ("cyber_auto_demo", "renueva_tu_auto", "seguimiento_vehiculo"):
            self.assertEqual(resolve_agent_for_campaign(campana, {}), "ventas")

    def test_la_campana_de_servicio_entra_a_agendamiento(self):
        from bot.flow.campaign_rules import resolve_agent_for_campaign
        self.assertEqual(
            resolve_agent_for_campaign("servicio_tecnico_mantencion", {}), "agendamiento")
