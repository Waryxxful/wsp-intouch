from datetime import timedelta

# ORTOGRAFIA DE LOS BLOQUES: todo string de este modulo que se devuelve va al
# system prompt, asi que se escribe CON tildes y sin voseo. No es cosmetico: el
# modelo imita el registro del corpus que lee. La version anterior de
# bloque_fecha_actual le entregaba "manana", "miercoles" y "sabado" -- y este
# bloque es lo PRIMERO del prompt. Ver la nota larga en bot/flow/global_prompt.py
# y docs/PENDIENTES.md (hallazgos #3 y #5 de la planilla del vendedor).


def bloque_nombre_contacto(state: dict) -> str:
    """Nombre de perfil de WhatsApp del contacto -- bot/whatsapp/handlers.py ya
    lo captura en cada mensaje (Conversation.name) y lo deja en BotState.name,
    pero hasta ahora ningun build_system_prompt lo leia: se descartaba antes de
    llegar al LLM (hallazgo real, ver prompt/correccion_prompt.md punto 4). No
    es necesariamente su nombre real (puede ser un apodo, el nombre de una
    empresa, etc.), por eso se entrega como dato a confirmar con criterio, no
    como un hecho."""
    nombre = (state.get("name") or "").strip()
    if not nombre:
        return ""
    return (
        f"\n\nNombre de perfil de WhatsApp del contacto: {nombre} (puede no ser "
        "su nombre real -- usa este dato con naturalidad y confirma con el cliente "
        "si no calza, no lo asumas como un hecho)."
    )


def bloque_numero_contacto(state: dict) -> str:
    """Numero de WhatsApp real del chat (wa_id, formato internacional sin
    "+"). El sistema siempre lo conoce -- lo usa para mandar cada mensaje --
    pero antes de este bloque nunca se lo pasaba al LLM como texto, solo se
    inyectaba a tools puntuales via ToolRuntime (ver
    bot.business.compliance.registrar_no_contactar). Sin esto el bot le
    decia al cliente que no tenia su numero registrado aunque le estaba
    respondiendo a traves de el (hallazgo real en conversacion de prueba,
    ver docs/PENDIENTES.md)."""
    wa_id = (state.get("wa_id") or "").strip()
    if not wa_id:
        return ""
    return (
        f"\n\nNúmero de WhatsApp real de este chat: +{wa_id}. Si el contacto "
        "pregunta con qué número quedó registrado o por dónde lo van a "
        "contactar, puede confirmarse directamente que es el mismo con el que "
        "está escribiendo ahora. Si prefiere que el seguimiento se haga a otro "
        "número distinto, pide ese dato para dejarlo anotado."
    )


def bloque_ya_saludado(state: dict) -> str:
    """Le dice al especialista que el saludo ya salio, para que no lo repita.

    Ataca la GENERACION, no la limpieza posterior. La primera version de esto
    intento recortar el saludo de la respuesta ya generada con una lista blanca
    de palabras, y el LLM la esquivo al primer intento real con "¡Hola de nuevo
    Tomas! Encantado de ayudarte" ("de"/"nuevo" no estaban en la lista). El
    espacio de saludos que un LLM puede escribir no se puede enumerar -- es la
    misma leccion del loop de despedidas y del matcher exacto de saludos.

    Notar que el modelo YA ve la bienvenida en el historial (se guarda antes de
    correr el grafo) y aun asi saludaba: de hecho dijo "de nuevo", o sea que
    sabia. Ver el historial no alcanza; hay que pedirlo explicito."""
    if not state.get("ya_saludamos"):
        return ""
    return (
        "\n\nIMPORTANTE para este turno: al contacto ya se le envió el saludo de "
        "bienvenida y tu presentación hace un segundo, en un mensaje aparte que "
        "ya recibió. NO vuelvas a saludar, NO te presentes de nuevo y NO uses "
        "aperturas de cortesía (\"hola\", \"hola de nuevo\", \"encantado de "
        "ayudarte\", \"un gusto\"). Empieza tu respuesta directamente por lo que "
        "el contacto pidió, como continuación natural de ese saludo."
    )


