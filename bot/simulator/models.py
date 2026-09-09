from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class EscenarioDePrueba(models.Model):
    """Reemplaza la lista ESCENARIOS hardcodeada de scenarios.py como fuente
    de verdad -- editable por completo desde el panel."""
    nombre = models.CharField(max_length=200, unique=True)
    persona = models.TextField()
    objetivo = models.TextField()
    criterios = models.JSONField(default=list)
    fuente = models.TextField(blank=True, default="")
    max_turns = models.IntegerField(default=12)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class CorridaDePrueba(models.Model):
    ESTADO_CHOICES = [
        ("corriendo", "Corriendo"),
        ("completa", "Completa"),
        ("interrumpida", "Interrumpida"),
        ("error", "Error"),
    ]

    fecha_inicio = models.DateTimeField(auto_now_add=True)
    fecha_fin = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="corriendo")
    disparada_por = models.CharField(max_length=150)
    nombre_escenario_filtro = models.CharField(max_length=200, null=True, blank=True)
    # No esta en el listado original del spec (seccion 1) -- se agrega para
    # que el chequeo de staleness (spec seccion 3, punto 4) tenga una señal
    # real de "ultima actividad": runner.py toca este campo cada vez que
    # guarda un ResultadoDeEscenario, asi que una corrida que dejo de
    # actualizarse hace mas de N minutos es indistinguible de un hilo
    # perdido por un reinicio del servidor.
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_inicio"]

    def __str__(self):
        return f"corrida {self.pk} ({self.estado})"


class ResultadoDeEscenario(models.Model):
    corrida = models.ForeignKey(CorridaDePrueba, on_delete=models.CASCADE, related_name="resultados")
    # PROTECT (no CASCADE ni SET_NULL): un escenario con historial de
    # corridas no se puede borrar de casualidad -- el panel debe forzar a
    # desactivarlo en su lugar (ver admin_panel/views.py::api_test_scenario_detail).
    escenario = models.ForeignKey(EscenarioDePrueba, on_delete=models.PROTECT, related_name="resultados")
    paso = models.BooleanField(null=True)
    fallos = models.JSONField(default=list, blank=True)
    transcript = models.TextField(blank=True, default="")
    error = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"resultado {self.pk} de {self.escenario_id} en corrida {self.corrida_id}"


def marcar_corridas_stale_como_interrumpidas() -> int:
    """Una corrida en estado 'corriendo' cuyo thread en background se perdio
    (ej. reinicio del servidor a mitad de una corrida, spec seccion 3 punto
    4) se queda 'corriendo' para siempre si nada la corrige -- esta funcion
    la reclasifica como 'interrumpida' despues de N minutos sin actividad
    (actualizado_en, que runner.py::_guardar_resultado toca en cada
    escenario). Se llama al listar corridas (admin_panel/views.py::
    api_test_runs), no en un cron aparte -- no hace falta mas que eso para
    que el panel deje de mostrarla como en progreso para siempre."""
    limite = timezone.now() - timedelta(minutes=getattr(settings, "SIMULATOR_STALENESS_MINUTES", 20))
    return CorridaDePrueba.objects.filter(estado="corriendo", actualizado_en__lt=limite).update(estado="interrumpida")
