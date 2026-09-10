import csv
import importlib
import json
import logging
import statistics
import threading
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Count, ProtectedError, Q
from django.db.models.functions import TruncDate
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from admin_panel import proyeccion
from .auth_helpers import grancrm_login_required as login_required
from .cliente_flip import actualizar_env_docker, escribir_flip_pendiente, leer_flip_pendiente
from .models import AuditLog, BusinessHours, QuickResponse, Snippet, Filter as WaFilter, HandoffConfig
from bot.models import (
    CampaignSend, CLIENTE_CHOICES, Conversation, CustomSpecialist, Incident, Message, PromptVersion, Reserva,
    ScrapingSource, Setting, deactivate_prompt, esta_optout, get_active_prompt, save_prompt_version,
)
from bot.flow.agents import RESERVED_SLUGS
from bot.scraping import runner
from bot.simulator.models import CorridaDePrueba, EscenarioDePrueba, marcar_corridas_stale_como_interrumpidas
from bot.simulator.runner import ejecutar_corrida, iniciar_corrida
from utils.tenant_middleware import get_current_db, set_current_db

logger = logging.getLogger(__name__)


def get_setting(key: str, default: str = "") -> str:
    try:
        return Setting.objects.get(key=key).value
    except Setting.DoesNotExist:
        return default


def set_setting(key: str, value: str) -> None:
    Setting.objects.update_or_create(key=key, defaults={"value": str(value)})


def json_body(request) -> dict:
    try:
        return json.loads(request.body)
    except Exception:
        return {}


def _actor(request) -> str:
    jwt_payload = getattr(request, "jwt_payload", None)
    if jwt_payload:
        return jwt_payload.get("email") or jwt_payload.get("nombre") or "jwt-user"
    return str(request.user)


def audit(request, action: str, target: str = "", details: str = "") -> None:
    AuditLog.objects.create(
        user=_actor(request), action=action, target=str(target), details=str(details),
        ip=request.META.get("REMOTE_ADDR") or None,
    )


def _has_role(request, *allowed_roles) -> bool:
    """Autorizacion real: en prod el rol viene del JWT de DIOS (rol_real,
    valores agente/supervisor/admin_cuenta/admin_ti); en DEBUG sin
    orquestador se acepta is_staff (no hay modelo de rol local en esta app)."""
    payload = getattr(request, "jwt_payload", None)
    if payload:
        return payload.get("rol_real") in allowed_roles
    return bool(settings.DEBUG and request.user.is_authenticated and request.user.is_staff)


def _forbidden() -> JsonResponse:
    return JsonResponse({"error": "forbidden"}, status=403)


def mask_wa_id(wa_id: str) -> str:
    if not wa_id or len(wa_id) <= 4:
        return wa_id or ""
    return "•••" + wa_id[-4:]


# Dias sin actividad para considerar una conversacion "cerrada" cuando nadie
# la archivo a mano. wsp_demo no tiene flow_state terminal (no es un bot de
# agendamiento como pompeyo) -- la inactividad es la unica señal disponible.
INACTIVE_DAYS = 7


@login_required
def api_connection_status(request):
    from bot.whatsapp.client import get_wa_client
    wa = get_wa_client()
    return JsonResponse({"connected": bool(wa.token and wa.phone_id), "phone_id": mask_wa_id(wa.phone_id)})


@login_required
def api_conversations(request):
    """Lista de conversaciones del panel.

    Filtros opcionales: ?estado=abiertas|cerradas|todas y ?desde=/?hasta=
    (YYYY-MM-DD sobre updated_at). Se respetan tambien en modo proyeccion,
    con la misma definicion de "cerrada": un selector que no filtra nada
    delante de un cliente es peor que no tener selector.
    """
    estado = request.GET.get("estado", "abiertas")
    # Filtro por rango de fechas (updated_at). Fecha invalida se ignora
    # silenciosamente -- el frontend garantiza el formato YYYY-MM-DD.
    desde_raw = request.GET.get("desde", "").strip()
    hasta_raw = request.GET.get("hasta", "").strip()

    if request.GET.get("modo") == "demo":
        filas = proyeccion.conversaciones()["items"]
        limite = timezone.now() - timedelta(days=INACTIVE_DAYS)

        def _cerrada(fila):
            return fila["archived"] or _datetime.fromisoformat(fila["updated_at"]) < limite

        if estado == "abiertas":
            filas = [f for f in filas if not _cerrada(f)]
        elif estado == "cerradas":
            filas = [f for f in filas if _cerrada(f)]

        def _dia(fila):
            return _datetime.fromisoformat(fila["updated_at"]).date()

        if desde_raw:
            try:
                desde = _date.fromisoformat(desde_raw)
                filas = [f for f in filas if _dia(f) >= desde]
            except ValueError:
                pass
        if hasta_raw:
            try:
                hasta = _date.fromisoformat(hasta_raw)
                filas = [f for f in filas if _dia(f) <= hasta]
            except ValueError:
                pass
        return JsonResponse({"items": filas, "count": len(filas), "es_proyeccion": True})

    closed = Q(archived=True) | Q(updated_at__lt=timezone.now() - timedelta(days=INACTIVE_DAYS))
    qs = Conversation.objects.all()
    if estado == "abiertas":
        qs = qs.exclude(closed)
    elif estado == "cerradas":
        qs = qs.filter(closed)
    # "todas" -> sin filtro

    if desde_raw:
        try:
            qs = qs.filter(updated_at__date__gte=_date.fromisoformat(desde_raw))
        except ValueError:
            pass
    if hasta_raw:
        try:
            qs = qs.filter(updated_at__date__lte=_date.fromisoformat(hasta_raw))
        except ValueError:
            pass

    count = qs.count()
    convs = list(qs[:50])
    ids = [c.pk for c in convs]
    # Bulk-count de incidents abiertos para no hacer N+1.
    open_by_conv = {}
    for row in Incident.objects.filter(
        conversation_id__in=ids, status="abierto"
    ).values("conversation_id"):
        cid = row["conversation_id"]
        open_by_conv[cid] = open_by_conv.get(cid, 0) + 1

    # Intencion, resumen IA y lead score: el docx S18 los pide en la tabla de
    # conversaciones. Salen de LeadComercial y no de Conversation -- "intent"
    # lo produce el LLM en cada turno pero nunca se persistio en Conversation,
    # y lead_class tiene el problema de los dos escritores (ver
    # bot/business/prospeccion.py). Bulk lookup por el mismo motivo que los
    # incidents: 50 filas no pueden costar 50 queries.
    from bot.models import LeadComercial
    leads_por_conv = {
        l.conversation_id: l
        for l in LeadComercial.objects.filter(conversation_id__in=ids)
    }

    def _fila(c):
        lead = leads_por_conv.get(c.pk)
        return {
            "id": c.pk, "wa_id": mask_wa_id(c.wa_id), "name": c.name,
            "active_agent": c.active_agent, "updated_at": c.updated_at.isoformat(),
            "human_mode": (c.get_flow() or {}).get("modo") == "HUMAN",
            "archived": c.archived,
            "open_incidents": open_by_conv.get(c.pk, 0),
            "stage": c.stage,
            "intencion": lead.intencion if lead else "",
            "resumen": lead.resumen if lead else "",
            "lead_score": lead.lead_score if lead else None,
            "temperatura": lead.temperatura if lead else "",
        }

    return JsonResponse({"count": count, "items": [_fila(c) for c in convs]})


