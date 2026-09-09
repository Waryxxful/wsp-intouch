from django.core.management.base import BaseCommand

from bot.business.geocoding import geocodificar_direccion
from bot.models import Sucursal


class Command(BaseCommand):
    help = "Geocodifica las Sucursal existentes que no tienen latitud/longitud."

    def handle(self, *args, **options):
        pendientes = Sucursal.objects.filter(latitud__isnull=True) | Sucursal.objects.filter(longitud__isnull=True)
        pendientes = pendientes.distinct()
        geocodificadas, fallidas = 0, 0
        for sucursal in pendientes:
            coords = geocodificar_direccion(sucursal.direccion)
            if coords:
                sucursal.latitud, sucursal.longitud = coords
                sucursal.save(update_fields=["latitud", "longitud"])
                geocodificadas += 1
            else:
                fallidas += 1
                self.stdout.write(self.style.WARNING(
                    f"No se pudo geocodificar sucursal {sucursal.id} ({sucursal.nombre!r}): {sucursal.direccion!r}"
                ))
        self.stdout.write(self.style.SUCCESS(
            f"{geocodificadas} sucursales geocodificadas, {fallidas} fallidas."
        ))
