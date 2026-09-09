# bot/tests/test_context_window.py
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from bot.models import Conversation, Message, CampaignSend
from bot.flow.context_window import (
    build_context_window, limite_sesion_actual, marcador_ficha_enviada, marcador_imagen_enviada,
    resolve_campaign_hint,
)


class ResolveCampaignHintTest(TestCase):
    def test_sin_campaign_send_devuelve_none(self):
        self.assertIsNone(resolve_campaign_hint("56911112222"))

    def test_campaign_send_reciente_sin_responder_devuelve_su_tipo(self):
        CampaignSend.objects.create(contacto="56911112222", campaign_type="confirmar_agenda", template="tpl1")
        self.assertEqual(resolve_campaign_hint("56911112222"), "confirmar_agenda")

    def test_campaign_send_ya_respondido_no_cuenta(self):
        CampaignSend.objects.create(contacto="56911112222", campaign_type="confirmar_agenda", template="tpl1", respondido=True)
        self.assertIsNone(resolve_campaign_hint("56911112222"))

    def test_campaign_send_fuera_de_ventana_24h_no_cuenta(self):
        send = CampaignSend.objects.create(contacto="56911112222", campaign_type="confirmar_agenda", template="tpl1")
        CampaignSend.objects.filter(pk=send.pk).update(enviado_at=timezone.now() - timedelta(hours=30))
        self.assertIsNone(resolve_campaign_hint("56911112222"))


