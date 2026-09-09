import importlib

from django.apps import apps as global_apps
from django.test import TestCase, TransactionTestCase

from bot.models import PromptVersion, Setting


class PromptVersionBackfillTest(TestCase):
    def _backfill(self):
        migration = importlib.import_module("bot.migrations.0005_promptversion")
        migration.backfill_prompt_versions(global_apps, None)

    def test_copia_override_de_setting_a_promptversion(self):
        Setting.objects.create(key="prompt_override_agendamiento", value="override viejo")
        self._backfill()
        version = PromptVersion.todos_los_clientes.get(agente="agendamiento")
        self.assertEqual(version.prompt, "override viejo")
        self.assertTrue(version.activa)

    def test_ignora_override_vacio(self):
        Setting.objects.create(key="prompt_override_confirmacion", value="")
        self._backfill()
        self.assertFalse(PromptVersion.todos_los_clientes.filter(agente="confirmacion").exists())

    def test_ignora_agente_sin_setting_guardado(self):
        self._backfill()
        self.assertFalse(PromptVersion.todos_los_clientes.filter(agente="faq").exists())


class PromptVersionBackfillCustomSpecialistMigrationTest(TransactionTestCase):
    # TransactionTestCase (no TestCase) a proposito: este test usa
    # MigrationExecutor para migrar la BD hacia atras hasta 0004 (antes de
    # que existiera PromptVersion) y luego hacia adelante otra vez, para
    # poder crear un CustomSpecialist con su viejo campo `prompt` (eliminado
    # en 0006) y asi simular el estado real que el backfill de 0005 tuvo que
    # cubrir en produccion. Migrar hacia atras hace DDL real (DROP/ALTER de
    # tablas) y sqlite no permite deshabilitar foreign key checks en medio de
    # una transaccion — que es justo lo que TestCase envuelve a cada test.
    # TransactionTestCase corre en autocommit (y trunca tablas entre tests),
    # asi que el executor puede hacer sus propias transacciones internas.
    def test_backfill_copia_prompt_de_customspecialist_en_estado_historico(self):
        from django.db.migrations.executor import MigrationExecutor
        from django.db import connection

        executor = MigrationExecutor(connection)
        try:
            executor.migrate([("bot", "0004_customspecialist")])
            old_apps = executor.loader.project_state(("bot", "0004_customspecialist")).apps
            OldCustomSpecialist = old_apps.get_model("bot", "CustomSpecialist")
            OldCustomSpecialist.objects.create(
                slug="envios", label="Envíos", descripcion="d", prompt="prompt historico",
            )

            executor.loader.build_graph()
            executor.migrate([("bot", "0005_promptversion")])

            # Use historical app state to avoid cliente field that doesn't exist in 0005
            old_apps = executor.loader.project_state(("bot", "0005_promptversion")).apps
            OldPromptVersion = old_apps.get_model("bot", "PromptVersion")
            self.assertEqual(
                OldPromptVersion.objects.get(agente="custom:envios").prompt, "prompt historico",
            )
        finally:
            # Vuelve a la migracion final del proyecto (incluyendo el resto
            # de las apps) para no dejar la BD de test en un estado
            # intermedio para el resto de la suite, corra o no el assert.
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())
