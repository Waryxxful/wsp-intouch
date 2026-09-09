"""Busqueda estructurada sobre el stock de usados (VehiculoUsado).

Esta es la tool que hace verdadero el guardrail "nunca inventes stock ni
precios" (docx S15): el LLM no busca, TRADUCE el pedido del cliente a filtros
y esta funcion devuelve filas reales. Las consultas difusas del docx S6
("algo familiar que no consuma demasiado", "parecido a una RAV4", "algo mas
premium") se resuelven asi -- eligiendo filtros -- y no con el RAG: un vector
store responde por semejanza, no sabe resolver "<= $20.000.000" ni "anio >=
2022", que es justo lo que piden 6 de las 12 preguntas del docx S24.
"""

import unicodedata

from asgiref.sync import sync_to_async
from django.db.models import Case, F, IntegerField, When
from langchain.tools import tool

from bot.models import VehiculoUsado

# Cuantos vehiculos se le devuelven al LLM de una vez. El docx S3 pide
# "recomendar 2 o 3 vehiculos", pero devolverle exactamente 3 le saca la
# posibilidad de elegir con criterio; se le dan 6 candidatos y el prompt le
# pide destacar 2-3. Mas que esto solo gasta contexto: con 36 autos en
# catalogo, una busqueda amplia trae 16+ y ninguna respuesta de WhatsApp
# puede listarlos todos.
_LIMITE_DEFAULT = 6

# El LLM (y el cliente) no usan las etiquetas exactas de la planilla. "Camioneta"
# es la palabra chilena para Pick-up y aparece textual en el docx S24 ("Busco una
# camioneta para trabajo"); sin este mapa esa pregunta devuelve cero resultados.
_SINONIMOS_TIPO = {
    "camioneta": "pick-up", "pickup": "pick-up", "pick up": "pick-up",
    "todoterreno": "suv", "4x4": "suv", "jeep": "suv",
    "auto": "", "vehiculo": "", "citycar": "hatchback", "compacto": "hatchback",
}
_SINONIMOS_COMBUSTIBLE = {"petrolero": "diesel", "petroleo": "diesel", "bencina": "gasolina", "bencinero": "gasolina"}


def _norm(texto: str) -> str:
    """Minusculas sin acentos. La planilla escribe "Automatica", "Sedan" y
    "Diesel" CON tilde, y comparar substrings sin normalizar falla en silencio
    -- bug real al importar el stock: `"automat" in "automatica"` es False
    porque la sexta letra es "a" acentuada, y los 36 vehiculos quedaron
    marcados como no-automaticos."""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(c) != "Mn"
    ).lower().strip()


def _clp(monto) -> str:
    """Monto en pesos chilenos listo para mostrar: "$19.890.000"."""
    from bot.business.catalogo import _clp as _formatear
    return _formatear(monto)


def _vehiculo_a_dict(v: VehiculoUsado) -> dict:
    return {
        "codigo": v.codigo,
        "marca": v.marca,
        "modelo": v.modelo,
        "version": v.version,
        "anio": v.anio,
        "km": v.km,
        "precio": v.precio_vigente,
        "precio_lista": v.precio_lista,
        "precio_oferta": v.precio_oferta,
        # Ya formateado para copiar TAL CUAL al mensaje. Un LLM copia un string
        # de forma mucho mas confiable de la que reformatea un numero: en una
        # conversacion real (2026-09-02) el bot tenia el precio correcto de la
        # Subaru XV en el resultado de la tool y escribio $18.990.000, que es
        # el precio del Kia Sportage que habia mostrado tres mensajes antes
        # (docs/PENDIENTES.md #20). Ademas el separador de miles en Chile es
        # el punto, no la coma que sale por defecto.
        "precio_formateado": _clp(v.precio_vigente),
        "precio_lista_formateado": _clp(v.precio_lista),
        "tipo_vehiculo": v.tipo_vehiculo,
        "combustible": v.combustible,
        "transmision": v.transmision,
        "traccion": v.traccion,
        "color": v.color,
        "n_duenos": v.n_duenos,
        "estado": v.estado,
        "airbags": v.airbags,
        "garantia": v.garantia,
        "ubicacion": v.ubicacion,
        "disponibilidad": v.disponibilidad,
        # docx S5 lo pide, y el diccionario de la planilla lo marca util para
        # antiguedad de inventario y campanas.
        "fecha_ingreso": v.fecha_ingreso.isoformat() if v.fecha_ingreso else None,
        "rendimiento": v.rendimiento,
        "equipamiento": v.equipamiento,
        "acepta_parte_pago": v.acepta_parte_pago,
        "acepta_financiamiento": v.acepta_financiamiento,
        "observaciones": v.observaciones,
    }


