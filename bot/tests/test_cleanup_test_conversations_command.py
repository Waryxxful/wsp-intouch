from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from bot.models import Conversation, Message


class CleanupTestConversationsCommandTest(TestCase):
    databases = {"default", "qaintouch"}

    def test_borra_conversaciones_y_mensajes_con_prefijo_test(self):
        conv_test = Conversation.objects.create(wa_id="TEST000001")
        Message.objects.create(conversation=conv_test, role="user", content="hola")
        conv_real = Conversation.objects.create(wa_id="56911112222")

        call_command("cleanup_test_conversations", stdout=StringIO())

        self.assertFalse(Conversation.objects.filter(wa_id="TEST000001").exists())
        self.assertFalse(Message.objects.filter(conversation_id=conv_test.id).exists())
        self.assertTrue(Conversation.objects.filter(id=conv_real.id).exists())

    def test_borra_solo_leads_con_prefijo_test(self):
        from leads.models import Lead

        Lead.objects.create(rut="11.111.111-1", nombre="A", telefono="1", razon_interes="[TEST] algo")
        real = Lead.objects.create(rut="22.222.222-2", nombre="B", telefono="2", razon_interes="Lead real")

        call_command("cleanup_test_conversations", stdout=StringIO())

        self.assertEqual(list(Lead.objects.values_list("id", flat=True)), [real.id])

    def test_older_than_days_respeta_el_filtro(self):
        conv_vieja = Conversation.objects.create(wa_id="TEST000001")
        Conversation.objects.filter(id=conv_vieja.id).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        conv_nueva = Conversation.objects.create(wa_id="TEST000002")

        call_command("cleanup_test_conversations", "--older-than-days", "5", stdout=StringIO())

        self.assertFalse(Conversation.objects.filter(id=conv_vieja.id).exists())
        self.assertTrue(Conversation.objects.filter(id=conv_nueva.id).exists())

    def test_imprime_el_resumen(self):
        Conversation.objects.create(wa_id="TEST000001")
        out = StringIO()
        call_command("cleanup_test_conversations", stdout=out)
        self.assertIn("1 conversaciones", out.getvalue())
