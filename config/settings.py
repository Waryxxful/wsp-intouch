# config/settings.py
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "CHANGEME-dev-only")
DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
CLIENTE_ACTIVO = os.environ.get("CLIENTE_ACTIVO", "intouch")
ALLOWED_HOSTS = ["*"]

# Flip de cliente activo desde el panel admin (ver admin_panel/cliente_flip.py):
# el contenedor edita .env.docker y deja un pedido de restart para que un
# script del HOST (fuera de Docker, via cron) lo ejecute -- el contenedor no
# tiene ni necesita acceso al socket de Docker. Ambos paths son overridables
# via settings para poder testear contra archivos temporales.
ENV_DOCKER_PATH = BASE_DIR / ".env.docker"
FLIP_REQUEST_PATH = BASE_DIR / ".flip_request.json"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bot.apps.BotConfig",
    "bot.simulator.apps.SimulatorConfig",
    "bot.rag_eval.apps.RagEvalConfig",
    "grancrm_auth.apps.GrancrmAuthConfig",
    "admin_panel.apps.AdminPanelConfig",
    "leads.apps.LeadsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Decodifica el cookie grancrm_session -> request.jwt_payload. Es barato (solo
    # jwt.decode) e inofensivo sin cookie (deja jwt_payload=None), y se necesita en
    # dev/tests tambien, por eso vive en la base y no en un settings docker-only.
    "grancrm_auth.middleware.GranCRMAuthMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"

TIME_ZONE = "America/Santiago"

# Schema de SQL Server donde este bot escribe. NO se puede configurar desde
# Django: `OPTIONS["database_schema"]` parece hacerlo pero **no es una opcion de
# mssql-django** (`grep -rn database_schema` sobre el paquete instalado, 1.8.0,
# no devuelve nada) y se ignoraba en silencio. El schema efectivo lo fija el
# DEFAULT_SCHEMA del login SQL con el que se conecta.
#
# Incidente real del 2026-09-02 (docs/PENDIENTES.md #12): el .env.docker de
# Cavem reusaba el login de wsp_demo (DEFAULT_SCHEMA=botdemo), asi que con
# DB_SCHEMA=cavem seteado el bot escribia en el schema de produccion de
# Renault/Astara, y el `migrate` del CMD del Dockerfile alcanzo a aplicar 5
# migraciones ahi antes de que alguien lo notara.
#
# Este valor ya no configura nada: es lo que el deploy DECLARA que espera, y
# bot/apps.py::check_db_schema_efectivo lo compara contra el SCHEMA_NAME() real
# y se niega a arrancar si no coinciden.
DB_SCHEMA = os.environ.get("DB_SCHEMA", "")

# Conexiones persistentes a SQL Server. Sin esto Django abre y CIERRA una
# conexion NUEVA en cada request, y este bot paga ese costo en cada mensaje de
# WhatsApp.
#
# Medido el 2026-09-02 sobre el contenedor real, abriendo la conexion en
# procesos frescos: 0,26s / 0,51s / 0,57s en el caso normal, pero
# INTERMITENTEMENTE 6,04s / 24,17s / 32,80s / 35,27s / 35,93s. Con la conexion
# ya abierta, las queries tardan 0,00s -- todo el tiempo se va en el handshake,
# no en consultar.
#
# Ese es el motivo de un turno real de 85s cuya traza de Langfuse mostraba solo
# 6,71s de LLM: los otros 78s fueron conectar. Incluso el saludo instantaneo,
# que no toca el LLM, tardo 23s por lo mismo.
#
# CONN_HEALTH_CHECKS valida la conexion reusada al inicio de cada request y
# reconecta si el servidor la cerro -- es lo que hace seguro reusarla: sin el,
# una conexion muerta se descubre recien al fallar una query real.
#
# Esto MITIGA, no arregla: el SQL Server de QA tardando hasta 36s en aceptar
# una conexion es un problema de infraestructura para el DBA. Si un worker
# arranca justo en un momento malo, su primer request sigue pagando la espera.
CONN_MAX_AGE = int(os.environ.get("DB_CONN_MAX_AGE", "600"))
CONN_HEALTH_CHECKS = True

