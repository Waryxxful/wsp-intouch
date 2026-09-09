import logging
import re
import unicodedata

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from bot.flow.context_window import (
    build_context_window, limite_sesion_actual, marcador_imagen_enviada, resolve_campaign_hint,
)
from bot.flow.graph import RESPUESTA_GENERICA_JSON_INVALIDO, get_flow_graph
from bot.models import Conversation, Message, get_setting
from bot.scraping.normalizar import _normalizar_clave
from bot.whatsapp.client import get_wa_client
from bot.whatsapp.cola_envio import encolar as encolar_envio, encolar_acuse
from langfuse import get_client, observe, propagate_attributes
from langgraph.errors import GraphRecursionError

logger = logging.getLogger(__name__)

# Default real de LangGraph (25) declarado explicito -- antes dependia de un
# valor implicito no documentado en este repo; ver docs/PENDIENTES.md. Nada
# atrapaba GraphRecursionError, asi que un loop patologico entre
# specialist/business_action (ver bot/flow/graph.py::build_graph) dejaba al
# contacto sin ninguna respuesta en vez de un fallback controlado.
_GRAPH_RECURSION_LIMIT = 25

# Saludos que se responden SIN pasar por el LLM. Antes era un set de 6 strings
# comparados por igualdad exacta, y eso cubria 9 de 22 saludos reales medidos
# (41%): "hola buenas" -- el saludo real del primer test por WhatsApp --,
# "holaa", "buen dia", "hola buenas tardes", "que tal", "hey" y "hola 👋" todos
# caian al LLM y costaban 3-11s (docs/PENDIENTES.md #15).
#
# Ahora se compara por TOKENS: un mensaje se considera saludo mientras TODAS
# sus palabras esten aca. Esa regla es la que impide tragarse un mensaje con
# contenido real -- "hola busco un suv" tiene "busco"/"suv" fuera del set, asi
# que su parte de contenido sigue al grafo. Mismo criterio que
# _es_mensaje_trivial ("ok pero tengo otra pregunta" no matchea).
_SALUDO_TOKENS = {
    "hola", "holi", "holis", "ola", "alo", "hey", "buenas", "buenos", "buen",
    "dia", "dias", "tarde", "tardes", "noche", "noches", "saludos",
    "que", "tal", "como", "estas", "esta", "andas", "va",
}

# Palabras que NO cuentan para decidir si algo es saludo: relleno de cortesia
# que suele venir pegado ("hola, muy buenas tardes"). Se ignoran en vez de
# sumarse a _SALUDO_TOKENS para no ensanchar el matcher con palabras que solas
# no son un saludo ("muy" o "todo" solos no deben interceptar nada).
_SALUDO_RELLENO = {"muy", "gracias", "por", "favor", "disculpa", "perdon"}


def _normalizar_palabra(palabra: str) -> str:
    """Minuscula, sin tildes, sin puntuacion, y colapsando letras repetidas
    ("Holaaa!" -> "hola"). El colapso es seguro para estas palabras: ninguna
    de _SALUDO_TOKENS tiene letras dobles legitimas."""
    base = unicodedata.normalize("NFD", palabra.lower())
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")
    base = "".join(c for c in base if c.isalnum())
    return re.sub(r"(.)\1+", r"\1", base)


def _partir_saludo(texto: str) -> tuple[bool, str]:
    """Devuelve (empieza_con_saludo, resto_del_mensaje).

    El resto es lo que el contacto dijo ADEMAS de saludar: para "hola quiero
    cotizar una suv" devuelve (True, "quiero cotizar una suv"), y para "hola
    buenas" devuelve (True, ""). Con resto vacio el saludo es toda la
    respuesta; con resto, el saludo sale al instante y el grafo se encarga del
    contenido en el mismo request (ver handle_message)."""
    palabras = (texto or "").split()
    if not palabras:
        return False, ""
    consumidas = 0
    for palabra in palabras:
        normalizada = _normalizar_palabra(palabra)
        if not normalizada:  # solo puntuacion o emoji: no corta el saludo
            consumidas += 1
            continue
        if normalizada in _SALUDO_TOKENS or normalizada in _SALUDO_RELLENO:
            consumidas += 1
            continue
        break
    # Al menos una palabra tiene que ser un saludo de verdad, no solo relleno:
    # "muy gracias" no es un saludo.
    hay_saludo = any(
        _normalizar_palabra(p) in _SALUDO_TOKENS for p in palabras[:consumidas]
    )
    if not hay_saludo:
        # Sin saludo NO se consume nada: el mensaje entero es el resto. Si se
        # devolviera lo que quedo despues del relleno, un "gracias" perderia
        # su texto para cualquier futuro llamador que use el resto (hoy solo
        # se lee cuando hay_saludo es True, pero el contrato tiene que valer).
        return False, (texto or "").strip()
    return True, " ".join(palabras[consumidas:]).strip()


