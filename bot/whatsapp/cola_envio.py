"""Cola de envio para todo lo que NO es el primer mensaje del turno.

POR QUE EXISTE (auditoria de latencia 2026-09-03):

Una respuesta se parte en varios mensajes de WhatsApp para que se lea como una
persona escribiendo (`_dividir_en_mensajes`): 2,12 partes por turno de media,
hasta 5. Se mandaban todas EN SERIE y DENTRO del turno, a 0,94s por parte
medidos contra la API real de Meta. Medido de las dos puntas y coincide: el
tramo posterior a la ultima llamada al LLM son 2,25s de media en Langfuse, y
las rafagas multiparte duran 2,11s en la BD.

Lo importante de esos numeros es CUAL latencia atacan. Medido sobre 38 turnos
reales de la BD:

  - mensaje del cliente -> PRIMER mensaje del bot:  12,01s de media
  - mensaje del cliente -> ULTIMO mensaje del bot:  13,97s de media

Las partes 2..N ocurren DESPUES de que el cliente ya leyo la respuesta, asi que
sacarlas del turno no baja la latencia que el cliente siente (esos 12,01s son
casi todos LLM). Lo que si hace, y por eso existe este modulo, es devolver el
worker ~2s antes por turno: con varias conversaciones en paralelo esos 2s eran
cola para el siguiente contacto, y ESA cola si se siente. No se paralelizan las
partes entre si a proposito: WhatsApp las entrega en el orden en que se mandan y
una respuesta que llega desordenada es peor que una que llega lenta.

Se usa un thread y no `asyncio.create_task`: el webhook es una vista SINCRONA
que llama `async_to_sync(handle_message)` (ver bot/whatsapp/webhooks.py), asi
que el event loop se destruye al volver de handle_message y una task quedaria
huerfana a medio mandar. Los threads ya son el patron del repo para trabajo en
background (bot/seguimiento.py, bot/scraping/crawler.py).
"""

import logging
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor

from asgiref.sync import sync_to_async
from django.db import close_old_connections

logger = logging.getLogger(__name__)

# Cuantos fallos CONSECUTIVOS del extractor hacen falta para dejar un incidente.
#
# El fallback por turno (devolver {} y seguir) es deliberado y no se toca: el
# mensaje ya se le mando al cliente, asi que un extractor caido no puede tumbar
# el turno (spec R5). Lo que faltaba era el otro lado: sin contador ni
# incidente, una falla SISTEMATICA es invisible -- el bot sigue respondiendo
# perfecto y deja de clasificar leads, de mover stage/flow_state y de DERIVAR A
# HUMANOS, que es su funcion mas delicada (9% de los turnos, spec R3).
# Precedente real en este mismo repo: el 402 "Insufficient Balance" de DeepSeek
# del 2026-09-02 rompio el indexado del RAG sin que nada avisara
# (docs/PENDIENTES.md #1).
#
# Por que 3 y no 1: el extractor es una llamada a un LLM y un fallo suelto es
# ruido; un incidente por cada uno se ignora, y un incidente que se ignora es
# peor que ninguno. Errar del lado silencioso sigue siendo deliberado: el fallo
# aislado igual queda en el log como error.
#
# BAJADO DE 5 A 3 el 2026-09-08, con el visto bueno del usuario, porque el
# reintento del extractor (bot/flow/extractor_metadatos.py) cambio lo que
# significa un fallo. Antes era UN timeout de 15s, que con la cola medida
# (84,5% de cobertura a 15s, n=84) pasaba el 15,5% de las veces por varianza
# del proveedor: hacian falta 5 para descartar la mala suerte. Ahora un `{}`
# es "3 intentos fallidos en hasta 35s" sobre un tope de 25s que cubre el
# 96,4%, asi que la probabilidad de tres rachas seguidas por azar es mucho mas
# baja y esperar 5 turnos avisa tarde. El umbral sigue el mismo criterio de
# antes (descartar el ruido), aplicado a la nueva tasa de fallo.
_RACHA_PARA_INCIDENTE = 3

