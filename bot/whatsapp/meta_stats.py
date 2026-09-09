"""Cliente para la Graph API de Meta — estadisticas del WhatsApp Business Account.

Consume 3 endpoints:
1. `{waba_id}?fields=conversation_analytics.start(...).end(...).granularity(DAILY)`
2. `{waba_id}?fields=pricing_analytics.start(...).end(...)`
3. `{waba_id}/template_analytics?start=&end=&metric_types=...`

Cache in-memory por (fn, start, end) con TTL 30 minutos — evita saturar la API
Graph con 1 request por vista del dashboard.

Best-effort: si falla la API, retorna None + log warning. Nunca lanza.

Uso desde management command para debug:
    from bot.whatsapp.meta_stats import get_message_stats, get_template_stats
    stats = get_message_stats(start_ts, end_ts)
"""
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

from django.conf import settings

logger = logging.getLogger(__name__)

# Cache: {(fn_name, start, end): (fetched_at_ts, data)}
_cache: dict[tuple, tuple[float, dict | None]] = {}
_CACHE_TTL_SECONDS = 30 * 60  # 30 minutos

_HTTP_TIMEOUT = 15  # segundos

# Numero propio (solo digitos) resuelto desde WHATSAPP_PHONE_ID, cacheado
# indefinidamente en memoria (no cambia en runtime). Ver _own_phone_number().
_phone_number_cache: dict[str, str] = {}


def _cache_get(key: tuple) -> dict | None:
    """Devuelve el valor cacheado si sigue vigente, sino None."""
    entry = _cache.get(key)
    if not entry:
        return None
    fetched_at, data = entry
    if time.time() - fetched_at > _CACHE_TTL_SECONDS:
        return None
    return data


def _cache_set(key: tuple, data: dict | None) -> None:
    _cache[key] = (time.time(), data)


def _graph_get(path: str, params: dict) -> dict | None:
    """GET a la Graph API. Devuelve dict o None si falla.

    `path` es la ruta sin `/{version}/` (ej. `"1234567/template_analytics"`).
    """
    token = settings.WHATSAPP_TOKEN
    version = settings.WHATSAPP_API_VERSION
    base = settings.WHATSAPP_API_BASE

    qs = urllib.parse.urlencode(params, safe=",()[]:")
    url = f"{base}/{version}/{path}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)
    except urllib.error.HTTPError as e:
        # Leer el body del error — Meta manda JSON con { "error": { "message": ..., "code": ... } }
        # que es lo unico util para debug.
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = "<no body>"
        logger.warning(
            "[meta-stats] fallo GET %s: %s | body=%s",
            url[:150], e, err_body[:400],
        )
        return None
    except Exception as e:
        logger.warning("[meta-stats] fallo GET %s: %s", url[:150], e)
        return None


def _own_phone_number() -> str | None:
    """Numero propio (solo digitos, sin '+') resuelto desde WHATSAPP_PHONE_ID.

    El WABA_ID puede tener mas de un numero registrado (ej. wsp_demo y
    wsp_pompeyo comparten la misma cuenta business con dos numeros). Sin
    filtrar por este numero, `analytics`/`pricing_analytics` devuelven el
    agregado de TODOS los numeros de la cuenta — mezclando el trafico de
    otro bot con el propio. None si no se pudo resolver (ej. API caida);
    en ese caso el caller cae de vuelta a las stats sin filtrar.
    """
    phone_id = str(settings.WHATSAPP_PHONE_ID or "")
    if not phone_id:
        return None
    cached = _phone_number_cache.get(phone_id)
    if cached:
        return cached
    data = _graph_get(phone_id, {"fields": "display_phone_number"})
    if not data:
        return None
    digits = re.sub(r"\D", "", data.get("display_phone_number", "") or "")
    if digits:
        _phone_number_cache[phone_id] = digits
    return digits or None


def get_message_stats(start_ts: int, end_ts: int) -> dict | None:
    """Devuelve las estadisticas de mensajes del rango [start_ts, end_ts] (unix ts).

    Combina `conversation_analytics` (para conteo por categoria) y
    `pricing_analytics` (para gratuitos vs pagados).

    Retorno:
        {
            "total_sent": int, "total_delivered": int, "total_received": int,
            "delivered_by_category": {"MARKETING": int, "UTILITY": int, ...},
            "free_delivered": {"customer_service": int, "free_entry_point": int},
            "paid_delivered_by_category": {...},
        }

    None si falla la API.
    """
    waba_id = settings.WHATSAPP_BUSINESS_ACCOUNT_ID
    if not waba_id:
        logger.warning("[meta-stats] WHATSAPP_BUSINESS_ACCOUNT_ID vacio")
        return None

    key = ("get_message_stats", start_ts, end_ts)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    own_number = _own_phone_number()
    if not own_number:
        logger.warning(
            "[meta-stats] no se pudo resolver el numero propio (WHATSAPP_PHONE_ID=%s) — "
            "las stats podrian incluir trafico de otros numeros de la cuenta business",
            settings.WHATSAPP_PHONE_ID,
        )

    # Analytics: agregado sent/delivered por dia. Este es el endpoint que
    # matchea el conteo del WhatsApp Manager para "Todos los mensajes".
    # `conversation_analytics` (que probamos antes) devuelve conversaciones
    # facturables, no mensajes — no sirve para los totales.
    # phone_numbers([own_number]) filtra al numero propio; sin esto (`[]`)
    # Meta agrega TODOS los numeros del WABA (ver _own_phone_number).
    phone_filter = json.dumps([own_number]) if own_number else "[]"
    analytics_fields = (
        f'analytics.start({start_ts}).end({end_ts})'
        f'.granularity(DAY).phone_numbers({phone_filter})'
    )
    analytics_data = _graph_get(waba_id, {"fields": analytics_fields})

    # Pricing analytics: cargos + volumen desglosado por categoria + tipo.
    # dimensions=["PRICING_CATEGORY","PRICING_TYPE"] separa Marketing/Utility/
    # Authentication/Service + Regular/Free_customer_service/Free_entry_point.
    # Agregamos "PHONE" para poder filtrar por numero propio abajo — a
    # diferencia de `analytics`, este endpoint no tiene un parametro de
    # filtro directo, solo la dimension (que agrega `phone_number` a cada
    # data_point).
    price_fields = (
        f'pricing_analytics.start({start_ts}).end({end_ts})'
        f'.granularity(DAILY)'
        f'.dimensions(["PRICING_CATEGORY","PRICING_TYPE","PHONE"])'
    )
    price_data = _graph_get(waba_id, {"fields": price_fields})

    # Filtrar pricing_analytics al numero propio (ver comentario arriba).
    # Si no se pudo resolver own_number, se deja sin filtrar (best-effort)
    # en vez de devolver vacio.
    if own_number and price_data:
        try:
            for series in (price_data.get("pricing_analytics") or {}).get("data") or []:
                series["data_points"] = [
                    pt for pt in series.get("data_points") or []
                    if pt.get("phone_number") == own_number
                ]
        except Exception as e:
            logger.warning("[meta-stats] fallo filtrando pricing_analytics por numero propio: %s", e)

    if analytics_data is None and price_data is None:
        _cache_set(key, None)
        return None

    result = {
        "raw_analytics": analytics_data,
        "raw_pricing_analytics": price_data,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "own_phone_number": own_number,
    }
    _cache_set(key, result)
    return result