def _con_precio_vigente(queryset):
    """Anota el precio que se cotiza (oferta si existe, si no lista) para poder
    filtrar y ordenar por el en SQL. Replica VehiculoUsado.precio_vigente --
    una property de Python no se puede usar en un filter()."""
    return queryset.annotate(
        pv=Case(
            When(precio_oferta__isnull=False, then=F("precio_oferta")),
            default=F("precio_lista"),
            output_field=IntegerField(),
        )
    )


def _buscar_vehiculos_impl(
    precio_max: int = 0, precio_min: int = 0, tipo_vehiculo: str = "", marca: str = "",
    modelo: str = "", anio_min: int = 0, km_max: int = 0, combustible: str = "",
    traccion: str = "", solo_automaticos: bool = False, ordenar_por: str = "",
    limite: int = _LIMITE_DEFAULT,
) -> dict:
    # Los filtros numericos van en SQL (columnas indexadas). Los categoricos se
    # resuelven en Python sobre las filas ya acotadas: son comparaciones sin
    # acentos y con sinonimos, y el catalogo es de decenas de filas -- no vale
    # la pena pelear con las diferencias de collation entre SQLite (dev) y SQL
    # Server (produccion) para eso.
    query = _con_precio_vigente(VehiculoUsado.objects.filter(disponibilidad__iexact="Disponible"))
    if precio_max:
        query = query.filter(pv__lte=int(precio_max))
    if precio_min:
        query = query.filter(pv__gte=int(precio_min))
    if anio_min:
        query = query.filter(anio__gte=int(anio_min))
    if km_max:
        query = query.filter(km__lte=int(km_max))
    if solo_automaticos:
        query = query.filter(es_automatico=True)

    tipo_n = _norm(tipo_vehiculo)
    tipo_n = _SINONIMOS_TIPO.get(tipo_n, tipo_n)
    comb_n = _norm(combustible)
    comb_n = _SINONIMOS_COMBUSTIBLE.get(comb_n, comb_n)
    marca_n, modelo_n, traccion_n = _norm(marca), _norm(modelo), _norm(traccion)

    filas = [
        v for v in query
        if (not tipo_n or tipo_n in _norm(v.tipo_vehiculo))
        and (not comb_n or comb_n in _norm(v.combustible))
        and (not marca_n or marca_n in _norm(v.marca))
        and (not modelo_n or modelo_n in _norm(v.modelo))
        and (not traccion_n or traccion_n == _norm(v.traccion))
    ]

    # Orden por defecto: si el cliente dio presupuesto, se muestra primero lo
    # MAS caro que entra en el (el mejor auto que alcanza), no lo mas barato.
    # No es un upsell arbitrario: es la respuesta que el propio docx S6 espera
    # para "SUV automatico de maximo $20 millones" -- Tucson 19,99 / Sportage
    # 18,99 / 3008 18,49 / Tiguan 17,99 son exactamente los primeros bajo 20M
    # en orden descendente. Ordenando ascendente saldrian primero los usados
    # mas baratos del catalogo y el guion de la demo (docx S17) no calzaria.
    # Si el cliente pide explicitamente lo mas economico, el LLM manda
    # ordenar_por="precio_asc".
    orden = _norm(ordenar_por)
    if orden == "precio_asc":
        filas.sort(key=lambda v: v.precio_vigente)
    elif orden == "km_asc":
        filas.sort(key=lambda v: v.km)
    elif orden == "anio_desc":
        filas.sort(key=lambda v: (-v.anio, v.km))
    elif precio_max:
        filas.sort(key=lambda v: -v.precio_vigente)
    else:
        filas.sort(key=lambda v: v.precio_vigente)

    if not filas:
        # Nunca dejar al LLM en un callejon sin salida: si el presupuesto no
        # alcanza, se le dice cual es el mas barato que cumple el RESTO de los
        # filtros, para que pueda ofrecer algo concreto en vez de "no tengo
        # nada". Sin esto el bot corta la conversacion y se pierde el lead.
        alternativa = None
        if precio_max:
            # precio_min SI se conserva: si el cliente puso un piso ("algo mas
            # premium, de 25 millones para arriba"), ofrecerle el auto mas
            # barato del catalogo como "lo mas cercano" no es una alternativa,
            # es ignorar lo que pidio.
            sin_precio = _buscar_vehiculos_impl(
                precio_min=precio_min, tipo_vehiculo=tipo_vehiculo, marca=marca,
                modelo=modelo, anio_min=anio_min, km_max=km_max,
                combustible=combustible, traccion=traccion,
                solo_automaticos=solo_automaticos, ordenar_por="precio_asc", limite=1,
            )
            if sin_precio.get("vehiculos"):
                alternativa = sin_precio["vehiculos"][0]
        return {
            "ok": False,
            "total_encontrados": 0,
            "motivo": "No hay vehiculos disponibles que cumplan esos criterios.",
            "alternativa_mas_cercana": alternativa,
        }

    limite = max(1, min(int(limite or _LIMITE_DEFAULT), 12))
    resultado = {
        "ok": True,
        "total_encontrados": len(filas),
        "vehiculos": [_vehiculo_a_dict(v) for v in filas[:limite]],
    }
    if len(filas) > limite:
        resultado["nota"] = (
            f"Hay {len(filas)} vehiculos que cumplen. Se muestran los {limite} mas relevantes: "
            "destaca 2 o 3 con un motivo concreto cada uno y ofrece acotar la busqueda "
            "(uso que le va a dar, anio minimo, kilometraje, si necesita 4x4 o mas espacio) "
            "en vez de listarlos todos."
        )
    return resultado


