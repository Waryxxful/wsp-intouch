import json
import logging

from django.conf import settings
from langchain.chat_models import init_chat_model

from bot.flow.graph import _texto_de_respuesta

logger = logging.getLogger(__name__)

# Polaridad uniforme: en todo booleano, true = el bot CUMPLIO el criterio
# (comportamiento correcto). En toda escala 1-5, mas alto = mejor. Nunca al reves.
CRITERIOS_GENERICOS = [
    {
        "nombre": "no_repregunta_dato_conocido",
        "pregunta": (
            "el bot NO volvio a pedir un dato (nombre, RUT, telefono, modelo de auto, "
            "etc.) que ya estaba en flow_data o en el historial de la conversacion"
        ),
        "estricto": True,
    },
    {
        "nombre": "avanza_hacia_objetivo",
        "pregunta": (
            "la conversacion avanzo razonablemente hacia el objetivo del cliente, "
            "sin dar vueltas ni repetirse sin progresar"
        ),
        "estricto": False,
    },
    {
        "nombre": "tono_apropiado_whatsapp",
        "pregunta": (
            "el tono y largo de las respuestas del bot son naturales para un chat de "
            "WhatsApp, no un bloque de texto robotico o excesivamente largo"
        ),
        "estricto": False,
    },
]


def _construir_prompt(transcript: str, criterios: list[dict]) -> str:
    lineas = []
    for i, c in enumerate(criterios):
        if c["estricto"]:
            lineas.append(
                f"{i + 1}. [{c['nombre']}] ¿Es cierto que {c['pregunta']}? "
                f"Responde \"valor\": true si SI se cumple, false si NO."
            )
        else:
            lineas.append(
                f"{i + 1}. [{c['nombre']}] ¿Es cierto que {c['pregunta']}? "
                f"Responde \"valor\" con un puntaje de 1 (muy en desacuerdo) a 5 (muy de acuerdo)."
            )
    return f"""Eres un evaluador de calidad de un bot de ventas de WhatsApp para una concesionaria.
Revisa la siguiente conversacion entre un cliente y el bot, y responde cada
pregunta de evaluacion por separado. Razona brevemente antes de decidir cada
valor -- no te apures a la conclusion.

CONVERSACION:
{transcript}

PREGUNTAS DE EVALUACION:
{chr(10).join(lineas)}

Responde SOLO con un JSON con esta forma exacta, una entrada por pregunta,
usando el nombre entre corchetes de cada pregunta como clave:
{{"<nombre>": {{"razonamiento": "...", "valor": true|false|<numero 1-5>}}, ...}}
"""


def _parsear_respuesta(bruto: str) -> dict:
    bruto = bruto.strip()
    if bruto.startswith("```"):
        bruto = bruto.split("\n", 1)[1]
        if bruto.endswith("```"):
            bruto = bruto[:-3]
        bruto = bruto.strip()
    return json.loads(bruto)


def evaluar_conversacion(transcript: str, criterios_escenario: list[str]) -> tuple[dict, list[str]]:
    """Un unico llamado combinado al juez (nunca uno por sub-pregunta -- ver
    seccion 6.1 del spec). `criterios_escenario` son los `criterios` del
    escenario (texto libre, siempre escritos como afirmaciones de
    comportamiento correcto) y se evaluan estrictos (true/false), igual que
    los anti-alucinacion genericos.

    Devuelve `(resultado, nombres_criterios)`: `resultado` es el dict
    parseado de la respuesta del juez (puede venir incompleto, o `{}` si el
    parseo fallo), y `nombres_criterios` es la lista COMPLETA de nombres de
    criterio que se le pidieron al juez en el prompt. El caller (runner.py)
    usa esta segunda lista para detectar si el juez omitio silenciosamente
    alguno de los criterios esperados (Hallazgo 4 de la revision final) --
    sin ella, un `resultado` con solo 3 de 6 claves se veria identico a uno
    completo."""
    criterios = list(CRITERIOS_GENERICOS) + [
        {"nombre": f"criterio_escenario_{i + 1}", "pregunta": texto, "estricto": True}
        for i, texto in enumerate(criterios_escenario)
    ]
    nombres_criterios = [c["nombre"] for c in criterios]
    modelo = init_chat_model(settings.JUDGE_MODEL)
    prompt = _construir_prompt(transcript, criterios)
    respuesta = modelo.invoke(prompt)
    contenido = _texto_de_respuesta(respuesta.content)
    try:
        return _parsear_respuesta(contenido), nombres_criterios
    except (json.JSONDecodeError, IndexError, AttributeError, TypeError):
        logger.error("[simulator.judge] respuesta del juez no es JSON valido: %s", contenido)
        return {}, nombres_criterios
