import re
import unicodedata

from asgiref.sync import sync_to_async
from django.utils import timezone
from langchain.tools import ToolRuntime, tool

from bot.models import CampaignSend, EncuestaServicioTecnico, EncuestaVentaAutoNuevo


def _normalizar_texto(valor: str) -> str:
    v = str(valor).strip().lower()
    v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii")
    return v


def _extraer_entero_1_a_10(respuesta: str) -> int | None:
    """Extrae el primer numero entero de 1 a 2 digitos de la respuesta cruda
    del contacto (ej. "un 8", "8/10", "creo que 9" -> 8, 8, 9) en vez de
    exigir que el string completo sea solo un numero -- el prompt le pide al
    LLM que pase la respuesta cruda, no un numero ya limpio."""
    match = re.search(r"\d{1,2}", str(respuesta))
    if match is None:
        return None
    try:
        return int(match.group())
    except ValueError:
        return None


def _normalizar_si_no_no_se(valor: str) -> str | None:
    v = _normalizar_texto(valor)
    if v == "si":
        return "si"
    if v == "no":
        return "no"
    if v in ("no se", "nose"):
        return "no_se"
    return None


def _buscar_campaign_send_activo(wa_id: str, campaign_type: str) -> CampaignSend | None:
    return (
        CampaignSend.objects
        .filter(contacto=wa_id, campaign_type=campaign_type, respondido=False)
        .order_by("-enviado_at", "-id")
        .first()
    )


def _registrar_respuesta_encuesta_servicio_tecnico_impl(wa_id: str, numero_pregunta: int, respuesta: str) -> dict:
    if numero_pregunta not in (1, 2, 3, 4):
        return {"ok": False, "motivo": "numero_pregunta inválido, debe ser 1, 2, 3 o 4."}

    campaign_send = _buscar_campaign_send_activo(wa_id, "encuesta_servicio_tecnico")
    if campaign_send is None:
        return {"ok": False, "motivo": "no hay una encuesta de Servicio Técnico activa para este contacto."}

    # Validar ANTES de get_or_create -- si la respuesta es invalida, no debe
    # quedar ninguna fila creada en la BD (ni siquiera vacia).
    if numero_pregunta in (1, 2, 4):
        valor = _extraer_entero_1_a_10(respuesta)
        if valor is None or not 1 <= valor <= 10:
            return {"ok": False, "motivo": "la respuesta debe ser un número del 1 al 10."}
    else:  # numero_pregunta == 3
        normalizado = _normalizar_si_no_no_se(respuesta)
        if normalizado is None:
            return {"ok": False, "motivo": "la respuesta debe ser Sí, No o No sé."}

    encuesta, _ = EncuestaServicioTecnico.objects.get_or_create(campaign_send=campaign_send)

    if numero_pregunta in (1, 2, 4):
        campo = {1: "p1_satisfaccion_ejecutivo", 2: "p2_satisfaccion_visita", 4: "p4_recomendaria"}[numero_pregunta]
        setattr(encuesta, campo, valor)
    else:
        encuesta.p3_trabajos_correctos = normalizado

    encuesta_completa = numero_pregunta == 4
    if encuesta_completa:
        encuesta.completed_at = timezone.now()
    encuesta.save()

    if encuesta_completa:
        campaign_send.respondido = True
        campaign_send.save(update_fields=["respondido"])

    return {"ok": True, "pregunta_registrada": numero_pregunta, "encuesta_completa": encuesta_completa}


@tool(parse_docstring=True)
async def registrar_respuesta_encuesta_servicio_tecnico(numero_pregunta: int, respuesta: str, runtime: ToolRuntime) -> dict:
    """Registra la respuesta del contacto a UNA pregunta de la encuesta de satisfacción de Servicio Tecnico.

    Args:
        numero_pregunta: número de la pregunta que se está respondiendo, 1 a 4.
        respuesta: la respuesta del contacto tal cual la escribio -- para las preguntas 1, 2 y 4 debe ser un número del 1 al 10; para la pregunta 3, Si/No/No se.
    """
    return await sync_to_async(_registrar_respuesta_encuesta_servicio_tecnico_impl, thread_sensitive=True)(
        wa_id=runtime.state.get("wa_id", ""), numero_pregunta=numero_pregunta, respuesta=respuesta,
    )


_MOTIVOS_P2 = {"no_ofrecieron", "precio_tasacion", "vendio_particular", "otro"}
_MOTIVOS_P3 = {"no_presentaron", "no_quiso_o_sin_tiempo"}


def _normalizar_si_no(valor: str) -> str | None:
    v = _normalizar_texto(valor)
    if v == "si":
        return "si"
    if v == "no":
        return "no"
    return None


