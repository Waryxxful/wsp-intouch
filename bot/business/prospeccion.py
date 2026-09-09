"""Captura de lead comercial (docx S8/S21) y parte de pago (docx S9).

Ambas tools escriben sobre la conversacion en curso, con el wa_id inyectado
por ToolRuntime -- nunca por el LLM. Mismo criterio que bot/business/
compliance.py: un dato que identifica a la persona no se le confia al modelo.
"""

import logging
import re

from asgiref.sync import sync_to_async
from langchain.tools import ToolRuntime, tool

from bot.business.usados import _fila_por_referencia, _norm
from bot.models import (
    Conversation, LeadComercial, VehiculoPartePago,
    calcular_lead_score, clasificar_temperatura,
)

logger = logging.getLogger(__name__)

# Campos de LeadComercial que la tool puede escribir. Explicito y no
# "cualquier kwarg": sin esta lista, un nombre de argumento alucinado por el
# LLM se escribiria como atributo suelto y se perderia sin error visible.
_CAMPOS_ESCRIBIBLES = {
    "nombre", "telefono", "email", "comuna", "vehiculo_interes", "vehiculo_codigo",
    "presupuesto", "pie_disponible", "cuota_objetivo", "plazo_compra",
    "tiene_parte_pago", "vehiculo_actual", "intencion", "sentimiento",
    "urgencia", "proxima_accion", "resumen",
}

# La columna se llama plazo_compra (la lee el panel y bot/seguimiento.py), pero
# el LLM ve el parametro como "cuando_compra": ver el docstring de
# registrar_datos_lead. Los mensajes que salen hacia el modelo (datos_que_faltan,
# campos_rechazados) usan el nombre del PARAMETRO -- si le pedimos un campo con
# un nombre que su schema de tools no tiene, no sabe que mandar.
_NOMBRE_PARA_EL_LLM = {"plazo_compra": "cuando_compra"}

# Como una persona dice CUANDO piensa comprar. Se usa para dos chequeos
# distintos sobre el mismo dato (ver _validar_cuando_compra): que el valor que
# manda el LLM tenga forma de fecha, y que el cliente de verdad haya hablado de
# fechas en el chat. Deliberadamente NO incluye "ya" ni numeros sueltos: son
# demasiado frecuentes como muletilla ("ya, dale") y darian por buena cualquier
# invencion.
_RE_FECHA_COMPRA = re.compile(
    r"\bhoy\b|\bmanana\b|\baltiro\b|\bahora\b|\binmediat|\burgente\b|\bapurad[oa]\b"
    r"|\bcuanto antes\b|\blo antes posible\b|\bfin de (mes|ano|semana)\b|\bquincena\b"
    r"|\b(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b"
    r"|\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre"
    r"|octubre|noviembre|diciembre)\b"
    r"|\b(est[ae]|proxim[ao]|entrante|siguiente)\s+(semana|mes|ano|fin)\b"
    r"|\b(en|dentro de|de aca a|antes de|para|durante)\s+"
    r"((el|la|los|las|un|una|unos|unas|\d+)\s+)?(dia|dias|semana|semanas|mes|meses|ano|anos)\b"
    r"|\b\d+\s*(dia|dias|semana|semanas)\b"
)

# Un plazo de financiamiento NO es una fecha de compra, aunque los dos se midan
# en unidades de tiempo. Se chequea aparte del regex de arriba para poder
# devolverle al LLM el motivo exacto del rechazo.
_RE_PLAZO_CREDITO = re.compile(r"cuota|credito|financiamiento|\bplazo\b")


def _cliente_hablo_de_fecha(conversation) -> bool:
    """True si el cliente, con sus propias palabras, dijo algo sobre CUANDO.

    El mensaje del turno en curso ya esta guardado cuando corren las tools
    (bot/whatsapp/handlers.py lo persiste antes de invocar el grafo), asi que
    un "paso el viernes" dicho en este mismo turno cuenta."""
    return any(
        _RE_FECHA_COMPRA.search(_norm(contenido))
        for contenido in conversation.messages.filter(role="user").values_list("content", flat=True)
    )