class BuildContextWindowTest(TestCase):
    def _msg(self, conv, content, hours_ago=0):
        msg = Message.objects.create(conversation=conv, role="user", content=content)
        Message.objects.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(hours=hours_ago))
        return msg

    def test_ventana_vacia_si_no_hay_mensajes(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self.assertEqual(build_context_window(conv), [])

    def test_ventana_incluye_mensajes_recientes_en_orden_cronologico(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._msg(conv, "primero", hours_ago=2)
        self._msg(conv, "segundo", hours_ago=1)
        self._msg(conv, "tercero", hours_ago=0)
        window = build_context_window(conv)
        self.assertEqual([m["content"] for m in window], ["primero", "segundo", "tercero"])

    def test_ventana_corta_en_gap_de_inactividad_mayor_a_24h(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._msg(conv, "conversacion vieja", hours_ago=48)
        self._msg(conv, "conversacion nueva", hours_ago=1)
        window = build_context_window(conv)
        self.assertEqual([m["content"] for m in window], ["conversacion nueva"])

    def test_ventana_corta_en_el_ultimo_campaign_send(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._msg(conv, "tema anterior no relacionado", hours_ago=5)
        send = CampaignSend.objects.create(contacto="56911112222", campaign_type="agendar_hora", template="tpl2")
        CampaignSend.objects.filter(pk=send.pk).update(enviado_at=timezone.now() - timedelta(hours=3))
        self._msg(conv, "respuesta a la campana", hours_ago=1)
        window = build_context_window(conv)
        self.assertEqual([m["content"] for m in window], ["respuesta a la campana"])

    def test_ventana_topea_en_los_ultimos_n_turnos(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        for i in range(25):
            self._msg(conv, f"msg{i}", hours_ago=25 - i)
        window = build_context_window(conv)
        # +1 por el marcador de historial truncado (ver
        # test_ventana_topada_agrega_marcador_de_historial_truncado abajo).
        self.assertEqual(len(window), 21)
        self.assertEqual(window[-1]["content"], "msg24")

    def test_marcador_de_imagen_enviada_se_traduce_a_prosa_en_el_historial(self):
        # El marcador crudo sigue siendo contabilidad interna para
        # deduplicar el envio de fotos (ver
        # bot/whatsapp/handlers.py::_imagen_enviada_recientemente), pero ya
        # no se excluye del historial que ve el LLM -- sin esto, el modelo
        # no tenia con que contrastar si afirmaba (o negaba) haber mandado
        # la foto (docs/PENDIENTES.md).
        conv = Conversation.objects.create(wa_id="56911112222")
        self._msg(conv, "quiero ver el koleos", hours_ago=2)
        marcador = Message.objects.create(conversation=conv, role="assistant", content=marcador_imagen_enviada("koleos"))
        Message.objects.filter(pk=marcador.pk).update(created_at=timezone.now() - timedelta(hours=1.5))
        self._msg(conv, "gracias", hours_ago=1)
        window = build_context_window(conv)
        self.assertEqual(
            [m["content"] for m in window],
            ["quiero ver el koleos", "[ya te compartí una foto de koleos en un mensaje anterior]", "gracias"],
        )
        self.assertEqual(window[1]["role"], "assistant")

    def test_marcador_de_ficha_enviada_se_traduce_a_prosa_en_el_historial(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._msg(conv, "mandame la ficha del koleos", hours_ago=2)
        marcador = Message.objects.create(conversation=conv, role="assistant", content=marcador_ficha_enviada("Koleos"))
        Message.objects.filter(pk=marcador.pk).update(created_at=timezone.now() - timedelta(hours=1.5))
        self._msg(conv, "gracias", hours_ago=1)
        window = build_context_window(conv)
        self.assertEqual(
            [m["content"] for m in window],
            ["mandame la ficha del koleos", "[ya te compartí la ficha técnica de Koleos en un mensaje anterior]", "gracias"],
        )

    def test_ventana_topada_agrega_marcador_de_historial_truncado(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        for i in range(25):
            self._msg(conv, f"msg{i}", hours_ago=25 - i)
        window = build_context_window(conv)
        self.assertEqual(len(window), 21)
        self.assertEqual(window[0]["role"], "system")
        self.assertIn("historial", window[0]["content"])
        self.assertEqual([m["content"] for m in window[1:]][0], "msg5")
        self.assertEqual(window[-1]["content"], "msg24")

    def test_ventana_sin_topar_no_agrega_marcador_de_historial_truncado(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        for i in range(5):
            self._msg(conv, f"msg{i}", hours_ago=5 - i)
        window = build_context_window(conv)
        self.assertEqual([m["role"] for m in window], ["user"] * 5)

    def test_corte_de_sesion_justo_en_el_limite_no_agrega_marcador_de_truncado(self):
        # Si la sesion tiene EXACTAMENTE 20 mensajes y el corte real es por
        # gap de inactividad (no por el tope), no hay historial oculto que
        # senalar.
        conv = Conversation.objects.create(wa_id="56911112222")
        self._msg(conv, "sesion vieja", hours_ago=48)
        for i in range(20):
            self._msg(conv, f"msg{i}", hours_ago=20 - i)
        window = build_context_window(conv)
        self.assertEqual(len(window), 20)
        self.assertEqual(window[0]["content"], "msg0")


class LimiteSesionActualTest(TestCase):
    def _msg(self, conv, content, hours_ago=0):
        msg = Message.objects.create(conversation=conv, role="user", content=content)
        Message.objects.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(hours=hours_ago))
        return msg

    def test_none_si_no_hay_mensajes(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self.assertIsNone(limite_sesion_actual(conv))

    def test_devuelve_el_mensaje_mas_viejo_de_la_sesion_activa(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._msg(conv, "conversacion vieja", hours_ago=48)
        primero = self._msg(conv, "arranca la sesion nueva", hours_ago=2)
        self._msg(conv, "sigue la sesion nueva", hours_ago=1)
        limite = limite_sesion_actual(conv)
        self.assertEqual(limite, Message.objects.get(pk=primero.pk).created_at)

    def test_no_se_topea_en_20_mensajes_a_diferencia_de_build_context_window(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        primero = self._msg(conv, "arranca la sesion", hours_ago=1)
        for i in range(25):
            self._msg(conv, f"msg{i}", hours_ago=1)
        limite = limite_sesion_actual(conv)
        self.assertEqual(limite, Message.objects.get(pk=primero.pk).created_at)
