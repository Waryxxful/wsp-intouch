"""Escritura y despacho del lead comercial B2B de InTouch.

POR QUÉ ESTO NO ES UNA TOOL (spec §7.1, medido en Cavem sobre 324 turnos):
`registrar_datos_lead` era una tool que el especialista pedía en una SEGUNDA
ronda de herramientas -- una llamada entera al LLM, 4,53s de mediana, en el
17,3% de los turnos. Acá el lead entra por la llamada que ya se hacía igual: la
del extractor de metadatos, después de que el mensaje salió al contacto.

La consecuencia de diseño, que está en el prompt: el bot NO puede decir "tu
solicitud quedó registrada" en el mismo turno, porque cuando redacta esa frase
el lead todavía no se escribió.
"""

import logging
import re

from django.conf import settings
from django.utils import timezone

from bot.models import (
    Conversation, LeadInTouch, SenalesLead, calcular_score_intouch, tiene_consentimiento,
)

logger = logging.getLogger(__name__)

# Los destinos externos que este bot sabe usar. Explícito y no "cualquier
# string": un valor desconocido en LEAD_SINK significaría leads que no se
# despachan a ningún lado sin que nada avise.
SINKS_VALIDOS = frozenset({"none", "http"})

# Campos que se pueden escribir. Explícito y no "cualquier clave": sin esta
# lista, un nombre de campo alucinado por el LLM se escribiría como atributo
# suelto y se perdería sin error visible.
CAMPOS_ESCRIBIBLES = frozenset({
    "nombre_completo", "correo", "empresa", "industria", "subtipo_automotriz",
    "cargo", "pais_ciudad", "situacion_contact_center", "tipo_contact_center",
    "usa_ia_actualmente", "canales_actuales", "volumen_interacciones",
    "necesidad_principal", "soluciones_interes", "intencion", "plazo_proyecto",
    "preferencia_horaria",
    "solicita_consultoria", "solicita_contacto_humano",
    "resumen_conversacion", "siguiente_accion_recomendada",
})

# Los campos que JUSTIFICAN abrir un lead: antecedentes que el contacto
# entregó, más las dos solicitudes explícitas del caso B del prompt §7.
#
# NO incluye `nombre_completo`: WhatsApp entrega el nombre del perfil sin que
# el contacto lo haya dado. Tampoco los campos de lectura de la conversación
# (`resumen_conversacion`, `siguiente_accion_recomendada`, `intencion`): el
# extractor los llena en casi todos los turnos, así que un "hola" abriría un
# lead NO_CALIFICADO, el panel se llenaría de filas vacías y -- peor -- las
# métricas de campaña cuentan leads como conversiones.
ANTECEDENTES_QUE_ABREN_LEAD = frozenset({
    "empresa", "correo", "industria", "cargo", "pais_ciudad",
    "necesidad_principal", "situacion_contact_center", "tipo_contact_center",
    "canales_actuales", "volumen_interacciones", "usa_ia_actualmente",
    "plazo_proyecto", "soluciones_interes",
    "solicita_contacto_humano", "solicita_consultoria",
})

# Formato mínimo de un correo: algo, una arroba, un dominio con punto. El
# prompt §4 es explícito en que sólo se revisa el FORMATO -- nunca se afirma
# que el correo existe, y se aceptan correos personales.
_RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_CAMPOS_BOOLEANOS = frozenset({
    "usa_ia_actualmente", "solicita_consultoria", "solicita_contacto_humano",
})
_CAMPOS_LISTA = frozenset({"canales_actuales", "soluciones_interes"})

# Las formas que el extractor puede mandar para cada estado de un campo
# booleano. El schema (`bot/flow/extractor_metadatos.py`) ya restringe el enum a
# "si"/"no"/"", pero esto se lee tolerante a propósito: el que escribe es un
# LLM y "sí" con tilde o "true" no pueden costar un dato.
_SI = frozenset({"si", "sí", "true", "verdadero", "1"})
_NO = frozenset({"no", "false", "falso", "0"})


