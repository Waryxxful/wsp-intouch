from django.db import migrations

# Task 9, Step 7 del plan (docs/superpowers/plans/2026-08-25-rag-hibrido-rerank-multimarca.md):
# las 5 preguntas de 0002_seed_preguntas.py usaban URLs ficticias
# (renault.cl/garantia/, /sucursales/, /politicas/, /servicio-tecnico/ -- ninguna
# existe en el corpus real). No se edita la migracion ya aplicada -- se
# desactivan esas 5 y se agregan 5 nuevas verificadas contra
# `select fuente_url, categoria, contenido from renault.documentos_conocimiento`
# tras el reindex de Task 9 Step 7B (2026-08-25). "sucursales" no se
# reemplaza por un equivalente real: Task 7 saco Sucursal del RAG por
# completo y el prompt de faq.py ya no rutea preguntas de sucursal por RAG
# (ver bot/flow/agents/faq.py) -- no hay ninguna fuente real que ese caso
# deberia encontrar.

QUERIES_VIEJAS = [
    "cuanto dura la garantia de un renault nuevo",
    "en que horario abren la sucursal de providencia",
    "que cubre la garantia del motor",
    "cuales son las politicas de devolucion",
    "hacen mantenciones los sabados",
]

PREGUNTAS_REALES = [
    {
        "query": "cuanto dura la garantia del renault master",
        "fuente_esperada": "https://renault.cl/wp-content/uploads/2026/06/ficha_tecnica_master.pdf",
    },
    {
        "query": "cada cuantos kilometros hay que llevar el master a mantencion",
        "fuente_esperada": "https://renault.cl/wp-content/uploads/2026/06/ficha_tecnica_master.pdf",
    },
    {
        "query": "cuantos anios de servicio gratuito trae el koleos",
        "fuente_esperada": "https://renault.cl/wp-content/uploads/2026/06/ficha-koleos.pdf",
    },
    {
        "query": "que garantia tiene el arkana hybrid",
        "fuente_esperada": "https://renault.cl/wp-content/uploads/2026/06/ficha_arkana-hybrid.pdf",
    },
    {
        "query": "que anuncio renault group sobre futuready",
        "fuente_esperada": "https://renault.cl/noticia/renault-group-lanza-futuready-y-abre-una-nueva-era-estrategica/",
    },
]


def corregir(apps, schema_editor):
    PreguntaEvaluacionRag = apps.get_model("rag_eval", "PreguntaEvaluacionRag")
    PreguntaEvaluacionRag.objects.filter(query__in=QUERIES_VIEJAS).update(activo=False)
    for p in PREGUNTAS_REALES:
        PreguntaEvaluacionRag.objects.get_or_create(query=p["query"], defaults=p)


def revertir(apps, schema_editor):
    PreguntaEvaluacionRag = apps.get_model("rag_eval", "PreguntaEvaluacionRag")
    PreguntaEvaluacionRag.objects.filter(query__in=QUERIES_VIEJAS).update(activo=True)
    PreguntaEvaluacionRag.objects.filter(
        query__in=[p["query"] for p in PREGUNTAS_REALES]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("rag_eval", "0002_seed_preguntas")]
    operations = [migrations.RunPython(corregir, revertir)]
