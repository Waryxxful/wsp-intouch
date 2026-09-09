from django.test import SimpleTestCase

from bot.scraping.normalizar import _normalizar_clave, _normalizar_direccion


class NormalizarClaveModuloCompartidoTest(SimpleTestCase):
    def test_ignora_mayusculas_acentos_y_puntuacion(self):
        self.assertEqual(_normalizar_clave("Garantía legal 3×3"), _normalizar_clave("Garantia legal 3x3"))

    def test_es_la_misma_funcion_que_reexporta_extractor(self):
        from bot.scraping.extractor import _normalizar_clave as _normalizar_clave_extractor
        self.assertIs(_normalizar_clave, _normalizar_clave_extractor)


class NormalizarDireccionTest(SimpleTestCase):
    def test_av_punto_y_avenida_dan_la_misma_clave(self):
        self.assertEqual(
            _normalizar_direccion("Av. Padre Hurtado 1389, Vitacura"),
            _normalizar_direccion("Avenida Padre Hurtado 1389, Vitacura"),
        )

    def test_avda_tambien_expande_a_avenida(self):
        self.assertEqual(
            _normalizar_direccion("Avda. Las Condes 12256"),
            _normalizar_direccion("Avenida Las Condes 12256"),
        )

    def test_psje_y_pasaje_dan_la_misma_clave(self):
        self.assertEqual(
            _normalizar_direccion("Psje. Los Aromos 123"),
            _normalizar_direccion("Pasaje Los Aromos 123"),
        )

    def test_pdte_y_presidente_dan_la_misma_clave(self):
        self.assertEqual(
            _normalizar_direccion("Av. Pdte. Jorge Alessandri 20150"),
            _normalizar_direccion("Avenida Presidente Jorge Alessandri 20150"),
        )

    def test_dos_direcciones_realmente_distintas_no_matchean(self):
        # Caso real: "Rancagua (Alameda)" tiene direcciones DISTINTAS para
        # Ventas (Alameda 232) y Servicio Tecnico (Alameda 214) -- deben
        # seguir siendo dos claves distintas.
        self.assertNotEqual(
            _normalizar_direccion("Av. Alameda 232, Rancagua"),
            _normalizar_direccion("Av. Alameda 214, Rancagua"),
        )

    def test_normaliza_acentos_y_puntuacion_igual_que_normalizar_clave(self):
        self.assertEqual(
            _normalizar_direccion("Av. José Alcalde Delano 10371"),
            _normalizar_direccion("Avenida Jose Alcalde Délano 10.371"),
        )
