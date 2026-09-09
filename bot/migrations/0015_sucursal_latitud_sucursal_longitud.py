from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0014_conversation_lead_class_conversation_rut_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sucursal',
            name='latitud',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sucursal',
            name='longitud',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
