"""Verificacion de salud de un bot, contra el sistema REAL.

POR QUE EXISTE (auditoria 2026-09-08, /home/admincrm/docs-repo/biblia_bots.md §IV.2):

Cada chequeo de este comando corresponde a un incidente que ya ocurrio y que se
descubrio TARDE, casi siempre porque el sintoma no era un error sino un
silencio:

  - los `grant` de Supabase no estaban en el DDL: `renault`/`astara` funcionaban
    porque se corrieron a mano una vez, y una marca nueva creada siguiendo el
    archivo al pie de la letra quedaba con una tabla que PostgREST no puede leer
    -- el sintoma aparecia recien al indexar (docs/PENDIENTES.md #1);
  - `cargar_conocimiento_rag` no existia y `reindexar_conocimiento_rag`
    imprimia "0 paginas reindexadas" sin fallar: el RAG quedaba VACIO con todo
    "configurado";
  - un scrape corrido con `RAG_SCHEMA` de otra marca mando 297 chunks de Astara
    al schema de Renault;
  - `reasoning: {"effort": "medium"}` no es soportado por OPENROUTER_MODEL y
    OpenRouter lo remapea a `high` EN SILENCIO -- nadie lo supo hasta que una
    auditoria de latencia lo encontro leyendo trazas;
  - el prompt activo de `ventas` (que vive en la BD, no en git) siguio diciendo
    "llama a registrar_datos_lead" despues de que la tool se desbindeara del
    especialista. Bloqueo un deploy.

El comando es de SOLO LECTURA: no escribe en la BD, no publica prompts, no
indexa. Sale con codigo != 0 si hay al menos una falla, para poder usarlo como
puerta antes de un deploy.

    python manage.py doctor
    python manage.py doctor --sin-red          # solo chequeos locales
    python manage.py doctor --seccion prompts  # una seccion
"""
import os
import re
from dataclasses import dataclass

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

OK, AVISO, FALLA = "ok", "aviso", "falla"

_SIMBOLO = {OK: "✓", AVISO: "!", FALLA: "✗"}

URL_CATALOGO_OPENROUTER = "https://openrouter.ai/api/v1/models"
_TIMEOUT_RED = 20.0

# Que nivel de `reasoning.effort` le pasa el CODIGO a cada modelo. Se declara
# aca (y no se descubre solo) porque el valor esta en la llamada, no en la
# config -- pero NO queda librado a que alguien se acuerde de actualizarlo:
# bot/tests/test_doctor.py lee bot/flow/graph.py y falla si aparece un nivel
# que esta tabla no cubre. Mismo patron que la regresion del orden de los
# `migrate` del Dockerfile (leads/tests.py).
#
# Referencias exactas del codigo que los pasa:
#   OPENROUTER_MODEL         -> graph.py::_get_llm (default "medium") y
#                               graph.py::specialist_node ("none" en la primera
#                               llamada del turno, la que elige la tool)
#   OPENROUTER_ROUTING_MODEL -> graph.py::_get_routing_llm ("none") y
#                               extractor_metadatos.py::_get_extractor_llm ("none")
#   OPENROUTER_MEDIA_MODEL   -> media_processing.py::_get_media_llm ("medium")
#   OPENROUTER_SCRAPING_MODEL-> scraping/extractor.py::_get_llm, que manda
#                               {"enabled": False} y no un effort: no se declara.
ESFUERZOS_QUE_PASA_EL_CODIGO = {
    "OPENROUTER_MODEL": {"medium", "none"},
    "OPENROUTER_ROUTING_MODEL": {"none"},
    "OPENROUTER_MEDIA_MODEL": {"medium"},
}

# Capacidades que cada rol NECESITA del modelo, y que el catalogo de OpenRouter
# publica en `supported_parameters`.
CAPACIDADES_POR_ROL = {
    # El especialista bindea tools nativas: sin esto no hay bot.
    "OPENROUTER_MODEL": ["tools"],
    # El ruteo devuelve JSON de dos campos y el extractor de metadatos usa el
    # MISMO setting con response_format json_schema.
    "OPENROUTER_ROUTING_MODEL": ["structured_outputs"],
}

SETTINGS_DE_MODELO = [
    "OPENROUTER_MODEL", "OPENROUTER_ROUTING_MODEL", "OPENROUTER_MEDIA_MODEL",
    "OPENROUTER_SCRAPING_MODEL",
]

# Modulos donde viven las tools del bot. Se escanean por instancias de BaseTool
# en vez de listarlas a mano: una tool nueva entra sola al universo conocido.
MODULOS_CON_TOOLS = [
    "bot.business.agendamiento", "bot.business.catalogo", "bot.business.compliance",
    "bot.business.ventas", "bot.business.usados", "bot.business.prospeccion",
    "bot.business.encuestas", "bot.business.soluciones", "bot.rag.tool", "bot.flow.respuesta",
]


@dataclass
class Hallazgo:
    nivel: str
    titulo: str
    detalle: str = ""