# Default del mensaje de bienvenida. Editable sin deploy desde el Setting
# "welcome_message" (panel). "{nombre}" se reemplaza por el nombre del perfil
# de WhatsApp del contacto, o por nada si no lo tiene -- se usa .replace() y no
# .format() a proposito: el texto lo puede editar un humano desde el panel y
# unas llaves sueltas no deben reventar la respuesta.
#
# El texto anterior era "¡Hola! ¿En qué le puedo ayudar?", que trataba de USTED
# y violaba la regla de tuteo del prompt global, ademas de no identificarse
# como Auto IA. Aprobado por el usuario el 2026-09-02.
# La bienvenida son DOS piezas, y se manda una o las dos segun el caso:
#
#   "hola"                       -> identidad + invitacion (es toda la respuesta)
#   "hola quiero cotizar una suv" -> SOLO identidad, y el grafo responde el resto
#
# La invitacion ("¿en que te puedo ayudar?") es redundante cuando ya viene una
# respuesta real detras: le pregunta al contacto algo que acaba de decir.
WELCOME_IDENTIDAD = "¡Hola{nombre}! Soy Auto IA, el asistente virtual de Cavem 👋"
WELCOME_INVITACION = (
    "¿En qué te puedo ayudar? Puedo mostrarte nuestro stock de usados, "
    "simular un financiamiento o agendar una hora en el taller."
)


def _texto_bienvenida(plantilla: str, nombre: str) -> str:
    """Interpola el nombre de pila del contacto, si lo hay."""
    pila = (nombre or "").strip().split()
    saludo = f" {pila[0].title()}" if pila else ""
    return plantilla.replace("{nombre}", saludo)


# Puntuacion que cierra la clausula de apertura de una respuesta.
_FIN_CLAUSULA = re.compile(r"[!?.\n]")

# Palabras de cortesia que aparecen DENTRO de una apertura, mas alla del saludo
# en si. Se agregan porque el primer intento real fallo justo aca: el LLM
# escribio "¡Hola de nuevo Tomas! Encantado de ayudarte" y "de"/"nuevo" no
# estaban, asi que la clausula no quedaba 100% en la lista blanca y no se
# cortaba. Este corte es la RED DE SEGURIDAD -- lo que de verdad evita el
# saludo repetido es la instruccion al especialista (bloque_ya_saludado en
# bot/flow/agents/_common.py), porque enumerar todo lo que un LLM puede
# escribir no funciona.
_CORTESIA_APERTURA = {
    "de", "nuevo", "otra", "vez", "encantado", "encantada", "gusto", "mucho",
    "bienvenido", "bienvenida", "un", "una", "el", "la", "y", "aqui", "aca",
    "estoy", "para", "ayudarte", "servirte", "soy", "auto", "ia", "asistente",
    "virtual", "cavem", "todo", "bien", "espero", "te", "en",
}


def _quitar_saludo_inicial(texto: str, nombre: str = "") -> str:
    """Saca el saludo de apertura de la respuesta del LLM, si trae uno.

    Se usa SOLO en el turno donde ya mandamos la bienvenida nosotros. Sin
    esto el contacto recibe dos saludos seguidos: la bienvenida instantanea
    y despues "¡Buenas Tomas! Perfecto, una camioneta..." del especialista
    (reportado por el usuario el 2026-09-02 con captura). Guardar la
    bienvenida en el historial NO alcanza -- se probo y el LLM saluda igual.

    Se corta en codigo y no con una regla de prompt por la misma razon que el
    loop de despedidas (ver _ACUSES_TRIVIALES_CONOCIDOS): ese mecanismo ya
    fallo dos rondas de prompt, y depender de que el modelo "se acuerde" es
    justo lo que no funciona.

    Solo saca la clausula inicial si TODAS sus palabras son de saludo, relleno
    de cortesia, o el nombre del contacto -- asi "Perfecto, una camioneta..."
    no se toca (ahi "perfecto" es un acuse, no un saludo). Y nunca devuelve
    vacio: si la respuesta entera era un saludo, se deja tal cual."""
    permitidas = set(_SALUDO_TOKENS) | set(_SALUDO_RELLENO) | _CORTESIA_APERTURA
    for parte in (nombre or "").split():
        normalizada = _normalizar_palabra(parte)
        if normalizada:
            permitidas.add(normalizada)

    restante = (texto or "").lstrip()
    for _ in range(2):  # "¡Hola! ¡Buenas Tomas! ..." -- raro, pero barato de cubrir
        corte = _FIN_CLAUSULA.search(restante)
        if corte is None:
            break
        clausula, resto = restante[:corte.end()], restante[corte.end():].lstrip()
        if not resto:
            break  # era todo saludo: mejor dejarlo que mandar vacio
        palabras = [_normalizar_palabra(p) for p in clausula.split()]
        palabras = [p for p in palabras if p]
        if not palabras or not all(p in permitidas for p in palabras):
            break
        if not any(p in _SALUDO_TOKENS for p in palabras):
            break  # relleno o el nombre solo, sin saludo: no es una apertura
        restante = resto
    return restante or (texto or "")

