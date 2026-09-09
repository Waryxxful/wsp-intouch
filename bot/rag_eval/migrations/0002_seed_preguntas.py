from django.db import migrations

PREGUNTAS_SEMILLA = [
    {
        "query": "cuanto dura la garantia de un renault nuevo",
        "fuente_esperada": "https://renault.cl/garantia/",
    },
    {
        "query": "en que horario abren la sucursal de providencia",
        "fuente_esperada": "https://renault.cl/sucursales/",
    },
    {
        "query": "que cubre la garantia del motor",
        "fuente_esperada": "https://renault.cl/garantia/",
    },
    {
        "query": "cuales son las politicas de devolucion",
        "fuente_esperada": "https://renault.cl/politicas/",
    },
    {
        "query": "hacen mantenciones los sabados",
        "fuente_esperada": "https://renault.cl/servicio-tecnico/",
    },
]


def sembrar(apps, schema_editor):
    PreguntaEvaluacionRag = apps.get_model("rag_eval", "PreguntaEvaluacionRag")
    for p in PREGUNTAS_SEMILLA:
        PreguntaEvaluacionRag.objects.get_or_create(query=p["query"], defaults=p)


def despoblar(apps, schema_editor):
    PreguntaEvaluacionRag = apps.get_model("rag_eval", "PreguntaEvaluacionRag")
    PreguntaEvaluacionRag.objects.filter(query__in=[p["query"] for p in PREGUNTAS_SEMILLA]).delete()


class Migration(migrations.Migration):
    dependencies = [("rag_eval", "0001_initial")]
    operations = [migrations.RunPython(sembrar, despoblar)]
