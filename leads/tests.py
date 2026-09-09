import copy
import os
import tempfile

from django.core.management import call_command
from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper as SqliteWrapper
from django.test import SimpleTestCase, TestCase

from leads.db_router import LeadsRouter
from leads.models import Lead


class LeadModelTest(TestCase):
    databases = {"qaintouch"}

    def test_str_incluye_nombre_y_rut(self):
        lead = Lead.objects.create(
            rut="11.111.111-1", nombre="Juan Perez", telefono="+56911112222",
            razon_interes="quiere hacer un test drive",
        )
        self.assertEqual(str(lead), "Juan Perez (11.111.111-1)")

    def test_ordering_mas_reciente_primero(self):
        primero = Lead.objects.create(rut="1", nombre="A", telefono="1", razon_interes="x")
        segundo = Lead.objects.create(rut="2", nombre="B", telefono="2", razon_interes="y")
        self.assertEqual(list(Lead.objects.all()), [segundo, primero])


class LeadsRouterTest(TestCase):
    databases = {"default", "qaintouch"}

    def test_db_for_read_y_write_de_lead_es_qaintouch(self):
        router = LeadsRouter()
        self.assertEqual(router.db_for_read(Lead), "qaintouch")
        self.assertEqual(router.db_for_write(Lead), "qaintouch")

    def test_db_for_read_de_otra_app_no_se_fuerza(self):
        from bot.models import Setting
        router = LeadsRouter()
        self.assertIsNone(router.db_for_read(Setting))
        self.assertIsNone(router.db_for_write(Setting))

    def test_allow_migrate_leads_solo_permite_qaintouch(self):
        router = LeadsRouter()
        self.assertTrue(router.allow_migrate("qaintouch", "leads"))
        self.assertFalse(router.allow_migrate("default", "leads"))

    def test_allow_migrate_otra_app_no_permite_qaintouch_y_no_opina_de_default(self):
        router = LeadsRouter()
        self.assertFalse(router.allow_migrate("qaintouch", "bot"))
        self.assertIsNone(router.allow_migrate("default", "bot"))

    def test_objects_create_sin_using_explicito_escribe_en_qaintouch_no_en_default(self):
        lead = Lead.objects.create(rut="1", nombre="A", telefono="1", razon_interes="x")
        self.assertTrue(Lead.objects.using("qaintouch").filter(pk=lead.pk).exists())
        # No se usa Lead.objects.using("default").filter(...).exists() porque la tabla
        # leads_lead nunca se crea en "default" (allow_migrate la bloquea), y consultarla
        # ahi lanza OperationalError/"invalid object name" en vez de devolver una queryset
        # vacia -- en cualquier backend, no solo sqlite. Verificamos la ausencia de la
        # tabla directamente, que es la garantia real que este test busca.
        self.assertNotIn("leads_lead", connections["default"].introspection.table_names())

    def test_migrar_default_no_crea_tabla_de_leads(self):
        call_command("migrate", "leads", database="default", verbosity=0)
        self.assertNotIn("leads_lead", connections["default"].introspection.table_names())

    def test_migrar_qaintouch_no_toca_tablas_de_bot(self):
        call_command("migrate", "bot", database="qaintouch", verbosity=0)
        self.assertNotIn("bot_conversation", connections["qaintouch"].introspection.table_names())

    def test_qaintouch_ya_tiene_la_tabla_de_leads(self):
        self.assertIn("leads_lead", connections["qaintouch"].introspection.table_names())