# Loop de despedidas: confirmado 2026-09-01 (docs/PENDIENTES.md, seccion
# "Revisión manual 2026-08-25 -- prompt v4.4/v1.3") que es comportamiento
# narrativo del LLM -- cada acuse trivial del cliente se trata como pie
# para una despedida nueva, sobrevivio 2 rondas de prompt. En vez de
# reintentar con una redaccion mas fuerte (mismo mecanismo que ya fallo 2
# veces), esto corta ANTES del grafo -- no depende de que el LLM "se
# acuerde" de no repetirse.
# Palabras que por si solas no aportan informacion nueva: son acuse de recibo.
# Antes esto se comparaba por igualdad EXACTA contra el mensaje entero, asi que
# "Ok gracias" y "muchas gracias" no matcheaban y se iban al LLM -- por eso el
# bot le mando a un contacto real DOS despedidas elaboradas seguidas
# (conversacion del vendedor, 2026-09-02, docs/PENDIENTES.md #23).
_ACUSES_TRIVIALES_CONOCIDOS = {
    "ok", "okay", "oka", "listo", "gracias", "dale", "bien", "genial", "perfecto",
    "vale", "excelente", "buenisimo", "bacan", "joya", "estupendo",
}

# Relleno que suele venir pegado a un acuse ("muchas gracias", "todo bien"). No
# cuenta por si solo: hace falta al menos un acuse de verdad.
#
# NO incluye "si": responder "si" a "¿te sirve esa hora?" es una respuesta con
# contenido, no un acuse, y tiene que llegar al especialista.
_RELLENO_ACUSE = {"muchas", "muchisimas", "mil", "todo", "super", "muy", "ya"}


def _es_mensaje_trivial(texto: str) -> bool:
    """True si el mensaje es vacio, solo emoji/puntuacion, o puro acuse.

    Se compara por TOKENS y no por igualdad exacta: "Ok gracias" y "muchas
    gracias" son acuses igual que "ok". La regla de que TODAS las palabras
    tienen que ser de acuse o relleno es la que impide tragarse un mensaje con
    contenido real -- "ok me interesa" y "ok pero tengo otra pregunta" tienen
    palabras fuera de los sets, asi que siguen al especialista."""
    normalizado = (texto or "").strip()
    if not normalizado:
        return True
    if not any(c.isalnum() for c in normalizado):
        return True
    palabras = [_normalizar_palabra(p) for p in normalizado.split()]
    palabras = [p for p in palabras if p]
    if not palabras:
        return True
    if not all(p in _ACUSES_TRIVIALES_CONOCIDOS or p in _RELLENO_ACUSE for p in palabras):
        return False
    # Solo relleno ("muchas", "ya") no es un acuse: hace falta uno de verdad.
    return any(p in _ACUSES_TRIVIALES_CONOCIDOS for p in palabras)


async def _bot_cerro_la_conversacion(conv: Conversation) -> bool:
    """True si el ultimo mensaje del bot NO invitaba a seguir.

    Se usa el signo de pregunta como senal en vez de enumerar despedidas
    ("nos vemos", "hasta luego", "que estes bien", ...): esa lista nunca esta
    completa -- misma leccion del matcher de saludos y del recorte del saludo
    repetido. Mientras el bot pregunta algo, la conversacion sigue abierta y un
    "perfecto" del contacto puede significar "si, dale" y tiene que llegar al
    especialista. Cuando deja de preguntar, cerro.

    Caso real que esto arregla (docs/PENDIENTES.md #23): el bot cerro con
    "Quedo todo coordinado... ¡Nos vemos el viernes!" (sin pregunta), el
    contacto dijo "Gracias" y el bot le mando OTRA despedida casi identica."""
    ultimo = await conv.messages.filter(role="assistant").order_by("-created_at").afirst()
    if ultimo is None:
        return False
    return "?" not in ultimo.content and "¿" not in ultimo.content


async def _acuses_triviales_consecutivos(conv: Conversation) -> int:
    """Cuenta cuantos mensajes de usuario CONSECUTIVOS, yendo hacia atras
    desde el mas reciente ya guardado y dentro de la sesion activa (mismo
    borde que usa build_context_window/_imagen_enviada_recientemente), son
    triviales. Se llama DESPUES de guardar el mensaje entrante (ver
    handle_message), asi que ese mensaje ya esta incluido en la cuenta."""
    limite = await sync_to_async(limite_sesion_actual)(conv)
    if limite is None:
        return 0
    count = 0
    async for msg in conv.messages.filter(created_at__gte=limite, role="user").order_by("-created_at"):
        if not _es_mensaje_trivial(msg.content):
            break
        count += 1
    return count