def _falta(valor) -> bool:
    return not (valor or "").strip()


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def chequear_variables_obligatorias(opciones):
    """Sin una de estas el bot arranca igual y falla recien en el primer
    mensaje real -- que es la peor forma de enterarse."""
    faltantes = [
        nombre for nombre in (
            "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_VERIFY_TOKEN",
            "OPENROUTER_API_KEY", "GOOGLE_API_KEY", "PUBLIC_BASE_URL",
        ) if _falta(getattr(settings, nombre, ""))
    ]
    faltantes += [n for n in ("SUPABASE_URL", "SUPABASE_KEY") if _falta(os.environ.get(n))]
    if faltantes:
        yield Hallazgo(FALLA, "faltan variables obligatorias", ", ".join(faltantes))
    else:
        yield Hallazgo(OK, "variables obligatorias presentes")

    # CHANGEME es el default de settings.py: pasa cualquier chequeo de "no
    # vacio" y deja el webhook con un token de verificacion publico.
    if getattr(settings, "WHATSAPP_VERIFY_TOKEN", "") == "CHANGEME":
        yield Hallazgo(FALLA, "WHATSAPP_VERIFY_TOKEN quedo en el default 'CHANGEME'")


def chequear_token_interno(opciones):
    """El token que separa /internal/webhook de cualquiera que alcance el
    puerto. Sin el, la vista responde 503 y el bot deja de recibir del
    dispatcher -- o sea que un olvido aca se ve como 'el bot no contesta'."""
    token = getattr(settings, "WEBHOOK_INTERNAL_TOKEN", "")
    if not token:
        yield Hallazgo(
            FALLA, "WEBHOOK_INTERNAL_TOKEN no esta configurado",
            "/internal/webhook responde 503 y el dispatcher no puede entregar "
            "mensajes. Generar uno con `openssl rand -hex 32` y ponerlo IGUAL "
            "en el .env.docker de este bot y en el .env de wsp_webhook_intouch.",
        )
    elif len(token) < 32:
        yield Hallazgo(
            AVISO, f"WEBHOOK_INTERNAL_TOKEN es corto ({len(token)} chars)",
            "conviene 64 hex de `openssl rand -hex 32`.",
        )
    else:
        yield Hallazgo(OK, "WEBHOOK_INTERNAL_TOKEN configurado")


def chequear_cliente_activo(opciones):
    from bot.models import CLIENTE_CHOICES
    validos = [c[0] for c in CLIENTE_CHOICES]
    if settings.CLIENTE_ACTIVO not in validos:
        yield Hallazgo(
            FALLA, f"CLIENTE_ACTIVO={settings.CLIENTE_ACTIVO!r} no esta en CLIENTE_CHOICES",
            f"validos: {', '.join(validos)}. Todos los managers filtrados por cliente "
            "van a devolver vacio.",
        )
    else:
        yield Hallazgo(OK, f"CLIENTE_ACTIVO = {settings.CLIENTE_ACTIVO}")


def chequear_rag_schema(opciones):
    """El bug de los 297 chunks: un scrape corrido con el RAG_SCHEMA de otra
    marca escribe el conocimiento de un cliente en el schema de otro."""
    schema = os.environ.get("RAG_SCHEMA", "")
    if _falta(schema):
        yield Hallazgo(
            AVISO, "RAG_SCHEMA sin definir",
            "bot/rag/cliente.py cae a 'public', que no es el schema de ningun cliente.",
        )
    elif schema != settings.CLIENTE_ACTIVO:
        yield Hallazgo(
            FALLA, f"RAG_SCHEMA={schema!r} no coincide con CLIENTE_ACTIVO={settings.CLIENTE_ACTIVO!r}",
            "El retrieval en vivo lee de RAG_SCHEMA: el bot le va a responder al "
            "cliente con la base de conocimiento de otra marca.",
        )
    else:
        yield Hallazgo(OK, f"RAG_SCHEMA = {schema}")


def chequear_schema_efectivo(opciones):
    """El schema donde el bot escribe DE VERDAD.

    `DB_SCHEMA` en el `.env` es decorativo: `OPTIONS["database_schema"]` no es
    una opción real de mssql-django y se ignora en silencio. El schema efectivo
    lo fija el `DEFAULT_SCHEMA` del login SQL, así que reusar el login de otro
    bot hace que este escriba en la producción del otro -- pasó el 2026-09-02,
    con las ScrapedPage de un bot apuntando a la base de otro.

    Consulta la BD del bot, no un servicio externo: no sale a la red, así que
    corre también con --sin-red (ver CHEQUEOS_CON_RED). Sin equivalente en
    SQLite, así que ahí se saltea con un OK explicativo en vez de fallar.
    """
    from django.db import connection

    if connection.vendor != "microsoft":
        yield Hallazgo(OK, "no es SQL Server, no aplica el chequeo de schema")
        return
    declarado = (getattr(settings, "DB_SCHEMA", "") or "").strip()
    with connection.cursor() as cursor:
        cursor.execute("select DB_NAME(), SCHEMA_NAME(), CURRENT_USER")
        base, schema, usuario = cursor.fetchone()
    if declarado and schema != declarado:
        yield Hallazgo(
            FALLA, f"el schema efectivo es '{schema}' y DB_SCHEMA dice '{declarado}'",
            f"login '{usuario}' en la base '{base}'. El schema lo fija el "
            f"DEFAULT_SCHEMA del login, no el .env: este bot está escribiendo en "
            f"'{schema}', que puede ser la producción de otro bot.",
        )
        return
    yield Hallazgo(OK, f"schema efectivo '{schema}'", f"base '{base}', login '{usuario}'")