@tool(parse_docstring=True)
async def buscar_vehiculos(
    precio_max: float = 0, precio_min: float = 0, tipo_vehiculo: str = "", marca: str = "",
    modelo: str = "", anio_min: int = 0, km_max: int = 0, combustible: str = "",
    traccion: str = "", solo_automaticos: bool = False, ordenar_por: str = "",
) -> dict:
    """Busca vehículos usados en el stock real según los criterios del cliente.

    Úsala SIEMPRE que el cliente describa lo que busca (presupuesto, tipo de
    vehículo, uso, marca, año). Traduce lo que dijo a estos filtros: "algo
    familiar que no gaste mucho" -> tipo_vehiculo="SUV"; "una camioneta para
    trabajo" -> tipo_vehiculo="Pick-up"; "algo más premium" -> precio_min alto.
    Nunca inventes vehículos ni precios: lo único que puedes ofrecer es lo que
    devuelva esta herramienta.

    Args:
        precio_max: presupuesto máximo en pesos (ej. 20000000)
        precio_min: precio mínimo en pesos, útil para "algo más premium"
        tipo_vehiculo: SUV, Pick-up, Sedan, Hatchback o Crossover (acepta "camioneta")
        marca: marca del vehículo (ej. Toyota)
        modelo: modelo del vehículo (ej. RAV4)
        anio_min: año mínimo aceptable (ej. 2022)
        km_max: kilometraje máximo aceptable
        combustible: Gasolina o Diesel
        traccion: 4x2, 4x4 o AWD
        solo_automaticos: True si el cliente pide transmisión automatica
        ordenar_por: vacío para el orden por defecto, "precio_asc" si pide lo más económico, "km_asc" si pide el menos usado, "anio_desc" si pide el más nuevo
    """
    return await sync_to_async(_buscar_vehiculos_impl)(
        precio_max=precio_max, precio_min=precio_min, tipo_vehiculo=tipo_vehiculo,
        marca=marca, modelo=modelo, anio_min=anio_min, km_max=km_max,
        combustible=combustible, traccion=traccion, solo_automaticos=solo_automaticos,
        ordenar_por=ordenar_por,
    )


