import math

from asgiref.sync import sync_to_async
from langchain.tools import tool

from bot.models import Servicio, Sucursal


def _distancia_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radio_tierra_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radio_tierra_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _sucursales_sin_ranking(categoria: str = "", motivo: str = "") -> dict:
    """Devuelve las sucursales sin ordenarlas por distancia.

    Fallback necesario: el ranking por cercania necesita geocodificar la
    ubicacion del cliente con la API de Google Maps, y sin GOOGLE_MAPS_API_KEY
    -- o si la direccion que dijo el cliente no resuelve -- esto devolvia
    ok=False. El efecto era que "donde estan ubicados?" (docx S24) quedaba sin
    respuesta y el bot derivaba a un humano por un dato que tiene en la base.
    Con una sola sucursal, ademas, el ranking no aporta nada: no hay nada que
    ordenar."""
    sucursales = [
        s for s in Sucursal.objects.all()
        if not categoria or not s.categorias or categoria in s.categorias
    ]
    if not sucursales:
        return {"ok": False, "motivo": "No hay sucursales cargadas para esa categoria."}
    return {
        "ok": True,
        "ordenadas_por_cercania": False,
        "motivo_sin_ranking": motivo,
        "sucursales": [
            {"id": s.id, "nombre": s.nombre, "direccion": s.direccion,
             "horario": s.horario_texto}
            for s in sucursales[:3]
        ],
    }


def _buscar_sucursales_cercanas_impl(lugar: str, categoria: str = "") -> dict:
    from bot.business.geocoding import geocodificar_direccion

    con_coordenadas = Sucursal.objects.filter(latitud__isnull=False, longitud__isnull=False).count()
    if con_coordenadas <= 1:
        # Una sola sucursal (o ninguna geolocalizada): no hay nada que rankear,
        # y llamar a Google Maps solo agrega latencia y un modo de falla.
        return _sucursales_sin_ranking(categoria, "hay una sola sucursal")

    coords = geocodificar_direccion(lugar)
    if not coords:
        return _sucursales_sin_ranking(
            categoria, f"no pude ubicar '{lugar}', se listan todas las sucursales")
    lat, lng = coords
    candidatas = sorted(
        (
            (_distancia_km(lat, lng, s.latitud, s.longitud), s)
            for s in Sucursal.objects.filter(latitud__isnull=False, longitud__isnull=False)
            if not categoria or not s.categorias or categoria in s.categorias
        ),
        key=lambda t: t[0],
    )
    if not candidatas:
        return _sucursales_sin_ranking(categoria, "ninguna sucursal de esa categoria esta geolocalizada")
    return {
        "ok": True,
        "ordenadas_por_cercania": True,
        "sucursales": [
            {"id": s.id, "nombre": s.nombre, "direccion": s.direccion,
             "horario": s.horario_texto, "distancia_km": round(dist, 1)}
            for dist, s in candidatas[:3]
        ],
    }


def _clp(monto) -> str:
    """Monto en pesos chilenos listo para mostrar: "$19.890.000".

    Se entrega ya formateado en el resultado de las tools para que el LLM lo
    COPIE en vez de reescribirlo. Un modelo copia un string de forma mucho mas
    confiable de la que reformatea un numero, y ademas el separador de miles
    en Chile es el punto (f"{n:,}" da comas)."""
    if monto is None:
        return ""
    return f"${monto:,.0f}".replace(",", ".")


def _listar_catalogo_impl(categoria: str = "") -> dict:
    return {
        # precio/precio_formateado: el prompt de agendamiento le dice al LLM
        # que "podes ver los servicios y sus valores referenciales con
        # listar_catalogo" y que los cotice, pero esta tool NO devolvia el
        # precio. Sin el dato, el LLM completo el hueco: le mando a un cliente
        # real "valor referencial desde $XX" (conversacion del 2026-09-02, ver
        # docs/PENDIENTES.md #20). El precio estaba en la BD, nunca llegaba
        # hasta aca.
        #
        # precio_formateado va listo para copiar tal cual al mensaje: el LLM
        # copia un string mucho mas confiable de lo que reformatea un numero,
        # y el separador de miles en Chile es el punto, no la coma que sale
        # por defecto.
        "servicios": [
            {
                "id": s.id, "nombre": s.nombre, "duracion_min": s.duracion_min,
                # precio puede ser None (el campo es opcional): se deja en None
                # en vez de romper, y _clp devuelve "" para que el LLM no tenga
                # un monto que copiar.
                "precio": int(s.precio) if s.precio is not None else None,
                "precio_formateado": _clp(s.precio),
            }
            for s in Servicio.objects.all()
        ],
        "sucursales": [
            {"id": s.id, "nombre": s.nombre, "direccion": s.direccion}
            for s in Sucursal.objects.all()
            if not categoria or not s.categorias or categoria in s.categorias
        ],
    }


@tool(parse_docstring=True)
async def listar_catalogo(categoria: str = "") -> dict:
    """Lista los servicios y sucursales disponibles en el catálogo del negocio.

    Usar cuando el catálogo tiene más de un servicio o sucursal y hace falta
    que el cliente elija, o para confirmar nombres/ids antes de agendar.

    Args:
        categoria: opcional -- "ventas" o "servicio_tecnico" si el cliente
            preguntó específicamente por una de las dos (ej. "donde compro
            un auto" -> "ventas", "donde hago mantención" -> "servicio_tecnico").
            Dejar vacío si la pregunta es general o no aplica.
    """
    # async + sync_to_async(thread_sensitive=True) en vez de una funcion
    # sync corriente: BaseTool.ainvoke() de una tool sync cae a
    # run_in_executor(None, ...), que corre en un hilo generico del pool de
    # asyncio (verificado: distinto de MainThread) -- eso rompe la
    # transaccion por-test de Django (TestCase espera que toda la
    # conexion/transaccion viva en un solo hilo "thread sensitive") y dejo
    # un Lead sin rollback filtrando a otro test (ver CreateAppTest). Con la
    # tool declarada async, ToolNode awaitea la coroutine directo en el
    # mismo hilo/loop, y sync_to_async(thread_sensitive=True) es quien
    # decide en que hilo corre el ORM sync de forma segura para Django.
    return await sync_to_async(_listar_catalogo_impl, thread_sensitive=True)(categoria)


@tool(parse_docstring=True)
async def buscar_sucursales_cercanas(lugar: str, categoria: str = "") -> dict:
    """Busca las sucursales más cercanas a un lugar/comuna cuando consultar_base_conocimiento no encontro una sucursal ahi.

    Args:
        lugar: nombre del lugar, comuna o sector que menciono el cliente (ej. "Las Condes", "Vina del Mar")
        categoria: opcional -- "ventas" o "servicio_tecnico" si el cliente
            preguntó específicamente por una de las dos. Dejar vacío si la
            pregunta es general.
    """
    return await sync_to_async(_buscar_sucursales_cercanas_impl, thread_sensitive=True)(lugar, categoria)