def _tri_estado(valor):
    """Los tres estados de un campo booleano del lead: sí, no y "no se sabe".

    UN BOOLEANO DE DOS ESTADOS NO PUEDE REPRESENTAR TRES, y eso costó datos
    reales. El extractor manda el objeto completo en cada turno porque
    `strict: true` se lo exige, y el prompt le pedía `false` en todo lo que no
    se hubiera dicho. `False` no cae en el centinela de vacío de más abajo (no
    es `None`, ni `""`, ni `[]`, ni `{}`), así que se escribía: un
    `solicita_contacto_humano` capturado en el turno N volvía a `False` en el
    N+1, y se perdía justamente la señal de que el contacto pidió hablar con
    una persona.

    Agregar `False` al centinela NO era el arreglo -- habría borrado el caso
    legítimo "el contacto dijo que no usa IA", que es un antecedente que el
    ejecutivo necesita. Los tres campos pasaron a enum de tres valores en el
    schema del extractor ("si" / "no" / "") y acá se traducen.

    Devuelve None cuando no hay evidencia, y el llamador NO escribe. Por eso
    `usa_ia_actualmente` puede quedar en NULL, que es para lo que la columna es
    `null=True`: "no se sabe" y "no usa" no son lo mismo, y el panel le mostraba
    "no usa IA" al ejecutivo sobre empresas a las que nadie les preguntó.
    """
    if isinstance(valor, bool):
        # El schema ya no lo permite. Un booleano suelto -- una llamada vieja, o
        # el modelo saliéndose del enum -- se lee conservador: un True afirma
        # algo, pero un False es indistinguible del relleno que causó el
        # defecto, así que no pisa lo capturado.
        return True if valor else None
    texto = str(valor).strip().lower()
    if texto in _SI:
        return True
    if texto in _NO:
        return False
    return None


def _recortar(instancia, campo: str, valor):
    """Recorta un texto al max_length real de su columna.

    Django NO trunca al guardar: SQLite (los tests) acepta el exceso en
    silencio, pero SQL Server -- el backend de producción -- levanta "String or
    binary data would be truncated" y la escritura revienta, perdiendo todos
    los datos capturados en esa llamada.
    """
    largo = instancia._meta.get_field(campo).max_length
    if largo and isinstance(valor, str) and len(valor) > largo:
        return valor[:largo]
    return valor


def _senales_desde(crudo) -> SenalesLead | None:
    """Las señales del extractor como dataclass, ignorando lo que no conozca.

    Un nombre de señal que el modelo invente no puede reventar la escritura del
    lead: se descarta. La dataclass es la lista blanca.
    """
    if not isinstance(crudo, dict):
        return None
    campos = {f.name for f in SenalesLead.__dataclass_fields__.values()}
    return SenalesLead(**{k: bool(v) for k, v in crudo.items() if k in campos})


