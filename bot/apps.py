import os

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, Tags, register
from django.db import connections


@register()
def check_cliente_activo(app_configs, **kwargs):
    """Falla ruidosamente si settings.CLIENTE_ACTIVO no es un cliente valido.

    Sin esto, un typo (Astara, "astara " con espacio, astera) hace que los 6
    managers filtrados por cliente (_ClienteActivoManager en bot/models.py)
    devuelvan querysets vacios en silencio -- el bot responde "no tengo esa
    informacion" para siempre y los prompts caen al default sin ningun error.
    """
    from bot.models import CLIENTE_CHOICES

    errors = []
    valores_validos = [clave for clave, _ in CLIENTE_CHOICES]
    if settings.CLIENTE_ACTIVO not in valores_validos:
        errors.append(
            Error(
                f"CLIENTE_ACTIVO={settings.CLIENTE_ACTIVO!r} no es un cliente valido.",
                hint=(
                    f"Valores validos: {valores_validos}. Revisa la variable de "
                    "entorno CLIENTE_ACTIVO (ver .env.docker.example)."
                ),
                id="bot.E001",
            )
        )
    return errors


@register()
def check_rag_schema_coincide_con_cliente_activo(app_configs, **kwargs):
    """Falla ruidosamente si RAG_SCHEMA no coincide con CLIENTE_ACTIVO.

    Son dos variables de entorno independientes -- nada mas las mantiene
    sincronizadas (ver bot/rag/cliente.py::get_supabase_client, que resuelve
    el schema del RETRIEVAL en vivo del bot por RAG_SCHEMA, no por
    CLIENTE_ACTIVO). Sin este check, flippear CLIENTE_ACTIVO para probar el
    otro cliente -- o revertirlo despues -- y olvidar RAG_SCHEMA deja al bot
    respondiendo con los datos de un cliente pero buscando en la base de
    conocimiento del otro, sin ningun error visible. Mismo tipo de mezcla
    silenciosa que ya paso una vez del lado del scraping (297 chunks de
    Astara en el schema renault, ver docs/PENDIENTES.md). Sin RAG_SCHEMA
    seteada (dev/test sin la env var) no hay nada que validar.
    """
    errors = []
    rag_schema = os.environ.get("RAG_SCHEMA")
    if rag_schema and rag_schema != settings.CLIENTE_ACTIVO:
        errors.append(
            Error(
                f"RAG_SCHEMA={rag_schema!r} no coincide con CLIENTE_ACTIVO={settings.CLIENTE_ACTIVO!r}.",
                hint=(
                    "El retrieval en vivo del bot busca en el schema de Supabase que "
                    "diga RAG_SCHEMA -- si no coincide con CLIENTE_ACTIVO, el bot "
                    "responde con los datos de un cliente pero busca conocimiento del "
                    "otro. Iguala ambas variables de entorno antes de arrancar."
                ),
                id="bot.E002",
            )
        )
    return errors


@register(Tags.database)
def check_db_schema_efectivo(app_configs, databases=None, **kwargs):
    """Falla ruidosamente si el schema REAL de SQL Server no es el declarado.

    El schema donde escribe este bot no lo decide Django: lo decide el
    DEFAULT_SCHEMA del login SQL. `settings.DB_SCHEMA` es solo la declaracion
    del deploy (ver el comentario largo en config/settings.py), y hasta el
    2026-09-02 nada las comparaba: Cavem arranco con el login de wsp_demo y
    escribio en `botdemo`, el schema de produccion de Renault/Astara, con
    DB_SCHEMA=cavem puesto en su .env.docker. El `migrate` del CMD del
    Dockerfile aplico 5 migraciones ahi antes de que alguien lo viera.
    Ver docs/PENDIENTES.md #12.

    Registrado con `Tags.database` a proposito: eso hace que corra en cada
    `manage.py migrate` (que pasa `databases=[alias]` via get_check_kwargs) y
    NO en un `self.check()` pelado. Importa porque el dano lo hace el migrate
    del arranque, antes de cualquier request -- gunicorn no ejecuta checks. Por
    lo mismo, esto no debe moverse a un hook de request ni a una view.

    Sin DB_SCHEMA declarada (dev/test) no hay nada que validar. Sobre sqlite
    tampoco: no tiene schemas. Y si la conexion falla, el check se calla -- que
    reviente el comando real con su propio error, no este chequeo enmascarando
    una BD caida como un problema de configuracion.
    """
    if not databases or not settings.DB_SCHEMA:
        return []

    errors = []
    for alias in databases:
        conexion = connections[alias]
        if conexion.vendor != "microsoft":
            continue
        try:
            with conexion.cursor() as cursor:
                cursor.execute("select SCHEMA_NAME()")
                efectivo = cursor.fetchone()[0]
        except Exception:
            continue
        if efectivo != settings.DB_SCHEMA:
            errors.append(
                Error(
                    f"La conexion {alias!r} escribe en el schema {efectivo!r}, "
                    f"pero DB_SCHEMA declara {settings.DB_SCHEMA!r}.",
                    hint=(
                        "El schema lo fija el DEFAULT_SCHEMA del login SQL, no "
                        "DB_SCHEMA ni OPTIONS. Revisa DB_USER en .env.docker: con un "
                        "login de otro bot, este bot escribe en la base de ESE bot "
                        "(paso el 2026-09-02, ver docs/PENDIENTES.md #12). Pedile al "
                        "DBA un login con DEFAULT_SCHEMA correcto, o corrige DB_SCHEMA "
                        "si el schema real es el que corresponde."
                    ),
                    id="bot.E003",
                )
            )
    return errors


class BotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bot"

    def ready(self):
        from utils.dios_registration import (
            notify_schema_updated, register_notify_types, register_with_dios,
        )
        from bot.scraping.scheduler import start_scheduler
        from bot.seguimiento import start_scheduler as start_seguimiento
        import bot.signals  # noqa: F401
        register_with_dios()
        notify_schema_updated()
        register_notify_types()
        start_scheduler()
        start_seguimiento()
