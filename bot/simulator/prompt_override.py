"""Permite correr el simulador contra un prompt candidato SIN publicarlo.

Sin esto, la unica forma de evaluar un prompt nuevo era activarlo con
save_prompt_version -- es decir, contra produccion, que sirve trafico real
(ver docs/superpowers/specs/2026-09-01-recorte-prompts-ventas-design.md).
"""
from contextlib import contextmanager
from unittest.mock import patch

import bot.models


@contextmanager
def override_prompts(overrides):
    """Mientras dura el bloque, get_active_prompt devuelve el texto candidato
    para los agentes presentes en `overrides` y el prompt activo real para
    el resto. No crea, activa ni desactiva ninguna fila de PromptVersion.

    patch() en codigo de produccion es a proposito: es el patron ya usado en
    este mismo modulo (bot/simulator/runner.py::correr_escenario lo usa para
    el cliente de WhatsApp).
    """
    if not overrides:
        yield
        return

    real = bot.models.get_active_prompt

    def _con_override(agente: str) -> str:
        if agente in overrides:
            return overrides[agente]
        return real(agente)

    with patch("bot.models.get_active_prompt", _con_override):
        yield
