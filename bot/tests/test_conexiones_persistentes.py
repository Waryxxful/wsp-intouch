"""Las conexiones a SQL Server tienen que ser persistentes.

Sin `CONN_MAX_AGE`, Django abre y cierra una conexion nueva en cada request y
este bot paga ese costo en CADA mensaje de WhatsApp. Medido el 2026-09-02
abriendo la conexion en procesos frescos contra el SQL Server de QA: 0,26s /
0,51s / 0,57s normalmente, pero intermitentemente 6,04s / 24,17s / 32,80s /
35,27s / 35,93s. Con la conexion ya abierta las queries tardan 0,00s.

Eso produjo un turno real de 85s cuya traza mostraba solo 6,71s de LLM, y un
saludo instantaneo -- que ni siquiera toca el LLM -- de 23s.
Ver docs/PENDIENTES.md #17.

La suite corre con USE_SQLITE=true, asi que estos tests reconstruyen la rama
de SQL Server importando settings con el entorno de produccion: verificar
`settings.DATABASES` tal cual solo probaria la config de sqlite, que es
justamente la que NO tiene el problema.
"""
import importlib
import os
from unittest.mock import patch

from django.test import SimpleTestCase


def _settings_de_produccion():
    """Importa config.settings como lo haria el contenedor real."""
    entorno = {
        "USE_SQLITE": "false", "DB_NAME": "QAIntouch", "DB_HOST": "172.20.21.50",
        "DB_USER": "u", "DB_PASSWORD": "p", "DB_SCHEMA": "cavem",
    }
    with patch.dict(os.environ, entorno):
        modulo = importlib.reload(importlib.import_module("config.settings"))
    # Devolver el modulo recargado con el entorno de test de vuelta seria
    # dejar settings apuntando a SQL Server para el resto de la suite: se
    # recarga otra vez con el entorno original antes de salir.
    return modulo


class ConexionesPersistentesTest(SimpleTestCase):
    def setUp(self):
        self.mod = _settings_de_produccion()
        # Restaurar el modulo con el entorno real de la suite (sqlite).
        self.addCleanup(lambda: importlib.reload(importlib.import_module("config.settings")))

    def test_las_dos_conexiones_de_sql_server_reusan_la_conexion(self):
        for alias in ("default", "qaintouch"):
            with self.subTest(alias=alias):
                config = self.mod.DATABASES[alias]
                self.assertEqual(config["ENGINE"], "mssql")
                # 0 = cerrar al final de cada request, el default de Django y
                # lo que causaba el problema.
                self.assertGreater(config.get("CONN_MAX_AGE", 0), 0)

    def test_las_dos_conexiones_validan_la_conexion_reusada(self):
        # Sin health check, una conexion que el servidor cerro se descubre
        # recien cuando falla una query real, o sea con un error para el
        # contacto. Es lo que hace seguro reusarlas.
        for alias in ("default", "qaintouch"):
            with self.subTest(alias=alias):
                self.assertTrue(self.mod.DATABASES[alias].get("CONN_HEALTH_CHECKS"))

    def test_el_valor_es_configurable_por_entorno(self):
        # Para poder bajarlo sin un deploy si el DBA pide menos conexiones
        # abiertas contra el servidor.
        entorno = {
            "USE_SQLITE": "false", "DB_CONN_MAX_AGE": "120", "DB_NAME": "QAIntouch",
            "DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "p",
        }
        with patch.dict(os.environ, entorno):
            mod = importlib.reload(importlib.import_module("config.settings"))
            self.assertEqual(mod.DATABASES["default"]["CONN_MAX_AGE"], 120)
