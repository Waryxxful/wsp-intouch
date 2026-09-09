"""El único especialista del bot: el asesor comercial B2B.

Uno solo y no dos (spec §2.2). El prompt de origen ya le encarga al mismo
agente distinguir consulta comercial de soporte, empleo o proveedores, así que
la distinción vive en el prompt y se resuelve con `crear_caso`, no en el ruteo.
Beneficio lateral: con un único destino, el ruteo del supervisor es trivial.

El SYSTEM_PROMPT se lee del fixture de git para que no haya dos copias del
mismo texto que puedan divergir -- el fixture es lo que el `doctor` compara
contra el prompt activo en BD.
"""
from pathlib import Path

from bot.flow.respuesta import bloque_contrato_respuesta

from ._common import bloque_fecha_actual, bloque_nombre_contacto, bloque_numero_contacto

SYSTEM_PROMPT = (Path(__file__).resolve().parents[1].parent
                 / "fixtures" / "prompt_comercial.md").read_text(encoding="utf-8")


class ComercialAgent:
    name = "comercial"
    descripcion = (
        "Atiende consultas comerciales B2B sobre las soluciones de InTouch, "
        "diagnostica la necesidad, recomienda un enfoque preliminar y acuerda "
        "un siguiente paso. Es el único especialista: también deriva las "
        "consultas que no son comerciales."
    )

    def effective_prompt(self) -> str:
        from bot.models import get_active_prompt
        return get_active_prompt(self.name) or SYSTEM_PROMPT

    def build_system_prompt(self, state: dict, effective_prompt: str) -> str:
        flow_data = state.get("flow_data") or {}
        bloque_flow_data = (
            f"\n\nDatos que ya conoces de este contacto (no los vuelvas a pedir si ya "
            f"están acá, salvo que él los corrija): {flow_data}"
        ) if flow_data else ""
        # No se incluye bloque_sucursal_unica: InTouch no tiene sucursales, y un
        # bloque que habla de una sucursal inexistente es una invitación a
        # inventarla (biblia §III.3, ley 4).
        return (
            f"{bloque_fecha_actual()}"
            f"{effective_prompt}"
            f"{bloque_nombre_contacto(state)}{bloque_numero_contacto(state)}"
            f"{bloque_flow_data}"
            f"{bloque_contrato_respuesta(self.name)}"
        )

    def business_actions(self) -> list:
        from bot.business import (
            consultar_solucion, crear_caso, listar_modelos_operacion,
            listar_soluciones, registrar_consentimiento, registrar_no_contactar,
        )
        from bot.rag.tool import consultar_base_conocimiento
        return [
            listar_soluciones, consultar_solucion, listar_modelos_operacion,
            consultar_base_conocimiento, crear_caso,
            registrar_no_contactar, registrar_consentimiento,
        ]
