from datetime import timedelta

from django.utils import timezone
from ninja import NinjaAPI

from bot.models import Conversation, Message, get_setting


class GranCrmCookieAuth:
    """Valida contra request.jwt_payload, seteado por GranCRMAuthMiddleware.
    No es un esquema Bearer real — django-ninja exige un objeto 'auth' con
    __call__(request) -> Any | None; devolvemos el payload para exponerlo."""

    def __call__(self, request):
        return getattr(request, "jwt_payload", None)


api = NinjaAPI(auth=GranCrmCookieAuth(), urls_namespace="bot_api")


@api.get("/metrics")
def metrics(request):
    desde = timezone.now() - timedelta(days=7)
    ultimo = Message.objects.order_by("-created_at").first()
    estado = "activo" if get_setting("bot_global_on", "true") == "true" else "inactivo"
    return {
        "estado": estado,
        "ultima_actividad": ultimo.created_at.isoformat() if ultimo else None,
        "conversaciones_7d": Conversation.objects.filter(created_at__gte=desde).count(),
        "mensajes_7d": Message.objects.filter(created_at__gte=desde).count(),
    }
