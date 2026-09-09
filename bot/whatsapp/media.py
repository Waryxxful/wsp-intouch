import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_GRAPH_API_BASE = "https://graph.facebook.com/v20.0"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}


def get_media_url(media_id: str) -> str | None:
    try:
        resp = httpx.get(f"{_GRAPH_API_BASE}/{media_id}", headers=_headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()["url"]
    except Exception:
        logger.exception("[media] fallo al resolver la URL del media %s", media_id)
        return None


def download_media(media_id: str) -> tuple[bytes | None, str | None]:
    media_url = get_media_url(media_id)
    if not media_url:
        return None, None
    try:
        resp = httpx.get(media_url, headers=_headers(), timeout=20)
        resp.raise_for_status()
        mime_type = resp.headers.get("Content-Type", "application/octet-stream")
        return resp.content, mime_type
    except Exception:
        logger.exception("[media] fallo al descargar el media %s", media_id)
        return None, None
