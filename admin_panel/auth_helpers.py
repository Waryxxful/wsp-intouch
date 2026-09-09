"""
Auth helpers para el panel admin.

En produccion (DEBUG=False, detras de DIOS): el usuario llega autenticado
via cookie JWT grancrm_session, decodificada por GranCRMAuthMiddleware
(grancrm_auth/middleware.py, ya incluido en el skeleton).

En dev local (DEBUG=True, sin DIOS levantado): se acepta tambien sesion
Django normal (via /django-admin/, con un superuser creado a mano) para no
obligar a levantar el orquestador solo para tocar el panel.
"""
from functools import wraps

from django.conf import settings
from django.shortcuts import redirect


def grancrm_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if getattr(request, "jwt_payload", None):
            return view_func(request, *args, **kwargs)
        if settings.DEBUG and request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
        return redirect(settings.LOGIN_URL)
    return wrapper
