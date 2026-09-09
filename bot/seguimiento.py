"""Seguimiento automatico de clientes que consultaron y no cerraron (docx S13).

La ventana NO es una constante: vive en Setting("followup_ventana_minutos") y
se edita desde el panel. En produccion son 1440 minutos (24 horas, lo que pide
el docx); para una demo se baja a 40 minutos y el seguimiento se ve ocurrir en
vivo sin esperar un dia.

Efecto lateral util de esos 40 minutos: la ventana de atencion al cliente de 24
horas de WhatsApp sigue abierta, asi que el mensaje sale como texto libre y no
necesita una plantilla aprobada por Meta. La plantilla solo hace falta para el
caso real de 24 horas o mas.
"""

import logging
import sys
import threading
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 5 * 60
_VENTANA_DEFAULT_MINUTOS = 1440  # 24 h (docx S13)
# Limite de la ventana de atencion al cliente de WhatsApp. Dentro de las 24 h
# desde el ultimo mensaje DEL CLIENTE se puede mandar texto libre; pasado eso,
# Meta solo acepta plantillas aprobadas.
_VENTANA_SERVICIO_WHATSAPP_HORAS = 24
_CAMPAIGN_TYPE = "seguimiento_vehiculo"
_TEMPLATE_SETTING = "seguimiento_vehiculo_template"

_started = False


def ventana_minutos() -> int:
    from bot.models import get_setting
    crudo = get_setting("followup_ventana_minutos", "")
    try:
        valor = int(crudo)
    except (TypeError, ValueError):
        return _VENTANA_DEFAULT_MINUTOS
    return valor if valor > 0 else _VENTANA_DEFAULT_MINUTOS


def _candidatas(ahora):
    """Conversaciones que quedaron con una consulta abierta y sin cerrar.

    Criterios, todos necesarios:
    - el ultimo mensaje lo escribio el cliente hace mas que la ventana (si el
      ultimo lo escribio el bot, el cliente todavia puede estar leyendo);
    - hay un lead con vehiculo de interes (sin eso no hay de que hacer
      seguimiento y el mensaje seria spam);
    - no esta en modo humano (un ejecutivo tomo el chat: el bot no interrumpe);
    - no pidio opt-out;
    - no se le mando ya un seguimiento (uno por conversacion, nunca insistir).
    """
    from bot.models import Conversation, CampaignSend, LeadComercial, esta_optout

    corte = ahora - timedelta(minutes=ventana_minutos())
    resultado = []
    for lead in LeadComercial.objects.exclude(vehiculo_interes="").select_related("conversation"):
        conv = lead.conversation
        if conv is None or conv.archived:
            continue
        if conv.updated_at > corte:
            continue
        if (conv.get_flow() or {}).get("modo") == "HUMAN":
            continue
        if conv.stage in ("cerrado", "handoff"):
            continue
        ultimo = conv.messages.order_by("-created_at").first()
        if ultimo is None or ultimo.role != "user":
            continue
        if esta_optout(conv.wa_id):
            continue
        if CampaignSend.objects.filter(contacto=conv.wa_id, campaign_type=_CAMPAIGN_TYPE).exists():
            continue
        resultado.append((conv, lead, ultimo))
    return resultado


def _dentro_de_ventana_de_servicio(ultimo_mensaje_cliente, ahora) -> bool:
    return ahora - ultimo_mensaje_cliente.created_at < timedelta(hours=_VENTANA_SERVICIO_WHATSAPP_HORAS)


def _texto_libre(conv, lead) -> str:
    nombre = (lead.nombre or conv.name or "").split(" ")[0].strip()
    saludo = f"Hola {nombre}" if nombre else "Hola"
    return (
        f"{saludo}, ¿cómo estás?\n\n"
        f"Estuvimos revisando juntos el {lead.vehiculo_interes} y quedaste con la consulta abierta.\n\n"
        "¿Quieres que te prepare una simulación de financiamiento con un pie aproximado, "
        "o prefieres que revisemos otras alternativas similares?"
    )