def _validar_cuando_compra(conversation, valor: str) -> tuple[str, str]:
    """Filtra el unico campo del lead que el LLM llenaba a ojo. Devuelve
    (valor_aceptado, motivo_del_rechazo).

    Motivo (conversacion 29, 2026-09-03): el cliente pidio una simulacion "a 24
    cuotas" y nunca dijo cuando compraria; el bot guardo plazo_compra="24
    cuotas". Ese campo vale 18 puntos en calcular_lead_score, asi que el lead
    quedo en 83 = HOT en vez de 65 = WARM, y el equipo comercial prioriza con un
    dato que no existe. Midiendo el turno real contra el modelo (10 corridas) se
    vio que el error tenia DOS formas, no una: 1 corrida copio el plazo del
    credito y 3 inventaron "inmediato" de la nada. Por eso no alcanza con
    desambiguar la descripcion del parametro -- el modelo llena el campo igual,
    solo que con otra cosa.

    Se valida en codigo y no se le pide mejor criterio al LLM, mismo principio
    que _resolver_precio_usado o el wa_id de ToolRuntime: si el sistema puede
    verificarlo, no se confia en el modelo. Dos condiciones, ambas necesarias:

    1. FORMA: el valor tiene que parecerse a una fecha de compra y no a un
       plazo de credito ("24 cuotas", "48 meses").
    2. EVIDENCIA: el cliente tiene que haber hablado de fechas en el chat. Un
       "inmediato" que no dijo nadie es peor que el campo vacio: vacio el lead
       queda WARM y el vendedor sabe que falta preguntarlo.

    El costo de equivocarse esta puesto a proposito del lado seguro: si el
    cliente da una fecha con palabras que este lexico no cubre, el lead pierde
    18 puntos (queda WARM en vez de HOT) pero nadie ve un dato falso."""
    texto = _norm(valor)
    # Los tres mensajes de abajo los LEE el modelo (viajan como resultado de
    # tool), asi que van con tildes y en tuteo: el modelo imita como esta
    # escrito su corpus, no solo obedece lo que le ordena. Evidencia del
    # 2026-09-03: la regla anti-voseo del prompt global traia el ejemplo
    # "cuentame" SIN tilde y el bot le escribio "cuentame" sin tilde a un
    # contacto real tres veces.
    if _RE_PLAZO_CREDITO.search(texto):
        return "", (
            f"'{valor}' es el plazo del CRÉDITO, no cuándo piensa comprar el cliente. "
            "cuando_compra es la fecha en que quiere tener el auto (ej. 'este viernes'). "
            "Déjalo vacío."
        )
    if not _RE_FECHA_COMPRA.search(texto):
        return "", (
            f"'{valor}' no es una fecha de compra. Manda cuando_compra solo con lo que "
            "el cliente diga sobre CUÁNDO quiere comprar (ej. 'este mes', 'en 3 semanas')."
        )
    if not _cliente_hablo_de_fecha(conversation):
        return "", (
            "el cliente todavía no dijo cuándo piensa comprar. No lo deduzcas ni lo "
            "estimes: si te sirve el dato, pregúntaselo, y recién ahí mándalo."
        )
    return valor, ""


def _codigos_de_stock(referencia: str) -> set:
    """Codigos de VehiculoUsado que calzan con como el LLM nombro el vehiculo.

    Reusa el matching por subconjunto de palabras de bot/business/usados.py --
    el mismo que resuelve "la Tucson" o "Hyundai Tucson 2023" para dar precio y
    ficha -- en vez de un match propio que se desincronizaria con aquel."""
    if not (referencia or "").strip():
        return set()
    filas = (_fila_por_referencia(referencia)
             # Un auto ya vendido igual identifica lo que el cliente quiso: el
             # vendedor necesita saber por cual unidad preguntaron para ofrecer
             # algo parecido. El bot NO lo cotiza igual -- eso lo sigue
             # cuidando _fila_por_referencia en las tools de catalogo.
             or _fila_por_referencia(referencia, solo_disponibles=False))
    return {v.codigo for v in filas}


