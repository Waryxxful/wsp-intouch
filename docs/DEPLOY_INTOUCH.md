# Despliegue de wsp_intouch

Runbook de esta instancia. Documentación pura: cada paso trae su comando
exacto y qué se rompe si se saltea. El razonamiento de diseño está en
`docs/superpowers/specs/2026-09-09-bot-intouch-comercial-design.md` (§10 y
§13); acá va el qué y el cómo, en el orden en que se corre.

> **Los tres datos que se buscan apurado:**
> - **Puerto:** `8040` (backend, `8040:8000`); `8041` el dev server de Vite.
> - **Schema:** `intouch` (SQL Server *y* Supabase/Postgres).
> - **Prefijo público:** `/wsp/intouch/` (shell), `/intouch/api/` (API),
>   `/intouch/webhook` (Meta, sólo si el número tiene App propia).

Antes de tocar nada: `git -C /home/admincrm/wsp_intouch status --short` y
`ListAgents`/`SendMessage` — el contenedor **es** producción y puede haber
otras sesiones sobre el mismo working tree.

---

## 1. Supabase

El schema `intouch` vive en el mismo proyecto Supabase que usa Cavem (el
aislamiento del diseño es por schema, no por proyecto). Su DDL ya está
generado y revisado en `docs/rag_schema_intouch.sql`, **sin ejecutar**.

- [ ] Abrir el SQL editor del proyecto Supabase (el mismo de Cavem).
- [ ] Pegar el contenido completo de `docs/rag_schema_intouch.sql` y
      ejecutarlo.
- [ ] Verificar los grants a `service_role`:
  ```sql
  select grantee, privilege_type from information_schema.role_table_grants
   where table_schema = 'intouch' and table_name = 'documentos_conocimiento';
  ```
- [ ] Verificar que RLS está activa, sin policies (sólo `service_role` lee):
  ```sql
  select relrowsecurity from pg_class where oid = 'intouch.documentos_conocimiento'::regclass;
  select count(*) from pg_policies where schemaname = 'intouch';  -- tiene que dar 0
  ```
- [ ] **El paso que no es SQL y se olvida siempre:** Supabase → *Settings →
      API → Exposed schemas* → agregar `intouch`. Sin esto todo responde
      `PGRST106` aunque la tabla exista.
- [ ] Confirmar que `match_documentos` responde (una llamada de prueba desde
      el SQL editor, o dejarlo para el `doctor --seccion rag` del paso 4).

No reemplazar `__CLIENTE__` a mano si en algún momento hay que regenerar el
DDL: usar `scripts/rag_schema_para.sh intouch` (ya corrido). El reemplazo
manual es el error que mandó 297 chunks de Astara al schema de Renault.

---

## 2. Configuración

`.env.docker` ya existe (creado a partir de `.env.docker.example` reusando
lo de `wsp_cavem`). Falta completar, todos hoy en `CHANGEME`:

```
WHATSAPP_TOKEN=
WHATSAPP_PHONE_ID=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_BUSINESS_ACCOUNT_ID=
DB_USER=
DB_PASSWORD=
GRANCRM_TENANT_SLUG=
```

`DB_USER`/`DB_PASSWORD` son las del login `intouch_login_qa` (sección 3).
`GRANCRM_TENANT_SLUG` es el slug de la cuenta de GranCRM que recibe los leads
HOT — buscarlo en el modelo `Cuenta`/`CuentaAplicacion` del orquestador, no
adivinarlo por el nombre del cliente; sin él, `bot/notify.py` sólo loguea un
aviso y ningún lead HOT llega a nadie.

Ya está puesto, reusado sin cambios de `wsp_cavem/.env.docker`:
`SUPABASE_URL`, `SUPABASE_KEY`, `OPENROUTER_API_KEY` y los cuatro modelos por
rol, `OPENROUTER_PROVIDER_ORDER=Baidu,CoreWeave,DeepSeek`, `GOOGLE_API_KEY`
(embeddings), `DB_HOST`, `DB_PORT`, `DB_NAME=QAIntouch`, `DB_DRIVER`,
`DB_CONN_MAX_AGE=600`, `GRANCRM_JWT_SECRET`, `DIOS_URL`, `DIOS_CONFIG_PATH`,
las tres `LANGFUSE_*` (proyecto propio, región **US**:
`https://us.cloud.langfuse.com` — el default EU de los ejemplos genéricos da
401), `PUBLIC_BASE_URL=https://qadash.in-touchcrm.cl/wsp/intouch` y
`LEAD_SINK=none`.