# La racha se lleva en la BD (una fila de Setting) y no en memoria del proceso:
# gunicorn corre 2 workers x 8 threads, asi que un contador de proceso cuenta
# por worker -- el umbral real se multiplicaria por la cantidad de workers y
# encima cada worker dejaria su propio incidente por la misma caida. Mismo
# criterio que el UniqueConstraint de CampaignSend (bot/models.py), que existe
# exactamente por este motivo.
_CLAVE_RACHA = "extractor_fallos_consecutivos"

# kind propio y no "error_tecnico": registrar_incidente dedupea por
# conversacion+kind, y compartir el kind con la falla de envio de la cola
# (_registrar_incidente_de_cola) haria que una pisara el contexto de la otra.
# Ademas el panel muestra el kind crudo como badge (frontend/src/panels/
# IncidentsPanel.tsx), asi que un nombre que se explique solo vale mas.
_KIND_EXTRACTOR_CAIDO = "extractor_caido"

# Ultimo resultado del extractor por wa_id. Existe porque desde el refactor de
# prosa (2026-09-03) el resultado del grafo YA NO trae `intent` ni
# `modelo_imagen`: nacen ACA, despues de que el grafo termino. Sin este punto
# de lectura, el simulador (bot/simulator/app_wrapper.py) armaba cada turno con
# `intent=None` y su evaluador `imagen_solo_con_intent_correcto` marcaba
# violacion falsa en cada foto enviada -- o sea que el chequeo dejo de chequear.
# Es un cache de observabilidad, no estado del sistema: se le pone tope y
# perder una entrada solo significa que el simulador no ve ese turno.
_ULTIMA_EXTRACCION: "OrderedDict[str, dict]" = OrderedDict()
_TOPE_ULTIMA_EXTRACCION = 64

# max_workers=1 a proposito: un solo thread FIFO garantiza que las partes de un
# turno salgan en orden y que dos turnos del mismo contacto no se entrelacen,
# sin tener que llevar un lock por wa_id. El techo es ~1 turno por segundo de
# trabajo de cola (1,1 partes extra x 0,94s), muy por encima del volumen actual;
# si algun dia hace falta mas, la salida es serializar por wa_id, no subir
# max_workers a ciegas.
_EJECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wa-cola")


# Executor SEPARADO del de envio, a proposito. El acuse de lectura y el
# indicador "escribiendo..." solo sirven si llegan YA; si compartieran la cola
# FIFO de un solo thread de _EJECUTOR, un acuse podria quedar esperando detras
# de las partes 2..N del turno anterior (0,94s cada una) y llegar tarde, que es
# justo lo contrario de para lo que existe.
_EJECUTOR_ACUSE = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wa-acuse")


def encolar_acuse(wa, msg_id: str, mostrar_escribiendo: bool = False,
                  inline: bool = False) -> Future | None:
    """Manda el acuse de lectura (y opcionalmente "escribiendo...") SIN esperarlo.

    Medido el 2026-09-03: un `POST /messages` real a Meta tarda 785ms de
    mediana (n=24, p90 1096ms) -- no los 142ms que decia el comentario viejo de
    bot/whatsapp/client.py, que se habia medido contra `GET /me` con token
    invalido, un endpoint que Meta rechaza en el borde y no sirve de proxy.

    Ese POST se esperaba ANTES de arrancar el grafo y nada depende de su
    resultado: son ~700ms de overhead puro por turno. Mandandolo desde un
    thread, el acuse y el "escribiendo..." salen igual de temprano (el thread
    arranca de inmediato) pero el turno ya no los espera.

    Fire-and-forget de verdad: si falla, se loguea y no se propaga. Un acuse de
    lectura perdido no vale abortar una respuesta que el cliente si va a leer.

    `inline=True` lo corre en el hilo actual, mismo criterio que `encolar`: los
    llamadores que verifican el acuse inmediatamente despues (tests, y el
    camino de media no soportada, que igual bloquea en su propio send_text)
    necesitan que sea deterministico.
    """
    if not msg_id:
        return None

    def _run():
        close_old_connections()
        try:
            wa.mark_as_read(msg_id, mostrar_escribiendo=mostrar_escribiendo)
        except Exception:
            logger.warning("[cola] fallo el acuse de lectura de %s", msg_id, exc_info=True)
        finally:
            close_old_connections()

    if inline:
        _run()
        return None
    return _EJECUTOR_ACUSE.submit(_run)


