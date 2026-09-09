"""El prompt del supervisor no debe contener lineas con rol "system".

Bug real del 2026-09-02 (docs/PENDIENTES.md #16): `build_context_window`
agrega `_MARCADOR_HISTORIAL_TRUNCADO` con role="system" al pasar MAX_TURNS
mensajes, y `supervisor_node` aplanaba el historial como "{role}: {content}".
El resultado era una linea

    system: (... no asumas que nunca paso ni lo niegues; pedile que te lo
    recuerde o confirmalo antes de responder)

dentro de un prompt de texto, o sea la firma de un prompt injection.
OpenRouter la bloqueaba con 403 "prompt injection patterns detected", asi que
CUALQUIER conversacion de mas de 20 mensajes dejaba al supervisor fallando
siempre: HTTP 500 en el webhook y silencio total para el contacto. Reproducido
con los dos modelos (el de ruteo y el conversacional), o sea que no dependia
del modelo.
"""
from unittest.mock import AsyncMock, patch

from django.test import TestCase, TransactionTestCase

from bot.flow.context_window import _MARCADOR_HISTORIAL_TRUNCADO
import bot.flow.graph as g


class SupervisorHistorialTest(TestCase):
    def _estado(self, messages):
        return dict(
            wa_id="56900000001", text="dame opciones hasta 25 millones", name="Pancho",
            messages=messages, flow_state="ESPERANDO_CONSULTA", flow_data={},
            active_agent="ventas", campaign_hint=None, lead_class=None, stage=None,
            response_text="", interactive_buttons=None, interactive_list=None,
            interactive_options=None, list_button_text=None, tool_messages=[],
            modelo_imagen=None, sucursal_direccion_ids=None, reply_to=None,
            _dispatch=None, ya_saludamos=False,
        )

    def _prompt_enviado(self, messages):
        """Corre supervisor_node con el LLM mockeado y devuelve el prompt."""
        import asyncio
        capturado = {}

        async def _falso(llm, prompt, label="", **kw):
            capturado["prompt"] = prompt
            return '{"agente": "ventas", "intencion_compra_real": false}'

        with patch.object(g, "_ainvoke_with_retry", new=_falso), \
             patch.object(g, "_get_routing_llm", new=AsyncMock(return_value=object())):
            asyncio.run(g.supervisor_node(self._estado(messages)))
        return capturado["prompt"]

    def test_el_marcador_de_truncado_no_llega_al_prompt(self):
        messages = [
            _MARCADOR_HISTORIAL_TRUNCADO,
            {"role": "user", "content": "estoy buscando una suv pa mi familia"},
            {"role": "assistant", "content": "Te muestro 3 opciones."},
        ]
        prompt = self._prompt_enviado(messages)

        # Lo que disparaba el 403: una linea "system:" con instrucciones.
        self.assertNotIn("system:", prompt)
        self.assertNotIn("no lo niegues", prompt)
        # El historial real del cliente sí tiene que estar.
        self.assertIn("estoy buscando una suv pa mi familia", prompt)
        self.assertIn("Te muestro 3 opciones.", prompt)

    def test_ningun_rol_system_llega_al_prompt_sea_cual_sea(self):
        # Guard general: si mañana se agrega otro mensaje de rol system a la
        # ventana de contexto, tampoco debe aplanarse acá.
        messages = [
            {"role": "system", "content": "instruccion interna cualquiera"},
            {"role": "user", "content": "hola"},
        ]
        self.assertNotIn("instruccion interna", self._prompt_enviado(messages))

    def test_el_historial_normal_no_cambia(self):
        messages = [
            {"role": "user", "content": "tengo 20 millones"},
            {"role": "assistant", "content": "Con eso hay varias opciones."},
        ]
        prompt = self._prompt_enviado(messages)
        self.assertIn("user: tengo 20 millones", prompt)
        self.assertIn("assistant: Con eso hay varias opciones.", prompt)


class FallaNoManejadaDelGrafoTest(TransactionTestCase):
    """Un error permanente del grafo no debe dejar al contacto sin respuesta.

    TransactionTestCase y no TestCase: el manejo del error escribe a la BD via
    sync_to_async, o sea desde otro thread, y con la transaccion sin commitear
    sqlite en shared-cache bloquea esa escritura (mismo gotcha ya documentado
    en GraphGetLlmOverrideTest).

    Segunda mitad del incidente del 2026-09-02: el 403 de OpenRouter subia sin
    atrapar hasta el webhook, Django devolvia 500 y el contacto no recibia NADA
    -- ni un mensaje de error ni un incidente en el panel. Escribio tres veces
    al vacio. La causa de ese 403 ya esta arreglada, pero el modo de falla no
    era especifico de ella.
    """

    def setUp(self):
        from bot.models import Conversation
        self.conv = Conversation.objects.create(wa_id="56900000002", name="Pancho")

    def test_responde_el_fallback_y_registra_incidente(self):
        import asyncio
        from unittest.mock import MagicMock
        from bot.models import Incident, Message
        from bot.whatsapp import handlers

        grafo = MagicMock()
        grafo.ainvoke = AsyncMock(side_effect=RuntimeError("403 prompt injection patterns detected"))
        cliente = MagicMock()

        with patch.object(handlers, "get_flow_graph", return_value=grafo), \
             patch.object(handlers, "get_wa_client", return_value=cliente):
            asyncio.run(handlers._run_graph(self.conv, "dame opciones", "wamid.1"))

        # 1) el contacto recibe algo
        self.assertTrue(cliente.send_text.called)
        enviado = cliente.send_text.call_args[0][1]
        self.assertIn("problema", enviado.lower())
        # 2) queda guardado en la conversación
        self.assertTrue(Message.objects.filter(conversation=self.conv, role="assistant").exists())
        # 3) y el operador lo ve en el panel
        self.assertTrue(
            Incident.objects.filter(conversation=self.conv, kind="revision_requerida").exists())