# Caracteres invisibles que el LLM mete en su salida sin querer. No se ven en
# WhatsApp, pero ensucian el texto: aparecieron 13 en una sola conversacion
# real (2026-09-02), incluso DENTRO de precios ("\u200b15.990.000"), y eso
# rompe el texto si el ejecutivo lo copia al CRM o si alguien lo busca.
#
#   U+200B zero width space      U+200C zero width non-joiner
#   U+200D zero width joiner     U+FEFF byte order mark
#   U+00AD soft hyphen           U+2060 word joiner
#
# NO se incluye U+00A0 (espacio duro): ese SI es un espacio visible y sacarlo
# pegaria palabras.
_INVISIBLES = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")

# Saltos de linea que llegaron ESCAPADOS DOS VECES. El contrato del
# especialista es un JSON, y el LLM a veces escribe "\\n" (barra invertida
# escapada) en vez de "\n": json.loads hace lo correcto y devuelve una barra
# invertida y una "n" LITERALES, asi que al contacto le llega "...asi 💰\\n-
# Pie: $9.945.000" con el "\\n" visible. Pasó en la conversacion del vendedor
# (2026-09-02, docs/PENDIENTES.md #21).
#
# Ademas rompe el partido en varios mensajes de WhatsApp: _dividir_en_mensajes
# corta por linea en blanco REAL, y un "\\n" literal no es una. Por eso ese
# turno llego como un unico mensaje largo mientras el resto llegaron en 3-4.
#
# Se convierten a saltos reales en vez de borrarlos: el texto que el modelo
# quiso escribir es correcto, lo unico mal es el escapado.
_SALTOS_ESCAPADOS = re.compile(r"\\r\\n|\\n|\\r")


def _a_formato_whatsapp(texto: str) -> str:
    """Deja el texto del LLM listo para WhatsApp.

    1. Saltos de linea escapados dos veces ("\\n" literal) -> saltos reales
       (ver _SALTOS_ESCAPADOS). Va PRIMERO porque de eso depende que
       _dividir_en_mensajes pueda partir la respuesta en varios mensajes.
    2. Negrita: convierte la de Markdown (**texto**) al formato real de
       WhatsApp (*texto*, un solo asterisco) -- doble asterisco no se renderiza
       y le llega al contacto como texto literal con asteriscos.
    3. Caracteres invisibles: los saca (ver _INVISIBLES).

    Las dos cosas se hacen en codigo y no por instruccion de prompt porque el
    LLM no las respeta de forma confiable en cada turno -- lo de la negrita ya
    estaba comprobado, y lo de los invisibles se detecto contando caracteres en
    una conversacion real."""
    texto = _SALTOS_ESCAPADOS.sub("\n", texto)
    texto = _INVISIBLES.sub("", texto)
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", texto)


# Largo (en caracteres) a partir del cual un mensaje se considera "muy
# largo" para WhatsApp -- criterio subjetivo (no es un limite tecnico de la
# API) elegido para que las respuestas se vean como una conversacion real,
# no un bloque de texto. Ver _dividir_en_mensajes.
_LARGO_MAXIMO_MENSAJE = 600


def _dividir_en_mensajes(texto: str) -> list[str]:
    """Divide `texto` en varios mensajes de WhatsApp en vez de mandar todo
    como un solo bloque -- feedback real del usuario (wsp_demo,
    renault.cl): el bot mandaba respuestas de 3-4 parrafos como un unico
    mensaje, muy largo comparado a como escribe una persona real por
    WhatsApp. Primero divide por parrafo (linea en blanco, "\\n\\n") -- el
    LLM ya separa sus ideas asi (ver seccion "LARGO DE LOS MENSAJES" del
    prompt global). Si un parrafo por si solo sigue siendo mas largo que
    _LARGO_MAXIMO_MENSAJE, lo subdivide por oraciones completas (nunca a
    mitad de una), agrupando oraciones consecutivas hasta llenar el
    limite. Si una sola oracion ya excede el limite, se manda entera
    igual -- nunca se corta una oracion a la mitad."""
    parrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    mensajes = []
    for parrafo in parrafos:
        if len(parrafo) <= _LARGO_MAXIMO_MENSAJE:
            mensajes.append(parrafo)
            continue
        oraciones = re.split(r"(?<=[.!?])\s+", parrafo)
        actual = ""
        for oracion in oraciones:
            candidato = f"{actual} {oracion}".strip() if actual else oracion
            if actual and len(candidato) > _LARGO_MAXIMO_MENSAJE:
                mensajes.append(actual)
                actual = oracion
            else:
                actual = candidato
        if actual:
            mensajes.append(actual)
    return mensajes or [texto]


async def _get_or_create_conversation(wa_id: str, name: str) -> tuple[Conversation, bool]:
    conv, created = await Conversation.objects.aget_or_create(wa_id=wa_id, defaults={"name": name})
    if name and conv.name != name:
        conv.name = name
        await conv.asave()
    return conv, created


