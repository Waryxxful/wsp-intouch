from datetime import timedelta
from unittest.mock import AsyncMock, patch
from django.test import TestCase
from django.utils import timezone
from asgiref.sync import async_to_sync
from bot.models import Conversation, Message, Setting
from bot.whatsapp.handlers import (
    _a_formato_whatsapp, _dividir_en_mensajes, _es_mensaje_trivial, _run_graph, handle_message,
)


class HandleMessageTest(TestCase):
    def setUp(self):
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client")
        self.mock_wa = self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)

    def test_mensaje_duplicado_por_wa_msg_id_se_ignora(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="hola", wa_msg_id="wamid.1")
        async_to_sync(handle_message)("56911112222", "Juan", "hola de nuevo", "wamid.1")
        self.assertEqual(conv.messages.count(), 1)

    def test_modo_humano_activo_no_llama_al_grafo(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        conv.set_flow({"modo": "HUMAN"})
        conv.save()
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "hola", "wamid.2")
            mock_graph.assert_not_called()

    def test_saludo_trivial_en_idle_responde_bienvenida_sin_llamar_al_grafo(self):
        Conversation.objects.create(wa_id="56911112222", flow_state="IDLE")
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "hola", "wamid.3")
            mock_graph.assert_not_called()
        self.mock_wa.return_value.send_text.assert_called_once()

    def test_texto_no_trivial_en_idle_invoca_el_grafo(self):
        Conversation.objects.create(wa_id="56911112222", flow_state="IDLE")
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "necesito agendar hora", "wamid.4")
            mock_graph.assert_called_once()

    def test_mensaje_duplicado_por_carrera_en_el_constraint_se_ignora(self):
        # Simula la ventana de carrera real: dos webhooks para el mismo
        # msg_id llegan casi al mismo tiempo y el chequeo .aexists() de
        # ambos corre ANTES de que el primero termine de guardar su
        # Message -- el segundo pasa el chequeo igual (se mockea aca para
        # forzar ese escenario). El UniqueConstraint de BD (bot/models.py)
        # es lo que realmente lo frena, no el chequeo.
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="hola", wa_msg_id="wamid.race")
        with patch("bot.whatsapp.handlers.Message.objects.filter") as mock_filter:
            mock_filter.return_value.aexists = AsyncMock(return_value=False)
            with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
                async_to_sync(handle_message)("56911112222", "Juan", "hola de nuevo", "wamid.race")
                mock_graph.assert_not_called()
        self.assertEqual(conv.messages.count(), 1)
        self.mock_wa.return_value.mark_as_read.assert_not_called()

    # Loop de despedidas -- confirmado 2026-09-01 (docs/PENDIENTES.md,
    # seccion "Revisión manual 2026-08-25 -- prompt v4.4/v1.3") que es
    # comportamiento narrativo del LLM (cada acuse trivial del cliente se
    # trata como pie para una despedida nueva), no una carrera de webhook --
    # sobrevivio 2 rondas de prompt. Decision del usuario: al 2do acuse
    # trivial consecutivo, un emoji fijo sin pasar por el LLM; al 3ro+,
    # silencio total.
    def test_primer_acuse_trivial_invoca_el_grafo_normalmente(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "gracias", "wamid.t1")
            mock_graph.assert_called_once()

    def test_acuse_tras_un_cierre_del_bot_no_manda_nada(self):
        # Antes este caso respondia "👍". Desde el 2026-09-03 la regla es la
        # del usuario: si el bot ya cerro (su ultimo mensaje no pregunta nada),
        # un acuse no recibe NINGUNA respuesta hasta que llegue un mensaje con
        # intencion clara. "¡De nada!" es un cierre. Ver test_despedida.py.
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="gracias", wa_msg_id="wamid.prev1")
        Message.objects.create(conversation=conv, role="assistant", content="¡De nada!")
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "👍", "wamid.t2")
            mock_graph.assert_not_called()
        self.mock_wa.return_value.send_text.assert_not_called()

    def test_segundo_acuse_con_la_conversacion_abierta_manda_emoji_fijo(self):
        # Con una pregunta abierta la conversacion NO esta cerrada, asi que
        # sigue valiendo el corte por racha: el 2do acuse consecutivo responde
        # "👍" sin pasar por el LLM.
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="gracias", wa_msg_id="wamid.prev1")
        Message.objects.create(conversation=conv, role="assistant", content="¿Te ayudo con algo mas?")
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "👍", "wamid.t2")
            mock_graph.assert_not_called()
        self.mock_wa.return_value.send_text.assert_called_once_with(
            "56911112222", "👍", reply_to="wamid.t2",
        )

    def test_tercer_acuse_trivial_consecutivo_no_llama_al_grafo_ni_manda_nada(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="gracias", wa_msg_id="wamid.prev1")
        Message.objects.create(conversation=conv, role="assistant", content="¡De nada!")
        Message.objects.create(conversation=conv, role="user", content="👍", wa_msg_id="wamid.prev2")
        Message.objects.create(conversation=conv, role="assistant", content="👍")
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "", "wamid.t3")
            mock_graph.assert_not_called()
        self.mock_wa.return_value.send_text.assert_not_called()

    def test_acuse_trivial_tras_mensaje_sustancial_no_hereda_la_racha(self):
        # La racha no se hereda: el mensaje sustancial la corta. Con el bot
        # dejando una pregunta abierta, el acuse siguiente llega al grafo.
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="cuanto cuesta el Koleos")
        Message.objects.create(
            conversation=conv, role="assistant",
            content="Desde $27.990.000. ¿Quieres que te simule el financiamiento?")
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "ok gracias", "wamid.t4")
            mock_graph.assert_called_once()

    def test_acuse_tras_una_respuesta_sin_pregunta_no_manda_nada(self):
        # Cambio de comportamiento del 2026-09-03: si el bot informo y no
        # pregunto nada, un "ok gracias" cierra el intercambio en silencio en
        # vez de gatillar otra respuesta. Es la regla del usuario aplicada de
        # forma consistente, no solo a las despedidas explicitas.
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="cuanto cuesta el Koleos")
        Message.objects.create(conversation=conv, role="assistant", content="Desde $27.990.000")
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "ok gracias", "wamid.t6")
            mock_graph.assert_not_called()
        self.mock_wa.return_value.send_text.assert_not_called()

    def test_racha_de_sesion_anterior_no_cuenta_para_una_sesion_nueva(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        vieja = Message.objects.create(conversation=conv, role="user", content="gracias", wa_msg_id="wamid.old")
        Message.objects.filter(pk=vieja.pk).update(created_at=timezone.now() - timedelta(hours=20))
        with patch("bot.whatsapp.handlers._run_graph") as mock_graph:
            async_to_sync(handle_message)("56911112222", "Juan", "gracias", "wamid.t5")
            mock_graph.assert_called_once()


class EsMensajeTrivialTest(TestCase):
    def test_vacio_es_trivial(self):
        self.assertTrue(_es_mensaje_trivial(""))
        self.assertTrue(_es_mensaje_trivial("   "))

    def test_solo_emoji_es_trivial(self):
        self.assertTrue(_es_mensaje_trivial("👍🏻"))
        self.assertTrue(_es_mensaje_trivial("😊😊"))

    def test_acuse_corto_conocido_es_trivial(self):
        for texto in ["ok", "Ok", "gracias", "Gracias!", "dale", "listo", "bien", "perfecto"]:
            self.assertTrue(_es_mensaje_trivial(texto), texto)

    def test_mensaje_con_contenido_real_no_es_trivial(self):
        self.assertFalse(_es_mensaje_trivial("ok pero tengo otra pregunta"))
        self.assertFalse(_es_mensaje_trivial("necesito agendar hora"))
        self.assertFalse(_es_mensaje_trivial("cuanto cuesta el Koleos"))


class SaltosEscapadosTest(TestCase):
    """Saltos de linea que el LLM escapa dos veces.

    El contrato del especialista es un JSON y el modelo a veces escribe "\\\\n"
    en vez de "\\n": json.loads hace lo correcto y devuelve una barra invertida
    y una "n" LITERALES. Al contacto le llego "...queda asi 💰\\\\n- Pie:
    $9.945.000" con el escape visible (conversacion del vendedor, 2026-09-02).
    """

    def test_convierte_el_salto_escapado_en_uno_real(self):
        from bot.whatsapp.handlers import _a_formato_whatsapp
        self.assertEqual(
            _a_formato_whatsapp("Pie: $9.945.000\\n24 cuotas"),
            "Pie: $9.945.000\n24 cuotas")

    def test_cubre_las_variantes_de_windows(self):
        from bot.whatsapp.handlers import _a_formato_whatsapp
        self.assertEqual(_a_formato_whatsapp("a\\r\\nb"), "a\nb")
        self.assertEqual(_a_formato_whatsapp("a\\rb"), "a\nb")

    def test_devuelve_el_partido_en_varios_mensajes(self):
        # Efecto lateral del bug: _dividir_en_mensajes corta por linea en
        # blanco REAL, asi que con "\\n" literal el turno llegaba como un
        # unico mensaje largo mientras el resto llegaban en 3-4.
        from bot.whatsapp.handlers import _a_formato_whatsapp, _dividir_en_mensajes
        crudo = "Opciones:\\n\\n*1. Subaru* $19.890.000\\n\\n*2. Tucson* $19.990.000"
        self.assertEqual(len(_dividir_en_mensajes(crudo)), 1)
        self.assertGreater(len(_dividir_en_mensajes(_a_formato_whatsapp(crudo))), 1)

    def test_no_toca_un_salto_de_linea_real(self):
        from bot.whatsapp.handlers import _a_formato_whatsapp
        self.assertEqual(_a_formato_whatsapp("Precio\nKm"), "Precio\nKm")


class InvisiblesTest(TestCase):
    """El LLM mete caracteres invisibles en su salida.

    Contados en una conversacion real del 2026-09-02: 13 U+200B, incluso
    DENTRO de precios ("\u200b15.990.000"). No se ven en WhatsApp pero ensucian
    el texto si el ejecutivo lo copia al CRM o si alguien lo busca.
    """

    def test_saca_el_zero_width_space_de_un_precio(self):
        from bot.whatsapp.handlers import _a_formato_whatsapp
        self.assertEqual(
            _a_formato_whatsapp("La m\u00e1s eficiente:\u200b 15.990.000"),
            "La m\u00e1s eficiente: 15.990.000")

    def test_saca_las_otras_variantes_invisibles(self):
        from bot.whatsapp.handlers import _a_formato_whatsapp
        for invisible in ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"):
            with self.subTest(char=hex(ord(invisible))):
                self.assertEqual(_a_formato_whatsapp(f"Cre{invisible}ta"), "Creta")

    def test_no_toca_el_espacio_duro(self):
        # U+00A0 SI es un espacio visible: sacarlo pegaria las palabras.
        from bot.whatsapp.handlers import _a_formato_whatsapp
        self.assertEqual(_a_formato_whatsapp("Precio\u00a0final"), "Precio\u00a0final")

    def test_sigue_convirtiendo_la_negrita(self):
        # La limpieza no puede romper lo que la funcion ya hacia.
        from bot.whatsapp.handlers import _a_formato_whatsapp
        self.assertEqual(_a_formato_whatsapp("**Creta** 2023"), "*Creta* 2023")

    def test_limpia_y_convierte_en_el_mismo_texto(self):
        from bot.whatsapp.handlers import _a_formato_whatsapp
        self.assertEqual(
            _a_formato_whatsapp("**Chery Tiggo (\u200b2023)** \u2014 16.490.000"),
            "*Chery Tiggo (2023)* \u2014 16.490.000")


class AFormatoWhatsappTest(TestCase):
    def test_convierte_doble_asterisco_a_asterisco_simple(self):
        # Bug real detectado en produccion (wsp_demo, renault.cl): el LLM
        # usa negrita estilo Markdown (**texto**) por defecto, pero
        # WhatsApp renderiza negrita con UN solo asterisco (*texto*) --
        # doble asterisco le llega al contacto como texto literal con
        # asteriscos, sin negrita.
        self.assertEqual(_a_formato_whatsapp("El **Koleos** es genial"), "El *Koleos* es genial")

    def test_convierte_varios_tramos_en_negrita_en_el_mismo_mensaje(self):
        self.assertEqual(
            _a_formato_whatsapp("El **Koleos** cuesta **$27.990.000**"),
            "El *Koleos* cuesta *$27.990.000*",
        )

    def test_texto_sin_negrita_no_cambia(self):
        self.assertEqual(_a_formato_whatsapp("Hola, ¿en qué le puedo ayudar?"), "Hola, ¿en qué le puedo ayudar?")


class DividirEnMensajesTest(TestCase):
    def test_texto_corto_no_se_divide(self):
        # Feedback real del usuario (wsp_demo, renault.cl): el bot mandaba
        # bloques de 3-4 parrafos como un solo mensaje de WhatsApp -- muy
        # largo comparado a como escribe una persona real por WhatsApp.
        texto = "Hola, ¿en qué le puedo ayudar?"
        self.assertEqual(_dividir_en_mensajes(texto), [texto])

    def test_parrafos_separados_por_linea_en_blanco_se_dividen_en_mensajes_distintos(self):
        texto = "Primer párrafo, corto.\n\nSegundo párrafo, también corto."
        self.assertEqual(
            _dividir_en_mensajes(texto),
            ["Primer párrafo, corto.", "Segundo párrafo, también corto."],
        )

    def test_parrafos_vacios_por_saltos_de_linea_extra_se_descartan(self):
        texto = "Primero.\n\n\n\nSegundo."
        self.assertEqual(_dividir_en_mensajes(texto), ["Primero.", "Segundo."])

    def test_parrafo_unico_muy_largo_se_divide_por_oraciones_sin_pasar_el_limite(self):
        # Reproduce el caso real: un parrafo unico de varias oraciones,
        # mas largo que _LARGO_MAXIMO_MENSAJE, sin ningun "\n\n" -- se
        # divide por oraciones completas (nunca a mitad de una), cada
        # mensaje resultante dentro del limite.
        oracion = "Esta es una oracion de relleno para hacer el parrafo bastante largo."
        texto = " ".join([oracion] * 10)
        partes = _dividir_en_mensajes(texto)
        self.assertGreater(len(partes), 1)
        for parte in partes:
            self.assertLessEqual(len(parte), 600)
        # ninguna oracion se corto a la mitad
        self.assertEqual(" ".join(partes), texto)

    def test_no_divide_una_oracion_a_la_mitad_aunque_una_sola_exceda_el_limite(self):
        oracion_larga = "x" * 800 + "."
        partes = _dividir_en_mensajes(oracion_larga)
        self.assertEqual(partes, [oracion_larga])


class RunGraphModeloImagenTest(TestCase):
    def setUp(self):
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client")
        self.mock_wa = self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)
        self.mock_wa.return_value.send_text.return_value = True

        self.graph_patch = patch("bot.whatsapp.handlers.get_flow_graph")
        self.mock_get_graph = self.graph_patch.start()
        self.addCleanup(self.graph_patch.stop)

    def _set_graph_result(self, result):
        self.mock_get_graph.return_value.ainvoke = AsyncMock(return_value=result)

    def test_con_modelo_imagen_y_resolucion_exitosa_manda_send_image(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "te muestro el Koleos", "modelo_imagen": "koleos",
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo", return_value="https://x/koleos.jpg"):
            async_to_sync(_run_graph)(conv, "ofreceme el koleos", "wamid.10", envio_inline=True)
        self.mock_wa.return_value.send_text.assert_called_once()
        self.mock_wa.return_value.send_image.assert_called_once_with("56911112222", "https://x/koleos.jpg")

    def test_sin_modelo_imagen_no_llama_a_resolver_ni_a_send_image(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "hola", "modelo_imagen": None,
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo") as mock_resolver:
            async_to_sync(_run_graph)(conv, "hola", "wamid.11", envio_inline=True)
        mock_resolver.assert_not_called()
        self.mock_wa.return_value.send_image.assert_not_called()

    def test_resolver_sin_imagen_disponible_no_llama_a_send_image(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "te cuento del Koleos", "modelo_imagen": "koleos",
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo", return_value=None):
            async_to_sync(_run_graph)(conv, "koleos?", "wamid.12", envio_inline=True)
        self.mock_wa.return_value.send_image.assert_not_called()

    def test_fallo_al_resolver_la_imagen_no_impide_que_el_texto_ya_se_haya_enviado(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "te muestro el Koleos", "modelo_imagen": "koleos",
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo", side_effect=Exception("boom")):
            async_to_sync(_run_graph)(conv, "koleos?", "wamid.13", envio_inline=True)
        self.mock_wa.return_value.send_text.assert_called_once()
        self.mock_wa.return_value.send_image.assert_not_called()

    def test_imagen_ya_enviada_hace_poco_no_se_reenvia(self):
        # Feedback real del usuario (wsp_demo, renault.cl): la foto del
        # modelo se estaba mandando demasiadas veces dentro de una misma
        # conversacion activa.
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="assistant", content="[imagen_enviada:koleos]")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "aca esta de nuevo el Koleos", "modelo_imagen": "koleos",
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo") as mock_resolver:
            async_to_sync(_run_graph)(conv, "mandame la foto de nuevo", "wamid.20", envio_inline=True)
        mock_resolver.assert_not_called()
        self.mock_wa.return_value.send_image.assert_not_called()

    def test_imagen_de_otro_modelo_si_se_envia_aunque_haya_una_reciente(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="assistant", content="[imagen_enviada:koleos]")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "te muestro el Duster", "modelo_imagen": "duster",
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo", return_value="https://x/duster.jpg"):
            async_to_sync(_run_graph)(conv, "y el duster?", "wamid.21", envio_inline=True)
        self.mock_wa.return_value.send_image.assert_called_once_with("56911112222", "https://x/duster.jpg")

    def test_imagen_no_se_reenvia_aunque_pasen_muchos_mensajes_triviales_en_la_misma_sesion(self):
        # Antes de este fix la ventana era un conteo fijo de 20 mensajes --
        # suficientes acuses de recibo triviales (emoji, "ok") en el medio
        # hacian que la imagen se reenviara igual dentro de la MISMA sesion
        # activa. Ahora el corte es por borde de sesion (mismo criterio que
        # build_context_window), no por conteo. Hallazgo real, ver
        # docs/PENDIENTES.md.
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="assistant", content="[imagen_enviada:koleos]")
        for i in range(30):
            Message.objects.create(conversation=conv, role="user", content=f"msg{i}")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "aca esta el Koleos otra vez", "modelo_imagen": "koleos",
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo") as mock_resolver:
            async_to_sync(_run_graph)(conv, "mandame la foto de nuevo", "wamid.22", envio_inline=True)
        mock_resolver.assert_not_called()
        self.mock_wa.return_value.send_image.assert_not_called()

    def test_imagen_si_se_reenvia_en_una_sesion_nueva_tras_el_gap(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        marcador = Message.objects.create(conversation=conv, role="assistant", content="[imagen_enviada:koleos]")
        Message.objects.filter(pk=marcador.pk).update(created_at=timezone.now() - timedelta(hours=13))
        Message.objects.create(conversation=conv, role="user", content="hola de nuevo")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "aca esta el Koleos otra vez", "modelo_imagen": "koleos",
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo", return_value="https://x/koleos.jpg"):
            async_to_sync(_run_graph)(conv, "mandame la foto de nuevo", "wamid.24", envio_inline=True)
        self.mock_wa.return_value.send_image.assert_called_once_with("56911112222", "https://x/koleos.jpg")

    def test_al_enviar_una_imagen_se_guarda_el_marcador_para_futura_deduplicacion(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "te muestro el Koleos", "modelo_imagen": "koleos",
        })
        with patch("bot.scraping.imagenes.resolver_imagen_modelo", return_value="https://x/koleos.jpg"):
            async_to_sync(_run_graph)(conv, "ofreceme el koleos", "wamid.23", envio_inline=True)
        marcador = conv.messages.filter(content="[imagen_enviada:koleos]").first()
        self.assertIsNotNone(marcador)

    def test_response_text_con_doble_asterisco_se_envia_y_guarda_ya_convertido(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "El **Koleos** parte desde **$27.990.000**", "modelo_imagen": None,
        })
        async_to_sync(_run_graph)(conv, "precio del koleos?", "wamid.14", envio_inline=True)

        self.mock_wa.return_value.send_text.assert_called_once_with(
            "56911112222", "El *Koleos* parte desde *$27.990.000*", reply_to="wamid.14",
        )
        mensaje_guardado = conv.messages.filter(role="assistant").first()
        self.assertEqual(mensaje_guardado.content, "El *Koleos* parte desde *$27.990.000*")

    def test_response_text_con_varios_parrafos_se_manda_en_varios_send_text(self):
        # Feedback real del usuario (wsp_demo, renault.cl): el bot mandaba
        # respuestas de 3-4 parrafos como un solo mensaje de WhatsApp --
        # se dividen en un send_text por parrafo, cada uno guardado como
        # su propio Message (asi el historial refleja lo que en verdad se
        # mando, mensaje por mensaje).
        conv = Conversation.objects.create(wa_id="56911112222")
        self._set_graph_result({
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "Primer párrafo.\n\nSegundo párrafo.\n\nTercer párrafo.",
            "modelo_imagen": None,
        })
        async_to_sync(_run_graph)(conv, "cuéntame más", "wamid.15", envio_inline=True)

        self.assertEqual(self.mock_wa.return_value.send_text.call_count, 3)
        llamadas = [c.args[1] for c in self.mock_wa.return_value.send_text.call_args_list]
        self.assertEqual(llamadas, ["Primer párrafo.", "Segundo párrafo.", "Tercer párrafo."])
        # solo el primer mensaje va como reply_to del mensaje original
        primer_llamada = self.mock_wa.return_value.send_text.call_args_list[0]
        self.assertEqual(primer_llamada.kwargs.get("reply_to"), "wamid.15")
        mensajes_guardados = list(conv.messages.filter(role="assistant").order_by("id").values_list("content", flat=True))
        self.assertEqual(mensajes_guardados, ["Primer párrafo.", "Segundo párrafo.", "Tercer párrafo."])


class RunGraphRecursionErrorTest(TestCase):
    def setUp(self):
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client")
        self.mock_wa = self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)
        self.mock_wa.return_value.send_text.return_value = True

        self.graph_patch = patch("bot.whatsapp.handlers.get_flow_graph")
        self.mock_get_graph = self.graph_patch.start()
        self.addCleanup(self.graph_patch.stop)

    def test_pasa_recursion_limit_explicito_al_invocar_el_grafo(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        mock_ainvoke = AsyncMock(return_value={
            "active_agent": "ventas", "flow_state": "IDLE", "flow_data": {},
            "response_text": "hola", "modelo_imagen": None,
        })
        self.mock_get_graph.return_value.ainvoke = mock_ainvoke
        async_to_sync(_run_graph)(conv, "hola", "wamid.30", envio_inline=True)
        _, kwargs = mock_ainvoke.call_args
        self.assertEqual(kwargs.get("config"), {"recursion_limit": 25})

    def test_graph_recursion_error_responde_fallback_generico_sin_crashear(self):
        # Antes de este fix, GraphRecursionError suba sin atrapar hasta el
        # webhook (500, sin ningun mensaje al contacto) -- un loop
        # patologico entre specialist/business_action dejaba al cliente sin
        # respuesta.
        from langgraph.errors import GraphRecursionError
        from bot.models import Incident
        conv = Conversation.objects.create(wa_id="56911112222")
        self.mock_get_graph.return_value.ainvoke = AsyncMock(side_effect=GraphRecursionError("boom"))

        async_to_sync(_run_graph)(conv, "hola", "wamid.31", envio_inline=True)

        self.mock_wa.return_value.send_text.assert_called_once()
        texto_enviado = self.mock_wa.return_value.send_text.call_args.args[1]
        self.assertIn("problema", texto_enviado.lower())
        mensaje_guardado = conv.messages.filter(role="assistant").first()
        self.assertEqual(mensaje_guardado.content, texto_enviado)
        self.assertTrue(Incident.objects.filter(conversation=conv, kind="revision_requerida").exists())

    def test_graph_recursion_error_no_toca_flow_state_ni_active_agent(self):
        from langgraph.errors import GraphRecursionError
        conv = Conversation.objects.create(wa_id="56911112222", active_agent="ventas", flow_state="ALGO")
        self.mock_get_graph.return_value.ainvoke = AsyncMock(side_effect=GraphRecursionError("boom"))
        async_to_sync(_run_graph)(conv, "hola", "wamid.32", envio_inline=True)
        conv.refresh_from_db()
        self.assertEqual(conv.active_agent, "ventas")
        self.assertEqual(conv.flow_state, "ALGO")


class OnGraphResultCallbackTest(TestCase):
    def setUp(self):
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client")
        self.mock_wa = self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)

    def test_on_graph_result_recibe_el_result_crudo_del_grafo(self):
        Conversation.objects.create(wa_id="56911112222", flow_state="ESPERANDO_CONSULTA")
        capturados = []
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_get_graph.return_value.ainvoke = AsyncMock(
                return_value={"response_text": "hola", "active_agent": "faq", "intent": "explorar"}
            )
            async_to_sync(handle_message)(
                "56911112222", "Juan", "hola que tal", "wamid.100",
                on_graph_result=capturados.append,
            )
        self.assertEqual(capturados, [{"response_text": "hola", "active_agent": "faq", "intent": "explorar"}])

    def test_sin_on_graph_result_no_rompe_nada(self):
        Conversation.objects.create(wa_id="56911112222", flow_state="ESPERANDO_CONSULTA")
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_get_graph.return_value.ainvoke = AsyncMock(
                return_value={"response_text": "hola", "active_agent": "faq"}
            )
            async_to_sync(handle_message)("56911112222", "Juan", "hola que tal", "wamid.101")
        self.mock_wa.return_value.send_text.assert_called_once()


