from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0023_add_cliente_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='sucursal',
            name='categorias',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='sucursal',
            name='categorias_actualizado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