def chequear_langfuse(opciones):
    faltan = [n for n in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if _falta(os.environ.get(n))]
    if faltan:
        yield Hallazgo(
            AVISO, "Langfuse sin configurar", f"faltan {', '.join(faltan)} -- el bot funciona, "
            "pero se pierde la unica via de auditar latencia y calidad sobre turnos reales.",
        )
        return
    base = _host_langfuse()
    if not base:
        yield Hallazgo(
            AVISO, "LANGFUSE_BASE_URL sin setear",
            "el SDK cae al default europeo (cloud.langfuse.com), que es otro deployment: "
            "con llaves de otro host las trazas dan 401.",
        )
    else:
        yield Hallazgo(OK, f"Langfuse configurado ({base})")


def _host_langfuse():
    # El SDK acepta cualquiera de las dos; los repos usan LANGFUSE_BASE_URL.
    return os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""


def chequear_credenciales_langfuse(opciones):
    """Prueba el par de llaves contra el host configurado en vez de comparar el
    host con uno fijo: el host cambió una vez (US cloud -> self-hosted) y el
    chequeo viejo quedó avisando en falso. Una llave pertenece a UN proyecto,
    así que se muestra cuál -- si dos bots comparten proyecto, se ve acá."""
    llave_publica = os.environ.get("LANGFUSE_PUBLIC_KEY")
    llave_secreta = os.environ.get("LANGFUSE_SECRET_KEY")
    base = _host_langfuse()
    if _falta(llave_publica) or _falta(llave_secreta) or not base:
        return  # ya lo reporta chequear_langfuse
    try:
        respuesta = httpx.get(
            f"{base.rstrip('/')}/api/public/projects",
            auth=(llave_publica, llave_secreta), timeout=_TIMEOUT_RED,
        )
    except Exception as exc:
        yield Hallazgo(AVISO, f"no se pudo contactar a Langfuse en {base}", repr(exc))
        return
    if respuesta.status_code == 401:
        yield Hallazgo(
            FALLA, f"Langfuse rechaza las llaves en {base} (401)",
            "llaves de otro host o revocadas: el bot funciona, pero cada traza se "
            "pierde en silencio.",
        )
        return
    if respuesta.status_code != 200:
        yield Hallazgo(AVISO, f"Langfuse respondió {respuesta.status_code} en {base}",
                       respuesta.text[:200])
        return
    proyectos = [p.get("name", "?") for p in respuesta.json().get("data", [])]
    yield Hallazgo(OK, "llaves de Langfuse válidas", f"proyecto: {', '.join(proyectos) or '?'}")


# --------------------------------------------------------------------------
# modelos
# --------------------------------------------------------------------------

def chequear_modelos_declarados(opciones):
    vacios = [n for n in SETTINGS_DE_MODELO if _falta(getattr(settings, n, ""))]
    if vacios:
        yield Hallazgo(FALLA, "hay roles de modelo sin declarar", ", ".join(vacios))
    else:
        yield Hallazgo(
            OK, "los 4 roles de modelo estan declarados",
            " · ".join(f"{n.replace('OPENROUTER_', '').lower()}={getattr(settings, n)}"
                       for n in SETTINGS_DE_MODELO),
        )


def chequear_orden_de_proveedores(opciones):
    """`sort: latency` ordena por time-to-first-token, no por throughput:
    elegia un proveedor con 662ms de TTFT y 14 tok/s. Fijar el orden bajo la
    mediana de 17,28s a 9,95s."""
    orden = getattr(settings, "OPENROUTER_PROVIDER_ORDER", [])
    if not orden:
        yield Hallazgo(
            AVISO, "OPENROUTER_PROVIDER_ORDER vacio",
            "sin orden fijo, OpenRouter elige proveedor por su cuenta y la latencia "
            "del turno pasa a depender de a quien le toque.",
        )
    else:
        yield Hallazgo(OK, f"orden de proveedores fijado: {', '.join(orden)}")


def _traer_catalogo_openrouter():
    respuesta = httpx.get(URL_CATALOGO_OPENROUTER, timeout=_TIMEOUT_RED)
    respuesta.raise_for_status()
    return {m["id"]: m for m in respuesta.json()["data"]}