if os.environ.get("USE_SQLITE", "true").lower() == "true":
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"},
        "qaintouch": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db_leads.sqlite3"},
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "mssql",
            "NAME": os.environ.get("DB_NAME", "CHANGEME"),
            "HOST": os.environ.get("DB_HOST", "CHANGEME"),
            "PORT": os.environ.get("DB_PORT", "1433"),
            "USER": os.environ.get("DB_USER", "CHANGEME"),
            "PASSWORD": os.environ.get("DB_PASSWORD", "CHANGEME"),
            "OPTIONS": {
                "driver": os.environ.get("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
                "extra_params": "TrustServerCertificate=yes;",
                # Aca iba "database_schema": mssql-django no la soporta, ver
                # el comentario de DB_SCHEMA mas arriba.
            },
            "CONN_MAX_AGE": CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": CONN_HEALTH_CHECKS,
        },
        "qaintouch": {
            "ENGINE": "mssql",
            "NAME": os.environ.get("QAINTOUCH_DB_NAME", "QAIntouch"),
            "HOST": os.environ.get("DB_HOST", "CHANGEME"),
            "PORT": os.environ.get("DB_PORT", "1433"),
            "USER": os.environ.get("DB_USER", "CHANGEME"),
            "PASSWORD": os.environ.get("DB_PASSWORD", "CHANGEME"),
            "OPTIONS": {
                "driver": os.environ.get("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
                "extra_params": "TrustServerCertificate=yes;",
                # Aca iba "database_schema": "botdemo", que ademas de ignorarse
                # era el schema de OTRO bot hardcodeado en este repo.
            },
            "CONN_MAX_AGE": CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": CONN_HEALTH_CHECKS,
        },
    }

DATABASE_ROUTERS = ["leads.db_router.LeadsRouter"]

GRANCRM_JWT_SECRET = os.environ.get("GRANCRM_JWT_SECRET", "CHANGEME")

LOGIN_URL = "/django-admin/login/"

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "CHANGEME")
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v22.0")
WHATSAPP_API_BASE = "https://graph.facebook.com"

