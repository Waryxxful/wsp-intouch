from datetime import timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone
from langchain.tools import ToolRuntime, tool

from bot.models import (
    Conversation, Incident, registrar_optout,
    registrar_consentimiento as _registrar_consentimiento_db,
    crear_caso as _crear_caso_db,
)

_CASO_DEDUP_VENTANA = timedelta(minutes=5)


def _registrar_no_contactar_impl(wa_id: str = "", motivo: str = "") -> dict:
    if not wa_id:
        return {"ok": False, "motivo": "falta wa_id."}
    registrar_optout(wa_id, motivo)
    return {"ok": True}


@tool(parse_docstring=True)
async def registrar_no_contactar(motivo: str, runtime: ToolRuntime) -> dict:
    """Registra que el contacto de esta conversación no quiere volver a ser
    contactado por este canal (opt-out). Usar SOLO cuando lo pida
    explícitamente (ej. "no me escriban más", "sácame de la lista", "no
    quiero mensajes comerciales"). No pidas ni inventes un número de
    contacto -- el sistema ya sabe con quién está hablando.

    Args:
        motivo: resumen breve de por qué pidió no ser contactado, a partir de la conversación
    """
    # wa_id inyectado por el framework via ToolRuntime -- nunca visible ni
    # controlable por el LLM (a diferencia de "contacto" en agendar_hora,
    # que hoy si depende de que el LLM lo pase). Es un dato de compliance.
    return await sync_to_async(_registrar_no_contactar_impl, thread_sensitive=True)(
        wa_id=runtime.state.get("wa_id", ""), motivo=motivo,
    )


def _registrar_consentimiento_impl(wa_id: str, otorgado: bool, motivo: str = "") -> dict:
    if not wa_id:
        return {"ok": False, "motivo": "falta wa_id."}
    _registrar_consentimiento_db(wa_id, otorgado, motivo)
    return {"ok": True}


@tool(parse_docstring=True)
async def registrar_consentimiento(otorgado: bool, motivo: str, runtime: ToolRuntime) -> dict:
    """Registra si el contacto dio o revoco su consentimiento para que usemos
    sus datos con fines comerciales/de seguimiento (marketing). Usar SOLO
    cuando el contacto se pronuncia explícitamente sobre esto -- no lo
    asumas ni lo infieras. Distinto de "registrar_no_contactar": esto es
    sobre uso de datos, no sobre dejar de recibir mensajes.

    Args:
        otorgado: true si acepta, false si lo rechaza
        motivo: resumen breve de que dijo el contacto, a partir de la conversación
    """
    return await sync_to_async(_registrar_consentimiento_impl, thread_sensitive=True)(
        wa_id=runtime.state.get("wa_id", ""), otorgado=otorgado, motivo=motivo,
    )


def _crear_caso_impl(wa_id: str, tipo: str, resumen: str) -> dict:
    if not wa_id:
        return {"ok": False, "motivo": "falta wa_id."}
    conversation = Conversation.objects.filter(wa_id=wa_id).first()
    if conversation is not None:
        # bot/flow/graph.py::_filtrar_acciones_repetidas dedupea por args
        # EXACTOS -- un reintento del LLM en el mismo turno con "resumen"
        # reformulado ya no matchea ese dedup y llega hasta aca. Mismo
        # patron que _crear_lead_impl (ventas.py): identidad = conversacion
        # + tipo, "resumen" se ignora a proposito. kind="otro" agrupa
        # cualquier tipo invalido -- se compara tipo_original en Python para
        # no deduplicar entre dos tipos invalidos distintos que cayeron ahi.
        validos = {c[0] for c in Incident.TIPO_CASO_CHOICES}
        kind = tipo if tipo in validos else "otro"
        candidatos = Incident.objects.filter(
            conversation=conversation, kind=kind,
            created_at__gte=timezone.now() - _CASO_DEDUP_VENTANA,
        ).order_by("-created_at")
        for candidato in candidatos:
            if kind != "otro" or candidato.context.get("tipo_original") == tipo:
                return {"ok": True, "caso_id": candidato.id}
    caso = _crear_caso_db(conversation, tipo, resumen)
    return {"ok": True, "caso_id": caso.id}


@tool(parse_docstring=True)
async def crear_caso(tipo: str, resumen: str, runtime: ToolRuntime) -> dict:
    """Crea un caso real para que una persona del área que corresponde lo pueda
    listar y tomar después -- no sólo leer el chat de WhatsApp. Úsala para las
    consultas que NO son comerciales: soporte de un servicio que el contacto ya
    tiene, postulaciones de empleo, ofertas de proveedores, reclamos, y
    cualquier solicitud sobre datos personales. Distinto de un handoff: el
    handoff deriva ESTA conversación a una persona ahora; crear_caso deja un
    registro estructurado que se puede usar junto con el handoff o sin él.

    Después de llamarla, dile al contacto con claridad que su consulta queda
    registrada para el área correspondiente, y no la trates como una
    oportunidad comercial.

    Args:
        tipo: soporte|empleo|proveedor|reclamo|datos_personales|otro
        resumen: resumen breve del caso con las palabras del contacto, incluidos los datos que ya te haya dado (empresa, correo, de qué servicio se trata)
    """
    return await sync_to_async(_crear_caso_impl, thread_sensitive=True)(
        wa_id=runtime.state.get("wa_id", ""), tipo=tipo, resumen=resumen,
    )
