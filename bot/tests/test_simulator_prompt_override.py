from unittest.mock import patch

from django.test import TestCase

from bot.models import PromptVersion, save_prompt_version
from bot.simulator.prompt_override import override_prompts
from bot.simulator.runner import correr_escenario


class OverridePromptsTest(TestCase):
    def setUp(self):
        save_prompt_version("global", "PROMPT GLOBAL REAL")
        save_prompt_version("custom:ventas", "PROMPT VENTAS REAL")

    def test_sin_override_devuelve_el_prompt_activo(self):
        with override_prompts(None):
            from bot.models import get_active_prompt
            self.assertEqual(get_active_prompt("custom:ventas"), "PROMPT VENTAS REAL")

    def test_override_vacio_devuelve_el_prompt_activo(self):
        with override_prompts({}):
            from bot.models import get_active_prompt
            self.assertEqual(get_active_prompt("custom:ventas"), "PROMPT VENTAS REAL")

    def test_con_override_devuelve_el_candidato_para_ese_agente(self):
        with override_prompts({"custom:ventas": "PROMPT VENTAS CANDIDATO"}):
            from bot.models import get_active_prompt
            self.assertEqual(get_active_prompt("custom:ventas"), "PROMPT VENTAS CANDIDATO")

    def test_los_agentes_sin_override_siguen_leyendo_el_activo(self):
        with override_prompts({"custom:ventas": "PROMPT VENTAS CANDIDATO"}):
            from bot.models import get_active_prompt
            self.assertEqual(get_active_prompt("global"), "PROMPT GLOBAL REAL")

    def test_el_override_no_persiste_despues_del_bloque(self):
        with override_prompts({"custom:ventas": "PROMPT VENTAS CANDIDATO"}):
            pass
        from bot.models import get_active_prompt
        self.assertEqual(get_active_prompt("custom:ventas"), "PROMPT VENTAS REAL")

    def test_el_override_no_toca_ninguna_fila_de_promptversion(self):
        antes = list(
            PromptVersion.objects.filter(agente="custom:ventas").values_list("id", "activa", "prompt")
        )
        with override_prompts({"custom:ventas": "PROMPT VENTAS CANDIDATO"}):
            from bot.models import get_active_prompt
            get_active_prompt("custom:ventas")
        despues = list(
            PromptVersion.objects.filter(agente="custom:ventas").values_list("id", "activa", "prompt")
        )
        self.assertEqual(antes, despues)

    def test_el_import_tardio_de_los_agentes_tambien_ve_el_override(self):
        # Los agentes hacen `from bot.models import get_active_prompt` DENTRO
        # de la funcion, no a nivel de modulo -- este test protege que el
        # mecanismo siga funcionando si alguien cambia esa forma de importar.
        def como_lo_hace_un_agente():
            from bot.models import get_active_prompt as fn
            return fn("custom:ventas")

        with override_prompts({"custom:ventas": "PROMPT VENTAS CANDIDATO"}):
            self.assertEqual(como_lo_hace_un_agente(), "PROMPT VENTAS CANDIDATO")


class CorrerEscenarioConOverrideTest(TestCase):
    def setUp(self):
        save_prompt_version("custom:ventas", "PROMPT VENTAS REAL")

    def _correr(self, item_input):
        visto = {}

        def fake_create_app(persona, mock_wa):
            from bot.models import get_active_prompt as fn
            visto["prompt"] = fn("custom:ventas")
            return object(), {}

        with patch("bot.simulator.runner.create_app", side_effect=fake_create_app), \
             patch("bot.simulator.runner.crear_cliente_simulado"), \
             patch("bot.simulator.runner.run_multiturn_simulation"):
            resultado = correr_escenario(item_input)
        return visto, resultado

    def test_con_prompts_override_la_simulacion_ve_el_candidato(self):
        visto, _ = self._correr({
            "persona": "p", "objetivo": "o",
            "prompts_override": {"custom:ventas": "PROMPT VENTAS CANDIDATO"},
        })
        self.assertEqual(visto["prompt"], "PROMPT VENTAS CANDIDATO")

    def test_sin_prompts_override_la_simulacion_ve_el_prompt_activo(self):
        visto, _ = self._correr({"persona": "p", "objetivo": "o"})
        self.assertEqual(visto["prompt"], "PROMPT VENTAS REAL")

    def test_el_override_no_cambia_la_forma_del_resultado(self):
        _, resultado = self._correr({
            "persona": "p", "objetivo": "o",
            "prompts_override": {"custom:ventas": "PROMPT VENTAS CANDIDATO"},
        })
        self.assertFalse(resultado["paso"])
        self.assertEqual(resultado["fallos_de_codigo"], ["conversacion_vacia"])
