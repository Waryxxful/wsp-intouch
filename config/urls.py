from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from django.views.static import serve as serve_static
from bot.whatsapp import webhooks
from bot.api import api as bot_api


def healthz(request):
    # Sin auth a proposito -- lo pega el HEALTHCHECK del Dockerfile/
    # docker-compose desde dentro del propio contenedor, no un cliente
    # externo. SELECT 1 real (no solo "el proceso responde") para que un
    # gunicorn vivo pero con la BD caida tambien se marque unhealthy.
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"ok": True})


def serve_media(request, path):
    # No usar {"document_root": settings.MEDIA_ROOT} como kwarg fijo del
    # path(): ese dict se evalua una sola vez al importar este modulo, asi
    # que congela el valor original de MEDIA_ROOT para siempre y rompe
    # override_settings(MEDIA_ROOT=...) en los tests (y cualquier cambio de
    # settings en runtime). Envolver la vista permite leer settings.MEDIA_ROOT
    # en cada request.
    return serve_static(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("django-admin/", admin.site.urls),
    path("webhook", webhooks.webhook, name="webhook"),
    path("internal/webhook", webhooks.internal_webhook, name="internal_webhook"),
    path("demo/media/<path:path>", serve_media),
    path("demo/", include("admin_panel.urls")),
    path("api/", bot_api.urls),
]
