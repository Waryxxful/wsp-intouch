import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def geocodificar_direccion(direccion: str) -> tuple[float, float] | None:
    """Resuelve una direccion a (lat, lng) via Google Geocoding API. None si falla o no hay key."""
    if not direccion or not settings.GOOGLE_MAPS_API_KEY:
        return None
    try:
        resp = httpx.get(
            _GEOCODE_URL,
            params={
                "address": direccion,
                "components": "country:CL",
                "key": settings.GOOGLE_MAPS_API_KEY,
            },
            timeout=10,
        )
        data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("Geocoding fallo para %r: %s", direccion, e)
        return None
    if data.get("status") != "OK" or not data.get("results"):
        logger.warning("Geocoding sin resultados para %r: %s", direccion, data.get("status"))
        return None
    location = data["results"][0]["geometry"]["location"]
    return location["lat"], location["lng"]
