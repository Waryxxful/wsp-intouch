from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from bot.simulator.models import CorridaDePrueba, EscenarioDePrueba, ResultadoDeEscenario


class TestBotConversationCommandTest(TestCase):
    def setUp(self):
        self.e1 = EscenarioDePrueba.objects.create(nombre="e1", persona="x", objetivo="y", criterios=["z"])

    def test_escenario_inexistente_lanza_command_error(self):
        with self.assertRaises(CommandError):
            call_command("test_bot_conversation", "--scenario", "no-existe", stdout=StringIO())

    def test_disparada_por_consola(self):
        corrida = CorridaDePrueba.objects.create(disparada_por="consola", nombre_escenario_filtro="e1")

        def _iniciar(nombre_escenario=None, disparada_por="consola"):
            self.assertEqual(disparada_por, "consola")
            return corrida

        with self._patch_runner(iniciar=_iniciar, ejecutar=lambda c: None):
            call_command("test_bot_conversation", "--scenario", "e1", stdout=StringIO())

    def test_imprime_resumen_pass_fail_por_escenario(self):
        corrida = CorridaDePrueba.objects.create(disparada_por="consola")
        ResultadoDeEscenario.objects.create(
            corrida=corrida, escenario=self.e1, paso=True, fallos=[], transcript="x",
        )
        e2 = EscenarioDePrueba.objects.create(nombre="e2", persona="a", objetivo="b", criterios=["c"])
        ResultadoDeEscenario.objects.create(
            corrida=corrida, escenario=e2, paso=False, fallos=["algo fallo"], transcript="y",
        )
        out = StringIO()

        with self._patch_runner(iniciar=lambda **k: corrida, ejecutar=lambda c: None):
            call_command("test_bot_conversation", stdout=out)

        salida = out.getvalue()
        self.assertIn("1/2", salida)
        self.assertIn("e1", salida)
        self.assertIn("e2", salida)
        self.assertIn("algo fallo", salida)

    def _patch_runner(self, iniciar, ejecutar):
        from unittest.mock import patch
        return patch.multiple(
            "bot.management.commands.test_bot_conversation",
            iniciar_corrida=iniciar, ejecutar_corrida=ejecutar,
        )
