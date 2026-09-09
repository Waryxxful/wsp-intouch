from django.db import migrations


def marcar_usados_por_url(apps, schema_editor):
    # Backfill: el AddField de la migracion anterior dejo todas las filas
    # existentes en "0km" por default -- esto corrige las que en realidad
    # vinieron de la seccion de seminuevos del sitio (ver comentario en
    # VehiculoCatalogo.condicion, docs/PENDIENTES.md).
    VehiculoCatalogo = apps.get_model("bot", "VehiculoCatalogo")
    VehiculoCatalogo.objects.filter(url_fuente__icontains="seminuevos").update(condicion="usado")


def revertir(apps, schema_editor):
    VehiculoCatalogo = apps.get_model("bot", "VehiculoCatalogo")
    VehiculoCatalogo.objects.filter(url_fuente__icontains="seminuevos").update(condicion="0km")


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0025_vehiculocatalogo_condicion'),
    ]

    operations = [
        migrations.RunPython(marcar_usados_por_url, revertir),
    ]
