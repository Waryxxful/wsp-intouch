from bot.business import registrar_no_contactar, registrar_consentimiento, buscar_sucursales_cercanas
# Tools/logica de negocio de ventas (catalogo, financiamiento, leads) viven en
# bot.business.ventas junto al resto de tools por dominio (agendamiento.py,
# catalogo.py, compliance.py) -- este modulo re-exporta lo que ya usaban
# callers existentes (tests, bot/simulator/code_evaluators.py) para no
# tener que tocarlos.
from bot.business.ventas import (
    simular_financiamiento, simular_por_cuota, crear_lead, comparar_vehiculos,
    consultar_especificaciones_vehiculo, enviar_ficha_tecnica,
    _buscar_en_catalogo, _vehiculo_a_dict, _resolver_precio_catalogo,
    _simular_financiamiento_impl, _crear_lead_impl,
    _consultar_especificaciones_vehiculo_impl, _comparar_vehiculos_impl,
)
# Cavem: el stock real es VehiculoUsado (planilla), no VehiculoCatalogo (scraper).
from bot.business.usados import (
    buscar_vehiculos, consultar_ficha_vehiculo, comparar_vehiculos_usados,
)
# registrar_datos_lead se sigue importando (y re-exportando) aunque ya no se
# bindee: bot/simulator/ y los tests la usan por este modulo.
from bot.business.prospeccion import registrar_datos_lead, registrar_parte_pago
from bot.rag.tool import consultar_base_conocimiento

from ._common import bloque_nombre_contacto, bloque_numero_contacto, bloque_ya_saludado, bloque_fecha_actual, bloque_sucursal_unica, bloque_datos_del_lead
from bot.flow.respuesta import bloque_contrato_respuesta

# buscar_sucursales_cercanas se agrego porque "ventas" no la tenia -- el
# especialista no podia responder "en que sucursal puedo probar el auto?"
# con datos reales (18 Sucursal en la BD) y derivaba todo a un ejecutivo
# humano, generando quejas reales del cliente (hallazgo en conversacion de
# prueba, ver docs/PENDIENTES.md). faq/agendamiento ya la tenian.
# Cavem: se reemplazan las tres tools que leian VehiculoCatalogo (salida del
# scraper, que en este bot queda sin fuentes) por sus equivalentes sobre
# VehiculoUsado, y se suman las dos nuevas del docx: buscar_vehiculos (S6
# busqueda por atributos) y simular_por_cuota (S8/S24 cuota objetivo).
# enviar_ficha_tecnica NO se incluye: resuelve un PDF real desde el catalogo
# scrapeado, y los links de la planilla de Cavem son ficticios (demo.valten.cl)
# -- mandarlos por WhatsApp le entregaria al cliente una URL que no abre.
# El link de ficha igual viaja dentro de consultar_ficha_vehiculo, como dato.
# registrar_datos_lead NO esta en esta lista, y es el cambio de 32.a
# (docs/PENDIENTES.md, medido el 2026-09-07 sobre 324 turnos de cavem):
# el especialista la pedia en una SEGUNDA ronda -- una llamada entera al LLM,
# 4,53s de mediana -- en el 17,3% de los turnos, porque buscaba primero y
# registraba despues con el resultado en la mano. Los mismos campos se capturan
# ahora fuera de la latencia percibida: por el extractor cuando la respuesta
# sale en prosa, y por el argumento `lead` de `responder` cuando sale por tool.
# La tool sigue existiendo (bot/business/prospeccion.py) para el simulador y
# para los tests; lo que se saco es su bindeo al especialista.
#
# registrar_parte_pago SI se queda: escribe VehiculoPartePago con campos que el
# extractor no modela (patente, km, estado, deuda) y dedup por marca_modelo, y
# de paso sigue escribiendo el lead por su llamada interna a
# _registrar_datos_lead_impl.
_TOOLS_VENTAS = [buscar_vehiculos, consultar_ficha_vehiculo, comparar_vehiculos_usados,
                 simular_financiamiento, simular_por_cuota,
                 registrar_parte_pago,
                 registrar_no_contactar, registrar_consentimiento,
                 consultar_base_conocimiento, buscar_sucursales_cercanas]


class CustomPromptAgent:
    """Especialista puramente conversacional definido desde el panel (sin
    codigo, sin acciones de negocio) -- ver CustomSpecialist en bot/models.py.
    Una instancia envuelve una fila ya cargada de la BD.

    Excepcion puntual: el slug "ventas" gana los tools de _TOOLS_VENTAS (ver
    business_actions()) -- el resto de especialistas personalizados que se
    creen desde el panel siguen siendo 100% conversacionales, sin
    generalizar estas capacidades a todos (decision explicita, ver
    docs/superpowers/specs/2026-08-04-ventas-conocimiento-y-financiamiento-design.md)."""

    def __init__(self, row):
        from bot.models import get_active_prompt
        self.name = row.slug
        self.descripcion = row.descripcion
        self._prompt = get_active_prompt(f"custom:{row.slug}")

    def effective_prompt(self) -> str:
        return self._prompt

    def build_system_prompt(self, state: dict, effective_prompt: str) -> str:
        """Reemplaza a build_prompt() -- ya no incluye "Historial reciente"/
        "Mensaje actual" (eso lo arma _construir_mensajes en graph.py como
        mensajes reales) ni el "Resultado de la ultima consulta de negocio"
        en texto (eso ahora viaja como ToolMessage real, ver Task 2/7)."""
        flow_data = state.get("flow_data") or {}
        bloque_flow_data = f"\n\nDatos ya conocidos de este contacto (no los vuelvas a pedir si ya están acá, salvo que el cliente los cambie): {flow_data}" if flow_data else ""
        # El contrato de salida ya no se escribe como un JSON literal aca: es la
        # tool `responder`, y su bloque de prompt se genera desde el mismo lugar
        # que declara los campos permitidos por especialista (bot/flow/respuesta.py),
        # asi que prompt y filtro no pueden desincronizarse. Los campos extra de
        # "ventas" (intent, modelo_imagen, lead_class, stage) viven ahora en
        # CAMPOS_EXTRA_POR_AGENTE.
        return (
            f"{bloque_fecha_actual()}{bloque_sucursal_unica()}"
            f"{effective_prompt}{bloque_nombre_contacto(state)}{bloque_numero_contacto(state)}"
            f"{bloque_flow_data}{bloque_datos_del_lead(state)}{bloque_ya_saludado(state)}"
            f"{bloque_contrato_respuesta(self.name)}"
        )

    def business_actions(self) -> list:
        return _TOOLS_VENTAS if self.name == "ventas" else []
