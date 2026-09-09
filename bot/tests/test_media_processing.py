import time
from unittest.mock import MagicMock, patch

from django.test import TestCase


class GetMediaLlmTest(TestCase):
    @patch("bot.flow.media_processing.ChatOpenRouter")
    def test_usa_el_default_de_settings_sin_override(self, mock_chat_cls):
        from django.conf import settings
        from bot.flow.media_processing import _get_media_llm
        _get_media_llm()
        mock_chat_cls.assert_called_once_with(
            model=settings.OPENROUTER_MEDIA_MODEL, api_key=settings.OPENROUTER_API_KEY,
            timeout=30_000, max_retries=0, reasoning={"effort": "medium"},
        )

    @patch("bot.flow.media_processing.ChatOpenRouter")
    def test_timeout_en_milisegundos_y_sin_reintento_del_sdk(self, mock_chat_cls):
        # Bug real encontrado verificando esta migracion: un `timeout=30`
        # (interpretado como 30 MILISEGUNDOS por ChatOpenRouter, no segundos)
        # hacia que cualquier llamada real fallara casi instantaneo y entrara
        # al retry interno del SDK (default max_retries=2, hasta ~300s de
        # backoff por llamada) -- el hilo del ThreadPoolExecutor de
        # _media_llm_executor quedaba colgado hasta ~5 minutos por imagen/audio
        # real, agotando sus 2 workers ante 2 solicitudes seguidas. Reproducido
        # end-to-end contra la API real de OpenRouter antes de este fix.
        from bot.flow.media_processing import _get_media_llm
        _get_media_llm()
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["timeout"], 30_000)
        self.assertEqual(kwargs["max_retries"], 0)

    @patch("bot.flow.media_processing.ChatOpenRouter")
    def test_usa_el_override_de_setting_si_existe(self, mock_chat_cls):
        from bot.models import Setting
        Setting.objects.create(key="openrouter_media_model_override", value="google/gemini-3.7-pro")
        from bot.flow.media_processing import _get_media_llm
        _get_media_llm()
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["model"], "google/gemini-3.7-pro")

    @patch("bot.flow.media_processing.ChatOpenRouter")
    def test_usa_el_override_de_api_key_si_existe(self, mock_chat_cls):
        # Mismo override que usa el LLM conversacional (bot/flow/graph.py) --
        # media y conversacional comparten una sola API key de OpenRouter.
        from bot.models import Setting
        Setting.objects.create(key="openrouter_api_key_override", value="sk-or-override")
        from bot.flow.media_processing import _get_media_llm
        _get_media_llm()
        _, kwargs = mock_chat_cls.call_args
        self.assertEqual(kwargs["api_key"], "sk-or-override")


