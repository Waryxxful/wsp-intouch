# Barrel de re-exports: la logica real vive en los submodulos por dominio
# (agendamiento.py, catalogo.py, compliance.py) -- este modulo existe solo
# para que `from bot.business import <lo que sea>` siga funcionando sin
# tener que tocar cada caller (bot/flow/agents/*, tests, scripts/smoke_test.py)
# cuando se movio la logica fuera de este __init__.py (antes 331 lineas con
# los 3 dominios mezclados, ver docs/PENDIENTES.md).
from bot.business.agendamiento import (
    _parse_horario, _generar_codigo,
    _consultar_disponibilidad_impl, _agendar_hora_impl, _buscar_reserva_impl,
    _reagendar_hora_impl, _anular_hora_impl,
    consultar_disponibilidad, agendar_hora, buscar_reserva, reagendar_hora, anular_hora,
)
from bot.business.catalogo import (
    _distancia_km, _buscar_sucursales_cercanas_impl, _listar_catalogo_impl,
    listar_catalogo, buscar_sucursales_cercanas,
)
from bot.business.compliance import (
    _registrar_no_contactar_impl, _registrar_consentimiento_impl, _crear_caso_impl,
    registrar_no_contactar, registrar_consentimiento, crear_caso,
)
from bot.business.soluciones import (
    _consultar_solucion_impl, _listar_modelos_operacion_impl, _listar_soluciones_impl,
    consultar_solucion, listar_modelos_operacion, listar_soluciones,
)

__all__ = [
    "consultar_disponibilidad", "agendar_hora", "buscar_reserva", "reagendar_hora", "anular_hora",
    "listar_catalogo", "buscar_sucursales_cercanas",
    "registrar_no_contactar", "registrar_consentimiento", "crear_caso",
    "listar_soluciones", "consultar_solucion", "listar_modelos_operacion",
]