async def encolar(conv_pk: int, wa_id: str, partes: list[str], modelo_imagen: str | None,
                  sucursal_ids: list[int], wa, metadatos: dict | None = None,
                  inline: bool = False) -> Future | None:
    """Despacha la cola del turno. Devuelve el Future, o None si no habia nada.

    `wa` es el cliente de WhatsApp YA RESUELTO, no se busca aca a proposito:
    asi la cola usa exactamente el mismo objeto que el turno, y queda UN solo
    punto donde mockear el envio (`bot.whatsapp.handlers.get_wa_client`) en vez
    de dos que hay que acordarse de patchear juntos. Este repo ya se comio ese
    bug: un patch que dejo de interceptar en silencio tras mover el codigo de
    modulo. Sin esto, cualquier test de handlers que no supiera de la cola
    habria mandado mensajes reales a la API de Meta desde el thread.

    `inline=True` corre todo en el hilo actual en vez de la cola. Lo necesitan
    los llamadores que leen el resultado del envio inmediatamente despues de
    que handle_message vuelve -- el simulador
    (bot/simulator/app_wrapper.py::build_app) arma `bot_responde` juntando las
    llamadas a send_text, y con la cola en background solo veria la primera
    parte y le entregaria al juez una respuesta truncada. Los tests lo usan por
    la misma razon, mas una propia: un thread no ve las escrituras sin commitear
    de la transaccion de un TestCase.

    Es una coroutine porque el modo inline lo obliga: `_enviar` usa el ORM
    sincrono y el llamador (`_run_graph`) es async, asi que correrlo derecho en
    este hilo levanta SynchronousOnlyOperation de Django. Va por sync_to_async,
    que ademas lo deja en el mismo hilo/conexion que el resto del turno -- por
    eso el modo inline ve las escrituras de una transaccion de test. Lo
    encontraron los tests de imagen de RunGraphModeloImagenTest, no una
    revision: el camino en background no lo tocaba porque el thread del
    ejecutor si es un hilo sincrono de verdad.

    `metadatos`, cuando viene, es {"prosa", "mensaje_cliente", "nombre_agente"}:
    la senal de que la respuesta salio como prosa y los metadatos del CRM
    todavia hay que extraerlos (ver bot/flow/extractor_metadatos.py). Se hace
    ACA y no en el grafo porque cuando esto corre el cliente ya leyo su
    mensaje, asi que la extraccion no le cuesta latencia percibida (spec R2).

    Ojo con la condicion de abajo: `metadatos` cuenta como trabajo pendiente.
    Sin eso, un turno de UNA sola parte (lo mas comun) saldria temprano y no
    extraeria nada.
    """
    if not partes and not modelo_imagen and not sucursal_ids and not metadatos:
        return None
    args = (conv_pk, wa_id, partes, modelo_imagen, sucursal_ids, wa, metadatos)
    if inline:
        await sync_to_async(_enviar, thread_sensitive=True)(*args)
        return None
    return _EJECUTOR.submit(_enviar, *args)