def _resolver_precio_usado(modelo: str, version: str = ""):
    """Precio real del stock para anclar una simulacion de financiamiento.

    Devuelve None si no hay match -- el caller decide que hacer, pero nunca
    inventa. Solo mira unidades DISPONIBLES: cotizarle una cuota a alguien
    sobre un auto ya vendido es peor que no anclar el precio.

    Si varias filas matchean y no se dio version (o la version no matchea
    ninguna), se toma el MINIMO: es el criterio "desde", el unico seguro
    cuando todavia no se sabe cual unidad quiere el cliente."""
    filas = _fila_por_referencia(f"{modelo} {version}".strip() if version else modelo)
    if not filas and version:
        # La version que mando el LLM puede ser una parafrasis que no calza con
        # ninguna palabra real ("Full Hybrid" vs "2.0 CVT Luxury"): se reintenta
        # solo con el modelo antes de rendirse.
        filas = _fila_por_referencia(modelo)
    if not filas:
        return None
    return min(v.precio_vigente for v in filas)


def _tokens(texto: str) -> set:
    return set(_norm(texto).split())


def _fila_por_referencia(referencia: str, solo_disponibles: bool = True):
    """Resuelve una referencia del cliente ("la Tucson", "US003", "Hyundai
    Tucson 2.0 AT Value") a filas del stock.

    Match por SUBCONJUNTO DE PALABRAS y no por substring: el substring exigia
    que la frase del cliente apareciera literal y contigua dentro de
    "marca modelo version", asi que "Hyundai Tucson 2023" o "Tucson 2.0 AT
    Value" -- las dos formas mas naturales de nombrar un auto, y las que el
    propio prompt le sugiere al LLM -- no matcheaban nada. Cuando esto lo usa
    _resolver_precio_usado, no matchear significa que la simulacion de
    financiamiento se queda con el precio que haya inventado el LLM, que es
    exactamente lo que ese resolver existe para impedir.

    solo_disponibles: por defecto se ignoran las unidades vendidas o
    reservadas. Dar la ficha o el precio de un auto que ya no esta viola la
    regla 8 del prompt global ("nunca afirmes disponibilidad que no
    verificaste"). El caller que quiera saber si existe pero no esta
    disponible pide solo_disponibles=False explicitamente.
    """
    ref = _norm(referencia)
    if not ref:
        return []
    query = VehiculoUsado.objects.all()
    if solo_disponibles:
        query = query.filter(disponibilidad__iexact="Disponible")
    filas = list(query)

    # El codigo es el unico identificador sin ambiguedad: si matchea, gana.
    exacto = [v for v in filas if _norm(v.codigo) == ref]
    if exacto:
        return exacto

    tokens_ref = _tokens(referencia)
    if not tokens_ref:
        return []
    return [
        v for v in filas
        # El cliente nombro un subconjunto de la ficha ("Tucson 2023")...
        if tokens_ref <= _tokens(f"{v.marca} {v.modelo} {v.version} {v.anio}")
        # ...o agrego palabras de relleno alrededor ("la Hyundai Tucson que vimos").
        or _tokens(f"{v.marca} {v.modelo}") <= tokens_ref
    ]


