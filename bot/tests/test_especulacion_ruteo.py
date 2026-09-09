"""Ejecucion especulativa del ruteo (docs/PENDIENTES.md 29.a).

Se dispara gen#1 con el prompt de `ventas` EN PARALELO con el ruteo, y se
descarta si el ruteo eligio otro especialista. Ahorro medido: 0,81s de media
por turno (n=72, pareado turno a turno), con 81,9% de acierto de `ventas`.

POR QUE ES SEGURO, y es lo que lo distingue del prefetch de tool que se
descarto (29 / spec del 2026-09-03): el prompt de gen#1 NO depende del ruteo.
Ningun `bloque_*`, ni `build_system_prompt`, ni `_construir_mensajes` leen
`active_agent` (verificado por grep el 2026-09-08), y `supervisor_node`
escribe SOLO `active_agent` y `_dispatch`. O sea que la llamada especulativa es
identica a la que se iba a hacer igual: una especulacion equivocada se tira
entera y no entra ningun dato nuevo al contexto. El prefetch, en cambio, metia
al contexto la salida de un modelo chico con 36% de error.

Lo que estos tests cuidan, en orden de gravedad:

1. Que una especulacion DESCARTADA no ejecute ninguna tool (la barrera de las
   cuatro capas de 29.a). Se descarta un AIMessage, no se revierte una
   escritura.
2. Que una especulacion no se reuse en la SEGUNDA ronda de tools. Es el bug
   mas facil de introducir aca: `gen_especulativa` sobrevive en el state, y
   reusarla despues de un business_action haria que el especialista ignore el
   resultado de la tool que acaba de pedir.
3. Que la tarea SIEMPRE se cancele si no se cosecha -- incluido el camino de
   excepcion, que 29.a documenta como real (5 turnos del 02-09, el 403 de
   #16). Sin cancelar, el caso de fallo cuesta +1,6s en vez de 0.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from langchain.messages import AIMessage, ToolMessage

import bot.flow.graph as g


class _AgenteFalso:
    """Lo minimo que `_generar_del_especialista` y la interpretacion usan."""

    def __init__(self, name="ventas", tools=None):
        self.name = name
        self.descripcion = f"especialista {name}"
        self._tools = tools or []

    def effective_prompt(self):
        return f"Sos el especialista de {self.name}."

    def build_system_prompt(self, state, effective_prompt):
        return effective_prompt

    def business_actions(self):
        return list(self._tools)


def _estado(**extra):
    base = dict(
        wa_id="56900000001", text="quiero una camioneta", name="Tomas",
        messages=[], flow_state=None, flow_data={}, active_agent=None,
        campaign_hint=None, lead_class=None, stage=None, lead_faltante=[],
        response_text="", interactive_buttons=None, interactive_list=None,
        interactive_options=None, list_button_text=None, tool_messages=[],
        modelo_imagen=None, sucursal_direccion_ids=None, reply_to=None,
        _dispatch=None, ya_saludamos=False, gen_especulativa=None,
    )
    base.update(extra)
    return base


class _BaseEspeculacion(SimpleTestCase):
    """Corre `supervisor_node` con el ruteo y la generacion mockeados."""

    def _correr_supervisor(self, agente_elegido="ventas", registry=None,
                           ai_msg=None, ruteo_explota=False, setting="true"):
        self.generadas = []
        self.tareas = []
        registry = registry or {"ventas": _AgenteFalso("ventas"),
                                "faq": _AgenteFalso("faq")}

        async def _ruteo(llm, prompt, label="", **kw):
            if ruteo_explota:
                raise RuntimeError("403 prompt injection patterns detected")
            # Le da a la especulacion la chance de empezar a correr, para que
            # el caso de cancelacion sea real y no una tarea que nunca arranco.
            await asyncio.sleep(0)
            return json.dumps({"agente": agente_elegido,
                               "intencion_compra_real": False})

        async def _generar(state, agent, llm, tools, run_name="generate-response"):
            self.generadas.append({"agent": agent.name, "run_name": run_name})
            await asyncio.sleep(0.01)
            return ai_msg if ai_msg is not None else AIMessage(content="Hola, te ayudo.")

        crear_real = asyncio.create_task
        self.cancelaciones = []

        def _create_task(coro, **kw):
            tarea = crear_real(coro, **kw)
            # Se envuelve `cancel` para poder afirmar que la cancelacion se
            # PIDIO. Mirar `tarea.cancelled()` no alcanza: `cancel()` solo
            # marca la intencion y la transicion ocurre en la siguiente vuelta
            # del loop, que puede no llegar antes de que el test termine.
            cancel_real = tarea.cancel

            def _cancel(*a, **k):
                self.cancelaciones.append(tarea)
                return cancel_real(*a, **k)

            tarea.cancel = _cancel
            self.tareas.append(tarea)
            return tarea

        async def _main():
            with patch.object(g, "_ainvoke_with_retry", new=_ruteo), \
                 patch.object(g, "_get_routing_llm", new=AsyncMock(return_value=object())), \
                 patch.object(g, "_get_llm", new=AsyncMock(return_value=object())), \
                 patch.object(g, "build_agent_registry", return_value=registry), \
                 patch.object(g, "resolve_agent_for_campaign", return_value=None), \
                 patch("bot.models.get_setting", return_value=setting), \
                 patch.object(g, "_generar_del_especialista", new=_generar), \
                 patch.object(asyncio, "create_task", new=_create_task):
                return await g.supervisor_node(_estado())

        return asyncio.run(_main())


class EspeculacionAciertoTest(_BaseEspeculacion):
    def test_si_el_ruteo_elige_ventas_la_generacion_viaja_en_el_state(self):
        msg = AIMessage(content="Tengo 3 camionetas para ti.")
        salida = self._correr_supervisor(agente_elegido="ventas", ai_msg=msg)

        self.assertEqual(salida["active_agent"], "ventas")
        self.assertIs(salida["gen_especulativa"], msg)
        # Se genero UNA sola vez, con el prompt de ventas.
        self.assertEqual([x["agent"] for x in self.generadas], ["ventas"])

    def test_la_generacion_especulativa_tiene_run_name_propio(self):
        # Si compartiera `generate-response` rompe todo analisis de "la primera
        # generate-response del turno", del que ya dependen las mediciones de
        # latencia de este repo (29, 32).
        self._correr_supervisor(agente_elegido="ventas")
        self.assertEqual(self.generadas[0]["run_name"],
                         "generate-response-especulativa")


class EspeculacionFalloTest(_BaseEspeculacion):
    def test_si_el_ruteo_elige_otro_agente_la_generacion_no_viaja(self):
        salida = self._correr_supervisor(agente_elegido="faq")

        self.assertEqual(salida["active_agent"], "faq")
        # Capa 3 de la barrera: la clave NUNCA se escribe al estado.
        self.assertIsNone(salida.get("gen_especulativa"))

    def test_la_tarea_descartada_se_cancela(self):
        # Sin cancelar, el caso de fallo cuesta +1,6s en vez de 0 (29.a): la
        # especulacion seguiria generando hasta el final para nada.
        self._correr_supervisor(agente_elegido="faq")
        self.assertEqual(len(self.tareas), 1)
        self.assertEqual(self.cancelaciones, self.tareas)

    def test_una_especulacion_cosechada_no_se_cancela(self):
        # El complemento del test de arriba: si se cancelara despues de
        # cosecharla, el `finally` estaria pisando el caso bueno.
        self._correr_supervisor(agente_elegido="ventas")
        self.assertEqual(self.cancelaciones, [])

    def test_una_especulacion_descartada_no_deja_ninguna_tool_pedida(self):
        # LA barrera que importa: el AIMessage descartado pedia una tool de
        # ESCRITURA y no queda rastro de ella en el state. No hay nada que
        # revertir porque nunca se ejecuto -- llegar a business_action_node
        # exige que el runtime de LangGraph siga una arista, y una Task suelta
        # no puede.
        escritura = AIMessage(content="", tool_calls=[
            {"name": "registrar_parte_pago",
             "args": {"marca_modelo": "Toyota Corolla"}, "id": "call_x"},
        ])
        salida = self._correr_supervisor(agente_elegido="faq", ai_msg=escritura)

        self.assertIsNone(salida.get("gen_especulativa"))
        self.assertNotIn("tool_messages", salida)
        self.assertNotEqual(salida.get("_dispatch"), "business_action")


class EspeculacionKillSwitchTest(_BaseEspeculacion):
    def test_apagada_no_se_genera_nada(self):
        salida = self._correr_supervisor(agente_elegido="ventas", setting="false")

        self.assertEqual(self.generadas, [])
        self.assertEqual(self.tareas, [])
        self.assertIsNone(salida.get("gen_especulativa"))
        # Y el ruteo sigue funcionando igual.
        self.assertEqual(salida["active_agent"], "ventas")

    def test_sin_ventas_en_el_registro_no_se_especula(self):
        # Un bot cuyo registro no tiene "ventas" (otro cliente, o el
        # especialista borrado desde el panel) no puede especular con el.
        registry = {"faq": _AgenteFalso("faq")}
        salida = self._correr_supervisor(agente_elegido="faq", registry=registry)

        self.assertEqual(self.generadas, [])
        self.assertIsNone(salida.get("gen_especulativa"))


class EspeculacionExcepcionTest(_BaseEspeculacion):
    def test_una_excepcion_en_el_ruteo_cancela_la_tarea(self):
        # Camino real, no hipotetico: los 5 turnos del 02-09 en que el
        # supervisor moria con 403 (docs/PENDIENTES.md #16).
        with self.assertRaises(RuntimeError):
            self._correr_supervisor(ruteo_explota=True)

        self.assertEqual(len(self.tareas), 1)
        self.assertEqual(self.cancelaciones, self.tareas)


class EspeculacionConsumoTest(SimpleTestCase):
    """`specialist_node` usa la especulacion en vez de volver a llamar."""

    def _correr_specialist(self, state, ai_msg_nuevo=None):
        self.llamadas = []
        agent = _AgenteFalso("ventas")

        async def _generar(state, agent, llm, tools, run_name="generate-response"):
            self.llamadas.append(run_name)
            return ai_msg_nuevo or AIMessage(content="respuesta recien generada")

        async def _main():
            with patch.object(g, "build_agent_registry", return_value={"ventas": agent}), \
                 patch.object(g, "_get_llm", new=AsyncMock(return_value=object())), \
                 patch.object(g, "_generar_del_especialista", new=_generar):
                return await g.specialist_node(state)

        return asyncio.run(_main())

    def test_con_especulacion_no_se_vuelve_a_llamar_al_llm(self):
        msg = AIMessage(content="Tengo 3 camionetas para ti.")
        salida = self._correr_specialist(
            _estado(active_agent="ventas", gen_especulativa=msg))

        self.assertEqual(self.llamadas, [])  # el ahorro: cero llamadas nuevas
        self.assertEqual(salida["response_text"], "Tengo 3 camionetas para ti.")
        # Se limpia al consumirla, para que no pueda reusarse mas adelante.
        self.assertIsNone(salida.get("gen_especulativa"))

    def test_sin_especulacion_se_llama_normalmente(self):
        salida = self._correr_specialist(_estado(active_agent="ventas"))

        self.assertEqual(self.llamadas, ["generate-response"])
        self.assertEqual(salida["response_text"], "respuesta recien generada")

    def test_no_se_reusa_en_la_segunda_ronda_de_tools(self):
        """El bug mas facil de introducir aca.

        `gen_especulativa` sobrevive en el state. Si specialist_node la
        reusara despues de un business_action, el especialista contestaria
        ignorando el resultado de la tool que el mismo acaba de pedir -- o
        peor, volveria a pedirla, que es el loop del incidente de
        tool_choice=required (docs/PENDIENTES.md, 2026-08-25).
        """
        vieja = AIMessage(content="", tool_calls=[
            {"name": "buscar_vehiculos", "args": {}, "id": "call_1"}])
        state = _estado(
            active_agent="ventas", gen_especulativa=vieja,
            tool_messages=[
                vieja,
                ToolMessage(content=json.dumps({"ok": True}),
                            tool_call_id="call_1", name="buscar_vehiculos"),
            ],
        )
        salida = self._correr_specialist(state)

        # Se ignoro la especulacion vieja y se genero de nuevo.
        self.assertEqual(self.llamadas, ["generate-response"])
        self.assertEqual(salida["response_text"], "respuesta recien generada")
