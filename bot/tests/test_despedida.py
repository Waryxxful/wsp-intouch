"""Después de una despedida el bot no sigue mandando mensajes.

Regla del usuario (2026-09-03): *"después de una despedida no debería seguir
enviando mensajes el bot hasta que haya un mensaje que reactive la conversación
con alguna intención clara"*.

Caso real que la motivó (conversación del vendedor, docs/PENDIENTES.md #23):

    bot   "Quedó todo coordinado... ¡Nos vemos el viernes! 🚗"
    user  "Gracias"
    bot   "¡Con gusto! Quedó todo listo... ¡Nos vemos el viernes! 🚗👋"  <-- de más

Dos causas: "Ok gracias" no se reconocía como acuse (la lista comparaba
palabras sueltas por igualdad exacta), y el primer acuse pasaba al LLM por
diseño.
"""
import asyncio
from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase

from bot.models import Conversation, Message
from bot.whatsapp import handlers


class DespedidaTest(TransactionTestCase):
    """TransactionTestCase: handle_message lee la BD vía sync_to_async, o sea
    desde otro thread, y sqlite en shared-cache bloquea esa lectura con la
    transacción sin commitear (mismo gotcha que GraphGetLlmOverrideTest)."""

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56900000777", name="Felipe")
        self.conv.flow_state = "ESPERANDO_CONSULTA"
        self.conv.save()

    def _responder(self, texto_bot, texto_usuario, msg_id="wamid.1"):
        """Deja `texto_bot` como último mensaje del bot, manda `texto_usuario`
        y devuelve lo que el bot contestó (None = silencio)."""
        Message.objects.create(conversation=self.conv, role="assistant", content=texto_bot)
        cliente = MagicMock()

        async def _grafo(*a, **kw):
            # Si el grafo corre, simula una respuesta elaborada.
            await handlers._save_message(self.conv, "assistant", "RESPUESTA DEL LLM")
            cliente.send_text(self.conv.wa_id, "RESPUESTA DEL LLM")

        with patch.object(handlers, "get_wa_client", return_value=cliente), \
             patch.object(handlers, "_run_graph", new=_grafo):
            asyncio.run(handlers.handle_message(
                self.conv.wa_id, "Felipe", texto_usuario, msg_id))
        enviados = [c[0][1] for c in cliente.send_text.call_args_list]
        return enviados[0] if enviados else None

    def test_no_responde_un_acuse_tras_cerrar(self):
        # El caso exacto de la conversación real.
        respuesta = self._responder(
            "¡Con gusto, Felipe! Quedó todo coordinado. ¡Nos vemos el viernes! 🚗",
            "Gracias")
        self.assertIsNone(respuesta)

    def test_tampoco_responde_un_acuse_de_varias_palabras(self):
        # "Ok gracias" no se reconocía como acuse y se iba al LLM.
        respuesta = self._responder(
            "Quedó todo listo. ¡Nos vemos el viernes! 🚗", "Ok gracias")
        self.assertIsNone(respuesta)

    def test_si_responde_cuando_el_mensaje_trae_intencion(self):
        # "reactivar la conversación con intención clara": esto sí pasa.
        respuesta = self._responder(
            "Quedó todo listo. ¡Nos vemos el viernes! 🚗",
            "Ok, pero quiero ver otra opción")
        self.assertEqual(respuesta, "RESPUESTA DEL LLM")

    def test_si_responde_un_acuse_cuando_el_bot_pregunto_algo(self):
        # Con una pregunta abierta, "perfecto" puede significar "sí, dale":
        # tiene que llegar al especialista, no cortarse.
        respuesta = self._responder("¿Te sirve esa hora de las 16:30?", "Perfecto")
        self.assertEqual(respuesta, "RESPUESTA DEL LLM")

    def test_reconoce_la_pregunta_con_signo_de_apertura(self):
        respuesta = self._responder("¿Algo más en que te pueda ayudar?", "Ok")
        self.assertEqual(respuesta, "RESPUESTA DEL LLM")

    def test_sin_mensajes_previos_del_bot_no_hay_cierre(self):
        # Primera interacción: nada que cerrar.
        cliente = MagicMock()

        async def _grafo(*a, **kw):
            cliente.send_text(self.conv.wa_id, "RESPUESTA DEL LLM")

        with patch.object(handlers, "get_wa_client", return_value=cliente), \
             patch.object(handlers, "_run_graph", new=_grafo):
            asyncio.run(handlers.handle_message(
                self.conv.wa_id, "Felipe", "gracias", "wamid.9"))
        self.assertTrue(cliente.send_text.called)


class AcuseTrivialTest(TransactionTestCase):
    def test_reconoce_acuses_de_varias_palabras(self):
        for texto in ("Ok gracias", "muchas gracias", "Ok, muchas gracias",
                      "todo bien gracias", "ok", "gracias", "👍", ""):
            with self.subTest(texto=texto):
                self.assertTrue(handlers._es_mensaje_trivial(texto))

    def test_no_traga_un_mensaje_con_contenido(self):
        for texto in ("ok me interesa", "ok pero tengo otra pregunta",
                      "Este viernes a las 16:00", "Ok que me contacte hoy"):
            with self.subTest(texto=texto):
                self.assertFalse(handlers._es_mensaje_trivial(texto))

    def test_un_si_no_es_acuse(self):
        # Responder "sí" a "¿te sirve esa hora?" es contenido, no acuse.
        self.assertFalse(handlers._es_mensaje_trivial("si"))
        self.assertFalse(handlers._es_mensaje_trivial("sí"))

    def test_solo_relleno_no_es_acuse(self):
        self.assertFalse(handlers._es_mensaje_trivial("muchas"))
        self.assertFalse(handlers._es_mensaje_trivial("ya"))
