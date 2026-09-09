import io
import logging

import httpx
from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image

from bot.models import ImagenConvertida, ScrapingSource

logger = logging.getLogger(__name__)


def _buscar_url_original(slug_modelo: str) -> str | None:
    """Busca, en la corrida 'ok' mas reciente de cada fuente, la primera
    imagen de una pagina cuya URL contenga slug_modelo -- prioriza paginas
    /modelo/<slug>/ (fotos de producto reales) por sobre cualquier otra
    pagina que solo mencione el modelo de pasada (ej. un /cotizar/.../ que
    linkea al mismo logo generico)."""
    slug_modelo = (slug_modelo or "").strip().lower()
    candidatas = []
    for source in ScrapingSource.objects.all():
        run = source.runs.filter(estado="ok").order_by("-finished_at").first()
        if not run:
            continue
        for pagina in run.pages.all():
            if slug_modelo not in pagina.url.lower() or not pagina.imagenes:
                continue
            es_pagina_de_modelo = f"/modelo/{slug_modelo}" in pagina.url.lower()
            candidatas.append((es_pagina_de_modelo, pagina))
    if not candidatas:
        return None
    _, pagina = max(candidatas, key=lambda c: c[0])
    return pagina.imagenes[0]["url"]


def _convertir_a_jpeg(contenido: bytes) -> bytes:
    imagen = Image.open(io.BytesIO(contenido)).convert("RGB")
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def resolver_imagen_modelo(slug_modelo: str) -> str | None:
    """Devuelve la URL PUBLICA (PUBLIC_BASE_URL + MEDIA_URL) de la imagen
    JPEG cacheada para slug_modelo, o None si no hay imagen disponible o
    la conversion fallo. Nunca lanza -- una falla aca no debe romper el
    envio del mensaje de texto (ver handlers.py)."""
    try:
        url_original = _buscar_url_original(slug_modelo)
        if not url_original:
            return None

        existente = ImagenConvertida.objects.filter(url_original=url_original).first()
        if existente and existente.archivo and existente.archivo.storage.exists(existente.archivo.name):
            return f"{settings.PUBLIC_BASE_URL}{existente.archivo.url}"

        resp = httpx.get(url_original, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        jpeg_bytes = _convertir_a_jpeg(resp.content)

        nombre_archivo = f"{slug_modelo}.jpg"
        convertida = existente or ImagenConvertida.objects.create(url_original=url_original)
        convertida.archivo.save(nombre_archivo, ContentFile(jpeg_bytes), save=True)
        return f"{settings.PUBLIC_BASE_URL}{convertida.archivo.url}"
    except Exception:
        logger.exception("[imagenes] no se pudo resolver imagen para %s", slug_modelo)
        return None
