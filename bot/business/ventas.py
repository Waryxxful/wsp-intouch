import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from langchain.tools import ToolRuntime, tool

logger = logging.getLogger(__name__)

_TASA_MENSUAL = 0.0199  # fija, credito tradicional (docs/prompt_calculo_credito_tradicional.txt)
_PLAZO_MIN_MESES = 12  # docx S7: financiamiento entre 12 y 60 meses
_PLAZO_MAX_MESES = 60
_PORCENTAJE_PIE_MINIMO = 0.20
_LEAD_DEDUP_VENTANA = timedelta(minutes=5)

# Marcas reales observadas en el catalogo multimarca de Astara (mas Renault,
# por si algun caller compartido las nombra) -- normalizadas (ver
# _normalizar_clave), ordenadas por cantidad de palabras descendente para
# que un prefijo de 2 palabras ("alfa romeo") se pruebe antes que cualquier
# marca de 1 palabra. Usadas SOLO para decidir si un modelo puede
# consolidarse con otro que aparece con/sin marca (ver _modelo_canonico) --
# nunca para filtrar ni descartar filas por su cuenta.
_MARCAS_CONOCIDAS = [
    ("alfa", "romeo"),
    ("mitsubishi",), ("ssangyong",), ("chery",), ("exeed",), ("jmc",),
    ("byd",), ("kgm",), ("jeep",), ("ram",), ("fiat",), ("opel",),
    ("peugeot",), ("gac",), ("renault",),
]


def _modelo_canonico(modelo: str) -> tuple:
    """Palabras normalizadas de `modelo` sin el prefijo de marca conocida,
    si tiene una -- "Mitsubishi Outlander" y "Outlander" resuelven al mismo
    canonico ("outlander",), pero "Outlander" y "Outlander Phev" NO (son
    canonicos distintos, aunque "outlander" sea subconjunto de palabras de
    "outlander phev"). Ver _buscar_en_catalogo para el porque."""
    from bot.scraping.normalizar import _normalizar_clave
    palabras = tuple(_normalizar_clave(modelo).split())
    for marca in _MARCAS_CONOCIDAS:
        n = len(marca)
        if palabras[:n] == marca:
            return palabras[n:]
    return palabras


