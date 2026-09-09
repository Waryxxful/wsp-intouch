import logging
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_IMAGE_MIME_A_EXTENSION = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

# Mismo set de mime types que _AUDIO_MIME_A_FORMATO en media_processing.py,
# pero como extension de archivo en vez de token de formato de OpenRouter.
_AUDIO_MIME_A_EXTENSION = {
    "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/flac": "flac", "audio/x-flac": "flac",
    "audio/mp4": "m4a", "audio/x-m4a": "m4a",
}

_EXTENSION_POR_KIND = {"image": _IMAGE_MIME_A_EXTENSION, "audio": _AUDIO_MIME_A_EXTENSION}


def guardar_media_privado(contenido: bytes, mime_type: str, kind: str) -> str | None:
    """Guarda `contenido` bajo settings.WHATSAPP_MEDIA_ROOT (directorio
    privado, servido solo via admin_panel.views.api_media_file con
    @login_required -- NO el MEDIA_ROOT/MEDIA_URL publico que ya usa
    bot.scraping.imagenes para fotos de catalogo) y devuelve el nombre de
    archivo relativo generado, para guardar en Message.media_url.

    kind: "image" | "audio". Nunca lanza -- un fallo de guardado (mime no
    soportado, directorio no escribible, etc.) no debe impedir que la
    percepcion de medios ya generada (texto) se siga usando con normalidad."""
    try:
        extension = _EXTENSION_POR_KIND[kind].get(mime_type)
        if not extension:
            return None
        nombre = f"{uuid.uuid4().hex}.{extension}"
        destino = Path(settings.WHATSAPP_MEDIA_ROOT) / nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
        return nombre
    except Exception:
        logger.exception("[media_storage] fallo al guardar media privado (%s)", kind)
        return None