def _crear_mensaje(conv: Conversation, role: str, content: str, wa_msg_id: str, media_url: str) -> Message:
    # transaction.atomic() en vez de Message.objects.acreate() directo: sin
    # esto, un IntegrityError del UniqueConstraint de wa_msg_id (ver
    # bot/models.py, carrera con otra ejecucion concurrente para el mismo
    # msg_id) deja la transaccion envolvente inutilizable para cualquier
    # query posterior -- este bloque atomico aisla el fallo a un savepoint
    # propio, mismo patron recomendado por Django para capturar
    # IntegrityError de un .create().
    with transaction.atomic():
        return Message.objects.create(
            conversation=conv, role=role, content=content, wa_msg_id=wa_msg_id, media_url=media_url,
        )


async def _save_message(conv: Conversation, role: str, content: str, wa_msg_id: str = "", media_url: str = ""):
    await sync_to_async(_crear_mensaje, thread_sensitive=True)(conv, role, content, wa_msg_id, media_url)


_MENSAJES_FALLBACK_MEDIA = {
    "video": "Por ahora no puedo ver videos. ¿Me puedes escribir tu consulta como texto?",
    "document": "Por ahora no puedo leer documentos. ¿Me puedes escribir tu consulta como texto?",
    "sticker": "Por ahora no puedo procesar stickers. ¿Me puedes escribir tu consulta como texto?",
    "location": "Por ahora no puedo procesar ubicaciones. ¿Me puedes escribir tu consulta como texto?",
    "image": "No pude procesar tu imagen. ¿Me puedes escribir tu consulta como texto?",
    "audio": "No pude procesar tu audio. ¿Me puedes escribir tu consulta como texto?",
}
_MENSAJE_FALLBACK_DEFAULT = "Por ahora no puedo procesar ese tipo de archivo. ¿Me puedes escribir tu consulta como texto?"


async def handle_unsupported_media(wa_id: str, name: str, msg_id: str, tipo: str):
    if msg_id and await Message.objects.filter(wa_msg_id=msg_id).aexists():
        logger.info("[wa] msg %s ya procesado, ignorando reintento de Meta", msg_id)
        return

    conv, _ = await _get_or_create_conversation(wa_id, name)
    try:
        await _save_message(conv, "user", f"[archivo recibido: {tipo}, no procesado]", msg_id)
    except IntegrityError:
        # Ventana de carrera con el chequeo .aexists() de arriba: otra
        # ejecucion concurrente para el mismo msg_id ya guardo su Message
        # primero -- el UniqueConstraint de Message (ver bot/models.py) lo
        # frena aca a nivel BD, mismo patron que Reserva en agendamiento.py.
        logger.info("[wa] msg %s ya procesado (carrera detectada por constraint), ignorando reintento de Meta", msg_id)
        return

    wa = get_wa_client()
    # inline: este camino ya bloquea en su propio send_text de abajo, asi que
    # no gana nada con el thread y de paso queda deterministico.
    encolar_acuse(wa, msg_id, inline=True)

    texto = _MENSAJES_FALLBACK_MEDIA.get(tipo, _MENSAJE_FALLBACK_DEFAULT)
    await sync_to_async(wa.send_text)(wa_id, texto, reply_to=msg_id)
    await _save_message(conv, "assistant", texto)


async def _imagen_enviada_recientemente(conv: Conversation, slug_modelo: str) -> bool:
    """Busca el marcador de la imagen dentro de la sesion activa actual
    (mismo borde que usa build_context_window para el historial que ve el
    LLM), no en una ventana fija de mensajes -- una ventana fija (usada
    antes: los ultimos 20 mensajes) se puede agotar con acuses de recibo
    triviales (emoji, "ok") y dejar reenviar la misma imagen dentro de la
    MISMA sesion activa, sin que haya pasado a una nueva (hallazgo real,
    ver docs/PENDIENTES.md)."""
    return await sync_to_async(_imagen_enviada_recientemente_sync)(conv, slug_modelo)


def _imagen_enviada_recientemente_sync(conv: Conversation, slug_modelo: str) -> bool:
    """La version sincrona es la real; la async de arriba la envuelve.

    Hace falta sincrona porque la cola de envio (bot/whatsapp/cola_envio.py)
    corre en un thread propio, sin event loop: ahi sync_to_async no aplica y
    llamar al ORM directo es lo correcto."""
    marcador = marcador_imagen_enviada(slug_modelo)
    limite = limite_sesion_actual(conv)
    if limite is None:
        return False
    return conv.messages.filter(created_at__gte=limite, content=marcador).exists()


