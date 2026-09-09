"""El catálogo de soluciones de InTouch.

Va a tabla y no al prompt porque el guardrail "no inventes integraciones ni
capacidades" sólo es cumplible si la lista sale de una fila -- el mismo
argumento que "no inventes un precio" (biblia §III.5).
"""
from django.conf import settings
from django.test import TestCase

from bot.models import ModeloOperacion, SolucionInTouch


class ManagerFiltradoTest(TestCase):
    def test_objects_filtra_por_cliente_activo(self):
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="agentes-ia",
            nombre="Agentes conversacionales con IA", categoria="agentes_ia",
            descripcion="Agentes para WhatsApp, voz, chat y correo.",
        )
        SolucionInTouch.todos_los_clientes.create(
            cliente="otro-cliente-que-no-es-el-activo", slug="ajena",
            nombre="Solución de otro cliente", categoria="agentes_ia",
            descripcion="No debería verse.",
        )
        self.assertEqual([s.slug for s in SolucionInTouch.objects.all()], ["agentes-ia"])
        self.assertEqual(SolucionInTouch.todos_los_clientes.count(), 2)

    def test_el_slug_es_unico_por_cliente(self):
        from django.db import IntegrityError

        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="paneles", nombre="Paneles",
            categoria="analitica", descripcion="Dashboards y Power BI.",
        )
        with self.assertRaises(IntegrityError):
            SolucionInTouch.todos_los_clientes.create(
                cliente=settings.CLIENTE_ACTIVO, slug="paneles", nombre="Duplicada",
                categoria="analitica", descripcion="No debería entrar.",
            )


class CamposDelCatalogoTest(TestCase):
    def test_los_canales_y_modelos_son_listas(self):
        sol = SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="agentes-ia",
            nombre="Agentes conversacionales con IA", categoria="agentes_ia",
            descripcion="Agentes para varios canales.",
            canales=["whatsapp", "voz", "chat", "email"],
            modelos_operacion=["automatizado", "hibrido"],
        )
        sol.refresh_from_db()
        self.assertEqual(sol.canales, ["whatsapp", "voz", "chat", "email"])
        self.assertEqual(sol.modelos_operacion, ["automatizado", "hibrido"])

    def test_requiere_evaluacion_tecnica_por_defecto_es_falso(self):
        # El prompt exige presentar las integraciones como sujetas a evaluación
        # técnica. El especialista lo lee de la fila en vez de acordarse.
        sol = SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="analitica", nombre="Analítica",
            categoria="analitica", descripcion="Analítica conversacional.",
        )
        self.assertFalse(sol.requiere_evaluacion_tecnica)


class SeedTest(TestCase):
    def test_el_seed_carga_catalogo_y_modelos_de_operacion(self):
        from django.core.management import call_command

        call_command("seed_intouch", verbosity=0)
        self.assertGreaterEqual(SolucionInTouch.objects.count(), 5)
        self.assertEqual(
            sorted(m.slug for m in ModeloOperacion.objects.all()),
            ["automatizado", "hibrido", "humano"],
        )

    def test_el_seed_es_idempotente(self):
        from django.core.management import call_command

        call_command("seed_intouch", verbosity=0)
        antes = SolucionInTouch.objects.count()
        call_command("seed_intouch", verbosity=0)
        self.assertEqual(SolucionInTouch.objects.count(), antes)

    def test_las_integraciones_quedan_marcadas_como_sujetas_a_evaluacion(self):
        from django.core.management import call_command

        call_command("seed_intouch", verbosity=0)
        integraciones = SolucionInTouch.objects.get(slug="integraciones")
        self.assertTrue(integraciones.requiere_evaluacion_tecnica)