class PersistenciaCamposNuevosTest(TestCase):
    def setUp(self):
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client")
        self.mock_wa = self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)

    def test_lead_class_y_stage_quedan_en_la_conversacion(self):
        conv = Conversation.objects.create(wa_id="56911112222", flow_state="IDLE")
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_graph = mock_get_graph.return_value
            async def fake_ainvoke(payload, **kwargs):
                return {
                    "response_text": "listo, te cotizo", "active_agent": "ventas",
                    "flow_data": {}, "flow_state": "IDLE", "modelo_imagen": None, "intent": "cotizar",
                    "lead_class": "HOT", "stage": "cotizacion",
                    "handoff_reason": None, "requiere_revision": False, "motivo_revision": None,
                }
            mock_graph.ainvoke = fake_ainvoke
            async_to_sync(handle_message)("56911112222", "Juan", "quiero cotizar un Koleos", "wamid.10")
        conv.refresh_from_db()
        self.assertEqual(conv.lead_class, "HOT")
        self.assertEqual(conv.stage, "cotizacion")

    def test_rut_se_promueve_desde_extracted_data(self):
        from bot.models import Incident
        conv = Conversation.objects.create(wa_id="56911112223")
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_graph = mock_get_graph.return_value
            async def fake_ainvoke(payload, **kwargs):
                return {
                    "response_text": "gracias", "active_agent": None,
                    "flow_data": {"rut": "11.111.111-1"}, "flow_state": "IDLE",
                    "modelo_imagen": None, "intent": None,
                    "lead_class": None, "stage": None,
                    "handoff_reason": None, "requiere_revision": False, "motivo_revision": None,
                }
            mock_graph.ainvoke = fake_ainvoke
            async_to_sync(handle_message)("56911112223", "Juan", "mi rut es 11.111.111-1", "wamid.11")
        conv.refresh_from_db()
        self.assertEqual(conv.rut, "11.111.111-1")

    def test_handoff_reason_crea_incident(self):
        from bot.models import Incident
        conv = Conversation.objects.create(wa_id="56911112224")
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_graph = mock_get_graph.return_value
            async def fake_ainvoke(payload, **kwargs):
                return {
                    "response_text": "te derivo con un ejecutivo", "active_agent": None,
                    "flow_data": {}, "flow_state": "IDLE", "modelo_imagen": None, "intent": None,
                    "lead_class": None, "stage": None,
                    "handoff_reason": "pide firmar contrato", "requiere_revision": False, "motivo_revision": None,
                }
            mock_graph.ainvoke = fake_ainvoke
            async_to_sync(handle_message)("56911112224", "Juan", "quiero firmar ya", "wamid.12")
        inc = Incident.objects.filter(conversation=conv, kind="handoff").first()
        self.assertIsNotNone(inc)
        self.assertEqual(inc.context["reason"], "pide firmar contrato")

    def test_handoff_incident_registra_el_specialist_previo_aunque_el_grafo_lo_limpie(self):
        # specialist_node (Task 4) siempre devuelve active_agent=None en la
        # rama de handoff -- deliberadamente limpia el ruteo al derivar a un
        # humano. El unico lugar donde sigue existiendo el especialista que
        # estaba activo ANTES del handoff es conv.active_agent previo al
        # turno -- si el fallback usa conv.active_agent DESPUES de que ya
        # se reasigno a partir de result.get("active_agent"), el campo
        # queda siempre vacio.
        from bot.models import Incident
        conv = Conversation.objects.create(wa_id="56911112226", active_agent="ventas")
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_graph = mock_get_graph.return_value
            async def fake_ainvoke(payload, **kwargs):
                return {
                    "response_text": "te derivo con un ejecutivo", "active_agent": None,
                    "flow_data": {}, "flow_state": "IDLE", "modelo_imagen": None, "intent": None,
                    "lead_class": None, "stage": None,
                    "handoff_reason": "pide firmar contrato", "requiere_revision": False, "motivo_revision": None,
                }
            mock_graph.ainvoke = fake_ainvoke
            async_to_sync(handle_message)("56911112226", "Juan", "quiero firmar ya", "wamid.14")
        inc = Incident.objects.filter(conversation=conv, kind="handoff").first()
        self.assertIsNotNone(inc)
        self.assertEqual(inc.context["specialist"], "ventas")

    def test_requiere_revision_crea_incident(self):
        from bot.models import Incident
        conv = Conversation.objects.create(wa_id="56911112225")
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_graph = mock_get_graph.return_value
            async def fake_ainvoke(payload, **kwargs):
                return {
                    "response_text": "entendido", "active_agent": "ventas",
                    "flow_data": {}, "flow_state": "IDLE", "modelo_imagen": None, "intent": None,
                    "lead_class": None, "stage": None,
                    "handoff_reason": None, "requiere_revision": True, "motivo_revision": "riesgo de seguridad",
                }
            mock_graph.ainvoke = fake_ainvoke
            async_to_sync(handle_message)("56911112225", "Juan", "el auto se prendió fuego", "wamid.13")
        inc = Incident.objects.filter(conversation=conv, kind="revision_requerida").first()
        self.assertIsNotNone(inc)
        self.assertEqual(inc.context["motivo"], "riesgo de seguridad")


