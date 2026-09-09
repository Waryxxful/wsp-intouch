# Pendientes de credenciales — Task 2 (dejado para 2026-09-09/10 con el usuario despierto)

Todo lo de acá quedó preparado hasta donde se pudo sin acceso a las
credenciales reales. Está en el orden en que conviene resolverlo: el paso 1
bloquea la Task 3 (no seguir sin resolverlo), los pasos 2 y 3 bloquean tareas
más adelante en el plan, el paso 4 es el que menos apura.

> **Aclaración sobre `doctor` y el `docker run` efímero (verificada):** al
> correr `manage.py doctor` con el `docker run` de una sola vez que se usa
> para los tests (el de `CLAUDE.md`, con `-e` explícitas), reporta como
> "ausentes" `SUPABASE_URL`, `SUPABASE_KEY`, `GOOGLE_API_KEY` y
> `PUBLIC_BASE_URL`. **Las cuatro SÍ están en `.env.docker` con valores
> reales** (ver "Ya resuelto en esta sesión" más abajo). La falla es un
> artefacto de ese `docker run`: pasa variables explícitas con `-e` y no carga
> el `env_file` del `docker-compose.yml`, así que ninguna variable que sólo
> viva en `.env.docker` llega al contenedor efímero. Corrido desde el
> contenedor real (`docker compose exec web python manage.py doctor`, que sí
> carga `env_file`), el `doctor` pasa de cinco fallas a **una**:
> `WHATSAPP_VERIFY_TOKEN=CHANGEME`, que es la credencial de mañana (punto 3).
> Las otras dos fallas que quedarían — el fixture del prompt y el catálogo de
> negocio vacío — las cierran el especialista comercial (ya implementado) y
> `seed_intouch` (punto 5 de `docs/DEPLOY_INTOUCH.md`), no una credencial.
> Sin esta aclaración, mañana se lee "5 fallas" desde el `docker run` de los
> tests y parece que el bot está roto cuando no lo está.

## 1. Login SQL Server `intouch_login_qa` (BLOQUEANTE para la Task 3)

