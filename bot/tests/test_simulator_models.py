from django.db.models import ProtectedError
from django.test import TestCase

from bot.simulator.models import CorridaDePrueba, EscenarioDePrueba, ResultadoDeEscenario


class EscenarioDePruebaTest(TestCase):
    def test_crea_con_defaults(self):
        e = EscenarioDePrueba.objects.create(
            nombre="saludo-simple", persona="cliente amable", objetivo="saludar",
            criterios=["el bot saluda de vuelta"],
        )
        self.assertEqual(e.max_turns, 12)
        self.assertTrue(e.activo)
        self.assertEqual(e.fuente, "")

    def test_nombre_es_unico(self):
        EscenarioDePrueba.objects.create(
            nombre="dup", persona="x", objetivo="y", criterios=["z"],
        )
        with self.assertRaises(Exception):
            EscenarioDePrueba.objects.create(
                nombre="dup", persona="a", objetivo="b", criterios=["c"],
            )


class CorridaDePruebaTest(TestCase):
    def test_crea_en_estado_corriendo_por_default(self):
        c = CorridaDePrueba.objects.create(disparada_por="admin@test.com")
        self.assertEqual(c.estado, "corriendo")
        self.assertIsNone(c.fecha_fin)
        self.assertIsNone(c.nombre_escenario_filtro)

    def test_actualizado_en_cambia_al_guardar(self):
        c = CorridaDePrueba.objects.create(disparada_por="admin@test.com")
        primero = c.actualizado_en
        c.estado = "completa"
        c.save(update_fields=["estado", "actualizado_en"])
        c.refresh_from_db()
        self.assertGreaterEqual(c.actualizado_en, primero)


class ResultadoDeEscenarioTest(TestCase):
    def setUp(self):
        self.escenario = EscenarioDePrueba.objects.create(
            nombre="e1", persona="x", objetivo="y", criterios=["z"],
        )
        self.corrida = CorridaDePrueba.objects.create(disparada_por="consola")

    def test_crea_resultado_asociado(self):
        r = ResultadoDeEscenario.objects.create(
            corrida=self.corrida, escenario=self.escenario, paso=True,
            fallos=[], transcript="Cliente: hola\nBot: hola",
        )
        self.assertIn(r, self.corrida.resultados.all())
        self.assertIn(r, self.escenario.resultados.all())

    def test_borrar_escenario_con_resultados_esta_protegido(self):
        ResultadoDeEscenario.objects.create(
            corrida=self.corrida, escenario=self.escenario, paso=True, fallos=[], transcript="x",
        )
        with self.assertRaises(ProtectedError):
            self.escenario.delete()
