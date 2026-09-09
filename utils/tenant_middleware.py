import threading

_local = threading.local()

# wsp_demo es single-tenant por decisión de producto (2026-07-30, ver
# docs-repo/contexto-general/operacion.md 2.4.4): tiene un solo numero de
# WhatsApp, y el webhook de Meta que escribe las conversaciones reales no
# lleva ningun JWT/tenant -- toda la data del bot vive SIEMPRE en la BD
# "default" (DEV: DevIntouch, QA: DevIntouchQA), sin importar que cuenta
# GranCRM este viendo el panel. Enrutar por el db_name del JWT (como hacen
# wsp_platform u otras apps con datos de negocio reales por cliente) solo
# mostraba un dashboard vacio -- la BD dedicada de la cuenta nunca tiene
# las conversaciones, que estan en "default".
#
# get_current_db()/set_current_db() se mantienen porque admin_panel/views.py
# los usa para propagar la BD activa a un thread nuevo (api_scraping_run,
# 2026-07-21) -- sin el middleware de tenencia, get_current_db() ya devuelve
# "default" siempre via el fallback de getattr, asi que la propagacion sigue
# siendo un no-op correcto.


def get_current_db() -> str:
    return getattr(_local, "db_name", "default")


def set_current_db(db_name: str) -> None:
    _local.db_name = db_name