def chequear_catalogo_openrouter(opciones):
    """Contrasta cada modelo configurado contra el catalogo real: que exista,
    que soporte lo que el rol necesita, y que acepte el nivel de razonamiento
    que el codigo le pasa (que es donde estaba el remapeo silencioso)."""
    try:
        catalogo = _traer_catalogo_openrouter()
    except Exception as exc:
        yield Hallazgo(AVISO, "no se pudo leer el catalogo de OpenRouter", repr(exc))
        return

    for nombre in SETTINGS_DE_MODELO:
        modelo_id = getattr(settings, nombre, "")
        if _falta(modelo_id):
            continue
        ficha = catalogo.get(modelo_id)
        if ficha is None:
            yield Hallazgo(
                FALLA, f"{nombre}: el modelo {modelo_id!r} no existe en OpenRouter",
                "toda llamada a ese rol va a fallar con 4xx.",
            )
            continue

        soportados = ficha.get("supported_parameters") or []
        faltantes = [c for c in CAPACIDADES_POR_ROL.get(nombre, []) if c not in soportados]
        if faltantes:
            yield Hallazgo(
                FALLA, f"{nombre} ({modelo_id}) no soporta {', '.join(faltantes)}",
                "es una capacidad que el rol usa en cada turno.",
            )

        razonamiento = ficha.get("reasoning") or {}
        efforts = set(razonamiento.get("supported_efforts") or [])
        obligatorio = bool(razonamiento.get("mandatory"))
        for effort in sorted(ESFUERZOS_QUE_PASA_EL_CODIGO.get(nombre, ())):
            # "none" no es un nivel de la escala: pide APAGAR el razonamiento,
            # y eso lo permite cualquier modelo que no lo tenga obligatorio.
            if effort == "none" and not obligatorio:
                continue
            if effort not in efforts:
                yield Hallazgo(
                    AVISO,
                    f"{nombre} ({modelo_id}) no soporta reasoning effort={effort!r}",
                    f"soportados: {', '.join(sorted(efforts)) or '(ninguno)'}. OpenRouter lo "
                    "remapea al mas cercano SIN avisar, asi que la llamada razona a un "
                    "nivel distinto del que dice el codigo.",
                )
        if not faltantes:
            yield Hallazgo(OK, f"{nombre} = {modelo_id}")


# --------------------------------------------------------------------------
# rag
# --------------------------------------------------------------------------

_RE_DIMENSION = re.compile(r"output_dimensionality\s*=\s*(\d+)")


def _dimension_declarada_en(funcion) -> int | None:
    import inspect
    encontrado = _RE_DIMENSION.search(inspect.getsource(funcion))
    return int(encontrado.group(1)) if encontrado else None


def chequear_dimension_embeddings(opciones):
    """Indexado y retrieval tienen que pedir la MISMA dimension: embeddings de
    distinta dimension no son comparables por coseno, y el schema declara
    vector(1536) porque pgvector no indexa mas de 2000."""
    from bot.rag.indexador import _embeddings_client
    # `_cliente_embeddings` y no `_buscar_en_supabase`: el 2026-09-22 el cliente
    # se saco a una factory cacheada por proceso, y la declaracion de la
    # dimension se fue con el. Este chequeo lee el CODIGO FUENTE de la funcion,
    # asi que un refactor que la mueve lo deja mirando un lugar vacio -- y se
    # degradaba a AVISO, no a FALLA, o sea que el bot habria seguido pasando el
    # doctor con la dimension sin verificar. Lo agarro su propio test.
    from bot.rag.tool import _cliente_embeddings
    indexado = _dimension_declarada_en(_embeddings_client)
    consulta = _dimension_declarada_en(_cliente_embeddings)
    if indexado is None or consulta is None:
        yield Hallazgo(
            AVISO, "no se pudo leer output_dimensionality del codigo",
            f"indexador={indexado}, tool={consulta}",
        )
    elif indexado != consulta:
        yield Hallazgo(
            FALLA, f"la dimension del embedding no coincide: indexado={indexado}, consulta={consulta}",
            "el retrieval devuelve resultados sin sentido o Postgres rechaza la consulta.",
        )
    else:
        yield Hallazgo(OK, f"dimension del embedding coherente ({indexado})")


def _cliente_supabase():
    from bot.rag.cliente import get_supabase_client
    return get_supabase_client()


def chequear_supabase(opciones):
    """Una sola llamada a match_documentos valida cuatro cosas de golpe: el
    schema existe, la funcion existe, los `grant` estan (PGRST106 si no), y la
    dimension del vector coincide con la columna."""
    from bot.rag.indexador import _embeddings_client
    dimension = _dimension_declarada_en(_embeddings_client) or 1536
    try:
        cliente = _cliente_supabase()
    except RuntimeError as exc:
        yield Hallazgo(AVISO, "chequeo de Supabase salteado", str(exc).split(" -- ")[0])
        return
    try:
        cliente.rpc("match_documentos", {
            "query_embedding": [0.0] * dimension,
            "query_texto": "chequeo de salud",
            "match_count": 1,
        }).execute()
    except Exception as exc:
        yield Hallazgo(
            FALLA, f"match_documentos no responde en el schema {os.environ.get('RAG_SCHEMA', 'public')!r}",
            f"{exc!r}. Revisar que el DDL de bot/rag/schema.sql se haya corrido COMPLETO "
            "(incluidos los grant y el enable row level security).",
        )
        return
    yield Hallazgo(OK, "match_documentos responde (schema, funcion, grants y dimension OK)")

    try:
        conteo = cliente.table("documentos_conocimiento").select("id", count="exact").limit(1).execute()
    except Exception as exc:
        yield Hallazgo(AVISO, "no se pudo contar los chunks indexados", repr(exc))
        return
    total = conteo.count or 0
    if total == 0:
        yield Hallazgo(
            FALLA, "la base de conocimiento esta VACIA (0 chunks)",
            "el bot va a decir 'no tengo ese dato' a todo. Correr "
            "`cargar_conocimiento_rag` y despues `reindexar_conocimiento_rag --cliente <cliente>`.",
        )
    else:
        yield Hallazgo(OK, f"{total} chunks indexados")


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

