import asyncio
import logging

from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from openevals.simulators import run_multiturn_simulation
from unittest.mock import MagicMock, patch

from bot.flow.graph import _texto_de_respuesta
from bot.simulator.app_wrapper import create_app
from bot.simulator.code_evaluators import correr_evaluadores_de_codigo
from bot.simulator.judge import evaluar_conversacion
from bot.simulator.models import CorridaDePrueba, EscenarioDePrueba, ResultadoDeEscenario
from bot.simulator.prompt_override import override_prompts
from bot.simulator.simulated_user import crear_cliente_simulado

logger = logging.getLogger(__name__)

_UMBRAL_NUMERICO = 3.0

# `stopping_condition` de `run_multiturn_simulation`: deliberadamente
# conservador (Hallazgo 8 de la revision final). No existe ninguna frase de
# cierre fija en los prompts del bot (bot/flow/agents/*.py no scriptea
# ninguna despedida), asi que en vez de adivinar una frase del BOT, miramos
# al CLIENTE simulado (simulated_user.py: su objetivo esta definido en el
# escenario) -- si el mismo cliente ya dijo explicitamente algo tipo "eso es
# todo"/"gracias, nada mas", es una señal fuerte y de bajo riesgo de que
# seguir gastando turnos (y plata en LLM) no aporta nada. Se exige un minimo
# de turnos ya corridos para no cortar una conversacion real antes de que el
# bot alcance a hacer lo que el escenario quiere verificar.
_TURNOS_MINIMOS_ANTES_DE_CORTAR = 2
# "nos vemos" y "chao" se sacaron de esta lista (re-revision final, Item 2):
# un cliente simulado agendando un test drive puede perfectamente decir
# "perfecto, nos vemos el jueves" a mitad de conversacion, antes de llegar
# al intercambio de RUT/telefono que el escenario
# agendar-test-drive-y-crear-lead necesita verificar -- cortar ahi
# arruinaria justo ese escenario. Solo quedan frases que declaran
# explicitamente que ya no se necesita nada mas, mucho menos probables de
# aparecer a mitad de un tramite todavia en curso.
_FRASES_CIERRE_CLIENTE = (
    "eso es todo", "eso era todo", "eso seria todo", "eso sería todo",
    "nada mas por ahora", "nada más por ahora", "no necesito nada mas",
    "no necesito nada más", "gracias, eso es todo", "gracias, eso era todo",
    "muchas gracias, eso",
)


def _contenido_como_texto(mensaje: dict) -> str:
    """`mensaje["content"]` puede venir como lista de content blocks (ver
    bot/flow/graph.py::_texto_de_respuesta) -- delega ahi la normalizacion,
    pero preserva el separador " " (en vez de "") que este modulo siempre
    uso para el matching de frases legibles por humanos contra el cierre
    del cliente simulado."""
    return _texto_de_respuesta(mensaje.get("content", ""), separador=" ")


def _cliente_parece_satisfecho(trajectory: list[dict], *, turn_counter: int, **kwargs) -> bool:
    """Callable pasado como `stopping_condition` a `run_multiturn_simulation`
    (firma real confirmada contra el paquete `openevals` instalado: recibe
    la trayectoria completa como lista de dicts {"role", "content", ...} mas
    el turno actual como kwarg `turn_counter`, y debe devolver bool)."""
    if turn_counter < _TURNOS_MINIMOS_ANTES_DE_CORTAR:
        return False
    mensajes_cliente = [m for m in trajectory if m.get("role") == "user"]
    if not mensajes_cliente:
        return False
    ultimo = _contenido_como_texto(mensajes_cliente[-1]).lower()
    return any(frase in ultimo for frase in _FRASES_CIERRE_CLIENTE)


