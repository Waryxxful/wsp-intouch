"""La preferencia horaria viaja, y el teléfono no sale sin consentimiento.

El texto legal no se inventa: TEXTO_CONSENTIMIENTO vacío es el default. Con
el sink apagado el doctor no puede ponerse rojo por eso.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from bot.business.lead_intouch import hash_de_negocio, payload_del_lead
from bot.models import Consentimiento, Conversation, Incident, LeadInTouch, Message


def _lead(wa_id="56900000111", **kwargs):
    conv = Conversation.objects.create(wa_id=wa_id)
    lead = LeadInTouch.objects.create(
        conversation=conv, empresa="Acme SpA", **kwargs)
    return conv, lead


class PayloadPreferenciaTest(TestCase):
    def test_la_preferencia_horaria_esta_en_el_payload(self):
        _, lead = _lead(preferencia_horaria="martes por la mañana")
        self.assertEqual(
            payload_del_lead(lead)["preferencia_horaria"], "martes por la mañana")


@override_settings(TEXTO_CONSENTIMIENTO="")
class TelefonoSinTextoTest(TestCase):
    def test_con_el_setting_vacio_el_telefono_viaja(self):
        conv, lead = _lead()
        payload = payload_del_lead(lead)
        self.assertEqual(payload["telefono"], conv.wa_id)
        self.assertNotIn("consentimiento", payload)


@override_settings(TEXTO_CONSENTIMIENTO="Aviso legal.")
class TelefonoConTextoTest(TestCase):
    def test_sin_consentimiento_el_telefono_va_vacio_y_queda_pendiente(self):
        _, lead = _lead()
        payload = payload_del_lead(lead)
        self.assertEqual(payload["telefono"], "")
        self.assertEqual(payload["consentimiento"], "pendiente")
        # El resto del lead se sigue despachando.
        self.assertEqual(payload["empresa"], "Acme SpA")

    def test_un_consentimiento_revocado_tampoco_deja_salir_el_telefono(self):
        conv, lead = _lead(wa_id="56900000113")
        Consentimiento.objects.create(wa_id=conv.wa_id, otorgado=False)
        payload = payload_del_lead(lead)
        self.assertEqual(payload["telefono"], "")
        self.assertEqual(payload["consentimiento"], "pendiente")

    def test_con_consentimiento_otorgado_el_telefono_viaja(self):
        conv, lead = _lead(wa_id="56900000112")
        Consentimiento.objects.create(wa_id=conv.wa_id, otorgado=True)
        payload = payload_del_lead(lead)
        self.assertEqual(payload["telefono"], conv.wa_id)
        self.assertEqual(payload["consentimiento"], "otorgado")

    def test_otorgar_el_consentimiento_cambia_el_contenido_que_se_despacha(self):
        # El teléfono entra al hash de negocio. Si no, el CRM se quedaría con
        # el número vacío para siempre: el evento no se reabriría al consentir.
        conv, lead = _lead(wa_id="56900000114")
        antes = hash_de_negocio(payload_del_lead(lead))
        Consentimiento.objects.create(wa_id=conv.wa_id, otorgado=True)
        self.assertNotEqual(antes, hash_de_negocio(payload_del_lead(lead)))
        self.assertEqual(payload_del_lead(lead)["telefono"], conv.wa_id)


class DoctorConsentimientoTest(SimpleTestCase):
    def _niveles(self, **ajustes):
        from bot.management.commands.doctor import chequear_texto_de_consentimiento

        with override_settings(**ajustes):
            return [h.nivel for h in chequear_texto_de_consentimiento({})]

    def test_falla_con_sink_http_y_texto_vacio(self):
        from bot.management.commands.doctor import FALLA

        self.assertEqual(
            self._niveles(LEAD_SINK="http", TEXTO_CONSENTIMIENTO=""), [FALLA])

    def test_ok_con_sink_none(self):
        from bot.management.commands.doctor import OK

        self.assertEqual(
            self._niveles(LEAD_SINK="none", TEXTO_CONSENTIMIENTO=""), [OK])

    def test_ok_con_sink_http_si_el_texto_esta_puesto(self):
        from bot.management.commands.doctor import OK

        self.assertEqual(
            self._niveles(LEAD_SINK="http", TEXTO_CONSENTIMIENTO="Aviso legal."),
            [OK])

    def test_el_chequeo_esta_enganchado(self):
        from bot.management.commands.doctor import (
            SECCIONES, chequear_texto_de_consentimiento,
        )

        enganchados = [c for funcs in SECCIONES.values() for c in funcs]
        self.assertIn(chequear_texto_de_consentimiento, enganchados)


class AvisoDeConsentimientoEnLaColaTest(TransactionTestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56900000120")

    def _turno(self):
        from bot.whatsapp.cola_envio import _enviar

        wa = MagicMock()
        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value={"stage": "nuevo"}):
            _enviar(
                self.conv.pk, self.conv.wa_id, [], None, [], wa,
                metadatos={
                    "prosa": "Hola.", "mensaje_cliente": "hola",
                    "nombre_agente": "comercial",
                },
            )
        return wa

    @override_settings(TEXTO_CONSENTIMIENTO="Aviso legal.")
    def test_el_aviso_sale_una_vez_y_no_se_repite(self):
        primero = self._turno()
        primero.send_text.assert_called_once_with(self.conv.wa_id, "Aviso legal.")
        self.assertEqual(
            Message.objects.filter(
                conversation=self.conv, content="Aviso legal.").count(),
            1)
        segundo = self._turno()
        segundo.send_text.assert_not_called()
        self.assertEqual(
            Message.objects.filter(
                conversation=self.conv, content="Aviso legal.").count(),
            1)

    @override_settings(TEXTO_CONSENTIMIENTO="")
    def test_sin_texto_no_manda_nada(self):
        self._turno().send_text.assert_not_called()

    @override_settings(TEXTO_CONSENTIMIENTO="Aviso legal.")
    def test_con_consentimiento_otorgado_no_manda_el_aviso(self):
        Consentimiento.objects.create(wa_id=self.conv.wa_id, otorgado=True)
        self._turno().send_text.assert_not_called()


@override_settings(TEXTO_CONSENTIMIENTO="")
class CampanitaDeCasoTest(TestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56900000130")

    def _persistir(self, meta):
        from bot.whatsapp.cola_envio import _persistir_metadatos

        _persistir_metadatos(self.conv, meta, wa=MagicMock())

    def test_un_handoff_nuevo_avisa_una_sola_vez(self):
        with patch("bot.notify.notificar") as notificar:
            self._persistir({
                "handoff": True, "handoff_reason": "Pidió un humano.",
            })
            self._persistir({
                "handoff": True, "handoff_reason": "Insiste.",
            })
        notificar.assert_called_once()
        self.assertEqual(notificar.call_args.kwargs["tipo"], "caso_equipo")
        self.assertEqual(
            notificar.call_args.kwargs["url"], "/wsp/intouch/conversaciones")
        self.assertEqual(
            notificar.call_args.kwargs["mensaje"],
            "Hay una derivación para que el equipo la tome.")
        self.assertEqual(Incident.objects.filter(kind="handoff").count(), 1)

    def test_una_revision_nueva_avisa_con_el_mismo_tipo(self):
        with patch("bot.notify.notificar") as notificar:
            self._persistir({
                "requiere_revision": True, "motivo_revision": "Reclamo grave.",
            })
        notificar.assert_called_once()
        self.assertEqual(notificar.call_args.kwargs["tipo"], "caso_equipo")
        self.assertEqual(
            notificar.call_args.kwargs["mensaje"],
            "Hay un caso para que el equipo lo tome.")


class TipoCasoEquipoTest(SimpleTestCase):
    def test_se_declara_caso_equipo_sin_sacar_lead_hot(self):
        from utils.dios_registration import NOTIFY_TYPES

        por_codigo = {t["codigo"]: t for t in NOTIFY_TYPES}
        self.assertEqual(por_codigo["lead_hot"]["rol_minimo"], "agente")
        caso = por_codigo["caso_equipo"]
        self.assertEqual(caso["rol_minimo"], "agente")
        self.assertEqual(
            caso["descripcion"],
            "Hay un caso o una derivación para que el equipo lo tome")
