"""Cobertura de `seed_intouch`, centrada en la publicación de prompts.

Reapuntado desde `test_seed_cavem.py` (comando borrado: el de este bot es
`seed_intouch`, con el flag `--republicar-prompt`). El motivo de fondo sigue
siendo el mismo: si la publicación no queda cubierta por un test, el bot
desplegado puede arrancar sin `PromptVersion` para el prompt global o el del
especialista "comercial", y la regla de comportamiento deja de ser editable
desde el panel.

A diferencia de `seed_cavem`, republicar prompts en `seed_intouch` está
detrás de `--republicar-prompt` a propósito: el prompt activo es estado de
producción y el comando no lo toca si no se lo pide explícitamente (ver el
docstring de `_republicar_prompts` en
`bot/management/commands/seed_intouch.py`). Por eso los tests llaman al
comando con `republicar_prompt=True` para probar la publicación, y sin el
flag para probar que una corrida normal del seed no pisa nada.
"""
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG, SYSTEM_PROMPT, get_effective_global_prompt
from bot.models import PromptVersion, get_active_prompt

FIXTURE_COMERCIAL = (
    Path(__file__).resolve().parents[1] / "fixtures" / "prompt_comercial.md"
).read_text(encoding="utf-8")


class SeedIntouchPromptsTest(TestCase):
    def test_publica_el_prompt_global_y_el_del_especialista_comercial(self):
        call_command("seed_intouch", republicar_prompt=True)

        self.assertEqual(get_active_prompt(GLOBAL_PROMPT_SLUG), SYSTEM_PROMPT)
        self.assertEqual(get_active_prompt("comercial"), FIXTURE_COMERCIAL)
        # cliente=CLIENTE_ACTIVO, no "intouch" fijo: save_prompt_version
        # estampa el cliente activo y la suite corre como renault (ver
        # CLAUDE.md).
        self.assertEqual(
            PromptVersion.objects.get(agente=GLOBAL_PROMPT_SLUG, activa=True).cliente,
            settings.CLIENTE_ACTIVO,
        )

    def test_el_prompt_publicado_es_el_que_usa_el_grafo(self):
        call_command("seed_intouch", republicar_prompt=True)

        # get_effective_global_prompt() antepone este texto al prompt del
        # especialista "comercial" (bot/flow/graph.py): si la versión
        # publicada no coincide con el default del código, el bot cambia de
        # comportamiento sin que nadie edite nada.
        self.assertEqual(get_effective_global_prompt(), SYSTEM_PROMPT)

    def test_una_corrida_normal_no_pisa_una_edicion_hecha_desde_el_panel(self):
        call_command("seed_intouch", republicar_prompt=True)
        PromptVersion.objects.filter(agente=GLOBAL_PROMPT_SLUG, activa=True).update(
            prompt="editado desde el panel")

        # Sin --republicar-prompt, el seed siembra el catálogo pero no toca
        # prompts: es la salvaguarda contra pisar una edición de producción
        # en cada deploy.
        call_command("seed_intouch")

        self.assertEqual(get_active_prompt(GLOBAL_PROMPT_SLUG), "editado desde el panel")

    def test_republicar_prompt_repone_el_default_del_codigo(self):
        call_command("seed_intouch", republicar_prompt=True)
        PromptVersion.objects.filter(agente=GLOBAL_PROMPT_SLUG, activa=True).update(
            prompt="editado desde el panel")

        call_command("seed_intouch", republicar_prompt=True)

        self.assertEqual(get_active_prompt(GLOBAL_PROMPT_SLUG), SYSTEM_PROMPT)
        # La vieja queda desactivada, no borrada: el historial de versiones
        # es lo que permite volver atrás desde el panel.
        self.assertEqual(PromptVersion.objects.filter(agente=GLOBAL_PROMPT_SLUG).count(), 2)
        self.assertEqual(
            PromptVersion.objects.filter(agente=GLOBAL_PROMPT_SLUG, activa=True).count(), 1)
