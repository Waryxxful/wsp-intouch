# config/settings_docker.py
from .settings import *  # noqa: F401,F403

# wsp_demo es single-tenant (ver utils/tenant_middleware.py) -- no hay
# middleware de tenencia que agregar aqui. DATABASE_ROUTERS se mantiene
# porque TenantDatabaseRouter sigue siendo la fuente de get_current_db()
# que usa admin_panel/views.py para el thread de scraping en background.
#
# LeadsRouter primero: para modelos de la app "leads" gana y fuerza "qaintouch";
# para todo lo demas devuelve None y cae al TenantDatabaseRouter de siempre
# (get_current_db(), que en single-tenant siempre resuelve "default" -- ver
# utils/tenant_middleware.py). Sin este orden, Lead.objects.create() terminaria
# ruteado por get_current_db() en vez de ir siempre a "qaintouch".
DATABASE_ROUTERS = ["leads.db_router.LeadsRouter", "utils.tenant_router.TenantDatabaseRouter"]
DEBUG = False
STATIC_ROOT = BASE_DIR / "staticfiles"