def _buscar_en_catalogo(modelo: str, version: str = "", condicion: str = "") -> list:
    """Devuelve las filas de VehiculoCatalogo que matchean modelo (y version
    si se da), normalizando mayusculas/acentos/puntuacion (_normalizar_clave).
    Usado por _resolver_precio_catalogo, consultar_especificaciones_vehiculo
    y comparar_vehiculos -- un solo lugar para esta logica de matching.

    condicion="0km"/"usado" filtra estrictamente a esa condicion. Sin
    condicion (default), NO filtra -- devuelve 0km y usado mezclados si
    ambos existen, para que el caller (consultar_especificaciones_vehiculo)
    pueda avisarle al LLM que existen ambas opciones y preguntarle al
    cliente cual quiere, en vez de asumir. _resolver_precio_catalogo (un
    compromiso de precio real, no informativo) SI le pasa un default
    explicito de "0km" para no heredar esta ambiguedad."""
    if not modelo:
        return []
    from bot.models import VehiculoCatalogo
    from bot.scraping.normalizar import _normalizar_clave
    modelo_norm = _normalizar_clave(modelo)
    if not modelo_norm:
        return []
    # Match por subconjunto de palabras EN CUALQUIER DIRECCION (un match
    # exacto es el caso particular en que ambos lados tienen las mismas
    # palabras, asi que no hace falta un chequeo de igualdad aparte). Bug
    # real confirmado 2026-08-28 (docs/PENDIENTES.md): el LLM extrae
    # "modelo" de forma inconsistente segun la pagina de origen -- con marca
    # incluida en unas ("JMC Grand Avenue") y sin marca en otras, donde la
    # marca viene en una columna separada ("Grand Avenue", /ofertas/). Con
    # match exacto nada mas, preguntar por cualquiera de las dos formas solo
    # encontraba una fila -- a veces precisamente la que no tenia precio
    # numerico, y _consolidar_candidatos nunca llegaba a ver la otra para
    # descartarla. Nunca alcanza con una sola palabra en comun: hace falta
    # que TODAS las palabras del lado mas corto aparezcan en el mas largo.
    tokens_modelo = set(modelo_norm.split())
    query = VehiculoCatalogo.objects.all()
    condicion_norm = condicion.strip().lower()
    if condicion_norm in ("0km", "usado"):
        query = query.filter(condicion=condicion_norm)
    filas = list(query)
    # Paso 1: preferir match EXACTO por modelo canonico (marca conocida
    # quitada de ambos lados) sobre el fallback difuso de abajo. Bug real
    # confirmado en produccion 2026-08-31 (docs/PENDIENTES.md, auditoria
    # conversacion 14): "Outlander" es subconjunto de palabras de "Outlander
    # Phev" -- el match difuso puro (subconjunto en cualquier direccion, sin
    # este paso) no distingue "el cliente omitio una palabra por comodidad"
    # de "el cliente nombra un producto genuinamente distinto (PHEV/EV/
    # Sport/etc.)", y dejaba que preguntar por el Outlander normal resolviera
    # a precio/specs del Outlander PHEV o viceversa -- el bot llego a
    # confirmarle a un cliente real el precio del auto sin PHEV como si
    # fuera el PHEV. Sistemico, no un caso aislado: 159 colisiones de este
    # tipo confirmadas en el catalogo real de Astara. Si hay match exacto,
    # ganan esas filas y no se cae al fallback -- si no hay ninguno (ej. el
    # cliente dice "Avenue" en vez de "Grand Avenue"), sigue el
    # comportamiento difuso de siempre.
    canonico_query = _modelo_canonico(modelo)
    exactos = [v for v in filas if canonico_query and _modelo_canonico(v.modelo) == canonico_query]
    if exactos:
        candidatos = exactos
    else:
        candidatos = [
            v for v in filas
            if tokens_modelo and (
                tokens_modelo <= set(_normalizar_clave(v.modelo).split())
                or set(_normalizar_clave(v.modelo).split()) <= tokens_modelo
            )
        ]
    if not candidatos:
        return []
    if version:
        version_norm = _normalizar_clave(version)
        con_version = [v for v in candidatos if _normalizar_clave(v.version) == version_norm]
        if not con_version:
            # Bug real confirmado (revision manual 2026-08-27): el LLM manda
            # una parafrasis razonable de la version ("Esprit Alpine Hybrid")
            # que nunca calza EXACTO contra el string real del catalogo
            # ("full hybrid e-tech esprit alpine") -- sin esto, con_version
            # quedaba vacio y _resolver_precio_catalogo caia en silencio al
            # precio MINIMO de todo el modelo (Techno, $27.990.000 en vez de
            # $34.990.000 del Esprit Alpine), sin ningun aviso. Match parcial
            # por subconjunto de palabras: todas las palabras que mando el
            # LLM tienen que aparecer en la version candidata -- no al reves,
            # para no matchear de mas con una version corta tipo "Techno".
            tokens_pedidos = set(version_norm.split())
            con_version = [
                v for v in candidatos
                if tokens_pedidos and tokens_pedidos <= set(_normalizar_clave(v.version).split())
            ]
        if con_version:
            candidatos = con_version
            # Preferir filas con precio_contado/precio_financiado presente por
            # sobre una fila de la MISMA version pero con un unico precio sin
            # contexto -- bug real encontrado en la auditoria previa a la
            # prueba de Astara del 2026-09-01 (docs/PENDIENTES.md): varias
            # paginas del sitio (home, /ofertas/) muestran un solo numero de
            # un vehiculo sin desglosar los 3 tiers, y ese numero suele SER
            # el precio contado o financiado (no el de lista), sin nada que
            # lo distinga de un precio de lista real cuando se lo mira
            # aislado. La pagina que SI desglosa los 3 precios es evidencia
            # mucho mas fuerte de que su "precio" es realmente el de lista.
            # Solo aplica aca (version especifica ya resuelta a un match
            # unico) -- no toca el caso de varias versiones reales sin
            # version pedida, donde es normal que ninguna fila tenga tiers.
            con_tiers = [v for v in candidatos if v.precio_contado is not None or v.precio_financiado is not None]
            if con_tiers:
                candidatos = con_tiers
    return _consolidar_candidatos(candidatos)


