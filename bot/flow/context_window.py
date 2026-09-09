# bot/flow/context_window.py
from datetime import datetime, timedelta

from django.utils import timezone

from bot.models import CampaignSend, Conversation

SESSION_GAP = timedelta(hours=12)
MAX_TURNS = 20

# Prefijo del mensaje "marcador" que handlers.py guarda cuando manda la foto
# de un modelo (ver marcador_imagen_enviada) -- es contabilidad interna para
# no reenviar la misma imagen. Ya NO se excluye del historial (docs/PENDIENTES.md,
# "Afirma haber compartido una foto que nunca compartio"): sin ninguna senal de
# que la foto se mando, el LLM no tenia con que contrastar cuando afirmaba (o
# negaba) haberla enviado. build_context_window lo traduce a una linea en
# prosa (_texto_para_llm) en vez de mostrar el marcador crudo.
_PREFIJO_MARCADOR_IMAGEN = "[imagen_enviada:"
# Mismo patron que _PREFIJO_MARCADOR_IMAGEN, para la ficha tecnica (PDF)
# mandada por WhatsApp -- ver marcador_ficha_enviada.
_PREFIJO_MARCADOR_FICHA = "[ficha_enviada:"

_MARCADOR_HISTORIAL_TRUNCADO = {
    "role": "system",
    "content": (
        "(el historial de esta conversacion sigue mas atras de lo que se "
        "muestra aqui -- si el cliente menciona algo que no ves arriba, no "
        "asumas que nunca paso ni lo niegues; pedile que te lo recuerde o "
        "confirmalo antes de responder)"
    ),
}


def marcador_imagen_enviada(slug_modelo: str) -> str:
    return f"{_PREFIJO_MARCADOR_IMAGEN}{slug_modelo}]"


def marcador_ficha_enviada(modelo: str) -> str:
    return f"{_PREFIJO_MARCADOR_FICHA}{modelo}]"


def _texto_para_llm(content: str) -> str:
    """Traduce un marcador interno (imagen/ficha ya enviada) a una linea en
    prosa que el LLM puede leer con naturalidad, sin exponerle el formato
    interno del marcador."""
    if content.startswith(_PREFIJO_MARCADOR_IMAGEN):
        slug = content[len(_PREFIJO_MARCADOR_IMAGEN):-1]
        return f"[ya te compartí una foto de {slug} en un mensaje anterior]"
    if content.startswith(_PREFIJO_MARCADOR_FICHA):
        modelo = content[len(_PREFIJO_MARCADOR_FICHA):-1]
        return f"[ya te compartí la ficha técnica de {modelo} en un mensaje anterior]"
    return content


def resolve_campaign_hint(wa_id: str) -> str | None:
    send = (
        CampaignSend.objects
        .filter(contacto=wa_id, respondido=False)
        .order_by("-enviado_at")
        .first()
    )
    if send is None:
        return None
    if timezone.now() - send.enviado_at > SESSION_GAP:
        return None
    return send.campaign_type


def limite_sesion_actual(conversation: Conversation) -> datetime | None:
    """Timestamp desde el que arranca la sesion activa mas reciente de esta
    conversacion: el ultimo CampaignSend del contacto, o un gap de
    inactividad > SESSION_GAP entre dos mensajes consecutivos — lo que
    ocurra primero al recorrer hacia atras. Recorre TODA la conversacion
    (sin tope de MAX_TURNS) porque a diferencia de build_context_window esto
    no arma el historial que ve el LLM, solo ubica el borde de sesion --
    usado por bot.whatsapp.handlers._imagen_enviada_recientemente para no
    reenviar la misma foto de modelo dentro de la sesion activa, sin
    depender de un conteo fijo de mensajes (hallazgo real: una sesion con
    mas de 20 mensajes triviales entre medio hacia que el marcador saliera
    de la ventana y la imagen se reenviara igual, ver docs/PENDIENTES.md).
    Devuelve None si no hay ningun mensaje."""
    last_send = (
        CampaignSend.objects
        .filter(contacto=conversation.wa_id)
        .order_by("-enviado_at")
        .first()
    )
    campaign_boundary = last_send.enviado_at if last_send else None

    limite = None
    prev_created_at = None
    # values_list y NO .only("created_at"): ese `.only()` causaba un N+1 real,
    # medido el 2026-09-03 -- una query por FILA en vez de una sola.
    #
    # La causa raiz, con stack capturado: el queryset sale del RelatedManager
    # `conversation.messages`, asi que Django lee la FK `conversation_id` de
    # cada instancia para poblar `_known_related_objects`... y `.only()` DIFIERE
    # justo esa FK, disparando un refresh_from_db por fila. No es cosa de
    # mssql-django.
    #
    # Medido: 6 mensajes -> 8 queries / 20,8ms; 13 -> 15 / 30,7ms; 33 -> 35 /
    # 71,4ms, o sea 1,87ms por mensaje de la sesion y SIN TECHO (una
    # conversacion de 300 mensajes son 571ms por llamada). Con values_list: UNA
    # query, 4,06ms.
    #
    # Importa porque esto corre en el tramo previo al grafo de CADA turno (ver
    # bot/whatsapp/handlers.py::_acuses_triviales_consecutivos) y ademas en
    # _imagen_enviada_recientemente_sync y bot/business/ventas.py.
    #
    # Solo se necesitan los timestamps, no los objetos Message: values_list
    # los trae directo y de paso evita instanciar 300 modelos para leerles un
    # campo. build_context_window (mas abajo) tiene su propio loop y NO sufre
    # esto, porque usa los mensajes completos de verdad.
    for created_at in (
        conversation.messages.order_by("-created_at").values_list("created_at", flat=True).iterator()
    ):
        if campaign_boundary and created_at < campaign_boundary:
            break
        if prev_created_at and (prev_created_at - created_at) > SESSION_GAP:
            break
        limite = created_at
        prev_created_at = created_at
    return limite


def build_context_window(conversation: Conversation) -> list[dict]:
    """Ventana de mensajes recientes acotada por el borde de sesion mas
    cercano a ahora: el ultimo CampaignSend del contacto, o un gap de
    inactividad > 24h entre dos mensajes consecutivos — lo que ocurra
    primero al recorrer hacia atras."""
    last_send = (
        CampaignSend.objects
        .filter(contacto=conversation.wa_id)
        .order_by("-enviado_at")
        .first()
    )
    campaign_boundary = last_send.enviado_at if last_send else None

    collected = []
    prev_created_at = None
    truncado_por_limite = False
    for msg in conversation.messages.order_by("-created_at").iterator():
        if campaign_boundary and msg.created_at < campaign_boundary:
            break
        if prev_created_at and (prev_created_at - msg.created_at) > SESSION_GAP:
            break
        if len(collected) >= MAX_TURNS:
            # Hay al menos un mensaje mas dentro de la misma sesion que no
            # entra en la ventana -- a diferencia de un corte de sesion
            # (arriba), esto es un corte arbitrario a mitad de una
            # conversacion en curso, asi que el LLM necesita la senal
            # explicita de _MARCADOR_HISTORIAL_TRUNCADO.
            truncado_por_limite = True
            break
        collected.append(msg)
        prev_created_at = msg.created_at

    collected.reverse()
    ventana = [{"role": m.role, "content": _texto_para_llm(m.content)} for m in collected]
    if truncado_por_limite:
        ventana.insert(0, _MARCADOR_HISTORIAL_TRUNCADO)
    return ventana