def _recortar(instancia, campo: str, valor):
    """Recorta un texto al max_length real de su columna.

    Django NO trunca al guardar: SQLite (tests) acepta el exceso en silencio,
    pero SQL Server -- el backend de produccion -- levanta "String or binary
    data would be truncated" y la tool revienta a mitad de conversacion,
    perdiendo TODOS los datos capturados en esa llamada. Y los campos que el
    LLM llena libremente son cortos a proposito: sentimiento y urgencia tienen
    30 caracteres, y una respuesta como "positivo, muy interesado en cerrar"
    ya son 34."""
    largo = instancia._meta.get_field(campo).max_length
    if largo and isinstance(valor, str) and len(valor) > largo:
        return valor[:largo]
    return valor


def _a_booleano_o_none(valor: str):
    """Tres estados a proposito: None = todavia no se pregunto, y por eso no
    suma al lead score; False = el cliente dijo que no, que si es una
    respuesta concreta."""
    texto = (valor or "").strip().lower()
    if texto in ("si", "sí", "true", "1"):
        return True
    if texto in ("no", "false", "0"):
        return False
    return None


def _registrar_datos_lead_impl(wa_id: str, datos: dict) -> dict:
    conversation = Conversation.objects.filter(wa_id=wa_id).first()
    if conversation is None:
        return {"ok": False, "motivo": "no encontré la conversación."}

    lead, _ = LeadComercial.objects.get_or_create(
        conversation=conversation,
        defaults={"telefono": wa_id, "nombre": conversation.name or ""},
    )

    datos = dict(datos or {})
    rechazados = {}

    # Los dos campos que el LLM llenaba mal se filtran ANTES del bucle, para que
    # nunca lleguen a setattr: una vez escritos ya suman al lead score y los ve
    # el vendedor en el panel.
    cuando = (datos.get("plazo_compra") or "").strip()
    if cuando:
        aceptado, motivo = _validar_cuando_compra(conversation, cuando)
        if not aceptado:
            datos.pop("plazo_compra")
            rechazados["cuando_compra"] = motivo

    # Un codigo inventado es peor que ninguno: el vendedor busca US007 en la
    # planilla y encuentra otro auto. Si el que mando el LLM no existe se
    # descarta y abajo se deriva el real desde vehiculo_interes.
    pedido = str(datos.get("vehiculo_codigo") or "").strip()
    codigo_pedido = next(
        (c for c in _codigos_de_stock(pedido) if c.upper() == pedido.upper()), "",
    ) if pedido else ""
    if pedido and not codigo_pedido:
        datos.pop("vehiculo_codigo")
        rechazados["vehiculo_codigo"] = (
            f"'{pedido}' no existe en el stock. No hace falta que lo mandes: "
            "el sistema lo resuelve solo a partir de vehiculo_interes."
        )
    elif codigo_pedido:
        # Se guarda con la escritura de la planilla ("US011", no "us011"): es
        # el string que el vendedor va a buscar tal cual.
        datos["vehiculo_codigo"] = codigo_pedido

    ignorados = []
    for campo, valor in datos.items():
        if campo not in _CAMPOS_ESCRIBIBLES:
            ignorados.append(campo)
            continue
        # Un valor vacio NO borra lo que ya se sabia: el LLM manda el dict
        # completo en cada turno y suele omitir (o mandar en blanco) lo que no
        # se hablo en ese turno. Sin esta guarda, cada turno pisaria con vacio
        # los datos capturados en los turnos anteriores y el lead terminaria
        # la conversacion mas pobre que a la mitad.
        if valor in (None, "", []):
            continue
        setattr(lead, campo, _recortar(lead, campo, valor))

    # El codigo del stock se DERIVA, no se le pide al LLM. En la conversacion
    # 29 el lead quedo con vehiculo_interes="Suzuki Swift 2023" y
    # vehiculo_codigo="" pese a que ese auto es el US011 de la planilla: el
    # vendedor tenia que buscarlo a mano. El sistema ya sabe resolverlo (es el
    # mismo matching con el que cotiza el precio), asi que preguntarselo al
    # modelo solo agrega una via de error.
    if lead.vehiculo_interes and not codigo_pedido:
        codigos = _codigos_de_stock(lead.vehiculo_interes)
        if len(codigos) == 1:
            lead.vehiculo_codigo = codigos.pop()
        elif lead.vehiculo_codigo not in codigos:
            # Ambiguo ("una SUV") o sin match, y el codigo guardado ya no
            # corresponde a lo que el cliente dice querer ahora: se limpia. Un
            # codigo del auto que descarto hace tres turnos manda al vendedor a
            # la unidad equivocada; vacio, al menos, es honesto.
            lead.vehiculo_codigo = ""

    lead.lead_score = calcular_lead_score(lead)
    lead.temperatura = clasificar_temperatura(lead.lead_score)
    lead.save()

    # NO se refleja en Conversation.lead_class. Esa columna ya tiene otro
    # escritor: el LLM la devuelve en su JSON y bot/whatsapp/handlers.py la
    # guarda con un `conv.asave()` completo al final del turno, sobre una
    # instancia cargada ANTES de que corriera el grafo. Con dos escritores
    # sobre la misma columna, el ultimo en escribir gana: la temperatura
    # calculada aca sobrevivia unos segundos y despues volvia al valor previo
    # al turno. La fuente de verdad es LeadComercial.temperatura, que sale de
    # un score reproducible (calcular_lead_score) y no de una estimacion del
    # modelo -- ver Campana.metricas(), su unico consumidor.
    resultado = {
        "ok": True,
        "lead_score": lead.lead_score,
        "temperatura": lead.temperatura,
        "datos_que_faltan": sorted(
            _NOMBRE_PARA_EL_LLM.get(c, c)
            for c in ("nombre", "telefono", "vehiculo_interes", "presupuesto", "plazo_compra")
            if not getattr(lead, c)
        ),
    }
    if ignorados:
        resultado["campos_ignorados"] = sorted(ignorados)
    if rechazados:
        # Se le devuelve el motivo al modelo (y no un rechazo mudo) para que no
        # reintente con otra variante del mismo dato inventado en el turno
        # siguiente: el resto de los campos SI se guardaron.
        resultado["campos_rechazados"] = rechazados
    return resultado