class PromptDePercepcionSinMarcaTest(TestCase):
    """Este repo es un clon de wsp_demo (Renault/Astara) adaptado a Cavem, y el
    prompt de percepcion de imagenes quedo pidiendole al modelo de vision que
    mirara la foto "pensando en el contexto de una venta de autos Renault" --
    texto que se le mandaba tal cual al modelo cada vez que un cliente de Cavem
    mandaba una foto. Era el ultimo resto del clon vivo y en el camino caliente.

    El prompt no nombra NINGUNA marca a proposito: Cavem vende usados
    multimarca (VehiculoUsado, ver CLAUDE.md), asi que nombrar una sesga la
    identificacion justo en la frase que le pide identificar la marca. Y un
    nombre fijo en codigo se desincroniza del cliente activo: la suite corre
    con CLIENTE_ACTIVO=renault mientras el bot corre como cavem, o sea que un
    "Cavem" hardcodeado pasaria el test y diria otra cosa en produccion.
    """

    def test_el_prompt_de_imagen_no_nombra_ninguna_marca(self):
        from bot.models import CLIENTE_CHOICES
        from bot.flow.media_processing import _PROMPT_DESCRIBIR_IMAGEN
        prompt = _PROMPT_DESCRIBIR_IMAGEN.lower()
        for slug, etiqueta in CLIENTE_CHOICES:
            self.assertNotIn(slug.lower(), prompt)
            self.assertNotIn(etiqueta.lower(), prompt)

    def test_el_prompt_de_imagen_sigue_pidiendo_identificar_marca_y_modelo(self):
        # Lo unico que el prompt tenia que conservar al sacarle "Renault": que
        # siga pidiendo marca/modelo del auto (es lo que despues lee el
        # especialista) y que siga acotando el largo y el idioma.
        from bot.flow.media_processing import _PROMPT_DESCRIBIR_IMAGEN
        self.assertIn("marca/modelo", _PROMPT_DESCRIBIR_IMAGEN)
        self.assertIn("español", _PROMPT_DESCRIBIR_IMAGEN)

    def test_el_prompt_de_audio_tampoco_nombra_una_marca(self):
        from bot.models import CLIENTE_CHOICES
        from bot.flow.media_processing import _PROMPT_TRANSCRIBIR_AUDIO
        prompt = _PROMPT_TRANSCRIBIR_AUDIO.lower()
        for slug, etiqueta in CLIENTE_CHOICES:
            self.assertNotIn(slug.lower(), prompt)
            self.assertNotIn(etiqueta.lower(), prompt)


