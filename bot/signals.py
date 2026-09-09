from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from bot.business.geocoding import geocodificar_direccion
from bot.models import Sucursal


@receiver(pre_save, sender=Sucursal)
def _limpiar_coords_si_cambio_direccion(sender, instance, **kwargs):
    if not instance.pk:
        return
    anterior = Sucursal.objects.filter(pk=instance.pk).values("direccion").first()
    if anterior and anterior["direccion"] != instance.direccion:
        instance.latitud = None
        instance.longitud = None


@receiver(post_save, sender=Sucursal)
def _geocodificar_sucursal(sender, instance, **kwargs):
    if instance.latitud is not None and instance.longitud is not None:
        return
    coords = geocodificar_direccion(instance.direccion)
    if coords:
        Sucursal.objects.filter(pk=instance.pk).update(latitud=coords[0], longitud=coords[1])
