import re
import secrets
import string
from datetime import datetime, time, timedelta

from asgiref.sync import sync_to_async
from django.db import IntegrityError
from langchain.tools import ToolRuntime, tool

from bot.models import Servicio, Sucursal, Reserva

_HORARIO_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(?:-|a)\s*(\d{1,2}):(\d{2})")
_CODIGO_ALFABETO = string.ascii_uppercase + string.digits


def _parse_horario(horario_texto: str) -> tuple[time, time]:
    # ponytail: regex simple sobre el ultimo patron "HH:MM-HH:MM"/"HH:MM a HH:MM"
    # encontrado en el texto. Si el sitio scrapeado no lo sigue, cae al default
    # 9:00-18:00 — subir a un parser mas robusto solo si un caso real lo pide.
    match = _HORARIO_RE.search(horario_texto or "")
    if not match:
        return time(9, 0), time(18, 0)
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    return time(h1, m1), time(h2, m2)


def _generar_codigo() -> str:
    for _ in range(10):
        codigo = "".join(secrets.choice(_CODIGO_ALFABETO) for _ in range(6))
        if not Reserva.objects.filter(codigo=codigo).exists():
            return codigo
    raise RuntimeError("No se pudo generar un codigo de reserva unico")


def _consultar_disponibilidad_impl(servicio_id: int, sucursal_id: int, fecha: str) -> list:
    try:
        servicio = Servicio.objects.get(pk=servicio_id)
        sucursal = Sucursal.objects.get(pk=sucursal_id)
    except (Servicio.DoesNotExist, Sucursal.DoesNotExist):
        # El LLM puede pasar un id que no existe (alucinado o de un
        # catalogo viejo) — degradar a "sin horas" en vez de reventar
        # toda la conversacion con una excepcion sin atrapar.
        return []
    apertura, cierre = _parse_horario(sucursal.horario_texto)

    ocupadas = set(
        Reserva.objects.filter(sucursal_id=sucursal_id, fecha=fecha, estado="activa")
        .values_list("hora", flat=True)
    )

    slots = []
    cursor = datetime.combine(datetime.today(), apertura)
    fin = datetime.combine(datetime.today(), cierre)
    paso = timedelta(minutes=servicio.duracion_min)
    while cursor + paso <= fin:
        hora = cursor.time()
        if hora not in ocupadas:
            slots.append({"hora": hora.strftime("%H:%M")})
        cursor += paso
    return slots


def _agendar_hora_impl(servicio_id: int, sucursal_id: int, fecha: str, hora: str,
                        contacto: str, nombre: str = "", email: str = "",
                        vehiculo: str = "", patente: str = "",
                        vehiculo_anio: int = None, vehiculo_km: int = None) -> dict:
    if not Servicio.objects.filter(pk=servicio_id).exists() or not Sucursal.objects.filter(pk=sucursal_id).exists():
        return {"ok": False, "motivo": "Servicio o sucursal no encontrado en el catálogo."}
    if Reserva.objects.filter(sucursal_id=sucursal_id, fecha=fecha, hora=hora, estado="activa").exists():
        return {"ok": False, "motivo": "Ese horario ya no está disponible."}
    try:
        reserva = Reserva.objects.create(
            codigo=_generar_codigo(), contacto=contacto,
            cliente_nombre=nombre, cliente_email=email,
            vehiculo=vehiculo[:200], patente=patente[:12].upper(),
            vehiculo_anio=vehiculo_anio or None, vehiculo_km=vehiculo_km or None,
            servicio_id=servicio_id, sucursal_id=sucursal_id,
            fecha=fecha, hora=hora, estado="activa",
        )
    except IntegrityError:
        # Otra conversacion concurrente gano la carrera entre el .exists() de
        # arriba y este .create() -- el UniqueConstraint de Reserva lo frena
        # a nivel BD, se responde igual que un conflicto detectado a tiempo.
        return {"ok": False, "motivo": "Ese horario ya no está disponible."}
    except RuntimeError:
        # _generar_codigo() agota sus 10 intentos si no encuentra un codigo
        # unico libre (estadisticamente casi imposible, 36**6 combinaciones)
        # -- se degrada igual que el resto de los fallos de esta tool en vez
        # de dejar escapar la excepcion cruda hacia el ToolNode.
        return {"ok": False, "motivo": "No se pudo generar la reserva, intenta de nuevo."}
    return {"ok": True, "codigo": reserva.codigo}


def _buscar_reserva_impl(codigo: str) -> dict:
    try:
        r = Reserva.objects.select_related("servicio", "sucursal").get(codigo=codigo)
    except Reserva.DoesNotExist:
        return {"ok": False, "motivo": "No encontré esa reserva."}
    return {
        "ok": True, "codigo": r.codigo, "estado": r.estado,
        "servicio": r.servicio.nombre, "sucursal": r.sucursal.nombre,
        "fecha": r.fecha.isoformat(), "hora": r.hora.strftime("%H:%M"),
        # El vehiculo y la patente viajan de vuelta para que el bot pueda
        # confirmarle al cliente QUE auto tiene agendado, no solo cuando --
        # y para que el agente los muestre antes de reagendar o anular.
        "vehiculo": r.vehiculo, "patente": r.patente,
        "vehiculo_anio": r.vehiculo_anio, "vehiculo_km": r.vehiculo_km,
    }