def _consolidar_candidatos(candidatos: list) -> list:
    """Colapsa filas que representan la MISMA version real de un vehiculo
    pero llegaron fragmentadas en el catalogo -- distintos scrapes de
    paginas distintas describen la misma version con distinto nivel de
    detalle (ej. "hybrid", "hybrid intens turbo", "intens turbo" y
    "1.3 4X2 AT INTENS MHEV" son las 4 la misma version Intens del Arkana).
    Bug real confirmado (revision manual + analisis de FB 2026-08-27):
    consultar_especificaciones_vehiculo("Arkana") devolvia estas 4 filas
    (mas otras 5) por separado -- el LLM tenia que elegir cual citar, sin
    ninguna garantia de elegir la misma entre turnos o pruebas distintas,
    lo que explica consumos reportados distintos en pruebas de dias
    distintos para el "mismo" dato real.

    Agrupa por precio -- senal mas confiable de identidad real que el
    nombre de version (dos filas con el mismo precio no nulo son casi
    siempre la misma version real; precios distintos, como los de Master
    Furgon L2H2/L3H2/Minibus, son versiones genuinamente distintas y no se
    tocan). De cada grupo se queda con la fila de version mas descriptiva
    (el string mas largo) y fusiona los specs de todas las filas del
    grupo. Las filas sin precio se descartan si el modelo ya tiene alguna
    fila CON precio -- sin precio y casi sin specs no aportan nada util al
    cliente, solo ruido/confusion adicional al LLM.

    Tambien prefiere, para url_fuente, cualquier fila del grupo cuyo link
    termine en .pdf por sobre una que solo apunte a una pagina web -- una
    ficha tecnica real descargable es un dato mas util que la pagina que
    la menciona, y feature de "mandar la ficha por WhatsApp" (ver
    _resolver_ficha_tecnica_url) depende de que url_fuente sea el PDF
    cuando existe uno en el grupo."""
    con_precio = [v for v in candidatos if v.precio is not None]
    if not con_precio:
        return candidatos

    grupos: dict = {}
    for v in con_precio:
        grupos.setdefault(v.precio, []).append(v)

    resultado = []
    for filas in grupos.values():
        principal = max(filas, key=lambda v: len(v.version or ""))
        specs_fusionados = {}
        for v in filas:
            for clave, valor in (v.specs or {}).items():
                if valor is not None and clave not in specs_fusionados:
                    specs_fusionados[clave] = valor
        principal.specs = specs_fusionados
        pdf = next((v.url_fuente for v in filas if (v.url_fuente or "").lower().endswith(".pdf")), None)
        if pdf:
            principal.url_fuente = pdf
        resultado.append(principal)
    return resultado


def _vehiculo_a_dict(v) -> dict:
    # ficha_tecnica_url: solo si url_fuente es un PDF real descargable, no
    # una pagina web -- asi el LLM ve, en el resultado real de la
    # herramienta, si existe una ficha para mandar por WhatsApp ANTES de
    # decidir si ofrece mandarla (setear "modelo_ficha" en su respuesta),
    # en vez de prometerla a ciegas y que handlers.py descubra despues que
    # no hay nada que mandar (decision de diseno 2026-08-27, ver
    # docs/PENDIENTES.md).
    ficha_tecnica_url = v.url_fuente if (v.url_fuente or "").lower().endswith(".pdf") else None
    return {
        "version": v.version, "condicion": v.condicion, "precio": v.precio,
        "precio_contado": v.precio_contado, "precio_financiado": v.precio_financiado,
        "specs": v.specs, "ficha_tecnica_url": ficha_tecnica_url,
    }


def _resolver_precio_catalogo(modelo: str, version: str = "", condicion: str = ""):
    """Bug real confirmado en revision manual: sin esto, _simular_financiamiento
    confiaba ciegamente en el precio que el LLM ponia. Si hay match en el
    catalogo, ese precio manda sobre el que puso el LLM. Sin version (o si la
    version dada no matchea ninguna fila), se toma el minimo entre las
    versiones del modelo -- mismo criterio "desde" que se usa para mostrar
    precios de un modelo con varias versiones.

    condicion default "0km" (no "" como _buscar_en_catalogo): esto resuelve
    un precio real que se usa para un compromiso financiero
    (simular_financiamiento), asi que no puede quedar ambiguo entre 0km y
    usado -- a diferencia de consultar_especificaciones_vehiculo, que SI
    quiere ver ambos para poder preguntarle al cliente."""
    # Cavem: el stock real vive en VehiculoUsado (planilla comercial), no en
    # VehiculoCatalogo (salida del scraper, que en este bot queda sin fuentes
    # configuradas). Sin este repunte, simular_financiamiento no encontraria
    # nunca un precio real y volveria a confiar ciegamente en el numero que
    # ponga el LLM -- exactamente el bug que este resolver existe para evitar.
    from bot.business.usados import _resolver_precio_usado
    return _resolver_precio_usado(modelo, version)


