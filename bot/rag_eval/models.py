from django.db import models


class PreguntaEvaluacionRag(models.Model):
    """Un caso del arnes de evaluacion del RAG: una query real o realista +
    la fuente que se espera encontrar entre los resultados. Mismo patron que
    EscenarioDePrueba (bot/simulator/models.py) para el simulador de
    conversaciones -- editable a mano, arranca con una semilla via
    migracion de datos (ver 0002_seed_preguntas.py)."""
    query = models.TextField()
    fuente_esperada = models.CharField(max_length=500)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.query


class ResultadoEvaluacionRag(models.Model):
    pregunta = models.ForeignKey(PreguntaEvaluacionRag, on_delete=models.PROTECT, related_name="resultados")
    corrida_en = models.DateTimeField(auto_now_add=True)
    recall_ok = models.BooleanField()
    relevancia_score = models.IntegerField(null=True, blank=True)
    detalle = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-corrida_en"]

    def __str__(self):
        return f"resultado de {self.pregunta_id} ({self.corrida_en})"
