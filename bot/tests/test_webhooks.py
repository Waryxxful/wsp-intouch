import hmac
import hashlib
import json
from unittest.mock import AsyncMock, patch
from django.test import TestCase, Client, override_settings
from django.conf import settings


class WebhookVerifyTest(TestCase):
    def test_get_con_verify_token_correcto_devuelve_challenge(self):
        client = Client()
        response = client.get("/webhook", {
            "hub.mode": "subscribe",
            "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
            "hub.challenge": "12345",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "12345")

    def test_get_con_verify_token_incorrecto_devuelve_403(self):
        client = Client()
        response = client.get("/webhook", {
            "hub.mode": "subscribe",
            "hub.verify_token": "token-malo",
            "hub.challenge": "12345",
        })
        self.assertEqual(response.status_code, 403)


class WebhookParseTest(TestCase):
    def test_process_incoming_message_extrae_texto_plano(self):
        from bot.whatsapp.webhooks import process_incoming_message
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222",
                "id": "wamid.123",
                "type": "text",
                "text": {"body": "hola"},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        result = process_incoming_message(payload)
        self.assertEqual(result, [("56911112222", "Juan", "hola", "wamid.123", None)])


@override_settings(WHATSAPP_APP_SECRET="test-secret-key")
class WebhookSignatureTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.body = b'{"entry": []}'
        self.app_secret = "test-secret-key"

    @patch("bot.whatsapp.webhooks._dispatch")
    def test_valid_signature_is_accepted(self, mock_dispatch):
        """POST with valid HMAC-SHA256 signature should return 200."""
        expected_sig = "sha256=" + hmac.new(
            self.app_secret.encode(),
            self.body,
            hashlib.sha256
        ).hexdigest()
        response = self.client.post(
            "/webhook",
            data=self.body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=expected_sig
        )
        self.assertEqual(response.status_code, 200)
        mock_dispatch.assert_called_once()

    def test_invalid_signature_is_rejected(self):
        """POST with invalid HMAC-SHA256 signature should return 403."""
        response = self.client.post(
            "/webhook",
            data=self.body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=deadbeef"
        )
        self.assertEqual(response.status_code, 403)

    def test_missing_signature_is_rejected(self):
        """POST without X-Hub-Signature-256 header should return 403 when secret is configured."""
        response = self.client.post(
            "/webhook",
            data=self.body,
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)


class WebhookParseMediaTest(TestCase):
    @patch("bot.flow.media_processing.resolve_image_text")
    def test_imagen_procesada_devuelve_texto_de_gemini_y_ruta_de_media(self, mock_resolve):
        from bot.whatsapp.webhooks import process_incoming_message
        mock_resolve.return_value = ("Se ve un Renault Kwid rojo", "abc123.jpg")
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222", "id": "wamid.img1", "type": "image",
                "image": {"id": "media-abc", "mime_type": "image/jpeg"},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        result = process_incoming_message(payload)
        self.assertEqual(
            result, [("56911112222", "Juan", "Se ve un Renault Kwid rojo", "wamid.img1", "abc123.jpg")]
        )
        mock_resolve.assert_called_once_with("media-abc", "image/jpeg")

    @patch("bot.flow.media_processing.resolve_image_text")
    def test_imagen_con_fallo_de_procesamiento_devuelve_marcador_fallback(self, mock_resolve):
        from bot.whatsapp.webhooks import process_incoming_message, _marcador_media_fallback
        mock_resolve.return_value = (None, None)
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222", "id": "wamid.img2", "type": "image",
                "image": {"id": "media-abc", "mime_type": "image/jpeg"},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        result = process_incoming_message(payload)
        self.assertEqual(
            result, [("56911112222", "Juan", _marcador_media_fallback("image"), "wamid.img2", None)]
        )

    @patch("bot.flow.media_processing.resolve_audio_text")
    def test_audio_procesado_devuelve_texto_transcrito_y_ruta_de_media(self, mock_resolve):
        from bot.whatsapp.webhooks import process_incoming_message
        mock_resolve.return_value = ("hola quiero cotizar un auto", "def456.ogg")
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222", "id": "wamid.audio1", "type": "audio",
                "audio": {"id": "media-def", "mime_type": "audio/ogg; codecs=opus", "voice": True},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        result = process_incoming_message(payload)
        self.assertEqual(
            result, [("56911112222", "Juan", "hola quiero cotizar un auto", "wamid.audio1", "def456.ogg")]
        )
        mock_resolve.assert_called_once_with("media-def", "audio/ogg; codecs=opus")

    @patch("bot.flow.media_processing.resolve_audio_text")
    def test_audio_con_fallo_de_procesamiento_devuelve_marcador_fallback(self, mock_resolve):
        from bot.whatsapp.webhooks import process_incoming_message, _marcador_media_fallback
        mock_resolve.return_value = (None, None)
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222", "id": "wamid.audio2", "type": "audio",
                "audio": {"id": "media-def", "mime_type": "audio/ogg"},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        result = process_incoming_message(payload)
        self.assertEqual(
            result, [("56911112222", "Juan", _marcador_media_fallback("audio"), "wamid.audio2", None)]
        )

    def test_video_documento_sticker_ubicacion_devuelven_marcador_fallback(self):
        from bot.whatsapp.webhooks import process_incoming_message, _marcador_media_fallback
        casos = [
            ("video", {"video": {"id": "v1", "mime_type": "video/mp4"}}),
            ("document", {"document": {"id": "d1", "mime_type": "application/pdf"}}),
            ("sticker", {"sticker": {"id": "s1", "mime_type": "image/webp"}}),
            ("location", {"location": {"latitude": -33.45, "longitude": -70.66}}),
        ]
        for tipo, campo_extra in casos:
            msg = {"from": "56911112222", "id": f"wamid.{tipo}", "type": tipo}
            msg.update(campo_extra)
            payload = {"entry": [{"changes": [{"value": {
                "messages": [msg], "contacts": [{"profile": {"name": "Juan"}}],
            }}]}]}
            result = process_incoming_message(payload)
            self.assertEqual(
                result, [("56911112222", "Juan", _marcador_media_fallback(tipo), f"wamid.{tipo}", None)],
                msg=f"tipo={tipo}",
            )


class WebhookParseMediaObservabilityTest(TestCase):
    # La percepcion de imagen/audio corre en process_incoming_message, ANTES
    # de que exista el unico @observe del flujo (_run_graph, en handlers.py)
    # -- sin esto, resolve_image_text/resolve_audio_text (que ahora tienen su
    # propio @observe, ver MediaPerceptionObservabilityTest en
    # test_media_processing.py) quedarian como traces sueltos en Langfuse,
    # sin agrupar bajo el mismo wa_id que el resto de la conversacion.
    @patch("bot.whatsapp.webhooks.propagate_attributes")
    @patch("bot.flow.media_processing.resolve_image_text")
    def test_percepcion_de_imagen_propaga_session_id_y_user_id_del_wa_id(self, mock_resolve, mock_propagate):
        from bot.whatsapp.webhooks import process_incoming_message
        mock_resolve.return_value = ("Se ve un Renault Kwid rojo", None)
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222", "id": "wamid.img1", "type": "image",
                "image": {"id": "media-abc", "mime_type": "image/jpeg"},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        process_incoming_message(payload)
        mock_propagate.assert_called_once_with(session_id="56911112222", user_id="56911112222")

    @patch("bot.whatsapp.webhooks.propagate_attributes")
    @patch("bot.flow.media_processing.resolve_audio_text")
    def test_percepcion_de_audio_propaga_session_id_y_user_id_del_wa_id(self, mock_resolve, mock_propagate):
        from bot.whatsapp.webhooks import process_incoming_message
        mock_resolve.return_value = ("hola quiero cotizar un auto", None)
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222", "id": "wamid.audio1", "type": "audio",
                "audio": {"id": "media-def", "mime_type": "audio/ogg"},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        process_incoming_message(payload)
        mock_propagate.assert_called_once_with(session_id="56911112222", user_id="56911112222")


class ProcessIncomingMessageIdempotencyTest(TestCase):
    def test_mensaje_ya_procesado_no_se_reprocesa(self):
        from bot.models import Conversation, Message
        from bot.whatsapp.webhooks import process_incoming_message
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="hola", wa_msg_id="wamid.dup")
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222", "id": "wamid.dup", "type": "text",
                "text": {"body": "hola de nuevo"},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        result = process_incoming_message(payload)
        self.assertEqual(result, [])

    @patch("bot.flow.media_processing.resolve_image_text")
    def test_imagen_duplicada_no_gasta_la_percepcion(self, mock_resolve):
        from bot.models import Conversation, Message
        from bot.whatsapp.webhooks import process_incoming_message
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="foto", wa_msg_id="wamid.imgdup")
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "56911112222", "id": "wamid.imgdup", "type": "image",
                "image": {"id": "media-abc", "mime_type": "image/jpeg"},
            }], "contacts": [{"profile": {"name": "Juan"}}]}}]}]
        }
        self.assertEqual(process_incoming_message(payload), [])
        mock_resolve.assert_not_called()