def _consultar_ficha_vehiculo_impl(referencia: str) -> dict:
    filas = _fila_por_referencia(referencia)
    if not filas:
        # Distinguir "nunca lo tuvimos" de "ya no esta" cambia lo que el bot
        # puede decir con honestidad, y lo segundo es una oportunidad comercial
        # ("esa ya se vendio, pero tengo estas parecidas").
        no_disponibles = _fila_por_referencia(referencia, solo_disponibles=False)
        if no_disponibles:
            return {
                "ok": False,
                "motivo": f"'{referencia}' existe en el catalogo pero YA NO ESTA DISPONIBLE "
                          f"(estado: {no_disponibles[0].disponibilidad}). Decile al cliente que "
                          "esa unidad ya no esta y ofrecele alternativas parecidas con "
                          "buscar_vehiculos. Nunca le des precio ni cuota de esta unidad.",
            }
        return {
            "ok": False,
            "motivo": f"No tengo '{referencia}' en el stock. No lo inventes: "
                      "decile al cliente que no lo tenemos disponible y ofrecele alternativas "
                      "parecidas con buscar_vehiculos.",
        }
    if len(filas) > 1:
        return {
            "ok": False,
            "motivo": "Hay varias unidades que calzan con esa descripcion. Preguntale al "
                      "cliente cual le interesa antes de dar datos.",
            "opciones": [
                {"codigo": v.codigo, "marca": v.marca, "modelo": v.modelo,
                 "version": v.version, "anio": v.anio, "precio": v.precio_vigente}
                for v in filas[:6]
            ],
        }
    v = filas[0]
    ficha = _vehiculo_a_dict(v)
    ficha.update({
        "motor": v.motor, "cilindrada": v.cilindrada, "potencia": v.potencia,
        "torque": v.torque, "seguridad": v.seguridad, "conectividad": v.conectividad,
        "confort": v.confort, "link_ficha": v.link_ficha, "link_fotos": v.link_fotos,
    })
    return {"ok": True, "vehiculo": ficha}


def _comparar_vehiculos_impl(referencias: list) -> dict:
    encontrados, no_encontrados = [], []
    for ref in referencias or []:
        filas = _fila_por_referencia(ref)
        if len(filas) == 1:
            encontrados.append(_consultar_ficha_vehiculo_impl(ref)["vehiculo"])
        elif not filas:
            no_encontrados.append(ref)
        else:
            # Varias unidades para la misma referencia: se toma la mas barata
            # (criterio "desde", igual que _resolver_precio_usado) y se avisa,
            # en vez de comparar contra una unidad elegida al azar.
            barata = min(filas, key=lambda x: x.precio_vigente)
            encontrados.append(_consultar_ficha_vehiculo_impl(barata.codigo)["vehiculo"])
    if not encontrados:
        return {"ok": False, "motivo": "No encontre ninguno de esos vehiculos en el stock.",
                "no_encontrados": no_encontrados}
    return {"ok": True, "vehiculos": encontrados, "no_encontrados": no_encontrados}


@tool(parse_docstring=True)
async def consultar_ficha_vehiculo(referencia: str) -> dict:
    """Devuelve la ficha técnica completa y real de un vehículo del stock.

    Úsala cuando el cliente pide detalles, especificaciones o equipamiento de
    una unidad puntual. Nunca completes datos que no vengan de acá.

    Args:
        referencia: como lo nombro el cliente ("Tucson", "RAV4 Luxury") o el código del stock ("US003")
    """
    return await sync_to_async(_consultar_ficha_vehiculo_impl, thread_sensitive=True)(referencia)


@tool(parse_docstring=True)
async def comparar_vehiculos_usados(referencias: list[str]) -> dict:
    """Compara dos o más vehículos del stock lado a lado con datos reales.

    Úsala cuando el cliente duda entre alternativas y pide compararlas.

    Args:
        referencias: lista de vehículos a comparar, como los nombro el cliente o por código (ej. ["Tucson", "Sportage"])
    """
    return await sync_to_async(_comparar_vehiculos_impl, thread_sensitive=True)(referencias)
