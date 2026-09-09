import base64
import concurrent.futures
import logging

from django.conf import settings
from langchain.messages import HumanMessage
from langchain_openrouter import ChatOpenRouter
from langfuse import observe
from langfuse.langchain import CallbackHandler

from bot.whatsapp.media import download_media
from bot.whatsapp.media_storage import guardar_media_privado

logger = logging.getLogger(__name__)

# Bug real en produccion (2026-08-19): con Gemini degradado (503/504,
# "high demand"), los reintentos internos del SDK google-genai hicieron que
# una sola llamada a Gemini superara el --timeout 120 de gunicorn
# (Dockerfile). Gunicorn mata al worker con SIGABRT -> SystemExit, que NO es
# subclase de Exception -- el except Exception de _invoke_media nunca lo
# atrapa, y la request se pierde sin respuesta ni fallback. Se acota la
# llamada a este plazo propio (bien por debajo de esos 120s) para que
# _invoke_media siempre devuelva el control antes de que gunicorn intervenga,
# sin importar cuanto reintente el SDK por su cuenta.
_MEDIA_LLM_DEADLINE_SEGUNDOS = 60
_media_llm_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# El prompt NO nombra ninguna marca, y es a proposito. Hasta el 2026-09-07
# decia "una venta de autos Renault": este repo es un clon de wsp_demo
# (Renault/Astara) adaptado a Cavem, y ese texto se le mandaba tal cual al
# modelo de vision cada vez que un cliente de Cavem mandaba una foto -- el
# ultimo resto del clon que quedaba vivo y en el camino caliente.
#
# Por que no se reemplazo por "Cavem" ni por settings.CLIENTE_ACTIVO:
#   1. Cavem vende usados MULTIMARCA (VehiculoUsado, ver CLAUDE.md), asi que
#      nombrar una marca sesga al modelo justo en la frase siguiente, que es
#      la que le pide identificar la marca del auto de la foto.
#   2. Un nombre fijo en codigo se desincroniza del cliente activo -- la suite
#      corre con CLIENTE_ACTIVO=renault mientras el bot corre como cavem, asi
#      que un "Cavem" hardcodeado pasa el test y dice otra cosa en produccion.
#   3. El nombre del cliente no le aporta nada a esta llamada: lo unico que
#      necesita es saber que mira una foto en una conversacion de venta de
#      autos, para desambiguar (una foto de un tablero es "la luz del check
#      engine", no "un plastico negro"). Quien si nombra la marca es el prompt
#      global (bot/flow/global_prompt.py), que es el texto que redacta lo que
#      el cliente lee -- aca solo se percibe.
_PROMPT_DESCRIBIR_IMAGEN = (
    "Describe brevemente en español qué se ve en esta imagen, en 1-2 frases, "
    "pensando en el contexto de una conversación con una concesionaria de "
    "autos. Si se ve un auto, intenta identificar la marca/modelo si es "
    "reconocible. Responde solo con la descripción, sin comentarios adicionales."
)
_PROMPT_TRANSCRIBIR_AUDIO = (
    "Transcribe exactamente lo que dice este audio, en español. Responde solo "
    "con la transcripción, sin comentarios adicionales."
)


def _get_media_llm():
    from bot.models import get_setting
    # openrouter_api_key_override: mismo override que usa el LLM conversacional
    # (bot/flow/graph.py::_get_llm) -- media y conversacional comparten una
    # sola API key de OpenRouter, solo el modelo difiere.
    model = get_setting("openrouter_media_model_override", "") or settings.OPENROUTER_MEDIA_MODEL
    api_key = get_setting("openrouter_api_key_override", "") or settings.OPENROUTER_API_KEY
    # timeout es en MILISEGUNDOS para ChatOpenRouter (no segundos, a diferencia
    # de ChatGoogleGenerativeAI que se usaba antes) -- bug real encontrado al
    # verificar esta migracion: un `timeout=30` (30ms, casi instantaneo) hacia
    # que CUALQUIER llamada real fallara al toque y entrara al retry interno
    # del SDK (default max_retries=2, hasta ~300s de backoff por llamada, ver
    # su docstring) -- el future.result(timeout=_MEDIA_LLM_DEADLINE_SEGUNDOS)
    # de abajo SI devolvia el control a tiempo (None), pero el hilo del
    # ThreadPoolExecutor quedaba colgado hasta ~5 minutos, agotando los 2
    # workers de _media_llm_executor ante 2 imagenes/audios seguidos y dejando
    # el resto en cola sin procesar durante ese lapso. max_retries=0 porque
    # _MEDIA_LLM_DEADLINE_SEGUNDOS + el ThreadPoolExecutor YA acotan un solo
    # intento -- el retry interno del SDK es redundante y peligroso aca.
    return ChatOpenRouter(
        model=model, api_key=api_key, timeout=30_000, max_retries=0,
        reasoning={"effort": "medium"},
    )