def _resolver_ficha_tecnica_url(modelo: str, version: str = "") -> str | None:
    """Busca, entre las filas de VehiculoCatalogo que matchean modelo (y
    version si se da), una cuyo url_fuente sea un PDF real descargable --
    no una pagina web que solo menciona el modelo. Devuelve None si no hay
    ninguna, para que el caller no intente mandar un adjunto que no
    existe. Usado por _vehiculo_a_dict (expone "ficha_tecnica_url" en el
    resultado de consultar_especificaciones_vehiculo, para que el LLM sepa
    si existe un PDF real ANTES de decidir si llama a enviar_ficha_tecnica)
    y por enviar_ficha_tecnica mas abajo -- el dato ya viene scrapeado,
    nunca lo inventa el LLM."""
    for v in _buscar_en_catalogo(modelo, version):
        if (v.url_fuente or "").lower().endswith(".pdf"):
            return v.url_fuente
    return None


def _enviar_ficha_tecnica_impl(wa_id: str, modelo: str) -> dict:
    if not wa_id:
        return {"ok": False, "motivo": "falta wa_id."}
    url = _resolver_ficha_tecnica_url(modelo)
    if not url:
        return {"ok": False, "motivo": f"no tengo la ficha tecnica en PDF para '{modelo}'."}

    from bot.flow.context_window import limite_sesion_actual, marcador_ficha_enviada
    from bot.models import Conversation, Message
    marcador = marcador_ficha_enviada(modelo)
    conversation = Conversation.objects.filter(wa_id=wa_id).first()
    if conversation is not None:
        # Mismo criterio de dedup que la imagen del auto (sesion activa,
        # no una ventana fija de mensajes) -- ver
        # bot/whatsapp/handlers.py::_imagen_enviada_recientemente. No
        # reenviar el mismo PDF varias veces en la misma sesion.
        limite = limite_sesion_actual(conversation)
        if limite is not None and conversation.messages.filter(created_at__gte=limite, content=marcador).exists():
            return {"ok": True, "motivo": "la ficha técnica ya se envió antes en esta conversación."}

    from bot.whatsapp.client import get_wa_client
    enviado = get_wa_client().send_document(wa_id, url, filename=f"Ficha tecnica {modelo}.pdf")
    if not enviado:
        return {"ok": False, "motivo": "no se pudo enviar el documento, intenta más tarde."}

    if conversation is not None:
        Message.objects.create(conversation=conversation, role="assistant", content=marcador)
    return {"ok": True}


@tool(parse_docstring=True)
async def enviar_ficha_tecnica(modelo: str, runtime: ToolRuntime) -> dict:
    """Manda la ficha técnica real de un modelo como archivo PDF adjunto por WhatsApp -- no un link en texto.

    Usar SOLO si el resultado de "consultar_especificaciones_vehiculo" trajo "ficha_tecnica_url" distinto de
    null para ese modelo. Si es null, no hay PDF real cargado -- no llames a esta herramienta ni prometas
    mandar nada.

    Args:
        modelo: modelo del vehículo (ej. "Arkana")
    """
    # wa_id inyectado por el framework via ToolRuntime -- nunca visible ni
    # controlable por el LLM (mismo patron que registrar_no_contactar en
    # bot/business/compliance.py). El LLM nunca elige la URL que se manda,
    # solo el modelo -- _resolver_ficha_tecnica_url adentro del impl es lo
    # unico que decide que archivo real se adjunta.
    return await sync_to_async(_enviar_ficha_tecnica_impl, thread_sensitive=True)(
        wa_id=runtime.state.get("wa_id", ""), modelo=modelo,
    )


