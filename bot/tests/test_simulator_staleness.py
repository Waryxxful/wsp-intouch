from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from bot.simulator.models import CorridaDePrueba, marcar_corridas_stale_como_interrumpidas


class MarcarCorridasStaleTest(TestCase):
    def _crear_corrida_corriendo(self, minutos_atras: int) -> CorridaDePrueba:
        corrida = CorridaDePrueba.objects.create(disparada_por="admin@test.com")
        # update() (no save()) para no disparar auto_now de actualizado_en.
        CorridaDePrueba.objects.filter(pk=corrida.pk).update(
            actualizado_en=timezone.now() - timedelta(minutes=minutos_atras)
        )
        corrida.refresh_from_db()
        return corrida

    @override_settings(SIMULATOR_STALENESS_MINUTES=20)
    def test_marca_interrumpida_una_corrida_corriendo_sin_actividad_reciente(self):
        corrida = self._crear_corrida_corriendo(minutos_atras=25)

        marcados = marcar_corridas_stale_como_interrumpidas()

        corrida.refresh_from_db()
        self.assertEqual(marcados, 1)
        self.assertEqual(corrida.estado, "interrumpida")

    @override_settings(SIMULATOR_STALENESS_MINUTES=20)
    def test_no_toca_una_corrida_corriendo_con_actividad_reciente(self):
        corrida = self._crear_corrida_corriendo(minutos_atras=5)

        marcar_corridas_stale_como_interrumpidas()

        corrida.refresh_from_db()
        self.assertEqual(corrida.estado, "corriendo")

    @override_settings(SIMULATOR_STALENESS_MINUTES=20)
    def test_no_toca_corridas_que_ya_terminaron(self):
        corrida = self._crear_corrida_corriendo(minutos_atras=60)
        CorridaDePrueba.objects.filter(pk=corrida.pk).update(estado="completa")

        marcar_corridas_stale_como_interrumpidas()

        corrida.refresh_from_db()
        self.assertEqual(corrida.estado, "completa")

    def test_usa_20_minutos_por_default_sin_el_setting(self):
        corrida = self._crear_corrida_corriendo(minutos_atras=25)

        marcar_corridas_stale_como_interrumpidas()

        corrida.refresh_from_db()
        self.assertEqual(corrida.estado, "interrumpida")
