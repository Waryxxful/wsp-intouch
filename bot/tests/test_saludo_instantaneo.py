"""Cobertura del saludo que se responde sin LLM, antes del grafo.

Existe porque el mecanismo ya estaba en el código pero interceptaba 9 de 22
saludos reales (41%): "hola buenas" -- el saludo del primer test real por
WhatsApp -- caía al LLM y costaba 11,2s. Y cuando sí interceptaba, respondía
con "¿En qué le puedo ayudar?", tratando de USTED, en contra de la regla de
tuteo del prompt global. Ver docs/PENDIENTES.md #15.
"""
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase

from bot.whatsapp.handlers import (
    WELCOME_IDENTIDAD, WELCOME_INVITACION, _partir_saludo, _quitar_saludo_inicial,
    _texto_bienvenida,
)


class PartirSaludoTest(TestCase):
    SALUDOS_PUROS = [
        "hola", "Hola", "hola!", "holaa", "holaaa", "hola buenas", "buenas",
        "Buenas!", "buenas tardes", "buenos dias", "buenos días", "buen dia",
        "hola buenas tardes", "hola, como estas?", "que tal", "hola?",
        "Hola buenas noches", "buenas noches", "hey", "alo", "hola 👋",
        "hola muy buenas", "Buenos días!", "holis", "hola que tal",
    ]

    def test_intercepta_los_saludos_puros_reales(self):
        for texto in self.SALUDOS_PUROS:
            with self.subTest(texto=texto):
                # resto vacío = la bienvenida es toda la respuesta, el grafo
                # no corre y el turno cuesta un POST a Meta.
                self.assertEqual(_partir_saludo(texto), (True, ""))

    def test_separa_el_saludo_del_contenido_real(self):
        # El saludo sale al instante y el grafo sigue con el resto en el mismo
        # request (el webhook es sincrónico, no hace falta background).
        casos = [
            ("hola, busco un suv usado", "busco un suv usado"),
            ("hola quiero cotizar una suv", "quiero cotizar una suv"),
            ("buenas, cuanto cuesta la tucson", "cuanto cuesta la tucson"),
            ("buenas tardes tienen autos automaticos", "tienen autos automaticos"),
        ]
        for texto, resto_esperado in casos:
            with self.subTest(texto=texto):
                self.assertEqual(_partir_saludo(texto), (True, resto_esperado))

    def test_no_intercepta_un_mensaje_sin_saludo(self):
        for texto in ["quiero un auto", "cuanto cuesta la tucson", "ok", "gracias"]:
            with self.subTest(texto=texto):
                self.assertEqual(_partir_saludo(texto), (False, texto))

    def test_relleno_de_cortesia_solo_no_es_saludo(self):
        # "muy" y "gracias" están en el relleno que se ignora, pero solos no
        # deben interceptar nada: hace falta al menos una palabra de saludo.
        self.assertEqual(_partir_saludo("muy gracias")[0], False)

    def test_mensaje_vacio_no_es_saludo(self):
        self.assertEqual(_partir_saludo(""), (False, ""))
        self.assertEqual(_partir_saludo(None), (False, ""))

    def test_solo_emoji_no_cuenta_como_saludo(self):
        # Un emoji suelto lo maneja el corte de mensajes triviales, no este.
        self.assertEqual(_partir_saludo("👋")[0], False)


class TextoBienvenidaTest(TestCase):
    def test_usa_el_nombre_de_pila_del_perfil(self):
        texto = _texto_bienvenida(WELCOME_IDENTIDAD, "tomas valenzuela")
        self.assertIn("¡Hola Tomas!", texto)

    def test_sin_nombre_de_perfil_no_deja_hueco(self):
        texto = _texto_bienvenida(WELCOME_IDENTIDAD, "")
        self.assertIn("¡Hola!", texto)
        self.assertNotIn("{nombre}", texto)

    def test_la_invitacion_respeta_el_tuteo_del_prompt_global(self):
        # La regla de TONO del prompt global es explícita: trato de tú, nunca
        # usted. El texto anterior ("¿En qué le puedo ayudar?") la violaba.
        self.assertIn("te puedo ayudar", WELCOME_INVITACION.lower())
        self.assertNotIn("le puedo ayudar", WELCOME_INVITACION.lower())

    def test_la_identidad_se_presenta_como_auto_ia(self):
        # IDENTIDAD del prompt global: el nombre es "Auto IA" en todos los
        # especialistas, y declara que es un asistente virtual.
        texto = _texto_bienvenida(WELCOME_IDENTIDAD, "Tomas")
        self.assertIn("Auto IA", texto)
        self.assertIn("virtual", texto)

    def test_la_identidad_no_incluye_la_invitacion(self):
        # Son piezas separadas a propósito: cuando viene una respuesta real
        # detrás, preguntar "¿en qué te puedo ayudar?" es redundante con lo que
        # el contacto acaba de pedir (reportado por el usuario 2026-09-02).
        self.assertNotIn("te puedo ayudar", WELCOME_IDENTIDAD.lower())


