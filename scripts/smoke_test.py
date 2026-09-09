#!/usr/bin/env python
"""Smoke test de arranque — correr a mano tras clonar el skeleton para un bot nuevo.
No es parte de un pipeline CI (ver spec 2026-07-10-wsp-bot-skeleton-design.md, seccion Testing)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("USE_SQLITE", "true")

import django  # noqa: E402
django.setup()  # noqa: E402

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402
from langchain.messages import AIMessage  # noqa: E402
from bot.flow.graph import get_flow_graph  # noqa: E402
from asgiref.sync import sync_to_async  # noqa: E402


async def main():
    print("[smoke] compilando el grafo...")
    graph = get_flow_graph()

    print("[smoke] invocando el grafo con un turno de prueba (LLM mockeado)...")
    # "agendamiento" ya migro (Task 9) a bind_tools/ainvoke -- ademas del mock
    # de _ainvoke_with_retry (que sigue cubriendo el paso del supervisor), hace
    # falta mockear _get_llm para que el paso del especialista no haga una
    # llamada real a Gemini (mismo patron que las Tasks 7/8/9 establecieron).
    llm_con_tools = AsyncMock()
    llm_con_tools.ainvoke.return_value = AIMessage(
        content='{"mensaje": "dale, para cuando?", "extracted_data": {}, "next_state": null, "handoff": false}',
        tool_calls=[],
    )
    llm_base = MagicMock()
    llm_base.bind_tools.return_value = llm_con_tools
    with patch(
        "bot.flow.graph._ainvoke_with_retry",
        return_value='{"agente": "agendamiento"}',
    ), patch("bot.flow.graph._get_llm", return_value=llm_base):
        state = {
            "wa_id": "56900000000", "text": "hola quiero agendar", "name": "Smoke Test",
            "messages": [], "flow_state": "IDLE", "flow_data": {},
            "active_agent": None, "campaign_hint": None,
            "response_text": "", "interactive_buttons": None, "interactive_list": None,
            "interactive_options": None, "list_button_text": None,
            "pending_tool_call": None, "business_result": None,
            "reply_to": None, "_dispatch": None,
        }
        result = await graph.ainvoke(state)

    assert result["active_agent"] == "agendamiento", f"esperaba agendamiento, llego {result['active_agent']}"
    print("[smoke] OK — el grafo enruta al especialista correcto.")

    print("[smoke] probando el catalogo real (ORM)...")
    from django.conf import settings
    from bot.business import _listar_catalogo_impl
    from bot.models import Servicio, Sucursal

    # get_or_create: este script se corre a mano, repetidas veces, contra el
    # db.sqlite3 real del repo — sin esto cada corrida deja una fila
    # duplicada acumulandose para siempre. `cliente` explicito en el lookup
    # (no solo en defaults) para que la fila quede del CLIENTE_ACTIVO real
    # del proceso -- el manager filtrado (_ClienteActivoManager) solo protege
    # lecturas, get_or_create() sin esto cae en silencio al default del
    # campo del modelo ("renault") sin importar bajo que cliente corra esto.
    await sync_to_async(Servicio.objects.get_or_create)(
        nombre="Test Servicio", cliente=settings.CLIENTE_ACTIVO, defaults={"duracion_min": 30},
    )
    await sync_to_async(Sucursal.objects.get_or_create)(
        nombre="Test Sucursal", cliente=settings.CLIENTE_ACTIVO, defaults={"horario_texto": "9:00-18:00"},
    )

    catalogo = await sync_to_async(_listar_catalogo_impl)()
    assert len(catalogo["servicios"]) > 0, "esperaba al menos un servicio en catalogo"
    assert len(catalogo["sucursales"]) > 0, "esperaba al menos una sucursal en catalogo"
    print("[smoke] OK — el catalogo se lista correctamente.")

    print("[smoke] TODO OK.")


if __name__ == "__main__":
    asyncio.run(main())