# WhatsApp Business Account ID (WABA_ID). Necesario para consumir la Graph API
# de estadisticas (analytics, pricing_analytics, template_analytics). Se ve en
# Meta Developer Console -> WhatsApp -> Configuracion. NO es lo mismo que
# WHATSAPP_PHONE_ID (que identifica un numero, no la cuenta business).
WHATSAPP_BUSINESS_ACCOUNT_ID = os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
ENCUESTA_SERVICIO_TECNICO_TEMPLATE = os.environ.get("ENCUESTA_SERVICIO_TECNICO_TEMPLATE", "")
ENCUESTA_VENTA_AUTO_NUEVO_TEMPLATE = os.environ.get("ENCUESTA_VENTA_AUTO_NUEVO_TEMPLATE", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
# GOOGLE_API_KEY sigue vigente SOLO para los embeddings (GoogleGenerativeAIEmbeddings
# en bot/rag/indexador.py, bot/rag/tool.py -- leido del entorno directo por el SDK,
# no via este setting) -- OpenRouter no ofrece endpoint de embeddings. El LLM
# conversacional y el de percepcion de medios migraron a OpenRouter el 2026-08-19
# (incidente real: Gemini directo devolviendo 503/504 "high demand", ver el
# comentario de _MEDIA_LLM_DEADLINE_SEGUNDOS en bot/flow/media_processing.py) --
# ver OPENROUTER_* mas abajo. GEMINI_MODEL/GEMINI_MEDIA_MODEL se retiraron por
# quedar sin ningun lector tras esa migracion.
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
# Modelo del cliente simulado del simulador de pruebas (bot/simulator/) --
# deliberadamente NO el mismo modelo del bot (OPENROUTER_MODEL): este rol es el
# de mayor volumen de llamadas de todo el simulador (una por turno) y no exige
# razonamiento fuerte, asi que conviene el mas barato disponible. Ver
# docs/superpowers/specs/2026-08-12-simulador-conversaciones-prueba-wsp-demo-design.md,
# seccion 6.1.
SIMULATED_USER_MODEL = os.environ.get("SIMULATED_USER_MODEL", "google_genai:gemini-3.5-flash-lite")
# Modelo del juez del simulador de pruebas (bot/simulator/) --
# deliberadamente el mejor Flash disponible (nunca Pro): bajo volumen de
# llamadas (una por escenario, no por turno) pero alto impacto si se
# equivoca, asi que aca conviene pagar mas que en SIMULATED_USER_MODEL. Ver
# spec, seccion 6.1.
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "google_genai:gemini-3.6-flash")
# Minutos sin actividad (CorridaDePrueba.actualizado_en) antes de que una
# corrida en estado "corriendo" se reclasifique como "interrumpida" -- ver
# bot/simulator/models.py::marcar_corridas_stale_como_interrumpidas. Valor
# inicial generoso frente a los ~8 escenarios x unos pocos minutos cada uno
# observados en la verificacion en vivo del simulador (spec seccion 3).
SIMULATOR_STALENESS_MINUTES = int(os.environ.get("SIMULATOR_STALENESS_MINUTES", "20"))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# ~deepseek/deepseek-v4-flash-latest: modelo elegido por el usuario para el LLM
# conversacional -- soporta tools/tool_choice (verificado contra la API real de
# OpenRouter antes de elegirlo, el bot depende de tool-calling para
# consultar_base_conocimiento/crear_caso/etc).
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "~deepseek/deepseek-v4-flash-latest")
# google/gemini-3.7-flash: mismo modelo que ya se usaba directo via Google para
# "percepcion" de medios (describir imagenes, transcribir audio) antes de esta
# migracion, ahora enrutado por OpenRouter -- soporta imagen/audio como input
# (verificado contra la API real de OpenRouter). Ver
# docs/superpowers/specs/2026-08-18-imagen-audio-whatsapp-design.md para el
# contexto original de por que este modelo esta separado del conversacional.
OPENROUTER_MEDIA_MODEL = os.environ.get("OPENROUTER_MEDIA_MODEL", "google/gemini-3.7-flash")

# Modelo del RUTEO (bot/flow/graph.py::supervisor_node, span classify-intent).
# Separado del conversacional desde el 2026-09-02 por una auditoria de latencia
# con mediciones sobre el contenedor real:
#
#   ~deepseek/deepseek-v4-flash-latest  mediana 2,78s  MAXIMO 62,40s
#   ibm-granite/granite-4.2-8b          mediana 0,71s  maximo  1,07s
#
# Los dos rutearon 5/5 casos correctos (saludo, busqueda de stock, pregunta de
# garantia, agendamiento de taller, simulacion de cuota). El ruteo es elegir
# entre ~5 slugs conocidos: no necesita un MoE de 284B, y la cola de latencia
# de ese modelo (62s con reasoning YA desactivado -- es del proveedor, no del
# razonamiento) era el peor componente de un turno.
#
# granite soporta `reasoning: none` de verdad (no lo remapea hacia arriba como
# el modelo conversacional hace con "medium"), y admite structured_outputs por
# si mas adelante se quiere forzar el JSON del ruteo por schema.
OPENROUTER_ROUTING_MODEL = os.environ.get(
    "OPENROUTER_ROUTING_MODEL", "ibm-granite/granite-4.2-8b")
# Techo de salida del ruteo: la respuesta es un JSON de dos campos. Sin techo,
# el `effort` de razonamiento se calcula como un porcentaje de max_tokens y el
# presupuesto queda practicamente ilimitado (ver la auditoria en
# docs/PENDIENTES.md).
OPENROUTER_ROUTING_MAX_TOKENS = int(os.environ.get("OPENROUTER_ROUTING_MAX_TOKENS", "200"))