def _registro_de_agentes():
    from bot.flow.agents import build_agent_registry
    return build_agent_registry()


def _universo_de_tools() -> set:
    """Todas las tools que existen en el repo, escaneadas por tipo."""
    from importlib import import_module
    from langchain_core.tools import BaseTool
    nombres = set()
    for ruta in MODULOS_CON_TOOLS:
        try:
            modulo = import_module(ruta)
        except ImportError:
            continue
        for atributo in vars(modulo).values():
            if isinstance(atributo, BaseTool):
                nombres.add(atributo.name)
    return nombres


def _nombra(texto: str, nombre_tool: str) -> bool:
    return re.search(rf"\b{re.escape(nombre_tool)}\b", texto) is not None


def chequear_prompts_activos(opciones):
    from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG
    from bot.models import get_active_prompt
    if not get_active_prompt(GLOBAL_PROMPT_SLUG):
        yield Hallazgo(
            AVISO, "no hay PromptVersion activa para el prompt global",
            "el bot cae al SYSTEM_PROMPT del codigo, asi que funciona -- pero el panel "
            "no puede editarlo hasta que se publique una version.",
        )
    else:
        yield Hallazgo(OK, "prompt global publicado")

    sin_prompt = [slug for slug, agente in _registro_de_agentes().items()
                  if not (agente.effective_prompt() or "").strip()]
    if sin_prompt:
        yield Hallazgo(
            FALLA, "hay especialistas sin prompt", ", ".join(sorted(sin_prompt)) +
            " -- un CustomSpecialist sin PromptVersion activa le manda al LLM un system "
            "prompt vacio.",
        )
    else:
        yield Hallazgo(OK, "todos los especialistas del registro tienen prompt")


def chequear_prompt_contra_fixture(opciones):
    """El prompt activo vive en la BD y se edita desde el panel; el fixture es
    la copia en git. Nada las reconcilia, y esa divergencia ya bloqueo un
    deploy (docs/PENDIENTES.md 32.a) -- en el bot hermano fue el chequeo que
    detecto que los prompts activos en produccion habian perdido las tildes.

    Es el chequeo mas valioso del doctor, asi que su fixture NO puede
    faltar en silencio: si `bot/fixtures/prompt_comercial.md` no esta, eso es
    FALLA, no una comparacion salteada. Ausencia distinta de divergencia: sin
    el archivo no hay nada contra que comparar, y eso es peor que encontrar una
    diferencia.

    `opciones["fixture_comercial"]` permite inyectar una ruta distinta a la
    real del repo -- lo usan los tests para probar los casos "presente" y
    "ausente" sin tocar el archivo trackeado. El CLI real nunca pasa esta
    clave, asi que en produccion siempre se usa la ruta del repo."""
    import difflib
    from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG, SYSTEM_PROMPT
    from bot.models import get_active_prompt

    fixture_comercial = opciones.get("fixture_comercial") or (
        settings.BASE_DIR / "bot" / "fixtures" / "prompt_comercial.md")
    pares = [(GLOBAL_PROMPT_SLUG, SYSTEM_PROMPT, "bot/flow/global_prompt.py::SYSTEM_PROMPT")]
    if fixture_comercial.is_file():
        pares.append(("comercial", fixture_comercial.read_text(encoding="utf-8"),
                      "bot/fixtures/prompt_comercial.md"))
    else:
        yield Hallazgo(
            FALLA, "falta bot/fixtures/prompt_comercial.md",
            "sin el fixture, este chequeo no puede comparar el prompt activo del "
            "especialista comercial contra su version en git, y una edicion desde el "
            "panel que le borre las tildes o le pida una tool que no existe pasa "
            "desapercibida. Lo crea la Task 8 (el especialista comercial) del plan de "
            ".superpowers/sdd/2026-09-09-bot-intouch-comercial/.",
        )

    for agente, en_git, origen in pares:
        activo = get_active_prompt(agente)
        if not activo:
            continue
        if activo.strip() == en_git.strip():
            yield Hallazgo(OK, f"prompt activo de {agente} = {origen}")
            continue
        diferencias = [
            linea for linea in difflib.unified_diff(
                en_git.splitlines(), activo.splitlines(), lineterm="", n=0)
            if linea.startswith(("+", "-")) and not linea.startswith(("+++", "---"))
        ]
        yield Hallazgo(
            AVISO, f"el prompt activo de {agente} difiere de {origen}",
            f"{len(diferencias)} lineas distintas. Primeras: "
            + " | ".join(d[:90] for d in diferencias[:3])
            + ". Si el codigo es lo correcto: `manage.py seed_intouch --republicar-prompt`.",
        )