@observe(name="whatsapp-turn", capture_input=False, capture_output=False)
async def _run_graph(conv: Conversation, text: str, msg_id: str, on_graph_result=None,
                     ya_saludamos: bool = False, envio_inline: bool = False):
    campaign_hint = await sync_to_async(resolve_campaign_hint)(conv.wa_id)
    messages = await sync_to_async(build_context_window)(conv)

    # session_id/user_id = wa_id: agrupa en Langfuse todos los turnos de un
    # mismo contacto bajo la misma sesion, y permite filtrar/buscar trazas
    # por numero de WhatsApp. Migracion langfuse v3->v4: update_current_trace
    # se separo en propagate_attributes (atributos de correlacion: session_id/
    # user_id/tags) y update_current_span (input/output de la observacion
    # raiz de este @observe) -- ver docs/observability/sdk/upgrade-path.
    get_client().update_current_span(input={"wa_id": conv.wa_id, "text": text})
    with propagate_attributes(
        session_id=conv.wa_id, user_id=conv.wa_id,
        tags=[campaign_hint] if campaign_hint else None,
    ):
        graph = get_flow_graph()
        # Que antecedentes comerciales faltan. Se resuelve ACA, sincrono: el
        # bloque de prompt que lo muestra corre dentro de un nodo async y una
        # query ahi levanta SynchronousOnlyOperation (ver
        # bot/flow/agents/_common.py::refrescar_sucursal_unica).
        from bot.flow.agents._common import antecedentes_que_faltan
        lead_faltante = await sync_to_async(antecedentes_que_faltan, thread_sensitive=True)(conv)
        try:
            result = await graph.ainvoke({
                "wa_id": conv.wa_id, "text": text, "name": conv.name,
                "messages": messages, "flow_state": conv.flow_state, "flow_data": conv.get_flow(),
                "active_agent": conv.active_agent or None, "campaign_hint": campaign_hint,
                # Si ya mandamos la bienvenida en este turno, el especialista
                # tiene que continuar sin volver a saludar (bloque_ya_saludado).
                "ya_saludamos": ya_saludamos,
                "lead_class": conv.lead_class or None, "stage": conv.stage or None,
                "lead_faltante": lead_faltante,
                "response_text": "", "interactive_buttons": None, "interactive_list": None,
                "interactive_options": None, "list_button_text": None,
                "tool_messages": [], "modelo_imagen": None, "sucursal_direccion_ids": None,
                "reply_to": msg_id, "_dispatch": None,
            }, config={"recursion_limit": _GRAPH_RECURSION_LIMIT})
        except GraphRecursionError:
            # Loop patologico entre specialist/business_action: sin este
            # catch, la excepcion suben sin atrapar hasta el webhook (500,
            # sin ningun mensaje al contacto). Se responde con el mismo
            # fallback generico que ya existe para JSON invalido y se deja
            # un incidente para revision humana -- no se toca flow_state/
            # active_agent, ya que `result` nunca llego a existir.
            logger.exception("[wa] GraphRecursionError para wa_id=%s -- loop patologico del grafo", conv.wa_id)
            from bot.models import registrar_incidente
            await sync_to_async(registrar_incidente, thread_sensitive=True)(
                conv, "revision_requerida",
                context={"motivo": "GraphRecursionError: el grafo no llego a un estado final"},
            )
            texto = _a_formato_whatsapp(RESPUESTA_GENERICA_JSON_INVALIDO)
            await sync_to_async(get_wa_client().send_text)(conv.wa_id, texto, reply_to=msg_id)
            await _save_message(conv, "assistant", texto)
            return
        except Exception:
            # CUALQUIER otra falla del grafo (un 4xx permanente del proveedor,
            # la BD caida, un bug nuevo) tenia el mismo final que el
            # GraphRecursionError antes de que se lo cubriera: la excepcion
            # subia hasta el webhook, Django devolvia 500 y el contacto se
            # quedaba SIN NINGUNA respuesta -- ni un mensaje de error, ni un
            # incidente para que un humano lo viera.
            #
            # Paso en vivo el 2026-09-02 (docs/PENDIENTES.md #16): OpenRouter
            # bloqueo el prompt del supervisor con 403 "prompt injection
            # patterns detected" y el bot dejo de responder por completo; el
            # contacto escribio tres veces mas al vacio. La causa de ESE 403
            # ya esta arreglada, pero el modo de falla (silencio total ante un
            # error permanente) no era especifico de ella y podia repetirse
            # con cualquier otra.
            #
            # Se responde el mismo fallback generico y se registra el
            # incidente, para que el operador lo vea en el panel en vez de
            # enterarse por el cliente.
            logger.exception("[wa] fallo no manejado del grafo para wa_id=%s", conv.wa_id)
            from bot.models import registrar_incidente
            await sync_to_async(registrar_incidente, thread_sensitive=True)(
                conv, "revision_requerida",
                context={"motivo": "el grafo fallo con un error no manejado; ver los logs del contenedor"},
            )
            texto = _a_formato_whatsapp(RESPUESTA_GENERICA_JSON_INVALIDO)
            await sync_to_async(get_wa_client().send_text)(conv.wa_id, texto, reply_to=msg_id)
            await _save_message(conv, "assistant", texto)
            return
    if on_graph_result:
        on_graph_result(result)
    get_client().update_current_span(
        output={"response_text": result.get("response_text"), "active_agent": result.get("active_agent")}
    )

    agente_anterior = conv.active_agent
    conv.active_agent = result.get("active_agent") or ""
    conv.flow_state = result.get("flow_state", conv.flow_state)
    flow_data = result.get("flow_data", conv.get_flow())
    conv.set_flow(flow_data)
    if flow_data.get("rut"):
        conv.rut = flow_data["rut"]
    if result.get("lead_class"):
        conv.lead_class = result["lead_class"]
    if result.get("stage"):
        conv.stage = result["stage"]
    await conv.asave()

    if result.get("handoff_reason"):
        from bot.models import registrar_incidente
        await sync_to_async(registrar_incidente, thread_sensitive=True)(
            conv, "handoff", context={"reason": result["handoff_reason"], "specialist": result.get("active_agent") or agente_anterior},
        )
    if result.get("requiere_revision"):
        from bot.models import registrar_incidente
        await sync_to_async(registrar_incidente, thread_sensitive=True)(
            conv, "revision_requerida", context={"motivo": result.get("motivo_revision")},
        )
    # El otro camino de salida. Cuando el especialista responde por la tool
    # `responder` el extractor no corre (los metadatos ya vinieron en sus args),
    # asi que el lead tiene que escribirse ACA tambien: si solo colgara del
    # extractor, estos turnos perderian el lead sin ningun error visible.
    # Los dos caminos llaman a la misma funcion, ver docs/PENDIENTES.md 32.a.
    if result.get("lead"):
        from bot.business.prospeccion import registrar_lead_del_turno
        await sync_to_async(registrar_lead_del_turno, thread_sensitive=True)(
            conv.wa_id, result["lead"])

    partes = []
    if result.get("response_text"):
        respuesta = result["response_text"]
        if ya_saludamos:
            respuesta = _quitar_saludo_inicial(respuesta, conv.name)
        partes = _dividir_en_mensajes(_a_formato_whatsapp(respuesta))

    if partes:
        # Solo la PRIMERA parte se manda dentro del turno: es la que el contacto
        # percibe como "el bot respondio", y es la unica que cita su mensaje con
        # reply_to. Las partes 2..N, la foto del modelo y la ubicacion de la
        # sucursal ocurren cuando el cliente ya esta leyendo, asi que salen por
        # la cola y dejan de retener el worker ~2s por turno (medido: 2,25s de
        # media en Langfuse, ver bot/whatsapp/cola_envio.py).
        await sync_to_async(get_wa_client().send_text)(conv.wa_id, partes[0], reply_to=msg_id)
        await _save_message(conv, "assistant", partes[0])

    # Si la respuesta salio como prosa, los metadatos del CRM todavia no
    # existen: los saca el extractor DENTRO de la cola, cuando el cliente ya
    # esta leyendo (ver bot/flow/extractor_metadatos.py y el spec del
    # 2026-09-03). El fallback de mensaje_cliente a `text` cubre el caso de que
    # el grafo no lo haya propagado.
    metadatos = None
    if result.get("metadatos_pendientes") and result.get("response_text"):
        metadatos = {
            "prosa": result["response_text"],
            "mensaje_cliente": result.get("mensaje_cliente") or text,
            "nombre_agente": result.get("active_agent") or "",
        }
    await encolar_envio(
        conv.pk, conv.wa_id, partes[1:], result.get("modelo_imagen"),
        result.get("sucursal_direccion_ids") or [], get_wa_client(),
        metadatos=metadatos, inline=envio_inline,
    )


