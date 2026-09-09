"""Cobertura del seed de la demo, centrada en la publicacion de prompts.

El prompt global se publica aca (y no a mano) porque la BD que importa es la
del deploy, no la sqlite de desarrollo: si la publicacion no es parte del
seed, el bot desplegado arranca sin PromptVersion global y la regla de
comportamiento deja de ser editable desde el panel.
"""
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG, SYSTEM_PROMPT, get_effective_global_prompt
from bot.models import PromptVersion, get_active_prompt


class SeedCavemPromptsTest(TestCase):
    def test_publica_el_prompt_global_y_el_de_ventas(self):
        call_command("seed_cavem")

        self.assertEqual(get_active_prompt(GLOBAL_PROMPT_SLUG), SYSTEM_PROMPT)
        self.assertTrue(get_active_prompt("custom:ventas"))
        # cliente=CLIENTE_ACTIVO, no "cavem" fijo: save_prompt_version estampa
        # el cliente activo y la suite corre como renault (ver CLAUDE.md).
        self.assertEqual(
            PromptVersion.objects.get(agente=GLOBAL_PROMPT_SLUG, activa=True).cliente,
            settings.CLIENTE_ACTIVO,
        )

    def test_el_prompt_publicado_es_el_que_usa_el_grafo(self):
        call_command("seed_cavem")

        # get_effective_global_prompt() antepone este texto al prompt de cada
        # especialista (bot/flow/graph.py:491): si la version publicada no
        # coincide con el default del codigo, el bot cambia de comportamiento
        # sin que nadie edite nada.
        self.assertEqual(get_effective_global_prompt(), SYSTEM_PROMPT)

    def test_no_pisa_una_edicion_hecha_desde_el_panel(self):
        call_command("seed_cavem")
        PromptVersion.objects.filter(agente=GLOBAL_PROMPT_SLUG, activa=True).update(
            prompt="editado desde el panel")

        call_command("seed_cavem")

        self.assertEqual(get_active_prompt(GLOBAL_PROMPT_SLUG), "editado desde el panel")

    def test_republicar_prompt_repone_el_default_del_codigo(self):
        call_command("seed_cavem")
        PromptVersion.objects.filter(agente=GLOBAL_PROMPT_SLUG, activa=True).update(
            prompt="editado desde el panel")

        call_command("seed_cavem", republicar_prompt=True)

        self.assertEqual(get_active_prompt(GLOBAL_PROMPT_SLUG), SYSTEM_PROMPT)
        # La vieja queda desactivada, no borrada: el historial de versiones es
        # lo que permite volver atras desde el panel.
        self.assertEqual(PromptVersion.objects.filter(agente=GLOBAL_PROMPT_SLUG).count(), 2)
        self.assertEqual(
            PromptVersion.objects.filter(agente=GLOBAL_PROMPT_SLUG, activa=True).count(), 1)