def _registrar_respuesta_encuesta_venta_auto_nuevo_impl(
    wa_id: str, numero_pregunta: int, respuesta: str,
    motivo_codigo: str = "", motivo_texto_libre: str = "", observaciones: str = "",
) -> dict:
    if numero_pregunta not in (1, 2, 3, 4, 5):
        return {"ok": False, "motivo": "numero_pregunta inválido, debe ser 1 a 5."}

    campaign_send = _buscar_campaign_send_activo(wa_id, "encuesta_venta_auto_nuevo")
    if campaign_send is None:
        return {"ok": False, "motivo": "no hay una encuesta de venta de auto nuevo activa para este contacto."}

    # Validar TODO antes de get_or_create -- si la respuesta es invalida, no
    # debe quedar ninguna fila creada en la BD (ni siquiera vacia). Por eso
    # esta funcion valida primero (sin tocar la BD) y recien despues, en un
    # segundo bloque, hace get_or_create + asigna los campos ya validados.
    if numero_pregunta == 1:
        valor = _extraer_entero_1_a_10(respuesta)
        if valor is None or not 1 <= valor <= 10:
            return {"ok": False, "motivo": "la respuesta debe ser un número del 1 al 10."}
        if valor <= 6 and not motivo_texto_libre.strip():
            return {"ok": False, "motivo": "falta el motivo (texto libre) para una nota de 6 o menos."}

    elif numero_pregunta == 2:
        normalizado = _normalizar_si_no(respuesta)
        if normalizado is None:
            return {"ok": False, "motivo": "la respuesta debe ser Sí o No."}
        if normalizado == "no":
            codigo = _normalizar_texto(motivo_codigo)
            if codigo not in _MOTIVOS_P2:
                return {"ok": False, "motivo": "motivo_codigo inválido para la pregunta 2."}
            if codigo == "otro" and not motivo_texto_libre.strip():
                return {"ok": False, "motivo": "falta especificar el 'otro motivo'."}

    elif numero_pregunta == 3:
        normalizado = _normalizar_si_no(respuesta)
        if normalizado is None:
            return {"ok": False, "motivo": "la respuesta debe ser Sí o No."}
        if normalizado == "no":
            codigo = _normalizar_texto(motivo_codigo)
            if codigo not in _MOTIVOS_P3:
                return {"ok": False, "motivo": "motivo_codigo inválido para la pregunta 3."}

    else:  # 4 o 5
        normalizado = _normalizar_si_no(respuesta)
        if normalizado is None:
            return {"ok": False, "motivo": "la respuesta debe ser Sí o No."}

    encuesta, _ = EncuestaVentaAutoNuevo.objects.get_or_create(campaign_send=campaign_send)

    if numero_pregunta == 1:
        encuesta.p1_nota_general = valor
        encuesta.p1_motivo_nota_baja = motivo_texto_libre.strip() if valor <= 6 else ""

    elif numero_pregunta == 2:
        if normalizado == "no":
            encuesta.p2_motivo_no = codigo
            encuesta.p2_motivo_otro_texto = motivo_texto_libre.strip() if codigo == "otro" else ""
        else:
            encuesta.p2_motivo_no = ""
            encuesta.p2_motivo_otro_texto = ""
        encuesta.p2_dejo_parte_pago = normalizado

    elif numero_pregunta == 3:
        if normalizado == "no":
            encuesta.p3_motivo_no = codigo
        else:
            encuesta.p3_motivo_no = ""
        encuesta.p3_firmo_checklist = normalizado
        if observaciones.strip():
            encuesta.p3_observaciones = observaciones.strip()

    else:  # 4 o 5
        campo = "p4_informaron_garantia" if numero_pregunta == 4 else "p5_informaron_mantenciones"
        setattr(encuesta, campo, normalizado)

    encuesta_completa = numero_pregunta == 5
    if encuesta_completa:
        encuesta.completed_at = timezone.now()
    encuesta.save()

    if encuesta_completa:
        campaign_send.respondido = True
        campaign_send.save(update_fields=["respondido"])

    return {"ok": True, "pregunta_registrada": numero_pregunta, "encuesta_completa": encuesta_completa}


@tool(parse_docstring=True)
async def registrar_respuesta_encuesta_venta_auto_nuevo(
    numero_pregunta: int, respuesta: str, runtime: ToolRuntime,
    motivo_codigo: str = "", motivo_texto_libre: str = "", observaciones: str = "",
) -> dict:
    """Registra la respuesta del contacto a UNA pregunta de la encuesta de satisfacción de venta de auto nuevo.

    Args:
        numero_pregunta: número de la pregunta que se está respondiendo, 1 a 5.
        respuesta: la respuesta principal tal cual la dio el contacto -- número 1-10 para la pregunta 1, Si/No para las preguntas 2 a 5.
        motivo_codigo: código del motivo elegido cuando la pregunta 2 (No) o 3 (No) lo piden -- ver las opciones exactas en el prompt del especialista, vacío si no aplica.
        motivo_texto_libre: texto libre del motivo -- usado en la pregunta 1 cuando la nota es 6 o menos, y en la pregunta 2 cuando motivo_codigo es "otro". Vacío si no aplica.
        observaciones: observación libre y opcional de la pregunta 3, independiente de la respuesta. Vacío si no aplica.
    """
    return await sync_to_async(_registrar_respuesta_encuesta_venta_auto_nuevo_impl, thread_sensitive=True)(
        wa_id=runtime.state.get("wa_id", ""), numero_pregunta=numero_pregunta, respuesta=respuesta,
        motivo_codigo=motivo_codigo, motivo_texto_libre=motivo_texto_libre, observaciones=observaciones,
    )
