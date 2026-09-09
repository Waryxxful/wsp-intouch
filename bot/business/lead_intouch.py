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

from bot.models import Conversation, LeadInTouch, SenalesLead, calcular_lead_score

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
            setattr(lead, campo, bool(valor))
        elif campo in _CAMPOS_LISTA:
            setattr(lead, campo, list(valor) if isinstance(valor, (list, tuple)) else [])
        else:
            setattr(lead, campo, _recortar(lead, campo, valor))

    # El score se recalcula SÓLO si este turno trajo señales. Un turno de
    # cortesía sin señales no puede degradar un lead que ya calificó.
    convertidas = _senales_desde(senales)
    if convertidas is not None:
        lead.lead_score = calcular_lead_score(convertidas)

    lead.save()

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
        _despachar_si_corresponde(wa_id)
    except Exception:
        logger.error("[lead] no pude registrar el lead de %s", wa_id, exc_info=True)


def _despachar_si_corresponde(wa_id: str) -> None:
    """Notifica el lead HOT y lo manda al destino externo, si hay uno.

    Se implementa en las Tasks 16 y 17. Acá queda el punto de llamada para que
    `registrar_lead_del_turno` no tenga que cambiar después.
    """
    return
