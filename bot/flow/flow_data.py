"""Normalizacion deterministica de `Conversation.flow_data`.

POR QUE EXISTE (conversacion 29 de produccion, 2026-09-03):

`flow_data` es el diccionario de "datos ya conocidos del contacto" y se le
inyecta COMPLETO al especialista en el system prompt en cada turno (ver
`bloque_flow_data` en bot/flow/agents/custom.py). O sea que todo lo que se
acumule ahi lo lee el modelo en cada respuesta: cuesta tokens y, peor, lo puede
confundir. Despues de 4 mensajes de un cliente real quedo asi:

    {'modelo_interes': '', 'plazo_compra': '', 'presupuesto': '$8.000.000',
     'comuna': '', 'monto_pie': 8000000, 'plazo_actual': 24,
     'plazo_preferido': None, 'plazo': '12', 'cuota_mensual': '$424.312',
     'monto_financiar': '$4.490.000', 'costo_total': '$13.091.743'}

Tres defectos concretos:

1. 4 de 11 claves vacias (`''` / `None`). No aportan nada y ocupan contexto.
2. Tres claves para el MISMO concepto y contradiciendose: el cliente cambio de
   24 a 12 cuotas y quedaron las dos vivas (`plazo_actual: 24` junto a
   `plazo: '12'`). El modelo lee las dos y puede contradecirse.
3. Montos como string formateado (`'$424.312'`) en vez de numero, imposibles de
   comparar o sumar sin volver a parsear.

Esto NO se arregla pidiendoselo mejor al prompt -- leccion cara de este repo:
lo que el modelo no hace de forma confiable se arregla en codigo.

POR QUE FAMILIAS POR PATRON Y NO UNA TABLA DE SINONIMOS: se sondeo el extractor
real (modelo de routing de OpenRouter) 4 veces sobre los dos turnos de
financiamiento de esa misma conversacion, y para el mismo concepto invento
`plazo_actual`, `plazo_financiado`, `plazo_cliente_deseado`,
`plazo_minimo_aceptable`, `plazo_minimo_aceptado`, `preferencia_plazo` y
`cuotas`; para el presupuesto, `presupuesto` y `monto_presupuesto`; para el
auto, `modelo_interes` y `modelo_auto`. `extracted_data` es un objeto abierto
(ver SCHEMA_METADATOS) y el modelo va a seguir inventando nombres nuevos, asi
que una lista cerrada de sinonimos envejece mal por diseno. Cada familia se
define entonces por un patron sobre el nombre de la clave, con las excepciones
escritas a mano donde el patron atraparia otro concepto.

EL VOCABULARIO CANONICO NO ES INVENTADO: son los nombres de campo de
`LeadComercial` (bot/models.py) que el propio modelo ya ve en el schema de la
tool `registrar_datos_lead` (bot/business/prospeccion.py). Asi `flow_data` y el
lead del CRM hablan el mismo idioma en vez de contradecirse.

Se aplica en tres puntos, todos AGUAS ARRIBA de los dos merges de flow_data
(bot/flow/graph.py y bot/whatsapp/cola_envio.py, que esta sesion no toca):

- `bot/flow/respuesta.py` y `bot/flow/extractor_metadatos.py`, sobre el
  `extracted_data` recien salido del LLM. Es lo que hace que el merge funcione:
  si el turno nuevo trae `plazo_financiado` ya canonizado a `plazo`, el
  `{**previo, **nuevo}` PISA el valor viejo en vez de dejar los dos.
- `Conversation.set_flow`/`get_flow` (bot/models.py), como red final y para que
  las conversaciones que ya tienen basura guardada se limpien solas en su
  proximo turno, sin migracion.
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# (nombre canonico, patron sobre la clave, claves que quedan FUERA de la familia).
#
# El orden importa: gana la primera familia que matchea.
#
# Las excepciones son la parte delicada, y cada una responde a un concepto
# distinto que el patron atraparia por accidente:
# - `plazo_compra` NO es el plazo del credito: es EN CUANTO TIEMPO piensa
#   comprar ("30 dias"), campo real de LeadComercial que pesa 18 puntos del
#   lead score. Unirlos haria que un "en 3 meses" pise una simulacion a 12
#   cuotas.
# - `plazo_entrega` es cuando le entregan el auto, otro concepto mas.
# - `cuota` a secas no entra en ninguna familia de cuota: puede ser la que el
#   cliente QUIERE pagar (`cuota_objetivo`) o la que devolvio la simulacion
#   (`cuota_mensual`), y no hay forma deterministica de distinguirlas.
# - las familias de vehiculo y cuota se definen por lista explicita y no por
#   patron amplio, porque `vehiculo_actual`/`modelo_actual` (el auto que el
#   cliente entrega en parte de pago) y `vehiculo_codigo` (el codigo del stock)
#   son datos DISTINTOS que un patron sobre "vehiculo"/"modelo" se comeria.
FAMILIAS = (
    # numero de cuotas del credito -- el caso de la conversacion 29
    ("plazo",
     re.compile(r"^(plazo(_.+)?|.+_plazo|(numero|cantidad)(_de)?_cuotas|n_cuotas|cuotas)$"),
     {"plazo_compra", "plazo_entrega"}),
    # LeadComercial.presupuesto
    ("presupuesto", re.compile(r"^(.+_)?presupuesto(_.+)?$"), set()),
    # LeadComercial.pie_disponible. `cuota_inicial`/`cuota_pie` entran aca y no
    # en las familias de cuota: en otros paises "cuota inicial" ES el pie, y el
    # modelo a veces lo escribe asi.
    ("pie_disponible", re.compile(r"^((.+_)?pie(_.+)?|cuota_(inicial|pie))$"), set()),
    # LeadComercial.cuota_objetivo -- la cuota que el cliente busca. Va ANTES
    # que cuota_mensual justamente para que el patron amplio de abajo no se la
    # coma.
    ("cuota_objetivo",
     re.compile(r"^cuota_(objetivo|deseada|buscada|maxima|max|pretendida|"
                r"que_puede_pagar)$"), set()),
    # resultado de la simulacion. El patron es amplio (`cuota` y cualquier
    # `cuota_*` que sobreviva) a proposito: en el sondeo el modelo escribio
    # `cuota_mes_actual` para la simulacion vieja y `cuota_mensual` para la
    # nueva, y las dos convivian en flow_data con montos distintos -- el mismo
    # defecto que este modulo viene a cerrar. El costo de la ambiguedad de
    # `cuota` a secas (podria ser la que el cliente quiere pagar) es menor que
    # el de dos cuotas contradiciendose en el prompt.
    ("cuota_mensual",
     re.compile(r"^(cuota(_.+)?|(valor|monto|importe)_cuota(_.+)?)$"), set()),
    ("monto_financiar",
     re.compile(r"^(monto_(a_)?financiar|monto_(del_)?credito|saldo_(a_)?financiar|"
                r"monto_financiado)$"), set()),
    # `monto_total_operacion`, `costo_total`, `total_a_pagar`... todos el mismo
    # numero (observados en el sondeo).
    ("costo_total", re.compile(r"^((costo|monto|valor)_)?total(_.+)?$"), set()),
    # LeadComercial.vehiculo_interes -- que auto quiere
    ("vehiculo_interes",
     re.compile(r"^((modelo|auto|vehiculo)(_de)?_(interes|interesado|buscado|elegido)|"
                r"modelo_auto|modelo)$"), set()),
    # LeadComercial.comuna / .nombre
    ("comuna", re.compile(r"^comuna_(cliente|contacto)$"), set()),
    ("nombre", re.compile(r"^nombre_(cliente|contacto)$"), set()),
)

# Conceptos que SIEMPRE son una cantidad. Solo para estas claves se acepta un
# numero con la unidad pegada ("24 cuotas", "12 meses"): el modelo mezcla las
# dos formas entre turnos y sin esto `plazo` queda a veces 24 y a veces
# "24 cuotas", que es justo la comparacion que despues no se puede hacer.
_CLAVES_NUMERICAS = {"plazo", "presupuesto", "pie_disponible", "cuota_objetivo",
                     "cuota_mensual", "monto_financiar", "costo_total"}

# Las claves de _CLAVES_NUMERICAS que son DINERO. `plazo` es la que queda
# afuera, y esa es toda la razon de que esta lista exista aparte: "12m" en un
# plazo son 12 MESES y en un monto son 12 millones. El sufijo "m" solo se
# interpreta como magnitud para estas.
_CLAVES_MONTO = _CLAVES_NUMERICAS - {"plazo"}

# Valores que el modelo escribe cuando en realidad NO tiene el dato. Se tratan
# igual que un vacio: guardarlos es peor que no tener la clave, porque el
# especialista los lee como si fueran informacion.
_SIN_DATO = {
    "", "none", "null", "nan", "n/a", "na", "-", "--", "?",
    "no especificado", "sin especificar", "no especifica", "no aplica",
    "desconocido", "no definido", "sin definir", "no informado", "pendiente",
}

# Claves donde un string de puros digitos NO es un numero: convertirlo perderia
# ceros a la izquierda o el sentido del dato.
_NUNCA_NUMERICAS = ("rut", "telefono", "fono", "celular", "patente", "codigo",
                    "folio", "vin", "chasis", "wa_id", "email", "numero_contacto")

# Montos como los escribe el modelo en Chile: "$8.000.000", "424.312",
# "8000000", "1.234,50", "8000000 CLP". El punto como separador de miles se
# acepta solo en grupos de 3 exactos, que es lo que lo hace no ambiguo contra
# un decimal.
_RE_NUMERO = re.compile(
    r"^\$?\s*(\d{1,3}(?:\.\d{3})+|\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:[,](\d{1,2}))?\s*(?:clp|pesos)?$",
    re.IGNORECASE,
)

# Solo para _CLAVES_NUMERICAS: el mismo numero con la unidad escrita al lado.
_RE_NUMERO_CON_UNIDAD = re.compile(
    r"^\$?\s*(\d{1,3}(?:\.\d{3})+|\d+)\s*"
    r"(cuotas?|meses|mes|anos|anios|años|pagos?)$",
    re.IGNORECASE,
)

# Como se dice un monto en Chile cuando no se escribe completo. Medido contra
# el LLM real el 2026-09-07 (n=21 presupuestos evidenciados en el texto del
# turno): pidiendole el monto como entero, "15 millones" y "20 palos" quedaban
# en 0 -- 3 de 21 perdidos, y son 15 puntos del lead score. Pidiendoselo como
# el cliente lo dijo y convirtiendo ACA, 21 de 21. Mismo principio que el resto
# de este modulo: lo que el modelo no hace de forma confiable se arregla en
# codigo.
_MAGNITUDES = {
    "millon": 1_000_000, "millones": 1_000_000,
    "palo": 1_000_000, "palos": 1_000_000, "palito": 1_000_000, "palitos": 1_000_000,
    "mil": 1_000, "luca": 1_000, "lucas": 1_000, "k": 1_000,
}

# "M"/"MM" es como se abrevia un monto en Chile ("$27M"), y faltaba teniendo
# "k" desde el principio. Va SEPARADO de _MAGNITUDES, y no sumado ahi, porque
# solo vale para _CLAVES_MONTO: ver el comentario de esa lista.
#
# CASO REAL que lo motiva (conversacion del 2026-09-07, turno 7): el cliente
# dijo "27 millones" y quedo el entero 27000000; dos turnos despues el extractor
# lo reescribio "$27M", que no se sabia leer, y el bot le contesto "Segun tu
# presupuesto de $27M". El dato no se perdio del lead --
# bot/business/prospeccion.py lo descarta antes de escribir una columna
# numerica-- pero si degrado el flow_data que el especialista lee en cada turno.
_MAGNITUDES_DE_MONTO = {"m": 1_000_000, "mm": 1_000_000}
_RE_MONTO_COLOQUIAL = re.compile(
    r"^\$?\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:[,.](\d{1,2}))?\s*"
    r"([a-záéíóúñ]+)(?:\s+(?:de\s+)?(?:pesos|clp))?$",
    re.IGNORECASE,
)


def clave_canonica(clave: str) -> str:
    """El nombre canonico de la familia a la que pertenece `clave`, o la clave
    tal cual si no cae en ninguna."""
    for canonica, patron, excepciones in FAMILIAS:
        if clave in excepciones:
            return clave
        if patron.match(clave):
            return canonica
    return clave


def _sin_tildes(texto: str) -> str:
    """Minusculas sin acentos, para comparar la magnitud que escribio el modelo
    ("millon" / "millón") contra _MAGNITUDES sin duplicar cada entrada."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def _es_vacio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, str):
        return valor.strip().lower() in _SIN_DATO
    if isinstance(valor, (dict, list, tuple, set)):
        # `{}` / `[]` son ruido; `False` y `0` NO -- son datos legitimos
        # ("no tiene parte de pago", "pie 0") y por eso no se chequea con
        # `not valor` a secas.
        return len(valor) == 0
    return False


