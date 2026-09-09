from django.db import models


class Lead(models.Model):
    rut = models.CharField(max_length=12)
    nombre = models.CharField(max_length=200)
    telefono = models.CharField(max_length=20)
    razon_interes = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.nombre} ({self.rut})"