# Nombres en español: el LLM tiene que poder mapear "este viernes" a una fecha
# concreta sin hacer aritmetica de calendario, que es donde se equivoca.
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def bloque_fecha_actual() -> str:
    """Le dice al LLM que dia es hoy, con los proximos 7 dias ya resueltos.

    Sin esto el modelo NO tiene forma de saber la fecha: no esta en ningun
    prompt. Bug real del 2026-09-03 (docs/PENDIENTES.md #22): un contacto pidio
    hora para "este viernes a las 16:00", el bot respondio "Quedo agendado" y
    NO llamo a agendar_hora -- no podia, porque las tools piden `fecha` en ISO
    y no tenia con que construirla. Lo que quedo guardado en el estado fue
    "2025-06-06T16:00": ano y mes inventados, probablemente de su entrenamiento.
    O sea que el cliente se presentaba un viernes que no existia en el sistema.

    Se entrega la TABLA de los proximos dias ya calculada en vez de solo "hoy
    es X", para que el modelo no tenga que hacer aritmetica de calendario --
    ahi es donde se equivoca, y ademas asi "el viernes" y "pasado manana"
    salen de una lectura, no de un calculo.

    Hora incluida porque el contacto puede decir "hoy despues de las 23:00" (lo
    dijo uno real) y el bot necesita saber si eso ya paso.
    """
    from django.utils import timezone

    ahora = timezone.localtime()
    # La hora se redondea a 5 minutos HACIA ABAJO, y no es cosmetico: este
    # bloque es lo PRIMERO del system prompt, o sea el prefijo de cache del
    # proveedor. Con la hora al minuto exacto, cada minuto de reloj invalidaba
    # el prefijo completo -- las ~2.500 tokens de instrucciones y las 11
    # definiciones de tools incluidas.
    #
    # Firma medida en Langfuse (281 llamadas, auditoria de latencia
    # 2026-09-03): la PRIMERA llamada de cada minuto de reloj tenia 41,4% de
    # cache hit y 39% con cache en cero; las SIGUIENTES del mismo minuto,
    # 76,4% y 12%. El borde caia exactamente en el minuto.
    #
    # Con 5 minutos la invalidacion baja 5 veces. Lo que esto prueba son
    # TOKENS, no segundos: el A/B de latencia quedo dentro del ruido (TTFT
    # 1,49s contra 1,36s, n=4) porque el proveedor no honro la cache de forma
    # consistente en el banco de prueba. Se hace igual porque es gratis y
    # ademas los tokens cacheados cuestan menos, pero no hay que esperar una
    # mejora de latencia visible por esto solo.
    #
    # 5 minutos es seguro para el unico uso que la hora tiene: saber si "hoy
    # despues de las 23:00" ya paso. La granularidad fina nunca importo, y el
    # bloque sigue estando (sin el vuelve el bug de "quedo agendado" sin
    # agendar, docs/PENDIENTES.md #22).
    minuto = (ahora.minute // 5) * 5
    lineas = []
    for offset in range(8):
        dia = ahora + timedelta(days=offset)
        etiqueta = {0: " (HOY)", 1: " (MAÑANA)", 2: " (pasado mañana)"}.get(offset, "")
        lineas.append(f"  - {_DIAS[dia.weekday()]} {dia.date().isoformat()}{etiqueta}")
    # "Hoy es X" y "Mañana es Y" van tambien como frases sueltas, ademas de la
    # tabla. La tabla sola no alcanzaba: el 2026-09-03 a las 11:20 el contacto
    # dijo "quiero agendar manana", el modelo llamo a consultar_disponibilidad
    # con fecha="2026-09-04" (CORRECTA, la leyo de la tabla) y sin embargo
    # escribio "Para manana jueves..." -- hoy ERA jueves. O sea que tomo la
    # fecha de la fila correcta y el nombre del dia de la fila de arriba.
    # La reserva habria quedado bien; el que se confunde es el cliente.
    # Mismo criterio que el resto del bloque: lo que el modelo tiene que juntar
    # de dos lugares, se lo damos ya junto.
    manana = ahora + timedelta(days=1)
    pasado = ahora + timedelta(days=2)
    return (
        f"\n\n## FECHA\nAhora mismo es {_DIAS[ahora.weekday()]} "
        f"{ahora.date().isoformat()}, {ahora.strftime('%H')}:{minuto:02d} hora de Chile.\n"
        f"Mañana es {_DIAS[manana.weekday()]} {manana.date().isoformat()}.\n"
        f"Pasado mañana es {_DIAS[pasado.weekday()]} {pasado.date().isoformat()}.\n"
        "Próximos días, ya resueltos (usa estas fechas tal cual, no las calcules):\n"
        + "\n".join(lineas)
        + "\nCuando el contacto diga \"este viernes\", \"mañana\" o similar, toma de "
        "esta tabla TANTO la fecha COMO el nombre del día -- nunca escribas el "
        "nombre del día de memoria. Si lo que pide es ambiguo o cae fuera de estos "
        "días, pregúntale la fecha exacta -- nunca la inventes."
    )


# Texto ya calculado del bloque de sucursal. Se refresca desde
# build_agent_registry(), que corre bajo sync_to_async una vez por turno --
# NO se calcula dentro de bloque_sucursal_unica() porque build_system_prompt
# se invoca desde un nodo async del grafo y una query ahi levanta
# SynchronousOnlyOperation (lo reventó en la primera prueba). Mismo motivo por
# el que los demas bloques de este modulo leen de `state` y no de la BD.
_BLOQUE_SUCURSAL = ""


def refrescar_sucursal_unica() -> None:
    """Recalcula el bloque de sucursal. Llamar desde contexto SINCRONO."""
    global _BLOQUE_SUCURSAL
    from bot.models import Sucursal

    sucursales = list(Sucursal.objects.all()[:2])
    if len(sucursales) != 1:
        _BLOQUE_SUCURSAL = ""
        return
    s = sucursales[0]
    horario = f" Horario: {s.horario_texto}." if s.horario_texto else ""
    # A proposito NO se pone aca la direccion ni el horario, solo el nombre.
    #
    # La primera version SI los ponia, y eso produjo una interaccion entre dos
    # arreglos mios: con la direccion ya en el prompt el modelo respondia sin
    # llamar a `buscar_sucursales_cercanas`, y el pin de ubicacion de WhatsApp
    # se resuelve justamente de los RESULTADOS de esa tool
    # (_extraer_sucursal_ids_de_tools). Medido: la direccion salia bien 6/6
    # pero el pin solo 4/6, y en las 2 fallas no se llamo ninguna tool.
    #
    # Dejando afuera los datos, el modelo sigue sin preguntar la comuna (que es
    # lo que este bloque vino a resolver) pero necesita la tool para dar la
    # direccion, asi que el pin vuelve a salir siempre.
    _BLOQUE_SUCURSAL = (
        f"\n\n## SUCURSAL\nHay UNA sola sucursal: {s.nombre}.\n"
        "No le preguntes al contacto en qué comuna está ni cuál le queda más "
        "cerca: no hay entre qué elegir. Si pregunta dónde están o quiere venir, "
        "llama a \"buscar_sucursales_cercanas\" y dale su dirección y horario."
    )


def bloque_sucursal_unica() -> str:
    """Si el cliente tiene UNA sola sucursal, se la damos ya resuelta.

    Cavem tiene una. Aun asi el bot le preguntaba al contacto "¿en que comuna
    estas?" para "recomendarle la mas cercana", haciendole perder un turno
    entero por una eleccion que no existe (caso real, conversacion de Quintin,
    docs/PENDIENTES.md #25c).

    Se agrego una regla al prompt global y **no alcanzo**: medido contra el LLM
    real, seguia preguntando 1 de cada 2 veces. La regla decia que hacer pero el
    modelo igual tenia que acordarse de consultarla; con la sucursal ya en el
    contexto no hay nada que preguntar ni que recordar. Mismo patron que
    bloque_fecha_actual, que resolvio el mismo tipo de problema.

    Con 2+ sucursales no se inyecta nada: ahi preguntar la comuna SI aporta, y
    el ranking por cercania de buscar_sucursales_cercanas hace su trabajo.
    """
    return _BLOQUE_SUCURSAL


# Los antecedentes que el vendedor necesita, en el orden en que conviene
# pedirlos: primero lo que mas pesa en el lead score (bot/models.py::
# _PESOS_LEAD_SCORE) y despues los datos de contacto.
_ANTECEDENTES_DEL_LEAD = (
    ("vehiculo_interes", "qué vehículo le interesa"),
    ("plazo_compra", "para cuándo quiere comprar"),
    ("presupuesto", "cuánto tiene pensado gastar"),
    ("pie_disponible", "de cuánto sería el pie"),
    ("nombre", "su nombre"),
    ("comuna", "en qué comuna vive"),
)


def antecedentes_que_faltan(conversation) -> list:
    """Los antecedentes del lead que todavia no estan capturados, en texto.

    Corre en contexto SINCRONO (se llama con sync_to_async al armar el state),
    por el mismo motivo que refrescar_sucursal_unica."""
    from bot.models import LeadComercial

    lead = LeadComercial.objects.filter(conversation=conversation).first()
    if lead is None:
        # Contacto sin lead todavia: no se le dicta al modelo una lista de seis
        # cosas por pedir en el primer turno. La conversacion arranca sola.
        return []
    return [texto for campo, texto in _ANTECEDENTES_DEL_LEAD
            if getattr(lead, campo, None) in (None, "", 0)]


def bloque_datos_del_lead(state: dict) -> str:
    """Que antecedentes comerciales ya estan capturados y cuales faltan.

    POR QUE EXISTE (docs/PENDIENTES.md 32.a): hasta el 2026-09-07 esto lo
    devolvia la tool `registrar_datos_lead` en su resultado (`datos_que_faltan`)
    y el modelo lo usaba para decidir que preguntar. Sacar esa tool del camino
    critico -- que es lo que ahorra una llamada entera al LLM en el 17,3% de los
    turnos -- se llevaba tambien ese feedback, asi que se reemplaza por un
    bloque deterministico: el dato sale de la BD, que es donde ya estaba.

    Inyectarlo ACA es correcto justamente porque ya no hay tool que llamar. La
    regla de no darle al LLM un dato que una tool ya provee (docs/CONFIG.md)
    existe para no quitarle la razon de llamarla; aca la razon se quito a
    proposito y con medicion.

    Sale de LeadComercial y no de flow_data: flow_data es lo que el modelo dijo,
    LeadComercial es lo que quedo escrito despues de las validaciones. Pero se
    lee del STATE y no de la BD aca: este bloque lo llama un nodo async y una
    query levanta SynchronousOnlyOperation -- ya paso con
    bloque_sucursal_unica. Lo calcula antecedentes_que_faltan(), que corre en
    contexto sincrono al armar el state (bot/whatsapp/handlers.py)."""
    faltan = state.get("lead_faltante")
    if not faltan:
        return ""
    return (
        "\n\nAntecedentes que todavía no tenemos de este cliente: "
        + ", ".join(faltan) +
        ". No los pidas todos de una ni armes un cuestionario: pregunta lo que "
        "corresponda cuando venga al caso en la conversación."
    )