`CLIENTE_ACTIVO` y `RAG_SCHEMA` valen los dos `intouch`, y **tienen que
coincidir**: hay un system check (`bot.E001`/`bot.E002`) que revienta el
arranque si difieren, para evitar que el bot responda con datos de un cliente
y busque conocimiento de otro.

`dios.json` ya existe (gitignoreado, con el secreto real). `source_db`,
`source_host` y `schemas` ahí dentro tienen que coincidir con `DB_NAME`,
`DB_HOST` y `DB_SCHEMA` del `.env.docker` — el registro es fire-and-forget y
se traga todas las excepciones (ver sección 8), así que un valor equivocado
no da ningún error visible.

---

## 3. Base de datos

Login SQL Server `intouch_login_qa`, `DEFAULT_SCHEMA=intouch`, en la BD
`QAIntouch` — su creación queda detallada en `docs/PENDIENTE_CREDENCIALES.md`
punto 1.

**Por qué es bloqueante:** `DB_SCHEMA` en `.env.docker` es decorativo —
`OPTIONS["database_schema"]` no es una opción real de `mssql-django` y Django
la ignora en silencio. El schema efectivo donde caen las tablas lo fija el
`DEFAULT_SCHEMA` del login SQL con el que se conecta, no la variable de
entorno. Reusar el login de otro bot (por ejemplo `cavem_login_qa`) hace que
este bot escriba calladamente en el schema — y por lo tanto en la producción
— de ese otro bot. Ya pasó una vez en este stack (2026-09-02, schema
`botdemo` con el login de `wsp_demo` reusado).

- [ ] Levantar el contenedor y verificar el schema efectivo **antes** de
      correr cualquier `migrate`/seed/carga de datos:
  ```bash
  docker compose up -d --build
  docker compose exec web python manage.py dbshell
  ```
  ```sql
  select DB_NAME(), SCHEMA_NAME(), CURRENT_USER;
  ```
  Tiene que devolver `('QAIntouch', 'intouch', 'intouch_login_qa')`. Si dice
  otra cosa, **no seguir**: parar y corregir el login.
- [ ] Backfillear `Aplicacion.db_login` en el orquestador si el login se
      provisionó a mano (es el caso de este bot): `provisionar_en_bd` es un
      no-op silencioso cuando ese campo está vacío.

Migrar, en este orden — **es obligatorio, comparten `django_migrations`**:

```bash
docker compose exec web python manage.py migrate leads --database=qaintouch --noinput
docker compose exec web python manage.py migrate --noinput
```

**Ninguna migración contra la BD de producción sin confirmación explícita del
usuario**, aunque el riesgo técnico parezca nulo.

---

## 4. Conocimiento del RAG

Va antes que los datos de negocio y los prompts. Dos comandos, en este
orden, contra la misma BD que usa el bot:

```bash
docker compose exec web python manage.py cargar_conocimiento_rag                      # .md -> ScrapedPage
docker compose exec web python manage.py reindexar_conocimiento_rag --cliente intouch # ScrapedPage -> Supabase
```

**Se verifica que indexó chunks, no que el comando terminó sin error.**
`reindexar_conocimiento_rag` indexa `ScrapedPage`, no archivos: si nada creó
las páginas de los `.md`, el comando imprime "0 páginas reindexadas" y el RAG
queda vacío — el bot arranca perfecto y contesta cualquier cosa, es el modo
de falla más caro de esta parte. Verificar con:

```bash
docker compose exec web python manage.py doctor --seccion rag
```

