"""Tools del catálogo comercial de InTouch.

POR QUÉ EL CATÁLOGO ES UNA TOOL Y NO UN BLOQUE DE PROMPT (spec §5.1): el
prompt §5 prohíbe inventar capacidades, integraciones y certificaciones, y ese
guardrail sólo es cumplible si la lista sale de una fila -- el mismo argumento
por el que un precio no puede vivir en un chunk vectorial (biblia §III.5). Con
las soluciones escritas en el prompt, el modelo pierde la razón para usar la
vía verificable y vuelve a poder completar el hueco (biblia §III.3, ley 4).

El costo asumido es una ronda de tool en los turnos donde el bot habla de
soluciones. Está medido y aceptado en el spec.

Todo lo que estas funciones devuelven lo LEE el modelo, así que va en español
correcto con tildes: el modelo imita su corpus, no sólo lo obedece.
"""

from asgiref.sync import sync_to_async
from langchain.tools import tool

from bot.models import ModeloOperacion, SolucionInTouch


def _serializar(solucion) -> dict:
    return {
        "slug": solucion.slug,
        "nombre": solucion.nombre,
        "categoria": solucion.categoria,
        "descripcion": solucion.descripcion,
        "canales": solucion.canales or [],
        "modelos_operacion": solucion.modelos_operacion or [],
        # Viaja siempre: es de dónde el bot sabe que tiene que presentarla como
        # sujeta a evaluación técnica (prompt §2). Sin el campo en el resultado,
        # el modelo tendría que acordarse, y acordarse no es una garantía.
        "requiere_evaluacion_tecnica": solucion.requiere_evaluacion_tecnica,
        "ejemplos_uso": solucion.ejemplos_uso,
    }


def _categorias_validas() -> list:
    return [c for c, _ in SolucionInTouch.CATEGORIA_CHOICES]


def _listar_soluciones_impl(categoria: str = "", canal: str = "") -> dict:
    categoria = (categoria or "").strip()
    canal = (canal or "").strip().lower()
    if categoria and categoria not in _categorias_validas():
        # NO se devuelve una lista vacía: el modelo la leería como "InTouch no
        # tiene nada de eso" y se lo diría al cliente, que es falso. Lo que pasa
        # es que la categoría no existe, y eso se dice explícitamente.
        return {
            "ok": False,
            "motivo": f"'{categoria}' no es una categoría del catálogo.",
            "categorias_validas": _categorias_validas(),
        }
    filas = [s for s in SolucionInTouch.objects.filter(activa=True)
             if (not categoria or s.categoria == categoria)
             and (not canal or canal in [c.lower() for c in (s.canales or [])])]
    return {"ok": True, "soluciones": [_serializar(s) for s in filas]}


def _consultar_solucion_impl(referencia: str) -> dict:
    texto = (referencia or "").strip().lower()
    activas = list(SolucionInTouch.objects.filter(activa=True))
    if texto:
        for solucion in activas:
            if solucion.slug.lower() == texto or solucion.nombre.lower() == texto:
                return {"ok": True, "solucion": _serializar(solucion)}
        # Match por subconjunto de palabras: el modelo la va a nombrar como se
        # la nombró al cliente ("los agentes conversacionales"), no por slug.
        palabras = set(texto.replace("-", " ").split())
        for solucion in activas:
            nombre = set(solucion.nombre.lower().replace("-", " ").split())
            if palabras and palabras <= nombre:
                return {"ok": True, "solucion": _serializar(solucion)}
    return {
        "ok": False,
        "motivo": f"no tengo '{referencia}' en el catálogo de soluciones.",
        "soluciones_disponibles": [s.slug for s in activas],
    }


def _listar_modelos_operacion_impl() -> dict:
    return {
        "ok": True,
        "modelos": [
            {"slug": m.slug, "nombre": m.nombre, "descripcion": m.descripcion,
             "cuando_aplica": m.cuando_aplica}
            for m in ModeloOperacion.objects.all()
        ],
    }


@tool(parse_docstring=True)
async def listar_soluciones(categoria: str = "", canal: str = "") -> dict:
    """Lista las soluciones que InTouch ofrece hoy, con sus canales y los
    modelos de operación en que se pueden entregar.

    Llámala antes de afirmar que InTouch hace algo. Es la única fuente del
    catálogo: si una capacidad no aparece acá, no la ofrezcas.

    Selecciona después las que sean pertinentes para lo que el cliente
    necesita; no le enumeres todo el catálogo en cada respuesta.

    Args:
        categoria: opcional -- "operacion", "agentes_ia", "analitica" o
            "integracion", si el cliente preguntó por un tipo puntual. Déjala
            vacía si la consulta es general.
        canal: opcional -- "whatsapp", "voz", "chat" o "email", si el cliente
            preguntó por un canal específico.
    """
    # async + sync_to_async(thread_sensitive=True) y no una función sync: una
    # tool sync cae a run_in_executor en un hilo genérico del pool de asyncio,
    # lo que rompe la transacción por-test de Django (espera que la conexión
    # viva en un solo hilo) y ya dejó filas filtrando a otros tests.
    return await sync_to_async(_listar_soluciones_impl, thread_sensitive=True)(categoria, canal)


@tool(parse_docstring=True)
async def consultar_solucion(referencia: str) -> dict:
    """Devuelve la ficha completa de una solución del catálogo: qué es, en qué
    canales se entrega, con qué modelos de operación y un ejemplo de uso.

    Úsala cuando el cliente pregunte por una solución en particular, o antes de
    describirla en detalle. Si la ficha dice que requiere evaluación técnica,
    preséntala como sujeta a evaluación y no como algo ya disponible.

    Args:
        referencia: nombre o identificador de la solución (ej. "agentes
            conversacionales", "integraciones")
    """
    return await sync_to_async(_consultar_solucion_impl, thread_sensitive=True)(referencia)


@tool(parse_docstring=True)
async def listar_modelos_operacion() -> dict:
    """Devuelve los modelos de operación de InTouch -- humano, híbrido y
    automatizado -- con la descripción de cada uno y cuándo aplica.

    Úsala cuando el cliente pregunte cómo se entrega el servicio, o cuando
    tengas que recomendar un enfoque preliminar. Preséntalo siempre como
    preliminar hasta que un especialista valide alcance y factibilidad.
    """
    return await sync_to_async(_listar_modelos_operacion_impl, thread_sensitive=True)()