class DescribeImageTest(TestCase):
    @patch("bot.flow.media_processing._get_media_llm")
    def test_describe_image_ok(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="Se ve un Renault Kwid rojo")
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import describe_image
        self.assertEqual(describe_image(b"bytes-imagen", "image/jpeg"), "Se ve un Renault Kwid rojo")

    @patch("bot.flow.media_processing._get_media_llm")
    def test_describe_image_error_devuelve_none(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("boom")
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import describe_image
        self.assertIsNone(describe_image(b"bytes-imagen", "image/jpeg"))

    @patch("bot.flow.media_processing._get_media_llm")
    def test_describe_image_respuesta_vacia_devuelve_none(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="   ")
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import describe_image
        self.assertIsNone(describe_image(b"bytes-imagen", "image/jpeg"))

    @patch("bot.flow.media_processing._get_media_llm")
    def test_describe_image_maneja_content_como_lista_de_bloques(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=[
            {"type": "text", "text": "Se ve un Renault Kwid rojo", "extras": {"signature": "abc123"}},
        ])
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import describe_image
        self.assertEqual(describe_image(b"bytes-imagen", "image/jpeg"), "Se ve un Renault Kwid rojo")

    @patch("bot.flow.media_processing._get_media_llm")
    def test_fallo_al_construir_el_llm_devuelve_none(self, mock_get_llm):
        mock_get_llm.side_effect = Exception("api key vacia")
        from bot.flow.media_processing import describe_image
        self.assertIsNone(describe_image(b"bytes-imagen", "image/jpeg"))


class InvokeMediaDeadlineTest(TestCase):
    """Bug real en produccion (2026-08-19): Gemini degradado (503/504,
    "high demand") + reintentos internos del SDK google-genai hicieron que
    una sola llamada superara el --timeout 120 de gunicorn (Dockerfile).
    Gunicorn mato al worker con SIGABRT -> SystemExit, que NO es subclase
    de Exception y por lo tanto nunca lo agarra el except Exception de
    _invoke_media -- la request se perdio sin respuesta ni fallback. Se
    acota la llamada a un plazo propio, muy por debajo de esos 120s, para
    que _invoke_media siempre devuelva el control (con None, si corresponde)
    antes de que gunicorn intervenga."""

    @patch("bot.flow.media_processing._MEDIA_LLM_DEADLINE_SEGUNDOS", 0.1)
    @patch("bot.flow.media_processing._get_media_llm")
    def test_llamada_que_supera_el_plazo_devuelve_none_sin_bloquear(self, mock_get_llm):
        mock_llm = MagicMock()

        def invoke_lento(*args, **kwargs):
            time.sleep(1)
            return MagicMock(content="no deberia llegar aca")

        mock_llm.invoke.side_effect = invoke_lento
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import describe_image

        inicio = time.monotonic()
        resultado = describe_image(b"bytes-imagen", "image/jpeg")
        duracion = time.monotonic() - inicio

        self.assertIsNone(resultado)
        self.assertLess(duracion, 0.5)


class TranscribeAudioTest(TestCase):
    @patch("bot.flow.media_processing._get_media_llm")
    def test_transcribe_audio_ok(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="hola quiero cotizar un auto")
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import transcribe_audio
        self.assertEqual(transcribe_audio(b"bytes-audio", "audio/ogg"), "hola quiero cotizar un auto")

    @patch("bot.flow.media_processing._get_media_llm")
    def test_transcribe_audio_error_devuelve_none(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("boom")
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import transcribe_audio
        self.assertIsNone(transcribe_audio(b"bytes-audio", "audio/ogg"))


class DescribeImageContentBlockTest(TestCase):
    @patch("bot.flow.media_processing._get_media_llm")
    def test_arma_bloque_image_url_con_data_uri(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="descripcion")
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import describe_image
        describe_image(b"contenido", "image/jpeg")

        mensajes = mock_llm.invoke.call_args[0][0]
        bloques = mensajes[0].content
        self.assertEqual(bloques[0]["type"], "text")
        self.assertEqual(bloques[1]["type"], "image_url")
        self.assertTrue(bloques[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))


class TranscribeAudioFormatoTest(TestCase):
    @patch("bot.flow.media_processing._get_media_llm")
    def test_arma_bloque_input_audio_con_formato_mapeado(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="transcripcion")
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import transcribe_audio
        transcribe_audio(b"contenido", "audio/ogg")

        mensajes = mock_llm.invoke.call_args[0][0]
        bloques = mensajes[0].content
        self.assertEqual(bloques[1]["type"], "input_audio")
        self.assertEqual(bloques[1]["input_audio"]["format"], "ogg")

    def test_mime_type_no_soportado_devuelve_none_sin_llamar_al_llm(self):
        # No se mockea _get_media_llm a proposito: si el mime type no
        # soportado no corta ANTES de construir el LLM, esto reventaria por
        # falta de API key real -- confirma que el corte es temprano.
        from bot.flow.media_processing import transcribe_audio
        with self.assertLogs("bot.flow.media_processing", level="WARNING") as logs:
            resultado = transcribe_audio(b"contenido", "audio/desconocido")
        self.assertIsNone(resultado)
        self.assertIn("no soportado", "\n".join(logs.output))

    @patch("bot.flow.media_processing._get_media_llm")
    def test_mime_types_soportados_mapean_al_formato_correcto(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="ok")
        mock_get_llm.return_value = mock_llm
        from bot.flow.media_processing import transcribe_audio
        casos = {
            "audio/wav": "wav", "audio/mpeg": "mp3", "audio/aac": "aac",
            "audio/ogg": "ogg", "audio/flac": "flac", "audio/mp4": "m4a",
        }
        for mime, formato_esperado in casos.items():
            transcribe_audio(b"x", mime)
            mensajes = mock_llm.invoke.call_args[0][0]
            self.assertEqual(mensajes[0].content[1]["input_audio"]["format"], formato_esperado)


class MimeTypeNormalizadoTest(TestCase):
    def test_recorta_los_parametros_extra(self):
        from bot.flow.media_processing import _mime_type_normalizado
        self.assertEqual(_mime_type_normalizado("image/png; codecs=x"), "image/png")
        self.assertEqual(_mime_type_normalizado("audio/ogg; codecs=opus"), "audio/ogg")

    def test_descarta_el_generico_octet_stream(self):
        from bot.flow.media_processing import _mime_type_normalizado
        self.assertIsNone(_mime_type_normalizado("application/octet-stream"))

    def test_descarta_vacio_y_none(self):
        from bot.flow.media_processing import _mime_type_normalizado
        self.assertIsNone(_mime_type_normalizado(""))
        self.assertIsNone(_mime_type_normalizado(None))
        self.assertIsNone(_mime_type_normalizado("; codecs=x"))

    def test_deja_intacto_un_mime_type_simple(self):
        from bot.flow.media_processing import _mime_type_normalizado
        self.assertEqual(_mime_type_normalizado("image/jpeg"), "image/jpeg")


class ResolveImageTextTest(TestCase):
    @patch("bot.flow.media_processing.guardar_media_privado")
    @patch("bot.flow.media_processing.describe_image")
    @patch("bot.flow.media_processing.download_media")
    def test_combina_descarga_descripcion_y_guardado(self, mock_download, mock_describe, mock_guardar):
        mock_download.return_value = (b"bytes-imagen", "image/jpeg")
        mock_describe.return_value = "Se ve un Renault Kwid rojo"
        mock_guardar.return_value = "abc123.jpg"
        from bot.flow.media_processing import resolve_image_text
        self.assertEqual(resolve_image_text("media123"), ("Se ve un Renault Kwid rojo", "abc123.jpg"))
        mock_download.assert_called_once_with("media123")
        mock_describe.assert_called_once_with(b"bytes-imagen", "image/jpeg")
        mock_guardar.assert_called_once_with(b"bytes-imagen", "image/jpeg", "image")

    @patch("bot.flow.media_processing.download_media")
    def test_sin_media_id_devuelve_none_none(self, mock_download):
        from bot.flow.media_processing import resolve_image_text
        self.assertEqual(resolve_image_text(""), (None, None))
        mock_download.assert_not_called()

    @patch("bot.flow.media_processing.download_media")
    def test_fallo_de_descarga_devuelve_none_none(self, mock_download):
        mock_download.return_value = (None, None)
        from bot.flow.media_processing import resolve_image_text
        self.assertEqual(resolve_image_text("media123"), (None, None))

    @patch("bot.flow.media_processing.guardar_media_privado")
    @patch("bot.flow.media_processing.describe_image")
    @patch("bot.flow.media_processing.download_media")
    def test_hint_tiene_prioridad_sobre_el_mime_type_descargado(self, mock_download, mock_describe, mock_guardar):
        mock_download.return_value = (b"bytes-imagen", "application/octet-stream")
        mock_describe.return_value = "Se ve un Renault Kwid rojo"
        mock_guardar.return_value = None
        from bot.flow.media_processing import resolve_image_text
        self.assertEqual(
            resolve_image_text("media123", "image/png; codecs=x"), ("Se ve un Renault Kwid rojo", None)
        )
        mock_describe.assert_called_once_with(b"bytes-imagen", "image/png")

    @patch("bot.flow.media_processing.guardar_media_privado")
    @patch("bot.flow.media_processing.describe_image")
    @patch("bot.flow.media_processing.download_media")
    def test_sin_hint_usa_el_mime_type_descargado_normalizado(self, mock_download, mock_describe, mock_guardar):
        mock_download.return_value = (b"bytes-imagen", "image/jpeg; charset=binary")
        mock_describe.return_value = "Se ve un Renault Kwid rojo"
        mock_guardar.return_value = None
        from bot.flow.media_processing import resolve_image_text
        self.assertEqual(resolve_image_text("media123"), ("Se ve un Renault Kwid rojo", None))
        mock_describe.assert_called_once_with(b"bytes-imagen", "image/jpeg")

    @patch("bot.flow.media_processing.describe_image")
    @patch("bot.flow.media_processing.download_media")
    def test_sin_mime_type_utilizable_devuelve_none_none(self, mock_download, mock_describe):
        mock_download.return_value = (b"bytes-imagen", "application/octet-stream")
        from bot.flow.media_processing import resolve_image_text
        self.assertEqual(resolve_image_text("media123", ""), (None, None))
        mock_describe.assert_not_called()

    @patch("bot.flow.media_processing.guardar_media_privado")
    @patch("bot.flow.media_processing.describe_image")
    @patch("bot.flow.media_processing.download_media")
    def test_fallo_al_guardar_media_no_impide_devolver_el_texto(self, mock_download, mock_describe, mock_guardar):
        # guardar_media_privado nunca deberia lanzar (contrato propio), pero
        # si de todos modos devuelve None (mime no soportado, fallo de disco),
        # el texto de percepcion ya generado se sigue devolviendo igual.
        mock_download.return_value = (b"bytes-imagen", "image/jpeg")
        mock_describe.return_value = "Se ve un Renault Kwid rojo"
        mock_guardar.return_value = None
        from bot.flow.media_processing import resolve_image_text
        self.assertEqual(resolve_image_text("media123"), ("Se ve un Renault Kwid rojo", None))


class ResolveAudioTextTest(TestCase):
    @patch("bot.flow.media_processing.guardar_media_privado")
    @patch("bot.flow.media_processing.transcribe_audio")
    @patch("bot.flow.media_processing.download_media")
    def test_combina_descarga_transcripcion_y_guardado(self, mock_download, mock_transcribe, mock_guardar):
        mock_download.return_value = (b"bytes-audio", "audio/ogg")
        mock_transcribe.return_value = "hola quiero cotizar un auto"
        mock_guardar.return_value = "def456.ogg"
        from bot.flow.media_processing import resolve_audio_text
        self.assertEqual(resolve_audio_text("media456"), ("hola quiero cotizar un auto", "def456.ogg"))
        mock_download.assert_called_once_with("media456")
        mock_transcribe.assert_called_once_with(b"bytes-audio", "audio/ogg")
        mock_guardar.assert_called_once_with(b"bytes-audio", "audio/ogg", "audio")

    @patch("bot.flow.media_processing.download_media")
    def test_sin_media_id_devuelve_none_none(self, mock_download):
        from bot.flow.media_processing import resolve_audio_text
        self.assertEqual(resolve_audio_text(""), (None, None))
        mock_download.assert_not_called()

    @patch("bot.flow.media_processing.guardar_media_privado")
    @patch("bot.flow.media_processing.transcribe_audio")
    @patch("bot.flow.media_processing.download_media")
    def test_hint_tiene_prioridad_sobre_el_mime_type_descargado(self, mock_download, mock_transcribe, mock_guardar):
        mock_download.return_value = (b"bytes-audio", "application/octet-stream")
        mock_transcribe.return_value = "hola quiero cotizar un auto"
        mock_guardar.return_value = None
        from bot.flow.media_processing import resolve_audio_text
        self.assertEqual(
            resolve_audio_text("media456", "audio/ogg; codecs=opus"), ("hola quiero cotizar un auto", None)
        )
        mock_transcribe.assert_called_once_with(b"bytes-audio", "audio/ogg")

    @patch("bot.flow.media_processing.transcribe_audio")
    @patch("bot.flow.media_processing.download_media")
    def test_sin_mime_type_utilizable_devuelve_none_none(self, mock_download, mock_transcribe):
        mock_download.return_value = (b"bytes-audio", "application/octet-stream")
        from bot.flow.media_processing import resolve_audio_text
        self.assertEqual(resolve_audio_text("media456"), (None, None))
        mock_transcribe.assert_not_called()


class MediaPerceptionObservabilityTest(TestCase):
    # PENDIENTES.md: _invoke_media no tenia CallbackHandler de Langfuse, y
    # resolve_image_text/resolve_audio_text corrian en bot/whatsapp/webhooks.py
    # ANTES de que exista el unico @observe del flujo (_run_graph, en
    # handlers.py) -- la percepcion de imagen/audio era invisible en
    # Langfuse. Fix: resolve_image_text/resolve_audio_text ganan su propio
    # @observe (trace propio, agrupado por session_id/user_id via
    # propagate_attributes en el call site de webhooks.py -- ver
    # WebhookParseMediaObservabilityTest en test_webhooks.py), y _invoke_media
    # le pasa un CallbackHandler fresco al llm.invoke real para que la
    # generation quede capturada dentro de ese trace.
    @patch("bot.flow.media_processing.CallbackHandler")
    @patch("bot.flow.media_processing._get_media_llm")
    def test_invoke_media_le_pasa_un_callback_handler_al_llm(self, mock_get_llm, mock_cb_cls):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="Se ve un Renault Kwid rojo")
        mock_get_llm.return_value = mock_llm
        mock_handler = MagicMock()
        mock_cb_cls.return_value = mock_handler
        from bot.flow.media_processing import describe_image
        describe_image(b"bytes-imagen", "image/jpeg")
        _, kwargs = mock_llm.invoke.call_args
        self.assertEqual(kwargs.get("config", {}).get("callbacks"), [mock_handler])

    def test_resolve_image_text_esta_decorada_con_observe(self):
        from bot.flow.media_processing import resolve_image_text
        self.assertTrue(hasattr(resolve_image_text, "__wrapped__"))

    def test_resolve_audio_text_esta_decorada_con_observe(self):
        from bot.flow.media_processing import resolve_audio_text
        self.assertTrue(hasattr(resolve_audio_text, "__wrapped__"))


class AcuseAntesDePercibirTest(TestCase):
    """El acuse tiene que salir ANTES de la percepcion, no despues.

    Medido el 2026-09-03: la percepcion de media (descarga desde Meta + LLM de
    vision) tarda 4,8-6,5s y corre dentro del request del webhook, ANTES de
    handle_message -- que era donde vivia el unico acuse. Quien mandaba una
    foto veia 5-6 segundos de silencio total, sin doble check azul y sin
    "escribiendo...", antes de la primera senal de vida.
    """

    def _payload(self, tipo, media_id="mid.1"):
        return {"entry": [{"changes": [{"value": {
            "contacts": [{"profile": {"name": "Tomas"}}],
            "messages": [{"from": "56911112222", "id": "wamid.media.1",
                          "type": tipo, tipo: {"id": media_id, "mime_type": f"{tipo}/jpeg"}}],
        }}]}]}

    def _correr(self, tipo, resolver):
        from bot.whatsapp.webhooks import process_incoming_message
        orden = []
        cliente = MagicMock()
        cliente.mark_as_read.side_effect = lambda *a, **kw: orden.append(("acuse", kw))

        def _percibe(*a, **kw):
            orden.append(("percepcion", {}))
            return ("lo que se vio", None)

        with patch("bot.whatsapp.client.get_wa_client", return_value=cliente), \
             patch(resolver, side_effect=_percibe), \
             patch("bot.whatsapp.cola_envio._EJECUTOR_ACUSE") as ejecutor:
            # El executor real es un thread: se ejecuta inline para que el
            # ORDEN sea deterministico, que es justo lo que este test mide.
            ejecutor.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
            process_incoming_message(self._payload(tipo))
        return orden

    def test_en_una_imagen_el_acuse_sale_primero(self):
        orden = self._correr("image", "bot.flow.media_processing.resolve_image_text")
        self.assertEqual([paso for paso, _ in orden], ["acuse", "percepcion"])

    def test_en_un_audio_el_acuse_sale_primero(self):
        orden = self._correr("audio", "bot.flow.media_processing.resolve_audio_text")
        self.assertEqual([paso for paso, _ in orden], ["acuse", "percepcion"])

    def test_con_media_siempre_se_pide_el_indicador_de_escribiendo(self):
        # No es condicional como en los mensajes de texto: con media SIEMPRE se
        # va a hacer esperar, asi que el indicador corresponde siempre.
        orden = self._correr("image", "bot.flow.media_processing.resolve_image_text")
        _, kwargs = orden[0]
        self.assertTrue(kwargs.get("mostrar_escribiendo"))
