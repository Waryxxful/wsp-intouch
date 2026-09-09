from django.db import migrations

from bot.scraping.backfill import agrupar_runs_en_fuentes


def backfill(apps, schema_editor):
    ScrapeRun = apps.get_model("bot", "ScrapeRun")
    ScrapingSource = apps.get_model("bot", "ScrapingSource")
    Setting = apps.get_model("bot", "Setting")
    agrupar_runs_en_fuentes(ScrapeRun, ScrapingSource, Setting)


class Migration(migrations.Migration):
    dependencies = [("bot", "0007_scrapingsource_scraperun_fields")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