def _waba_has_multiple_numbers() -> bool:
    """True si el WABA configurado tiene mas de un numero de telefono registrado.

    Los templates son un recurso a nivel de WABA, no de numero — Meta no
    ofrece forma de filtrar `template_analytics` por numero (a diferencia de
    `analytics`/`pricing_analytics`, ver _own_phone_number). Si el WABA tiene
    mas de un numero (ej. wsp_demo y wsp_pompeyo comparten uno), la lista de
    templates y sus envios puede incluir trafico de otro bot — el caller debe
    mostrar esto como caveat en vez de atribuirlo silenciosamente.
    """
    waba_id = settings.WHATSAPP_BUSINESS_ACCOUNT_ID
    if not waba_id:
        return False
    cached = _cache_get(("_waba_has_multiple_numbers", waba_id, 0))
    if cached is not None:
        return bool(cached.get("value"))
    data = _graph_get(f"{waba_id}/phone_numbers", {"fields": "id"})
    multiple = bool(data and len(data.get("data") or []) > 1)
    _cache_set(("_waba_has_multiple_numbers", waba_id, 0), {"value": multiple})
    return multiple


def get_template_stats(start_ts: int, end_ts: int) -> dict | None:
    """Devuelve las estadisticas por template en el rango [start_ts, end_ts].

    Retorno:
        {
            "templates": [
                {"name": str, "sent": int, "delivered": int,
                 "read": int, "clicked": int},
                ...
            ],
            "shared_waba": bool,  # ver _waba_has_multiple_numbers
        }

    None si falla. Meta requiere pasar ademas los template_ids que queremos —
    los sacamos primero con /message_templates.
    """
    waba_id = settings.WHATSAPP_BUSINESS_ACCOUNT_ID
    if not waba_id:
        logger.warning("[meta-stats] WHATSAPP_BUSINESS_ACCOUNT_ID vacio")
        return None

    shared_waba = _waba_has_multiple_numbers()

    key = ("get_template_stats", start_ts, end_ts)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # 1. Listar templates disponibles. Incluimos `category` para poder mostrar
    # tag en el frontend (MARKETING/UTILITY/AUTHENTICATION/SERVICE).
    templates_resp = _graph_get(
        f"{waba_id}/message_templates",
        {"fields": "id,name,status,category,language", "limit": "100"},
    )
    if not templates_resp:
        _cache_set(key, None)
        return None

    templates = templates_resp.get("data", []) or []
    template_ids = [t.get("id") for t in templates if t.get("id")]
    if not template_ids:
        result = {"templates": [], "raw_list": templates_resp, "shared_waba": shared_waba}
        _cache_set(key, result)
        return result

    # 2. Analytics: template_analytics agregado a nivel WABA.
    # Meta pide arrays con comillas dobles + comas escapadas.
    # Ejemplo: metric_types=["SENT","DELIVERED","READ","CLICKED"]
    ids_str = '[' + ','.join(f'"{tid}"' for tid in template_ids) + ']'
    metrics_str = '["SENT","DELIVERED","READ","CLICKED"]'
    analytics_resp = _graph_get(
        f"{waba_id}/template_analytics",
        {
            "start": str(start_ts),
            "end": str(end_ts),
            "granularity": "DAILY",
            "metric_types": metrics_str,
            "template_ids": ids_str,
        },
    )

    result = {
        "templates_list": templates,
        "raw_analytics": analytics_resp,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "shared_waba": shared_waba,
    }
    _cache_set(key, result)
    return result


def clear_cache() -> None:
    """Reset del cache — util para tests o refresh forzado desde admin."""
    _cache.clear()


def date_to_ts(date_str: str) -> int:
    """Convierte 'YYYY-MM-DD' a unix timestamp (medianoche UTC).

    Meta acepta cualquier ts en el rango; usamos medianoche UTC para
    consistencia con el WhatsApp Manager.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return int(dt.timestamp())