class DispatchMediaFallbackTest(TestCase):
    @patch("bot.whatsapp.handlers.handle_unsupported_media", new_callable=AsyncMock)
    @patch("bot.whatsapp.webhooks.process_incoming_message")
    def test_marcador_fallback_rutea_a_handle_unsupported_media(self, mock_process, mock_handle_unsupported):
        from bot.whatsapp.webhooks import _dispatch, _marcador_media_fallback
        mock_process.return_value = [
            ("56911112222", "Juan", _marcador_media_fallback("video"), "wamid.1", None)
        ]
        _dispatch({})
        mock_handle_unsupported.assert_called_once_with("56911112222", "Juan", "wamid.1", "video")

    @patch("bot.whatsapp.handlers.handle_message", new_callable=AsyncMock)
    @patch("bot.whatsapp.webhooks.process_incoming_message")
    def test_texto_normal_rutea_a_handle_message(self, mock_process, mock_handle_message):
        from bot.whatsapp.webhooks import _dispatch
        mock_process.return_value = [("56911112222", "Juan", "hola", "wamid.2", None)]
        _dispatch({})
        mock_handle_message.assert_called_once_with("56911112222", "Juan", "hola", "wamid.2", media_url=None)

    @patch("bot.whatsapp.handlers.handle_message", new_callable=AsyncMock)
    @patch("bot.whatsapp.webhooks.process_incoming_message")
    def test_imagen_procesada_pasa_media_url_a_handle_message(self, mock_process, mock_handle_message):
        from bot.whatsapp.webhooks import _dispatch
        mock_process.return_value = [
            ("56911112222", "Juan", "Se ve un Renault Kwid rojo", "wamid.img1", "abc123.jpg")
        ]
        _dispatch({})
        mock_handle_message.assert_called_once_with(
            "56911112222", "Juan", "Se ve un Renault Kwid rojo", "wamid.img1", media_url="abc123.jpg"
        )
