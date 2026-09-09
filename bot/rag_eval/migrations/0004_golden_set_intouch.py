"""Golden set de InTouch.

Se siembra por migracion para que exista desde el primer deploy: armarlo
"despues" es exactamente lo que no paso en Cavem (biblia SVI.4) -- el golden
set es el unico numero objetivo que este bot va a tener, y sin un recall de
partida anotado cualquier cambio futuro al RAG se juzga por impresion.

Las preguntas estan escritas como las escribe un contacto real -- en
minusculas, sin signos, con abreviaturas -- porque es justo donde el coseno
solo falla y el retrieval hibrido gana.

`fuente_esperada` usa el mismo valor que `evaluar_rag` compara contra
`fuente_url` de cada chunk (comparacion exacta, no por substring): el
prefijo `file:///app/bot/fixtures/rag/<archivo>.md` que le asigna
`cargar_conocimiento_rag` (bot/management/commands/cargar_conocimiento_rag.py,
URL_FUENTE) a cada documento de conocimiento del repo. Un `fuente_esperada`
con solo el nombre de archivo nunca matchearia nada y el recall medido
seria siempre cero, sin importar que tan bueno sea el retrieval.
"""
from django.db import migrations

PREFIJO_FUENTE = "file:///app/bot/fixtures/rag"

PREGUNTAS = [
    ("qué hace intouch", "sobre-intouch.md"),
    ("a qué se dedican ustedes", "sobre-intouch.md"),
    ("qué soluciones ofrecen", "soluciones.md"),
    ("tienen agentes conversacionales con ia", "soluciones.md"),
    ("pueden diseñar una operación a medida", "soluciones.md"),
    ("qué modelos de operación tienen", "modelos-de-operacion.md"),
    ("en qué se diferencia lo híbrido de que todo lo atienda la ia", "modelos-de-operacion.md"),
    ("cuándo conviene una operación humana", "modelos-de-operacion.md"),
    ("atienden por whatsapp", "canales.md"),
    ("trabajan con llamadas de voz", "canales.md"),
    ("se puede atender por correo", "canales.md"),
    ("tienen dashboards", "analitica-y-calidad.md"),
    ("hacen control de calidad de las conversaciones", "analitica-y-calidad.md"),
    ("trabajan con power bi", "analitica-y-calidad.md"),
    ("se integran con mi crm", "integraciones.md"),
    ("pueden conectarse a un erp", "integraciones.md"),
    ("qué pasa con los datos de mis clientes", "datos-y-seguridad.md"),
    ("cómo tratan los datos personales", "datos-y-seguridad.md"),
    ("cumplen con la ley de datos personales", "datos-y-seguridad.md"),
]


def sembrar(apps, schema_editor):
    Pregunta = apps.get_model("rag_eval", "PreguntaEvaluacionRag")
    Resultado = apps.get_model("rag_eval", "ResultadoEvaluacionRag")

    # Las preguntas heredadas (Renault/Cavem, sembradas en 0002/0003) apuntan
    # a documentos que este RAG no tiene: dejarlas activas mediria el recall
    # contra fuentes inexistentes, puro ruido. Se borran -- salvo que ya
    # tengan resultados de evaluacion corridos, porque ResultadoEvaluacionRag
    # protege esa relacion (on_delete=PROTECT): borrar la pregunta ahi
    # reventaria la migracion y, si no reventara, perderiamos ese historial.
    # Para esas se preserva el mismo patron que ya usa
    # 0003_corrige_preguntas_semilla_con_urls_reales.py: desactivar en vez
    # de borrar.
    heredadas = Pregunta.objects.all()
    con_resultados = set(
        Resultado.objects.filter(pregunta__in=heredadas).values_list("pregunta_id", flat=True)
    )
    heredadas.exclude(id__in=con_resultados).delete()
    heredadas.filter(id__in=con_resultados).update(activo=False)

    for query, archivo in PREGUNTAS:
        Pregunta.objects.create(
            query=query, fuente_esperada=f"{PREFIJO_FUENTE}/{archivo}",
        )


def revertir(apps, schema_editor):
    Pregunta = apps.get_model("rag_eval", "PreguntaEvaluacionRag")
    Pregunta.objects.filter(query__in=[q for q, _ in PREGUNTAS]).delete()


class Migration(migrations.Migration):
    dependencies = [("rag_eval", "0003_corrige_preguntas_semilla_con_urls_reales")]
    operations = [migrations.RunPython(sembrar, revertir)]