@login_required
def api_conversation_detail(request, pk):
    if not _has_role(request, "supervisor", "admin_cuenta", "admin_ti"):
        return _forbidden()
    conv = get_object_or_404(Conversation, pk=pk)
    audit(request, "wa_id_reveal", conv.pk)
    return JsonResponse({
        "id": conv.pk, "wa_id": conv.wa_id, "name": conv.name,
        "active_agent": conv.active_agent, "updated_at": conv.updated_at.isoformat(),
        "human_mode": (conv.get_flow() or {}).get("modo") == "HUMAN",
        "archived": conv.archived,
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
@login_required
def api_conversation_mode(request, pk):
    conv = get_object_or_404(Conversation, pk=pk)
    flow = conv.get_flow() or {}
    if request.method == "POST":
        body = json_body(request)
        new_mode = "HUMAN" if body.get("human_mode") else None
        if new_mode:
            flow["modo"] = "HUMAN"
        else:
            flow.pop("modo", None)
        conv.set_flow(flow)
        conv.save(update_fields=["flow_data", "updated_at"])
        audit(request, "conversation_mode", conv.wa_id, new_mode or "BOT")
        return JsonResponse({"ok": True, "human_mode": new_mode == "HUMAN"})
    return JsonResponse({"human_mode": flow.get("modo") == "HUMAN"})


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def api_conversation_archive(request, pk):
    conv = get_object_or_404(Conversation, pk=pk)
    body = json_body(request)
    conv.archived = bool(body["archived"]) if "archived" in body else not conv.archived
    conv.save(update_fields=["archived", "updated_at"])
    audit(request, "conversation_archive", conv.wa_id, str(conv.archived))
    return JsonResponse({"ok": True, "archived": conv.archived})


MESSAGES_PAGE_SIZE = 50

_EXTENSIONES_IMAGEN = {"jpg", "jpeg", "png", "webp"}


def _media_url_y_tipo(m: Message) -> tuple[str | None, str | None]:
    if not m.media_url:
        return None, None
    extension = m.media_url.rsplit(".", 1)[-1].lower()
    tipo = "image" if extension in _EXTENSIONES_IMAGEN else "audio"
    return f"/intouch/api/media/{m.pk}", tipo


@login_required
def api_messages(request, pk):
    # `pk` llega como texto y no como int: la ruta usa una expresion regular
    # que acepta ids negativos, porque el converter <int:...> de Django solo
    # matchea [0-9]+ y dejaria las conversaciones proyectadas en 404.
    pk = int(pk)
    if request.GET.get("modo") == "demo":
        return JsonResponse(proyeccion.mensajes(pk))

    qs = Message.objects.filter(conversation_id=pk).order_by("-id")
    before_id = request.GET.get("before_id")
    if before_id:
        try:
            qs = qs.filter(id__lt=int(before_id))
        except ValueError:
            pass
    msgs = list(qs[:MESSAGES_PAGE_SIZE + 1])
    has_more = len(msgs) > MESSAGES_PAGE_SIZE
    msgs = msgs[:MESSAGES_PAGE_SIZE]
    msgs.reverse()
    items = []
    for m in msgs:
        media_url, media_type = _media_url_y_tipo(m)
        items.append({
            "id": m.pk, "role": m.role, "content": m.content,
            "created_at": m.created_at.isoformat(),
            "media_url": media_url, "media_type": media_type,
        })
    return JsonResponse({"items": items, "has_more": has_more})


@login_required
def api_media_file(request, pk):
    # m.media_url siempre es un nombre generado por el propio backend (uuid +
    # extension fija, ver bot.whatsapp.media_storage.guardar_media_privado)
    # -- nunca input directo del cliente final, no hay path traversal que
    # validar mas alla de eso.
    m = get_object_or_404(Message, pk=pk)
    if not m.media_url:
        raise Http404
    ruta = Path(settings.WHATSAPP_MEDIA_ROOT) / m.media_url
    if not ruta.is_file():
        raise Http404
    return FileResponse(open(ruta, "rb"))


@login_required
def api_incidents(request, pk):
    # Antes de esto, Incident no tenia NINGUNA superficie en el panel mas
    # alla del contador "open_incidents" de api_conversations -- sin lista,
    # sin poder cerrarlos, se acumulaban para siempre (24 incidentes
    # abiertos desde el 2026-08-11 en una sola conversacion de prueba, ver
    # docs/PENDIENTES.md).
    conv = get_object_or_404(Conversation, pk=pk)
    incidents = conv.incidents.order_by("-created_at")[:200]
    return JsonResponse({"items": [{
        "id": i.pk, "kind": i.kind, "status": i.status, "context": i.context,
        "created_at": i.created_at.isoformat(),
    } for i in incidents]})


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def api_incident_status(request, pk):
    incident = get_object_or_404(Incident, pk=pk)
    validos = {c[0] for c in Incident.STATUS_CHOICES}
    nuevo_status = json_body(request).get("status")
    if nuevo_status not in validos:
        return JsonResponse({"error": f"status invalido, opciones: {sorted(validos)}"}, status=400)
    incident.status = nuevo_status
    incident.save(update_fields=["status"])
    audit(request, "incident_status", str(incident.pk), nuevo_status)
    return JsonResponse({"id": incident.pk, "status": incident.status})


@csrf_exempt
@login_required
def api_send_message(request):
    body = json_body(request)
    conv = get_object_or_404(Conversation, pk=body.get("conversation_id"))
    text = body.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "text required"}, status=400)
    import httpx
    from bot.whatsapp.client import get_wa_client
    wa = get_wa_client()
    try:
        ok = wa.send_text(conv.wa_id, text)
    except httpx.HTTPError:
        # WHATSAPP_TOKEN vacio (dev/test sin credenciales reales) hace que
        # httpx arme un header "Authorization: Bearer " invalido y reviente
        # con LocalProtocolError antes de cualquier request de red. Guardamos
        # el mensaje igual (el operador necesita ver lo que escribio) y
        # reportamos ok=False en vez de un 500.
        ok = False
    Message.objects.create(conversation=conv, role="human", content=text)
    return JsonResponse({"ok": ok})


AGENT_PROMPT_MODULES = {
    "global": "bot.flow.global_prompt",
    "agendamiento": "bot.flow.agents.agendamiento",
    "confirmacion": "bot.flow.agents.confirmacion",
    "faq": "bot.flow.agents.faq",
    "encuesta_servicio_tecnico": "bot.flow.agents.encuesta_servicio_tecnico",
    "encuesta_venta_auto_nuevo": "bot.flow.agents.encuesta_venta_auto_nuevo",
}


@csrf_exempt
@login_required
def api_bot_state(request):
    if request.method == "POST":
        body = json_body(request)
        set_setting("bot_global_on", "true" if body.get("active") else "false")
        audit(request, "bot_state", "toggle", str(body.get("active")))
        return JsonResponse({"ok": True})
    return JsonResponse({"active": get_setting("bot_global_on", "true") == "true"})


@csrf_exempt
@login_required
def api_cliente_activo(request):
    """Flip liviano de CLIENTE_ACTIVO/RAG_SCHEMA (ver docs/PENDIENTES.md,
    auditoria multi-cliente): esta view solo edita .env.docker y deja un
    pedido de restart -- el contenedor NUNCA toca Docker directamente (no
    tiene ni necesita el socket), un script del host (cron) hace el
    `docker compose up -d --force-recreate` real. El restart tarda hasta
    ~1 minuto en aplicarse (el cron corre cada minuto)."""
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    if request.method == "POST":
        target = (json_body(request).get("target") or "").strip()
        validos = [clave for clave, _ in CLIENTE_CHOICES]
        if target not in validos:
            return JsonResponse({"error": f"cliente invalido, valores validos: {validos}"}, status=400)
        if target == settings.CLIENTE_ACTIVO:
            return JsonResponse({"error": "ese cliente ya esta activo"}, status=400)
        if not PromptVersion.todos_los_clientes.filter(cliente=target, agente="global", activa=True).exists():
            return JsonResponse(
                {"error": "el cliente destino no tiene un prompt global activo -- no se puede flippear a un bot sin identidad configurada"},
                status=400,
            )
        actualizar_env_docker(settings.ENV_DOCKER_PATH, {"CLIENTE_ACTIVO": target, "RAG_SCHEMA": target})
        escribir_flip_pendiente(settings.FLIP_REQUEST_PATH, target, _actor(request))
        audit(request, "cliente_activo", "flip_request", target)
        return JsonResponse({"ok": True, "target": target})
    return JsonResponse({
        "cliente_activo": settings.CLIENTE_ACTIVO,
        "opciones": CLIENTE_CHOICES,
        "flip_pendiente": leer_flip_pendiente(settings.FLIP_REQUEST_PATH),
    })


