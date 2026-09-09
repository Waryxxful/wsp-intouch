from django.test import TestCase

from bot.models import VehiculoCatalogo


class VehiculoCatalogoModelTest(TestCase):
    def test_str_incluye_modelo_y_version(self):
        vehiculo = VehiculoCatalogo.objects.create(modelo="Koleos", version="techno 2.0T")
        self.assertEqual(str(vehiculo), "Koleos techno 2.0T")

    def test_str_sin_version_no_deja_espacio_colgando(self):
        vehiculo = VehiculoCatalogo.objects.create(modelo="Koleos")
        self.assertEqual(str(vehiculo), "Koleos")

    def test_defaults_de_precio_specs_url_fuente(self):
        vehiculo = VehiculoCatalogo.objects.create(modelo="Koleos")
        self.assertIsNone(vehiculo.precio)
        self.assertEqual(vehiculo.specs, {})
        self.assertEqual(vehiculo.url_fuente, "")
