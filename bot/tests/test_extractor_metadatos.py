"""El extractor que saca los metadatos del CRM de la prosa ya escrita.

Ver docs/superpowers/specs/2026-09-03-canal-de-salida-prosa-natural-design.md.
Lo que estos tests protegen: que el extractor NUNCA reescriba el mensaje (R1),
que nunca invente un handoff (R3), y que una falla suya no tumbe el turno (R5).
"""
import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase
from langchain.messages import AIMessage

from bot.flow import extractor_metadatos as extractor
from bot.flow.extractor_metadatos import SCHEMA_METADATOS, extraer_metadatos


def _llm_que_devuelve(contenido):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=contenido))
    return llm


class SchemaTest(SimpleTestCase):
    def test_el_schema_pide_los_campos_del_contrato(self):
        props = SCHEMA_METADATOS["schema"]["properties"]
        for campo in ("intent", "stage", "handoff", "handoff_reason",
                      "requiere_revision", "motivo_revision", "extracted_data",
                      "next_state"):
            self.assertIn(campo, props)

    def test_el_schema_no_le_pide_lead_class_al_modelo(self):
        # HOT/WARM/COLD es el veredicto que `calcular_score_intouch` produce en
        # código desde las `senales`. Pedírselo además al modelo son dos
        # escritores del mismo dato, y el del modelo no es reproducible: el
        # mismo lead salía HOT o WARM según el turno.
        self.assertNotIn("lead_class", SCHEMA_METADATOS["schema"]["properties"])
        self.assertNotIn("lead_class", SCHEMA_METADATOS["schema"]["required"])

    def test_el_schema_no_pide_el_mensaje(self):
        # El mensaje ya lo escribio el modelo grande: el extractor NUNCA lo
        # reescribe (spec R1). Si el schema lo pidiera, el modelo chico tendria
        # licencia para inventar texto que el cliente va a leer.
        self.assertNotIn("mensaje", SCHEMA_METADATOS["schema"]["properties"])

    def test_el_schema_es_estricto(self):
        self.assertTrue(SCHEMA_METADATOS.get("strict"))