@csrf_exempt
@login_required
def api_prompt(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    agente = request.GET.get("agente") or json_body(request).get("agente") or "agendamiento"
    if agente not in AGENT_PROMPT_MODULES:
        return JsonResponse({"error": "agente invalido"}, status=400)
    if request.method == "POST":
        body = json_body(request)
        if body.get("action") == "reset":
            deactivate_prompt(agente)
            audit(request, "prompt", f"{agente}:reset")
        else:
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                return JsonResponse({"error": "prompt requerido"}, status=400)
            save_prompt_version(agente, prompt)
            audit(request, "prompt", f"{agente}:save")
        return JsonResponse({"ok": True})
    current = get_active_prompt(agente)
    is_default = not current
    if is_default:
        current = getattr(importlib.import_module(AGENT_PROMPT_MODULES[agente]), "SYSTEM_PROMPT")
    return JsonResponse({
        "agente": agente, "prompt": current, "is_default": is_default,
        "agentes": list(AGENT_PROMPT_MODULES.keys()),
    })


@csrf_exempt
@login_required
def api_prompt_versions(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    agente = request.GET.get("agente", "")
    if not agente:
        return JsonResponse({"error": "agente requerido"}, status=400)
    versions = PromptVersion.objects.filter(agente=agente).order_by("-created_at")
    return JsonResponse([
        {"id": v.pk, "created_at": v.created_at.isoformat(), "activa": v.activa}
        for v in versions
    ], safe=False)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def api_prompt_version_restore(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    body = json_body(request)
    agente = body.get("agente", "")
    version = get_object_or_404(PromptVersion, pk=body.get("version_id"))
    if version.agente != agente:
        return JsonResponse({"error": "la version no pertenece a ese agente"}, status=400)
    PromptVersion.objects.filter(agente=agente, activa=True).update(activa=False)
    version.activa = True
    version.save(update_fields=["activa"])
    audit(request, "prompt", f"{agente}:restore:{version.pk}")
    return JsonResponse({"ok": True})


@csrf_exempt
@login_required
def api_specialists(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    if request.method == "POST":
        body = json_body(request)
        label = (body.get("label") or "").strip()
        descripcion = (body.get("descripcion") or "").strip()
        prompt = (body.get("prompt") or "").strip()
        if not label or not descripcion or not prompt:
            return JsonResponse({"error": "label, descripcion y prompt son obligatorios"}, status=400)
        slug = slugify(label)[:50]
        if not slug or slug in RESERVED_SLUGS or CustomSpecialist.objects.filter(slug=slug).exists():
            return JsonResponse(
                {"error": "ya existe un especialista con ese nombre, o el nombre esta reservado"}, status=400,
            )
        if PromptVersion.objects.filter(agente=f"custom:{slug}").exists():
            return JsonResponse(
                {"error": "ya existio un especialista con ese nombre antes; elegi otro nombre"}, status=400,
            )
        specialist = CustomSpecialist.objects.create(
            slug=slug, label=label, descripcion=descripcion, cliente=settings.CLIENTE_ACTIVO,
        )
        save_prompt_version(f"custom:{slug}", prompt)
        audit(request, "specialist", "create", specialist.slug)
        return JsonResponse({"id": specialist.pk, "slug": specialist.slug}, status=201)
    items = CustomSpecialist.objects.all()
    return JsonResponse([{
        "id": s.pk, "slug": s.slug, "label": s.label, "descripcion": s.descripcion,
        "prompt": get_active_prompt(f"custom:{s.slug}"),
    } for s in items], safe=False)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
@login_required
def api_specialist_detail(request, pk):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    specialist = get_object_or_404(CustomSpecialist, pk=pk)
    if request.method == "DELETE":
        specialist.delete()
        audit(request, "specialist", "delete", specialist.slug)
        return JsonResponse({"ok": True})
    body = json_body(request)
    descripcion = (body.get("descripcion") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    if not descripcion or not prompt:
        return JsonResponse({"error": "descripcion y prompt son obligatorios"}, status=400)
    specialist.descripcion = descripcion
    specialist.save(update_fields=["descripcion"])
    save_prompt_version(f"custom:{specialist.slug}", prompt)
    audit(request, "specialist", "update", specialist.slug)
    return JsonResponse({"ok": True})


def _serialize_escenario(e: EscenarioDePrueba) -> dict:
    return {
        "id": e.pk, "nombre": e.nombre, "persona": e.persona, "objetivo": e.objetivo,
        "criterios": e.criterios, "fuente": e.fuente, "max_turns": e.max_turns, "activo": e.activo,
    }


@csrf_exempt
@login_required
def api_test_scenarios(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    if request.method == "POST":
        body = json_body(request)
        nombre = (body.get("nombre") or "").strip()
        persona = (body.get("persona") or "").strip()
        objetivo = (body.get("objetivo") or "").strip()
        criterios = body.get("criterios") or []
        if not nombre or not persona or not objetivo or not isinstance(criterios, list) or not criterios:
            return JsonResponse(
                {"error": "nombre, persona, objetivo y al menos un criterio son obligatorios"}, status=400,
            )
        if EscenarioDePrueba.objects.filter(nombre=nombre).exists():
            return JsonResponse({"error": "ya existe un escenario con ese nombre"}, status=400)
        escenario = EscenarioDePrueba.objects.create(
            nombre=nombre, persona=persona, objetivo=objetivo, criterios=criterios,
            fuente=(body.get("fuente") or "").strip(), max_turns=int(body.get("max_turns") or 12),
        )
        audit(request, "test_scenario", "create", escenario.nombre)
        return JsonResponse(_serialize_escenario(escenario), status=201)
    items = EscenarioDePrueba.objects.all()
    return JsonResponse([_serialize_escenario(e) for e in items], safe=False)


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
@login_required
def api_test_scenario_detail(request, pk):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    escenario = get_object_or_404(EscenarioDePrueba, pk=pk)
    if request.method == "DELETE":
        try:
            escenario.delete()
        except ProtectedError:
            return JsonResponse(
                {"error": "este escenario ya tiene corridas registradas; desactivalo en vez de borrarlo"},
                status=400,
            )
        audit(request, "test_scenario", "delete", escenario.nombre)
        return JsonResponse({"ok": True})
    if request.method == "PUT":
        body = json_body(request)
        persona = (body.get("persona") or "").strip()
        objetivo = (body.get("objetivo") or "").strip()
        criterios = body.get("criterios") or []
        if not persona or not objetivo or not isinstance(criterios, list) or not criterios:
            return JsonResponse(
                {"error": "persona, objetivo y al menos un criterio son obligatorios"}, status=400,
            )
        escenario.persona = persona
        escenario.objetivo = objetivo
        escenario.criterios = criterios
        if "fuente" in body:
            escenario.fuente = (body.get("fuente") or "").strip()
        escenario.max_turns = int(body.get("max_turns") or escenario.max_turns)
        escenario.activo = bool(body.get("activo", escenario.activo))
        escenario.save()
        audit(request, "test_scenario", "update", escenario.nombre)
        return JsonResponse({"ok": True})
    return JsonResponse(_serialize_escenario(escenario))


def _ejecutar_corrida_en_background(db_name: str, corrida_id: int) -> None:
    set_current_db(db_name)
    corrida = CorridaDePrueba.objects.get(pk=corrida_id)
    ejecutar_corrida(corrida)


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def api_test_run_trigger(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    body = json_body(request)
    nombre_escenario = body.get("nombre_escenario") or None
    # Reclasifica corridas huerfanas (thread perdido por un reinicio del
    # servidor) antes de chequear el guard de abajo -- si no, una corrida
    # asi bloquearia nuevas corridas para siempre.
    marcar_corridas_stale_como_interrumpidas()
    if CorridaDePrueba.objects.filter(estado="corriendo").exists():
        # Hallazgo de la revision final: sin este guard, un doble click (o
        # dos operadores) puede lanzar dos ejecutar_corrida() en paralelo, y
        # el cleanup_test_conversations del finally de la primera en
        # terminar borra las conversaciones/leads de test de la que sigue
        # corriendo, corrompiendo su resultado.
        return JsonResponse({"error": "ya hay una corrida en curso, espera a que termine"}, status=409)
    try:
        corrida = iniciar_corrida(nombre_escenario=nombre_escenario, disparada_por=_actor(request))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    threading.Thread(
        target=_ejecutar_corrida_en_background, args=(get_current_db(), corrida.pk), daemon=True,
    ).start()
    audit(request, "test_run", "trigger", nombre_escenario or "todos")
    return JsonResponse({"id": corrida.pk, "estado": "corriendo"}, status=202)


def _serialize_corrida_resumen(c: CorridaDePrueba, resultados=None) -> dict:
    # `resultados` puede venir precalculado (api_test_run_detail ya hizo su
    # propio select_related("escenario") y no queremos repetir la query) --
    # si no, cae a c.resultados.all(), que golpea la cache de
    # prefetch_related cuando el caller (api_test_runs) la uso.
    if resultados is None:
        resultados = list(c.resultados.all())
    return {
        "id": c.pk, "fecha_inicio": c.fecha_inicio.isoformat(),
        "fecha_fin": c.fecha_fin.isoformat() if c.fecha_fin else None,
        "estado": c.estado, "disparada_por": c.disparada_por,
        "nombre_escenario_filtro": c.nombre_escenario_filtro,
        "total": len(resultados), "pasaron": sum(1 for r in resultados if r.paso),
    }


@login_required
def api_test_runs(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    marcar_corridas_stale_como_interrumpidas()
    # prefetch_related evita el N+1 (antes, cada _serialize_corrida_resumen
    # disparaba su propio c.resultados.all() -- 1+50 queries al listar).
    corridas = CorridaDePrueba.objects.all().prefetch_related("resultados")[:50]
    return JsonResponse([_serialize_corrida_resumen(c) for c in corridas], safe=False)


@login_required
def api_test_run_detail(request, pk):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    corrida = get_object_or_404(CorridaDePrueba, pk=pk)
    # Se calcula una sola vez y se reusa para el resumen y para el detalle,
    # en vez de que _serialize_corrida_resumen dispare su propio
    # resultados.all() por separado.
    resultados = list(corrida.resultados.select_related("escenario").all())
    return JsonResponse({
        **_serialize_corrida_resumen(corrida, resultados=resultados),
        "resultados": [{
            "id": r.pk, "escenario": r.escenario.nombre, "paso": r.paso,
            "fallos": r.fallos, "transcript": r.transcript, "error": r.error,
        } for r in resultados],
    })


@csrf_exempt
@login_required
def api_llm_config(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    if request.method == "POST":
        body = json_body(request)
        if "model" in body:
            set_setting("openrouter_model_override", body["model"])
            audit(request, "llm_config", "model", body["model"])
        if "api_key" in body:
            set_setting("openrouter_api_key_override", body["api_key"])
            audit(request, "llm_config", "api_key", "****")
        if body.get("action") == "clear_key":
            set_setting("openrouter_api_key_override", "")
            audit(request, "llm_config", "clear_api_key")
        return JsonResponse({"ok": True})
    active_model = get_setting("openrouter_model_override", "") or settings.OPENROUTER_MODEL
    active_key = get_setting("openrouter_api_key_override", "") or settings.OPENROUTER_API_KEY
    return JsonResponse({
        "active_model": active_model,
        "model_override": get_setting("openrouter_model_override", ""),
        "model_source": "override" if get_setting("openrouter_model_override", "") else "env",
        "key_set": bool(active_key),
        "key_override": bool(get_setting("openrouter_api_key_override", "")),
        "key_source": "override" if get_setting("openrouter_api_key_override", "") else ("env" if settings.OPENROUTER_API_KEY else "none"),
        # Todos verificados contra la API real de OpenRouter (soportan
        # tools/tool_choice, el bot depende de tool-calling) antes de listarlos.
        "models": ["~deepseek/deepseek-v4-flash-latest", "deepseek/deepseek-v4-flash"],
    })


@csrf_exempt
@login_required
def api_scraping_llm_config(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    # Solo modelo, igual que media-llm-config: desde la migracion del
    # 2026-09-02 el scraping usa la API key de OpenRouter, que se administra en
    # llm-config (una sola key para bot, media y scraping). Este endpoint
    # aceptaba ademas una key propia de DeepSeek; se saco para que no haya dos
    # lugares del panel editando credenciales del mismo proveedor.
    if request.method == "POST":
        body = json_body(request)
        if "model" in body:
            set_setting("openrouter_scraping_model_override", body["model"])
            audit(request, "scraping_llm_config", "model", body["model"])
        return JsonResponse({"ok": True})
    override = get_setting("openrouter_scraping_model_override", "")
    return JsonResponse({
        "active_model": override or settings.OPENROUTER_SCRAPING_MODEL,
        "model_override": override,
        "model_source": "override" if override else "env",
        "key_set": bool(get_setting("openrouter_api_key_override", "") or settings.OPENROUTER_API_KEY),
        # Verificados contra el catalogo real de OpenRouter (2026-09-02). No
        # necesitan tool-calling: este rol solo reestructura texto a JSON.
        "models": ["~deepseek/deepseek-v4-flash-latest", "deepseek/deepseek-v4-flash-0731"],
    })


@csrf_exempt
@login_required
def api_media_llm_config(request):
    if not _has_role(request, "admin_cuenta", "admin_ti"):
        return _forbidden()
    if request.method == "POST":
        body = json_body(request)
        if "model" in body:
            set_setting("openrouter_media_model_override", body["model"])
            audit(request, "media_llm_config", "model", body["model"])
        return JsonResponse({"ok": True})
    active_model = get_setting("openrouter_media_model_override", "") or settings.OPENROUTER_MEDIA_MODEL
    return JsonResponse({
        "active_model": active_model,
        "model_override": get_setting("openrouter_media_model_override", ""),
        "model_source": "override" if get_setting("openrouter_media_model_override", "") else "env",
        # Verificados contra la API real de OpenRouter: soportan imagen+audio
        # como input (describe_image/transcribe_audio en bot/flow/media_processing.py).
        "models": ["google/gemini-3.7-flash", "google/gemini-2.5-flash"],
    })


_RANGE_DEFAULT = "7d"
_SLA_RESPUESTA_SEG = 300
_HUMAN_SIN_ATENCION_SEG = 900
_TASA_AGENDAMIENTO_MINIMA = 0.10


def _resolve_range(request):
    range_key = request.GET.get("range", _RANGE_DEFAULT)
    today = _date.today()
    if range_key == "today":
        return today, today, "today"
    if range_key == "month":
        return today.replace(day=1), today, "month"
    if range_key == "prev_month":
        first_this_month = today.replace(day=1)
        last_day_prev_month = first_this_month - timedelta(days=1)
        return last_day_prev_month.replace(day=1), last_day_prev_month, "prev_month"
    if range_key == "custom":
        try:
            start = _date.fromisoformat(request.GET.get("from", ""))
            end = _date.fromisoformat(request.GET.get("to", ""))
            return start, end, "custom"
        except ValueError:
            pass
    return today - timedelta(days=6), today, _RANGE_DEFAULT


def _funnel(start, end):
    convs = list(
        Conversation.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
        .prefetch_related("messages")
    )
    total = len(convs)
    respondidas = sum(1 for c in convs if any(m.role in ("assistant", "human") for m in c.messages.all()))
    citas = Reserva.objects.filter(
        created_at__date__gte=start, created_at__date__lte=end, estado="activa",
    ).count()
    envios = CampaignSend.objects.filter(enviado_at__date__gte=start, enviado_at__date__lte=end)
    enviados = envios.count()
    confirmados = envios.filter(respondido=True).count()

    stages = [
        ("conversaciones", "Conversaciones iniciadas", total),
        ("respondidas", "Respondidas por el bot", respondidas),
        ("cita_agendada", "Cita agendada", citas),
        ("recordatorio_enviado", "Recordatorio enviado", enviados),
        ("recordatorio_confirmado", "Recordatorio confirmado", confirmados),
    ]
    funnel = []
    prev_count = None
    for stage, label, count in stages:
        rate_step = round(count / prev_count, 4) if prev_count else None
        rate_total = round(count / total, 4) if (total and stage != "conversaciones") else None
        funnel.append({"stage": stage, "label": label, "count": count, "rate_step": rate_step, "rate_total": rate_total})
        prev_count = count
    return convs, funnel


def _tiempos_respuesta(convs):
    tiempos = []
    for conv in convs:
        msgs = sorted(conv.messages.all(), key=lambda m: m.created_at)
        primer_user = next((m for m in msgs if m.role == "user"), None)
        if not primer_user:
            continue
        primera_respuesta = next(
            (m for m in msgs if m.role in ("assistant", "human") and m.created_at > primer_user.created_at), None
        )
        if primera_respuesta:
            tiempos.append((primera_respuesta.created_at - primer_user.created_at).total_seconds())
    return tiempos


def _fuera_de_horario_count(convs):
    _ensure_business_hours()
    horario_por_dia = {
        bh.dia_semana: (bh.hora_inicio, bh.hora_fin) for bh in BusinessHours.objects.filter(activo=True)
    }
    count = 0
    for conv in convs:
        local_dt = timezone.localtime(conv.created_at)
        ventana = horario_por_dia.get(local_dt.weekday())
        if not ventana or not (ventana[0] <= local_dt.time() <= ventana[1]):
            count += 1
    return count


def _alerts(kpis):
    alerts = []
    ahora = timezone.now()
    for conv in Conversation.objects.all()[:2000]:
        if conv.get_flow().get("modo") != "HUMAN":
            continue
        last_msg = conv.messages.order_by("-created_at").first()
        if last_msg and (ahora - last_msg.created_at).total_seconds() > _HUMAN_SIN_ATENCION_SEG:
            alerts.append({
                "severity": "critica",
                "title": "Conversación en HUMAN sin atender",
                "cause": "Nadie tomó la conversación",
                "action": "Asignar a un ejecutivo disponible",
            })
            break
    if kpis["tiempo_respuesta_promedio_seg"] is not None and kpis["tiempo_respuesta_promedio_seg"] > _SLA_RESPUESTA_SEG:
        alerts.append({
            "severity": "media",
            "title": "Tiempo de respuesta sobre el SLA",
            "cause": "Volumen alto de conversaciones o el bot está caído",
            "action": "Revisar el estado del bot y los logs",
        })
    if kpis["pct_agendada"] is not None and kpis["pct_agendada"] < _TASA_AGENDAMIENTO_MINIMA:
        alerts.append({
            "severity": "media",
            "title": "Tasa de agendamiento baja",
            "cause": "El FAQ no está derivando a agendamiento",
            "action": "Revisar el prompt y el flujo de agendamiento",
        })
    return alerts


def _funnel_evolution(convs, start, end):
    dias = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    por_dia = {d: {"conversaciones": 0, "respondidas": 0} for d in dias}
    for conv in convs:
        d = timezone.localtime(conv.created_at).date()
        if d in por_dia:
            por_dia[d]["conversaciones"] += 1
            if any(m.role in ("assistant", "human") for m in conv.messages.all()):
                por_dia[d]["respondidas"] += 1

    citas_por_dia = {
        row["dia"]: row["count"]
        for row in Reserva.objects.filter(
            created_at__date__gte=start, created_at__date__lte=end, estado="activa",
        ).annotate(dia=TruncDate("created_at")).values("dia").annotate(count=Count("id"))
    }
    envios_qs = CampaignSend.objects.filter(enviado_at__date__gte=start, enviado_at__date__lte=end)
    enviados_por_dia = {
        row["dia"]: row["count"]
        for row in envios_qs.annotate(dia=TruncDate("enviado_at")).values("dia").annotate(count=Count("id"))
    }
    confirmados_por_dia = {
        row["dia"]: row["count"]
        for row in envios_qs.filter(respondido=True).annotate(dia=TruncDate("enviado_at")).values("dia").annotate(count=Count("id"))
    }

    evolution = []
    for d in dias:
        evolution.append({
            "date": d.isoformat(),
            "conversaciones": por_dia[d]["conversaciones"],
            "respondidas": por_dia[d]["respondidas"],
            "cita_agendada": citas_por_dia.get(d, 0),
            "recordatorio_enviado": enviados_por_dia.get(d, 0),
            "recordatorio_confirmado": confirmados_por_dia.get(d, 0),
        })
    return evolution


@login_required
def api_dashboard_data(request):
    if request.GET.get("modo") == "demo":
        return JsonResponse(proyeccion.dashboard())
    today = _date.today()
    week_ago = today - timedelta(days=7)
    start, end, resolved_range = _resolve_range(request)
    convs, funnel = _funnel(start, end)
    tiempos_respuesta = _tiempos_respuesta(convs)
    kpis = {
        "tiempo_respuesta_promedio_seg": round(statistics.mean(tiempos_respuesta)) if tiempos_respuesta else None,
        "tiempo_respuesta_p50_seg": round(statistics.median(tiempos_respuesta)) if tiempos_respuesta else None,
        "pct_respondidas": funnel[1]["rate_total"],
        "pct_agendada": funnel[2]["rate_total"],
        "templates_enviados": funnel[3]["count"],
        "fuera_de_horario": _fuera_de_horario_count(convs),
    }
    alerts = _alerts(kpis)
    funnel_evolution = _funnel_evolution(convs, start, end)
    msg_today = Message.objects.filter(created_at__date=today).count()
    active_24h = Conversation.objects.filter(updated_at__gte=timezone.now() - timedelta(hours=24)).count()
    chart_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        chart_data.append({"date": d.isoformat(), "count": Message.objects.filter(created_at__date=d).count()})
    msg_by_role = {
        "user": Message.objects.filter(role="user", created_at__date=today).count(),
        "assistant": Message.objects.filter(role="assistant", created_at__date=today).count(),
        "human": Message.objects.filter(role="human", created_at__date=today).count(),
    }
    agent_distribution = list(
        Conversation.objects.exclude(active_agent="")
        .values("active_agent").annotate(count=Count("id")).order_by("-count")
    )
    human_mode_count = sum(
        1 for c in Conversation.objects.all()[:2000] if (c.get_flow() or {}).get("modo") == "HUMAN"
    )
    return JsonResponse({
        "es_proyeccion": False,
        "conv_count": Conversation.objects.count(),
        "msg_count": Message.objects.count(),
        "msg_today": msg_today,
        "msg_by_role": msg_by_role,
        "active_24h": active_24h,
        "active_flows": Conversation.objects.exclude(active_agent="").count(),
        "chart": chart_data,
        "bot_on": get_setting("bot_global_on", "true") == "true",
        "connected": bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_ID),
        "phone_id": mask_wa_id(settings.WHATSAPP_PHONE_ID),
        "model": get_setting("openrouter_model_override", "") or settings.OPENROUTER_MODEL,
        "reservas_total": Reserva.objects.filter(estado="activa").count(),
        "reservas_today": Reserva.objects.filter(estado="activa", created_at__date=today).count(),
        "reservas_week": Reserva.objects.filter(estado="activa", created_at__date__gte=week_ago).count(),
        "human_mode_count": human_mode_count,
        "agent_distribution": agent_distribution,
        "range": resolved_range,
        "funnel": funnel,
        "kpis": kpis,
        "funnel_evolution": funnel_evolution,
        "alerts": alerts,
    })


@csrf_exempt
@login_required
def api_scraping_sources(request):
    if request.method == "POST":
        body = json_body(request)
        url = (body.get("url") or "").strip()
        if not url:
            return JsonResponse({"error": "url es obligatoria"}, status=400)
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        if ScrapingSource.objects.filter(url=url).exists():
            return JsonResponse({"error": "ya existe una fuente con esa url"}, status=400)
        source = ScrapingSource.objects.create(
            url=url, nombre=(body.get("nombre") or "").strip(),
            frecuencia_horas=float(body.get("frecuencia_horas", 0) or 0),
            cliente=settings.CLIENTE_ACTIVO,
        )
        audit(request, "scraping_source", "create", source.url)
        return JsonResponse({"id": source.pk}, status=201)
    sources = ScrapingSource.objects.all()
    return JsonResponse([_serialize_source(s) for s in sources], safe=False)


def _serialize_source(source: ScrapingSource) -> dict:
    last_run = source.runs.order_by("-started_at").first()
    return {
        "id": source.pk, "url": source.url, "nombre": source.nombre,
        "frecuencia_horas": source.frecuencia_horas,
        "last_run": _serialize_run(last_run) if last_run else None,
    }


def _serialize_run(run) -> dict:
    return {
        "estado": run.estado, "paginas_procesadas": run.paginas_procesadas,
        "error_detalle": run.error_detalle,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
@login_required
def api_scraping_source_detail(request, pk):
    source = get_object_or_404(ScrapingSource, pk=pk)
    if request.method == "DELETE":
        source.delete()
        audit(request, "scraping_source", "delete", str(pk))
        return JsonResponse({"ok": True})
    body = json_body(request)
    if "nombre" in body:
        source.nombre = (body.get("nombre") or "").strip()
    if "frecuencia_horas" in body:
        source.frecuencia_horas = float(body.get("frecuencia_horas", 0) or 0)
    source.save()
    audit(request, "scraping_source", "update", str(pk))
    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST"])
@login_required
def api_scraping_source_run(request, pk):
    source = get_object_or_404(ScrapingSource, pk=pk)
    last_run = source.runs.order_by("-started_at").first()
    if last_run and last_run.estado == "corriendo":
        return JsonResponse({"error": "ya hay un scrape en curso para esta fuente"}, status=409)
    run = runner.start_scrape(source)

    def _run_in_background(db_name, run):
        set_current_db(db_name)
        runner.execute_scrape(run)

    threading.Thread(target=_run_in_background, args=(get_current_db(), run), daemon=True).start()
    audit(request, "scraping_run", "start", source.url)
    return JsonResponse({"estado": "corriendo"}, status=202)


@require_http_methods(["GET"])
@login_required
def api_scraping_source_detail_info(request, pk):
    source = get_object_or_404(ScrapingSource, pk=pk)
    last_run = source.runs.order_by("-started_at").first()
    if not last_run:
        return JsonResponse({"error": "esta fuente todavia no tiene corridas"}, status=404)
    return JsonResponse({
        "last_run": _serialize_run(last_run),
        "pages": [{"url": p.url, "texto": p.texto} for p in last_run.pages.all()],
        "paginas_con_error": last_run.paginas_con_error,
        "catalogo_extraido": last_run.catalogo_extraido,
    })


@csrf_exempt
@login_required
def api_enviar_recordatorio(request, codigo):
    try:
        reserva = Reserva.objects.select_related("servicio", "sucursal").get(codigo=codigo)
    except Reserva.DoesNotExist:
        return JsonResponse({"error": "reserva no encontrada"}, status=404)
    if reserva.estado != "activa":
        return JsonResponse({"error": "la reserva no esta activa"}, status=409)
    if esta_optout(reserva.contacto):
        audit(request, "enviar_recordatorio", reserva.codigo, "bloqueado_optout")
        return JsonResponse({"error": "contacto en lista de no contactar (opt-out), no se envia"}, status=409)

    conv, _ = Conversation.objects.get_or_create(wa_id=reserva.contacto, defaults={"name": reserva.cliente_nombre})
    flow = conv.get_flow() or {}
    flow["reserva_actual"] = {
        "codigo": reserva.codigo, "servicio": reserva.servicio.nombre, "sucursal": reserva.sucursal.nombre,
        "fecha": reserva.fecha.isoformat(), "hora": reserva.hora.strftime("%H:%M"),
    }
    conv.set_flow(flow)
    conv.save(update_fields=["flow_data", "updated_at"])

    CampaignSend.objects.create(
        contacto=reserva.contacto, campaign_type="recordatorio_24h",
        template="recordatorio_24h", conversation=conv,
    )
    import httpx
    from bot.whatsapp.client import get_wa_client
    wa = get_wa_client()
    mensaje = (
        f"Hola {reserva.cliente_nombre or ''}, te recordamos tu hora de "
        f"{reserva.servicio.nombre} el {reserva.fecha} a las {reserva.hora.strftime('%H:%M')} "
        f"en {reserva.sucursal.nombre}. ¿Confirmas, quieres reagendar o anular?"
    ).strip()
    try:
        ok = wa.send_text(reserva.contacto, mensaje)
    except httpx.HTTPError:
        # WHATSAPP_TOKEN vacio (dev/test sin credenciales reales) hace que
        # httpx arme un header "Authorization: Bearer " invalido y reviente
        # con LocalProtocolError antes de cualquier request de red. Ver
        # api_send_message para el mismo patron.
        ok = False
    audit(request, "enviar_recordatorio", reserva.codigo, str(ok))
    return JsonResponse({"ok": True, "enviado": ok})


ENCUESTA_CAMPAIGN_TYPES = {"encuesta_servicio_tecnico", "encuesta_venta_auto_nuevo"}


def _template_para_encuesta(campaign_type: str) -> str:
    if campaign_type == "encuesta_servicio_tecnico":
        return settings.ENCUESTA_SERVICIO_TECNICO_TEMPLATE
    if campaign_type == "encuesta_venta_auto_nuevo":
        return settings.ENCUESTA_VENTA_AUTO_NUEVO_TEMPLATE
    return ""


@csrf_exempt
@login_required
def api_enviar_encuesta_masiva(request):
    if request.method != "POST":
        return JsonResponse({"error": "metodo no permitido"}, status=405)

    body = json_body(request)
    campaign_type = body.get("campaign_type", "")
    csv_text = body.get("csv_text", "")

    if campaign_type not in ENCUESTA_CAMPAIGN_TYPES:
        audit(request, "enviar_encuesta_masiva", campaign_type, "rechazado_campaign_type_invalido")
        return JsonResponse({"error": "campaign_type invalido"}, status=400)
    template = _template_para_encuesta(campaign_type)
    if not template:
        audit(request, "enviar_encuesta_masiva", campaign_type, "rechazado_sin_template")
        return JsonResponse({"error": f"no hay template configurado para {campaign_type}"}, status=400)

    import io
    import httpx
    from bot.whatsapp.client import get_wa_client

    filas = list(csv.DictReader(io.StringIO(csv_text)))
    if len(filas) > 200:
        audit(request, "enviar_encuesta_masiva", campaign_type, "rechazado_demasiadas_filas")
        return JsonResponse({"error": f"el CSV tiene {len(filas)} filas, el maximo por envio es 200"}, status=400)
    if filas and "wa_id" not in filas[0]:
        audit(request, "enviar_encuesta_masiva", campaign_type, "rechazado_sin_columna_wa_id")
        return JsonResponse({"error": "el CSV no tiene una columna 'wa_id'"}, status=400)

    wa = get_wa_client()
    enviados = 0
    optout_saltados = 0
    errores = []
    for i, row in enumerate(filas, start=2):
        wa_id = (row.get("wa_id") or "").strip()
        if not wa_id:
            errores.append(f"fila {i}: wa_id vacio")
            continue
        if esta_optout(wa_id):
            optout_saltados += 1
            continue
        if CampaignSend.objects.filter(contacto=wa_id, campaign_type=campaign_type, respondido=False).exists():
            errores.append(f"fila {i}: {wa_id} ya tiene una encuesta de este tipo sin responder, no se reenvia")
            continue
        CampaignSend.objects.create(contacto=wa_id, campaign_type=campaign_type, template=template)
        try:
            ok = wa.send_template(wa_id, template)
        except httpx.HTTPError:
            ok = False
        if ok:
            enviados += 1
        else:
            errores.append(f"fila {i}: envio fallo para {wa_id}")

    audit(request, "enviar_encuesta_masiva", campaign_type, f"enviados={enviados}")
    return JsonResponse({"ok": True, "enviados": enviados, "optout_saltados": optout_saltados, "errores": errores})


@login_required
def api_reservas(request):
    """Horas agendadas de taller (docx S11).

    No existia ningun endpoint de listado: solo estaba el de enviar
    recordatorio, que recibe un codigo que habia que conocer de antes. El docx
    pide que la reserva quede VISIBLE en la plataforma con nombre, telefono,
    email, vehiculo, patente, servicio, fecha, hora y estado.

    Filtros opcionales: ?estado=activa|cancelada y ?desde=YYYY-MM-DD. Se
    respetan tambien en modo proyeccion: un selector que no filtra nada
    delante de un cliente es peor que no tener selector.
    """
    estado = (request.GET.get("estado") or "").strip()
    desde = (request.GET.get("desde") or "").strip()

    if request.GET.get("modo") == "demo":
        filas = proyeccion.reservas()
        if estado in ("activa", "cancelada"):
            filas = [f for f in filas if f["estado"] == estado]
        if desde:
            try:
                desde_date = _date.fromisoformat(desde)
                filas = [f for f in filas if _date.fromisoformat(f["fecha"]) >= desde_date]
            except ValueError:
                pass
        return JsonResponse(filas, safe=False)

    from bot.models import Reserva

    query = Reserva.objects.select_related("servicio", "sucursal")
    if estado in ("activa", "cancelada"):
        query = query.filter(estado=estado)
    if desde:
        try:
            query = query.filter(fecha__gte=_date.fromisoformat(desde))
        except ValueError:
            pass

    return JsonResponse([{
        "codigo": r.codigo,
        "cliente": r.cliente_nombre,
        # Mismo enmascarado que el resto del panel: el telefono es dato
        # personal y no hace falta completo para operar una reserva.
        "telefono": mask_wa_id(r.contacto),
        "email": r.cliente_email,
        "vehiculo": r.vehiculo,
        "patente": r.patente,
        "vehiculo_anio": r.vehiculo_anio,
        "vehiculo_km": r.vehiculo_km,
        "servicio": r.servicio.nombre,
        "sucursal": r.sucursal.nombre,
        "fecha": r.fecha.isoformat(),
        "hora": r.hora.strftime("%H:%M"),
        "estado": r.estado,
        "created_at": r.created_at.isoformat(),
    } for r in query.order_by("fecha", "hora")[:500]], safe=False)


@login_required
def api_leads(request):
    """Leads comerciales con sus antecedentes (docx S18).

    Orden por lead score descendente: el panel existe para que un ejecutivo
    sepa a quien llamar primero, no para listar todo por fecha.
    Filtros opcionales: ?temperatura=HOT y ?q=<texto> sobre nombre/telefono/vehiculo.
    Se respetan tambien en modo proyeccion: un selector que no filtra nada
    delante de un cliente es peor que no tener selector.
    """
    temperatura = (request.GET.get("temperatura") or "").upper()
    busqueda = (request.GET.get("q") or "").strip()

    if request.GET.get("modo") == "demo":
        filas = proyeccion.leads()
        if temperatura in ("HOT", "WARM", "COLD"):
            filas = [f for f in filas if f["temperatura"] == temperatura]
        if busqueda:
            b = busqueda.lower()
            filas = [f for f in filas if b in (f["nombre"] or "").lower()
                     or b in (f["telefono"] or "").lower()
                     or b in (f["vehiculo_interes"] or "").lower()]
        return JsonResponse(filas, safe=False)

    from bot.models import LeadComercial

    query = LeadComercial.objects.select_related("conversation")
    if temperatura in ("HOT", "WARM", "COLD"):
        query = query.filter(temperatura=temperatura)
    if busqueda:
        query = query.filter(
            Q(nombre__icontains=busqueda)
            | Q(telefono__icontains=busqueda)
            | Q(vehiculo_interes__icontains=busqueda)
        )
    return JsonResponse([{
        "id": l.id,
        "conversation_id": l.conversation_id,
        "nombre": l.nombre,
        "telefono": l.telefono,
        "email": l.email,
        "comuna": l.comuna,
        "vehiculo_interes": l.vehiculo_interes,
        "vehiculo_codigo": l.vehiculo_codigo,
        "presupuesto": l.presupuesto,
        "pie_disponible": l.pie_disponible,
        "cuota_objetivo": l.cuota_objetivo,
        "plazo_compra": l.plazo_compra,
        "tiene_parte_pago": l.tiene_parte_pago,
        "vehiculo_actual": l.vehiculo_actual,
        "intencion": l.intencion,
        "sentimiento": l.sentimiento,
        "urgencia": l.urgencia,
        "temperatura": l.temperatura,
        "lead_score": l.lead_score,
        "proxima_accion": l.proxima_accion,
        "resumen": l.resumen,
        "ultima_interaccion": l.conversation.updated_at.isoformat() if l.conversation else None,
        "created_at": l.created_at.isoformat(),
    } for l in query[:500]], safe=False)


def _estado_despacho(lead) -> str:
    """En qué estado está el envío de este lead al CRM.

    Cuatro estados y no un booleano: con `LEAD_SINK=none` TODOS los leads
    estarían "pendientes" y el rojo del panel dejaría de significar algo. Un
    estado propio para "no hay destino configurado" mantiene la señal.

    "conflicto" es distinto de "pendiente" A PROPÓSITO: un pendiente se
    arregla solo (el barrido lo reintenta), un conflicto no -- necesita que
    una persona decida la identidad correcta del lado del CRM. Confundirlos
    en el panel sería el mismo error que confundirlos en el código.
    """
    if settings.LEAD_SINK == "none":
        return "sin_destino"
    if lead.conflicto_en:
        return "conflicto"
    return "despachado" if lead.despachado_en else "pendiente"


@login_required
def api_leads_intouch(request):
    """Los leads comerciales B2B de InTouch, para el panel (Task 18 del spec).

    Hoy es la UNICA via por la que el equipo comercial ve una oportunidad: no
    existe todavia ningun endpoint de leads en el orquestador, y el adaptador
    de salida de este bot arranca apagado (LEAD_SINK=none) hasta que se
    construya su destino. Un lead que no aparece aca no lo trabaja nadie.

    No comparte nombre ni ruta con `api_leads` (el lead automotriz heredado,
    `LeadComercial`): son modelos y verticales distintos, y ese endpoint sigue
    en uso por su propia suite de tests.

    Incluye `telefono` (del wa_id de la conversacion) y `despachado`, que el
    modelo no tiene como columna propia: el primero lo agrega la plataforma
    porque el prompt le prohibe al bot pedirselo al contacto, el segundo se
    deriva del sello `despachado_en`. Tambien incluye `sink_activo`, para que
    el panel sepa si mostrar la marca de "sin despachar" -- con el sink
    apagado, todos los leads quedarian marcados y la señal se pierde.

    Filtro opcional por `lead_score` (HOT/WARM/COLD/NO_CALIFICADO). Orden por
    actualizacion descendente (`Meta.ordering` del modelo): el lead que
    acaba de cambiar es el que un ejecutivo necesita ver primero.
    """
    from bot.models import LeadInTouch

    filas = LeadInTouch.objects.select_related("conversation").all()
    score = request.GET.get("lead_score", "").strip()
    if score:
        filas = filas.filter(lead_score=score)
    return JsonResponse({
        "sink_activo": settings.LEAD_SINK != "none",
        "leads": [
            {
                "id": lead.id,
                "conversation_id": lead.conversation_id,
                "telefono": lead.conversation.wa_id,
                "nombre_completo": lead.nombre_completo,
                "correo": lead.correo,
                "empresa": lead.empresa,
                "industria": lead.industria,
                "subtipo_automotriz": lead.subtipo_automotriz,
                "cargo": lead.cargo,
                "pais_ciudad": lead.pais_ciudad,
                "situacion_contact_center": lead.situacion_contact_center,
                "tipo_contact_center": lead.tipo_contact_center,
                "usa_ia_actualmente": lead.usa_ia_actualmente,
                "canales_actuales": lead.canales_actuales or [],
                "volumen_interacciones": lead.volumen_interacciones,
                "necesidad_principal": lead.necesidad_principal,
                "soluciones_interes": lead.soluciones_interes or [],
                "intencion": lead.intencion,
                "plazo_proyecto": lead.plazo_proyecto,
                "lead_score": lead.lead_score,
                "solicita_consultoria": lead.solicita_consultoria,
                "solicita_contacto_humano": lead.solicita_contacto_humano,
                "resumen_conversacion": lead.resumen_conversacion,
                "siguiente_accion_recomendada": lead.siguiente_accion_recomendada,
                "creado": lead.creado.isoformat(),
                "actualizado": lead.actualizado.isoformat(),
                "notificado": bool(lead.notificado_en),
                "estado_despacho": _estado_despacho(lead),
                "conflicto_motivo": lead.conflicto_motivo,
                "crm_contact_id": lead.crm_contact_id,
                "crm_deal_id": lead.crm_deal_id,
                # Se conserva por compatibilidad con el frontend viejo.
                "despachado": bool(lead.despachado_en),
            }
            for lead in filas
        ],
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_lead_reintentar(request, lead_id: int):
    """Reintenta el despacho de un lead al CRM, a pedido de una persona.

    Va con el MISMO `evento_id` que el intento anterior: si el receptor ya
    había hecho commit y se perdió la respuesta, devuelve el mismo id en vez
    de crear otra oportunidad. Por eso NO se llama a `marcar_evento` acá.

    SIGUE FUNCIONANDO SOBRE UN LEAD EN CONFLICTO, a propósito y sin ninguna
    guarda que lo bloquee: es la ÚNICA vía por la que un conflicto vuelve al
    circuito. El bot no tiene forma de enterarse solo de que una persona lo
    resolvió del lado del CRM -- ni el barrido ni el despacho en el turno lo
    reintentan (ver `_despachar_si_corresponde` y `despachar_leads_pendientes`)
    -- así que este botón es deliberadamente el único camino de vuelta.

    No propaga el error: un reintento que falla informa el estado y deja el
    lead reintentable, que es exactamente lo que ya era.
    """
    from bot.business import lead_intouch
    from bot.models import LeadInTouch

    lead = get_object_or_404(
        LeadInTouch.objects.select_related("conversation"), pk=lead_id)

    if settings.LEAD_SINK == "none":
        return JsonResponse({
            "estado_despacho": "sin_destino",
            "detalle": "No hay destino configurado (LEAD_SINK=none).",
            "crm_contact_id": lead.crm_contact_id,
            "crm_deal_id": lead.crm_deal_id,
            "conflicto_motivo": lead.conflicto_motivo,
        })

    if not lead.evento_id:
        lead_intouch.marcar_evento(lead)

    try:
        resultado = lead_intouch._enviar_al_sink(lead_intouch.payload_del_lead(lead))
    except Exception:
        logger.warning("[lead] falló el reintento manual de %s", lead_id, exc_info=True)
        resultado = {"resultado": "fallo", "motivo": "excepción durante el reintento manual"}

    if resultado["resultado"] == "ok":
        cuerpo = resultado["cuerpo"]
        lead.despachado_en = timezone.now()
        lead.crm_contact_id = cuerpo.get("contactId") or ""
        lead.crm_deal_id = cuerpo.get("dealId") or ""
        # La persona ya resolvió la ambigüedad del lado del CRM: se limpia el
        # conflicto para que el estado no siga marcándolo.
        lead.conflicto_en = None
        lead.conflicto_motivo = ""
        lead.save(update_fields=["despachado_en", "crm_contact_id", "crm_deal_id",
                                  "conflicto_en", "conflicto_motivo"])
    elif resultado["resultado"] == "conflicto":
        lead.conflicto_en = timezone.now()
        lead.conflicto_motivo = resultado["motivo"]
        lead.save(update_fields=["conflicto_en", "conflicto_motivo"])

    return JsonResponse({
        "estado_despacho": _estado_despacho(lead),
        "crm_contact_id": lead.crm_contact_id,
        "crm_deal_id": lead.crm_deal_id,
        "conflicto_motivo": lead.conflicto_motivo,
    })


@login_required
def api_campanas(request):
    """Campanas configuradas con su embudo real (docx S18/S20).

    Entregados y leidos vienen en None: los sabe Meta, no nosotros. El panel
    los completa con GET /api/admin/template-stats, que consulta la Graph API.
    Devolverlos inventados aca seria exactamente lo que el docx S15 prohibe.

    Sin filtros: la vista no ofrece ningun selector, asi que no hay nada que
    replicar en el camino de proyeccion.
    """
    if request.GET.get("modo") == "demo":
        return JsonResponse(proyeccion.campanas(), safe=False)

    from bot.models import Campana
    return JsonResponse([c.metricas() | {
        "id": c.id, "template": c.template, "segmento": c.segmento,
        "objetivo": c.objetivo, "mensaje": c.mensaje,
        "palabra_clave": c.palabra_clave, "activa": c.activa,
    } for c in Campana.objects.all()], safe=False)


@csrf_exempt
@login_required
def api_campana_enviar(request):
    """Envio masivo de una campana comercial a una lista de numeros.

    Comparte las tres guardas del envio de encuestas, que existen por razones
    reales y no por prolijidad: tope de filas (un CSV pegado por error no
    dispara miles de mensajes), opt-out de Ley 21.719, y no reenviar a quien
    ya recibio esta campana y todavia no responde.
    """
    if request.method != "POST":
        return JsonResponse({"error": "metodo no permitido"}, status=405)

    import io
    import httpx
    from bot.models import Campana, CampaignSend, esta_optout
    from bot.whatsapp.client import get_wa_client

    body = json_body(request)
    campaign_type = body.get("campaign_type", "")
    csv_text = body.get("csv_text", "")

    campana = Campana.objects.filter(campaign_type=campaign_type, activa=True).first()
    if campana is None:
        audit(request, "enviar_campana", campaign_type, "rechazado_campana_inexistente")
        return JsonResponse({"error": "campana inexistente o inactiva"}, status=400)
    if not campana.template:
        audit(request, "enviar_campana", campaign_type, "rechazado_sin_template")
        return JsonResponse(
            {"error": f"la campana '{campana.nombre}' no tiene plantilla de Meta configurada"},
            status=400)

    filas = list(csv.DictReader(io.StringIO(csv_text)))
    if len(filas) > 200:
        audit(request, "enviar_campana", campaign_type, "rechazado_demasiadas_filas")
        return JsonResponse(
            {"error": f"el CSV tiene {len(filas)} filas, el maximo por envio es 200"}, status=400)
    if filas and "wa_id" not in filas[0]:
        audit(request, "enviar_campana", campaign_type, "rechazado_sin_columna_wa_id")
        return JsonResponse({"error": "el CSV no tiene una columna 'wa_id'"}, status=400)

    wa = get_wa_client()
    enviados = 0
    optout_saltados = 0
    errores = []
    for i, row in enumerate(filas, start=2):
        wa_id = (row.get("wa_id") or "").strip()
        if not wa_id:
            errores.append(f"fila {i}: wa_id vacio")
            continue
        if esta_optout(wa_id):
            optout_saltados += 1
            continue
        if CampaignSend.objects.filter(
                contacto=wa_id, campaign_type=campaign_type, respondido=False).exists():
            errores.append(f"fila {i}: {wa_id} ya recibio esta campana y no ha respondido")
            continue
        try:
            ok = wa.send_template(wa_id, campana.template)
        except httpx.HTTPError:
            ok = False
        if ok:
            # El CampaignSend se crea DESPUES de un envio exitoso, no antes: si
            # se registra primero y el envio falla, el contacto queda marcado
            # como ya contactado y la deduplicacion de arriba lo excluye para
            # siempre de una campana que nunca recibio.
            CampaignSend.objects.create(
                contacto=wa_id, campaign_type=campaign_type, template=campana.template)
            enviados += 1
        else:
            errores.append(f"fila {i}: envio fallo para {wa_id}")

    audit(request, "enviar_campana", campaign_type, f"enviados={enviados}")
    return JsonResponse({
        "ok": True, "enviados": enviados,
        "optout_saltados": optout_saltados, "errores": errores,
    })


@csrf_exempt
@login_required
def api_welcome(request):
    if request.method == "POST":
        body = json_body(request)
        set_setting("welcome_message", body.get("message", ""))
        audit(request, "welcome", "save")
        return JsonResponse({"ok": True})
    return JsonResponse({"message": get_setting("welcome_message", "¡Hola! ¿En qué le puedo ayudar?")})


@csrf_exempt
@login_required
def api_vacation(request):
    if request.method == "POST":
        body = json_body(request)
        set_setting("vacation_message", body.get("message", ""))
        audit(request, "vacation", "save")
        return JsonResponse({"ok": True})
    return JsonResponse({
        "message": get_setting(
            "vacation_message",
            "Estamos fuera de nuestro horario de atención. Te responderemos a la brevedad.",
        ),
    })


_BUSINESS_HOURS_DEFAULT = [
    (0, "09:00", "18:00", True), (1, "09:00", "18:00", True), (2, "09:00", "18:00", True),
    (3, "09:00", "18:00", True), (4, "09:00", "18:00", True),
    (5, "09:00", "13:00", False), (6, "00:00", "00:00", False),
]


def _ensure_business_hours():
    if not BusinessHours.objects.exists():
        for dia, inicio, fin, activo in _BUSINESS_HOURS_DEFAULT:
            BusinessHours.objects.create(dia_semana=dia, hora_inicio=inicio, hora_fin=fin, activo=activo)


@csrf_exempt
@login_required
def api_business_hours(request):
    _ensure_business_hours()
    if request.method == "POST":
        body = json_body(request)
        for dia in body.get("dias", []):
            BusinessHours.objects.filter(dia_semana=dia["dia_semana"]).update(
                hora_inicio=dia["hora_inicio"], hora_fin=dia["hora_fin"], activo=dia["activo"],
            )
        audit(request, "business_hours", "save")
        return JsonResponse({"ok": True})
    dias = BusinessHours.objects.order_by("dia_semana")
    return JsonResponse({"dias": [{
        "dia_semana": d.dia_semana,
        "hora_inicio": d.hora_inicio.strftime("%H:%M"),
        "hora_fin": d.hora_fin.strftime("%H:%M"),
        "activo": d.activo,
    } for d in dias]})


@csrf_exempt
@require_http_methods(["GET", "POST"])
@login_required
def api_quick_responses(request):
    if request.method == "POST":
        body = json_body(request)
        qr = QuickResponse.objects.create(
            pattern=body.get("pattern", ""), response=body.get("response", ""), priority=body.get("priority", 0),
        )
        audit(request, "quick_response", "create", qr.pattern)
        return JsonResponse({"id": qr.pk}, status=201)
    items = QuickResponse.objects.all()
    return JsonResponse([{
        "id": q.pk, "pattern": q.pattern, "response": q.response, "priority": q.priority,
    } for q in items], safe=False)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
@login_required
def api_quick_response_detail(request, pk):
    qr = get_object_or_404(QuickResponse, pk=pk)
    if request.method == "DELETE":
        qr.delete()
        audit(request, "quick_response", "delete", str(pk))
        return JsonResponse({"ok": True})
    body = json_body(request)
    qr.pattern = body.get("pattern", qr.pattern)
    qr.response = body.get("response", qr.response)
    qr.priority = body.get("priority", qr.priority)
    qr.save()
    audit(request, "quick_response", "update", str(pk))
    return JsonResponse({"ok": True})


@csrf_exempt
@login_required
def api_snippets(request):
    if request.method == "POST":
        body = json_body(request)
        s = Snippet.objects.create(nombre=body.get("nombre", ""), texto=body.get("texto", ""))
        audit(request, "snippet", "create", s.nombre)
        return JsonResponse({"id": s.pk}, status=201)
    items = Snippet.objects.all()
    return JsonResponse([{"id": s.pk, "nombre": s.nombre, "texto": s.texto} for s in items], safe=False)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
@login_required
def api_snippet_detail(request, pk):
    s = get_object_or_404(Snippet, pk=pk)
    if request.method == "DELETE":
        s.delete()
        audit(request, "snippet", "delete", str(pk))
        return JsonResponse({"ok": True})
    body = json_body(request)
    s.nombre = body.get("nombre", s.nombre)
    s.texto = body.get("texto", s.texto)
    s.save()
    audit(request, "snippet", "update", str(pk))
    return JsonResponse({"ok": True})


@csrf_exempt
@login_required
def api_filters(request):
    if request.method == "POST":
        body = json_body(request)
        f = WaFilter.objects.create(wa_id=body.get("wa_id", ""), tipo=body.get("tipo", "block"))
        audit(request, "filter", "create", f.wa_id)
        return JsonResponse({"id": f.pk}, status=201)
    items = WaFilter.objects.all()
    return JsonResponse([{"id": f.pk, "wa_id": f.wa_id, "tipo": f.tipo} for f in items], safe=False)


@csrf_exempt
@require_http_methods(["DELETE"])
@login_required
def api_filter_detail(request, pk):
    f = get_object_or_404(WaFilter, pk=pk)
    f.delete()
    audit(request, "filter", "delete", str(pk))
    return JsonResponse({"ok": True})


@csrf_exempt
@login_required
def api_handoff(request):
    config, _ = HandoffConfig.objects.get_or_create(pk=1)
    if request.method == "POST":
        body = json_body(request)
        config.keywords = body.get("keywords", [])
        config.save(update_fields=["keywords"])
        audit(request, "handoff", "save")
        return JsonResponse({"ok": True})
    return JsonResponse({"keywords": config.keywords})


def _parse_int_param(request, name, default):
    # request.GET siempre trae strings -- un valor no numerico (link viejo,
    # URL editada a mano) no debe tumbar la vista con un 500. Se ignora
    # silenciosamente y se cae al default, igual que si el param no viniera.
    raw = request.GET.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _logs_queryset(request):
    qs = Message.objects.select_related("conversation").order_by("-created_at")
    conv_id = _parse_int_param(request, "conversation_id", None)
    if conv_id is not None:
        qs = qs.filter(conversation_id=conv_id)
    role = request.GET.get("role")
    if role:
        qs = qs.filter(role=role)
    return qs


@login_required
def api_logs(request):
    limit = min(_parse_int_param(request, "limit", 200), 500)
    msgs = list(_logs_queryset(request)[:limit])
    return JsonResponse([{
        "id": m.pk, "conversation_id": m.conversation_id,
        "wa_id": mask_wa_id(m.conversation.wa_id), "name": m.conversation.name,
        "role": m.role, "content": m.content, "created_at": m.created_at.isoformat(),
    } for m in msgs], safe=False)


@login_required
def api_logs_export(request):
    limit = min(_parse_int_param(request, "limit", 500), 2000)
    msgs = list(_logs_queryset(request)[:limit])
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="logs.csv"'
    writer = csv.writer(response)
    writer.writerow(["id", "conversation_id", "wa_id", "name", "role", "content", "created_at"])
    for m in msgs:
        writer.writerow([
            m.pk, m.conversation_id, mask_wa_id(m.conversation.wa_id), m.conversation.name,
            m.role, m.content, m.created_at.isoformat(),
        ])
    return response


@login_required
def api_audit(request):
    limit = min(_parse_int_param(request, "limit", 200), 500)
    logs = list(AuditLog.objects.all()[:limit])
    return JsonResponse([{
        "id": a.pk, "user": a.user, "action": a.action, "target": a.target,
        "details": a.details, "created_at": a.created_at.isoformat(),
    } for a in logs], safe=False)


# ─── Meta Stats (Graph API) ───────────────────────────────
# Endpoints que exponen las estadisticas del WhatsApp Business Account al
# dashboard. Consumen bot.whatsapp.meta_stats (que cachea 30 min en memoria)
# y devuelven JSON normalizado listo para el frontend.

def _parse_stats_date_range(request):
    """Extrae start/end YYYY-MM-DD desde ?start=&end=, default ultimos 30 dias.

    Devuelve (start_ts, end_ts, start_str, end_str) o (None, None, err_msg, None)
    si el formato es invalido.
    """
    from bot.whatsapp.meta_stats import date_to_ts
    end_str = request.GET.get("end", "").strip()
    start_str = request.GET.get("start", "").strip()
    today = timezone.localdate()
    if not end_str:
        end_str = today.isoformat()
    if not start_str:
        start_str = (today - timedelta(days=30)).isoformat()
    try:
        start_ts = date_to_ts(start_str)
        end_ts = date_to_ts(end_str)
    except ValueError as e:
        return None, None, f"Fecha invalida (formato YYYY-MM-DD): {e}", None
    if start_ts > end_ts:
        return None, None, "start > end", None
    return start_ts, end_ts, start_str, end_str


@login_required
def api_meta_stats(request):
    """Estadisticas agregadas de mensajes desde la Graph API de Meta.

    Query params: ?start=YYYY-MM-DD&end=YYYY-MM-DD (default ultimos 30 dias).

    Devuelve las 4 cards de la imagen (excepto Cargos totales):
      - total_messages: enviados / entregados
      - delivered_by_category: Marketing/Utilidad/Autenticacion/Servicio
      - free_delivered: Atencion al cliente gratuita / Punto de acceso gratuito
      - paid_delivered_by_category: mismo desglose pero solo pagados
    """
    from bot.whatsapp.meta_stats import get_message_stats

    start_ts, end_ts, start_str, end_str = _parse_stats_date_range(request)
    if start_ts is None:
        return JsonResponse({"ok": False, "error": start_str}, status=400)

    raw = get_message_stats(start_ts, end_ts)
    if raw is None:
        return JsonResponse({
            "ok": False,
            "error": "No se pudo obtener stats de Meta (ver logs)",
            "start": start_str, "end": end_str,
        }, status=502)

    # Extraer los data_points del pricing_analytics con dimensions.
    # Estructura Meta: raw_pricing_analytics.pricing_analytics.data[].data_points[]
    # Cada data_point tiene: start, end, volume, cost, y (si hay dimensions)
    # pricing_category + pricing_type.
    price = (raw.get("raw_pricing_analytics") or {}).get("pricing_analytics") or {}
    data_series = price.get("data") or []

    # Buckets para las cards. Meta categorias: MARKETING/UTILITY/AUTHENTICATION/
    # AUTHENTICATION_INTERNATIONAL/SERVICE/REFERRAL_CONVERSION (por si aparece).
    # Meta pricing_types: REGULAR (pagado) / FREE_CUSTOMER_SERVICE / FREE_ENTRY_POINT.
    delivered_total = 0
    total_cost = 0.0
    delivered_by_cat: dict[str, int] = {}
    paid_delivered_by_cat: dict[str, int] = {}
    free_delivered: dict[str, int] = {}

    # Meta pone pricing_category y pricing_type DENTRO de cada data_point,
    # no en la serie. Cada serie tiene un solo `data_points[]` mezclando
    # todas las combinaciones de categoria+tipo.
    for series in data_series:
        for pt in series.get("data_points") or []:
            volume = int(pt.get("volume") or 0)
            cost = float(pt.get("cost") or 0.0)
            cat = str(pt.get("pricing_category") or "UNKNOWN")
            ptype = str(pt.get("pricing_type") or "REGULAR")
            delivered_total += volume
            total_cost += cost
            delivered_by_cat[cat] = delivered_by_cat.get(cat, 0) + volume
            if ptype == "REGULAR":
                paid_delivered_by_cat[cat] = paid_delivered_by_cat.get(cat, 0) + volume
            elif ptype in ("FREE_CUSTOMER_SERVICE", "FREE_ENTRY_POINT"):
                free_delivered[ptype] = free_delivered.get(ptype, 0) + volume

    # Fallback: si data_series viene sin dimensions (formato viejo), sumamos
    # todos los data_points del solo bloque como total sin desglose.
    if not data_series and price.get("data_points"):
        for pt in price["data_points"]:
            delivered_total += int(pt.get("volume") or 0)
            total_cost += float(pt.get("cost") or 0.0)

    # Analytics: sent + delivered agregados desde el endpoint `analytics`.
    # Estructura: raw_analytics.analytics.data_points[{start,end,sent,delivered}].
    # `sent` es lo que el bot envio (incluye templates + respuestas). Puede
    # diferir levemente de `delivered_total` (mensajes que Meta cobra), porque
    # los mensajes de free_customer_service NO entran en pricing_analytics
    # pero SI en analytics.
    analytics = (raw.get("raw_analytics") or {}).get("analytics") or {}
    total_sent = 0
    total_delivered_analytics = 0
    for pt in analytics.get("data_points") or []:
        total_sent += int(pt.get("sent") or 0)
        total_delivered_analytics += int(pt.get("delivered") or 0)

    # `received` no lo expone Meta en la Graph API — lo contamos desde nuestra
    # BD: mensajes con role="user" en el rango de fechas. Es la mejor
    # aproximacion local. Si el bot recibe pero no procesa (ej. caida temporal
    # del webhook), Meta lo podria haber contado y nosotros no — asumimos
    # discrepancia baja.
    start_date = _date.fromisoformat(start_str)
    end_date = _date.fromisoformat(end_str)
    received_count = Message.objects.filter(
        role="user",
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).count()

    return JsonResponse({
        "ok": True,
        "start": start_str, "end": end_str,
        "total_messages": {
            # Prefiero delivered de analytics porque incluye los free_customer_service
            # que pricing_analytics no cobra. Si analytics fallo, uso el de pricing.
            "delivered": total_delivered_analytics or delivered_total,
            "sent": total_sent or None,
            "received": received_count,
        },
        "delivered_by_category": delivered_by_cat,
        "free_delivered": free_delivered,
        "paid_delivered_by_category": paid_delivered_by_cat,
        "total_cost_approx": round(total_cost, 2),
        # raw para debug del front hasta cerrar el mapping
        "_raw_pricing": price,
    })


@login_required
def api_template_stats(request):
    """Estadisticas por template desde la Graph API de Meta.

    Query params: ?start=YYYY-MM-DD&end=YYYY-MM-DD (default ultimos 30 dias).

    Devuelve por template: sent / delivered / read / replied + tasas.
    replied = suma de counts en `clicked[]` (quick-reply buttons).
    """
    from bot.whatsapp.meta_stats import get_template_stats

    start_ts, end_ts, start_str, end_str = _parse_stats_date_range(request)
    if start_ts is None:
        return JsonResponse({"ok": False, "error": start_str}, status=400)

    raw = get_template_stats(start_ts, end_ts)
    if raw is None:
        return JsonResponse({
            "ok": False,
            "error": "No se pudo obtener template stats de Meta (ver logs)",
            "start": start_str, "end": end_str,
        }, status=502)

    templates_list = raw.get("templates_list") or []
    analytics = (raw.get("raw_analytics") or {}).get("data") or []

    # Meta estructura: `data[].data_points[]` con template_id + sent/delivered/read/clicked.
    # Agregamos por template_id sumando todos los data_points del rango.
    by_id: dict[str, dict] = {}
    for series in analytics:
        for pt in series.get("data_points") or []:
            tid = pt.get("template_id")
            if not tid:
                continue
            b = by_id.setdefault(tid, {
                "sent": 0, "delivered": 0, "read": 0, "replied": 0,
                "clicked_by_button": {},
            })
            b["sent"] += int(pt.get("sent") or 0)
            b["delivered"] += int(pt.get("delivered") or 0)
            b["read"] += int(pt.get("read") or 0)
            for click in pt.get("clicked") or []:
                count = int(click.get("count") or 0)
                b["replied"] += count
                label = str(click.get("button_content") or "unknown")
                b["clicked_by_button"][label] = (
                    b["clicked_by_button"].get(label, 0) + count
                )

    # Armar respuesta cruzando con el listado de templates (para nombre + categoria).
    templates_out = []
    for tmpl in templates_list:
        tid = tmpl.get("id")
        agg = by_id.get(tid, {
            "sent": 0, "delivered": 0, "read": 0, "replied": 0,
            "clicked_by_button": {},
        })
        sent = agg["sent"]
        delivered = agg["delivered"]
        read = agg["read"]
        replied = agg["replied"]

        def pct(num, den):
            return round((num / den) * 100.0, 1) if den else 0.0

        templates_out.append({
            "id": tid,
            "name": tmpl.get("name"),
            "status": tmpl.get("status"),
            "category": tmpl.get("category"),
            "language": tmpl.get("language"),
            "sent": sent,
            "delivered": delivered,
            "read": read,
            "replied": replied,
            "clicked_by_button": agg["clicked_by_button"],
            "delivery_rate_pct": pct(delivered, sent),
            "read_rate_pct": pct(read, delivered),
            "reply_rate_pct": pct(replied, delivered),
        })

    # Ordenar por sent desc — los mas usados arriba.
    templates_out.sort(key=lambda t: t["sent"], reverse=True)

    return JsonResponse({
        "ok": True,
        "start": start_str, "end": end_str,
        "templates": templates_out,
        # True si el WABA tiene mas de un numero registrado (ver
        # meta_stats._waba_has_multiple_numbers) — Meta no permite filtrar
        # template_analytics por numero, asi que esta lista puede incluir
        # templates/envios de otro bot que comparta la misma cuenta business.
        "shared_waba": raw.get("shared_waba", False),
    })