def chequear_tools_del_prompt(opciones):
    """El chequeo estrella: un prompt que nombra una tool que el especialista
    NO tiene bindeada. Es el bug de `registrar_datos_lead` -- se desbindeo del
    especialista, se corrigio el fixture, y el prompt activo en la BD siguio
    pidiendola. Nada lo detectaba."""
    from bot.flow.respuesta import NOMBRE_TOOL_RESPUESTA
    universo = _universo_de_tools()
    if not universo:
        yield Hallazgo(AVISO, "no se pudo escanear el universo de tools")
        return

    hubo_falla = False
    for slug, agente in sorted(_registro_de_agentes().items()):
        prompt = agente.effective_prompt() or ""
        # `responder` se bindea aparte de business_actions(), en
        # graph.py::_specialist_node_con_tools -- es el canal de salida, no una
        # accion, pero el prompt SI lo nombra.
        bindeadas = {t.name for t in agente.business_actions()} | {NOMBRE_TOOL_RESPUESTA}

        fantasmas = sorted(n for n in universo - bindeadas if _nombra(prompt, n))
        if fantasmas:
            hubo_falla = True
            yield Hallazgo(
                FALLA, f"{slug}: el prompt nombra tools que no tiene bindeadas",
                ", ".join(fantasmas) + " -- el modelo va a intentar usarlas y no van a existir, "
                "o va a prometerle al cliente algo que no puede ejecutar.",
            )

        mudas = sorted(n for n in bindeadas - {NOMBRE_TOOL_RESPUESTA} if not _nombra(prompt, n))
        if mudas:
            yield Hallazgo(
                AVISO, f"{slug}: tools bindeadas que el prompt nunca nombra",
                ", ".join(mudas) + " -- puede estar bien (el docstring de la tool lleva su "
                "contrato), pero conviene revisar que no sea una tool huerfana.",
            )

    if not hubo_falla:
        yield Hallazgo(OK, "ningun prompt nombra una tool sin bindear")


# Vocabulario del vertical retirado. Es una lista corta y explicita a
# proposito: el valor de este chequeo depende de que NO grite en falso, y la
# unica forma honesta de conseguirlo es enumerar palabras que en un bot B2B de
# contact center no pueden aparecer por una razon legitima.
#
# "auto" NO esta como palabra suelta: vive dentro de "automatizacion" y de
# "automotriz", que son legitimas acá (el subtipo automotriz es el rubro de la
# EMPRESA del contacto, no un vehiculo que este bot venda). Todo se busca con
# \b para no disparar por subcadena.
VOCABULARIO_DE_OTRO_VERTICAL = (
    "veh[ií]culos?", "patente", "stock", "garant[ií]a", "repuestos?",
    "sucursal(?:es)?", "financiamiento", "manten[cs]i[oó]n", "taller(?:es)?",
    "dyp", "rent a car", "test drive", "cotizar un", "auto nuevo",
)

# Marcador que reemplaza al prompt escrito por una persona. El texto del
# fixture (y su version publicada en la BD) SI puede nombrar el sector
# automotriz con razon -- el prompt de InTouch lo usa como ejemplo ilustrativo
# y dice explicitamente que no se presente como caso real -- asi que meterlo en
# este chequeo seria criar lobos. Lo que se revisa es lo que agrega el CODIGO.
_MARCADOR_DE_PROMPT = "<<<PROMPT DEL ESPECIALISTA>>>"


def chequear_vocabulario_del_prompt_armado(opciones):
    """La otra mitad del chequeo estrella: lo que el modelo lee de verdad.

    `chequear_tools_del_prompt` escanea `effective_prompt()` y busca NOMBRES de
    tool. Eso deja dos huecos que costaron dos hallazgos Important en la review
    final de rama (I1, I2), los dos en produccion silenciosa:

      - los bloques determinísticos que `build_system_prompt` le pega al prompt
        en cada turno no los ve nadie: `_BLOQUE_PROSA` le ofrecia a un bot B2B
        "buscar en el stock, simular un financiamiento, agendar";
      - el DOCSTRING de una tool bindeada tampoco: `crear_caso` se presentaba
        como ticket de postventa con enum garantia|repuesto|dyp|rent_a_car.

    Este chequeo mira las dos cosas y busca CONTENIDO, no nombres. Para no
    gritar en falso reemplaza el prompt humano por un marcador antes de
    escanear: `build_system_prompt` recibe el prompt efectivo como parametro,
    asi que pasarle el marcador deja exactamente los bloques que agrega el
    codigo. El texto escrito por una persona lo revisa una persona, y el doctor
    ya lo compara contra el fixture de git en otro chequeo.

    Alcance: los especialistas de codigo REGISTRADOS (bot/flow/agents/AGENTS).
    Un CustomSpecialist creado desde el panel es dato, no codigo de este repo,
    y su prompt no se puede recortar con el marcador -- reportarlo aca seria
    reportar lo que un operador escribio en un textarea.

    Lo que este chequeo NO puede ver, dicho para que nadie lo suponga cubierto:
    el NOMBRE de un argumento. `responder` declara `sucursal_direccion_ids` en
    su esquema para los especialistas heredados, y eso solo se arregla
    recortando el esquema por especialista.
    """
    from bot.flow.agents import AGENTS
    from bot.flow.respuesta import NOMBRE_TOOL_RESPUESTA, responder

    def textos_de_la_tool(tool):
        yield tool.description or ""
        for esquema in (tool.args or {}).values():
            if isinstance(esquema, dict) and esquema.get("description"):
                yield esquema["description"]

    hubo_falla = False
    for slug, cls in sorted(AGENTS.items()):
        agente = cls()
        fuentes = [("los bloques del prompt armado",
                    agente.build_system_prompt({}, _MARCADOR_DE_PROMPT))]
        for tool in [*agente.business_actions(), responder]:
            etiqueta = ("el canal de salida `responder`"
                        if tool.name == NOMBRE_TOOL_RESPUESTA
                        else f"el docstring de `{tool.name}`")
            fuentes.extend((etiqueta, texto) for texto in textos_de_la_tool(tool))

        encontradas = {}
        for etiqueta, texto in fuentes:
            for forma in VOCABULARIO_DE_OTRO_VERTICAL:
                hallado = re.search(rf"\b{forma}\b", texto, re.IGNORECASE)
                if hallado:
                    encontradas.setdefault(etiqueta, set()).add(hallado.group(0).lower())
        if encontradas:
            hubo_falla = True
            detalle = "; ".join(f"{etiqueta}: {', '.join(sorted(palabras))}"
                                for etiqueta, palabras in sorted(encontradas.items()))
            yield Hallazgo(
                FALLA, f"{slug}: el modelo lee vocabulario de otro vertical en cada turno",
                detalle + " -- un docstring y un bloque de prompt son corpus: el modelo "
                "imita lo que lee y le promete al contacto capacidades que este bot no "
                "tiene. Adaptalos al negocio de este bot.",
            )

    if not hubo_falla:
        yield Hallazgo(OK, "el prompt armado y los docstrings hablan del negocio de este bot")