def _construir_transcript(turnos: list[dict]) -> str:
    """Incluye no solo el dialogo Cliente/Bot sino tambien las señales
    internas que los propios criterios del juez referencian: si se envio
    una imagen, que acciones de negocio se ejecutaron (una o varias, ver
    `bot.simulator.app_wrapper._acciones_de_tool_messages`) y si tuvieron
    exito, y el flow_data acumulado al cierre de la conversacion. Sin esto,
    criterios como "no_repregunta_dato_conocido" o cualquier criterio de
    escenario sobre imagenes/crear_lead son inverificables para el juez
    porque nunca ven esos datos."""
    lineas = []
    for turno in turnos:
        lineas.append(f"Cliente: {turno['cliente_dice']}")
        lineas.append(f"Bot: {turno['bot_responde']}")
        anotaciones = []
        if turno.get("imagen_enviada"):
            anotaciones.append(f"imagen enviada: {turno.get('modelo_imagen')}")
        for accion in turno.get("acciones", []):
            resultado = accion.get("resultado") or {}
            anotaciones.append(f"accion: {accion['nombre']} -> ok={resultado.get('ok')}")
        if anotaciones:
            lineas.append(f"[{', '.join(anotaciones)}]")
    if turnos:
        lineas.append(f"[flow_data final: {turnos[-1].get('flow_data') or {}}]")
    return "\n".join(lineas)


def _fallos_de_juez(resultado_juez: dict, nombres_esperados: list[str]) -> list[str]:
    """Traduce la respuesta del juez a una lista de mensajes de fallo, en la
    misma forma que fallos_de_codigo (str legible) -- para que
    ResultadoDeEscenario.fallos (bot/simulator/models.py) pueda persistir
    ambas fuentes juntas. Reemplaza al viejo _judge_paso (que solo devolvia
    un bool): ahora se necesita tambien EL DETALLE de que fallo, no solo si
    paso o no."""
    if not resultado_juez:
        return ["el juez no devolvio una respuesta valida"]
    fallos = []
    faltantes = [n for n in nombres_esperados if n not in resultado_juez]
    if faltantes:
        fallos.append(f"el juez no respondio {len(faltantes)} criterio(s) esperado(s): {faltantes}")
    for nombre, detalle in resultado_juez.items():
        try:
            valor = detalle.get("valor")
            razonamiento = detalle.get("razonamiento", "")
        except (AttributeError, TypeError):
            fallos.append(f"{nombre}: forma de respuesta del juez invalida: {detalle!r}")
            continue
        if isinstance(valor, bool):
            if not valor:
                fallos.append(f"{nombre}: {razonamiento or 'el juez marco este criterio en false'}")
            continue
        try:
            valor_numerico = float(valor)
        except (TypeError, ValueError):
            fallos.append(f"{nombre}: valor no numerico ni booleano: {valor!r}")
            continue
        if valor_numerico < _UMBRAL_NUMERICO:
            fallos.append(f"{nombre}: puntaje {valor_numerico} bajo el umbral ({_UMBRAL_NUMERICO}): {razonamiento}")
    return fallos