def _registrar_lead_impl(wa_id: str, datos: dict, senales=None) -> dict:
    conversation = Conversation.objects.filter(wa_id=wa_id).first()
    if conversation is None:
        return {"ok": False, "motivo": "no encontré la conversación."}

    lead, _ = LeadInTouch.objects.get_or_create(
        conversation=conversation,
        defaults={"nombre_completo": conversation.name or ""},
    )

    datos = dict(datos or {})
    rechazados = {}
    ignorados = []

    # El correo se filtra ANTES del bucle: una vez escrito, el equipo comercial
    # le escribe a una dirección que no existe.
    correo = (datos.get("correo") or "").strip()
    if correo and not _RE_CORREO.match(correo):
        datos.pop("correo")
        rechazados["correo"] = (
            f"'{correo}' no tiene formato de correo válido. Pídele al contacto que "
            "lo confirme una vez; si sigue sin ser válido, déjalo vacío. No lo corrijas tú."
        )

    for campo, valor in datos.items():
        if campo not in CAMPOS_ESCRIBIBLES:
            ignorados.append(campo)
            continue
        # Un valor vacío NO borra lo que ya se sabía: el extractor manda el
        # objeto completo en cada turno porque `strict: true` se lo exige, y
        # suele mandar en blanco lo que no se habló en ese turno. Sin esta
        # guarda, cada turno pisaría con vacío los datos de los anteriores.
        if valor in (None, "", [], {}):
            continue
        if campo in _CAMPOS_BOOLEANOS:
            estado = _tri_estado(valor)
            if estado is None:
                continue
            setattr(lead, campo, estado)
        elif campo in _CAMPOS_LISTA:
            setattr(lead, campo, list(valor) if isinstance(valor, (list, tuple)) else [])
        else:
            setattr(lead, campo, _recortar(lead, campo, valor))

    # El score se recalcula SÓLO si este turno trajo señales. Un turno de
    # cortesía sin señales no puede degradar un lead que ya calificó.
    score_previo = lead.lead_score
    convertidas = _senales_desde(senales)
    if convertidas is not None:
        lead.lead_score = calcular_score_intouch(convertidas)

    # Se notifica en la TRANSICIÓN a HOT y se sella con `notificado_en`. Sin el
    # sello, el equipo comercial recibiría una notificación por cada mensaje que
    # el contacto siga escribiendo, que es la forma más rápida de que la
    # apaguen.
    paso_a_hot = lead.lead_score == "HOT" and score_previo != "HOT" and not lead.notificado_en
    if paso_a_hot:
        lead.notificado_en = timezone.now()

    lead.save()

    if paso_a_hot:
        # En su propio try y después del save: el lead ya está guardado, así
        # que un orquestador caído no puede costarlo. `notificar` tampoco
        # propaga, pero la defensa vale doble porque acá se decide el sello.
        try:
            from bot import notify

            quien = lead.empresa or lead.nombre_completo or wa_id
            notify.notificar(
                tipo="lead_hot",
                mensaje=f"Oportunidad HOT: {quien}. {lead.necesidad_principal or ''}".strip(),
                url="/wsp/intouch/leads",
            )
        except Exception:
            logger.warning("[lead] no pude notificar el lead HOT de %s", wa_id,
                           exc_info=True)

    _despachar_si_corresponde(wa_id)

    resultado = {
        "ok": True,
        "lead_score": lead.lead_score,
        "datos_que_faltan": sorted(
            campo for campo in ("empresa", "correo", "industria", "necesidad_principal")
            if not getattr(lead, campo)
        ),
    }
    if ignorados:
        resultado["campos_ignorados"] = sorted(ignorados)
    if rechazados:
        resultado["campos_rechazados"] = rechazados
    return resultado


def registrar_lead_del_turno(wa_id: str, lead) -> None:
    """`_registrar_lead_impl` con la guarda de apertura y el aislamiento del turno.

    Es el punto de entrada que usan los DOS caminos de salida: la cola de
    envío cuando la respuesta sale en prosa (el camino normal, el lead lo trae
    el extractor) y el bloque post-grafo de `bot/whatsapp/handlers.py` cuando
    sale por la tool `responder` (el lead lo trae el modelo en sus argumentos).
    Los dos escriben en `LeadInTouch` y eso está anclado por
    `bot/tests/test_lead_dos_caminos.py`: mientras uno de los dos apuntaba al
    escritor automotriz heredado, sus turnos perdían el lead en silencio.

    Tres reglas:

    1. Un `lead` vacío no hace nada (el extractor manda el objeto completo en
       blanco cuando no capturó nada).
    2. La fila se abre SÓLO con un antecedente real -- ver
       ANTECEDENTES_QUE_ABREN_LEAD. Sobre un lead que ya existe se escribe todo.
    3. Un fallo escribiendo el lead no puede tumbar lo que ya venía hecho del
       turno: es lo último que pasa y ya nadie lo espera.

    El log va en error y no en warning porque el síntoma de perder esto es
    silencioso: el chat sale igual de bien y el equipo comercial simplemente no
    ve el lead.
    """
    if not isinstance(lead, dict):
        return
    datos = {k: v for k, v in lead.items() if k != "senales"}
    con_valor = {c for c, v in datos.items() if v not in (None, "", [], {}, False)}
    if not con_valor:
        return
    try:
        if not (con_valor & ANTECEDENTES_QUE_ABREN_LEAD) and not (
                LeadInTouch.objects.filter(conversation__wa_id=wa_id).exists()):
            return
        _registrar_lead_impl(wa_id, datos, senales=lead.get("senales"))
    except Exception:
        logger.error("[lead] no pude registrar el lead de %s", wa_id, exc_info=True)


