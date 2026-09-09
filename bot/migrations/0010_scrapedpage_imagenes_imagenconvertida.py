from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0009_scrapingsource_url_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='scrapedpage',
            name='imagenes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name='ImagenConvertida',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url_original', models.URLField(unique=True)),
                ('archivo', models.ImageField(upload_to='model_images/')),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