class SinPlantilla(Exception):
    """El contacto quedo fuera de la ventana de 24 h y no hay plantilla
    configurada. Lo levanta enviar_seguimiento para que tick() pueda contarlos
    y emitir UN aviso por corrida en vez de uno por contacto cada 5 minutos."""


def enviar_seguimiento(conv, lead, ultimo, ahora=None) -> bool:
    from bot.models import CampaignSend, get_setting
    from bot.whatsapp.client import get_wa_client

    ahora = ahora or timezone.now()
    plantilla = get_setting(_TEMPLATE_SETTING, "") or getattr(settings, "SEGUIMIENTO_TEMPLATE", "")
    en_ventana = _dentro_de_ventana_de_servicio(ultimo, ahora)

    if not en_ventana and not plantilla:
        # Intentar texto libre aca solo produce un rechazo de Meta y una fila
        # registrada como enviada que el cliente nunca recibio -- peor que no
        # mandar. Se avisa, pero agregado por corrida (ver tick).
        raise SinPlantilla(conv.wa_id)

    # Claim ANTES de mandar: la UniqueConstraint de CampaignSend hace que solo
    # un worker de gunicorn gane la fila. Si el envio falla, el claim se libera
    # abajo para que se reintente en el proximo tick -- si se dejara puesto, la
    # conversacion quedaria excluida para siempre de un seguimiento que nunca
    # recibio.
    _, creado = CampaignSend.objects.get_or_create(
        contacto=conv.wa_id, campaign_type=_CAMPAIGN_TYPE,
        defaults={"template": plantilla or "texto_libre", "conversation": conv},
    )
    if not creado:
        return False

    wa = get_wa_client()
    try:
        if en_ventana:
            enviado = wa.send_text(conv.wa_id, _texto_libre(conv, lead))
        else:
            nombre = (lead.nombre or conv.name or "").split(" ")[0].strip() or "hola"
            enviado = wa.send_template(conv.wa_id, plantilla, components=[{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": nombre},
                    {"type": "text", "text": lead.vehiculo_interes},
                ],
            }])
    except Exception:
        CampaignSend.objects.filter(
            contacto=conv.wa_id, campaign_type=_CAMPAIGN_TYPE).delete()
        raise

    if not enviado:
        CampaignSend.objects.filter(
            contacto=conv.wa_id, campaign_type=_CAMPAIGN_TYPE).delete()
        return False

    conv.stage = "seguimiento"
    conv.save(update_fields=["stage"])
    return True


def tick(ahora=None) -> int:
    ahora = ahora or timezone.now()
    enviados = 0
    sin_plantilla = 0
    for conv, lead, ultimo in _candidatas(ahora):
        try:
            enviados += bool(enviar_seguimiento(conv, lead, ultimo, ahora))
        except SinPlantilla:
            sin_plantilla += 1
        except Exception:
            logger.exception("[seguimiento] fallo el seguimiento de %s", conv.wa_id)
    if enviados:
        logger.info("[seguimiento] %d seguimientos enviados", enviados)
    if sin_plantilla:
        # UN aviso por corrida y no uno por contacto: estas conversaciones
        # vuelven a ser candidatas en cada tick (cada 5 minutos, para siempre),
        # asi que avisar por contacto ahoga el log sin agregar informacion.
        logger.warning(
            "[seguimiento] %d contactos esperan seguimiento pero quedaron fuera de la "
            "ventana de 24h y no hay plantilla configurada (Setting %r)",
            sin_plantilla, _TEMPLATE_SETTING,
        )
    return enviados


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:
            logger.exception("[seguimiento] tick fallo")
        threading.Event().wait(_CHECK_INTERVAL_SECONDS)


def start_scheduler() -> None:
    global _started
    if _started or "test" in sys.argv:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()
