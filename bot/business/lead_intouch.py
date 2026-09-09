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

from bot.models import Conversation, LeadInTouch, SenalesLead, calcular_score_intouch

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

    Es el punto de entrada que usa la cola de envío. Tres reglas:

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


def clave_idempotencia(lead) -> str:
    """Clave estable por conversación, para que el receptor pueda deduplicar.

    Es el punto 5 de la sección "Ajustes necesarios" del prompt de origen: la
    instrucción al modelo no alcanza frente a reentregas de WhatsApp ni a
    fallos de red. La clave es estable porque hay UN lead por conversación
    (OneToOne), así que dos despachos del mismo lead llevan la misma clave y el
    receptor sabe que son el mismo hecho.

    Va hasheada: viaja a otro sistema y no tiene por qué llevar el teléfono en
    claro cuando un hash cumple la misma función.
    """
    import hashlib

    crudo = f"wsp_intouch:{lead.conversation.wa_id}"
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:32]


# Los campos del contrato que viajan al destino externo. Explícito y no
# `__dict__`: así un campo interno nuevo (un flag de proceso, una marca de
# tiempo) no se filtra al payload sin que nadie lo decida.
_CAMPOS_DEL_PAYLOAD = (
    "nombre_completo", "correo", "empresa", "industria", "subtipo_automotriz",
    "cargo", "pais_ciudad", "situacion_contact_center", "tipo_contact_center",
    "usa_ia_actualmente", "canales_actuales", "volumen_interacciones",
    "necesidad_principal", "soluciones_interes", "intencion", "plazo_proyecto",
    "lead_score", "solicita_consultoria", "solicita_contacto_humano",
    "resumen_conversacion", "siguiente_accion_recomendada",
)


def payload_del_lead(lead) -> dict:
    """El lead como lo espera el endpoint del spec B."""
    payload = {campo: getattr(lead, campo) for campo in _CAMPOS_DEL_PAYLOAD}
    # El teléfono lo agrega la PLATAFORMA desde los metadatos de WhatsApp, no
    # el modelo: el prompt le prohíbe pedirlo, pero el equipo comercial
    # necesita a quién llamar.
    payload["telefono"] = lead.conversation.wa_id
    payload["origen"] = "wsp_intouch"
    payload["clave_idempotencia"] = clave_idempotencia(lead)
    return payload


def _enviar_al_sink(payload: dict) -> bool:
    """POST al endpoint configurado. True si el receptor lo aceptó.

    stdlib `urllib` y no `requests`, que no está en requirements -- mismo
    criterio que bot/notify.py y utils/dios_registration.py.
    """
    import json
    import urllib.request

    destino = getattr(settings, "LEAD_SINK_URL", "")
    if not destino:
        logger.warning("[lead] LEAD_SINK=http pero LEAD_SINK_URL está vacío")
        return False
    req = urllib.request.Request(
        destino, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        # Se verifica el CÓDIGO, no que el POST se haya completado. Un
        # despachador que devuelve éxito para un HTTP de error convierte un
        # fallo en "no había datos" -- la falla de §IV.1 que este stack ya pagó.
        return 200 <= resp.status < 300


def _despachar_si_corresponde(wa_id: str) -> None:
    """Manda el lead al destino externo, si hay uno configurado y falta.

    Un fallo NO sella `despachado_en`: un lead sin despachar tiene que quedar
    visible y reintentable. Y nunca propaga: la fuente de verdad ya está
    escrita, y el despacho es un espejo.
    """
    sink = getattr(settings, "LEAD_SINK", "none")
    if sink == "none":
        return
    if sink not in SINKS_VALIDOS:
        logger.warning(
            "[lead] LEAD_SINK=%r no es un destino conocido (%s): el lead no se despacha",
            sink, ", ".join(sorted(SINKS_VALIDOS)))
        return
    lead = LeadInTouch.objects.filter(
        conversation__wa_id=wa_id, despachado_en__isnull=True).first()
    if lead is None:
        return
    try:
        if _enviar_al_sink(payload_del_lead(lead)):
            lead.despachado_en = timezone.now()
            lead.save(update_fields=["despachado_en"])
        else:
            logger.warning("[lead] el destino externo rechazó el lead de %s", wa_id)
    except Exception:
        logger.warning("[lead] no pude despachar el lead de %s", wa_id, exc_info=True)
