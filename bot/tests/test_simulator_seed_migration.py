from django.test import TestCase

from bot.simulator.models import EscenarioDePrueba


class SeedEscenariosMigrationTest(TestCase):
    """Como la BD de test corre TODAS las migraciones antes de cada test run
    (incluida la semilla de escenarios), no hace falta invocar la migracion a
    mano -- alcanza con verificar que sus filas quedaron bien formadas.

    Verifica PROPIEDADES, no una cantidad ni un nombre fijo: un numero o un
    slug fijo se rompe cada vez que alguien agrega, saca o renombra un
    escenario -- paso real, no hipotetico: 0003_escenarios_intouch.py
    reemplazo los 8 escenarios automotrices heredados por los 13 de este bot
    y esto mismo se rompio (contaba 8, buscaba
    "precio-financiamiento-anclado-a-catalogo" por nombre). La semilla que
    haya en cada momento tiene que cumplir estas propiedades, sea cual sea
    su tamaño."""

    def test_hay_al_menos_un_escenario_semilla(self):
        self.assertGreater(EscenarioDePrueba.objects.count(), 0)

    def test_no_hay_slugs_duplicados(self):
        nombres = list(EscenarioDePrueba.objects.values_list("nombre", flat=True))
        self.assertEqual(len(nombres), len(set(nombres)), nombres)

    def test_cada_escenario_semilla_esta_activo_y_tiene_criterios(self):
        for e in EscenarioDePrueba.objects.all():
            self.assertTrue(e.activo, e.nombre)
            self.assertGreaterEqual(len(e.criterios), 1, e.nombre)
            self.assertTrue(e.criterios[0].strip(), e.nombre)

    def test_max_turns_es_razonable(self):
        for e in EscenarioDePrueba.objects.all():
            self.assertGreater(e.max_turns, 0, e.nombre)
            self.assertLessEqual(e.max_turns, 30, e.nombre)