def _enviar(conv_pk: int, wa_id: str, partes: list[str], modelo_imagen: str | None,
            sucursal_ids: list[int], wa, metadatos: dict | None = None) -> None:
    # Import local: handlers importa este modulo, asi que a nivel de modulo
    # seria circular. Mismo patron que el resto de imports diferidos del repo.
    from bot.models import Conversation

    # Este thread tiene sus propias conexiones a la BD (son thread-local en
    # Django). close_old_connections al entrar descarta una conexion vencida de
    # un turno anterior, y al salir evita dejarla colgando entre turnos.
    close_old_connections()
    try:
        conv = Conversation.objects.filter(pk=conv_pk).first()
        if conv is None:
            logger.warning("[cola] conversacion %s ya no existe, se descarta la cola", conv_pk)
            return
        for parte in partes:
            # reply_to=None: solo el primer mensaje del turno (el que salio
            # dentro del request) cita el mensaje del contacto; los siguientes
            # son continuacion natural de la conversacion.
            wa.send_text(wa_id, parte)
            _guardar(conv, parte)
        if metadatos:
            # El orden importa y esta testeado: primero salen las partes (el
            # cliente lee), recien despues se extrae.
            meta = _extraer_sync(metadatos["prosa"], metadatos["mensaje_cliente"],
                                 metadatos["nombre_agente"], conv=conv)
            _recordar_extraccion(wa_id, meta)
            _vigilar_racha_del_extractor(conv, meta)
            _persistir_metadatos(conv, meta)
        if modelo_imagen:
            _enviar_imagen(conv, wa, modelo_imagen)
        for sucursal_id in sucursal_ids:
            _enviar_ubicacion(conv, wa, sucursal_id)
    except Exception:
        # La cola corre fuera del request: si revienta, no hay nadie mirando el
        # HTTP 500. Se deja incidente para que aparezca en el panel, igual que
        # cualquier otra falla no manejada del turno (ver handlers.py).
        logger.exception("[cola] fallo el envio en background para %s", wa_id)
        _registrar_incidente_de_cola(conv_pk, wa_id)
    finally:
        close_old_connections()


def _guardar(conv, contenido: str) -> None:
    from bot.whatsapp.handlers import _crear_mensaje
    _crear_mensaje(conv, "assistant", contenido, "", "")


def _enviar_imagen(conv, wa, slug_modelo: str) -> None:
    from bot.flow.context_window import marcador_imagen_enviada
    from bot.whatsapp.handlers import _imagen_enviada_recientemente_sync
    from bot.scraping.imagenes import resolver_imagen_modelo
    try:
        if _imagen_enviada_recientemente_sync(conv, slug_modelo):
            logger.info("[cola] imagen de %s ya se envio en la sesion activa, no se reenvia", slug_modelo)
            return
        url_imagen = resolver_imagen_modelo(slug_modelo)
        if url_imagen:
            wa.send_image(conv.wa_id, url_imagen)
            _guardar(conv, marcador_imagen_enviada(slug_modelo))
    except Exception:
        # Aislado del resto de la cola: que falle la foto no debe impedir que
        # salga la ubicacion de la sucursal (ni al reves).
        logger.exception("[cola] fallo al mandar la imagen del modelo %s", slug_modelo)


def _enviar_ubicacion(conv, wa, sucursal_id: int) -> None:
    from bot.models import Sucursal
    try:
        sucursal = Sucursal.objects.filter(pk=sucursal_id).first()
        if sucursal and sucursal.latitud is not None and sucursal.longitud is not None:
            wa.send_location(
                conv.wa_id, sucursal.latitud, sucursal.longitud,
                name=sucursal.nombre, address=sucursal.direccion,
            )
    except Exception:
        logger.exception("[cola] fallo al mandar la ubicacion de la sucursal %s", sucursal_id)