class QuitarSaludoInicialTest(TestCase):
    """La respuesta del especialista no debe volver a saludar en el mismo turno.

    Caso reportado por el usuario con captura: la bienvenida salía al instante
    y acto seguido el especialista abría con "¡Buenas Tomas!". Guardar la
    bienvenida en el historial NO alcanza -- se probó y el LLM saluda igual --,
    así que se corta en código, mismo criterio que el loop de despedidas.
    """

    CASO_REAL = (
        "¡Buenas Tomas! Perfecto, una camioneta puede servir para varias cosas, "
        "así que cuéntame un poco más: ¿la usarías más para trabajo/carga?"
    )

    def test_corta_el_saludo_del_caso_real_reportado(self):
        salida = _quitar_saludo_inicial(self.CASO_REAL, "tomas valenzuela")
        self.assertTrue(salida.startswith("Perfecto, una camioneta"))
        self.assertNotIn("¡Buenas Tomas!", salida)

    def test_corta_variantes_de_apertura(self):
        casos = [
            ("¡Hola! ¿En qué te ayudo?", "¿En qué te ayudo?"),
            ("¡Hola Tomas! Te muestro 3 opciones.", "Te muestro 3 opciones."),
            ("Buenas tardes Tomas. La garantia es de 6 meses.", "La garantia es de 6 meses."),
        ]
        for entrada, esperado in casos:
            with self.subTest(entrada=entrada):
                self.assertEqual(_quitar_saludo_inicial(entrada, "Tomas"), esperado)

    def test_no_toca_una_apertura_que_no_es_saludo(self):
        # "Perfecto" es un acuse, no un saludo: cortarlo mutilaría la respuesta.
        for texto in [
            "Perfecto, una camioneta puede servir.",
            "Con 20 millones tienes buenas opciones.",
            "Tomas, con 20 millones tienes opciones.",
        ]:
            with self.subTest(texto=texto):
                self.assertEqual(_quitar_saludo_inicial(texto, "Tomas"), texto)

    def test_nunca_devuelve_vacio(self):
        # Si la respuesta entera era un saludo, mejor mandarla que mandar nada.
        self.assertEqual(_quitar_saludo_inicial("¡Hola!", "Tomas"), "¡Hola!")
        self.assertEqual(_quitar_saludo_inicial("", "Tomas"), "")

    def test_solo_se_aplica_cuando_ya_saludamos(self):
        # El flag lo pasa handle_message; sin él la respuesta va intacta.
        import inspect
        from bot.whatsapp import handlers
        firma = inspect.signature(handlers._run_graph)
        self.assertFalse(firma.parameters["ya_saludamos"].default)

    def test_una_plantilla_editada_con_llaves_sueltas_no_revienta(self):
        # El texto se edita desde el panel: unas llaves de más no deben tirar
        # abajo la respuesta (por eso .replace() y no .format()).
        texto = _texto_bienvenida("¡Hola{nombre}! {ojo} {}", "Tomas")
        self.assertEqual(texto, "¡Hola Tomas! {ojo} {}")


class IndicadorEscribiendoTest(TestCase):
    def test_el_acuse_de_lectura_pide_el_indicador(self):
        # Va en la MISMA petición que el acuse (así lo define la Cloud API), o
        # sea sin latencia extra. Si alguien lo separa en otra request, esto
        # se cae.
        from bot.whatsapp.client import WhatsAppCloudClient

        with patch("bot.whatsapp.client._http.post") as mock_post:
            WhatsAppCloudClient().mark_as_read("wamid.X", mostrar_escribiendo=True)
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["status"], "read")
        self.assertEqual(body["message_id"], "wamid.X")
        self.assertEqual(body["typing_indicator"], {"type": "text"})

    def test_sin_pedirlo_el_acuse_queda_como_antes(self):
        from bot.whatsapp.client import WhatsAppCloudClient

        with patch("bot.whatsapp.client._http.post") as mock_post:
            WhatsAppCloudClient().mark_as_read("wamid.X")
        self.assertNotIn("typing_indicator", mock_post.call_args.kwargs["json"])


