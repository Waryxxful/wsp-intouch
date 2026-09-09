"""El único especialista del bot.

Uno solo y no dos, a propósito (spec §2.2): un especialista visible es un
especialista al que el LLM puede rutear MAL, y "soporte" contra "comercial" es
un límite semántico difuso -- un contacto que se queja calza con los dos. El
precedente son las encuestas de Renault desregistradas en Cavem.
"""
import re

from django.test import SimpleTestCase, TestCase


class RegistroTest(SimpleTestCase):
    def test_hay_un_solo_especialista_registrado(self):
        from bot.flow.agents import AGENTS

        self.assertEqual(list(AGENTS), ["comercial"])

    def test_los_especialistas_de_autos_quedan_desregistrados(self):
        # Están en el repo por linaje y para conservar su cobertura, pero
        # fuera de AGENTS: invisibles para el ruteo.
        from bot.flow.agents import AGENTES_NO_REGISTRADOS

        for slug in ("agendamiento", "confirmacion", "faq"):
            self.assertIn(slug, AGENTES_NO_REGISTRADOS, slug)

    def test_el_slug_comercial_esta_reservado(self):
        from bot.flow.agents import RESERVED_SLUGS

        self.assertIn("comercial", RESERVED_SLUGS)


class ToolsBindeadasTest(SimpleTestCase):
    def setUp(self):
        from bot.flow.agents.comercial import ComercialAgent

        self.nombres = {t.name for t in ComercialAgent().business_actions()}

    def test_estan_las_tools_del_catalogo_y_el_rag(self):
        self.assertEqual(self.nombres, {
            "listar_soluciones", "consultar_solucion", "listar_modelos_operacion",
            "consultar_base_conocimiento", "crear_caso",
            "registrar_no_contactar", "registrar_consentimiento",
        })

    def test_no_hay_tools_de_autos(self):
        for prohibida in ("buscar_vehiculos", "simular_financiamiento", "agendar_hora",
                          "crear_lead", "registrar_datos_lead", "registrar_parte_pago",
                          "buscar_sucursales_cercanas", "listar_catalogo"):
            self.assertNotIn(prohibida, self.nombres, prohibida)

    def test_la_tool_de_lead_no_vuelve_al_camino_critico(self):
        # Si vuelve, vuelve la segunda ronda de 4,53s y nadie se entera por los
        # tests. Ver spec §7.1.
        self.assertNotIn("registrar_datos_lead", self.nombres)


