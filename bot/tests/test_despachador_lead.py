"""El adaptador de salida hacia el endpoint de leads del orquestador.

Arranca apagado (LEAD_SINK=none) porque ese endpoint es el spec B y todavía no
existe. Lo que se prueba acá es que cuando exista, conmutarlo sea sólo
configuración -- y que la clave de idempotencia esté lista, que es el punto que
el prompt de origen pedía y que la instrucción al modelo no puede cumplir
frente a reentregas de WhatsApp.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from bot.business.lead_intouch import (
    _registrar_lead_impl, clave_idempotencia, payload_del_lead,
)
from bot.models import Conversation, LeadInTouch


class ApagadoTest(TestCase):
    @override_settings(LEAD_SINK="none")
    def test_con_none_no_sale_ninguna_peticion(self):
        Conversation.objects.create(wa_id="56900000010")
        with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
            _registrar_lead_impl("56900000010", {"empresa": "Acme SpA"})
        enviar.assert_not_called()
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://orquestador:9000/api/leads")
class EncendidoTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56900000011")

    def test_despacha_y_sella(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "created", "contactId": "c1", "dealId": None}):
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertIsNotNone(LeadInTouch.objects.get().despachado_en)

    def test_un_fallo_deja_el_lead_sin_sellar_para_reintentarlo(self):
        # Un lead sin despachar tiene que ser VISIBLE: el sello es la única
        # forma de saber cuáles quedaron afuera.
        with patch("bot.business.lead_intouch._enviar_al_sink", return_value=None):
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)

    def test_un_fallo_no_tumba_la_escritura(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   side_effect=RuntimeError("endpoint caído")):
            resultado = _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertTrue(resultado["ok"])
        self.assertEqual(LeadInTouch.objects.get().empresa, "Acme SpA")

    def test_no_despacha_dos_veces_el_mismo_contenido(self):
        # Antes este test afirmaba que dos escrituras dan UN despacho. Con
        # evento_id eso cambio a proposito: la segunda escritura de abajo NO
        # cambia el contenido, y por eso no hay segundo despacho. Un cambio
        # real si tiene que despacharse -- es una actualizacion comercial, no
        # un reintento.
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "created", "contactId": "c1",
                                 "dealId": None}) as enviar:
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertEqual(enviar.call_count, 1)

    def test_un_cambio_real_si_se_despacha_otra_vez(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "updated", "contactId": "c1",
                                 "dealId": None}) as enviar:
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
            _registrar_lead_impl("56900000011", {"cargo": "Gerenta"})
        self.assertEqual(enviar.call_count, 2)

    def test_un_sink_desconocido_no_despacha_y_deja_ruido(self):
        with override_settings(LEAD_SINK="ftp"):
            with self.assertLogs("bot.business.lead_intouch", level="WARNING"):
                _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)


class IdempotenciaTest(TestCase):
    def test_la_clave_es_estable_entre_llamadas(self):
        conv = Conversation.objects.create(wa_id="56900000012")
        lead = LeadInTouch.objects.create(conversation=conv, empresa="Acme SpA")
        self.assertEqual(clave_idempotencia(lead), clave_idempotencia(lead))

    def test_la_clave_es_distinta_por_conversacion(self):
        primera = LeadInTouch.objects.create(
            conversation=Conversation.objects.create(wa_id="56900000013"))
        segunda = LeadInTouch.objects.create(
            conversation=Conversation.objects.create(wa_id="56900000014"))
        self.assertNotEqual(clave_idempotencia(primera), clave_idempotencia(segunda))

    def test_la_clave_no_expone_el_telefono(self):
        # Viaja a otro sistema: no tiene por qué llevar un dato personal en
        # claro cuando un hash cumple la misma función.
        conv = Conversation.objects.create(wa_id="56900000015")
        lead = LeadInTouch.objects.create(conversation=conv)
        self.assertNotIn("56900000015", clave_idempotencia(lead))


class PayloadTest(TestCase):
    def test_lleva_los_campos_del_contrato_y_la_clave(self):
        conv = Conversation.objects.create(wa_id="56900000016")
        lead = LeadInTouch.objects.create(
            conversation=conv, empresa="Acme SpA", correo="ana@acme.cl",
            lead_score="WARM", canales_actuales=["whatsapp"])
        payload = payload_del_lead(lead)
        self.assertEqual(payload["empresa"], "Acme SpA")
        self.assertEqual(payload["lead_score"], "WARM")
        self.assertEqual(payload["canales_actuales"], ["whatsapp"])
        self.assertIn("clave_contacto", payload)
        self.assertEqual(payload["origen"], "wsp_intouch")

    def test_lleva_el_telefono_del_wa_id(self):
        # El contrato del prompt §8 no lo incluye porque el LLM no debe
        # pedirlo, pero el equipo comercial necesita a quién llamar: lo agrega
        # la plataforma desde metadatos confiables, no el modelo.
        conv = Conversation.objects.create(wa_id="56900000017")
        lead = LeadInTouch.objects.create(conversation=conv)
        self.assertEqual(payload_del_lead(lead)["telefono"], "56900000017")


class EventoTest(TestCase):
    """El evento_id y la revision, que son lo que hace idempotente al despacho.

    La clave de contacto sola no alcanza: identifica a la persona, no al
    intento. Con una sola clave, "misma clave con otro contenido" seria el caso
    NORMAL de una conversacion que avanzo, y el receptor no podria distinguir un
    reintento de una calificacion nueva.
    """

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56900000020")
        self.lead = LeadInTouch.objects.create(
            conversation=self.conv, empresa="Acme SpA")

    def test_el_primer_marcado_abre_evento_y_revision_uno(self):
        from bot.business.lead_intouch import marcar_evento

        self.assertTrue(marcar_evento(self.lead))
        self.lead.refresh_from_db()
        self.assertNotEqual(self.lead.evento_id, "")
        self.assertEqual(self.lead.revision, 1)

    def test_sin_cambios_no_abre_evento_nuevo(self):
        # Es lo que permite que un reintento lleve la MISMA clave: si el
        # contenido no cambio, no es un intento nuevo.
        from bot.business.lead_intouch import marcar_evento

        marcar_evento(self.lead)
        primero = self.lead.evento_id
        self.assertFalse(marcar_evento(self.lead))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.evento_id, primero)
        self.assertEqual(self.lead.revision, 1)

    def test_un_cambio_de_contenido_abre_evento_nuevo_y_sube_la_revision(self):
        from bot.business.lead_intouch import marcar_evento

        marcar_evento(self.lead)
        primero = self.lead.evento_id
        self.lead.cargo = "Gerenta de Operaciones"
        self.lead.save()
        self.assertTrue(marcar_evento(self.lead))
        self.lead.refresh_from_db()
        self.assertNotEqual(self.lead.evento_id, primero)
        self.assertEqual(self.lead.revision, 2)

    def test_la_revision_solo_avanza(self):
        from bot.business.lead_intouch import marcar_evento

        for indice, cargo in enumerate(["A", "B", "C"], start=1):
            self.lead.cargo = cargo
            self.lead.save()
            marcar_evento(self.lead)
            self.lead.refresh_from_db()
            self.assertEqual(self.lead.revision, indice)

    def test_la_clave_de_contacto_no_cambia_entre_revisiones(self):
        from bot.business.lead_intouch import clave_contacto, marcar_evento

        antes = clave_contacto(self.lead)
        self.lead.cargo = "Otra cosa"
        self.lead.save()
        marcar_evento(self.lead)
        self.assertEqual(clave_contacto(self.lead), antes)

    def test_el_hash_ignora_los_campos_de_transporte(self):
        # Si evento_id o revision entraran al hash, cada reintento pareceria
        # contenido nuevo y el despacho no seria idempotente nunca.
        from bot.business.lead_intouch import hash_de_negocio

        base = {"empresa": "Acme SpA", "evento_id": "uno", "revision": 1}
        otro = {"empresa": "Acme SpA", "evento_id": "dos", "revision": 9}
        self.assertEqual(hash_de_negocio(base), hash_de_negocio(otro))

    def test_el_payload_es_un_SNAPSHOT_completo_y_no_un_patch(self):
        """De esto depende todo el algoritmo de orden del receptor.

        El receptor DESCARTA una revision anterior a la ultima aplicada. Eso
        solo es correcto si cada envio trae el estado completo: con parches,
        descartar una revision vieja perderia los campos que solo venian ahi.

        `payload_del_lead` serializa _CAMPOS_DEL_PAYLOAD entero en cada envio,
        asi que es un snapshot. Este test lo ANCLA: si alguien lo convierte en
        un diff para ahorrar bytes, rompe la idempotencia del receptor y tiene
        que verlo aca.
        """
        from bot.business.lead_intouch import _CAMPOS_DEL_PAYLOAD, marcar_evento

        marcar_evento(self.lead)
        # Un segundo envio que solo cambia un campo sigue trayendo TODOS.
        self.lead.cargo = "Gerenta"
        self.lead.save()
        marcar_evento(self.lead)
        payload = payload_del_lead(self.lead)
        for campo in _CAMPOS_DEL_PAYLOAD:
            self.assertIn(campo, payload, f"{campo} falta: el payload dejo de ser snapshot")

    def test_los_dos_solicita_son_booleanos_reales_y_usa_ia_es_triestado(self):
        """La semantica que el contrato del receptor tiene que respetar.

        `usa_ia_actualmente` es null=True en el modelo: sus tres estados son
        si / no / no se sabe. Los dos `solicita_*` son BooleanField(default=False)
        sin null, asi que en el cable son siempre booleanos reales -- nunca
        null. El receptor los valida como z.boolean() obligatorio y eso es
        correcto; documentado aca para que nadie lo "arregle" haciendolos
        nullable sin cambiar el receptor.
        """
        payload = payload_del_lead(self.lead)
        self.assertIsNone(payload["usa_ia_actualmente"])
        self.assertIs(payload["solicita_consultoria"], False)
        self.assertIs(payload["solicita_contacto_humano"], False)

    def test_el_payload_lleva_las_tres_claves(self):
        from bot.business.lead_intouch import marcar_evento

        marcar_evento(self.lead)
        self.lead.refresh_from_db()
        payload = payload_del_lead(self.lead)
        self.assertEqual(len(payload["clave_contacto"]), 32)
        self.assertEqual(payload["evento_id"], self.lead.evento_id)
        self.assertEqual(payload["revision"], 1)