def chequear_tools_del_prompt_global(opciones):
    from bot.flow.global_prompt import get_effective_global_prompt
    from bot.flow.respuesta import NOMBRE_TOOL_RESPUESTA
    # `responder` sale del universo de este chequeo por dos razones que se
    # suman: lo tienen TODOS los especialistas (se bindea en
    # graph.py::_specialist_node_con_tools, no en business_actions()), y ademas
    # es un verbo espanol corriente -- el match por palabra completa lo
    # encontraba en "antes de responder con informacion" del prompt global y
    # reportaba un aviso para cada especialista. Falso positivo real, visto en
    # la primera corrida contra produccion.
    universo = _universo_de_tools() - {NOMBRE_TOOL_RESPUESTA}
    global_prompt = get_effective_global_prompt() or ""
    nombradas = {n for n in universo if _nombra(global_prompt, n)}
    if not nombradas:
        yield Hallazgo(OK, "el prompt global no nombra tools puntuales")
        return
    for slug, agente in sorted(_registro_de_agentes().items()):
        bindeadas = {t.name for t in agente.business_actions()}
        faltan = sorted(nombradas - bindeadas)
        if faltan:
            yield Hallazgo(
                AVISO, f"{slug}: el prompt global nombra tools que este especialista no tiene",
                ", ".join(faltan) + " -- si son reglas de guardrail esta bien; si son "
                "instrucciones de uso, este especialista no puede cumplirlas.",
            )


# --------------------------------------------------------------------------
# datos
# --------------------------------------------------------------------------

def chequear_catalogo_intouch(opciones):
    """El catálogo es la única fuente de lo que el bot puede afirmar.

    Es FALLA y no aviso: sin soluciones cargadas, `listar_soluciones` devuelve
    una lista vacía, el bot no puede decir qué hace InTouch y el guardrail
    "no inventes capacidades" lo deja mudo. Un bot que no puede hablar de su
    producto no está listo para producción.

    NO se chequean sucursales ni stock: este bot no los tiene, y un chequeo que
    falla siempre enseña a ignorar la salida completa del doctor.
    """
    from bot.models import ModeloOperacion, SolucionInTouch

    soluciones = list(SolucionInTouch.objects.filter(activa=True))
    if not soluciones:
        yield Hallazgo(
            FALLA, "no hay ninguna solución activa en el catálogo",
            "`manage.py seed_intouch`. Sin catálogo, listar_soluciones no tiene "
            "qué devolver y el bot no puede afirmar nada de lo que InTouch hace.",
        )
    else:
        yield Hallazgo(OK, f"{len(soluciones)} solución(es) activa(s) en el catálogo")

        sin_descripcion = [s.slug for s in soluciones if not s.descripcion.strip()]
        if sin_descripcion:
            yield Hallazgo(
                AVISO, "hay soluciones sin descripción", ", ".join(sin_descripcion)
                + " -- el bot las va a nombrar sin poder explicarlas.",
            )
        sin_canales = [s.slug for s in soluciones
                       if s.categoria == "agentes_ia" and not s.canales]
        if sin_canales:
            yield Hallazgo(
                AVISO, "hay soluciones de agentes sin canales declarados",
                ", ".join(sin_canales),
            )

    modelos = {m.slug for m in ModeloOperacion.objects.all()}
    faltan = {"humano", "hibrido", "automatizado"} - modelos
    if faltan:
        yield Hallazgo(
            FALLA, "faltan modelos de operación",
            ", ".join(sorted(faltan)) + " -- el prompt los presenta como un "
            "conjunto cerrado de tres, y el bot los lee de la tabla.",
        )
    else:
        yield Hallazgo(OK, "los tres modelos de operación están cargados")


