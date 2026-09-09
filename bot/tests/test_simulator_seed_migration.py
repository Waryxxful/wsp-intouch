from django.test import TestCase

from bot.simulator.models import EscenarioDePrueba


class SeedEscenariosMigrationTest(TestCase):
    """Como la BD de test corre TODAS las migraciones antes de cada test run
    (incluida 0002_seed_escenarios), no hace falta invocar la migracion a
    mano -- alcanza con verificar que sus filas ya estan ahi."""

    def test_hay_8_escenarios_semilla(self):
        self.assertEqual(EscenarioDePrueba.objects.count(), 8)

    def test_cada_escenario_semilla_esta_activo_y_tiene_criterios(self):
        for e in EscenarioDePrueba.objects.all():
            self.assertTrue(e.activo, e.nombre)
            self.assertGreaterEqual(len(e.criterios), 1, e.nombre)

    def test_incluye_el_escenario_de_precio_anclado_a_catalogo(self):
        e = EscenarioDePrueba.objects.get(nombre="precio-financiamiento-anclado-a-catalogo")
        self.assertEqual(e.max_turns, 8)
        self.assertIn("catalogo", e.criterios[0])