def _registrar_incidente_de_cola(conv_pk: int, wa_id: str) -> None:
    try:
        from bot.models import Conversation, registrar_incidente
        conv = Conversation.objects.filter(pk=conv_pk).first()
        if conv is not None:
            registrar_incidente(conv, "error_tecnico", context={
                "origen": "cola_de_envio",
                "detalle": f"fallo el envio en background de los mensajes de continuacion a {wa_id}",
            })
    except Exception:
        logger.exception("[cola] tampoco se pudo registrar el incidente de la cola")


def _recordar_extraccion(wa_id: str, meta: dict) -> None:
    """Deja legible lo que el extractor decidio en este turno (ver
    `_ULTIMA_EXTRACCION`). Se guarda tambien el `{}` de un fallo: para el
    simulador "no se extrajo nada" es un dato, no una ausencia."""
    _ULTIMA_EXTRACCION[wa_id] = dict(meta or {})
    _ULTIMA_EXTRACCION.move_to_end(wa_id)
    while len(_ULTIMA_EXTRACCION) > _TOPE_ULTIMA_EXTRACCION:
        _ULTIMA_EXTRACCION.popitem(last=False)


def ultima_extraccion(wa_id: str) -> dict:
    """Metadatos que el extractor saco en el ultimo turno de ese contacto, o
    {} si no hay ninguno guardado (contacto nuevo, camino JSON viejo, o entrada
    ya desalojada por el tope)."""
    return _ULTIMA_EXTRACCION.get(wa_id, {})


def _vigilar_racha_del_extractor(conv, meta: dict) -> None:
    """Cuenta fallos consecutivos del extractor y deja UN incidente por racha.

    `meta` vacio == fallo, y la equivalencia es solida: en el camino sano
    `extraer_metadatos` siempre devuelve al menos los campos de CAMPOS_BASE
    (bot/flow/respuesta.py::campos_de, que nunca es un conjunto vacio), asi que
    {} solo puede salir de sus tres `return {}` de error.

    El incidente cuelga de la conversacion cuyo turno cruzo el umbral. No es
    que esa conversacion sea la culpable -- una falla sistematica no es de
    nadie en particular -- pero el panel lista los incidentes POR conversacion
    (admin_panel/views.py::api_incidents) y uno con conversation=None no se ve
    en ningun lado, que es justo el problema que este contador viene a resolver.
    """
    from bot.models import Setting, registrar_incidente

    if meta:
        # UPDATE directo, sin leer antes: este es el camino normal (un turno
        # sano por cada turno) y no vale una query extra. `exclude(value="0")`
        # hace que el caso habitual toque 0 filas.
        Setting.objects.filter(key=_CLAVE_RACHA).exclude(value="0").update(value="0")
        return

    fila, _ = Setting.objects.get_or_create(key=_CLAVE_RACHA, defaults={"value": "0"})
    try:
        racha = int(fila.value) + 1
    except (TypeError, ValueError):
        racha = 1
    fila.value = str(racha)
    fila.save(update_fields=["value"])
    logger.error(
        "[cola] fallo el extractor de metadatos (racha: %s): este turno no clasifico "
        "el lead ni evaluo el handoff de %s", racha, conv.wa_id,
    )
    # `!=` y no `>=`: UN incidente por racha, no uno por turno caido. La racha
    # sigue contando para que el proximo exito la vuelva a cero y una segunda
    # caida vuelva a avisar. El read-modify-write de arriba no lleva lock a
    # proposito: si dos workers pisan el mismo incremento la cuenta queda corta,
    # o sea que el aviso tarda un turno mas -- el error posible es el silencioso,
    # nunca el ruidoso.
    if racha != _RACHA_PARA_INCIDENTE:
        return
    registrar_incidente(conv, _KIND_EXTRACTOR_CAIDO, context={
        "origen": "extractor_metadatos",
        "detalle": (
            f"el extractor de metadatos fallo {racha} turnos seguidos. El bot sigue "
            "respondiendo, pero no esta clasificando leads, ni moviendo el stage, ni "
            "derivando a humanos. Revisar los logs del contenedor ([extractor]) y el "
            "saldo/estado de la API de OpenRouter."
        ),
    })


