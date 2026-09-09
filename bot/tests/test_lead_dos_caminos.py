"""El invariante que faltaba: los DOS caminos de salida escriben el lead en
`LeadInTouch`.

Por qué existe este archivo (hallazgo C1 de la review final de rama,
2026-09-09): hay dos caminos por los que un turno entrega su respuesta, y cada
uno escribe el lead en un lugar distinto del código.

  1. Prosa (el camino normal desde el refactor del 2026-09-03): el lead lo trae
     el extractor de metadatos y lo escribe `bot/whatsapp/cola_envio.py`.
  2. La tool `responder`: el lead viene en los argumentos del modelo y lo
     escribe el bloque post-grafo de `bot/whatsapp/handlers.py`.

El camino 2 quedó apuntando al escritor del bot automotriz heredado
(`bot.business.prospeccion`, tabla `LeadComercial`) mientras el 1 ya apuntaba
al de InTouch. Y el modo de falla no era un error: los antecedentes que abren
un lead de un contrato no comparten UN SOLO nombre con los del otro
(automotriz: `comuna`, `vehiculo_interes`, `presupuesto`...; InTouch:
`empresa`, `correo`, `industria`, `necesidad_principal`...), así que el
escritor equivocado se iba por su propia guarda de apertura y retornaba **sin
fila y sin log**. El lead completo del contacto se perdía, en la fracción de
turnos en que el modelo contesta por `responder` -- entre el 10% y el 25%
según la medición de `bot/flow/respuesta.py:205-215`.

Por eso el test no verifica el fix, verifica el INVARIANTE: cada camino se
ejercita de punta a punta con un lead real y se afirma que la fila aparece en
`LeadInTouch`. Un refactor que reapunte cualquiera de los dos a otro escritor
-- o que agregue un tercer camino -- se cae acá en vez de perder leads en
producción sin dejar rastro.
"""
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from bot.models import Conversation, LeadInTouch
from bot.whatsapp.handlers import handle_message

# Antecedentes del contrato de InTouch, no del automotriz: `empresa` y
# `necesidad_principal` están en ANTECEDENTES_QUE_ABREN_LEAD de
# bot/business/lead_intouch.py y no existen en el de bot/business/prospeccion.py.
LEAD_DEL_CONTACTO = {
    "nombre_completo": "Ana Pérez",
    "empresa": "Acme SpA",
    "correo": "ana@acme.cl",
    "industria": "retail",
    "necesidad_principal": "automatizar la atención de postventa por WhatsApp",
}


class LosDosCaminosEscribenElLeadTest(TestCase):
    """Un test por camino, los dos por `handle_message` real."""

    def setUp(self):
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client")
        self.mock_wa = self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)
        self.conv = Conversation.objects.create(
            wa_id="56911112222", flow_state="ESPERANDO_CONSULTA")

    def _resultado_del_grafo(self, **extra):
        base = {
            "response_text": "Perfecto, Ana. Te contacta un especialista.",
            "active_agent": "comercial", "flow_data": {}, "flow_state": "IDLE",
            "modelo_imagen": None, "intent": None, "lead_class": None, "stage": None,
            "handoff_reason": None, "requiere_revision": False, "motivo_revision": None,
            "mensaje_cliente": "somos Acme, necesitamos automatizar postventa",
        }
        base.update(extra)
        return base

    def test_el_camino_de_la_tool_responder_escribe_el_lead(self):
        # `lead` en el resultado del grafo es exactamente lo que deja
        # `bot/flow/respuesta.py::respuesta_de_tool_calls` cuando el modelo
        # contesta llamando a `responder`.
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_get_graph.return_value.ainvoke = AsyncMock(
                return_value=self._resultado_del_grafo(lead=dict(LEAD_DEL_CONTACTO)))
            async_to_sync(handle_message)(
                "56911112222", "Ana", "somos Acme, necesitamos automatizar postventa",
                "wamid.responder", envio_inline=True)

        lead = LeadInTouch.objects.filter(conversation=self.conv).first()
        self.assertIsNotNone(
            lead, "el camino de `responder` no escribió el lead en LeadInTouch")
        self.assertEqual(lead.empresa, "Acme SpA")
        self.assertEqual(lead.correo, "ana@acme.cl")

    def test_el_camino_de_prosa_escribe_el_lead(self):
        # En prosa el grafo NO trae `lead`: lo trae el extractor dentro de la
        # cola de envío, que es lo que se simula acá (el extractor mismo llama
        # al LLM; lo que se ejercita es el escritor que la cola elige).
        meta = {
            "intent": "", "stage": "", "handoff": False, "handoff_reason": "",
            "requiere_revision": False, "motivo_revision": "", "extracted_data": {},
            "next_state": "", "lead": dict(LEAD_DEL_CONTACTO),
        }
        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph, \
             patch("bot.whatsapp.cola_envio._extraer_sync", return_value=meta):
            mock_get_graph.return_value.ainvoke = AsyncMock(
                return_value=self._resultado_del_grafo(metadatos_pendientes=True))
            async_to_sync(handle_message)(
                "56911112222", "Ana", "somos Acme, necesitamos automatizar postventa",
                "wamid.prosa", envio_inline=True)

        lead = LeadInTouch.objects.filter(conversation=self.conv).first()
        self.assertIsNotNone(
            lead, "el camino de prosa no escribió el lead en LeadInTouch")
        self.assertEqual(lead.empresa, "Acme SpA")
        self.assertEqual(lead.correo, "ana@acme.cl")


class NingunCaminoEscribeEnLaTablaAutomotrizTest(TestCase):
    """La otra mitad del invariante.

    Que aparezca la fila de InTouch no alcanza para descartar el defecto: si un
    camino escribiera en las DOS tablas, el lead del contacto quedaría también
    en el CRM del bot automotriz -- datos de un cliente en la tabla de otro
    dominio, que es la clase de mezcla silenciosa que este stack ya pagó una
    vez (297 chunks de Astara en el schema de Renault).
    """

    def setUp(self):
        self.wa_patch = patch("bot.whatsapp.handlers.get_wa_client")
        self.mock_wa = self.wa_patch.start()
        self.addCleanup(self.wa_patch.stop)
        self.conv = Conversation.objects.create(
            wa_id="56911112223", flow_state="ESPERANDO_CONSULTA")

    def test_el_camino_de_responder_no_toca_leadcomercial(self):
        from bot.models import LeadComercial

        with patch("bot.whatsapp.handlers.get_flow_graph") as mock_get_graph:
            mock_get_graph.return_value.ainvoke = AsyncMock(return_value={
                "response_text": "listo", "active_agent": "comercial",
                "flow_data": {}, "flow_state": "IDLE", "modelo_imagen": None,
                "intent": None, "lead_class": None, "stage": None,
                "handoff_reason": None, "requiere_revision": False,
                "motivo_revision": None, "lead": dict(LEAD_DEL_CONTACTO),
            })
            async_to_sync(handle_message)(
                "56911112223", "Ana", "somos Acme", "wamid.solo-intouch",
                envio_inline=True)

        self.assertEqual(LeadInTouch.objects.count(), 1)
        self.assertEqual(LeadComercial.objects.count(), 0)
