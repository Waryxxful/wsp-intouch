"""El bloque que el comercial recibe en cada turno, aunque el RAG venga vacío.

Cubre el sitio, los tres modelos, la franja de BusinessHours, la preferencia
ya anotada y el texto de consentimiento. No es un prompt de autos.
"""
import re
from datetime import time

from django.test import TestCase, override_settings


class BloqueContextoTurnoTest(TestCase):
    def test_trae_el_sitio_y_los_tres_modelos_sin_cifras(self):
        from bot.flow.contexto_turno import bloque_contexto_turno

        bloque = bloque_contexto_turno({})
        self.assertIn("https://in-touch.cl", bloque)
        self.assertNotIn("https://in-touch.cl/soluciones", bloque)
        self.assertNotIn("https://in-touch.cl/casos", bloque)
        self.assertNotIn("https://in-touch.cl/contacto", bloque)
        for modelo in ("Humano", "Híbrido", "Automatizado"):
            self.assertIn(modelo, bloque)
        self.assertIn("listar_modelos_operacion", bloque)
        self.assertIn("copiloto", bloque.lower())
        self.assertNotIn("83 %", bloque)
        self.assertNotIn("83%", bloque)
        self.assertNotRegex(bloque, r"\$\s?\d")
        self.assertNotRegex(bloque, r"(?i)\d[\d.]*\s*pesos")
        self.assertNotRegex(bloque, r"\d+\s*%")

    def test_no_mete_vocabulario_de_otro_vertical(self):
        # El doctor escanea el prompt armado. Este bloque entra en cada turno:
        # una palabra de autos acá falla ese chequeo.
        from bot.flow.contexto_turno import bloque_contexto_turno
        from bot.management.commands.doctor import VOCABULARIO_DE_OTRO_VERTICAL

        bloque = bloque_contexto_turno({})
        for forma in VOCABULARIO_DE_OTRO_VERTICAL:
            self.assertIsNone(
                re.search(rf"\b{forma}\b", bloque, re.IGNORECASE),
                forma,
            )

    def test_la_franja_por_defecto_no_se_despega_del_panel(self):
        from admin_panel.views import _BUSINESS_HOURS_DEFAULT

        from bot.flow.contexto_turno import _FILAS_SI_NO_HAY_HORARIO

        self.assertEqual(list(_FILAS_SI_NO_HAY_HORARIO), list(_BUSINESS_HOURS_DEFAULT))

    def test_sin_filas_dice_lunes_a_viernes_y_no_escribe(self):
        from admin_panel.models import BusinessHours

        from bot.flow.contexto_turno import bloque_contexto_turno

        BusinessHours.objects.all().delete()
        bloque = bloque_contexto_turno({})
        self.assertEqual(BusinessHours.objects.count(), 0)
        self.assertIn("lunes a viernes", bloque)
        self.assertIn("09:00", bloque)
        self.assertIn("18:00", bloque)
        self.assertNotIn("sábado", bloque.lower())
        self.assertNotIn("sabado", bloque.lower())
        self.assertNotIn("13:00", bloque)

    def test_respeta_la_franja_activa_y_no_ofrece_las_22(self):
        from admin_panel.models import BusinessHours

        from bot.flow.contexto_turno import bloque_contexto_turno

        BusinessHours.objects.all().delete()
        for dia in range(7):
            activo = dia == 0
            BusinessHours.objects.create(
                dia_semana=dia,
                hora_inicio=time(10, 0) if activo else time(0, 0),
                hora_fin=time(16, 0) if activo else time(0, 0),
                activo=activo,
            )
        bloque = bloque_contexto_turno({})
        self.assertIn("lunes", bloque)
        self.assertIn("10:00", bloque)
        self.assertIn("16:00", bloque)
        self.assertNotIn("22:00", bloque)
        self.assertNotIn("viernes", bloque.lower())
        self.assertNotIn("sábado", bloque.lower())

    def test_la_preferencia_anotada_no_se_presenta_como_agendada(self):
        from bot.flow.contexto_turno import bloque_contexto_turno
        from bot.models import Conversation, LeadInTouch

        conv = Conversation.objects.create(wa_id="56900000001")
        LeadInTouch.objects.create(
            conversation=conv, preferencia_horaria="hoy a las 22:00")
        bloque = bloque_contexto_turno({"wa_id": "56900000001"})
        self.assertIn("hoy a las 22:00", bloque)
        self.assertIn('Preferencia ya anotada: "hoy a las 22:00".', bloque)
        self.assertIn("No la repitas completa.", bloque)
        self.assertIn("No digas que quedó agendada", bloque)
        self.assertIn("coordinada", bloque)

        otro = bloque_contexto_turno({"wa_id": "56900000002"})
        self.assertNotIn("hoy a las 22:00", otro)
        self.assertNotIn("Preferencia ya anotada", otro)

    def test_una_preferencia_vacia_no_se_menciona(self):
        from bot.flow.contexto_turno import bloque_contexto_turno
        from bot.models import Conversation, LeadInTouch

        conv = Conversation.objects.create(wa_id="56900000003")
        LeadInTouch.objects.create(conversation=conv, preferencia_horaria="   ")
        bloque = bloque_contexto_turno({"wa_id": "56900000003"})
        self.assertNotIn("Preferencia ya anotada", bloque)

    def test_sin_texto_de_consentimiento_no_lo_menciona(self):
        from bot.flow.contexto_turno import bloque_contexto_turno

        bloque = bloque_contexto_turno({"wa_id": "56900000004"})
        self.assertNotIn("consentimiento", bloque.lower())
        self.assertNotIn("INICIO_TEXTO_LEGAL", bloque)

    @override_settings(TEXTO_CONSENTIMIENTO="Texto legal de prueba.")
    def test_el_texto_legal_entra_solo_si_no_hay_consentimiento(self):
        from bot.flow.contexto_turno import bloque_contexto_turno
        from bot.models import Consentimiento

        wa = "56900000005"
        sin = bloque_contexto_turno({"wa_id": wa})
        self.assertIn("Texto legal de prueba.", sin)
        self.assertEqual(sin.count("Texto legal de prueba."), 1)
        self.assertIn("<<<INICIO_TEXTO_LEGAL>>>", sin)
        self.assertIn("tal cual", sin)

        Consentimiento.objects.create(wa_id=wa, otorgado=True)
        con = bloque_contexto_turno({"wa_id": wa})
        self.assertNotIn("Texto legal de prueba.", con)
        self.assertNotIn("INICIO_TEXTO_LEGAL", con)

    @override_settings(TEXTO_CONSENTIMIENTO="Texto legal de prueba.")
    def test_un_consentimiento_revocado_no_cuenta_como_aceptado(self):
        from bot.flow.contexto_turno import bloque_contexto_turno
        from bot.models import Consentimiento

        wa = "56900000006"
        Consentimiento.objects.create(wa_id=wa, otorgado=False)
        bloque = bloque_contexto_turno({"wa_id": wa})
        self.assertIn("Texto legal de prueba.", bloque)

    def test_el_grafo_no_arma_el_prompt_en_el_event_loop(self):
        # Una query de horario dentro del loop frena a los demás contactos
        # del worker. El armado va por sync_to_async, igual que el prompt
        # efectivo. No se prende DJANGO_ALLOW_ASYNC_UNSAFE.
        from pathlib import Path

        fuente = Path(__file__).resolve().parents[1].joinpath(
            "flow", "graph.py").read_text(encoding="utf-8")
        self.assertIn(
            "await sync_to_async(agent.build_system_prompt)(",
            fuente,
        )
        self.assertNotIn("DJANGO_ALLOW_ASYNC_UNSAFE", fuente)

    def test_el_prompt_del_comercial_incluye_el_bloque_junto_a_la_fecha(self):
        from bot.flow.agents.comercial import ComercialAgent

        armado = ComercialAgent().build_system_prompt({}, "PROMPT-MARCADOR")
        self.assertIn("https://in-touch.cl", armado)
        self.assertIn("Humano", armado)
        self.assertIn("Híbrido", armado)
        self.assertIn("Automatizado", armado)
        self.assertLess(armado.find("## FECHA"), armado.find("https://in-touch.cl"))
        self.assertLess(armado.find("https://in-touch.cl"), armado.find("PROMPT-MARCADOR"))
