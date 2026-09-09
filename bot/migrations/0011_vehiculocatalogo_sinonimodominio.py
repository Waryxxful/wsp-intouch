from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0010_scrapedpage_imagenes_imagenconvertida'),
    ]

    operations = [
        migrations.CreateModel(
            name='SinonimoDominio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('termino_canonico', models.CharField(max_length=100, unique=True)),
                ('alias', models.JSONField(blank=True, default=list)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='VehiculoCatalogo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('modelo', models.CharField(max_length=100)),
                ('version', models.CharField(blank=True, default='', max_length=150)),
                ('precio', models.IntegerField(blank=True, null=True)),
                ('specs', models.JSONField(blank=True, default=dict)),
                ('url_fuente', models.URLField(blank=True, default='')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['modelo', 'version'],
            },
        ),
    ]