def _a_numero(clave: str, valor):
    """String formateado -> int/float. El resto se devuelve tal cual."""
    if not isinstance(valor, str):
        return valor
    if any(marca in clave for marca in _NUNCA_NUMERICAS):
        return valor
    texto = valor.strip()
    m = _RE_NUMERO.match(texto)
    if m:
        entero = m.group(1).replace(".", "").replace(",", "")
        return float(f"{entero}.{m.group(2)}") if m.group(2) else int(entero)
    if clave in _CLAVES_NUMERICAS:
        m = _RE_NUMERO_CON_UNIDAD.match(texto)
        if m:
            return int(m.group(1).replace(".", ""))
        m = _RE_MONTO_COLOQUIAL.match(texto)
        if m:
            unidad = _sin_tildes(m.group(3))
            factor = _MAGNITUDES.get(unidad)
            if factor is None and clave in _CLAVES_MONTO:
                factor = _MAGNITUDES_DE_MONTO.get(unidad)
            if factor:
                entero = int(m.group(1).replace(".", ""))
                # "1,5 millones" -> 1.500.000. El decimal se aplica sobre la
                # magnitud, no sobre el entero, y se redondea: un peso con
                # decimales no existe.
                if m.group(2):
                    return round((entero + float(f"0.{m.group(2)}")) * factor)
                return entero * factor
    return valor


