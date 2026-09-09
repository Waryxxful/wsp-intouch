from django.db import models


class AuditLog(models.Model):
    user = models.CharField(max_length=150)
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=200, blank=True, default="")
    details = models.CharField(max_length=500, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class QuickResponse(models.Model):
    pattern = models.CharField(max_length=200)
    response = models.TextField()
    priority = models.IntegerField(default=0)

    class Meta:
        ordering = ["-priority", "id"]

    def __str__(self):
        return self.pattern


class Snippet(models.Model):
    nombre = models.CharField(max_length=100)
    texto = models.TextField()

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class BusinessHours(models.Model):
    dia_semana = models.IntegerField(unique=True)  # 0=lunes .. 6=domingo
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["dia_semana"]


class Filter(models.Model):
    TIPO_CHOICES = [("block", "Bloqueo"), ("allow", "Permitido")]
    wa_id = models.CharField(max_length=32)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.tipo}:{self.wa_id}"


class HandoffConfig(models.Model):
    keywords = models.JSONField(default=list, blank=True)
