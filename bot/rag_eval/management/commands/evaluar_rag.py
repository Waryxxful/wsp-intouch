import asyncio

from django.core.management.base import BaseCommand

from bot.rag.tool import _buscar_en_supabase, _rerankear
from bot.rag_eval.judge import evaluar_relevancia
from bot.rag_eval.models import PreguntaEvaluacionRag, ResultadoEvaluacionRag


async def _evaluar_una(pregunta: PreguntaEvaluacionRag) -> dict:
    """Solo la parte async (busqueda + rerank) -- el juez (llamada al LLM,
    potencialmente sincrona/con acceso a BD en el futuro) y la escritura a
    BD quedan fuera de esta coroutine a proposito: el ORM sincrono no se
    puede llamar directamente desde dentro de un asyncio.run() (Django
    levanta SynchronousOnlyOperation), mismo motivo por el que el resto de
    bot/business/*.py envuelve sus llamadas ORM con sync_to_async en vez de
    hacerlas sueltas dentro de una funcion async."""
    candidatos = await _buscar_en_supabase(pregunta.query)
    relevantes = await _rerankear(pregunta.query, candidatos) if candidatos else []
    recall_ok = pregunta.fuente_esperada in {c["fuente_url"] for c in relevantes}
    return {
        "recall_ok": recall_ok,
        "textos_relevantes": [c["contenido"] for c in relevantes],
        "detalle": {"candidatos": len(candidatos), "relevantes": len(relevantes)},
    }


class Command(BaseCommand):
    help = (
        "Corre el arnes de evaluacion del RAG contra las preguntas activas de "
        "PreguntaEvaluacionRag: recall@k (¿aparece la fuente esperada?) y relevancia "
        "percibida (juez LLM, 1-5). Disparo manual, nunca automatico -- mismo criterio "
        "que el simulador de conversaciones."
    )

    def handle(self, *args, **options):
        preguntas = list(PreguntaEvaluacionRag.objects.filter(activo=True))
        if not preguntas:
            self.stdout.write(self.style.WARNING("no hay ninguna pregunta activa para evaluar"))
            return

        resultados = []
        for p in preguntas:
            parcial = asyncio.run(_evaluar_una(p))
            textos_relevantes = parcial.pop("textos_relevantes")
            relevancia_score = evaluar_relevancia(p.query, textos_relevantes)
            resultados.append(ResultadoEvaluacionRag.objects.create(
                pregunta=p, relevancia_score=relevancia_score, **parcial,
            ))

        recall_promedio = sum(1 for r in resultados if r.recall_ok) / len(resultados)
        con_score = [r.relevancia_score for r in resultados if r.relevancia_score is not None]
        relevancia_promedio = sum(con_score) / len(con_score) if con_score else None

        self.stdout.write(self.style.SUCCESS(
            f"{len(resultados)} preguntas evaluadas -- "
            f"recall@k promedio: {recall_promedio:.2f}, "
            f"relevancia promedio: {relevancia_promedio if relevancia_promedio is None else round(relevancia_promedio, 2)}"
        ))
        for r in resultados:
            estado = "OK" if r.recall_ok else "FALLO"
            self.stdout.write(f"  [{estado}] {r.pregunta.query!r} (relevancia={r.relevancia_score})")
