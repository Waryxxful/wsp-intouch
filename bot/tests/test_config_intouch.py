"""La configuración que, si está mal, no da error visible.

Los dos primeros checks ya existen como system checks (bot.E001/E002) y esto
sólo los ancla desde la suite. Los de LEAD_SINK son nuevos: un valor
desconocido ahí significa leads que no se despachan a ningún lado, en silencio.
"""
from django.conf import settings
from django.test import SimpleTestCase, override_settings


class LeadSinkTest(SimpleTestCase):
    def test_el_default_es_none(self):
        # El endpoint del orquestador es el spec B y todavía no existe: el bot
        # arranca sin despachar a ningún lado, y eso es lo correcto.
        self.assertEqual(settings.LEAD_SINK, "none")

    def test_solo_hay_dos_valores_posibles(self):
        from bot.business.lead_intouch import SINKS_VALIDOS

        self.assertEqual(SINKS_VALIDOS, frozenset({"none", "http"}))


class ClienteYRagTest(SimpleTestCase):
    def test_el_cliente_activo_es_un_choice_valido(self):
        from bot.models import CLIENTE_CHOICES

        self.assertIn(settings.CLIENTE_ACTIVO, dict(CLIENTE_CHOICES))
