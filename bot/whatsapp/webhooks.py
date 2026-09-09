import hashlib
import hmac
import json
import logging

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from langfuse import propagate_attributes

logger = logging.getLogger(__name__)


def _verify(request):
    if (
        request.GET.get("hub.mode") == "subscribe"
        and request.GET.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN
    ):
        return HttpResponse(request.GET.get("hub.challenge", ""))
    return HttpResponseForbidden("verify token invalido")


def _valid_signature(body: bytes, sig_header: str | None) -> bool:
    app_secret = settings.WHATSAPP_APP_SECRET
    if not app_secret:
        return True
    if not sig_header:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


_PREFIJO_MEDIA_FALLBACK = "[media_fallback:"
_TIPOS_MEDIA_NO_SOPORTADOS = {"video", "document", "sticker", "location"}


def _marcador_media_fallback(tipo: str) -> str:
    return f"{_PREFIJO_MEDIA_FALLBACK}{tipo}]"


def process_incoming_message(data: dict) -> list[tuple[str, str, str, str, str | None]]:
    from bot.flow.media_processing import resolve_audio_text, resolve_image_text
    from bot.models import Message
    from bot.whatsapp.client import get_wa_client
    from bot.whatsapp.cola_envio import encolar_acuse

    out = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            profile = value.get("contacts", [{}])[0].get("profile", {})
            name = profile.get("name", "")
            for msg in value.get("messages", []):
                wa_id = msg.get("from", "")
                msg_id = msg.get("id", "")
                # Idempotencia temprana: Meta reintenta el webhook y la
                # percepcion de media (descarga + Gemini) es lenta y cara,
                # asi que se descarta el duplicado ANTES de gastarla en vez
                # de esperar el chequeo aguas abajo en handle_message.
                if msg_id and Message.objects.filter(wa_msg_id=msg_id).exists():
                    continue
                msg_type = msg.get("type", "text")
                media_path = None
                if msg_type == "text":
                    text = msg.get("text", {}).get("body", "")
                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    button = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    text = button.get("id", "")
                elif msg_type == "button":
                    text = msg.get("button", {}).get("payload", "")
                elif msg_type == "image":
                    media_id = msg.get("image", {}).get("id", "")
                    mime_hint = msg.get("image", {}).get("mime_type", "")
                    # El acuse va ANTES de percibir, y esta es la razon: la
                    # percepcion (descarga desde Meta + LLM de vision) tarda
                    # 4,8-6,5s medidos, corre DENTRO de este request y ANTES de
                    # handle_message, que es donde vivia el unico acuse. O sea
                    # que quien manda una foto veia entre 5 y 6 segundos de
                    # silencio TOTAL -- sin doble check azul y sin
                    # "escribiendo..." -- antes de la primera senal de vida, y
                    # despues todavia le faltaba el turno completo.
                    #
                    # mostrar_escribiendo=True fijo, no condicional: con media
                    # SIEMPRE se va a hacer esperar. Meta descarta el indicador
                    # a los 25s o cuando respondemos, y percepcion + turno da
                    # ~16s, asi que entra. handle_message vuelve a mandar el
                    # suyo mas adelante y eso lo refresca; no molesta porque el
                    # acuse ya no bloquea (ver encolar_acuse).
                    encolar_acuse(get_wa_client(), msg_id, mostrar_escribiendo=True)
                    # session_id/user_id = wa_id, mismo criterio que _run_graph
                    # (handlers.py) -- resolve_image_text corre ANTES de que
                    # exista ese trace, asi que abre el suyo propio, pero
                    # agrupado bajo el mismo wa_id en Langfuse.
                    with propagate_attributes(session_id=wa_id, user_id=wa_id):
                        texto, media_path = resolve_image_text(media_id, mime_hint)
                    text = texto or _marcador_media_fallback("image")
                elif msg_type == "audio":
                    media_id = msg.get("audio", {}).get("id", "")
                    mime_hint = msg.get("audio", {}).get("mime_type", "")
                    # Mismo motivo que en la rama de imagen: la transcripcion
                    # tarda segundos y corre antes de cualquier acuse.
                    encolar_acuse(get_wa_client(), msg_id, mostrar_escribiendo=True)
                    with propagate_attributes(session_id=wa_id, user_id=wa_id):
                        texto, media_path = resolve_audio_text(media_id, mime_hint)
                    text = texto or _marcador_media_fallback("audio")
                elif msg_type in _TIPOS_MEDIA_NO_SOPORTADOS:
                    text = _marcador_media_fallback(msg_type)
                else:
                    text = ""
                out.append((wa_id, name, text, msg_id, media_path))
    return out


def _dispatch(data: dict):
    from bot.whatsapp.handlers import handle_message, handle_unsupported_media

    for wa_id, name, text, msg_id, media_path in process_incoming_message(data):
        if text.startswith(_PREFIJO_MEDIA_FALLBACK):
            tipo = text[len(_PREFIJO_MEDIA_FALLBACK):-1]
            async_to_sync(handle_unsupported_media)(wa_id, name, msg_id, tipo)
        else:
            async_to_sync(handle_message)(wa_id, name, text, msg_id, media_url=media_path)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    if request.method == "GET":
        return _verify(request)

    if not _valid_signature(request.body, request.headers.get("X-Hub-Signature-256")):
        return HttpResponseForbidden("firma invalida")

    data = json.loads(request.body or "{}")
    _dispatch(data)
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_http_methods(["POST"])
def internal_webhook(request):
    data = json.loads(request.body or "{}")
    _dispatch(data)
    return JsonResponse({"status": "ok"})
