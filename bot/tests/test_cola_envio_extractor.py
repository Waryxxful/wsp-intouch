"""Los metadatos del CRM se persisten DESPUES del envio, en la cola.

Ver docs/superpowers/specs/2026-09-03-canal-de-salida-prosa-natural-design.md.
Cuando esto corre, el cliente ya leyo su mensaje: por eso el extractor puede
tardar sin que nadie lo sienta (R2), y por eso una falla suya no puede tumbar
el turno (R5).
"""
from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase

from bot.models import Conversation, Incident


class ExtractorEnLaColaTest(TransactionTestCase):
    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56911110001", name="Tomas")

    def _correr(self, metadatos_extraidos, partes=None, modelo_imagen=None):
        from bot.whatsapp.cola_envio import _enviar
        self.wa = MagicMock()
        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value=metadatos_extraidos) as mock_extraer:
            _enviar(self.conv.pk, self.conv.wa_id, partes or [], modelo_imagen, [],
                    self.wa,
                    metadatos={"prosa": "La garantía es de 6 meses.",
                               "mensaje_cliente": "cual es la garantia?",
                               "nombre_agente": "ventas"})
        self.conv.refresh_from_db()
        return mock_extraer

    def test_persiste_lead_class_y_stage(self):
        self._correr({"lead_class": "HOT", "stage": "cotizacion"})
        self.assertEqual(self.conv.lead_class, "HOT")
        self.assertEqual(self.conv.stage, "cotizacion")

    def test_acumula_extracted_data_en_flow_data(self):
        self.conv.set_flow({"contacto_previo": True})
        self.conv.save()
        self._correr({"extracted_data": {"presupuesto": 12000000}})
        self.assertEqual(self.conv.get_flow(),
                         {"contacto_previo": True, "presupuesto": 12000000})

    def test_un_handoff_deja_incidente(self):
        self._correr({"handoff": True, "handoff_reason": "pidio un humano"})
        self.assertTrue(Incident.objects.filter(
            conversation=self.conv, kind="handoff").exists())

    def test_sin_handoff_no_deja_incidente(self):
        self._correr({"handoff": False})
        self.assertFalse(Incident.objects.filter(
            conversation=self.conv, kind="handoff").exists())

    def test_requiere_revision_deja_su_incidente(self):
        self._correr({"requiere_revision": True, "motivo_revision": "reclamo grave"})
        self.assertTrue(Incident.objects.filter(
            conversation=self.conv, kind="revision_requerida").exists())

    def test_un_extractor_vacio_no_pisa_nada(self):
        # spec R5: el extractor puede fallar y devolver {}. El valor previo
        # tiene que sobrevivir -- lo contrario borraria lo que el turno anterior
        # ya sabia.
        self.conv.lead_class = "WARM"
        self.conv.stage = "descubrimiento"
        self.conv.save()
        self._correr({})
        self.assertEqual(self.conv.lead_class, "WARM")
        self.assertEqual(self.conv.stage, "descubrimiento")

    def test_el_extractor_recibe_la_prosa_y_el_mensaje_del_cliente(self):
        mock_extraer = self._correr({"intent": "explorar"})
        # `conv` se agrego con el historial del caso (docs/PENDIENTES.md 33,
        # punto (b)): es lo que le permite a `_extraer_sync` armar la ventana
        # con build_context_window. Se afirma explicito y no con ANY para que
        # sacarlo por accidente rompa acá -- sin la conversacion el extractor
        # vuelve a clasificar un turno suelto, en silencio.
        mock_extraer.assert_called_once_with(
            "La garantía es de 6 meses.", "cual es la garantia?", "ventas",
            conv=self.conv)

    def test_el_modelo_imagen_del_grafo_se_manda_directo(self):
        # InTouch es B2B y su extractor ya no propone `modelo_imagen` (contrato
        # sin fotos de vehiculo, commit e185fac). El unico origen que queda es
        # el parametro que llega del camino JSON viejo (bot/flow/graph.py), y
        # se manda tal cual, sin pasar por la extraccion.
        with patch("bot.scraping.imagenes.resolver_imagen_modelo",
                   return_value="https://x/koleos.jpg") as mock_resolver:
            self._correr({"intent": "explorar"}, modelo_imagen="koleos")
        mock_resolver.assert_called_once_with("koleos")

    def test_sin_metadatos_no_llama_al_extractor(self):
        from bot.whatsapp.cola_envio import _enviar
        with patch("bot.whatsapp.cola_envio._extraer_sync") as mock_extraer:
            _enviar(self.conv.pk, self.conv.wa_id, ["parte 2"], None, [], MagicMock())
        mock_extraer.assert_not_called()

    def test_las_partes_se_mandan_antes_de_extraer(self):
        # El orden importa: el cliente tiene que leer su mensaje mientras el
        # extractor trabaja, no despues.
        orden = []
        wa = MagicMock()
        wa.send_text.side_effect = lambda *a, **kw: orden.append("envio")
        from bot.whatsapp.cola_envio import _enviar
        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   side_effect=lambda *a, **kw: orden.append("extraccion") or {}):
            _enviar(self.conv.pk, self.conv.wa_id, ["parte 2"], None, [], wa,
                    metadatos={"prosa": "x", "mensaje_cliente": "y", "nombre_agente": "ventas"})
        self.assertEqual(orden, ["envio", "extraccion"])