class ExtraerMetadatosTest(SimpleTestCase):
    def _extraer(self, contenido, nombre_agente="ventas"):
        with patch("bot.flow.extractor_metadatos._get_extractor_llm",
                   AsyncMock(return_value=_llm_que_devuelve(contenido))), \
             patch("bot.flow.extractor_metadatos.CallbackHandler", MagicMock()):
            return asyncio.run(extraer_metadatos(
                "La garantía es de 6 meses.", "cual es la garantia?", nombre_agente))

    def test_parsea_los_campos(self):
        m = self._extraer('{"intent": "explorar", "lead_class": "WARM", "stage": "descubrimiento"}')
        self.assertEqual(m["intent"], "explorar")
        self.assertEqual(m["lead_class"], "WARM")

    def test_descarta_los_campos_que_el_especialista_no_declara(self):
        # "faq" no declara "intent" (bot/flow/respuesta.py::CAMPOS_EXTRA_POR_AGENTE):
        # solo "comercial" lo hace.
        m = self._extraer('{"intent": "cotizar", "lead_class": "HOT"}', nombre_agente="faq")
        self.assertNotIn("intent", m)

    def test_json_invalido_devuelve_vacio_y_no_revienta(self):
        self.assertEqual(self._extraer("no soy json"), {})

    def test_una_excepcion_del_llm_devuelve_vacio(self):
        # spec R5: si el extractor se cae, el turno no se cae -- el mensaje ya
        # se le mando al cliente antes de llegar aca.
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("proveedor caido"))
        # El backoff en 0: desde el reintento (docs/PENDIENTES.md 33) una
        # excepcion transitoria atraviesa los 3 intentos, y con el backoff real
        # este test dormiria 3s sin medir nada.
        with patch("bot.flow.extractor_metadatos._get_extractor_llm", AsyncMock(return_value=llm)), \
             patch("bot.flow.extractor_metadatos.CallbackHandler", MagicMock()), \
             patch("bot.flow.graph._BACKOFF_TRANSITORIO_SEGUNDOS", 0):
            m = asyncio.run(extraer_metadatos("hola", "hola", "ventas"))
        self.assertEqual(m, {})

    def test_una_falla_se_loguea_como_error_porque_el_handoff_quedo_sin_evaluar(self):
        # No es cosmetico: un fallo del extractor deja el turno SIN evaluar
        # handoff ni requiere_revision (spec R3, la funcion mas delicada del
        # bot) y el chat sale igual de bien, asi que el log es la unica senal.
        # A nivel warning se pierde entre el ruido normal del contenedor.
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("proveedor caido"))
        with patch("bot.flow.extractor_metadatos._get_extractor_llm", AsyncMock(return_value=llm)), \
             patch("bot.flow.extractor_metadatos.CallbackHandler", MagicMock()), \
             patch("bot.flow.graph._BACKOFF_TRANSITORIO_SEGUNDOS", 0), \
             self.assertLogs("bot.flow.extractor_metadatos", level="ERROR") as logs:
            asyncio.run(extraer_metadatos("hola", "hola", "ventas"))
        self.assertIn("handoff", "\n".join(logs.output))

    def test_un_json_invalido_tambien_se_loguea_como_error(self):
        with self.assertLogs("bot.flow.extractor_metadatos", level="ERROR"):
            self._extraer("no soy json")

    def test_handoff_nunca_se_inventa(self):
        # spec R3: sin evidencia explicita, false. Un handoff inventado
        # interrumpe una conversacion sana.
        m = self._extraer('{"intent": "explorar"}')
        self.assertIs(m.get("handoff", False), False)

    def test_handoff_se_respeta_cuando_viene_true(self):
        m = self._extraer('{"handoff": true, "handoff_reason": "pidio hablar con un humano"}')
        self.assertIs(m["handoff"], True)
        self.assertEqual(m["handoff_reason"], "pidio hablar con un humano")

    def test_no_reescribe_el_mensaje(self):
        m = self._extraer('{"mensaje": "TEXTO INVENTADO POR EL CHICO", "intent": "explorar"}')
        self.assertNotIn("mensaje", m)

    def test_los_strings_vacios_se_normalizan_a_none(self):
        # El resto del sistema hace `meta.get(x) or valor_previo`: un "" del
        # default del schema pisaria el valor que el turno anterior si sabia.
        m = self._extraer('{"intent": "", "lead_class": "", "stage": ""}')
        self.assertIsNone(m["intent"])
        self.assertIsNone(m["lead_class"])

    def test_extracted_data_que_no_es_dict_queda_vacio(self):
        m = self._extraer('{"extracted_data": "no soy un dict"}')
        self.assertEqual(m["extracted_data"], {})

    def test_extracted_data_sale_normalizado(self):
        # Lo que devuelve el extractor va directo al merge de
        # bot/whatsapp/cola_envio.py, asi que tiene que salir de aca ya limpio:
        # sin claves vacias, con el nombre canonico del CRM y el monto como
        # numero. Con `monto_pie` crudo, flow_data terminaba con `monto_pie` Y
        # `pie_disponible` diciendo cosas distintas (conversacion 29).
        m = self._extraer('{"extracted_data": {"comuna": "", "monto_pie": "$3.000.000",'
                          ' "plazo_actual": 24}}')
        self.assertEqual(m["extracted_data"], {"pie_disponible": 3000000, "plazo": 24})