# Los montos que el LLM manda como texto ("15 millones") y las columnas que son
# IntegerField. Sin la conversion, un setattr con el string revienta al guardar
# en SQL Server -- y no en SQLite, o sea que el test pasa y produccion cae.
_CAMPOS_MONTO = ("presupuesto", "pie_disponible", "cuota_objetivo")


def _a_entero_o_none(campo: str, valor):
    """El monto como int, o None si no se pudo leer.

    Reusa el parser de bot/flow/flow_data.py (el mismo que canoniza flow_data)
    en vez de uno propio: ahi viven los formatos chilenos ya medidos, incluidos
    los coloquiales ("15 millones", "20 palos", "500 mil")."""
    from bot.flow.flow_data import _a_numero

    if valor in (None, "", 0):
        return None
    convertido = _a_numero(campo, valor) if isinstance(valor, str) else valor
    if isinstance(convertido, bool):
        return None
    if isinstance(convertido, (int, float)):
        return int(convertido)
    # Se pierde el dato a proposito en vez de escribir basura en una columna
    # numerica, pero se deja ruido: es la unica senal de que el vocabulario de
    # montos se quedo corto.
    logger.warning("[lead] no pude leer %s=%r como monto, se descarta", campo, valor)
    return None


def registrar_lead_de_metadatos(wa_id: str, lead: dict) -> dict:
    """Escribe el lead comercial desde los metadatos del turno.

    ES EL MISMO CAMINO QUE LA TOOL, a proposito: llama a
    `_registrar_datos_lead_impl`, que es lo que conserva las tres garantias que
    no dependen de quien provea los campos -- la validacion de `cuando_compra`
    (_validar_cuando_compra), la derivacion de `vehiculo_codigo` contra el
    stock real, y que un valor vacio NO pise lo que ya se sabia. Duplicar la
    escritura aca habria dejado dos escritores de LeadComercial con reglas
    distintas, que es el defecto que este modulo ya documenta para
    Conversation.lead_class.

    POR QUE EXISTE (docs/PENDIENTES.md 32.a): antes estos datos entraban por la
    tool `registrar_datos_lead`, que el especialista pedia en una SEGUNDA ronda
    -- una llamada entera al LLM, 4,53s de mediana, en el 17,3% de los turnos
    medidos. Ahora entran por la llamada que ya se hacia igual: la del
    extractor cuando la respuesta sale en prosa, o la de `responder` cuando
    sale por tool. Los dos caminos aterrizan aca.
    """
    datos = dict(lead or {})
    # El nombre del parametro es el que ve el LLM; el de la columna es el que ve
    # el panel. Misma traduccion que hacia el wrapper de la tool.
    if "cuando_compra" in datos:
        datos["plazo_compra"] = datos.pop("cuando_compra")
    for campo in _CAMPOS_MONTO:
        if campo in datos:
            datos[campo] = _a_entero_o_none(campo, datos[campo])
    return _registrar_datos_lead_impl(wa_id, datos)


