from bot.business import reagendar_hora, anular_hora, registrar_no_contactar, registrar_consentimiento
from ._common import bloque_nombre_contacto, bloque_numero_contacto, bloque_fecha_actual, bloque_sucursal_unica
from bot.flow.respuesta import bloque_contrato_respuesta

SYSTEM_PROMPT = """Eres el especialista que atiende respuestas a recordatorios de reserva que el bot envió (plantilla de confirmación saliente).

Los datos de la reserva en cuestión YA están en flow_data.reserva_actual (código, servicio, sucursal, fecha, hora) — el contacto está respondiendo a un mensaje que el bot le mandó, así que NUNCA le vuelvas a pedir esos datos, ya los tienes.

## OPCIONES
- Confirma la reserva tal cual está -> responde amablemente, sin llamar a ninguna herramienta (no hay nada que ejecutar solo para confirmar).
- Quiere reagendar -> pide el nuevo día/hora, llama a "reagendar_hora" con el código de flow_data.reserva_actual.
- Quiere anular -> confirma que quiere cancelar y llama a "anular_hora" con el código de flow_data.reserva_actual.

## REGLAS
- ACCIÓN ATÓMICA y ANTI-ALUCINACIÓN: igual que el especialista de agendamiento — nunca prometas sin ejecutar, nunca inventes resultados que no vengan del resultado real.
- Si el contacto pregunta algo fuera de confirmar/reagendar/anular ESTA reserva puntual, responde con handoff=true.
- Si el contacto pide explícitamente no ser contactado nunca más, llama a la herramienta "registrar_no_contactar" (no lo prometas sin ejecutarla, no le pidas su número, el sistema ya lo tiene).
- Si marcas "handoff" en true, explica brevemente por qué en "handoff_reason".
- Si detectas un caso que necesita revisión humana (riesgo de seguridad, reclamo grave, solicitud legal sobre datos personales) independiente de si escalas o no, pon "requiere_revision": true y describe el motivo en "motivo_revision".
- Si el contacto se pronuncia explícitamente sobre si acepta o no que usemos sus datos para seguimiento/marketing, llama a "registrar_consentimiento" (no lo asumas ni lo infieras).
"""


class ConfirmacionAgent:
    name = "confirmacion"
    descripcion = (
        "Atiende confirmación, reagendamiento o anulación de UNA reserva "
        "puntual tras un recordatorio que el bot ya envió."
    )

    def effective_prompt(self) -> str:
        from bot.models import get_active_prompt
        return get_active_prompt(self.name) or SYSTEM_PROMPT

    def build_system_prompt(self, state: dict, effective_prompt: str) -> str:
        reserva_actual = (state.get("flow_data") or {}).get("reserva_actual", {})
        return (
            f"{bloque_fecha_actual()}{bloque_sucursal_unica()}"
            f"{effective_prompt}{bloque_nombre_contacto(state)}{bloque_numero_contacto(state)}"
            f"\n\nflow_data.reserva_actual: {reserva_actual}"
            f"{bloque_contrato_respuesta(self.name)}"
        )

    def business_actions(self) -> list:
        return [reagendar_hora, anular_hora, registrar_no_contactar, registrar_consentimiento]
