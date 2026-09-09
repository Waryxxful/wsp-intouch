"""Notificación a la campanita del shell de GranCRM, vía el orquestador.

Molde: wsp_pompeyo/bot/notify.py, que es el único bot que hoy usa este
subsistema end-to-end.

EL CONTRATO SALE DEL CÓDIGO DEL ORQUESTADOR, NO DE LA DOC. Verificado el
2026-09-09: `docs-repo/notificaciones.md` describe la versión pre-granular y
NO menciona el campo `tipo`, que hoy es obligatorio -- quien siga ese
documento come un 400. Tampoco menciona `POST /internal/notify-types/`, que es
el paso previo indispensable.

Auth: secreto compartido en el BODY JSON (no hay header ni JWT). Es el mismo
`DIOS_REGISTER_SECRET` que ya vive en dios.json.

`notificados: 0` es un 200 válido: significa que nadie con acceso a la app está
suscrito al tipo. No es un error.
"""
import json
import logging
import os
import urllib.request

from django.conf import settings

from utils.dios_registration import _load_config

logger = logging.getLogger(__name__)


def notificar(tipo: str, mensaje: str, url: str = "") -> None:
    """Notifica a los suscritos al tipo `tipo` de la cuenta configurada.

    Best-effort: nunca lanza excepción. Cualquier falla -- timeout, secreto
    inválido, orquestador caído, tipo no declarado -- se loguea y se ignora.
    Una notificación es un aviso: no puede costar el turno de un contacto.
    """
    tenant = getattr(settings, "GRANCRM_TENANT_SLUG", "")
    if not tenant:
        logger.warning(
            "[notify] GRANCRM_TENANT_SLUG vacío, no se notifica (tipo=%r)", tipo)
        return
    dios_url = os.environ.get("DIOS_URL", "http://orquestador:9000")
    target = f"{dios_url}/internal/notify/"
    try:
        config = _load_config()
        body = json.dumps({
            "secret": config["secret"],
            "app_nombre": config["nombre"],
            "tenant_id": tenant,
            # Obligatorio desde la versión granular del subsistema. Sin este
            # campo el orquestador responde 400 "campos faltantes: ['tipo']".
            "tipo": tipo,
            "mensaje": mensaje,
            "url": url,
        }).encode("utf-8")
        req = urllib.request.Request(
            target, data=body, headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            respuesta = json.loads(resp.read() or b"{}")
        logger.info("[notify] %s notificado a %s (notificados=%s)",
                    tipo, tenant, respuesta.get("notificados"))
    except Exception as exc:
        logger.warning("[notify] no se pudo notificar %s a %s: %s", tipo, target, exc)
