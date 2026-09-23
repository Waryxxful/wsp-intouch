"""Direcciones que la empresa publica en su sitio, para ContactoInstitucional.

Es el "catálogo estructurado" del vertical de InTouch (spec §12.6): el
automotriz saca servicios, sucursales y vehículos; este saca con qué datos se
contacta a la empresa. Los teléfonos y correos no pasan por acá -- salen de los
enlaces tel:/mailto: sin LLM (bot/scraping/crawler.py::_contactos_de_enlaces).
Las direcciones no tienen marcado propio en un sitio común, así que las lee un
LLM, y el código descarta cualquiera que no esté escrita en la página.
"""
import asyncio
import json
import logging
import re

from .extractor import _ainvoke_with_retry, _get_llm

logger = logging.getLogger(__name__)

# Tope de texto por llamada. Un sitio de una página (in-touch.cl) usa ~6.000
# caracteres; esto deja pasar varias páginas sin mandar un sitio entero.
_MAX_CHARS = 40_000

PROMPT_DIRECCIONES = """Recibes el texto de las páginas del sitio de una empresa.

Extrae las direcciones físicas de las oficinas o sedes de ESA empresa, tal como
aparecen escritas en el texto. Si el sitio solo nombra una ciudad o un país
donde tiene presencia (por ejemplo «Lima, Perú»), inclúyela también.

Reglas:
- Copia la dirección como está en el texto. No la completes, no la corrijas y
  no agregues comuna, ciudad ni país que no estén escritos.
- No incluyas direcciones de clientes, de eventos ni de otras empresas.
- "etiqueta" es el rótulo con que el sitio presenta esa dirección (por ejemplo
  el país o el nombre de la oficina). Si no tiene, déjala vacía.
- Si no hay ninguna, devuelve la lista vacía.

Responde solo con JSON, sin texto antes ni después:
{{"direcciones": [{{"etiqueta": "...", "direccion": "..."}}]}}

Texto del sitio:
{texto}
"""


def _normalizar(texto: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (texto or "").lower()).split())


def _armar_texto(paginas: list[dict]) -> str:
    partes, total = [], 0
    for p in paginas:
        texto = (p.get("texto") or "").strip()
        if not texto:
            continue
        bloque = f"URL: {p['url']}\n{texto}"
        if total + len(bloque) > _MAX_CHARS:
            bloque = bloque[: max(0, _MAX_CHARS - total)]
        partes.append(bloque)
        total += len(bloque)
        if total >= _MAX_CHARS:
            break
    return "\n\n---\n\n".join(partes)


def _parsear(raw: str) -> list[dict]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[scraping-direcciones] la respuesta del LLM no es JSON: %s", raw[:500])
        raise ValueError("el LLM no devolvió JSON válido al extraer las direcciones") from exc
    items = parsed.get("direcciones") if isinstance(parsed, dict) else None
    return [i for i in (items or []) if isinstance(i, dict)]


async def _extraer_async(paginas: list[dict]) -> list[dict]:
    texto = _armar_texto(paginas)
    if not texto:
        return []
    llm = await _get_llm()
    raw = await _ainvoke_with_retry(llm, PROMPT_DIRECCIONES.format(texto=texto), label="scraping-direcciones")
    textos_por_url = [(p["url"], _normalizar(p.get("texto", ""))) for p in paginas]

    direcciones, vistas = [], set()
    for item in _parsear(raw):
        valor = " ".join(str(item.get("direccion") or "").split())
        clave = _normalizar(valor)
        if not clave or clave in vistas:
            continue
        # La defensa contra una dirección inventada es esta, no el prompt: si
        # no está escrita en alguna página, no entra.
        fuente = next((url for url, texto_n in textos_por_url if clave in texto_n), None)
        if fuente is None:
            logger.warning("[scraping-direcciones] el LLM devolvió %r, que no está en el sitio: se descarta", valor)
            continue
        vistas.add(clave)
        direcciones.append({
            "tipo": "direccion", "valor": valor,
            "etiqueta": " ".join(str(item.get("etiqueta") or "").split())[:120],
            "fuente_url": fuente,
        })
    return direcciones


def extraer_direcciones(paginas: list[dict]) -> list[dict]:
    """Punto de entrada síncrono, como extract_catalog: runner.py es síncrono
    de punta a punta. Levanta si el LLM falla o no devuelve JSON; el runner
    deja el run en error y no toca los contactos que ya había."""
    return asyncio.run(_extraer_async(paginas))