# Los campos que JUSTIFICAN abrir un lead: antecedentes que el cliente entrego.
# Son los que pesan en calcular_lead_score, menos `telefono` (que sale del wa_id
# y por lo tanto lo tiene cualquiera que escriba) y menos `tiene_parte_pago`
# (lo escribe registrar_parte_pago, que ya crea la fila por su cuenta).
_ANTECEDENTES_QUE_ABREN_LEAD = frozenset({
    "nombre", "email", "comuna", "vehiculo_interes",
    "presupuesto", "pie_disponible", "cuota_objetivo", "cuando_compra",
})


def registrar_lead_del_turno(wa_id: str, lead) -> None:
    """`registrar_lead_de_metadatos` con la guarda y el aislamiento del turno.

    ESCRITOR DEL DOMINIO AUTOMOTRIZ HEREDADO (LeadComercial). En wsp_intouch
    ya no lo llama ningun camino de salida: los dos -- la cola de envio
    (respuesta en prosa) y el bloque post-grafo de handlers.py (respuesta por
    la tool `responder`) -- llaman a
    bot.business.lead_intouch.registrar_lead_del_turno, que escribe
    LeadInTouch. Sigue vivo porque `registrar_parte_pago` y la cobertura
    heredada lo usan. Si vas a reapuntar un llamador, reapuntalo a lead_intouch
    y no aca: los antecedentes que abren un lead de este modulo no comparten un
    solo nombre con el contrato de InTouch, asi que un lead de InTouch entra
    por aca y se descarta sin fila y sin log.

    QUE ABRE UN LEAD Y QUE NO, que es la parte delicada de este cambio. La tool
    creaba la fila cuando el especialista DECIDIA llamarla, o sea que la
    decision de "aca hay un lead" la tomaba el modelo grande. El extractor, en
    cambio, clasifica TODOS los turnos: medido sobre 58 turnos reales, llena
    `resumen` y `proxima_accion` en el 100% y `intencion` en el 93%. Si
    cualquiera de esos campos abriera la fila, un "hola" crearia un lead, la
    lista del panel se llenaria de filas COLD vacias y -- peor --
    `Campana.metricas()` cuenta `LeadComercial` como conversiones, asi que toda
    campana reportaria 100% de conversion.

    Por eso la fila se abre SOLO con un antecedente que el cliente haya
    entregado (_ANTECEDENTES_QUE_ABREN_LEAD). Los campos de lectura de la
    conversacion (intencion, sentimiento, urgencia, proxima_accion, resumen) se
    escriben igual, pero sobre un lead que YA existe: mantenerlos frescos es
    justamente para lo que sirven.

    Las otras dos reglas: un `lead` vacio no hace nada (el modelo manda el
    objeto completo en blanco cuando no capturo nada, porque `strict: true` se
    lo exige), y un fallo escribiendo el lead no puede tumbar lo que ya venia
    hecho del turno -- es lo ultimo que pasa y ya nadie lo espera, mismo
    criterio que el spec R5 le aplica al extractor.

    El log va en error y no en warning porque el sintoma de perder esto es
    silencioso: el chat sale igual de bien y el vendedor simplemente no ve el
    dato.
    """
    if not isinstance(lead, dict):
        return
    con_valor = {c for c, v in lead.items() if v not in (None, "", 0)}
    if not con_valor:
        return
    try:
        if not (con_valor & _ANTECEDENTES_QUE_ABREN_LEAD) and not (
                LeadComercial.objects.filter(conversation__wa_id=wa_id).exists()):
            return
        registrar_lead_de_metadatos(wa_id, lead)
    except Exception:
        logger.error("[lead] no pude registrar el lead de %s", wa_id, exc_info=True)


