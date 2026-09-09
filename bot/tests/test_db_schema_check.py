"""Cobertura del check que compara el schema REAL de SQL Server con el declarado.

Existe por el incidente del 2026-09-02 (docs/PENDIENTES.md #12): Cavem arrancó
con el login de wsp_demo y escribió en `botdemo`, el schema de producción de
Renault/Astara, teniendo `DB_SCHEMA=cavem` en su `.env.docker`. Nada lo
comparaba, porque `OPTIONS["database_schema"]` no es una opción de
mssql-django y se ignoraba en silencio.
"""
from unittest.mock import MagicMock, patch

from django.core.checks import Tags, registry
from django.test import SimpleTestCase, override_settings

from bot.apps import check_db_schema_efectivo


class _CursorFalso:
    def __init__(self, schema=None, revienta=False):
        self._schema, self._revienta = schema, revienta

    def __enter__(self):
        if self._revienta:
            raise RuntimeError("la BD no responde")
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        assert "SCHEMA_NAME()" in sql

    def fetchone(self):
        return (self._schema,)


def _conexion(vendor="microsoft", schema=None, revienta=False):
    conexion = MagicMock()
    conexion.vendor = vendor
    conexion.cursor.return_value = _CursorFalso(schema, revienta)
    return conexion


class CheckDbSchemaEfectivoTest(SimpleTestCase):
    def _correr(self, conexiones, databases=("default",)):
        with patch.dict("bot.apps.connections", conexiones, clear=False):
            return check_db_schema_efectivo(None, databases=list(databases))

    @override_settings(DB_SCHEMA="cavem")
    def test_error_si_el_schema_real_no_es_el_declarado(self):
        errores = self._correr({"default": _conexion(schema="botdemo")})

        self.assertEqual([e.id for e in errores], ["bot.E003"])
        # El mensaje tiene que nombrar los DOS schemas: sin el real, el operador
        # no sabe donde estuvo escribiendo.
        self.assertIn("botdemo", errores[0].msg)
        self.assertIn("cavem", errores[0].msg)

    @override_settings(DB_SCHEMA="cavem")
    def test_sin_error_si_coinciden(self):
        self.assertEqual(self._correr({"default": _conexion(schema="cavem")}), [])

    @override_settings(DB_SCHEMA="cavem")
    def test_revisa_todas_las_conexiones_que_le_pasan(self):
        # El CMD del Dockerfile corre primero `migrate leads --database=qaintouch`:
        # esa conexión tiene que quedar cubierta igual que la default.
        errores = self._correr(
            {"default": _conexion(schema="cavem"), "qaintouch": _conexion(schema="botdemo")},
            databases=("default", "qaintouch"),
        )
        self.assertEqual([e.id for e in errores], ["bot.E003"])
        self.assertIn("qaintouch", errores[0].msg)

    @override_settings(DB_SCHEMA="")
    def test_sin_db_schema_declarada_no_valida_nada(self):
        # dev/test sin la env var: no hay declaración contra la que comparar.
        self.assertEqual(self._correr({"default": _conexion(schema="botdemo")}), [])

    @override_settings(DB_SCHEMA="cavem")
    def test_sobre_sqlite_no_valida_nada(self):
        # sqlite no tiene schemas; es el motor de toda la suite.
        self.assertEqual(
            self._correr({"default": _conexion(vendor="sqlite", schema=None)}), [])

    @override_settings(DB_SCHEMA="cavem")
    def test_una_conexion_caida_no_genera_error_de_configuracion(self):
        # Que reviente el comando real con su propio error: este check no debe
        # disfrazar una BD caída de problema de configuración.
        self.assertEqual(self._correr({"default": _conexion(revienta=True)}), [])

    @override_settings(DB_SCHEMA="cavem")
    def test_no_corre_cuando_no_le_pasan_databases(self):
        # Un `self.check()` pelado (la mayoría de los comandos) pasa databases=None.
        with patch.dict("bot.apps.connections", {"default": _conexion(schema="botdemo")}):
            self.assertEqual(check_db_schema_efectivo(None), [])

    def test_esta_registrado_con_tags_database(self):
        # Es lo que hace que corra en `manage.py migrate`, que es donde ocurrió
        # el daño: migrate sobreescribe get_check_kwargs y pasa
        # databases=[alias]. Con el check sin tag, no recibiría databases.
        registrados = [
            c for c in registry.registry.registered_checks
            if c is check_db_schema_efectivo
        ]
        self.assertEqual(len(registrados), 1)
        self.assertIn(Tags.database, registrados[0].tags)