def correr_escenario(item_input: dict) -> dict:
    """Corre un escenario completo: simula la conversacion contra el bot
    real, corre los evaluadores de codigo, y (solo si ninguno fallo) el
    juez LLM. Ya NO reporta scores a Langfuse (antes asumia correr dentro
    del task de un Dataset Experiment, que traia su propio trace activo --
    esa forma de reportar se reemplazo por la persistencia en
    ResultadoDeEscenario, ver bot/simulator/runner.py::_guardar_resultado).
    El tracing normal por turno (via @observe en bot/flow/graph.py) sigue
    intacto, no depende de nada de esta funcion.

    Puede correr dentro de un thread de worker despachado via
    asyncio.to_thread (ver ejecutar_corrida, Hallazgo 1 de la revision
    final del simulador original) -- por eso el finally cierra
    explicitamente las conexiones de BD abiertas por ese thread al
    terminar, sin depender de que Django las limpie solo."""
    try:
        persona = item_input["persona"]
        objetivo = item_input["objetivo"]
        criterios_escenario = item_input.get("criterios", [])
        max_turns = item_input.get("max_turns", 12)
        prompts_override = item_input.get("prompts_override")

        # El override envuelve create_app a proposito: CustomPromptAgent lee
        # su prompt en __init__ (bot/flow/agents/custom.py:41-44), no por
        # turno -- si envolviera solo la simulacion, los agentes ya estarian
        # construidos con el prompt activo real.
        with override_prompts(prompts_override), \
                patch("bot.whatsapp.handlers.get_wa_client") as mock_get_wa_client:
            mock_wa = mock_get_wa_client.return_value
            mock_wa.send_text = MagicMock()
            mock_wa.send_image = MagicMock()
            mock_wa.mark_as_read = MagicMock()

            app, capturas = create_app(persona=persona, mock_wa=mock_wa)
            cliente = crear_cliente_simulado(persona=persona, objetivo=objetivo)
            run_multiturn_simulation(
                app=app, user=cliente, max_turns=max_turns,
                stopping_condition=_cliente_parece_satisfecho,
            )

        thread_id = next(iter(capturas), None)
        turnos = capturas.get(thread_id, [])

        if not turnos:
            # Una conversacion que nunca ocurrio (cero turnos capturados) NO
            # puede reportarse como "paso" -- ver Hallazgo 2 de la revision
            # original. Sin esto, correr_evaluadores_de_codigo([]) devuelve
            # [] (vacuamente verdadero) y el juez recibe un transcript vacio
            # y puede aprobarlo igual.
            return {
                "paso": False, "num_turnos": 0,
                "fallos_de_codigo": ["conversacion_vacia"], "fallos_de_juez": [],
                "juez": None, "turnos": [],
            }

        fallos_de_codigo = correr_evaluadores_de_codigo(turnos)

        resultado_juez = None
        fallos_de_juez = []
        if not fallos_de_codigo:
            resultado_juez, nombres_criterios_esperados = evaluar_conversacion(
                _construir_transcript(turnos), criterios_escenario,
            )
            fallos_de_juez = _fallos_de_juez(resultado_juez, nombres_criterios_esperados)
        else:
            logger.info(
                "[simulator] se salta el juez LLM: ya hubo %s fallo(s) de codigo", len(fallos_de_codigo),
            )

        return {
            "paso": not fallos_de_codigo and not fallos_de_juez,
            "num_turnos": len(turnos),
            "fallos_de_codigo": fallos_de_codigo,
            "fallos_de_juez": fallos_de_juez,
            "juez": resultado_juez,
            "turnos": turnos,
        }
    finally:
        connections.close_all()


def _guardar_resultado(corrida: CorridaDePrueba, escenario: EscenarioDePrueba) -> None:
    """Corre UN escenario y persiste su ResultadoDeEscenario de inmediato --
    no espera a que termine toda la corrida (spec seccion 2, punto 4).
    Corre en un thread real via asyncio.to_thread (ver ejecutar_corrida),
    igual que correr_escenario -- no asume tener un event loop activo
    encima."""
    item_input = {
        "nombre": escenario.nombre, "persona": escenario.persona, "objetivo": escenario.objetivo,
        "criterios": escenario.criterios, "max_turns": escenario.max_turns,
    }
    try:
        resultado = correr_escenario(item_input)
        ResultadoDeEscenario.objects.create(
            corrida=corrida, escenario=escenario, paso=resultado["paso"],
            fallos=resultado["fallos_de_codigo"] + resultado["fallos_de_juez"],
            transcript=_construir_transcript(resultado["turnos"]),
        )
    except Exception as exc:
        # Un escenario que revienta con una excepcion no controlada (ej. la
        # API real de Gemini falla) no debe interrumpir el resto de la
        # corrida -- spec seccion 7.
        logger.exception("[simulator] el escenario %r termino en una excepcion no controlada", escenario.nombre)
        ResultadoDeEscenario.objects.create(
            corrida=corrida, escenario=escenario, paso=False, fallos=[], transcript="", error=str(exc),
        )
    finally:
        # Toca actualizado_en (auto_now) en cada escenario -- es la señal
        # que el chequeo de staleness (Tarea 5) usa para saber si el hilo
        # en background sigue vivo.
        corrida.save(update_fields=["actualizado_en"])


