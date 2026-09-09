from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class CampaignRule:
    default_agent: str
    override: Optional[Callable[[dict], Optional[str]]] = None


# Definicion de las reglas de este bot. PRE_ROUTING_RULES (abajo) es una copia
# MUTABLE que varios tests vacian con .clear() para aislarse; sin esta constante
# aparte no habia forma de volver a dejarla como estaba, y un test que la vaciaba
# hacia fallar a otro que corriera despues en la misma suite -- pasaba
# aislado y fallaba en conjunto, que es la clase de rojo mas cara de diagnosticar.
REGLAS_POR_DEFECTO: dict[str, CampaignRule] = {
    "recordatorio_24h": CampaignRule(default_agent="confirmacion"),
    # Campanas comerciales de Cavem (docx S12/S13): quien responde QUIERO,
    # RENOVAR o al seguimiento entra directo al especialista de ventas.
    "cyber_auto_demo": CampaignRule(default_agent="ventas"),
    "renueva_tu_auto": CampaignRule(default_agent="ventas"),
    "seguimiento_vehiculo": CampaignRule(default_agent="ventas"),
    # La campana de servicio tecnico entra a agendamiento, que es quien sabe
    # consultar disponibilidad y reservar una hora de taller.
    "servicio_tecnico_mantencion": CampaignRule(default_agent="agendamiento"),
}


PRE_ROUTING_RULES: dict[str, CampaignRule] = dict(REGLAS_POR_DEFECTO)


def restaurar_reglas_por_defecto() -> None:
    """Devuelve PRE_ROUTING_RULES a su contenido original. Para tests que la
    vacian, y para cualquiera que corra despues de ellos."""
    PRE_ROUTING_RULES.clear()
    PRE_ROUTING_RULES.update(REGLAS_POR_DEFECTO)


def resolve_agent_for_campaign(campaign_hint: str | None, state: dict) -> str | None:
    """Regla deterministica campaign_type -> agente, evaluada ANTES del LLM
    del supervisor. Devuelve None si no hay campaign_hint o no matchea
    ninguna regla — en ese caso el supervisor cae al flujo normal (LLM +
    ventana de contexto)."""
    if not campaign_hint:
        return None
    rule = PRE_ROUTING_RULES.get(campaign_hint)
    if rule is None:
        return None
    if rule.override:
        overridden = rule.override(state)
        if overridden:
            return overridden
    return rule.default_agent