def _simular_financiamiento_impl(precio, pie=None, plazo_meses=None, gastos_adicionales=0, modelo="", version="",
                                  condicion="", porcentaje_pie=None, tasa_mensual=None) -> dict:
    precio_catalogo = _resolver_precio_catalogo(modelo, version, condicion)
    if precio_catalogo is not None:
        precio = precio_catalogo
    try:
        precio = float(precio)
        plazo_meses = int(plazo_meses)
        gastos_adicionales = float(gastos_adicionales)
    except (TypeError, ValueError):
        return {"ok": False, "motivo": "precio, plazo_meses y gastos_adicionales deben ser valores numéricos."}

    if precio <= 0:
        return {"ok": False, "motivo": "el precio del vehículo debe ser mayor que cero."}

    # pie/porcentaje_pie: exactamente uno de los dos. porcentaje_pie existe
    # para que la CUENTA (precio x porcentaje) la haga siempre esta funcion,
    # nunca el LLM -- bug real confirmado en conversacion 14 (2026-08-31,
    # docs/PENDIENTES.md "Pie/monto financiado inconsistente"): el LLM
    # reuso un pie en pesos de una simulacion anterior cuando el precio
    # cambio entre turnos, en vez de recalcular 50% del precio nuevo. Con
    # porcentaje_pie, el mismo porcentaje aplicado a un precio distinto
    # siempre da el monto correcto, sin depender de que el LLM haga la
    # cuenta bien de memoria.
    if (pie is None) == (porcentaje_pie is None):
        return {
            "ok": False,
            "motivo": "hay que indicar pie (monto en pesos) o porcentaje_pie (ej. 0.5 para 50%) -- exactamente uno de los dos.",
        }
    if porcentaje_pie is not None:
        try:
            porcentaje_pie = float(porcentaje_pie)
        except (TypeError, ValueError):
            return {"ok": False, "motivo": "porcentaje_pie debe ser un valor numérico (ej. 0.5 para 50%)."}
        pie = precio * porcentaje_pie
    else:
        try:
            pie = float(pie)
        except (TypeError, ValueError):
            return {"ok": False, "motivo": "pie debe ser un valor numérico."}

    # tasa_mensual: mismo criterio que arriba, pero para la TASA -- permite
    # cotizar con la tasa real de un banco/financista externo (no Astara)
    # con matematica garantizada por la tool, en vez de que el LLM calcule
    # la cuota "de memoria" en texto libre (visto en la misma conversacion
    # 14: el turno no invocaba ningun tool_call). El resultado siempre
    # informa que tasa se uso via "tasa_mensual_referencial".
    if tasa_mensual is None:
        tasa_mensual = _TASA_MENSUAL
    else:
        try:
            tasa_mensual = float(tasa_mensual)
        except (TypeError, ValueError):
            return {"ok": False, "motivo": "tasa_mensual debe ser un valor numérico (ej. 0.0199 para 1,99%)."}
        if tasa_mensual <= 0:
            return {"ok": False, "motivo": "tasa_mensual debe ser mayor que cero."}

    if not (_PLAZO_MIN_MESES <= plazo_meses <= _PLAZO_MAX_MESES):
        return {"ok": False, "motivo": f"el plazo debe ser un numero entero entre {_PLAZO_MIN_MESES} y {_PLAZO_MAX_MESES} meses."}
    if gastos_adicionales < 0:
        return {"ok": False, "motivo": "los gastos adicionales financiados no pueden ser negativos."}
    if pie >= precio:
        return {"ok": False, "motivo": "el pie no puede ser igual o superior al precio total del vehiculo."}
    pie_minimo = precio * _PORCENTAJE_PIE_MINIMO
    if pie < pie_minimo:
        return {
            "ok": False,
            "motivo": "el pie ingresado es menor al minimo exigido (20% del precio del vehiculo).",
            "pie_minimo_exigido": round(pie_minimo),
        }

    monto_base_a_financiar = precio - pie
    monto_total_financiado = monto_base_a_financiar + gastos_adicionales
    i = tasa_mensual
    n = plazo_meses
    cuota = monto_total_financiado * (i * (1 + i) ** n) / ((1 + i) ** n - 1)
    total_pagado_en_cuotas = cuota * n
    intereses_totales = total_pagado_en_cuotas - monto_total_financiado
    costo_total_operacion = pie + total_pagado_en_cuotas

    return {
        "ok": True,
        "precio": round(precio),
        "pie": round(pie),
        "porcentaje_pie": round(pie / precio * 100, 1),
        "pie_minimo_exigido": round(pie_minimo),
        "monto_base_a_financiar": round(monto_base_a_financiar),
        "gastos_adicionales": round(gastos_adicionales),
        "monto_total_financiado": round(monto_total_financiado),
        "plazo_meses": plazo_meses,
        "tasa_mensual_referencial": i,
        "cuota_mensual_estimada": round(cuota),
        "total_pagado_en_cuotas": round(total_pagado_en_cuotas),
        "intereses_totales": round(intereses_totales),
        "costo_total_operacion": round(costo_total_operacion),
    }