que reporta chunks > 0, no sólo conexión OK. El barrido de huérfanas del
reindexado borra en Supabase las filas cuyo `scraped_page_id` no existe en
Django: cargar en una BD e indexar desde otra deja el conocimiento a medias.

**Los siete documentos de `bot/fixtures/rag/` (`sobre-intouch.md`,
`soluciones.md`, `modelos-de-operacion.md`, `canales.md`,
`analitica-y-calidad.md`, `integraciones.md`, `datos-y-seguridad.md`)
esperan revisión del usuario antes de indexar**: este bot no tiene precios
que anclen el contenido a la realidad, así que el conocimiento es tan bueno
como lo que quedó redactado ahí.

---

## 5. Datos de negocio

```bash
docker compose exec web python manage.py seed_intouch
```

Idempotente (`update_or_create`): soluciones, modelos de operación y el
prompt comercial. **No pisa el prompt si ya hay una `PromptVersion` activa.**
Para republicarlo:

```bash
docker compose exec web python manage.py seed_intouch --republicar-prompt
```

Esto va **detrás de visto bueno explícito del usuario**: publica estado de
producción (la `PromptVersion` activa que el bot usa en cada turno), no un
archivo del repo.

---

## 6. Frontend

```bash
cd /home/admincrm/wsp_intouch/frontend
corepack pnpm@9.15.0 install
corepack pnpm@9.15.0 build
cp -r dist/. /home/admincrm/staticfiles/mf/wsp_intouch/
```

**`pnpm build` por sí solo no despliega nada**: sin el `cp` a `staticfiles`,
nginx sigue sirviendo lo anterior. Es el error más repetido de este
ecosistema.

Es el pico de memoria de todo este trabajo (`pnpm build` pasa 1 GB) y el host
tiene swap en uso: **conviene no correrlo en paralelo con otro build** en el
mismo host.

---

## 7. nginx

Bloque a copiar de `/home/admincrm/gateway/nginx.conf` (el de Cavem, líneas
338-383 al momento de escribir esto), con `cavem`→`intouch` y `8030`→`8040`,
insertado **antes** de los catch-all (`location = /index.html` y
`location /`):

```nginx
# --- Panel de InTouch anidado bajo el launcher wsp_platform ---
location /intouch/api/ {
  proxy_pass http://127.0.0.1:8040/demo/api/;
  proxy_hide_header X-Content-Type-Options;
  proxy_hide_header Referrer-Policy;
}
location /intouch/media/ {
  proxy_pass http://127.0.0.1:8040/demo/media/;
}
# Remote de Module Federation (scope wsp_intouch), servido del disco.
location /mf/wsp_intouch/ {
  root /home/admincrm/staticfiles;
  expires 1y;
  add_header Cache-Control "public, immutable";
  try_files $uri =404;
  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
  add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
}
# Shell React. Gana sobre /wsp/ por prefijo mas largo.
location /wsp/intouch/ {
  root /home/admincrm/staticfiles/shell;
  try_files $uri /index.html;
  add_header Cache-Control "no-store" always;
  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
  add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'self'" always;
  add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
}

# --- Webhook Meta WhatsApp de InTouch (sólo si el número tiene Meta App propia) ---
location = /intouch/webhook {
  proxy_pass http://127.0.0.1:8040/webhook;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto $scheme;
}
```

Tres detalles no obvios:

1. **El `proxy_pass` de la API va a `/demo/api/`, no a `/intouch/api/`.** El
   Django clonado monta sus URLs bajo `/demo/` dentro del contenedor — es el
   prefijo interno heredado de wsp_cavem/wsp_demo. "Corregirlo" rompe el
   proxy.
2. **nginx no mergea `add_header` entre niveles**: cualquier `add_header` en
   un `location` tira abajo *todos* los del `server` para ese location, así
   que HSTS, CSP y Permissions-Policy se repiten literales en el bloque de la
   SPA (no alcanza con heredarlos del bloque `server`).
3. El `location = /intouch/webhook` (match exacto, para ganarle al catch-all
   de la SPA) sólo se agrega **si el número tiene Meta App propia**. Si entra
   en la App del dispatcher `wsp_webhook`, en cambio se registra su
   `phone_number_id` en el `BOT_MAP` de `wsp_webhook` y **no** se agrega este
   location — ver sección 8.