class ReintentoDelExtractorTest(SimpleTestCase):
    """El extractor era el UNICO camino LLM del repo con `max_retries=0` y sin
    wrapper de reintento propio: un solo `wait_for` y, si se pasaba, el turno
    quedaba sin evaluar `handoff` ni `requiere_revision` (spec R3).

    Medido el 2026-09-07 (n=84 llamadas reales, variante en produccion): con el
    tope viejo de 15s el 15,5% de las llamadas no llegaba. Ver
    docs/PENDIENTES.md 33.
    """

    def _correr(self, llm, **constantes):
        # El backoff se pone en 0 para que el test no duerma de verdad. Se
        # patchea la CONSTANTE y no `asyncio.sleep`: `patch("...
        # extractor_metadatos.asyncio.sleep")` parchea el atributo del modulo
        # asyncio, o sea asyncio.sleep GLOBAL, y de paso anula los sleeps del
        # propio test (lo encontro test_el_tope_por_intento_corta_una_llamada_
        # lenta_de_verdad, que dejaba de colgarse y devolvia None).
        with contextlib.ExitStack() as pila:
            pila.enter_context(patch("bot.flow.extractor_metadatos._get_extractor_llm",
                                     AsyncMock(return_value=llm)))
            pila.enter_context(patch("bot.flow.extractor_metadatos.CallbackHandler",
                                     MagicMock()))
            pila.enter_context(patch("bot.flow.graph._BACKOFF_TRANSITORIO_SEGUNDOS", 0))
            for clave, valor in constantes.items():
                pila.enter_context(patch.object(extractor, clave, valor))
            return asyncio.run(extraer_metadatos("La garantía es de 6 meses.",
                                                 "cual es la garantia?", "ventas"))

    def test_un_timeout_en_el_primer_intento_se_reintenta_y_el_segundo_gana(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=[
            asyncio.TimeoutError(),
            AIMessage(content='{"intent": "explorar", "handoff": true}'),
        ])
        meta = self._correr(llm)
        self.assertEqual(meta["intent"], "explorar")
        self.assertIs(meta["handoff"], True)
        self.assertEqual(llm.ainvoke.await_count, 2)

    def test_un_429_se_reintenta(self):
        # El rate limit es el fallo transitorio por excelencia y ya pego en
        # produccion (el 429 de Cohere en el rerank, docs/PENDIENTES.md 30).
        rate_limit = RuntimeError("429 Too Many Requests")
        rate_limit.status_code = 429
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=[
            rate_limit, AIMessage(content='{"intent": "cotizar"}')])
        meta = self._correr(llm)
        self.assertEqual(meta["intent"], "cotizar")
        self.assertEqual(llm.ainvoke.await_count, 2)

    def test_agotar_los_intentos_devuelve_vacio(self):
        # El contrato que lee bot/whatsapp/cola_envio.py::
        # _vigilar_racha_del_extractor: {} == fallo. No puede cambiar a una
        # excepcion sin romper la racha.
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError())
        self.assertEqual(self._correr(llm), {})

    def test_un_4xx_permanente_no_se_reintenta(self):
        # Una api key mala o un schema invalido no se arreglan reintentando:
        # gastar los intentos (y su backoff) reteniendo el UNICO thread de la
        # cola es peor que fallar rapido. Mismo criterio que
        # graph.py::_es_error_permanente.
        permanente = RuntimeError("401 Unauthorized")
        permanente.status_code = 401
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=permanente)
        self.assertEqual(self._correr(llm), {})
        self.assertEqual(llm.ainvoke.await_count, 1)

    def test_el_presupuesto_total_corta_antes_de_un_intento_que_no_cabe(self):
        # El tope por intento NO alcanza como unica defensa: 3 intentos de 25s
        # serian 75s reteniendo el unico thread de envio de TODOS los
        # contactos.
        #
        # Reloj falso y no un presupuesto chico con un mock que falla rapido:
        # ese mock no consume tiempo, asi que el presupuesto nunca se gasta y
        # los 3 intentos entran igual (paso al escribir este test). Se
        # reemplaza el modulo `time` que ve el extractor -- no `time.monotonic`
        # a secas, que seria el modulo global.
        reloj = MagicMock()
        reloj.monotonic.side_effect = [0, 0, 999]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError())
        with patch("bot.flow.extractor_metadatos.time", reloj):
            self.assertEqual(self._correr(llm), {})
        self.assertEqual(llm.ainvoke.await_count, 1)

    def test_el_exito_tras_reintento_devuelve_meta_no_vacia_y_resetea_la_racha(self):
        # La racha de bot/whatsapp/cola_envio.py se resetea con cualquier meta
        # no vacia: lo que este test protege es que un reintento exitoso
        # PRODUZCA esa meta, o sea que la racha no cuente un fallo que el
        # reintento ya resolvio.
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=[
            asyncio.TimeoutError(), AIMessage(content='{"intent": "explorar"}')])
        self.assertTrue(self._correr(llm))

    def test_el_tope_por_intento_cubre_la_cola_medida(self):
        # 25s cubre el 96,4% de las 84 llamadas reales medidas (p95 21,11s,
        # max 26,11s); los 15s viejos cubrian el 84,5%.
        self.assertGreaterEqual(extractor._TIMEOUT_SEGUNDOS, 25)
        # Y el total tiene que caber en el techo del thread de la cola.
        self.assertLessEqual(extractor._PRESUPUESTO_TOTAL_SEGUNDOS, 40)

    def test_el_tope_por_intento_corta_una_llamada_lenta_de_verdad(self):
        # Los otros tests simulan la excepcion; este verifica que el tope se
        # APLIQUE, que es el bug original: `ChatOpenRouter` no respeta su
        # `request_timeout` (verificado el 2026-09-03 con 3, 3000 y 30000), asi
        # que si el `wait_for` no estuviera puesto la llamada colgada retendria
        # el unico thread de la cola hasta que el proveedor se digne.
        async def se_cuelga(*a, **k):
            await asyncio.sleep(30)
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=se_cuelga)
        arranque = time.monotonic()
        meta = self._correr(llm, _TIMEOUT_SEGUNDOS=0.05, _PRESUPUESTO_TOTAL_SEGUNDOS=0.2)
        self.assertEqual(meta, {})
        # Corto de verdad: sin el tope esto tardaria 30s por intento.
        self.assertLess(time.monotonic() - arranque, 5)


