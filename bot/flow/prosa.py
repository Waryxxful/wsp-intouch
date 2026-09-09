"""Compuerta determinística sobre la prosa del especialista.

Existe porque la prosa ahora ES la respuesta que se le manda al cliente (ver el
spec en docs/superpowers/specs/2026-09-03-canal-de-salida-prosa-natural-design.md),
y el modelo a veces filtra restos de nuestras propias instrucciones al canal de
texto: 6 de 8 fallos reales de Langfuse traían un preámbulo inventado. En 28
replays del estado exacto de producción, 0 de 7 prosas lo traían — depende del
estado, así que no se puede mandar el `content` crudo sin revisarlo.

Es determinística a propósito: no gasta una llamada al LLM en decidir si un
texto sirve. La decisión tiene que ser barata porque corre en el camino
crítico, justo antes de mandarle el mensaje al cliente.
"""

import unicodedata

# Arrancan SIEMPRE con un espacio en los casos reales, y restatean
# instrucciones nuestras. Se listan en minúsculas y sin tildes: la comparación
# normaliza las dos puntas (ver _sin_tildes), así que "demás" y "demas" matchean
# igual. Son cadenas que el modelo copia de nuestro prompt, no lenguaje libre
# del cliente, por eso alcanza con comparar el comienzo.
PREFIJOS_CONTAMINADOS = (
    "usa la estructura",
    "todos los demas campos",
    "el primer caracter",
    "responde siempre",
    "para responderle al cliente",
    "recordatorio para este turno",
    "nunca escribas el mensaje",
)

# Una respuesta útil del bot nunca es tan corta. El piso ataca el mensaje
# degenerado ("...", de 3 caracteres) que el tool_choice forzado le mandaba al
# cliente 2 de 12 veces en la medición del 2026-09-03.
_LARGO_MINIMO = 12


def _sin_tildes(texto: str) -> str:
    base = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")


def prosa_utilizable(texto) -> bool:
    """True si `texto` se le puede mandar al cliente tal cual."""
    if not isinstance(texto, str):
        return False
    limpio = texto.strip()
    if len(limpio) < _LARGO_MINIMO:
        return False
    # Un texto que arranca con "{" NUNCA es prosa, parsee o no. Los dos casos
    # tienen dueño y ninguno es este:
    #   - si es JSON válido, es el contrato viejo -> _parse_json_response;
    #   - si está malformado, es salida rota -> hay que reintentarla, no
    #     mandársela al cliente.
    # El segundo caso lo encontró un test existente de test_graph: el LLM
    # devolvió '{"mensaje": "el arkana tiene precios "desde" varios", ...}',
    # roto por las comillas internas. Una versión anterior de esta función solo
    # rechazaba el JSON que SÍ parseaba, así que ese string crudo se le habría
    # enviado al contacto. Por eso el chequeo es sobre el primer carácter, no
    # sobre el resultado de json.loads.
    #
    # Y también se rechaza el bloque de código: _parse_json_response acepta
    # ```json ... ```, así que un contrato viejo así envuelto NO empieza con "{"
    # y se habría colado como prosa, mandándole al cliente el JSON con las
    # comillas invertidas incluidas.
    if limpio.startswith("{") or limpio.startswith("```"):
        return False
    normalizado = _sin_tildes(limpio)
    return not any(normalizado.startswith(_sin_tildes(p)) for p in PREFIJOS_CONTAMINADOS)
