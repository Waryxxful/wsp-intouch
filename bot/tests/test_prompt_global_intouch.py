"""El prompt global se antepone a todos los especialistas y gana en cualquier
conflicto. Acá viven la identidad, el tono, la ortografía, los guardrails
duros y el marco legal.
"""
import json
import re
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
    # Formas SIN tilde de palabras (o frases) que este prompt usa. Esta lista
    # negra reemplaza a un conteo de tildes: el fallo que hay que atrapar es
    # un prompt publicado sin acentuar -- el prompt global de otro bot de
    # este stack salió con CERO tildes y el bot le escribió "cuentame" a un
    # contacto real seis horas después. Un piso numérico no lo detecta y
    # además empuja a inflar el prompt para pasar el test, que es estado de
    # producción: fue justo lo que le pasó al implementer anterior de este
    # test acá.
    #
    # Los ítems de una sola palabra se buscan como TOKEN completo, no como
    # subcadena: "solucion" sin límite de palabra también aparece dentro de
    # "soluciones", que está bien escrito sin tilde (el plural no la lleva).
    # Las palabras que en español son válidas SIN tilde con otro
    # significado -- más/mas, cómo/como, cuándo/cuando, quién/quien,
    # está/esta, sí/si, él/el, qué/que, quedó/quedo -- no se listan sueltas:
    # un test que grita por una palabra correcta es un test que alguien
    # apaga. Donde vale la pena cubrirlas se usan como FRASE, con el
    # contexto exacto de este prompt en el que la forma sin tilde no tiene
    # ninguna lectura válida.
    _FORMAS_SIN_TILDE = [
        # palabras sin ninguna otra lectura válida en español
        "cuentame", "acentua", "aqui", "asi", "atencion", "atiendelo",
        "compania", "compartio", "contestalas", "conversacion", "decision",
        "dias", "dificil", "envio", "estan", "exclamacion", "exito",
        "funcion", "gestion", "implementacion", "informacion",
        "interrogacion", "limites", "linea", "numero", "ordenalas",
        "ortografia", "pidio", "podras", "proteccion", "proxima", "proximo",
        "parrafo", "parrafos", "respondele", "respondelo", "respondio",
        "reunion", "revision", "segun", "solucion", "tambien", "tendras",
        "unico",
        # frases: la palabra sí tiene otra lectura válida sin tilde en
        # español, pero no en el contexto puntual en que aparece acá
        "mas de dos",  # "más de dos" (dos preguntas); "mas" (pero) no encaja
        "el contacto esta apurado",  # "está" (verbo), no "esta" (este/a)
        "reunion quedo",  # "quedó" (3a persona); "quedo" (1a) no concuerda
    ]

    def test_no_hay_formas_sin_tilde(self):
        # Reemplaza al viejo piso de "más de 60 caracteres acentuados": ese
        # conteo no distingue un prompt bien acentuado de uno mal acentuado
        # con relleno, y empuja a inflar el prompt para pasar el test en vez
        # de medir el defecto real.
        from bot.flow.global_prompt import SYSTEM_PROMPT

        texto = SYSTEM_PROMPT.lower()
        texto_plano = " ".join(texto.split())
        tokens = set(re.findall(r"[a-záéíóúñ]+", texto))

        encontradas = [
            forma
            for forma in self._FORMAS_SIN_TILDE
            if (forma in texto_plano if " " in forma else forma in tokens)
        ]
        self.assertEqual(
            encontradas,
            [],
            "formas sin tilde en el prompt global: " + ", ".join(encontradas),
        )

    def test_la_regla_de_ortografia_esta_escrita_con_tildes(self):
        # El ejemplo de la regla ES corpus: la versión anterior traía
        # "cuentame" sin tilde y le enseñó exactamente eso. Cubre las dos
        # mitades del fallo histórico: que la forma incorrecta no esté, y
        # que la correcta sí esté.
        from bot.flow.global_prompt import SYSTEM_PROMPT

        self.assertNotIn("cuentame", SYSTEM_PROMPT)
        self.assertNotIn(" ano ", SYSTEM_PROMPT)
        self.assertIn("cuéntame", SYSTEM_PROMPT)


class MensajesLargosTest(SimpleTestCase):
    def test_pide_mensajes_breves(self):
        from bot.flow.global_prompt import SYSTEM_PROMPT

        self.assertIn("3\nlíneas", SYSTEM_PROMPT)
        self.assertIn("Como máximo dos mensajes", SYSTEM_PROMPT)
        self.assertNotIn("contéstalas una por una", SYSTEM_PROMPT)