def _registrar_parte_pago_impl(wa_id: str, **campos) -> dict:
    conversation = Conversation.objects.filter(wa_id=wa_id).first()
    if conversation is None:
        # Sin conversacion, la deduplicacion de abajo degenera en
        # conversation__isnull=True y el registro de un contacto anonimo pisa el
        # de otro: dos personas distintas ofreciendo un "Toyota Corolla"
        # terminan en UNA fila, sin telefono y sin chat al que volver. Ademas el
        # equipo comercial recibiria una tasacion imposible de contactar.
        return {"ok": False, "motivo": "no encontré la conversación."}
    marca_modelo = (campos.get("marca_modelo") or "").strip()
    if not marca_modelo:
        return {"ok": False, "motivo": "falta al menos la marca y modelo del vehículo."}

    # Uno por conversacion y vehiculo: si el cliente corrige el kilometraje o
    # agrega la patente mas adelante, se completa el registro existente en vez
    # de crear una solicitud de tasacion duplicada para el equipo comercial
    # (mismo problema real que ya tuvimos con los Incident de handoff).
    registro = VehiculoPartePago.objects.filter(
        conversation=conversation, marca_modelo__iexact=marca_modelo, estado="solicitada",
    ).first()
    if registro is None:
        registro = VehiculoPartePago(conversation=conversation, marca_modelo=marca_modelo)

    for campo in ("anio", "version", "km", "patente", "estado_general",
                  "tiene_deuda", "ubicacion", "observaciones"):
        valor = campos.get(campo)
        if valor not in (None, ""):
            setattr(registro, campo, valor)
    registro.save()

    _registrar_datos_lead_impl(conversation.wa_id, {
        "tiene_parte_pago": True, "vehiculo_actual": marca_modelo,
    })

    faltan = [c for c in ("anio", "km", "patente", "estado_general") if not getattr(registro, c)]
    return {
        "ok": True,
        "id": registro.id,
        "datos_que_faltan": faltan,
        "aviso": (
            "Quedo registrada la solicitud de tasacion. NUNCA le des un monto "
            "de tasacion al cliente: decile que un especialista la revisa y lo "
            "contacta."
        ),
    }