def _crear_lead_impl(rut, nombre, telefono, razon_interes) -> dict:
    from django.utils import timezone
    from leads.models import Lead
    rut = str(rut or "")[:12]
    nombre = str(nombre or "")[:200]
    telefono = str(telefono or "")[:20]
    try:
        if rut:
            # Idempotencia real: bot/flow/graph.py::_filtrar_acciones_repetidas
            # dedupea business actions por args EXACTOS -- un reintento del
            # LLM en el mismo turno con razon_interes reformulado ya no
            # matchea ese dedup y llega hasta aca. El rut identifica a la
            # persona; razon_interes no es parte de la identidad, se ignora
            # a proposito. Sin rut (el LLM lo dejo vacio) no hay con que
            # deduplicar de forma confiable -- se crea el lead igual, mismo
            # comportamiento que antes.
            reciente = Lead.objects.filter(
                rut=rut, created_at__gte=timezone.now() - _LEAD_DEDUP_VENTANA,
            ).order_by("-created_at").first()
            if reciente is not None:
                return {"ok": True, "lead_id": reciente.id}
        lead = Lead.objects.create(rut=rut, nombre=nombre, telefono=telefono, razon_interes=razon_interes)
    except Exception as exc:
        logger.warning("[crear_lead] fallo al registrar el lead: %s", exc)
        return {"ok": False, "motivo": "no se pudo registrar el lead."}
    return {"ok": True, "lead_id": lead.id}


def _consultar_especificaciones_vehiculo_impl(modelo: str, version: str = "", condicion: str = "") -> dict:
    filas = _buscar_en_catalogo(modelo, version, condicion=condicion)
    if not filas:
        return {"ok": False, "motivo": f"no tengo informacion cargada para el modelo '{modelo}'."}
    resultado = {"ok": True, "vehiculos": [_vehiculo_a_dict(v) for v in filas]}
    # Si el caller no especifico condicion y hay 0km Y usado mezclados en el
    # resultado, se lo marcamos explicito al LLM en vez de dejar que elija
    # una fila arbitrariamente -- a pedido del usuario (auditoria previa a
    # la prueba de Astara del 2026-09-01, docs/PENDIENTES.md): si el cliente
    # no especifico que busca, hay que preguntarle en vez de asumir.
    if not condicion.strip() and len({v.condicion for v in filas}) > 1:
        resultado["aviso"] = (
            "Existen versiones 0km y usadas de este modelo -- preguntale al "
            "cliente cual quiere antes de cotizar o comparar precios."
        )
    return resultado


def _comparar_vehiculos_impl(modelos: list[str], condicion: str = "") -> dict:
    resultado, no_encontrados = {}, []
    hay_ambiguedad = False
    for modelo in modelos:
        filas = _buscar_en_catalogo(modelo, condicion=condicion)
        if filas:
            resultado[modelo] = [_vehiculo_a_dict(v) for v in filas]
            if not condicion.strip() and len({v.condicion for v in filas}) > 1:
                hay_ambiguedad = True
        else:
            no_encontrados.append(modelo)
    salida = {"ok": True, "vehiculos": resultado, "no_encontrados": no_encontrados}
    if hay_ambiguedad:
        salida["aviso"] = (
            "Al menos uno de estos modelos tiene versiones 0km y usadas "
            "mezcladas -- preguntale al cliente cual quiere antes de comparar precios."
        )
    return salida


