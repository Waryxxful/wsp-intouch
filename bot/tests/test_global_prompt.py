from django.test import TestCase
from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG, SYSTEM_PROMPT, get_effective_global_prompt


class GlobalPromptTest(TestCase):
    def test_sin_override_devuelve_el_default(self):
        self.assertEqual(get_effective_global_prompt(), SYSTEM_PROMPT)

    def test_con_version_activa_la_devuelve(self):
        from bot.models import save_prompt_version
        save_prompt_version(GLOBAL_PROMPT_SLUG, "prompt global de prueba")
        self.assertEqual(get_effective_global_prompt(), "prompt global de prueba")


class ReglasDeCalidadDelPromptTest(TestCase):
    """Reglas heredadas del prompt de Cavem (bot automotriz), revisadas para
    InTouch el 2026-09-09.

    Se sacaron `test_manda_copiar_el_precio_formateado` y
    `test_prohibe_preguntar_entre_una_sola_sucursal`: probaban reglas de
    dominio automotriz (precios de catálogo, sucursales) que este prompt no
    tiene y que además contradiría -- la sección NUNCA INVENTES de
    `SYSTEM_PROMPT` le prohíbe explícitamente al bot afirmar un precio. No es
    cobertura que falte, es dominio que este bot no tiene.

    Queda `test_prohibe_el_voseo_con_las_formas_concretas`: la regla anti-
    voseo sí aplica igual, sólo cambian las formas concretas que enumera.
    """

    def test_prohibe_el_voseo_con_las_formas_concretas(self):
        # La regla "nunca vosees" no alcanza en abstracto: la ley del stack
        # es que el modelo imita el corpus del prompt, no sólo lo obedece
        # (ver feedback_el_modelo_imita_su_corpus.md -- un prompt hermano
        # publicó su regla anti-voseo con el ejemplo "cuentame" sin tilde y
        # el bot le escribió "cuentame" a un contacto real seis horas
        # después). Por eso importa que la sección IDIOMA Y ORTOGRAFÍA
        # enumere pares concretos tuteo/voseo, no sólo la advertencia
        # general.
        from bot.flow.global_prompt import SYSTEM_PROMPT
        for forma in ("cuéntame", "quieres", "necesitas", "contame", "querés", "necesitás"):
            with self.subTest(forma=forma):
                self.assertIn(forma, SYSTEM_PROMPT)
        self.assertIn("Nunca vosees", SYSTEM_PROMPT)