class DockerfileMigrateOrderRegressionTest(SimpleTestCase):
    """Regresion para el bug critico detectado en la revision final del plan
    2026-08-06-lead-capture-qaintouch: en QA real, "default" y "qaintouch"
    apuntan a la MISMA base de datos fisica en SQL Server (mismo NAME) y por
    lo tanto comparten UNA sola tabla django_migrations. Django registra una
    migracion como "aplicada" ahi incluso cuando todas sus operaciones fueron
    saltadas por allow_migrate (comportamiento documentado del executor: la
    fila de bookkeeping se escribe siempre, el skip solo afecta las
    operaciones de la migracion). El test-runner normal NO puede reproducir
    esto: bajo USE_SQLITE=true cada alias usa un archivo .sqlite3 DISTINTO
    (ver settings.py), asi que aqui se inyectan manualmente dos wrappers de
    sqlite3 ("default"/"qaintouch") apuntando al MISMO archivo temporal para
    forzar el escenario real de QA y ejercitar el comando de migracion tal
    cual queda en el Dockerfile."""

    databases = {"default", "qaintouch"}

    def _shared_wrapper(self, path, alias):
        settings_dict = copy.deepcopy(connections.databases["default"])
        settings_dict["NAME"] = path
        return SqliteWrapper(settings_dict, alias)

    def _instalar_conexiones_compartidas(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(path)  # arrancar desde un archivo realmente vacio

        originales = {}
        for alias in ("default", "qaintouch"):
            try:
                originales[alias] = connections[alias]
            except Exception:
                originales[alias] = None

        connections["default"] = self._shared_wrapper(path, "default")
        connections["qaintouch"] = self._shared_wrapper(path, "qaintouch")

        def _restaurar():
            for alias in ("default", "qaintouch"):
                try:
                    connections[alias].close()
                except Exception:
                    pass
                del connections[alias]
            if os.path.exists(path):
                os.remove(path)

        self.addCleanup(_restaurar)
        return path

    def test_orden_viejo_default_completo_primero_deja_leads_sin_tabla_real(self):
        # RED: reproduce el bug tal cual estaba antes del fix -- "migrate"
        # (default, grafo completo) primero, despues "migrate --database=qaintouch"
        # (tambien grafo completo, sin acotar por app).
        self._instalar_conexiones_compartidas()

        call_command("migrate", database="default", verbosity=0, interactive=False)
        call_command("migrate", database="qaintouch", verbosity=0, interactive=False)

        tablas_qaintouch = connections["qaintouch"].introspection.table_names()
        self.assertNotIn("leads_lead", tablas_qaintouch)  # bug: nunca se crea

        cursor = connections["qaintouch"].cursor()
        cursor.execute("SELECT COUNT(*) FROM django_migrations WHERE app = 'leads'")
        # La fila de bookkeeping SI existe aunque la tabla real nunca se creo --
        # esta es la trampa: el proximo `migrate` la va a ver "aplicada" para
        # siempre y no va a reintentar crearla.
        self.assertEqual(cursor.fetchone()[0], 1)

    def test_orden_invertido_sin_acotar_por_app_rompe_las_otras_apps(self):
        # RED (variante confirmada por la revision): invertir el orden SIN
        # acotar por app_label es peor, no mejor -- migra "qaintouch" completo
        # primero (allow_migrate bloquea crear las tablas de bot/auth/contenttypes/etc
        # ahi, pero la fila de bookkeeping en django_migrations SI se escribe para
        # todas). Cuando el segundo `migrate` (default, grafo completo) corre, ve
        # esas migraciones ya "aplicadas" en la tabla compartida y tampoco crea sus
        # tablas reales -- ni siquiera django_content_type, lo que hace que el
        # propio post_migrate signal de Django (create_permissions/
        # create_contenttypes) reviente con un OperationalError al intentar leerla.
        # Es decir: no solo faltan tablas (bot_conversation, auth_user,
        # django_session, etc.) -- el comando ni siquiera termina limpio.
        self._instalar_conexiones_compartidas()

        call_command("migrate", database="qaintouch", verbosity=0, interactive=False)
        from django.db.utils import OperationalError
        with self.assertRaises(OperationalError):
            call_command("migrate", database="default", verbosity=0, interactive=False)

        tablas_default = connections["default"].introspection.table_names()
        self.assertNotIn("bot_conversation", tablas_default)
        self.assertNotIn("auth_user", tablas_default)
        self.assertNotIn("django_session", tablas_default)

    def test_orden_nuevo_leads_acotado_primero_deja_leads_y_bot_presentes(self):
        # GREEN: el orden real del Dockerfile despues del fix -- "migrate leads
        # --database=qaintouch" (acotado por app) primero, despues "migrate"
        # (default, grafo completo) segundo.
        self._instalar_conexiones_compartidas()

        call_command("migrate", "leads", database="qaintouch", verbosity=0, interactive=False)
        call_command("migrate", database="default", verbosity=0, interactive=False)

        self.assertIn("leads_lead", connections["qaintouch"].introspection.table_names())
        tablas_default = connections["default"].introspection.table_names()
        self.assertIn("bot_conversation", tablas_default)
        self.assertIn("auth_user", tablas_default)
        self.assertIn("django_session", tablas_default)


class SettingsDockerRouterCompuestoTest(TestCase):
    def test_settings_docker_antepone_leads_router_al_tenant_router(self):
        from config import settings_docker
        self.assertEqual(
            settings_docker.DATABASE_ROUTERS,
            ["leads.db_router.LeadsRouter", "utils.tenant_router.TenantDatabaseRouter"],
        )