class HandleUnsupportedMediaTest(TestCase):
    def setUp(self):
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client")
        self.mock_wa = self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)

    def test_video_no_soportado_responde_generico_y_guarda_historial(self):
        from bot.whatsapp.handlers import handle_unsupported_media
        async_to_sync(handle_unsupported_media)("56911112222", "Juan", "wamid.v1", "video")
        conv = Conversation.objects.get(wa_id="56911112222")
        mensajes = list(conv.messages.order_by("created_at"))
        self.assertEqual(len(mensajes), 2)
        self.assertEqual(mensajes[0].role, "user")
        self.assertEqual(mensajes[1].role, "assistant")
        self.mock_wa.return_value.send_text.assert_called_once()
        texto_enviado = self.mock_wa.return_value.send_text.call_args[0][1]
        self.assertIn("videos", texto_enviado)

    def test_mensaje_duplicado_por_wa_msg_id_se_ignora(self):
        from bot.whatsapp.handlers import handle_unsupported_media
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="x", wa_msg_id="wamid.v2")
        async_to_sync(handle_unsupported_media)("56911112222", "Juan", "wamid.v2", "video")
        self.assertEqual(conv.messages.count(), 1)
        self.mock_wa.return_value.send_text.assert_not_called()

    def test_mensaje_duplicado_por_carrera_en_el_constraint_se_ignora(self):
        from bot.whatsapp.handlers import handle_unsupported_media
        conv = Conversation.objects.create(wa_id="56911112222")
        Message.objects.create(conversation=conv, role="user", content="x", wa_msg_id="wamid.v3")
        with patch("bot.whatsapp.handlers.Message.objects.filter") as mock_filter:
            mock_filter.return_value.aexists = AsyncMock(return_value=False)
            async_to_sync(handle_unsupported_media)("56911112222", "Juan", "wamid.v3", "video")
        self.assertEqual(conv.messages.count(), 1)
        self.mock_wa.return_value.send_text.assert_not_called()

    def test_tipo_desconocido_usa_mensaje_default(self):
        from bot.whatsapp.handlers import handle_unsupported_media
        async_to_sync(handle_unsupported_media)("56911112222", "Juan", "wamid.v3", "tipo_raro")
        self.mock_wa.return_value.send_text.assert_called_once()
        texto_enviado = self.mock_wa.return_value.send_text.call_args[0][1]
        self.assertIn("no puedo procesar ese tipo de archivo", texto_enviado)

    def test_marca_como_leido(self):
        from bot.whatsapp.handlers import handle_unsupported_media
        async_to_sync(handle_unsupported_media)("56911112222", "Juan", "wamid.v4", "image")
        # mostrar_escribiendo=False explicito: el acuse ahora sale por
        # encolar_acuse (bot/whatsapp/cola_envio.py), que siempre pasa el kwarg.
        # De paso la asercion queda mas fuerte que antes -- fija que en este
        # camino NO se pide el indicador de "escribiendo...", que seria mentira:
        # aca no viene ninguna respuesta del LLM detras, solo el fallback fijo.
        self.mock_wa.return_value.mark_as_read.assert_called_once_with(
            "wamid.v4", mostrar_escribiendo=False)
