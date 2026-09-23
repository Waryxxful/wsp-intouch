"""Promesas que la demo del 15-09 afirmó y el sistema no cumple.

El docstring de `crear_caso` le ganó al prompt: le pedía decir que la consulta
«queda registrada» y devolver un `caso_id` que el contacto no puede usar. La
frase de escape, a su vez, era la única salida de los ocho «Nunca inventes»,
también para un precio que sí tiene otra respuesta.
"""
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "prompt_comercial.md"
FRASE_DE_ESCAPE = "No tengo ese dato confirmado, prefiero que lo valide un especialista."
FORMULA_DEL_CASO = "dejo tu caso listo para que el equipo lo tome"


def _texto_de_crear_caso() -> str:
    from bot.business.compliance import crear_caso

    partes = [crear_caso.description or ""]
    for attr in ("func", "coroutine"):
        fn = getattr(crear_caso, attr, None)
        if fn is not None:
            partes.append(getattr(fn, "__doc__", "") or "")
    return "\n".join(partes)


class DocstringCrearCasoTest(SimpleTestCase):
    def test_pide_la_formula_y_no_el_registro(self):
        texto = _texto_de_crear_caso()
        self.assertIn(FORMULA_DEL_CASO, texto)
        self.assertNotIn("queda registrada", texto)
        self.assertIn("caso_id", texto)
        self.assertIn("no se lo leas", texto.lower())


class ReglasDePromptTest(SimpleTestCase):
    def test_el_fixture_y_el_prompt_global_traen_el_canal_interno(self):
        from bot.flow.global_prompt import SYSTEM_PROMPT

        fixture = FIXTURE.read_text(encoding="utf-8")
        for nombre, texto in (("fixture", fixture), ("global", SYSTEM_PROMPT)):
            self.assertIn("SERNAC", texto, nombre)
            self.assertIn("canal interno", texto, nombre)
            self.assertIn("crear_caso", texto, nombre)
            self.assertIn("reclamo", texto, nombre)
            self.assertIn("datos_personales", texto, nombre)
            self.assertIn("Agencia de Protección de Datos", texto, nombre)

    def test_el_fixture_separa_la_evaluacion_comercial_de_la_frase_de_escape(self):
        texto = FIXTURE.read_text(encoding="utf-8")
        self.assertIn(FRASE_DE_ESCAPE, texto)
        self.assertNotIn("Para todos estos casos la frase es", texto)
        self.assertRegex(
            texto,
            r"Precio, plazo o integración concreta:[\s\S]{0,400}"
            r"evaluación comercial[\s\S]{0,200}No uses la frase de escape",
        )
        self.assertRegex(
            texto,
            r"bloque de hechos del turno:[\s\S]{0,200}frase de escape",
        )

    def test_horario_consentimiento_y_ficha_estan_en_los_dos_prompts(self):
        from bot.flow.global_prompt import SYSTEM_PROMPT

        fixture = FIXTURE.read_text(encoding="utf-8")
        for nombre, texto in (("fixture", fixture), ("global", SYSTEM_PROMPT)):
            self.assertIn("anoto que te acomoda", texto, nombre)
            self.assertIn("no queda agendado, el equipo te confirma", texto, nombre)
            self.assertIn("bloque de horario del turno", texto, nombre)
            self.assertIn("texto de consentimiento entre marcas", texto, nombre)
            self.assertIn(FORMULA_DEL_CASO, texto, nombre)

    def test_clientes_y_trayectoria_siguen_la_regla_del_gerente_comercial(self):
        # Regla del gerente comercial (2026-09-23), tras el chat 9: el bot no
        # da nombres de clientes ("No nombres clientes" se leía como imperativo
        # y el modelo lo imitó: "no nombre clientes"). Según la industria del
        # contacto, habla de la trayectoria en general.
        #
        # nombra clientes; habla de la trayectoria en general según la
        # industria del contacto. Las cifras que publica in-touch.cl sí se
        # pueden citar si las trae la base de conocimiento, con su aclaración.
        # Reemplaza a "todavía no hay una ficha firmada", que dejaba al bot
        # sin poder decir ni los años de experiencia que están en el sitio.
        from bot.flow.global_prompt import SYSTEM_PROMPT

        fixture = FIXTURE.read_text(encoding="utf-8")
        for nombre, crudo in (("fixture", fixture), ("global", SYSTEM_PROMPT)):
            texto = " ".join(crudo.split())
            self.assertNotIn("ficha firmada", texto, nombre)
            self.assertIn("Nunca des nombres de clientes de InTouch", texto, nombre)
            self.assertIn("InTouch es líder en la industria automotriz", texto, nombre)
            self.assertIn(
                "presencia y experiencia en la industria automotriz, en empresas "
                "privadas y corporativas, y en entidades públicas", texto, nombre)
            self.assertIn("consultar_base_conocimiento", texto, nombre)
            self.assertIn("clientes activos del sector automotriz e industrial", texto, nombre)


class NotificarCasoNuevoTest(TestCase):
    def setUp(self):
        from bot.models import Conversation

        self.conv = Conversation.objects.create(wa_id="56911119999")

    def test_un_caso_nuevo_avisa_y_el_dedup_no_repite(self):
        from bot.business.compliance import _crear_caso_impl
        from bot.models import Incident

        with patch("bot.notify.notificar") as notificar:
            primero = _crear_caso_impl(
                wa_id="56911119999", tipo="reclamo",
                resumen="La facturación del servicio llegó mal.",
            )
            segundo = _crear_caso_impl(
                wa_id="56911119999", tipo="reclamo",
                resumen="Insiste: la facturación sigue mal.",
            )

        notificar.assert_called_once()
        kwargs = notificar.call_args.kwargs
        self.assertEqual(kwargs["tipo"], "caso_equipo")
        self.assertEqual(kwargs["url"], "/wsp/intouch/conversaciones")
        self.assertIn("reclamo", kwargs["mensaje"])
        self.assertIn("facturación", kwargs["mensaje"])
        self.assertEqual(primero["caso_id"], segundo["caso_id"])
        self.assertEqual(
            Incident.objects.filter(conversation=self.conv, kind="reclamo").count(), 1)

    def test_si_notificar_falla_el_caso_igual_queda_creado(self):
        from bot.business.compliance import _crear_caso_impl
        from bot.models import Incident

        with patch("bot.notify.notificar", side_effect=RuntimeError("orquestador caído")):
            resultado = _crear_caso_impl(
                wa_id="56911119999", tipo="datos_personales",
                resumen="Pide saber qué datos hay de él.",
            )

        self.assertTrue(resultado["ok"])
        self.assertTrue(Incident.objects.filter(pk=resultado["caso_id"]).exists())
