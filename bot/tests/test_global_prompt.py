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
    """Las tres reglas agregadas el 2026-09-03 tras la revisión del vendedor.

    Las tres salieron de fallas reales en conversaciones de producción
    (docs/PENDIENTES.md #18, #20b y #25). Este test no prueba que el LLM las
    obedezca —eso se verificó aparte contra el modelo real— sino que no
    desaparezcan del prompt en una edición futura.
    """

    def test_prohibe_el_voseo_con_las_formas_concretas(self):
        # La regla "nunca vos" ya existía y el modelo la violaba igual
        # ("preferís", "querés", "pensás"). Hicieron falta las formas
        # explícitas.
        from bot.flow.global_prompt import SYSTEM_PROMPT
        for forma in ("prefieres", "quieres", "tienes", "puedes", "piensas"):
            with self.subTest(forma=forma):
                self.assertIn(forma, SYSTEM_PROMPT)
        self.assertIn("voseantes", SYSTEM_PROMPT)

    def test_manda_copiar_el_precio_formateado(self):
        # El bot cotizó la Subaru XV con el precio del Kia Sportage: tenía el
        # dato correcto en la tool y lo reescribió mal.
        from bot.flow.global_prompt import SYSTEM_PROMPT
        self.assertIn("precio_formateado", SYSTEM_PROMPT)
        self.assertIn("COPIA ese texto tal cual", SYSTEM_PROMPT)

    def test_prohibe_preguntar_entre_una_sola_sucursal(self):
        # El bot preguntó "¿cuál sucursal te queda más cerca?" con una sola
        # sucursal, haciéndole perder un turno al cliente.
        from bot.flow.global_prompt import SYSTEM_PROMPT
        self.assertIn("## SUCURSALES", SYSTEM_PROMPT)
        self.assertIn("motivo_sin_ranking", SYSTEM_PROMPT)