@tool(parse_docstring=True)
async def simular_financiamiento(precio: float, plazo_meses: int, pie: float = 0, porcentaje_pie: float = 0,
                                  modelo: str = "", version: str = "", gastos_adicionales: float = 0,
                                  condicion: str = "", tasa_mensual: float = 0) -> dict:
    """Simula un crédito (amortización francesa) para comprar un vehículo.

    NUNCA calcules tú el pie ni la cuota de memoria -- pasa los datos y deja
    que esta herramienta haga la cuenta, incluso si ya la hiciste antes en la
    conversación (un precio distinto da un pie/cuota distintos aunque el
    porcentaje sea el mismo).

    Args:
        precio: precio del vehículo (referencial -- si hay match de modelo/version en el catálogo, ese precio real reemplaza a este)
        plazo_meses: plazo en meses, entero entre 6 y 60
        pie: monto del pie en pesos, si el cliente dio un monto exacto. Dejar en 0 y usar porcentaje_pie en su lugar si el cliente dio (o tú vas a proponer) un porcentaje -- nunca multipliques precio x porcentaje tú mismo
        porcentaje_pie: pie como fracción del precio (ej. 0.5 para 50%), si el cliente dio o acepto un porcentaje en vez de un monto. Usar esto en vez de "pie" para que el monto siempre se recalcule contra el precio real de esta llamada -- exactamente uno de pie/porcentaje_pie debe venir informado, nunca ambos
        modelo: modelo del vehículo que se está cotizando (siempre que se conozca -- ancla el precio real del catálogo)
        version: version específica del modelo, si el cliente la menciono
        gastos_adicionales: gastos adicionales financiados, opcional, 0 si no se mencionan
        condicion: "0km" o "usado", si el cliente ya específico cual quiere financiar. Si no se sabe, dejar vacío -- se asume 0km (nunca ancla el precio de un usado sin que el cliente lo haya pedido explícitamente)
        tasa_mensual: tasa mensual como decimal (ej. 0.012 para 1,2%), SOLO si el cliente menciono la tasa real de un banco/financista externo (no Astara) y quieres calcularle la cuota exacta con esa tasa. Dejar en 0 para usar la tasa vigente de Astara (1,99%). Mostra el resultado como información neutral -- nunca lo uses para decirle al cliente cuál opción "conviene más", eso es una recomendación financiera que no te corresponde dar
    """
    # async + sync_to_async(thread_sensitive=True): ver comentario en
    # bot/business/catalogo.py::listar_catalogo -- misma razon (esta tool
    # toca VehiculoCatalogo via _resolver_precio_catalogo).
    return await sync_to_async(_simular_financiamiento_impl, thread_sensitive=True)(
        precio, pie or None, plazo_meses, gastos_adicionales, modelo, version, condicion,
        porcentaje_pie or None, tasa_mensual or None,
    )


@tool(parse_docstring=True)
async def crear_lead(rut: str, nombre: str, telefono: str, razon_interes: str) -> dict:
    """Registra un lead para que el equipo comercial contacte al cliente.

    Usar SOLO cuando el cliente quiera avanzar más allá de información y
    simulación (agendar test drive, cotización formal, reserva) -- pedir
    precio o simular crédito NO es intención real de compra por si solo.

    Args:
        rut: RUT del cliente
        nombre: nombre completo del cliente
        telefono: telefono de contacto (si el número de WhatsApp ya es confiable, usarlo directo)
        razon_interes: resumen breve de por qué está interesado, a partir de la conversación
    """
    return await sync_to_async(_crear_lead_impl, thread_sensitive=True)(rut, nombre, telefono, razon_interes)


@tool(parse_docstring=True)
async def consultar_especificaciones_vehiculo(modelo: str, version: str = "", condicion: str = "") -> dict:
    """Consulta precio y especificaciones tecnicas reales de UN modelo de vehículo, según el catálogo cargado.

    Usar cuando el cliente pregunta específicamente por un modelo (no para comparar varios).

    El resultado puede traer "precio" (de lista), "precio_contado" y "precio_financiado" por separado --
    son 3 formas de pago del MISMO vehículo, no elijas uno solo para mostrarle al cliente, muéstralos
    diferenciados si están disponibles. También puede traer "condicion" (0km/usado) por vehículo; si el
    resultado trae un "aviso" pidiendo confirmar la condicion, pregúntale al cliente si busca 0km o
    usado ANTES de cotizar o afirmar un precio -- no asumas.

    Args:
        modelo: modelo del vehículo (ej. "Arkana")
        version: version específica, si se conoce -- sin ella, se devuelven todas las versiones cargadas de ese modelo
        condicion: "0km" o "usado", si el cliente ya específico cual busca. Dejar vacío si no lo dijo.
    """
    return await sync_to_async(_consultar_especificaciones_vehiculo_impl, thread_sensitive=True)(modelo, version, condicion)