def normalizar_flow_data(datos) -> dict:
    """Deja `flow_data` con una sola clave por concepto, sin vacios y con los
    montos como numero. Idempotente: normalizar dos veces da lo mismo.

    Precedencia cuando conviven un alias y su nombre canonico (pasa sobre todo
    con datos viejos, guardados antes de este modulo): manda el canonico si
    viene explicito; entre dos alias, el ultimo del diccionario. En la
    conversacion 29 eso deja `plazo: 12` -- el valor que el cliente eligio
    ultimo -- y descarta `plazo_actual: 24`.
    """
    if not isinstance(datos, dict):
        return {}
    salida: dict = {}
    canonicas_explicitas: set[str] = set()
    for clave, valor in datos.items():
        if not isinstance(clave, str):
            continue
        original = clave.strip().lower()
        if not original:
            continue
        if _es_vacio(valor):
            continue
        canonica = clave_canonica(original)
        valor = _a_numero(canonica, valor)
        es_explicita = canonica == original
        if not es_explicita and canonica in canonicas_explicitas:
            continue
        salida[canonica] = valor
        if es_explicita:
            canonicas_explicitas.add(canonica)
    return salida


def fusionar_flow_data(previo, nuevo) -> dict:
    """Une lo que ya se sabia del contacto con lo que trajo este turno.

    Reemplaza al `{**previo, **nuevo}` que hacian los dos merges (bot/flow/
    graph.py y bot/whatsapp/cola_envio.py) con UNA sola regla agregada: un
    valor nuevo que no se pudo leer como numero NO pisa a uno que si lo era.

    POR QUE (conversacion del 2026-09-07, turno 7): el cliente dijo "27
    millones" y quedo el entero 27000000. Dos turnos despues el extractor
    escribio "$27M" y el merge lo dejo pasar, asi que el especialista paso a
    leer un string donde antes tenia un numero -- y le contesto al cliente
    "Segun tu presupuesto de $27M".

    Esto NO es lo mismo que descartar el string: cuando no hay nada previo, un
    monto ilegible se guarda igual (ver el test
    test_una_magnitud_que_no_conocemos_deja_el_texto_intacto -- vale mas un
    string que el vendedor puede leer que un numero inventado). Lo unico que se
    impide es la DEGRADACION de un dato que ya estaba bien.

    El cambio de opinion del cliente sigue funcionando igual: "15 millones"
    pisa a 27000000 porque los dos son numeros. Es el caso que el merge existe
    para resolver (conversacion 29, plazo 24 -> 12).
    """
    anterior = normalizar_flow_data(previo)
    entrante = normalizar_flow_data(nuevo)
    salida = dict(anterior)
    for clave, valor in entrante.items():
        if (isinstance(valor, str)
                and clave in _CLAVES_NUMERICAS
                and isinstance(anterior.get(clave), (int, float))
                and not isinstance(anterior.get(clave), bool)):
            # El valor viejo es un numero y el nuevo no se pudo leer como tal:
            # se queda el viejo. Con logging, porque la unica otra senal de que
            # el vocabulario de montos se quedo corto es esta.
            logger.info(
                "[flow_data] %s=%r no es un numero y ya habia %r: se conserva el previo",
                clave, valor, anterior[clave],
            )
            continue
        salida[clave] = valor
    return salida