async def _ejecutar_corrida_async(corrida: CorridaDePrueba, escenarios: list) -> None:
    for escenario in escenarios:
        # Hallazgo 1 de la revision original del simulador: correr_escenario
        # termina llamando async_to_sync(handle_message)(...) (via
        # app_wrapper.create_app). Si esta corutina lo llamara directo,
        # async_to_sync reventaria por correr en el mismo thread que este
        # event loop. asyncio.to_thread despacha a un thread real sin event
        # loop propio, donde async_to_sync si puede crear el suyo sin
        # problema. Secuencial a proposito: nunca dos escenarios en
        # paralelo (Restricciones Globales del spec, prioridad costo sobre
        # velocidad).
        await asyncio.to_thread(_guardar_resultado, corrida, escenario)


def iniciar_corrida(nombre_escenario: str = None, disparada_por: str = "consola") -> CorridaDePrueba:
    """Crea el registro CorridaDePrueba y devuelve de inmediato -- separado
    de ejecutar_corrida() para que la vista del panel pueda responder al
    operador sin esperar a que termine la corrida completa (puede ser
    varios escenarios, cada uno varios turnos reales contra Gemini). Mismo
    patron que bot/scraping/runner.py::start_scrape/execute_scrape.

    Lanza ValueError si el escenario pedido no existe, o si no hay ningun
    escenario activo para correr -- en ambos casos, SIN crear la
    CorridaDePrueba (spec seccion 7: una corrida que no puede ni empezar no
    debe dejar un registro vacio)."""
    if nombre_escenario and not EscenarioDePrueba.objects.filter(nombre=nombre_escenario).exists():
        raise ValueError(f"no existe un escenario llamado {nombre_escenario!r}")

    escenarios = EscenarioDePrueba.objects.filter(activo=True)
    if nombre_escenario:
        escenarios = escenarios.filter(nombre=nombre_escenario)
    if not escenarios.exists():
        raise ValueError("no hay ningun escenario activo para correr")

    return CorridaDePrueba.objects.create(disparada_por=disparada_por, nombre_escenario_filtro=nombre_escenario)


def ejecutar_corrida(corrida: CorridaDePrueba) -> None:
    """Hace el trabajo pesado (corre cada escenario activo -- o el unico
    filtrado por corrida.nombre_escenario_filtro -- de forma secuencial
    contra el bot real) sobre una CorridaDePrueba ya creada por
    iniciar_corrida(). Pensado para correr en un thread separado (vista del
    panel, admin_panel/views.py::api_test_run_trigger) o de forma
    sincronica en el hilo principal (management command
    test_bot_conversation), igual que execute_scrape."""
    escenarios = EscenarioDePrueba.objects.filter(activo=True)
    if corrida.nombre_escenario_filtro:
        escenarios = escenarios.filter(nombre=corrida.nombre_escenario_filtro)
    escenarios = list(escenarios)

    try:
        asyncio.run(_ejecutar_corrida_async(corrida, escenarios))
        corrida.estado = "completa"
    except Exception:
        logger.exception("[simulator] la corrida %s termino en un error no controlado", corrida.pk)
        corrida.estado = "error"
    finally:
        corrida.fecha_fin = timezone.now()
        corrida.save(update_fields=["estado", "fecha_fin", "actualizado_en"])
        # Corre siempre, complete o no cada escenario individual (spec
        # seccion 4) -- para que probar repetidamente desde el panel no
        # vaya acumulando conversaciones/leads de test.
        call_command("cleanup_test_conversations")