@tool(parse_docstring=True)
async def comparar_vehiculos(modelos: list[str], condicion: str = "") -> dict:
    """Compara precio y especificaciones reales entre 2 o más modelos de vehículo, según el catálogo cargado.

    Mismo criterio de "precio"/"precio_contado"/"precio_financiado" y "condicion" que
    consultar_especificaciones_vehiculo -- si el resultado trae un "aviso", pregúntale al cliente
    0km o usado antes de comparar precios.

    Args:
        modelos: lista de modelos a comparar (ej. ["Arkana", "Duster"])
        condicion: "0km" o "usado", si el cliente ya específico cual busca. Dejar vacío si no lo dijo.
    """
    return await sync_to_async(_comparar_vehiculos_impl, thread_sensitive=True)(modelos, condicion)


def _simular_por_cuota_impl(cuota_objetivo, pie=0, plazo_meses=48, tasa_mensual=None) -> dict:
    """Camino inverso de _simular_financiamiento_impl: en vez de precio ->
    cuota, va de cuota -> precio alcanzable. Despeja el capital de la
    amortizacion francesa, P = cuota * (1 - (1+i)^-n) / i, y le suma el pie.

    Existe porque el docx (S8 "cuota objetivo", S24 "quiero pagar maximo
    $400.000 mensual") plantea el presupuesto por la cuota y no por el precio,
    y sin esto el LLM tendria que despejar la formula de memoria -- justo el
    tipo de cuenta a ojo que ya causo un bug real de pie/monto inconsistente
    en produccion (docs/PENDIENTES.md, conversacion 14)."""
    try:
        cuota_objetivo = float(cuota_objetivo)
        pie = float(pie or 0)
        plazo_meses = int(plazo_meses)
    except (TypeError, ValueError):
        return {"ok": False, "motivo": "cuota_objetivo, pie y plazo_meses deben ser valores numericos."}
    if cuota_objetivo <= 0:
        return {"ok": False, "motivo": "la cuota objetivo debe ser mayor que cero."}
    if pie < 0:
        return {"ok": False, "motivo": "el pie no puede ser negativo."}
    if not (_PLAZO_MIN_MESES <= plazo_meses <= _PLAZO_MAX_MESES):
        return {"ok": False, "motivo": f"el plazo debe ser un numero entero entre {_PLAZO_MIN_MESES} y {_PLAZO_MAX_MESES} meses."}

    i = _TASA_MENSUAL if tasa_mensual is None else float(tasa_mensual)
    if i <= 0:
        return {"ok": False, "motivo": "tasa_mensual debe ser mayor que cero."}

    monto_financiable = cuota_objetivo * (1 - (1 + i) ** -plazo_meses) / i
    precio_alcanzable = monto_financiable + pie

    from bot.business.usados import _buscar_vehiculos_impl
    sugeridos = _buscar_vehiculos_impl(precio_max=int(precio_alcanzable), limite=4)

    return {
        "ok": True,
        "cuota_objetivo": round(cuota_objetivo),
        "plazo_meses": plazo_meses,
        "pie": round(pie),
        "tasa_mensual_referencial": i,
        "monto_financiable": round(monto_financiable),
        "precio_alcanzable": round(precio_alcanzable),
        "vehiculos_en_ese_rango": sugeridos.get("vehiculos", []),
        "total_en_ese_rango": sugeridos.get("total_encontrados", 0),
        "disclaimer": (
            "Simulacion referencial para efectos de demostracion. Financiamiento "
            "sujeto a evaluacion y condiciones de la entidad financiera."
        ),
    }


@tool(parse_docstring=True)
async def simular_por_cuota(cuota_objetivo: float, pie: float = 0, plazo_meses: int = 48) -> dict:
    """Calcula hasta que precio de vehículo alcanza el cliente a partir de la
    cuota mensual que quiere pagar, y devuelve vehículos reales en ese rango.

    Úsala cuando el cliente plantee su presupuesto como cuota ("quiero pagar
    como $400.000 al mes") en vez de como precio total. NUNCA despejes vos la
    formula: pasa los datos y deja que la herramienta haga la cuenta.

    Args:
        cuota_objetivo: cuota mensual que el cliente quiere pagar, en pesos
        pie: monto del pie en pesos que tiene disponible, 0 si no lo dijo
        plazo_meses: plazo a simular, entero entre 12 y 60 (48 si el cliente no indico uno)
    """
    return await sync_to_async(_simular_por_cuota_impl, thread_sensitive=True)(
        cuota_objetivo, pie, plazo_meses,
    )
