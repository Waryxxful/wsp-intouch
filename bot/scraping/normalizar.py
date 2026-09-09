import re
import unicodedata


def _normalizar_clave(texto: str) -> str:
    """Normaliza un texto para usarlo como clave de matching insensible a
    mayusculas/acentos/puntuacion/espacios (ej. "Cotización" == "cotizacion",
    "Garantía legal 3×3" == "Garantia legal 3x3"). Bug real detectado en
    produccion (wsp_demo, renault.cl): el LLM nombra el MISMO producto o
    sucursal con variaciones minimas entre distintas paginas/chunks (con o
    sin el prefijo de marca, con o sin acentos, × vs x, mayusculas
    distintas) y el matching por nombre exacto anterior creaba una fila
    duplicada por cada variacion en vez de una sola actualizada."""
    texto = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.replace("×", "x")
    texto = re.sub(r"[^\w\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


# Abreviaturas comunes de direccion chilena -- _normalizar_clave por si sola
# NO las expande (solo saca acentos/puntuacion/mayusculas), asi que "Av."
# y "Avenida" quedaban como strings distintos y no matcheaban como la misma
# direccion. La clave ya viene sin puntuacion cuando llega aca (gracias a
# _normalizar_clave), por eso el diccionario no necesita variantes con punto.
_ABREVIATURAS_DIRECCION = {
    "av": "avenida",
    "avda": "avenida",
    "psje": "pasaje",
    "pje": "pasaje",
    "pdte": "presidente",
}


def _normalizar_direccion(texto: str) -> str:
    """Como _normalizar_clave, pero ademas expande abreviaturas comunes de
    direccion chilena antes de normalizar -- separada de _normalizar_clave a
    proposito: esa funcion tambien matchea nombres de producto/sucursal, y
    expandir abreviaturas ahi podria dar falsos positivos sin relacion con
    direcciones (ej. una marca o modelo que empiece con "Av")."""
    # Remove dots between digits (thousand separators in Chilean addresses)
    # before normalizing, so "10.371" matches "10371"
    texto = re.sub(r'(\d)\.(\d)', r'\1\2', texto)
    clave = _normalizar_clave(texto)
    palabras = [_ABREVIATURAS_DIRECCION.get(p, p) for p in clave.split(" ")]
    return " ".join(palabras)
