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
            canales=["whatsapp", "voz", "chat", "correo"],
            modelos_operacion=["automatizado", "hibrido"],
        )
        sol.refresh_from_db()
        self.assertEqual(sol.canales, ["whatsapp", "voz", "chat", "correo"])
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


class CuandoRecomendarlaTest(TestCase):
    """`cuando_recomendarla` traslada al catálogo el mapeo necesidad -> solución
    del documento comercial (§10), en vez de meterlo en el prompt.

    Va acá y no en el prompt por la misma razón que el resto del catálogo: el
    prompt se manda en CADA turno, la tool sólo cuando el bot habla de
    soluciones. Y el que edita el catálogo no debería tener que editar el
    prompt para cambiar cuándo se recomienda algo.
    """

    def test_la_tool_expone_cuando_recomendarla(self):
        from bot.business.soluciones import _listar_soluciones_impl
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="saas-whitelabel",
            nombre="SaaS y Whitelabel", categoria="agentes_ia",
            descripcion="Tecnología en modalidad SaaS o marca blanca.",
            cuando_recomendarla="Cuando el contacto es un call center o BPO.",
        )
        resultado = _listar_soluciones_impl()
        self.assertTrue(resultado["ok"])
        solucion = next(s for s in resultado["soluciones"] if s["slug"] == "saas-whitelabel")
        self.assertEqual(solucion["cuando_recomendarla"],
                         "Cuando el contacto es un call center o BPO.")

    def test_consultar_solucion_tambien_lo_expone(self):
        from bot.business.soluciones import _consultar_solucion_impl
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="seguridad-compliance",
            nombre="Seguridad y compliance", categoria="operacion",
            descripcion="Resguardo de datos y cumplimiento normativo.",
            cuando_recomendarla="Cuando el contacto es de un rubro regulado.",
        )
        resultado = _consultar_solucion_impl("seguridad y compliance")
        self.assertTrue(resultado["ok"])
        self.assertIn("regulado", resultado["solucion"]["cuando_recomendarla"])

    def test_el_campo_es_opcional_y_no_rompe_una_solucion_vieja(self):
        """Las 6 soluciones que ya existían no lo tenían. Si el campo fuera
        obligatorio, la migración las dejaría inválidas."""
        from bot.business.soluciones import _listar_soluciones_impl
        SolucionInTouch.objects.create(
            cliente=settings.CLIENTE_ACTIVO, slug="sin-mapeo",
            nombre="Solución sin mapeo", categoria="operacion",
            descripcion="No declara cuándo recomendarla.",
        )
        resultado = _listar_soluciones_impl()
        solucion = next(s for s in resultado["soluciones"] if s["slug"] == "sin-mapeo")
        self.assertEqual(solucion["cuando_recomendarla"], "")


class SemillaCubreElDocumentoTest(TestCase):
    """El documento comercial (§2) lista capacidades que el prompt prohíbe
    ofrecer si no están en el catálogo: "si una capacidad no aparece ahí, no la
    ofrezcas". Dos quedaron sin fila y este test las ancla.

    SaaS/Whitelabel es la Situación D completa del documento (§8): un call
    center o BPO que escribe es exactamente el prospecto que InTouch quiere, y
    sin fila el bot no tiene qué ofrecerle.
    """

    def test_la_semilla_incluye_saas_whitelabel_y_seguridad(self):
        from bot.management.commands.seed_intouch import SOLUCIONES
        slugs = {s["slug"] for s in SOLUCIONES}
        self.assertIn("saas-whitelabel", slugs)
        self.assertIn("seguridad-compliance", slugs)

    def test_toda_solucion_sembrada_declara_cuando_recomendarla(self):
        from bot.management.commands.seed_intouch import SOLUCIONES
        sin_mapeo = [s["slug"] for s in SOLUCIONES if not s.get("cuando_recomendarla")]
        self.assertEqual(sin_mapeo, [],
                         f"estas soluciones no dicen cuándo recomendarlas: {sin_mapeo}")