def clave_contacto(lead) -> str:
    """La identidad comercial del contacto, estable para siempre.

    Se llamaba `clave_idempotencia`, y el nombre mentía: `Conversation.wa_id`
    es `unique=True`, así que hay UNA conversación por número y esta clave
    identifica al CONTACTO, no a una conversación. La misma persona que vuelve
    a escribir meses después reusa la misma fila y la misma clave -- que es
    justo lo que el CRM necesita para no abrir un registro nuevo.

    Va hasheada: viaja a otro sistema y no tiene por qué llevar el teléfono en
    claro cuando un hash cumple la misma función.
    """
    import hashlib

    crudo = f"wsp_intouch:{lead.conversation.wa_id}"
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:32]


# Alias del nombre viejo, para no romper llamadas que queden en el repo.
clave_idempotencia = clave_contacto


# Los campos del payload que son TRANSPORTE y no contenido. Si entraran al
# hash, cada reintento parecería contenido nuevo y el despacho no sería
# idempotente nunca.
_CAMPOS_DE_TRANSPORTE = frozenset({"evento_id", "revision"})


def hash_de_negocio(payload: dict) -> str:
    """Hash del contenido comercial de un payload, ignorando el transporte.

    Ordenado y serializado de forma estable: dos payloads con las mismas claves
    en otro orden tienen que dar el mismo hash, o cada turno abriría un evento
    nuevo sin que nada hubiera cambiado.
    """
    import hashlib
    import json

    negocio = {k: v for k, v in payload.items() if k not in _CAMPOS_DE_TRANSPORTE}
    serializado = json.dumps(negocio, sort_keys=True, ensure_ascii=False,
                             default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def marcar_evento(lead) -> bool:
    """Abre un evento nuevo si el contenido del lead cambió. True si lo abrió.

    Es lo que separa "reintento del mismo envío" de "nueva actualización
    comercial", que es la distinción que el receptor no puede hacer solo.
    """
    import uuid

    payload = payload_del_lead(lead)
    hash_actual = hash_de_negocio(payload)
    if lead.evento_id and lead.payload_hash == hash_actual:
        return False

    lead.evento_id = str(uuid.uuid4())
    lead.payload_hash = hash_actual
    lead.revision = (lead.revision or 0) + 1
    # `despachado_en` se limpia: hay contenido nuevo que todavía no llegó al
    # destino, y dejarlo sellado escondería el lead de los pendientes.
    lead.despachado_en = None
    # El conflicto se limpia por la misma razón: si el contenido cambió, el
    # dato nuevo puede ser justo lo que resuelve la ambigüedad que el receptor
    # señaló (un correo que faltaba, un teléfono corregido), así que el lead
    # vuelve a ser despachable en el próximo intento.
    lead.conflicto_en = None
    lead.conflicto_motivo = ""
    lead.save(update_fields=[
        "evento_id", "payload_hash", "revision", "despachado_en",
        "conflicto_en", "conflicto_motivo"])
    return True


# Los campos del contrato que viajan al destino externo. Explícito y no
# `__dict__`: así un campo interno nuevo (un flag de proceso, una marca de
# tiempo) no se filtra al payload sin que nadie lo decida.
_CAMPOS_DEL_PAYLOAD = (
    "nombre_completo", "correo", "empresa", "industria", "subtipo_automotriz",
    "cargo", "pais_ciudad", "situacion_contact_center", "tipo_contact_center",
    "usa_ia_actualmente", "canales_actuales", "volumen_interacciones",
    "necesidad_principal", "soluciones_interes", "intencion", "plazo_proyecto",
    # No es una hora reservada: es el día u horario que el contacto dijo que
    # le acomoda. El help_text del modelo se mantiene.
    "preferencia_horaria",
    "lead_score", "solicita_consultoria", "solicita_contacto_humano",
    "resumen_conversacion", "siguiente_accion_recomendada",
)


def payload_del_lead(lead) -> dict:
    """El lead como lo espera el endpoint del spec B."""
    payload = {campo: getattr(lead, campo) for campo in _CAMPOS_DEL_PAYLOAD}
    # El teléfono lo agrega la PLATAFORMA desde los metadatos de WhatsApp, no
    # el modelo: el prompt le prohíbe pedirlo, pero el equipo comercial
    # necesita a quién llamar. Si hay texto de consentimiento y el contacto
    # no lo otorgó, el número no sale; el resto del lead sí.
    wa_id = lead.conversation.wa_id
    texto = getattr(settings, "TEXTO_CONSENTIMIENTO", "") or ""
    texto_configurado = bool(str(texto).strip())
    if not texto_configurado or tiene_consentimiento(wa_id) is True:
        payload["telefono"] = wa_id
        if texto_configurado:
            payload["consentimiento"] = "otorgado"
    else:
        payload["telefono"] = ""
        payload["consentimiento"] = "pendiente"
    payload["origen"] = "wsp_intouch"
    payload["clave_contacto"] = clave_contacto(lead)
    payload["evento_id"] = lead.evento_id
    payload["revision"] = lead.revision
    return payload


# Los estados de respuesta que el receptor puede devolver y que significan
# "el lead está en el CRM". Explícito y no "cualquier 2xx": un 2xx con un
# cuerpo incompleto es un fallo de contrato, no un éxito.
_ESTADOS_DE_EXITO = frozenset({"created", "replayed", "updated"})


# Los tres resultados posibles de `_enviar_al_sink`. Explícito y no un booleano
# ni un `None`: la lección de `_tri_estado` más arriba en este mismo archivo
# aplica también acá -- un valor de dos estados no puede representar tres sin
# perder uno, y "fallo" y "conflicto" necesitan tratos opuestos (uno se
# reintenta solo, el otro nunca sin que una persona intervenga).
_RESULTADOS_ENVIO = frozenset({"ok", "conflicto", "fallo"})


def _motivo_del_conflicto(crudo: bytes, tipo: str) -> str:
    """El motivo del 409 que informó el receptor, o uno genérico.

    Best-effort a propósito: el 409 ya se clasificó por el código HTTP antes
    de llegar acá, así que esto no puede reventar ni cambiar la clasificación
    si el cuerpo no trae el detalle -- sólo enriquece el texto que ve la
    persona que va a resolver el conflicto en el CRM.
    """
    import json

    if "json" in tipo:
        try:
            cuerpo = json.loads(crudo)
        except (ValueError, TypeError):
            cuerpo = None
        if isinstance(cuerpo, dict):
            motivo = cuerpo.get("motivo") or cuerpo.get("message") or cuerpo.get("error")
            if motivo:
                return str(motivo)[:500]
    return "el receptor respondió 409 sin detalle del motivo"


def _enviar_al_sink(payload: dict) -> dict:
    """POST al receptor. Clasifica la respuesta en tres resultados posibles.

    ANTES DEVOLVÍA `dict | None`, y un `None` era indistinguible entre un
    timeout, un 500, un cuerpo con HTML de login y un 409 de conflicto de
    identidad. El 409 es distinto de todo lo demás: no se resuelve
    reintentando -- el mismo teléfono en dos contactos, o teléfono y correo
    que apuntan a personas distintas, necesita que una persona decida del
    lado del CRM. Tratarlo como un fallo de red más lo dejaba reintentándose
    cada 15 minutos para siempre, con el receptor respondiendo 409 cada vez.

    Se clasifica por el CÓDIGO HTTP y no por el cuerpo de la respuesta: el
    status es transporte. Si el receptor mañana renombra un valor de su JSON
    editorial, la clasificación de transporte no se entera y no se rompe.

    El resultado es un dict con clave `resultado` en _RESULTADOS_ENVIO --
    ninguno de los tres es un sentinel fácil de confundir con otro:

    - "ok": el receptor aceptó el lead (2xx, JSON parseable, `status` conocido,
      `contactId` presente). Trae además `cuerpo`, el JSON completo.
    - "conflicto": 409. Trae además `motivo`, el texto del receptor si vino en
      el cuerpo, o uno genérico si no.
    - "fallo": todo lo demás -- otros 4xx, 5xx, timeout, HTML de login,
      redirect, JSON inválido, contrato incompleto. Se reintenta solo. Trae
      además `motivo`, para el log y para quien depure.

    stdlib `urllib` y no `requests`, que no está en requirements -- mismo
    criterio que bot/notify.py y utils/dios_registration.py.
    """
    import json
    import urllib.error
    import urllib.request

    destino = getattr(settings, "LEAD_SINK_URL", "")
    if not destino:
        logger.warning("[lead] LEAD_SINK=http pero LEAD_SINK_URL está vacío")
        return {"resultado": "fallo", "motivo": "LEAD_SINK_URL está vacío"}

    cabeceras = {"Content-Type": "application/json"}
    token = getattr(settings, "LEAD_SINK_TOKEN", "")
    if token:
        # NO es una API key del framework de autenticación del CRM -- esas
        # equivalen a su usuario dueño (leen y crean contactos, empresas y
        # oportunidades). Este es un secreto de ingesta dedicado, validado
        # por un guard que corre sólo en esta ruta. No "corregir" a x-api-key.
        cabeceras["x-intouch-ingest-key"] = token
    else:
        logger.warning("[lead] LEAD_SINK_TOKEN está vacío: el receptor va a rechazar")

    req = urllib.request.Request(
        destino, data=json.dumps(payload).encode("utf-8"),
        headers=cabeceras, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            crudo = resp.read()
            status_http = resp.status
            tipo = (resp.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as error:
        # 4xx y 5xx llegan acá. El 409 se separa del resto: es el único que no
        # se reintenta solo.
        if error.code == 409:
            try:
                crudo_error = error.read()
            except Exception:
                crudo_error = b""
            tipo_error = (error.headers.get("Content-Type") or "").lower() if error.headers else ""
            motivo = _motivo_del_conflicto(crudo_error, tipo_error)
            logger.warning("[lead] el receptor marcó un conflicto de identidad: %s", motivo)
            return {"resultado": "conflicto", "motivo": motivo}
        # El motivo se loguea, el cuerpo del error no: puede traer detalle
        # interno del receptor.
        logger.warning("[lead] el receptor respondió %s", error.code)
        return {"resultado": "fallo", "motivo": f"el receptor respondió {error.code}"}

    if not (200 <= status_http < 300):
        logger.warning("[lead] el receptor respondió %s", status_http)
        return {"resultado": "fallo", "motivo": f"el receptor respondió {status_http}"}

    if "json" not in tipo:
        # El síntoma de un redirect a login: 200 con HTML.
        logger.warning("[lead] el receptor respondió %s en vez de JSON", tipo or "sin tipo")
        return {"resultado": "fallo",
                "motivo": f"el receptor respondió {tipo or 'sin tipo'} en vez de JSON"}

    try:
        cuerpo = json.loads(crudo)
    except (ValueError, TypeError):
        logger.warning("[lead] el receptor respondió un JSON que no se puede leer")
        return {"resultado": "fallo", "motivo": "el receptor respondió un JSON que no se puede leer"}

    if not isinstance(cuerpo, dict):
        logger.warning("[lead] el receptor respondió algo que no es un objeto")
        return {"resultado": "fallo", "motivo": "el receptor respondió algo que no es un objeto"}

    if cuerpo.get("status") not in _ESTADOS_DE_EXITO:
        logger.warning("[lead] el receptor respondió status=%r", cuerpo.get("status"))
        return {"resultado": "fallo", "motivo": f"el receptor respondió status={cuerpo.get('status')!r}"}

    if not cuerpo.get("contactId"):
        # `dealId` vacío SÍ es válido (un lead frío no abre oportunidad), pero
        # sin contacto no hay nada en el CRM que mirar.
        logger.warning("[lead] el receptor no devolvió contactId")
        return {"resultado": "fallo", "motivo": "el receptor no devolvió contactId"}

    return {"resultado": "ok", "cuerpo": cuerpo}


def _despachar_si_corresponde(wa_id: str) -> None:
    """Manda el lead al destino externo, si hay uno configurado y falta.

    Un fallo NO sella `despachado_en`: un lead sin despachar tiene que quedar
    visible y reintentable. Y nunca propaga: la fuente de verdad ya está
    escrita, y el despacho es un espejo.

    Un conflicto (409) tampoco se reintenta acá: necesita que una persona lo
    resuelva del lado del CRM, y el bot no tiene forma de enterarse solo de
    que eso pasó. La única vía deliberada de vuelta al circuito es el
    reintento manual del panel (`admin_panel.views.api_lead_reintentar`).
    """
    sink = getattr(settings, "LEAD_SINK", "none")
    if sink == "none":
        return
    if sink not in SINKS_VALIDOS:
        logger.warning(
            "[lead] LEAD_SINK=%r no es un destino conocido (%s): el lead no se despacha",
            sink, ", ".join(sorted(SINKS_VALIDOS)))
        return
    lead = LeadInTouch.objects.filter(conversation__wa_id=wa_id).first()
    if lead is None:
        return
    try:
        hay_evento_nuevo = marcar_evento(lead)
        # Sin contenido nuevo Y (ya sellado O en conflicto), no hay nada que
        # hacer automáticamente: sellado es un reintento del mismo hecho, y en
        # conflicto es algo que sólo una persona destraba. Pero si ninguno de
        # los dos sellos está puesto (el intento anterior falló de red, o
        # nunca hubo uno) hay que reintentar aunque el contenido sea el mismo.
        if not hay_evento_nuevo and (lead.despachado_en is not None
                                     or lead.conflicto_en is not None):
            return
        resultado = _enviar_al_sink(payload_del_lead(lead))
        if resultado["resultado"] == "ok":
            cuerpo = resultado["cuerpo"]
            lead.despachado_en = timezone.now()
            lead.crm_contact_id = cuerpo.get("contactId") or ""
            lead.crm_deal_id = cuerpo.get("dealId") or ""
            lead.save(update_fields=["despachado_en", "crm_contact_id", "crm_deal_id"])
        elif resultado["resultado"] == "conflicto":
            lead.conflicto_en = timezone.now()
            lead.conflicto_motivo = resultado["motivo"]
            lead.save(update_fields=["conflicto_en", "conflicto_motivo"])
            logger.warning("[lead] conflicto de identidad despachando %s: %s",
                           wa_id, resultado["motivo"])
        else:
            logger.warning("[lead] el destino externo no aceptó el lead de %s", wa_id)
    except Exception:
        logger.warning("[lead] no pude despachar el lead de %s", wa_id, exc_info=True)