def _invoke_media(prompt: str, content_block: dict, label: str) -> str | None:
    from bot.flow.graph import _texto_de_respuesta
    try:
        llm = _get_media_llm()
        mensaje = HumanMessage(content=[{"type": "text", "text": prompt}, content_block])
        # CallbackHandler() fresco: sin contexto propio, se engancha al trace
        # activo -- antes de esto, ninguna llamada de percepcion de medios
        # quedaba registrada en Langfuse (ver @observe en resolve_image_text/
        # resolve_audio_text mas abajo, que es lo que ahora abre ese trace).
        future = _media_llm_executor.submit(
            llm.invoke, [mensaje], config={"callbacks": [CallbackHandler()]},
        )
        resultado = future.result(timeout=_MEDIA_LLM_DEADLINE_SEGUNDOS)
        texto = _texto_de_respuesta(resultado.content, separador=" ").strip()
        return texto or None
    except Exception:
        logger.exception("[media] fallo al procesar %s con OpenRouter", label)
        return None


def describe_image(contenido: bytes, mime_type: str) -> str | None:
    data = base64.b64encode(contenido).decode("utf-8")
    content_block = {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{data}"}}
    return _invoke_media(_PROMPT_DESCRIBIR_IMAGEN, content_block, "imagen")


# OpenRouter exige el content block "input_audio" con un "format" -- token
# corto (ver docs), no el mime type completo. WhatsApp entrega casi siempre
# "audio/ogg" (notas de voz, codec opus) ya normalizado por
# _mime_type_normalizado -- el resto de las entradas es para archivos de
# audio subidos directo. Un mime no listado NUNCA se manda al LLM (mismo
# contrato best-effort que el resto de este modulo, pero cortando ANTES de
# gastar la llamada).
_AUDIO_MIME_A_FORMATO = {
    "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/flac": "flac", "audio/x-flac": "flac",
    "audio/mp4": "m4a", "audio/x-m4a": "m4a",
}


def transcribe_audio(contenido: bytes, mime_type: str) -> str | None:
    formato = _AUDIO_MIME_A_FORMATO.get(mime_type)
    if formato is None:
        logger.warning("[media] mime type de audio no soportado por OpenRouter: %r", mime_type)
        return None
    data = base64.b64encode(contenido).decode("utf-8")
    content_block = {"type": "input_audio", "input_audio": {"data": data, "format": formato}}
    return _invoke_media(_PROMPT_TRANSCRIBIR_AUDIO, content_block, "audio")


def _mime_type_normalizado(mime_type: str | None) -> str | None:
    """WhatsApp/CDN entregan mime types que Gemini rechaza con 400
    INVALID_ARGUMENT: con parametros extra (`audio/ogg; codecs=opus`) o el
    genérico `application/octet-stream` que usa el fallback del download.
    Se recorta al tipo base y se descarta el genérico (devuelve None para
    que el caller pueda caer al siguiente candidato o abortar)."""
    if not mime_type:
        return None
    base = mime_type.split(";")[0].strip()
    if not base or base == "application/octet-stream":
        return None
    return base


# capture_input=False: evita volcar el media_id/mime_type_hint como input del
# span (sin dato sensible real aca, pero mismo criterio conservador que
# _run_graph en handlers.py). El trace real (prompt+imagen/audio en base64,
# respuesta del modelo) ya lo captura el CallbackHandler de _invoke_media
# como GENERATION anidada -- capturar tambien a nivel de este span duplicaria
# el payload grande sin agregar nada.
@observe(name="media-perception", capture_input=False, capture_output=False)
def resolve_image_text(media_id: str, mime_type_hint: str | None = None) -> tuple[str | None, str | None]:
    """Devuelve (texto_percibido, nombre_archivo_privado). El segundo valor
    es el nombre relativo bajo settings.WHATSAPP_MEDIA_ROOT (para
    Message.media_url) o None si el guardado no aplico/fallo --
    guardar_media_privado nunca lanza, asi que un fallo ahi nunca impide
    devolver el texto ya generado."""
    if not media_id:
        return None, None
    contenido, mime_type_descargado = download_media(media_id)
    if contenido is None:
        return None, None
    mime_type = _mime_type_normalizado(mime_type_hint) or _mime_type_normalizado(mime_type_descargado)
    if not mime_type:
        return None, None
    texto = describe_image(contenido, mime_type)
    media_path = guardar_media_privado(contenido, mime_type, "image")
    return texto, media_path


@observe(name="media-perception", capture_input=False, capture_output=False)
def resolve_audio_text(media_id: str, mime_type_hint: str | None = None) -> tuple[str | None, str | None]:
    """Ver docstring de resolve_image_text -- mismo contrato (texto, nombre
    de archivo privado o None)."""
    if not media_id:
        return None, None
    contenido, mime_type_descargado = download_media(media_id)
    if contenido is None:
        return None, None
    mime_type = _mime_type_normalizado(mime_type_hint) or _mime_type_normalizado(mime_type_descargado)
    if not mime_type:
        return None, None
    texto = transcribe_audio(contenido, mime_type)
    media_path = guardar_media_privado(contenido, mime_type, "audio")
    return texto, media_path