**Por qué es bloqueante:** `DB_SCHEMA` en `.env.docker` es decorativo —
`OPTIONS["database_schema"]` no es una opción real de `mssql-django` (no
aparece en el paquete instalado si se hace `grep -rn database_schema` sobre
él) y Django la ignora en silencio. El schema efectivo donde caen las tablas
lo fija el `DEFAULT_SCHEMA` del login SQL con el que se conecta, no la
variable de entorno. Si se reusa el login de otro bot (por ejemplo
`cavem_login_qa`, `DEFAULT_SCHEMA=cavem`), el `migrate`/`seed`/carga de datos
de este bot escribe calladamente en el schema — y por lo tanto en la
producción — de ese otro bot. Ya pasó una vez en este mismo stack (schema
`botdemo` con el login de wsp_demo reusado, ver `wsp_cavem/docs/PENDIENTES.md`
#12).

**Qué pedir/crear** (contra el SQL Server de `172.20.21.50`, BD `QAIntouch`,
con un usuario `sysadmin` tipo `sa`):

```sql
USE [QAIntouch];
GO

CREATE LOGIN intouch_login_qa
    WITH PASSWORD = '<ELEGIR-UNA-CONTRASEÑA-SEGURA>',
         CHECK_POLICY = ON,
         CHECK_EXPIRATION = OFF;
GO

CREATE USER intouch_login_qa FOR LOGIN intouch_login_qa;
GO

CREATE SCHEMA intouch AUTHORIZATION intouch_login_qa;
GO

ALTER USER intouch_login_qa WITH DEFAULT_SCHEMA = intouch;
GO

GRANT CONTROL ON SCHEMA::intouch TO intouch_login_qa;
GO
```

Este es el mismo patrón que se usó para `cavem_login_qa` (`DEFAULT_SCHEMA=cavem`,
`CONTROL` sólo sobre ese schema) según queda descrito en
`wsp_cavem/docs/PENDIENTES.md` #12 — no existe en ningún repo el script literal
que se corrió esa vez, así que esto es una reconstrucción del mismo patrón, no
una copia; revisarlo antes de correrlo.

**Después de crear el login:**

1. Completar en `.env.docker`:
   ```
   DB_USER=intouch_login_qa
   DB_PASSWORD=<la contraseña elegida arriba>
   ```
2. Verificar el schema efectivo ANTES de correr cualquier `migrate`/seed/carga
   de datos:
   ```bash
   docker compose up -d --build
   docker compose exec web python manage.py dbshell
   ```
   ```sql
   select DB_NAME(), SCHEMA_NAME(), CURRENT_USER;
   ```
   Tiene que devolver `('QAIntouch', 'intouch', 'intouch_login_qa')`. Si dice
   otra cosa (por ejemplo el schema de otro bot), **no seguir**: parar y
   corregir el login antes de tocar la Task 3.

## 2. Schema del RAG en Supabase (bloqueante para `doctor --seccion rag` y la carga de conocimiento, Task 12+)

El DDL ya está generado y revisado en `docs/rag_schema_intouch.sql` (emitido
con `scripts/rag_schema_para.sh intouch`, que valida contra `CLIENTE_CHOICES`
en vivo — nunca reemplazar `__CLIENTE__` a mano, ver el comentario del
script). Contiene las tres cosas que se suelen olvidar: los `grant` a
`service_role`/`anon`/`authenticated`, `enable row level security` (sin
policies, así que sólo `service_role` lee), y la función `match_documentos`.

Pasos, en orden:

1. Abrir el SQL editor de Supabase, proyecto `intouch` (el mismo de Cavem: el
   aislamiento es por schema, no por proyecto).
2. Pegar el contenido completo de `docs/rag_schema_intouch.sql` y ejecutarlo.
3. Verificar con estas tres consultas (todas en el mismo SQL editor):
   ```sql
   -- los grants
   select grantee, privilege_type from information_schema.role_table_grants
    where table_schema = 'intouch' and table_name = 'documentos_conocimiento';

   -- RLS activa
   select relrowsecurity from pg_class where oid = 'intouch.documentos_conocimiento'::regclass;

   -- sin policies (tiene que dar 0)
   select count(*) from pg_policies where schemaname = 'intouch';
   ```
4. **Paso que no es SQL y se olvida siempre:** Supabase → Settings → API →
   Exposed schemas → agregar `intouch`. Sin esto, todo responde `PGRST106`
   aunque la tabla exista.
5. Recién ahí correr:
   ```bash
   docker compose exec web python manage.py doctor --seccion rag
   ```
   Va a decir OK con 0 chunks (se cargan en la Task 14) — 0 chunks es
   esperado, lo que no puede pasar es un error de conexión o `PGRST106`.

## 3. Credenciales de Meta / WhatsApp

Completar en `.env.docker` (quedaron en `CHANGEME`):

```
WHATSAPP_TOKEN=
WHATSAPP_PHONE_ID=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_BUSINESS_ACCOUNT_ID=
```

Después:

1. Configurar el webhook de Meta apuntando a
   `https://qadash.in-touchcrm.cl/wsp/intouch/webhook` con el
   `WHATSAPP_VERIFY_TOKEN` de arriba.
2. Este bot es B2B y no manda seguimiento de vehículo ni usa plantillas de
   Cavem — cuando exista el catálogo de plantillas propio de InTouch (fuera
   del alcance de esta Task), darlas de alta en idioma `es`, **no** `es_CL`
   (con `es_CL` Meta responde 132001 — bug real ya visto con Cavem).

## 4. `GRANCRM_TENANT_SLUG` (para notificar leads HOT, Task 14+)

Completar en `.env.docker`:
```
GRANCRM_TENANT_SLUG=<slug de la cuenta de GranCRM que recibe los leads de InTouch>
```
Sin esto, `bot/notify.py` loguea un aviso y no notifica — mismo comportamiento
que `wsp_pompeyo` (no es un error duro, pero los leads HOT no llegan a nadie
hasta completarlo). Buscar el slug correcto en el modelo `Cuenta`/
`CuentaAplicacion` del orquestador, no adivinarlo por el nombre del cliente.

---

## Ya resuelto en esta sesión (no hace falta credenciales)

- `.env.docker` creado con todo lo reusable de `wsp_cavem/.env.docker` ya
  completado: `SUPABASE_URL`, `SUPABASE_KEY`, `OPENROUTER_API_KEY`, los cuatro
  modelos por rol, `OPENROUTER_PROVIDER_ORDER`, `OPENROUTER_ROUTING_MAX_TOKENS`,
  `GOOGLE_API_KEY`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_DRIVER`,
  `DB_CONN_MAX_AGE`, `QAINTOUCH_DB_NAME`, `GRANCRM_JWT_SECRET`, `DIOS_URL`,
  `DIOS_CONFIG_PATH` y las tres `LANGFUSE_*` (región **US**,
  `https://us.cloud.langfuse.com` — el default EU de los ejemplos genéricos
  da 401).
- `dios.json` creado con el `secret` real (mismo secreto de registro del
  orquestador que usa Cavem — es un secreto de entorno, no uno por app).
- `scripts/rag_schema_para.sh` tenía un bug real (no relacionado a
  credenciales): leía `CLIENTE_CHOICES` con un grep de una sola línea, y
  `bot/models.py` lo tiene partido en varias líneas desde la Task 1. Se arregló
  para tolerar ambos formatos — sin el fix, el script fallaba con "no se pudo
  leer CLIENTE_CHOICES" para cualquier cliente, no sólo `intouch`.