# Extraccion/reestructuracion de contenido scrapeado y de documentos para el
# RAG (bot/scraping/extractor.py). Hasta el 2026-09-02 este componente era el
# unico que seguia llamando a la API directa de DeepSeek: quedo fuera de la
# migracion del 2026-08-19 por ser "un proveedor aparte, resuelto
# independientemente". Esa cuenta se quedo sin saldo y devolvio 402 en el
# primer indexado del RAG de Cavem -- con dos proveedores hay dos saldos que
# vigilar, y el que no se usa a diario es justo el que se descubre vacio
# cuando se lo necesita. Mismo modelo que antes, ahora enrutado por OpenRouter
# (una sola API key para bot, media y scraping).
OPENROUTER_SCRAPING_MODEL = os.environ.get(
    "OPENROUTER_SCRAPING_MODEL", "~deepseek/deepseek-v4-flash-latest")

# Proveedores de OpenRouter para el modelo conversacional/extraccion, en orden
# de preferencia. Antes se pedia `provider: {"sort": "latency"}`, que ordena por
# TIME-TO-FIRST-TOKEN, y esa es la metrica equivocada para este bot.
#
# Auditoria del 2026-09-02 (docs/PENDIENTES.md #14): `sort: latency` elegia
# OpenInference, el mejor del catalogo en TTFT (662ms p50) y **el peor en
# throughput: 14 tokens/s**. Un turno genera 500-850 tokens entre razonamiento
# y salida, asi que:
#
#     850 tokens / 14 tok/s  = 60 s   <- el classify-intent de 62s medido
#     850 tokens / 115 tok/s =  7 s   <- el mismo trabajo en Baidu
#
# Medido con los proveedores fijados (3 repeticiones por turno): la busqueda
# bajo de 17,28s a 9,95s de mediana, y el PEOR CASO de 35,50s a 13,81s -- esa
# cola era la que hacia impredecible cada turno.
#
# Los tres elegidos son buenos en las DOS metricas y **fp8 o mejor**: los fp4
# del catalogo (Relace, Sail Research, Ambient, Inceptron, Reka) se descartaron
# a proposito para no arriesgar la calidad de redaccion por la puerta de atras
# -- es la misma razon por la que se descarto bajar el `effort` de razonamiento.
#
#   Baidu       TTFT 818ms  throughput 115 tok/s  fp8       uptime 24h 99,89%  $0,065/$0,13
#   CoreWeave   TTFT 608ms  throughput 102 tok/s  fp8       uptime 24h 99,96%  $0,13/$0,28
#   DeepSeek    TTFT 1078ms throughput  98 tok/s  completa  uptime 24h 99,99%  $0,22/$0,66
#
# Sin `sort` junto al `order` a proposito: ordenar por latencia es lo que causo
# el problema, y ordenar solo por throughput elige proveedores con TTFT malisimo
# (Wafer: 4s antes del primer token). Con `allow_fallbacks` en True, si los tres
# se caen a la vez OpenRouter usa su ruteo por defecto en vez de fallar.
OPENROUTER_PROVIDER_ORDER = [
    p.strip() for p in os.environ.get(
        "OPENROUTER_PROVIDER_ORDER", "Baidu,CoreWeave,DeepSeek").split(",") if p.strip()
]

MEDIA_URL = "/demo/media/"
MEDIA_ROOT = BASE_DIR / "media"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

# Fotos/audios que mandan los clientes por WhatsApp -- directorio separado de
# MEDIA_ROOT a proposito: MEDIA_ROOT se sirve completo y sin auth via
# config/urls.py::serve_media (pensado para fotos de catalogo, publicas). Un
# adjunto de un cliente es dato personal, se sirve solo con @login_required
# via admin_panel.views.api_media_file, nunca por esa ruta publica.
WHATSAPP_MEDIA_ROOT = BASE_DIR / "media_private" / "whatsapp"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
