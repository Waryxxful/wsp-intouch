"""El contrato de salida como tool nativa (bot/flow/respuesta.py).

Lo que estos tests protegen NO es cosmetico: el contrato viejo (un JSON pedido
en el prompt, con tools bindeadas al mismo tiempo) hacia que el LLM escribiera
la respuesta en prosa, la validacion la rechazara y se gastara una llamada
COMPLETA al LLM en reformatear el mismo texto. Medido sobre 44 turnos reales:
1,09 reintentos por turno, 3,78s de media, el 27% del turno. Ver el docstring
de bot/flow/respuesta.py.

El test que cuida esa ganancia es
`SalidaUtilizableTest.test_una_llamada_a_responder_no_gasta_un_reintento`: si
alguna vez vuelve a fallar, la latencia volvio a subir.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase
from langchain.messages import AIMessage

from bot.flow.respuesta import (
    CAMPOS_BASE, NOMBRE_TOOL_RESPUESTA, bloque_contrato_respuesta, campos_de,
    respuesta_de_tool_calls, responder, tool_calls_de_negocio,
)


def _tool_call(nombre, args, tid="call_1"):
    return {"name": nombre, "args": args, "id": tid, "type": "tool_call"}


def _ai(tool_calls=None, content=""):
    return AIMessage(content=content, tool_calls=tool_calls or [])


class CamposDeTest(SimpleTestCase):
    def test_la_base_la_tiene_cualquier_especialista(self):
        for campo in ("mensaje", "handoff", "handoff_reason",
                      "requiere_revision", "motivo_revision",
                      "extracted_data", "next_state"):
            self.assertIn(campo, campos_de("un_slug_cualquiera"))

    def test_ventas_suma_los_de_clasificacion_e_imagen(self):
        campos = campos_de("ventas")
        for campo in ("intent", "lead_class", "stage", "modelo_imagen"):
            self.assertIn(campo, campos)

    def test_faq_suma_ids_de_sucursal_y_ventas_no(self):
        # "ventas" nunca tuvo sucursal_direccion_ids en su contrato: sus ids se
        # resuelven por el fallback deterministico _extraer_sucursal_ids_de_tools
        # (graph.py). Se conserva tal cual para no cambiar comportamiento junto
        # con la latencia.
        self.assertIn("sucursal_direccion_ids", campos_de("faq"))
        self.assertNotIn("sucursal_direccion_ids", campos_de("ventas"))

    def test_un_especialista_cualquiera_no_puede_mandar_fotos_ni_clasificar(self):
        campos = campos_de("soporte")
        for campo in ("modelo_imagen", "lead_class", "stage", "intent"):
            self.assertNotIn(campo, campos)


class RespuestaDeToolCallsTest(SimpleTestCase):
    def test_extrae_el_mensaje_del_tool_call(self):
        parsed = respuesta_de_tool_calls(
            _ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": "Hola, te cuento"})]), "faq")
        self.assertEqual(parsed["mensaje"], "Hola, te cuento")

    def test_sin_tool_call_de_responder_devuelve_none(self):
        self.assertIsNone(respuesta_de_tool_calls(
            _ai([_tool_call("consultar_base_conocimiento", {"query": "garantia"})]), "faq"))
        self.assertIsNone(respuesta_de_tool_calls(_ai(), "faq"))

    def test_responder_sin_mensaje_util_devuelve_none(self):
        # Peor que reintentar es mandarle un mensaje vacio al cliente: el
        # llamador tiene que poder distinguir "no hay respuesta" y reintentar.
        for args in ({}, {"mensaje": ""}, {"mensaje": "   "}, {"mensaje": None}):
            self.assertIsNone(respuesta_de_tool_calls(
                _ai([_tool_call(NOMBRE_TOOL_RESPUESTA, args)]), "faq"), msg=f"args={args}")

    def test_args_que_no_son_dict_no_revientan(self):
        # Un AIMessage real no puede llegar asi: pydantic valida que args sea
        # dict y falla al construirlo. La guarda existe porque
        # respuesta_de_tool_calls lee el mensaje con getattr y tambien la
        # alcanzan objetos armados a mano (mocks de test, el simulador, un
        # canal de mensajes reconstruido) -- y este repo ya se comio ese bug
        # dos veces por el otro lado: _extraer_sucursal_ids_de_tools asumiendo
        # dict y _parse_json_response asumiendo objeto. Por eso se prueba con
        # un stub, no con un AIMessage.
        from types import SimpleNamespace
        stub = SimpleNamespace(tool_calls=[{"name": NOMBRE_TOOL_RESPUESTA, "args": "no soy un dict"}])
        self.assertIsNone(respuesta_de_tool_calls(stub, "faq"))

    def test_descarta_los_campos_que_el_especialista_no_declara(self):
        # faq no clasifica leads ni manda fotos: si el LLM los llena igual (el
        # schema de la tool los ofrece a todos), se descartan en silencio, misma
        # politica que _validar_choice ya tenia.
        parsed = respuesta_de_tool_calls(_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {
            "mensaje": "la garantia es de 6 meses",
            "modelo_imagen": "kwid", "lead_class": "HOT", "stage": "cotizacion",
        })]), "faq")
        for campo in ("modelo_imagen", "lead_class", "stage"):
            self.assertNotIn(campo, parsed)

    def test_conserva_los_campos_que_el_especialista_si_declara(self):
        parsed = respuesta_de_tool_calls(_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {
            "mensaje": "te muestro el Kwid",
            "modelo_imagen": "kwid", "lead_class": "HOT", "stage": "cotizacion",
            "intent": "cotizar", "extracted_data": {"presupuesto": 9000000},
        })]), "ventas")
        self.assertEqual(parsed["modelo_imagen"], "kwid")
        self.assertEqual(parsed["lead_class"], "HOT")
        self.assertEqual(parsed["intent"], "cotizar")
        self.assertEqual(parsed["extracted_data"], {"presupuesto": 9000000})

    def test_los_strings_vacios_se_normalizan_a_none(self):
        # El resto del grafo hace `parsed.get(x) or state.get(x)`: un "" del
        # default del schema pisaria el valor previo con nada.
        parsed = respuesta_de_tool_calls(_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {
            "mensaje": "ok", "next_state": "", "lead_class": "", "intent": "",
        })]), "ventas")
        self.assertIsNone(parsed["next_state"])
        self.assertIsNone(parsed["lead_class"])

    def test_extracted_data_que_no_es_dict_queda_vacio(self):
        parsed = respuesta_de_tool_calls(_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {
            "mensaje": "ok", "extracted_data": "no soy un dict",
        })]), "ventas")
        self.assertEqual(parsed["extracted_data"], {})

    def test_extracted_data_sale_normalizado(self):
        # _specialist_node_con_tools hace `{**flow_data, **extracted_data}`
        # justo despues de esto: si la clave llega con un sinonimo se suma en
        # vez de pisar, y el especialista lee dos valores del mismo dato en el
        # system prompt del turno siguiente (conversacion 29).
        parsed = respuesta_de_tool_calls(_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {
            "mensaje": "ok",
            "extracted_data": {"modelo_interes": "Tucson", "comuna": "",
                               "cuota_estimada": "$424.312"},
        })]), "ventas")
        self.assertEqual(parsed["extracted_data"],
                         {"vehiculo_interes": "Tucson", "cuota_mensual": 424312})

    def test_handoff_llega_como_booleano(self):
        parsed = respuesta_de_tool_calls(_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {
            "mensaje": "te derivo", "handoff": True, "handoff_reason": "pide un humano",
        })]), "faq")
        self.assertIs(parsed["handoff"], True)
        self.assertEqual(parsed["handoff_reason"], "pide un humano")

    def test_ids_de_sucursal_pasan_para_faq(self):
        parsed = respuesta_de_tool_calls(_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {
            "mensaje": "queda en Maipu", "sucursal_direccion_ids": [3, 7],
        })]), "faq")
        self.assertEqual(parsed["sucursal_direccion_ids"], [3, 7])


class ToolCallsDeNegocioTest(SimpleTestCase):
    def test_excluye_responder(self):
        # Critico: business_action_node arma su ToolNode desde
        # agent.business_actions(), que NO incluye `responder` -- si se colara,
        # el ToolNode reventaria con una tool inexistente y se perderia el turno.
        ai = _ai([
            _tool_call("consultar_base_conocimiento", {"query": "x"}, "c1"),
            _tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": "ya te digo"}, "c2"),
        ])
        nombres = [tc["name"] for tc in tool_calls_de_negocio(ai)]
        self.assertEqual(nombres, ["consultar_base_conocimiento"])

    def test_sin_tool_calls_da_lista_vacia(self):
        self.assertEqual(tool_calls_de_negocio(_ai()), [])
        self.assertEqual(tool_calls_de_negocio(
            _ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": "hola"})])), [])


class BloqueContratoTest(SimpleTestCase):
    def test_manda_la_salida_por_la_tool_y_no_pide_json(self):
        bloque = bloque_contrato_respuesta("faq", modo="tool")
        self.assertIn("responder", bloque)
        self.assertIn("## RESPUESTA", bloque)
        self.assertNotIn("JSON exacto", bloque)

    def test_el_bloque_se_genera_de_campos_de_y_no_se_desincroniza(self):
        # El bug que esto previene: pedirle en el prompt un campo que el filtro
        # de codigo despues descarta, o al revés. Aplica al modo "tool", que es
        # el unico que sigue nombrando campos (el de prosa no nombra ninguno).
        for slug, esperados in (("ventas", ("intent", "lead_class", "stage", "modelo_imagen")),
                                ("faq", ("sucursal_direccion_ids",))):
            bloque = bloque_contrato_respuesta(slug, modo="tool")
            for campo in esperados:
                self.assertIn(campo, bloque, msg=f"{slug} no pide {campo}")
                self.assertIn(campo, campos_de(slug))

    def test_un_especialista_sin_extras_no_menciona_campos_ajenos(self):
        bloque = bloque_contrato_respuesta("confirmacion", modo="tool")
        for campo in ("modelo_imagen", "lead_class", "sucursal_direccion_ids"):
            self.assertNotIn(campo, bloque)


class ToolResponderTest(SimpleTestCase):
    def test_el_schema_ofrece_todos_los_campos_del_contrato(self):
        campos_schema = set(responder.args.keys())
        for campo in CAMPOS_BASE:
            self.assertIn(campo, campos_schema)
        for campo in ("intent", "lead_class", "stage", "modelo_imagen", "sucursal_direccion_ids"):
            self.assertIn(campo, campos_schema)

    def test_mensaje_es_el_unico_argumento_obligatorio(self):
        requeridos = set(responder.args_schema.model_json_schema().get("required", []))
        self.assertEqual(requeridos, {"mensaje"})


class SalidaUtilizableTest(SimpleTestCase):
    """El corazon de la ganancia de latencia: cuando una llamada NO se reintenta."""

    def _correr(self, respuestas):
        from bot.flow.graph import _ainvoke_tools_json_with_retry
        llm = AsyncMock()
        llm.ainvoke.side_effect = respuestas
        with patch("bot.flow.graph.CallbackHandler", MagicMock()), \
             patch("bot.flow.graph._BACKOFF_TRANSITORIO_SEGUNDOS", 0):
            ai_msg = asyncio.run(_ainvoke_tools_json_with_retry(
                llm, [], label="test", nombre_agente="ventas"))
        return ai_msg, llm.ainvoke.call_count

    def test_una_llamada_a_responder_no_gasta_un_reintento(self):
        # ESTE es el test de la latencia. Antes de este cambio, el LLM escribia
        # la respuesta en prosa y se pagaba una segunda llamada completa
        # (3,78s de media, el 27% del turno) para reformatearla a JSON.
        _, llamadas = self._correr([_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": "listo"})])])
        self.assertEqual(llamadas, 1)

    def test_una_accion_de_negocio_tampoco_gasta_reintento(self):
        _, llamadas = self._correr([_ai([_tool_call("buscar_vehiculos", {"precio_max": 1})])])
        self.assertEqual(llamadas, 1)

    def test_el_json_en_content_sigue_siendo_valido(self):
        # Fallback de compatibilidad: un prompt custom en BD que pida el JSON a
        # mano tiene que seguir funcionando sin reintento.
        _, llamadas = self._correr([_ai(content='{"mensaje": "listo"}')])
        self.assertEqual(llamadas, 1)

    def test_una_respuesta_sin_nada_utilizable_si_reintenta(self):
        # Reemplaza a test_prosa_suelta_si_reintenta: desde el refactor del
        # canal de salida (2026-09-03) la prosa suelta YA NO se reintenta, es
        # la respuesta. Lo que sigue reintentando es una respuesta de la que no
        # se puede sacar nada -- content vacio, sin tool_calls.
        respuestas = [_ai(content="   "),
                      _ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": "la garantia es de 6 meses"})])]
        ai_msg, llamadas = self._correr(respuestas)
        self.assertEqual(llamadas, 2)
        self.assertEqual(respuesta_de_tool_calls(ai_msg, "ventas")["mensaje"],
                         "la garantia es de 6 meses")

    def test_responder_con_mensaje_vacio_reintenta_en_vez_de_mandar_nada(self):
        respuestas = [_ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": ""})]),
                      _ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": "ahora si"})])]
        _, llamadas = self._correr(respuestas)
        self.assertEqual(llamadas, 2)


class RecordatorioAlFinalTest(SimpleTestCase):
    """El recordatorio del canal de salida tiene que quedar ULTIMO.

    No es cosmetico: con el contrato solo en el system prompt el modelo
    entregaba el 25% de sus respuestas en prosa (y cada una costaba un reintento
    completo al LLM); repetirlo al final lo baja al 10%, medido contra el LLM
    real en A/B dentro de la misma corrida. Si algo vuelve a meter mensajes
    despues de este, la ganancia se pierde en silencio.
    """

    def _mensajes(self, **extra):
        from bot.flow.graph import _construir_mensajes
        state = {"messages": [{"role": "user", "content": "hola"}], "text": "y la garantia?"}
        state.update(extra)
        return _construir_mensajes(state, "SYSTEM PROMPT DEL ESPECIALISTA")

    def test_el_recordatorio_es_el_ultimo_mensaje(self):
        from bot.flow.respuesta import RECORDATORIO_RESPUESTA
        mensajes = self._mensajes()
        self.assertEqual(mensajes[-1].content, RECORDATORIO_RESPUESTA)

    def test_queda_ultimo_tambien_despues_de_los_tool_messages(self):
        # Este es el caso donde caia el reintento en produccion: la llamada
        # POSTERIOR a ejecutar una accion, con el resultado de la tool en medio.
        from langchain.messages import ToolMessage
        from bot.flow.respuesta import RECORDATORIO_RESPUESTA
        tool_msgs = [
            AIMessage(content="", tool_calls=[_tool_call("consultar_base_conocimiento", {"query": "x"})]),
            ToolMessage(content='{"ok": true}', tool_call_id="call_1", name="consultar_base_conocimiento"),
        ]
        mensajes = self._mensajes(tool_messages=tool_msgs)
        self.assertEqual(mensajes[-1].content, RECORDATORIO_RESPUESTA)
        self.assertIsInstance(mensajes[-2], ToolMessage)

    def test_pide_texto_normal_y_las_acciones_por_herramienta(self):
        # ACTUALIZADO el 2026-09-03: el recordatorio ya no dirige la salida a la
        # tool `responder`, pide texto normal. Lo que sigue protegiendo es que
        # el recordatorio EXISTE y llega ultimo -- eso es lo que baja la prosa
        # contaminada del 25% al 10%, medido en A/B contra el LLM real.
        from bot.flow.respuesta import RECORDATORIO_RESPUESTA
        self.assertIn("texto normal", RECORDATORIO_RESPUESTA)
        self.assertIn("herramienta", RECORDATORIO_RESPUESTA)
        self.assertNotIn("JSON", RECORDATORIO_RESPUESTA)


class TopeDeTiempoDelLLMTest(SimpleTestCase):
    """El tope de tiempo por intento tiene que aplicarse en la APLICACION.

    ChatOpenRouter no respeta su propio `request_timeout` (verificado el
    2026-09-03: ni 3, ni 3000, ni 30000 cortaron un ensayo de 900 palabras).
    Sin tope, una llamada colgada retiene uno de los DOS workers de gunicorn
    hasta que su `--timeout 120` lo mata, y el contacto no recibe nada. Ese
    mismo dia se vieron en Langfuse llamadas reales de 288s, 92s y 54s.
    """

    def _llm_que_tarda(self, segundos, resultado=None):
        async def _lento(*a, **kw):
            await asyncio.sleep(segundos)
            return resultado if resultado is not None else _ai(content='{"mensaje": "ok"}')
        llm = MagicMock()
        llm.ainvoke = _lento
        return llm

    def test_corta_la_llamada_que_pasa_el_tope(self):
        from bot.flow.graph import _ainvoke_con_tope
        llm = self._llm_que_tarda(5)
        with self.assertRaises(TimeoutError):
            asyncio.run(_ainvoke_con_tope(llm, [], {}, 0.05))

    def test_no_corta_la_llamada_que_entra_en_el_tope(self):
        from bot.flow.graph import _ainvoke_con_tope
        llm = self._llm_que_tarda(0)
        ai = asyncio.run(_ainvoke_con_tope(llm, [], {}, 5))
        self.assertEqual(ai.content, '{"mensaje": "ok"}')

    def test_un_corte_por_tiempo_se_trata_como_transitorio_y_se_reintenta(self):
        # Es el comportamiento que hace que el tope sirva: si el corte se
        # tratara como error permanente, el contacto quedaria sin respuesta
        # igual, que es justo lo que se quiere evitar.
        from bot.flow.graph import _es_error_permanente
        self.assertFalse(_es_error_permanente(TimeoutError("paso el tope")))

    def test_el_reintento_corre_despues_de_un_corte_y_puede_tener_exito(self):
        from bot.flow.graph import _ainvoke_messages_with_retry
        lento = self._llm_que_tarda(5)
        llamadas = {"n": 0}
        async def _mixto(*a, **kw):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                return await lento.ainvoke()
            return _ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": "a la segunda"})])
        llm = MagicMock(); llm.ainvoke = _mixto
        with patch("bot.flow.graph.CallbackHandler", MagicMock()), \
             patch("bot.flow.graph._BACKOFF_TRANSITORIO_SEGUNDOS", 0):
            ai = asyncio.run(_ainvoke_messages_with_retry(
                llm, [], label="test", tope_segundos=0.05))
        self.assertEqual(llamadas["n"], 2)
        self.assertEqual(respuesta_de_tool_calls(ai, "ventas")["mensaje"], "a la segunda")

    def test_el_presupuesto_total_queda_bajo_el_timeout_de_gunicorn(self):
        # 3 intentos x tope + 2 backoffs < 120s (gunicorn --timeout 120). Si
        # alguien sube el tope sin mirar esto, un turno lento pasa a ser un
        # worker muerto en vez de un fallback.
        from bot.flow.graph import _TIMEOUT_LLM_SEGUNDOS, _BACKOFF_TRANSITORIO_SEGUNDOS
        peor_caso = _TIMEOUT_LLM_SEGUNDOS * 3 + _BACKOFF_TRANSITORIO_SEGUNDOS * 2
        self.assertLess(peor_caso, 120, f"el peor caso son {peor_caso}s y gunicorn corta a los 120s")

    def test_el_ruteo_tiene_un_tope_mas_corto_que_el_conversacional(self):
        from bot.flow.graph import _TIMEOUT_LLM_SEGUNDOS, _TIMEOUT_RUTEO_SEGUNDOS
        self.assertLess(_TIMEOUT_RUTEO_SEGUNDOS, _TIMEOUT_LLM_SEGUNDOS)


class ProsaComoRespuestaTest(SimpleTestCase):
    """La prosa del especialista ya no gasta un reintento: ES la respuesta.

    Este es el test de la ganancia de la tarea 3: el reintento por formato
    costaba el 24% de las llamadas de respuesta de `ventas` (hasta 64% cuando
    el resultado de la tool venia bueno) porque le pediamos al modelo que su
    respuesta viajara por una tool. Ver el spec.
    """

    def test_la_prosa_utilizable_no_gasta_reintento(self):
        from bot.flow.graph import _ainvoke_tools_json_with_retry
        llm = AsyncMock()
        llm.ainvoke.return_value = _ai(
            content="La garantía de los usados es de 6 meses o 10.000 km.")
        with patch("bot.flow.graph.CallbackHandler", MagicMock()):
            asyncio.run(_ainvoke_tools_json_with_retry(
                llm, [], label="test", nombre_agente="ventas"))
        self.assertEqual(llm.ainvoke.call_count, 1)

    def test_la_prosa_contaminada_si_reintenta(self):
        from bot.flow.graph import _ainvoke_tools_json_with_retry
        llm = AsyncMock()
        llm.ainvoke.side_effect = [
            _ai(content=" Todos los demás campos son opcionales."),
            _ai([_tool_call(NOMBRE_TOOL_RESPUESTA, {"mensaje": "ahora sí, la garantía es de 6 meses"})]),
        ]
        with patch("bot.flow.graph.CallbackHandler", MagicMock()), \
             patch("bot.flow.graph._BACKOFF_TRANSITORIO_SEGUNDOS", 0):
            asyncio.run(_ainvoke_tools_json_with_retry(
                llm, [], label="test", nombre_agente="ventas"))
        self.assertEqual(llm.ainvoke.call_count, 2)


class BloqueContratoEnModoProsaTest(SimpleTestCase):
    """El prompt deja de pedir el contrato de `responder`.

    Mientras el prompt dijera "llama SIEMPRE a responder", el modelo seguia
    restateando esa instruccion en `content`: 45 de 84 llamadas medidas en
    Langfuse. Ver el spec del 2026-09-03.
    """

    def test_pide_prosa_y_no_menciona_la_tool_responder(self):
        bloque = bloque_contrato_respuesta("ventas", modo="prosa")
        self.assertNotIn("responder", bloque)
        self.assertNotIn("JSON", bloque)

    def test_no_pide_ningun_campo_de_metadatos(self):
        # Los saca el extractor: pedirlos aca era lo que hacia que el modelo
        # los restateara en su respuesta al cliente.
        bloque = bloque_contrato_respuesta("ventas", modo="prosa")
        for campo in ("intent", "lead_class", "stage", "modelo_imagen",
                      "extracted_data", "handoff"):
            self.assertNotIn(campo, bloque)

    def test_sigue_pidiendo_que_las_acciones_vayan_por_tools(self):
        bloque = bloque_contrato_respuesta("ventas", modo="prosa")
        self.assertIn("herramienta", bloque.lower())

    def test_el_modo_tool_sigue_existiendo_para_compatibilidad(self):
        # spec R6: un CustomSpecialist de BD que pida el JSON a mano sigue
        # funcionando.
        bloque = bloque_contrato_respuesta("ventas", modo="tool")
        self.assertIn("responder", bloque)

    def test_el_default_es_prosa(self):
        self.assertEqual(bloque_contrato_respuesta("ventas"),
                         bloque_contrato_respuesta("ventas", modo="prosa"))


class SinLogsDeErrorEnElCaminoSanoTest(SimpleTestCase):
    """Un turno que responde en prosa NO debe dejar errores en el log.

    _parse_json_response loguea un logger.error con el texto completo cuando no
    parsea, y desde el refactor no parsear es lo NORMAL. Si la prosa se
    chequeara despues del JSON, cada turno sano dejaria 2-3 errores con la
    respuesta del cliente adentro -- ruido que taparia un problema real.
    """

    def test_la_prosa_no_pasa_por_el_parser_de_json(self):
        from bot.flow.graph import _salida_utilizable
        with patch("bot.flow.graph._parse_json_response") as mock_parse:
            self.assertTrue(_salida_utilizable(
                _ai(content="La garantía de los usados es de 6 meses."), "ventas"))
        mock_parse.assert_not_called()

    def test_lo_que_no_es_prosa_si_pasa_por_el_parser(self):
        from bot.flow.graph import _salida_utilizable
        with patch("bot.flow.graph._parse_json_response", return_value={"mensaje": "x"}) as mock_parse:
            self.assertTrue(_salida_utilizable(_ai(content='{"mensaje": "x"}'), "ventas"))
        mock_parse.assert_called_once()