def _reagendar_hora_impl(codigo: str, fecha: str, hora: str) -> dict:
    try:
        r = Reserva.objects.get(codigo=codigo, estado="activa")
    except Reserva.DoesNotExist:
        return {"ok": False, "motivo": "No encontré una reserva activa con ese código."}
    conflicto = Reserva.objects.filter(
        sucursal_id=r.sucursal_id, fecha=fecha, hora=hora, estado="activa",
    ).exclude(pk=r.pk).exists()
    if conflicto:
        return {"ok": False, "motivo": "Ese horario ya no está disponible."}
    r.fecha = fecha
    r.hora = hora
    try:
        r.save(update_fields=["fecha", "hora"])
    except IntegrityError:
        # Misma carrera que en _agendar_hora_impl, entre el chequeo de
        # conflicto de arriba y este save().
        return {"ok": False, "motivo": "Ese horario ya no está disponible."}
    return {"ok": True, "codigo": r.codigo}


def _anular_hora_impl(codigo: str) -> dict:
    try:
        r = Reserva.objects.get(codigo=codigo, estado="activa")
    except Reserva.DoesNotExist:
        return {"ok": False, "motivo": "No encontré una reserva activa con ese código."}
    r.estado = "cancelada"
    r.save(update_fields=["estado"])
    return {"ok": True, "codigo": r.codigo}


@tool(parse_docstring=True)
async def consultar_disponibilidad(servicio_id: int, sucursal_id: int, fecha: str) -> list:
    """Consulta las horas disponibles para un servicio en una sucursal, en una fecha dada.

    Args:
        servicio_id: id del servicio (obtenido de listar_catalogo)
        sucursal_id: id de la sucursal (obtenido de listar_catalogo)
        fecha: fecha en formato YYYY-MM-DD
    """
    return await sync_to_async(_consultar_disponibilidad_impl, thread_sensitive=True)(servicio_id, sucursal_id, fecha)


@tool(parse_docstring=True)
async def agendar_hora(servicio_id: int, sucursal_id: int, fecha: str, hora: str,
                        runtime: ToolRuntime, nombre: str = "", email: str = "",
                        vehiculo: str = "", patente: str = "",
                        vehiculo_anio: int = 0, vehiculo_km: int = 0) -> dict:
    """Agenda una hora para un servicio en una sucursal, a nombre de un contacto.

    Args:
        servicio_id: id del servicio
        sucursal_id: id de la sucursal
        fecha: fecha en formato YYYY-MM-DD
        hora: hora en formato HH:MM
        nombre: nombre de la persona que agenda
        email: email opcional de la persona que agenda
        vehiculo: marca y modelo del vehiculo que entra al taller (ej. "Hyundai Tucson")
        patente: patente del vehiculo, si el cliente la entrega o la manda en una foto
        vehiculo_anio: año del vehiculo
        vehiculo_km: kilometraje actual del vehiculo, el dato que define que mantención corresponde
    """
    # wa_id inyectado por el framework via ToolRuntime -- nunca visible ni
    # controlable por el LLM (mismo patron que registrar_no_contactar/
    # crear_caso en compliance.py). Antes "contacto" era un argumento normal
    # que el LLM pasaba libre, sin validarlo contra el wa_id real de la
    # conversacion -- podia alucinarlo o copiarlo mal y asociar la reserva
    # al numero equivocado.
    return await sync_to_async(_agendar_hora_impl, thread_sensitive=True)(
        servicio_id, sucursal_id, fecha, hora, runtime.state.get("wa_id", ""), nombre, email,
        vehiculo, patente, vehiculo_anio or None, vehiculo_km or None,
    )


@tool(parse_docstring=True)
async def buscar_reserva(codigo: str) -> dict:
    """Busca una reserva existente por su codigo.

    Args:
        codigo: codigo de la reserva
    """
    return await sync_to_async(_buscar_reserva_impl, thread_sensitive=True)(codigo)


@tool(parse_docstring=True)
async def reagendar_hora(codigo: str, fecha: str, hora: str) -> dict:
    """Cambia la fecha/hora de una reserva activa existente.

    Args:
        codigo: codigo de la reserva a reagendar
        fecha: nueva fecha en formato YYYY-MM-DD
        hora: nueva hora en formato HH:MM
    """
    return await sync_to_async(_reagendar_hora_impl, thread_sensitive=True)(codigo, fecha, hora)


@tool(parse_docstring=True)
async def anular_hora(codigo: str) -> dict:
    """Anula (cancela) una reserva activa existente.

    Args:
        codigo: codigo de la reserva a anular
    """
    return await sync_to_async(_anular_hora_impl, thread_sensitive=True)(codigo)
