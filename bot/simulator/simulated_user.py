from django.conf import settings
from openevals.simulators import create_llm_simulated_user

INSTRUCCIONES_FIJAS = """
Reglas que debes seguir siempre, sin excepcion:
1. Si te piden RUT, nombre completo o telefono, entrega SIEMPRE datos
   obviamente ficticios (ej. nombre "Prueba Automatica - no es cliente
   real", RUT "11.111.111-1"). Nunca uses datos que parezcan reales.
2. Expresa tu situacion con tus propias palabras, de forma distinta cada
   vez (usa sinonimos, cambia el orden, varia el nivel de formalidad).
   Nunca cites textualmente un ejemplo o conversacion de referencia --
   inventa tu propia forma de decirlo.
"""


def construir_system_prompt(persona: str, objetivo: str) -> str:
    return (
        f"Eres un cliente en la siguiente situacion:\n{objetivo}\n\n"
        f"Tu personalidad:\n{persona}\n\n"
        f"{INSTRUCCIONES_FIJAS}"
    )


def crear_cliente_simulado(persona: str, objetivo: str):
    return create_llm_simulated_user(
        system=construir_system_prompt(persona, objetivo),
        model=settings.SIMULATED_USER_MODEL,
    )