async def handle_message(wa_id: str, name: str, text: str, msg_id: str, on_graph_result=None,
                         media_url: str | None = None, envio_inline: bool = False):
    # 1) Idempotencia por wa_msg_id
    if msg_id and await Message.objects.filter(wa_msg_id=msg_id).aexists():
        logger.info("[wa] msg %s ya procesado, ignorando reintento de Meta", msg_id)
        return

    conv, _ = await _get_or_create_conversation(wa_id, name)
    try:
        await _save_message(conv, "user", text, msg_id, media_url or "")
    except IntegrityError:
        # Ventana de carrera con el chequeo .aexists() de arriba: otra
        # ejecucion concurrente para el mismo msg_id ya guardo su Message
        # primero -- el UniqueConstraint de Message (ver bot/models.py) lo
        # frena aca a nivel BD. Sin esto, ambas ejecuciones seguian de
        # largo y cada una terminaba mandando su propia respuesta al
        # cliente (sintoma real reportado: mensaje de cierre repetido dos
        # veces seguidas sin mensaje del cliente entremedio).
        logger.info("[wa] msg %s ya procesado (carrera detectada por constraint), ignorando reintento de Meta", msg_id)
        return
    wa = get_wa_client()

    # El indicador "escribiendo..." se pide SOLO si de verdad vamos a hacer
    # esperar al contacto. Mostrarlo y no responder nada es peor que no
    # mostrarlo: Meta lo mantiene visible 25 segundos, así que el contacto ve
    # al bot "escribiendo" un mensaje que nunca llega. Los tres casos que no
    # deben pedirlo son los que responden al instante o no responden:
    #   - modo humano: contesta un ejecutivo por otro canal, el bot calla;
    #   - saludo que es todo el mensaje: se responde sin LLM, en un POST;
    #   - racha de acuses triviales: el 2do responde "👍" y el 3ro es silencio.
    # Se evalúa de lo más barato a lo más caro (dict, string, y recién ahí la
    # query de la racha) para no gastar una consulta en el camino instantáneo.
    en_modo_humano = (conv.get_flow() or {}).get("modo") == "HUMAN"
    hay_saludo, resto = _partir_saludo(text) if conv.flow_state == "IDLE" else (False, text)
    saludo_resuelve_todo = hay_saludo and not resto

    racha = None
    hara_esperar = not en_modo_humano and not saludo_resuelve_todo
    if hara_esperar:
        racha = await _acuses_triviales_consecutivos(conv)
        hara_esperar = racha < 2
    # Sin await sobre la red: ver encolar_acuse (785ms medidos por POST a Meta,
    # y el turno no depende del resultado).
    encolar_acuse(wa, msg_id, mostrar_escribiendo=hara_esperar, inline=envio_inline)

    # 2) Modo humano — el operador responde por otro canal, el bot no contesta
    if en_modo_humano:
        return

    # 3) Saludo — se responde SIN LLM y ANTES del grafo, solo si la
    # conversación está IDLE (un "hola" a mitad de conversación es contextual
    # y sí tiene que pasar por el LLM).
    #
    # Si el mensaje trae contenido además del saludo ("hola quiero cotizar una
    # suv"), el saludo sale igual al instante y el grafo se encarga del
    # contenido en ESTE MISMO request -- el webhook es sincrónico
    # (bot/whatsapp/webhooks.py::_dispatch), así que no hace falta ningún hilo
    # en background: basta con mandar la bienvenida primero. El contacto ve
    # respuesta en el tiempo de un POST a Meta y la respuesta real le llega
    # cuando está lista.
    #
    # El saludo se GUARDA antes de correr el grafo, así que entra en la ventana
    # de contexto (build_context_window) y el especialista ve que ya se saludó
    # -- es lo que evita el saludo duplicado, sin agregar ningún flag nuevo al
    # estado del grafo.
    if hay_saludo:
        identidad = await sync_to_async(get_setting)("welcome_message", "") or WELCOME_IDENTIDAD
        welcome = _texto_bienvenida(identidad, conv.name)
        if not resto:
            # El saludo era todo el mensaje: no viene ninguna respuesta detras,
            # asi que la invitacion es la que abre la conversacion.
            invitacion = await sync_to_async(get_setting)("welcome_invitacion", "") or WELCOME_INVITACION
            welcome = f"{welcome}\n\n{invitacion}"
        await sync_to_async(wa.send_text)(wa_id, welcome, reply_to=msg_id)
        await _save_message(conv, "assistant", welcome)
        conv.flow_state = "ESPERANDO_CONSULTA"
        await conv.asave()
        if not resto:
            return
        logger.info(
            "[wa] saludo instantaneo enviado a %s; el grafo sigue con el contenido: %r",
            wa_id, resto,
        )

    # 4) Conversacion ya cerrada por el bot + acuse del contacto -> silencio.
    #
    # Regla del usuario: despues de una despedida el bot no sigue mandando
    # mensajes hasta que llegue uno que reactive la conversacion con intencion
    # clara. Un acuse ("ok gracias") no es intencion; cualquier otra cosa si, y
    # cae sola al grafo porque no entra por aca.
    #
    # Se evalua ANTES de la racha a proposito: el caso real que esto arregla
    # ocurrio con racha=1 (el primer acuse tras la despedida), que la racha
    # dejaba pasar al LLM y producia una segunda despedida casi identica.
    #
    # racha ya se consulto arriba para decidir el indicador de "escribiendo"
    # (siempre, porque los dos caminos que la saltean -- modo humano y saludo
    # puro -- ya retornaron).
    if racha and await _bot_cerro_la_conversacion(conv):
        logger.info(
            "[wa] %s mando un acuse y el bot ya habia cerrado: no se responde", wa_id)
        return

    # Loop de despedidas con la conversacion todavia ABIERTA (el bot pregunto
    # algo): 2do acuse consecutivo -> emoji fijo, sin pasar por el LLM; 3ro+ ->
    # silencio. El 1ro sigue al grafo, porque con una pregunta abierta un
    # "perfecto" puede significar "si, dale" y merece respuesta real.
    if racha == 2:
        await sync_to_async(wa.send_text)(wa_id, "👍", reply_to=msg_id)
        await _save_message(conv, "assistant", "👍")
        return
    if racha >= 3:
        return

    # 5) Nada intercepto -> entra al grafo. ya_saludamos corta el saludo de
    # apertura de la respuesta: sin eso el contacto recibe la bienvenida y
    # acto seguido otro "¡Buenas Tomas!" del especialista.
    await _run_graph(conv, text, msg_id, on_graph_result=on_graph_result,
                     ya_saludamos=hay_saludo, envio_inline=envio_inline)
