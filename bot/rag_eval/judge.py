import json
import logging

from django.conf import settings
from langchain.chat_models import init_chat_model

from bot.flow.graph import _texto_de_respuesta

logger = logging.getLogger(__name__)

_PROMPT = """Eres un evaluador de calidad de un sistema de busqueda (RAG) para
el sitio de una concesionaria de autos. Te doy una pregunta de un cliente y
los fragmentos de texto que el sistema recupero para responderla.

PREGUNTA:
{query}

FRAGMENTOS RECUPERADOS:
{fragmentos}

¿Estos fragmentos alcanzan para responder la pregunta completa y
correctamente, sin inventar nada que no este en ellos? Responde SOLO con
este JSON exacto:
{{"relevancia": {{"razonamiento": "...", "valor": <puntaje de 1 a 5>}}}}
"""


def _parsear(bruto: str) -> dict:
    bruto = bruto.strip()
    if bruto.startswith("```"):
        bruto = bruto.split("\n", 1)[1]
        if bruto.endswith("```"):
            bruto = bruto[:-3]
        bruto = bruto.strip()
    return json.loads(bruto)


def evaluar_relevancia(query: str, textos_chunks: list[str]) -> int | None:
    """Juez LLM: puntaje 1-5 de si los chunks recuperados alcanzan para
    responder `query`. Devuelve None si no hay chunks (nada que evaluar) o
    si la respuesta del juez no se pudo parsear -- nunca levanta, mismo
    contrato de tolerancia a fallos que el resto de bot/rag/."""
    if not textos_chunks:
        return None
    modelo = init_chat_model(settings.JUDGE_MODEL)
    fragmentos = "\n---\n".join(textos_chunks)
    prompt = _PROMPT.format(query=query, fragmentos=fragmentos)
    respuesta = modelo.invoke(prompt)
    contenido = _texto_de_respuesta(respuesta.content)
    try:
        return int(_parsear(contenido)["relevancia"]["valor"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        logger.error("[rag_eval] respuesta del juez no es valida: %s", contenido)
        return None