class RachaDeFallosDelExtractorTest(TransactionTestCase):
    """Un extractor que falla SIEMPRE tiene que gritar.

    El fallback por turno ({} y seguir) es deliberado (spec R5): el mensaje ya
    salio y el turno no se cae. Lo que faltaba era el otro lado: sin contador
    ni incidente, una falla sistematica es invisible -- el bot responde
    perfecto y deja de clasificar leads, de mover stage y de DERIVAR A HUMANOS.
    Precedente real: el 402 de DeepSeek del 2026-09-02 rompio el indexado del
    RAG sin que nada avisara (docs/PENDIENTES.md #1).
    """
    KIND = "extractor_caido"

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56911110003", name="Tomas")

    def _turno(self, meta):
        from bot.whatsapp.cola_envio import _enviar
        with patch("bot.whatsapp.cola_envio._extraer_sync", return_value=meta):
            _enviar(self.conv.pk, self.conv.wa_id, [], None, [], MagicMock(),
                    metadatos={"prosa": "p", "mensaje_cliente": "m", "nombre_agente": "ventas"})

    def _fallar(self, veces):
        for _ in range(veces):
            self._turno({})

    def test_un_fallo_aislado_no_deja_incidente(self):
        # Errar del lado de no ser ruidoso: un incidente por cada timeout suelto
        # de un LLM se ignora, y un incidente que se ignora no sirve de nada.
        self._fallar(1)
        self.assertFalse(Incident.objects.filter(kind=self.KIND).exists())

    def test_una_racha_deja_un_incidente(self):
        from bot.whatsapp.cola_envio import _RACHA_PARA_INCIDENTE
        self._fallar(_RACHA_PARA_INCIDENTE)
        inc = Incident.objects.get(kind=self.KIND)
        self.assertEqual(inc.conversation_id, self.conv.pk)
        self.assertEqual(inc.context["origen"], "extractor_metadatos")

    def test_la_racha_larga_no_deja_un_incidente_por_turno(self):
        from bot.whatsapp.cola_envio import _RACHA_PARA_INCIDENTE
        self._fallar(_RACHA_PARA_INCIDENTE + 4)
        self.assertEqual(Incident.objects.filter(kind=self.KIND).count(), 1)

    def test_una_extraccion_buena_corta_la_racha(self):
        from bot.whatsapp.cola_envio import _RACHA_PARA_INCIDENTE
        self._fallar(_RACHA_PARA_INCIDENTE - 1)
        self._turno({"intent": "explorar"})
        self._fallar(_RACHA_PARA_INCIDENTE - 1)
        self.assertFalse(Incident.objects.filter(kind=self.KIND).exists())

    def test_la_racha_se_lleva_en_la_bd_y_no_en_memoria(self):
        # Gunicorn corre 2 workers x 8 threads: un contador en memoria de
        # proceso cuenta por worker, asi que el umbral real se multiplicaria
        # por la cantidad de workers y cada uno dejaria su propio incidente.
        from bot.models import Setting
        from bot.whatsapp.cola_envio import _CLAVE_RACHA
        self._fallar(3)
        self.assertEqual(Setting.objects.get(key=_CLAVE_RACHA).value, "3")
        self._turno({"intent": "explorar"})
        self.assertEqual(Setting.objects.get(key=_CLAVE_RACHA).value, "0")


class UltimaExtraccionTest(TransactionTestCase):
    """Lo que el extractor decidio queda legible despues del turno.

    Desde el refactor de prosa el resultado del grafo ya NO trae `intent`:
    nace en la cola, despues de que el grafo termino. El simulador
    (bot/simulator/app_wrapper.py) lo leia del resultado del grafo y por eso
    veia `intent=None` en todos los turnos.
    """

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56911110004", name="Tomas")

    def test_queda_registrado_lo_que_saco_el_extractor(self):
        from bot.whatsapp.cola_envio import _enviar, ultima_extraccion
        with patch("bot.whatsapp.cola_envio._extraer_sync",
                   return_value={"intent": "cotizar"}):
            _enviar(self.conv.pk, self.conv.wa_id, [], None, [], MagicMock(),
                    metadatos={"prosa": "p", "mensaje_cliente": "m", "nombre_agente": "ventas"})
        self.assertEqual(ultima_extraccion(self.conv.wa_id)["intent"], "cotizar")

    def test_sin_extraccion_previa_devuelve_vacio(self):
        from bot.whatsapp.cola_envio import ultima_extraccion
        self.assertEqual(ultima_extraccion("56900000000"), {})
