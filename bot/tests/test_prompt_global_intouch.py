"""El prompt global se antepone a todos los especialistas y gana en cualquier
conflicto. Acá viven la identidad, el tono, la ortografía, los guardrails
duros y el marco legal.
"""
import json
from pathlib import Path

from django.test import SimpleTestCase

RAIZ = Path(__file__).resolve().parents[2]


class IdentidadUnicaTest(SimpleTestCase):
    def test_los_tres_lugares_dicen_lo_mismo(self):
        # Prompt global, bienvenida y dios.json. Tres copias que se
        # contradicen es como se le presenta al contacto un bot con dos nombres.
        from bot.flow.global_prompt import SYSTEM_PROMPT
        from bot.whatsapp.handlers import WELCOME_IDENTIDAD

        self.assertIn("InTouch", SYSTEM_PROMPT)
        self.assertIn("InTouch", WELCOME_IDENTIDAD)
        ejemplo = json.loads((RAIZ / "dios.json.example").read_text(encoding="utf-8"))
        self.assertIn("InTouch", ejemplo["nombre"])

    def test_no_queda_identidad_del_bot_anterior(self):
        from bot.flow.global_prompt import SYSTEM_PROMPT
        from bot.whatsapp.handlers import WELCOME_IDENTIDAD

        for texto in (SYSTEM_PROMPT, WELCOME_IDENTIDAD):
            self.assertNotIn("Cavem", texto)
            self.assertNotIn("Auto IA", texto)


class GuardrailsTest(SimpleTestCase):
    def test_la_lista_de_nunca_inventes_esta_enumerada(self):
        # Biblia §III.6 punto 3: los guardrails genéricos no se cumplen, los
        # enumerados sí. Se exige la numeración explícita.
        from bot.flow.global_prompt import SYSTEM_PROMPT

        for n in range(1, 9):
            self.assertIn(f"{n}.", SYSTEM_PROMPT)

    def test_esta_la_frase_exacta_de_escape(self):
        from bot.flow.global_prompt import SYSTEM_PROMPT

        self.assertIn("No tengo ese dato confirmado", SYSTEM_PROMPT)

    def test_esta_el_marco_legal_chileno(self):
        # El bot recopila datos personales de contactos en Chile.
        from bot.flow.global_prompt import SYSTEM_PROMPT

        self.assertIn("21.719", SYSTEM_PROMPT)

    def test_prohibe_afirmar_un_registro_que_no_puede_verificar(self):
        # Spec §7.2: cuando el bot redacta la respuesta, el lead todavía no se
        # escribió. Es el guardrail propio de esta arquitectura.
        from bot.flow.global_prompt import SYSTEM_PROMPT

        self.assertIn("registrado", SYSTEM_PROMPT)


class OrtografiaTest(SimpleTestCase):
    def test_el_prompt_va_con_tildes(self):
        # Biblia §III.3 ley 5. El prompt global de Cavem se publicó con 0
        # tildes contra 102 en el código y el bot le escribió "cuentame" a un
        # contacto real. Este test es la defensa de la que salió ese hallazgo.
        from bot.flow.global_prompt import SYSTEM_PROMPT

        self.assertGreater(sum(SYSTEM_PROMPT.count(c) for c in "áéíóúñ¿¡"), 60)

    def test_la_regla_de_ortografia_esta_escrita_con_tildes(self):
        # El ejemplo de la regla ES corpus: la versión anterior traía
        # "cuentame" sin tilde y le enseñó exactamente eso.
        from bot.flow.global_prompt import SYSTEM_PROMPT

        self.assertNotIn("cuentame", SYSTEM_PROMPT)
        self.assertNotIn(" ano ", SYSTEM_PROMPT)


class MensajesLargosTest(SimpleTestCase):
    def test_pide_mensajes_breves(self):
        from bot.flow.global_prompt import SYSTEM_PROMPT

        self.assertIn("párrafo", SYSTEM_PROMPT.lower())