def chequear_texto_de_consentimiento(opciones):
    """El teléfono no sale al CRM sin el aviso que tiene que definir legal.

    Con el sink apagado no hay despacho, así que el texto vacío no es una
    falla: el hueco queda preparado y el doctor se pone rojo recién cuando
    el número puede salir.
    """
    texto = getattr(settings, "TEXTO_CONSENTIMIENTO", "") or ""
    sink = getattr(settings, "LEAD_SINK", "none")
    if sink != "none" and not str(texto).strip():
        yield Hallazgo(
            FALLA,
            "el teléfono se despacha sin texto de consentimiento",
            "LEAD_SINK no es none y TEXTO_CONSENTIMIENTO está vacío. "
            "El teléfono sale al CRM sin el aviso que tiene que definir legal.",
        )
        return
    yield Hallazgo(
        OK, "el despacho del teléfono no queda sin texto de consentimiento",
    )


# --------------------------------------------------------------------------
# whatsapp
# --------------------------------------------------------------------------

def chequear_whatsapp(opciones):
    """Un token vencido no se nota hasta que un cliente escribe: el webhook
    entra, el grafo corre, y el envio falla al final."""
    if _falta(settings.WHATSAPP_TOKEN) or _falta(settings.WHATSAPP_PHONE_ID):
        yield Hallazgo(AVISO, "chequeo de Meta salteado", "falta token o phone id")
        return
    url = f"https://graph.facebook.com/v20.0/{settings.WHATSAPP_PHONE_ID}"
    try:
        respuesta = httpx.get(
            url, params={"fields": "display_phone_number,verified_name"},
            headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}, timeout=_TIMEOUT_RED,
        )
    except Exception as exc:
        yield Hallazgo(AVISO, "no se pudo contactar a la API de Meta", repr(exc))
        return
    if respuesta.status_code != 200:
        yield Hallazgo(
            FALLA, f"Meta rechazo la credencial (HTTP {respuesta.status_code})",
            respuesta.text[:300],
        )
        return
    datos = respuesta.json()
    yield Hallazgo(
        OK, "credencial de WhatsApp valida",
        f"{datos.get('verified_name', '?')} · {datos.get('display_phone_number', '?')}",
    )


SECCIONES = {
    "config": [chequear_variables_obligatorias, chequear_cliente_activo,
               chequear_rag_schema, chequear_schema_efectivo, chequear_langfuse,
               chequear_credenciales_langfuse, chequear_token_interno],
    "modelos": [chequear_modelos_declarados, chequear_orden_de_proveedores,
                chequear_catalogo_openrouter],
    "rag": [chequear_dimension_embeddings, chequear_supabase],
    "prompts": [chequear_prompts_activos, chequear_prompt_contra_fixture,
                chequear_tools_del_prompt, chequear_tools_del_prompt_global,
                chequear_vocabulario_del_prompt_armado],
    "datos": [chequear_catalogo_intouch, chequear_texto_de_consentimiento],
    "whatsapp": [chequear_whatsapp],
}

# Chequeos que salen a la red. Con --sin-red se saltean, para poder correr el
# comando en un entorno sin credenciales ni salida a internet.
CHEQUEOS_CON_RED = {chequear_catalogo_openrouter, chequear_credenciales_langfuse,
                    chequear_supabase, chequear_whatsapp}


class Command(BaseCommand):
    help = (
        "Verifica la salud de este bot contra el sistema real: configuracion, modelos, "
        "RAG, prompts, datos de negocio y credenciales de WhatsApp. Solo lectura. "
        "Sale con codigo != 0 si hay alguna falla."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--seccion", action="append", choices=sorted(SECCIONES),
            help="Correr solo esta seccion (se puede repetir). Default: todas.",
        )
        parser.add_argument(
            "--sin-red", action="store_true",
            help="Saltear los chequeos que salen a internet (OpenRouter, Supabase, Meta).",
        )

    def handle(self, *args, **opciones):
        secciones = opciones.get("seccion") or list(SECCIONES)
        conteo = {OK: 0, AVISO: 0, FALLA: 0}

        for nombre in secciones:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{nombre}"))
            for chequeo in SECCIONES[nombre]:
                if opciones["sin_red"] and chequeo in CHEQUEOS_CON_RED:
                    self.stdout.write(f"  · {chequeo.__name__}: salteado (--sin-red)")
                    continue
                for hallazgo in self._correr(chequeo, opciones):
                    conteo[hallazgo.nivel] += 1
                    self._escribir(hallazgo)

        resumen = f"\n{conteo[OK]} ok · {conteo[AVISO]} aviso(s) · {conteo[FALLA]} falla(s)"
        if conteo[FALLA]:
            self.stdout.write(self.style.ERROR(resumen))
            raise CommandError(f"{conteo[FALLA]} chequeo(s) en falla")
        self.stdout.write(self.style.SUCCESS(resumen))

    def _correr(self, chequeo, opciones):
        """Un chequeo que revienta no puede tumbar al resto: el valor del
        comando es correrlos TODOS y mostrar el panorama completo."""
        try:
            return list(chequeo(opciones))
        except Exception as exc:
            return [Hallazgo(FALLA, f"el chequeo {chequeo.__name__} fallo", repr(exc))]

    def _escribir(self, hallazgo: Hallazgo):
        estilo = {OK: self.style.SUCCESS, AVISO: self.style.WARNING, FALLA: self.style.ERROR}
        self.stdout.write(estilo[hallazgo.nivel](
            f"  {_SIMBOLO[hallazgo.nivel]} {hallazgo.titulo}"))
        if hallazgo.detalle:
            self.stdout.write(f"      {hallazgo.detalle}")