class PromptTest(TestCase):
    def test_el_prompt_por_defecto_sale_del_codigo(self):
        from bot.flow.agents.comercial import SYSTEM_PROMPT, ComercialAgent

        self.assertEqual(ComercialAgent().effective_prompt(), SYSTEM_PROMPT)

    def test_la_bd_pisa_al_codigo(self):
        from bot.flow.agents.comercial import ComercialAgent
        from bot.models import save_prompt_version

        save_prompt_version("comercial", "Prompt publicado desde el panel.")
        self.assertEqual(
            ComercialAgent().effective_prompt(), "Prompt publicado desde el panel.")

    def test_el_prompt_nombra_solo_tools_que_existen(self):
        # Es el chequeo estrella del doctor, anclado también desde la suite: un
        # prompt que nombra una tool no bindeada le pide al modelo algo
        # imposible, y el turno se va en intentarlo.
        from bot.flow.agents.comercial import SYSTEM_PROMPT, ComercialAgent

        bindeadas = {t.name for t in ComercialAgent().business_actions()} | {"responder"}
        nombradas = set(re.findall(r'"([a-z_]+)"', SYSTEM_PROMPT))
        inventadas = {n for n in nombradas if n.endswith(("_lead", "_solucion", "_caso"))
                      or n.startswith(("listar_", "consultar_", "registrar_", "buscar_"))}
        self.assertTrue(inventadas <= bindeadas, inventadas - bindeadas)

    def test_el_fixture_de_git_coincide_con_el_codigo(self):
        # El doctor compara BD contra fixture; esto ancla fixture contra código.
        from pathlib import Path

        from bot.flow.agents.comercial import SYSTEM_PROMPT

        fixture = (Path(__file__).resolve().parents[1] / "fixtures" / "prompt_comercial.md")
        self.assertEqual(fixture.read_text(encoding="utf-8").strip(), SYSTEM_PROMPT.strip())

    def test_el_prompt_no_tiene_palabras_sin_tilde(self):
        # Biblia §III.3 ley 5: el modelo imita su corpus. Un prompt sin tildes
        # le enseña a escribir sin tildes, y ya le llegó a un contacto real
        # ("cuentame", publicado sin tildes el 2026-09-03, replicado por el
        # bot 6 horas después).
        #
        # Un piso numérico de tildes totales (el diseño original de este test)
        # no detecta ese defecto: un prompt largo con 40 tildes puede repetir
        # "informacion" sin tilde tres veces y pasar igual, y encima empuja a
        # inflar el prompt sólo para juntar caracteres acentuados. Se reemplaza
        # por una lista negra de FORMAS SIN TILDE de palabras que en este
        # prompt siempre llevan tilde -- si alguna aparece, es la forma
        # incorrecta.
        #
        # Ojo con dos trampas (por eso la lista sale de correr esto contra el
        # prompt real, no de escribirla a mano):
        #   1. Palabras válidas SIN tilde ("mas", "como", "cuando", "quien",
        #      "esta", "solo", "que", "cual", "si", "el", "tu") no se listan
        #      sueltas -- se verifican con el fragmento de frase real donde
        #      aparece la forma acentuada, para no gritar por un uso legítimo.
        #   2. Cada entrada se confirmó por búsqueda directa contra
        #      bot/fixtures/prompt_comercial.md, no se adivinó.
        from bot.flow.agents.comercial import SYSTEM_PROMPT

        # Palabras sin ambigüedad: en este prompt SIEMPRE llevan tilde, así
        # que su forma sin tilde es siempre un error. Se buscan como palabra
        # completa (\b) para no disparar por subcadena (ej. "area" no debe
        # matchear dentro de otra palabra).
        formas_sin_tilde = [
            "accion", "acepto", "afirmacion", "ahi", "atencion", "automatizacion",
            "automaticamente", "busqueda", "catalogo", "compartio", "consultoria",
            "conversacion", "correccion", "corrigela", "cotizacion", "decision",
            "declaro", "derivacion", "despues", "electronico", "enfocate", "envio",
            "esten", "evaluacion", "explicale", "explicitos", "gustaria",
            "implementacion", "informacion", "intencion", "limite", "limites",
            "mision", "moderacion", "numero", "operacion", "pidio", "politicas",
            "presentala", "propon", "parrafos", "quedo", "recien", "reconocelo",
            "respondelo", "reunion", "revision", "rigido", "segun", "solucion",
            "tambien", "telefono", "tecnica", "tecnico", "terminos", "ubicacion",
            "area", "exito", "util",
        ]
        for forma in formas_sin_tilde:
            self.assertIsNone(
                re.search(rf"\b{forma}\b", SYSTEM_PROMPT),
                f'"{forma}" aparece sin tilde en el prompt',
            )

        # Palabras ambiguas (válidas sin tilde en otro sentido/forma): se
        # verifican con el fragmento de frase real, no sueltas. `\s+` en vez
        # de un espacio literal porque alguna frase real cruza un salto de
        # línea del markdown.
        frases_con_tilde = [
            (r"Para explicar cómo se entrega el servicio",
             r"Para explicar como se entrega el servicio"),
            (r"pregunta qué datos tienes de él",
             r"pregunta que datos tienes de el"),
            (r"cuál de estos subtipos",
             r"cual de estos subtipos"),
            (r"nunca más de dos",
             r"nunca mas de dos"),
            (r"Lo que sí puedes hacer",
             r"Lo que si puedes hacer"),
            (r"los datos que él mismo\s+compartió",
             r"los datos que el mismo\s+compartio"),
            (r"las que tú le",
             r"las que tu le"),
            (r"NO está en estas instrucciones",
             r"NO esta en estas instrucciones"),
        ]
        for con_tilde, sin_tilde in frases_con_tilde:
            self.assertRegex(SYSTEM_PROMPT, con_tilde, f"la frase de referencia cambió: {con_tilde!r}")
            self.assertNotRegex(SYSTEM_PROMPT, sin_tilde, f"'{sin_tilde}' aparece sin tilde en el prompt")


class BloquesDelPromptTest(TestCase):
    def test_el_system_prompt_incluye_el_contrato_de_respuesta(self):
        from bot.flow.agents.comercial import ComercialAgent

        agente = ComercialAgent()
        armado = agente.build_system_prompt({}, agente.effective_prompt())
        self.assertIn("## RESPUESTA", armado)

    def test_incluye_los_datos_ya_conocidos_del_contacto(self):
        from bot.flow.agents.comercial import ComercialAgent

        agente = ComercialAgent()
        armado = agente.build_system_prompt(
            {"flow_data": {"empresa": "Acme SpA"}}, agente.effective_prompt())
        self.assertIn("Acme SpA", armado)

    def test_no_menciona_sucursales(self):
        from bot.flow.agents.comercial import ComercialAgent

        agente = ComercialAgent()
        armado = agente.build_system_prompt({}, agente.effective_prompt())
        self.assertNotIn("sucursal", armado.lower())