def _extraer_sync(prosa: str, mensaje_cliente: str, nombre_agente: str,
                  conv=None) -> dict:
    """Puente sincrono al extractor.

    Existe por dos razones: este thread no tiene event loop propio (asyncio.run
    le crea uno), y da UN solo punto donde mockear la extraccion en los tests
    sin tocar la red.

    El HISTORIAL se arma ACA y no dentro del extractor (docs/PENDIENTES.md 33,
    punto (b)): `build_context_window` consulta la BD y `extraer_metadatos` es
    async, donde una query levanta SynchronousOnlyOperation -- el mismo error
    que ya se pago con `bloque_sucursal_unica` y `bloque_datos_del_lead`. Este
    thread SI tiene BD (close_old_connections al entrar a `_enviar`), asi que
    es el lugar correcto.

    `conv` es opcional para no romper a ningun llamador: sin conversacion el
    extractor clasifica el turno suelto, que es el comportamiento anterior.
    Un fallo armando la ventana NO puede tumbar la extraccion -- el handoff
    vale mas que el resumen, asi que se sigue sin historial y se deja ruido.
    """
    import asyncio

    from bot.flow.extractor_metadatos import extraer_metadatos

    historial = None
    if conv is not None:
        try:
            from bot.flow.context_window import build_context_window
            historial = build_context_window(conv)
        except Exception:
            logger.warning(
                "[cola] no pude armar el historial para el extractor de %s: "
                "se extrae sin contexto del caso", conv.wa_id, exc_info=True,
            )
    return asyncio.run(extraer_metadatos(
        prosa, mensaje_cliente, nombre_agente, historial=historial))


def _persistir_metadatos(conv, meta: dict) -> None:
    """Guarda en la Conversation lo que el extractor saco de la prosa.

    Mismo criterio que el bloque post-grafo de bot/whatsapp/handlers.py: un
    campo vacio o None NO pisa el valor previo. El extractor puede no tener
    evidencia de algo que el turno anterior si sabia, y perderlo seria peor que
    no actualizarlo.
    """
    from bot.models import registrar_incidente

    if not meta:
        return
    cambio = False
    if meta.get("lead_class"):
        conv.lead_class = meta["lead_class"]
        cambio = True
    if meta.get("stage"):
        conv.stage = meta["stage"]
        cambio = True
    if meta.get("next_state"):
        conv.flow_state = meta["next_state"]
        cambio = True
    extra = meta.get("extracted_data") or {}
    if extra:
        from bot.flow.flow_data import fusionar_flow_data
        flow = fusionar_flow_data(conv.get_flow(), extra)
        conv.set_flow(flow)
        if flow.get("rut"):
            conv.rut = flow["rut"]
        cambio = True
    if cambio:
        conv.save()
    # El lead comercial va DESPUES del save de la conversacion y en su propio
    # try: escribe otra tabla (LeadInTouch) y su fallo no puede costar los
    # metadatos de arriba, que ya estan guardados. Antes de este stack esto era
    # una tool que el especialista pedia en una segunda ronda, dentro de la
    # latencia que el contacto siente -- ver el spec §7.1.
    from bot.business.lead_intouch import registrar_lead_del_turno

    registrar_lead_del_turno(conv.wa_id, meta.get("lead"))
    # Los incidentes van DESPUES del save: si el save falla, no queremos haber
    # notificado al equipo comercial por un turno que no quedo guardado.
    if meta.get("handoff"):
        registrar_incidente(conv, "handoff", context={
            "reason": meta.get("handoff_reason"),
            "specialist": conv.active_agent,
            "origen": "extractor",
        })
    if meta.get("requiere_revision"):
        registrar_incidente(conv, "revision_requerida", context={
            "motivo": meta.get("motivo_revision"),
            "origen": "extractor",
        })