class IndicadorSoloCuandoSeHaraEsperarTest(TransactionTestCase):
    """El indicador no debe pedirse cuando el bot no va a responder.

    TransactionTestCase y no TestCase a proposito: handle_message lee la BD via
    sync_to_async, o sea desde otro thread, y con TestCase (transaccion sin
    commitear) sqlite en modo shared-cache bloquea esa lectura -- mismo motivo
    que GraphGetLlmOverrideTest en bot/tests/test_graph.py.

    Defecto encontrado por el usuario al ver el indicador en vivo: se pedía
    incondicionalmente, así que en modo humano y en el silencio por racha de
    acuses el contacto veía al bot "escribiendo" durante los 25 segundos que
    Meta mantiene el indicador, para un mensaje que nunca llegaba.
    """

    def setUp(self):
        from bot.models import Conversation
        self.conv = Conversation.objects.create(wa_id="56900000009", name="Tomas")

    def _correr(self, texto, msg_id="wamid.1"):
        """Corre handle_message con todo lo externo mockeado y devuelve el
        kwargs con el que se llamó a mark_as_read."""
        import asyncio
        from unittest.mock import MagicMock
        from bot.whatsapp import handlers

        cliente = MagicMock()
        with patch.object(handlers, "get_wa_client", return_value=cliente), \
             patch.object(handlers, "_run_graph", new=self._grafo_falso):
            asyncio.run(handlers.handle_message(
                self.conv.wa_id, "Tomas", texto, msg_id, envio_inline=True))
        return cliente.mark_as_read.call_args

    @staticmethod
    async def _grafo_falso(*a, **kw):
        return None

    def test_saludo_puro_no_pide_indicador(self):
        # Se responde en el tiempo de un POST: el indicador solo parpadearía.
        _, kwargs = self._correr("hola buenas")
        self.assertFalse(kwargs["mostrar_escribiendo"])

    def test_saludo_con_contenido_si_pide_indicador(self):
        # El saludo sale al instante pero el grafo todavía tiene que trabajar.
        _, kwargs = self._correr("hola quiero cotizar una suv", msg_id="wamid.2")
        self.assertTrue(kwargs["mostrar_escribiendo"])

    def test_mensaje_normal_si_pide_indicador(self):
        self.conv.flow_state = "ESPERANDO_CONSULTA"
        self.conv.save()
        _, kwargs = self._correr("cuanto cuesta la tucson", msg_id="wamid.3")
        self.assertTrue(kwargs["mostrar_escribiendo"])

    def test_modo_humano_no_pide_indicador(self):
        # Contesta un ejecutivo por otro canal; el bot no manda nada.
        self.conv.set_flow({"modo": "HUMAN"})
        self.conv.save()
        _, kwargs = self._correr("hola", msg_id="wamid.4")
        self.assertFalse(kwargs["mostrar_escribiendo"])

    def test_racha_de_acuses_no_pide_indicador(self):
        # 2do acuse consecutivo responde "👍" sin LLM; el 3ro es silencio.
        from bot.models import Message
        self.conv.flow_state = "ESPERANDO_CONSULTA"
        self.conv.save()
        Message.objects.create(conversation=self.conv, role="user", content="ok")
        _, kwargs = self._correr("gracias", msg_id="wamid.5")
        self.assertFalse(kwargs["mostrar_escribiendo"])


class BloqueYaSaludadoTest(TestCase):
    """La instrucción al especialista es lo que de verdad evita el re-saludo.

    El primer intento fue recortar el saludo de la respuesta ya generada con
    una lista blanca de palabras, y el LLM la esquivó en la primera prueba real
    con "¡Hola de nuevo Tomas! Encantado de ayudarte" -- "de" y "nuevo" no
    estaban en la lista. Enumerar lo que un LLM puede escribir no funciona (es
    la misma lección del loop de despedidas), así que se ataca la generación.

    Verificado contra el LLM real el 2026-09-02: con el bloque, 3/3 turnos
    continuaron sin saludar; sin el bloque, volvió a abrir con "¡Hola Tomas!".
    """

    def test_sin_el_flag_no_agrega_nada(self):
        from bot.flow.agents._common import bloque_ya_saludado
        self.assertEqual(bloque_ya_saludado({}), "")
        self.assertEqual(bloque_ya_saludado({"ya_saludamos": False}), "")

    def test_con_el_flag_pide_no_repetir_el_saludo(self):
        from bot.flow.agents._common import bloque_ya_saludado
        bloque = bloque_ya_saludado({"ya_saludamos": True}).lower()
        self.assertIn("no vuelvas a saludar", bloque)
        self.assertIn("no te presentes", bloque)

    def test_el_especialista_lo_incluye_en_su_system_prompt(self):
        from django.conf import settings
        from bot.models import CustomSpecialist
        from bot.flow.agents.custom import CustomPromptAgent

        # cliente=CLIENTE_ACTIVO y no "cavem" fijo: manager filtrado + la suite
        # corre como renault (ver CLAUDE.md).
        especialista = CustomSpecialist.todos_los_clientes.create(
            slug="ventas", cliente=settings.CLIENTE_ACTIVO, label="Ventas",
            descripcion="Atiende compra de usados.")
        agente = CustomPromptAgent(especialista)

        con = agente.build_system_prompt({"ya_saludamos": True}, "PROMPT")
        sin = agente.build_system_prompt({"ya_saludamos": False}, "PROMPT")
        self.assertIn("NO vuelvas a saludar", con)
        self.assertNotIn("NO vuelvas a saludar", sin)

    def test_el_corte_es_red_de_seguridad_del_caso_que_fallo(self):
        # Aunque la instrucción es la defensa principal, el corte tiene que
        # cubrir el texto exacto que se escapó en producción.
        salida = _quitar_saludo_inicial(
            "¡Hola de nuevo Tomas! Encantado de ayudarte 👋", "tomas valenzuela")
        self.assertNotIn("Hola de nuevo", salida)

    def test_handle_message_le_pasa_el_flag_al_grafo(self):
        import asyncio
        import inspect
        from bot.whatsapp import handlers

        # El flag viaja al estado del grafo, no solo a _run_graph.
        fuente = inspect.getsource(handlers._run_graph)
        self.assertIn('"ya_saludamos": ya_saludamos', fuente)