Después de editar:

```bash
cd /home/admincrm/gateway && docker compose up -d --force-recreate nginx
```

`nginx -s reload` **no aplica** los cambios: el conf es bind-mount.

**`gateway/nginx.conf` lo comparten todas las apps del host: se coordina con
su dueño antes de tocarlo**, y el commit de ese cambio va en **su** repo, no
en el de `wsp_intouch`.

---

## 8. Meta y DIOS

**DIOS (auto-registro):** al levantar el contenedor, el bot se registra
contra el orquestador (`docker compose logs web | grep -i dios` debería
mostrar el registro y los tipos de notificación declarados). **Es
fire-and-forget y se traga todas las excepciones** (`utils/
dios_registration.py`): la ausencia de errores en el log no confirma nada.
**Se verifica a mano en el panel SA.**

**Meta:** webhook a `https://qadash.in-touchcrm.cl/wsp/intouch/webhook` (o,
si el número entra en la App del dispatcher, el `BOT_MAP` de `wsp_webhook` en
vez de un location propio — ver sección 7 punto 3), con el
`WHATSAPP_VERIFY_TOKEN` del `.env.docker`. Sin campañas salientes no hacen
falta plantillas; si en algún momento las hay, se crean en idioma **`es`**,
nunca `es_CL` (Meta responde `132001` con `es_CL`).

**Panel SA — checklist de cuatro pasos, obligatorio:** `qaintouch` **ya
existe** como cuenta, y ése es precisamente el caso que rompe: habilitar una
app en una cuenta preexistente **no la provisiona** en su BD. El síntoma de
saltearse esto es un 500 en cualquier endpoint tenant-aware (SQL Server 4060
"login failed"), con el request colgado **~25 segundos** por el timeout de
login ODBC.

1. Confirmar que `Aplicacion.db_login` **no está vacío** (backfillearlo si el
   login se creó a mano — es el caso de este bot, ver sección 3).
2. Correr `account_sync` para la cuenta `qaintouch`.
3. Verificar **tablas reales** en la BD del tenant: `sys.tables` del schema
   `intouch`. No basta con que el sync devuelva "ok".
4. Probar un endpoint **tenant-aware real**, no uno público.

---

## 9. Verificación

```bash
docker compose exec web python manage.py doctor
```

Sólo lectura, sale con código ≠ 0 si hay falla. Es el único momento en que se
validan de verdad los grants de Supabase, el catálogo de modelos de
OpenRouter y la credencial de Meta — corrido con red, en el contenedor real,
no con el `docker run` efímero de los tests (ver `docs/
PENDIENTE_CREDENCIALES.md`). **Cero fallas es el piso.**

```bash
docker compose exec web python manage.py doctor --seccion prompts
```

Confirma que el prompt activo en BD corresponde al código desplegado. Si
difieren, **se despliegan juntos** — no uno después del otro (sección 5).

Suite completa (sólo si nadie más la está corriendo — coordinar por
`ListAgents`/`SendMessage`, ver `docs/superpowers/specs/
2026-09-09-bot-intouch-comercial-design.md` §11.2):

```bash
docker compose exec -e USE_SQLITE=true web python manage.py test
```

End-to-end con un WhatsApp real, contra el número de InTouch:

1. Un saludo → orienta sin abrir lead.
2. Una consulta por soluciones → llama a `listar_soluciones` y responde con
   el catálogo real (verificar en Langfuse que la tool se llamó).
3. Entregar empresa, correo y necesidad → el bot **no** afirma que quedó
   registrado; el lead aparece en el panel unos segundos después.
4. Pedir hablar con una persona → `solicita_contacto_humano=True` y, si
   califica HOT, la campanita del shell.
5. Una consulta de soporte → `crear_caso`, sin lead.
6. Una imagen o un audio → el bot lo percibe (verificar en los logs del
   contenedor, no en Langfuse: `_invoke_media` no tiene traza propia).
