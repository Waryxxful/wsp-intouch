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

import re
import unicodedata

from asgiref.sync import sync_to_async
from langchain.tools import tool

from bot.models import ModeloOperacion, SolucionInTouch

# Lo que se le dice al modelo cuando el catálogo no tiene NINGUNA fila. Un
# `CLIENTE_ACTIVO` mal puesto basta para llegar acá: el manager filtra por
# cliente y no queda ninguna. Devolver `ok=True` con lista vacía sería lo peor
# que puede pasar -- el modelo la lee como "InTouch no ofrece nada" y se lo
# afirma al contacto. El doctor lo cubre como FALLA en deploy, pero eso no es
# una guarda en runtime.
_MOTIVO_CATALOGO_VACIO = (
    "el catálogo de soluciones está vacío: es una falla de configuración de "
    "este bot, no significa que InTouch no ofrezca soluciones. No le afirmes al "
    "contacto que no tenemos algo; dile que vas a confirmarlo y ofrece que lo "
    "contacte una persona."
)
_MOTIVO_MODELOS_VACIO = (
    "no hay modelos de operación cargados: es una falla de configuración de "
    "este bot, no significa que InTouch no tenga modelos de operación. No le "
    "afirmes nada al contacto sobre cómo se entrega el servicio."
)


def _normalizar(texto) -> str:
    """Minúsculas, sin tildes y sin puntuación.

    El que escribe la referencia es un LLM, y va a nombrar la solución como se
    la nombró al contacto. Sin esto, "operacion de contact center" (sin tilde)
    daba `ok=False` mientras "operación de contact center" daba `ok=True`, y
    "paneles y dashboards" fallaba porque `"Paneles, supervisión y
    dashboards".split()` deja el token "paneles,". Peor, se cerraba el círculo:
    el fallback devuelve los slugs, que van sin tilde, así que el modelo
    reintentaba con la forma que la tool rechazaba.

    NFKD descompone "ñ" en n + tilde combinante y acá se pierde la tilde, así
    que "diseño" queda "diseno". Es correcto porque las DOS puntas de la
    comparación pasan por la misma función.
    """
    plano = unicodedata.normalize("NFKD", str(texto or "").lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", plano).strip()


def _palabras(texto) -> set:
    return set(_normalizar(texto).split())


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
        "cuando_recomendarla": solucion.cuando_recomendarla,
    }


def _categorias_validas() -> list:
    return [c for c, _ in SolucionInTouch.CATEGORIA_CHOICES]


def _canales_validos() -> list:
    return list(SolucionInTouch.CANALES_VALIDOS)


def _listar_soluciones_impl(categoria: str = "", canal: str = "") -> dict:
    categoria = _normalizar(categoria).replace(" ", "_")
    canal = _normalizar(canal)
    if categoria and categoria not in _categorias_validas():
        # NO se devuelve una lista vacía: el modelo la leería como "InTouch no
        # tiene nada de eso" y se lo diría al cliente, que es falso. Lo que pasa
        # es que la categoría no existe, y eso se dice explícitamente.
        return {
            "ok": False,
            "motivo": f"'{categoria}' no es una categoría del catálogo.",
            "categorias_validas": _categorias_validas(),
        }
    # `canal` se valida por lo MISMO que `categoria`, y no se validaba: con la
    # semilla guardando "email" y todos los prompts diciendo "correo",
    # `canal="correo"` devolvía `{"ok": True, "soluciones": []}` y el bot le
    # afirmaba al contacto que InTouch no atiende por correo. Un vocabulario
    # solo (SolucionInTouch.CANALES_VALIDOS) y un error explícito cuando no
    # calza.
    if canal and canal not in _canales_validos():
        return {
            "ok": False,
            "motivo": f"'{canal}' no es un canal del catálogo.",
            "canales_validos": _canales_validos(),
        }
    activas = list(SolucionInTouch.objects.filter(activa=True))
    if not activas:
        return {"ok": False, "motivo": _MOTIVO_CATALOGO_VACIO}
    filas = [s for s in activas
             if (not categoria or s.categoria == categoria)
             and (not canal or canal in [_normalizar(c) for c in (s.canales or [])])]
    return {"ok": True, "soluciones": [_serializar(s) for s in filas]}


def _consultar_solucion_impl(referencia: str) -> dict:
    texto = _normalizar(referencia)
    activas = list(SolucionInTouch.objects.filter(activa=True))
    if not activas:
        return {"ok": False, "motivo": _MOTIVO_CATALOGO_VACIO}
    if texto:
        for solucion in activas:
            if texto in (_normalizar(solucion.slug), _normalizar(solucion.nombre)):
                return {"ok": True, "solucion": _serializar(solucion)}
        # Match por subconjunto de palabras: el modelo la va a nombrar como se
        # la nombró al cliente ("los agentes conversacionales"), no por slug.
        # Normalizado en las dos puntas -- ver `_normalizar`.
        palabras = _palabras(referencia)
        for solucion in activas:
            if palabras and palabras <= _palabras(solucion.nombre):
                return {"ok": True, "solucion": _serializar(solucion)}
    return {
        "ok": False,
        "motivo": f"no tengo '{referencia}' en el catálogo de soluciones.",
        "soluciones_disponibles": [s.slug for s in activas],
    }


def _listar_modelos_operacion_impl() -> dict:
    modelos = list(ModeloOperacion.objects.all())
    if not modelos:
        return {"ok": False, "motivo": _MOTIVO_MODELOS_VACIO}
    return {
        "ok": True,
        "modelos": [
            {"slug": m.slug, "nombre": m.nombre, "descripcion": m.descripcion,
             "cuando_aplica": m.cuando_aplica}
            for m in modelos
        ],
    }


@tool(parse_docstring=True)
async def listar_soluciones(categoria: str = "", canal: str = "") -> dict:
    """Lista las soluciones que InTouch ofrece hoy, con sus canales y los
    modelos de operación en que se pueden entregar.

    La ficha corta del turno ya trae el nombre y una línea de cada solución
    activa. No la llames para nombrarlas ni para seguir la conversación.
    Llámala si necesitas filtrar por canal o por categoría, o si el turno
    dice que el catálogo no está cargado. Si una capacidad no aparece ni en
    la ficha ni acá, no la ofrezcas.

    Args:
        categoria: opcional -- "operacion", "agentes_ia", "analitica" o
            "integracion", si el cliente preguntó por un tipo puntual. Déjala
            vacía si la consulta es general.
        canal: opcional -- "whatsapp", "voz", "chat" o "correo", si el cliente
            preguntó por un canal específico. Si pasas otro valor, la tool
            devuelve `ok: false` con la lista de canales válidos: no interpretes
            un resultado vacío como que InTouch no atiende por ese canal.
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

    Úsala cuando pidan el detalle de una solución y la línea de la ficha del
    turno no alcance. Si la ficha dice que requiere evaluación técnica,
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

    El bloque del turno ya trae una línea de cada modelo. Úsala solo cuando
    pidan cómo se entrega el servicio más allá de esa línea. Preséntalo
    siempre como preliminar hasta que un especialista valide alcance y
    factibilidad.
    """
    return await sync_to_async(_listar_modelos_operacion_impl, thread_sensitive=True)()