@tool(parse_docstring=True)
async def registrar_datos_lead(
    runtime: ToolRuntime,
    nombre: str = "", email: str = "", comuna: str = "",
    vehiculo_interes: str = "", presupuesto: int = 0, pie_disponible: int = 0,
    cuota_objetivo: int = 0, cuando_compra: str = "", intencion: str = "",
    sentimiento: str = "", urgencia: str = "", proxima_accion: str = "",
    resumen: str = "",
) -> dict:
    """Guarda los antecedentes comerciales del cliente a medida que aparecen
    en la conversación, y devuelve su lead score y que datos faltan.

    Llamala apenas el cliente entregue CUALQUIERA de estos datos, sin esperar
    a tenerlos todos -- se va completando de a poco. Manda solo los campos
    nuevos de este turno; los que ya guardaste antes no se pierden. El score y
    la temperatura los calcula el sistema, tú no los inventes.

    Manda únicamente datos que el cliente HAYA DICHO. Nada de deducir ni
    estimar: un campo vacío se puede preguntar después, uno inventado se le
    entrega al vendedor como si fuera cierto.

    El código del stock (US011) no se manda: lo resuelve el sistema a partir de
    vehiculo_interes.

    Args:
        nombre: nombre del cliente
        email: correo, solo si lo entrega
        comuna: comuna donde vive
        vehiculo_interes: vehículo que le interesa (ej. "Hyundai Tucson 2023")
        presupuesto: presupuesto máximo en pesos
        pie_disponible: pie en pesos que tiene disponible
        cuota_objetivo: cuota mensual en pesos que quiere pagar
        cuando_compra: FECHA en que el cliente dijo que quiere comprar, con sus palabras (ej. "este viernes", "este mes", "en 3 semanas"). NO es el plazo del crédito: una simulación "a 24 cuotas" no dice nada de cuando compra. Si no lo dijo, dejalo vacío.
        intencion: qué quiere hacer (ej. "compra vehículo", "servicio técnico")
        sentimiento: cómo se percibe al cliente: positivo, neutro o negativo
        urgencia: alta, media o baja, según lo apurado que se muestre
        proxima_accion: que corresponde hacer después (ej. "contactar en el día")
        resumen: resumen breve del caso para que un ejecutivo lo lea sin releer el chat
    """
    datos = {
        "nombre": nombre, "email": email, "comuna": comuna,
        "vehiculo_interes": vehiculo_interes,
        "presupuesto": presupuesto or None, "pie_disponible": pie_disponible or None,
        # cuando_compra -> columna plazo_compra: el nombre del parametro es el
        # que ve el LLM, el de la columna es el que ve el panel.
        "cuota_objetivo": cuota_objetivo or None, "plazo_compra": cuando_compra,
        "intencion": intencion, "sentimiento": sentimiento, "urgencia": urgencia,
        "proxima_accion": proxima_accion, "resumen": resumen,
    }
    return await sync_to_async(_registrar_datos_lead_impl, thread_sensitive=True)(
        runtime.state.get("wa_id", ""), datos,
    )


@tool(parse_docstring=True)
async def registrar_parte_pago(
    marca_modelo: str, runtime: ToolRuntime,
    anio: int = 0, version: str = "", km: int = 0, patente: str = "",
    estado_general: str = "", tiene_deuda: str = "", ubicacion: str = "",
    observaciones: str = "",
) -> dict:
    """Registra el vehículo que el cliente quiere dejar en parte de pago para
    solicitar una tasación preliminar.

    Úsala cuando el cliente ofrezca su auto actual. Puedes llamarla apenas
    sepas marca y modelo y volver a llamarla después con los datos que falten.
    NUNCA le des un monto de tasación al cliente, ni siquiera aproximado.

    Args:
        marca_modelo: marca y modelo del vehículo (ej. "Mazda CX-5")
        anio: anio del vehículo
        version: version, si la sabe
        km: kilometraje aproximado
        patente: patente, si la entrega o la manda en una foto
        estado_general: como describe su estado (ej. "muy bueno")
        tiene_deuda: "si" si tiene deuda o prenda vigente, "no" si dice que no, vacío si todavía no se preguntó
        ubicacion: comuna o ciudad dónde está el vehículo
        observaciones: cualquier detalle adicional relevante
    """
    return await sync_to_async(_registrar_parte_pago_impl, thread_sensitive=True)(
        runtime.state.get("wa_id", ""), marca_modelo=marca_modelo, anio=anio or None,
        version=version, km=km or None, patente=patente, estado_general=estado_general,
        tiene_deuda=_a_booleano_o_none(tiene_deuda), ubicacion=ubicacion,
        observaciones=observaciones,
    )