class HistorialDelCasoTest(SimpleTestCase):
    """El resumen necesita el CASO, no el turno (docs/PENDIENTES.md 33, (b)).

    Caso real del 2026-09-07: el turno 2 escribio "busca camioneta, tope 27M,
    quiere ahorrar" y el turno 5 lo reemplazo por "pregunta donde ver las
    camionetas". Peor, el turno 3 escribio "No ha dicho presupuesto ni plazo"
    -- FALSO, el cliente lo habia dicho un turno antes. Medido: 4 de 35
    resumenes (11%) afirmaban cosas sobre el caso completo viendo un solo
    turno. Con el historial a la vista, 0 de 5.
    """

    HISTORIAL = [
        {"role": "user", "content": "busco una camioneta, tengo 27 millones"},
        {"role": "assistant", "content": "Te muestro 3 opciones bajo tu presupuesto."},
        {"role": "user", "content": "donde las puedo ver?"},
    ]

    def _prompt_de(self, historial):
        """El prompt que se le manda al LLM, para poder inspeccionarlo."""
        llm = _llm_que_devuelve("{}")
        with patch("bot.flow.extractor_metadatos._get_extractor_llm",
                   AsyncMock(return_value=llm)), \
             patch("bot.flow.extractor_metadatos.CallbackHandler", MagicMock()):
            asyncio.run(extraer_metadatos(
                "Puedes verlas en Cavem La Reina.", "donde las puedo ver?",
                "ventas", historial=historial))
        mensajes = llm.ainvoke.await_args.args[0]
        return mensajes[0].content

    def test_el_historial_viaja_al_prompt(self):
        prompt = self._prompt_de(self.HISTORIAL)
        self.assertIn("27 millones", prompt)
        self.assertIn("3 opciones", prompt)

    def test_sin_historial_sigue_funcionando(self):
        # Compatibilidad: el parametro es opcional y el camino viejo (sin
        # conversacion a mano) no puede romperse.
        prompt = self._prompt_de(None)
        self.assertIn("donde las puedo ver?", prompt)

    def test_el_turno_a_clasificar_sigue_marcado_aparte(self):
        # El riesgo de darle el historial: que marque handoff por algo que paso
        # cinco turnos atras. El turno en curso tiene que quedar distinguible
        # del contexto.
        prompt = self._prompt_de(self.HISTORIAL)
        self.assertIn("Puedes verlas en Cavem La Reina.", prompt)
        i_hist = prompt.index("27 millones")
        i_turno = prompt.index("Puedes verlas en Cavem La Reina.")
        self.assertLess(i_hist, i_turno, "el historial va antes del turno a clasificar")

    def test_el_prompt_acota_los_campos_por_turno_al_turno(self):
        prompt = self._prompt_de(self.HISTORIAL)
        # La instruccion explicita de que handoff/requiere_revision/intent son
        # de ESTE turno y no del historial. Sin esto, el modelo chico marca
        # handoff por un pedido viejo ya resuelto.
        self.assertIn("SOLO de este turno", prompt)
