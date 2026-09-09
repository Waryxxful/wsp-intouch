"""La notificación del lead HOT al orquestador.

Verificado el 2026-09-09: el subsistema del orquestador es real y está en
producción, y wsp_pompeyo lo usa end-to-end. `lead_nuevo` NO existe -- estaba
sólo en un test del orquestador -- así que se declara `lead_hot`.

Dos cosas que se prueban y que son la diferencia entre notificar y molestar:
  1. Se notifica en la TRANSICIÓN a HOT, no en cada turno en que el lead está
     HOT. Sin el sello, el equipo recibe una notificación por mensaje.
  2. La notificación es best-effort: nunca puede tumbar el turno.
"""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from bot.business.lead_intouch import _registrar_lead_impl
from bot.models import Conversation, LeadInTouch

SENALES_HOT = {"encaje_con_oferta": True, "necesidad_concreta": True,
               "solicita_siguiente_paso": True, "intencion_avanzar_declarada": True}
SENALES_COLD = {"encaje_con_oferta": True, "interes_exploratorio": True}


class TiposDeclaradosTest(SimpleTestCase):
    def test_se_declara_lead_hot_y_no_lead_nuevo(self):
        from utils.dios_registration import NOTIFY_TYPES

        codigos = {t["codigo"] for t in NOTIFY_TYPES}
        self.assertIn("lead_hot", codigos)
        # `lead_nuevo` aparecía sólo en un test del orquestador: nunca se
        # reservó, y `lead_hot` describe el evento.
        self.assertNotIn("lead_nuevo", codigos)

    def test_cada_tipo_declara_rol_minimo_y_descripcion(self):
        from utils.dios_registration import NOTIFY_TYPES

        for tipo in NOTIFY_TYPES:
            self.assertIn("rol_minimo", tipo)
            self.assertTrue(tipo["descripcion"])

    def test_el_payload_lleva_el_campo_tipo(self):
        # docs-repo/notificaciones.md está desactualizado y no lo menciona;
        # sin `tipo` el orquestador responde 400. Este test es la defensa.
        import inspect

        from bot.notify import notificar

        self.assertIn('"tipo"', inspect.getsource(notificar))


@override_settings(GRANCRM_TENANT_SLUG="qaintouch")
class TransicionAHotTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56977777777")

    def test_notifica_cuando_el_lead_pasa_a_hot(self):
        with patch("bot.notify.notificar") as notificar:
            _registrar_lead_impl(
                "56977777777",
                {"empresa": "Acme SpA", "necesidad_principal": "Ordenar la atención."},
                senales=SENALES_HOT)
        notificar.assert_called_once()
        self.assertEqual(notificar.call_args.kwargs["tipo"], "lead_hot")
        self.assertIsNotNone(LeadInTouch.objects.get().notificado_en)

    def test_no_notifica_dos_veces_el_mismo_lead(self):
        # Sin el sello, el equipo comercial recibe una notificación por cada
        # mensaje que el contacto siga escribiendo.
        with patch("bot.notify.notificar") as notificar:
            _registrar_lead_impl("56977777777", {"empresa": "Acme SpA"},
                                 senales=SENALES_HOT)
            _registrar_lead_impl("56977777777", {"cargo": "Gerente"},
                                 senales=SENALES_HOT)
        self.assertEqual(notificar.call_count, 1)

    def test_no_notifica_un_lead_que_no_es_hot(self):
        with patch("bot.notify.notificar") as notificar:
            _registrar_lead_impl("56977777777", {"empresa": "Acme SpA"},
                                 senales=SENALES_COLD)
        notificar.assert_not_called()
        self.assertIsNone(LeadInTouch.objects.get().notificado_en)

    def test_notifica_al_subir_de_cold_a_hot(self):
        with patch("bot.notify.notificar") as notificar:
            _registrar_lead_impl("56977777777", {"empresa": "Acme SpA"},
                                 senales=SENALES_COLD)
            _registrar_lead_impl("56977777777", {"plazo_proyecto": "este mes"},
                                 senales=SENALES_HOT)
        self.assertEqual(notificar.call_count, 1)

    def test_un_fallo_notificando_no_tumba_la_escritura_del_lead(self):
        with patch("bot.notify.notificar", side_effect=RuntimeError("DIOS caído")):
            resultado = _registrar_lead_impl("56977777777", {"empresa": "Acme SpA"},
                                             senales=SENALES_HOT)
        self.assertTrue(resultado["ok"])
        self.assertEqual(LeadInTouch.objects.get().empresa, "Acme SpA")


class SinTenantTest(TestCase):
    @override_settings(GRANCRM_TENANT_SLUG="")
    def test_sin_tenant_no_revienta_y_deja_ruido(self):
        Conversation.objects.create(wa_id="56988888888")
        with self.assertLogs("bot.notify", level="WARNING"):
            from bot.notify import notificar

            notificar(tipo="lead_hot", mensaje="Lead HOT de prueba.", url="/wsp/intouch/leads")
