# Integración Comp AI CRM — la ruta del lead · Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un lead calificado por `wsp_intouch` llegue al pipeline de Comp AI CRM sin duplicados indebidos, con los 22 campos preservados, y que un fallo de despacho quede visible y reintentable.

**Architecture:** El bot ya escribe `LeadInTouch` (fuente de verdad) desde el extractor de metadatos y tiene un adaptador de salida apagado (`LEAD_SINK=none`). Se construye el receptor como módulo NestJS nuevo en `apps/api/src/ingest/` del CRM vendored, que resuelve identidad y escribe Company/Contact/Deal/FieldValue/Activity en una transacción con advisory lock; y se completa el emisor con `evento_id`/`revision`, validación de respuesta y recuperación de pendientes por cron. El bot alcanza la API por una red Docker compartida, sin exponerla.

**Tech Stack:** CRM: bun 1.3.12, Node ≥22, NestJS 11, Prisma 6 + PostgreSQL 17, zod 4, Better Auth (plugin `apiKey`). Bot: Django, `urllib` de stdlib (no `requests`). Infra: Docker Compose, nginx del gateway en `network_mode: host`.

**Spec:** `docs/superpowers/specs/2026-09-09-integracion-compai-crm-design.md`

**Plan hermano:** `docs/superpowers/plans/2026-09-09-compai-crm-acceso-comercial.md` (el acceso del comercial y el puente de login; subsistema independiente, con su propia review de seguridad).

## Global Constraints

- **El contenedor que corre acá es producción.** `git add` explícito, nunca `-a`/`-A`. `git status` antes de cualquier reload. `ListAgents`/`SendMessage` antes de un rebuild o una migración.
- **Ninguna migración contra la BD real de `wsp_intouch` sin confirmación explícita del usuario**, aunque el riesgo técnico sea bajo.
- **Todo lo que un LLM va a leer se escribe en español correcto, con tildes** — docstrings, mensajes de error, `agentBrief` de campos. El modelo imita su corpus.
- **Versión del CRM fijada en v1.15.3.** No se actualiza a upstream durante la integración.
- **Nunca `prisma db push`, `migrate reset` ni `--accept-data-loss`** contra datos que no sean del entorno de test.
- **`CRM_TELEMETRY_DISABLED=1`** en todos los entornos: por defecto el CRM manda un evento diario a un proyecto de terceros.
- **No se despliega `apps/agent`** ni se habilitan Google/Microsoft/Slack, mailbox sync, enriquecimiento, Vercel Blob, AI Gateway ni tracking de sitios.
- **Postgres no publica puerto.** `crm-app` y `crm-api` publican sólo en `127.0.0.1`.
- **Datos de prueba**: nombres marcados `PRUEBA INTEGRACIÓN`, correos `@example.com`. Nunca se envía WhatsApp ni correo a direcciones de ejemplo.
- **Tests del CRM**: son de integración real y escriben filas. **Nunca se corren en el contenedor de la API de producción**: van en un servicio `tools` aparte, con su propia BD y un usuario de privilegio mínimo que **no tiene permisos sobre la BD de la app**. Un nombre terminado en `_test` no es aislamiento; el aislamiento es de credenciales.
- **Ninguna garantía de PostgreSQL se afirma con SQLite.** Locks, restricciones únicas, transacciones y carreras se prueban contra Postgres real. SQLite sirve para los tests del bot, que son de otra naturaleza.
- **Un archivo Compose autónomo, siempre invocado con `-f`.** No se usa un `override` para "quitar" cosas del compose de upstream: `ports: []` **no elimina el puerto heredado** — verificado con `docker compose config`, la lista se conserva y Postgres queda publicado.
- **Credenciales propias de Postgres.** Jamás `postgres:postgres`, que son las de desarrollo de upstream.
- **Ninguna migración corre en el arranque de la API.** Van en un paso de release explícito, revisado y coordinado.
- **Ninguna credencial se imprime.** Ni en terminal, ni en argumentos, ni en commits, ni en informes. Se muestra id o huella no reutilizable.
- **Limpieza de datos de prueba por ids propios**, nunca por prefijo de teléfono o de nombre: un `startsWith` puede borrar datos reales que casualmente coinciden.
- **`bun` no está en el host** (sólo Node v22.23.2). Todo comando `bun` corre dentro de un contenedor.
- **Criterio de un test que no rompe el repo**: `git status` limpio DESPUÉS de correr la suite.

---

### Task 1: CRM vendored, Postgres y build reproducible

**Files:**
- Create: `/home/admincrm/compai-crm/` (clon de `https://github.com/trycompai/crm.git`, **en el tag `v1.15.3`**)
- Create: `/home/admincrm/compai-crm/docker-compose.crm.yml` (**autónomo**, no un override)
- Create: `/home/admincrm/compai-crm/Dockerfile.api`
- Create: `/home/admincrm/compai-crm/Dockerfile.app`
- Create: `/home/admincrm/compai-crm/Dockerfile.tools`
- Create: `/home/admincrm/compai-crm/.dockerignore`
- Create: `/home/admincrm/compai-crm/.env`
- Create: `/home/admincrm/compai-crm/RUNBOOK.md`

**Interfaces:**
- Produces: contenedores `crm-postgres`, `crm-api` (interno 3001, publicado `127.0.0.1:3006`), `crm-app` (interno 3000, publicado `127.0.0.1:3005`); red externa `crm_ingest`; `GET /api/health` respondiendo 200.

- [ ] **Step 1: Clonar y fijar la versión en el TAG**

Clonar la rama `release` **no fija la versión**: la rama se mueve, y un clon
posterior traería otro commit. El tag `v1.15.3` existe (verificado en la API de
GitHub), así que se fija ahí.

```bash
cd /home/admincrm
git clone https://github.com/trycompai/crm.git compai-crm
cd compai-crm
git remote rename origin upstream
git checkout -b local/integracion-intouch v1.15.3
git rev-parse HEAD                      # anotar el SHA exacto en RUNBOOK.md
grep '"version"' package.json           # espera: "1.15.3"
```

Anotar en `RUNBOOK.md` el SHA, no sólo el número de versión: es lo único que
identifica sin ambigüedad qué se desplegó.

- [ ] **Step 2: Crear la red compartida con el bot**

```bash
docker network create crm_ingest
docker network ls | grep crm_ingest
```

Expected: una línea con `crm_ingest` y driver `bridge`.

- [ ] **Step 3: Escribir `Dockerfile.api`**

Upstream no trae ninguno. `bun` va dentro de la imagen porque no está en el host.

```dockerfile
FROM oven/bun:1.3.12-alpine

WORKDIR /app

# El lockfile y los manifests primero: así una edición de código no invalida
# la capa de dependencias del monorepo entero.
COPY package.json bun.lock turbo.json ./
COPY apps/api/package.json apps/api/
COPY apps/app/package.json apps/app/
COPY packages/db/package.json packages/db/
COPY packages/auth/package.json packages/auth/
COPY packages/env/package.json packages/env/
COPY packages/telemetry/package.json packages/telemetry/
COPY packages/validation/package.json packages/validation/
COPY packages/typescript-config/package.json packages/typescript-config/
COPY packages/ui/package.json packages/ui/
RUN bun install --frozen-lockfile

COPY . .

# El cliente Prisma se genera en el build: sin esto el arranque falla con
# "@prisma/client did not initialize yet".
RUN bun run db:generate

EXPOSE 3001
CMD ["bun", "apps/api/src/main.ts"]
```

- [ ] **Step 4: Escribir `Dockerfile.app`**

```dockerfile
FROM oven/bun:1.3.12-alpine

WORKDIR /app

COPY package.json bun.lock turbo.json ./
COPY apps/api/package.json apps/api/
COPY apps/app/package.json apps/app/
COPY packages/db/package.json packages/db/
COPY packages/auth/package.json packages/auth/
COPY packages/env/package.json packages/env/
COPY packages/telemetry/package.json packages/telemetry/
COPY packages/validation/package.json packages/validation/
COPY packages/typescript-config/package.json packages/typescript-config/
COPY packages/ui/package.json packages/ui/
RUN bun install --frozen-lockfile

COPY . .
RUN bun run db:generate

# basePath y NEXT_PUBLIC_API_URL quedan HORNEADOS en el bundle (spec §1.1):
# cambiar la URL pública exige rebuild, no reinicio. Se pasan como build args.
ARG APP_URL
ARG API_URL
ENV APP_URL=$APP_URL
ENV API_URL=$API_URL
RUN cd apps/app && bun run build

EXPOSE 3000
CMD ["sh", "-c", "cd apps/app && bun run start"]
```

- [ ] **Step 5: Escribir `docker-compose.crm.yml` (autónomo)**

**NO se usa un override.** El compose de upstream publica `5432:5432`, y un
`ports: []` en un override **no lo quita** — verificado:

```bash
docker compose config | grep -A3 'ports:'   # el 5432 heredado sigue ahí
```

Compose combina las listas en vez de reemplazarlas, así que Postgres quedaría
publicado al host mientras el plan afirma lo contrario. La solución es un
archivo propio y completo, invocado **siempre** con `-f`, que no herede nada:

```yaml
# docker-compose.crm.yml — autónomo. Usar SIEMPRE con -f:
#   docker compose -f docker-compose.crm.yml <cmd>
# Nunca `docker compose` a secas en este directorio: tomaría el
# docker-compose.yml de upstream, que publica Postgres al host.
services:
  postgres:
    image: postgres:17-alpine
    container_name: crm-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    # Sin `ports`: no se publica nada al host (prompt §E02).
    volumes:
      - crm-postgres:/var/lib/postgresql/data
      - ./tools/init-db.sql:/docker-entrypoint-initdb.d/10-init-db.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 3s
      timeout: 5s
      retries: 20
    networks: [crm_interna]

  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    container_name: crm-api
    restart: unless-stopped
    env_file: [.env]
    # 3001 adentro, 3006 afuera y sólo en loopback: el gateway corre en
    # network_mode host y llega por 127.0.0.1.
    ports: ["127.0.0.1:3006:3001"]
    depends_on:
      postgres: {condition: service_healthy}
    networks: [crm_interna, crm_ingest]
    # SIN migraciones en el arranque: van en un paso de release explícito
    # (Step 11). Migrar en cada arranque hace que un restart automático aplique
    # DDL sin que nadie lo haya revisado ni coordinado.

  app:
    build:
      context: .
      dockerfile: Dockerfile.app
      args:
        APP_URL: ${APP_URL}
        API_URL: ${API_URL}
    container_name: crm-app
    restart: unless-stopped
    env_file: [.env]
    ports: ["127.0.0.1:3005:3000"]
    depends_on: [api]
    networks: [crm_interna]

  # Imagen separada para migraciones, semillas y tests. NO es la imagen de
  # despliegue: trae herramientas que no tienen por qué existir en producción,
  # y su usuario de BD es otro.
  #
  # Los tests del CRM son de integración real y escriben filas. Correrlos en
  # el contenedor de la API sería correrlos con la credencial y la
  # DATABASE_URL de producción -- un fallo del setup y escriben en la BD real.
  tools:
    build:
      context: .
      dockerfile: Dockerfile.tools
    container_name: crm-tools
    env_file: [.env.tools]
    depends_on:
      postgres: {condition: service_healthy}
    networks: [crm_interna]
    profiles: [tools]        # no arranca solo; se invoca con `run --rm tools`

networks:
  crm_interna:
    driver: bridge
  crm_ingest:
    external: true

volumes:
  crm-postgres:
```

- [ ] **Step 6: Aislar la BD de pruebas por credenciales, no por nombre**

`tools/init-db.sql`, que Postgres corre en la primera inicialización:

```sql
-- Tres roles con propósitos distintos. El aislamiento de las pruebas es de
-- PERMISOS y no de nombre: un runner mal configurado que apunte a la BD de la
-- app tiene que recibir "permission denied", no escribir.
CREATE DATABASE crm_test;

CREATE ROLE crm_app  LOGIN PASSWORD :'app_password';
CREATE ROLE crm_test LOGIN PASSWORD :'test_password';

-- La app manda en su BD y no puede ni conectarse a la de pruebas.
GRANT ALL PRIVILEGES ON DATABASE crm TO crm_app;
REVOKE CONNECT ON DATABASE crm_test FROM PUBLIC;
GRANT CONNECT ON DATABASE crm_test TO crm_test;

-- Y el rol de pruebas NO puede conectarse a la BD de la app. Es la línea que
-- convierte "ojalá el runner esté bien configurado" en una garantía.
REVOKE CONNECT ON DATABASE crm FROM crm_test;
```

Verificarlo, porque es la defensa que importa:

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml exec postgres \
  psql "postgresql://crm_test:$TEST_PASSWORD@localhost:5432/crm" -c 'select 1'
```

Expected: `FATAL: permission denied for database "crm"`. Si conecta, las
pruebas pueden escribir en producción y **no se sigue** con las tareas
siguientes.

- [ ] **Step 7: Escribir `.dockerignore`**

Sin esto, el `COPY . .` de los Dockerfiles mete `.env`, `.git` y volcados de BD
en la imagen.

```
.git
.env
.env.*
*.dump
*.sql.gz
node_modules
**/node_modules
**/.next
**/dist
docs/
```

Comprobar que el secreto no viajó:

```bash
docker run --rm --entrypoint sh crm-api -c 'ls -a /app | grep -E "^\.env|^\.git" || echo "OK: sin secretos en la imagen"'
```

- [ ] **Step 8: Escribir `.env`**

Sólo lo que el código de esta versión usa. Sin `GEMINI_API_KEY`: no aparece en v1.15.3.

Credenciales **propias**: `postgres:postgres` son las de desarrollo de upstream
y no se reutilizan.

```bash
cd /home/admincrm/compai-crm
POSTGRES_PASSWORD=$(openssl rand -hex 24)
APP_PASSWORD=$(openssl rand -hex 24)
TEST_PASSWORD=$(openssl rand -hex 24)

cat > .env <<EOF
POSTGRES_USER=crm_owner
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_DB=crm

# La app usa el rol crm_app, que NO puede conectarse a la BD de pruebas.
DATABASE_URL="postgresql://crm_app:$APP_PASSWORD@postgres:5432/crm?schema=public"

# openssl rand -base64 32
BETTER_AUTH_SECRET=""

# Segunda puerta de acceso, además del puente de login (plan hermano).
ALLOWED_SIGN_IN="in-touchcrm.cl"

APP_URL="https://172.20.21.249/crm"
API_URL="https://172.20.21.249/crm-api"

# Tráfico saliente que nadie pidió: apagado (Global Constraints).
CRM_TELEMETRY_DISABLED="1"

# El principal de ingesta. Lo llena la Task 2.
INTOUCH_INGEST_USER_ID=""
INTOUCH_LEAD_OWNER_EMAIL=""
EOF

# Archivo aparte para las herramientas y los tests: su rol de BD es otro, y
# NO lleva BETTER_AUTH_SECRET ni la URL de la app.
cat > .env.tools <<EOF
DATABASE_URL="postgresql://crm_test:$TEST_PASSWORD@postgres:5432/crm_test?schema=public"
TEST_DATABASE_URL="postgresql://crm_test:$TEST_PASSWORD@postgres:5432/crm_test?schema=public"
CRM_TELEMETRY_DISABLED="1"
EOF

chmod 600 .env .env.tools
sed -i "s|^BETTER_AUTH_SECRET=.*|BETTER_AUTH_SECRET=\"$(openssl rand -base64 32)\"|" .env
```

**Registrar en `RUNBOOK.md`, por variable, si se consume al compilar o al
ejecutar.** `APP_URL` y `API_URL` quedan horneadas en el bundle del frontend
(§1.1): cambiarlas exige rebuild, no reinicio.

- [ ] **Step 9: Levantar y verificar que la API responde**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml up -d --build postgres api
docker compose -f docker-compose.crm.yml logs -f api | head -40
```

Expected: `API listening on http://localhost:3001`. **Todavía sin migraciones**:
las aplica el paso de release del Step 9, no el arranque.

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3006/api/health
```

Expected: `200`.

- [ ] **Step 10: Verificar que Postgres NO está expuesto**

```bash
ss -ltn | grep 5432 || echo "OK: 5432 no escucha en el host"
```

Expected: la línea `OK:`. Si aparece un listener, se está usando el compose de
upstream: revisar que TODOS los comandos lleven `-f docker-compose.crm.yml`.

- [ ] **Step 11: Aplicar las migraciones como paso de release explícito**

Con la imagen de herramientas, no con la de la API, y como un comando que una
persona decide correr:

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm \
  -e DATABASE_URL="postgresql://crm_owner:$POSTGRES_PASSWORD@postgres:5432/crm?schema=public" \
  tools bunx --bun prisma migrate deploy --schema packages/db/prisma/schema.prisma
```

`migrate deploy` y **nunca** `db push`, `migrate reset` ni
`--accept-data-loss`. Va con el rol `crm_owner` porque `crm_app` no tiene por
qué poder cambiar el esquema en caliente.

- [ ] **Step 12: Comprobar el esquema real, no sólo el historial**

```bash
docker compose -f docker-compose.crm.yml run --rm tools \
  bunx --bun prisma migrate status --schema packages/db/prisma/schema.prisma
```

Expected: `Database schema is up to date!`.

**Pero `migrate status` compara historiales de migración, no certifica que el
esquema físico coincida.** Para eso, un diff real contra el esquema declarado:

```bash
docker compose -f docker-compose.crm.yml run --rm tools \
  bunx --bun prisma migrate diff \
    --from-schema-datasource packages/db/prisma/schema.prisma \
    --to-schema-datamodel packages/db/prisma/schema.prisma \
    --exit-code
```

Expected: exit code `0` y ninguna diferencia. Un exit `2` significa deriva y
hay que resolverla antes de seguir.

- [ ] **Step 13: Verificar que la imagen ejecuta el build y no el fuente**

El `CMD` de `Dockerfile.api` corre `bun apps/api/src/main.ts`, o sea el fuente
bajo el runtime de Bun. Eso **no** es necesariamente equivalente al build de
Nest que el repo define en su script `build`, sobre todo con decoradores y
`reflect-metadata`. Comprobarlo antes de darlo por bueno:

```bash
docker compose -f docker-compose.crm.yml exec api sh -c 'ls dist/ 2>/dev/null || echo "sin dist: corre el fuente"'
curl -s http://127.0.0.1:3006/api/health
```

Si la API responde y los tests de la Task 9 pasan por HTTP, correr el fuente es
aceptable y queda anotado en `RUNBOOK.md` como decisión. Si algo falla con
metadatos de decoradores, cambiar el `CMD` a `bun run build` + `bun dist/main.js`
y volver a verificar.

- [ ] **Step 14: Commit**

```bash
cd /home/admincrm/compai-crm
git add Dockerfile.api Dockerfile.app Dockerfile.tools .dockerignore \
        docker-compose.crm.yml tools/init-db.sql RUNBOOK.md
git commit -m "infra: build y compose autonomo para desplegar el CRM en GranCRM-QA

Upstream no trae Dockerfile y su compose solo levanta Postgres, publicandolo
al host. Un override con ports: [] NO quita ese puerto -- compose combina las
listas -- asi que este es un archivo autonomo que se invoca siempre con -f.

Credenciales propias y tres roles: crm_owner migra, crm_app corre la app y
crm_test corre las pruebas, sin permiso para conectarse a la BD de la app.
Las migraciones NO van en el arranque: paso de release explicito."
```

Verificar que ningún secreto entró al commit:

```bash
git show --stat HEAD
git diff HEAD~1 HEAD | grep -iE "password|secret|crm_" | grep -v "crm_app\|crm_test\|crm_owner\|crm-" || echo "OK: sin secretos"
```

---

### Task 2: El principal de ingesta y su API key

**Files:**
- Create: `/home/admincrm/compai-crm/tools/seed-ingest-principal.ts`
- Modify: `/home/admincrm/compai-crm/.env` (rellenar `INTOUCH_INGEST_USER_ID`)

**Interfaces:**
- Consumes: contenedores de la Task 1.
- Produces: un `User` de servicio con `email` `bot-intouch@in-touchcrm.cl`; una API key `crm_…`; el valor de `INTOUCH_INGEST_USER_ID` (cuid del User).

**Por qué un usuario de servicio y no la key de una persona:** verificado en `packages/auth/src/auth.ts` — las keys se crean **sin `permissions`** y `enableSessionForAPIKeys: true` las vuelve equivalentes a su usuario dueño. Una key no tiene alcance propio; el alcance lo pone la Task 9 comparando contra este id.

**Y por eso no basta con chequearla en un endpoint.** Una key de un usuario con
acceso total, verificada sólo en la ruta de ingesta, sigue siendo una
credencial de acceso total: sirve para leer contactos, exportar y tocar
ajustes por cualquier otra ruta. El Step 4 lo comprueba, y si resulta que la
key abre el resto de la API, **el diseño de la credencial cambia** (Step 4) y
eso se decide antes de seguir, no después.

- [ ] **Step 1: Escribir el script de semilla**

```typescript
// tools/seed-ingest-principal.ts
//
// Crea el usuario de servicio que emite los leads de wsp_intouch y una API
// key suya. Idempotente: correrlo dos veces no duplica el usuario.
//
// La key se crea con el API de servidor de Better Auth pasando `userId`, sin
// sesión de navegador: el CRM no tiene login usuario/contraseña y no queremos
// que crear la credencial del bot dependa del puente de login.
import { auth } from "@crm/auth";
import { db } from "@crm/db";

const EMAIL = "bot-intouch@in-touchcrm.cl";

const user = await db.user.upsert({
	where: { email: EMAIL },
	update: {},
	create: {
		id: crypto.randomUUID(),
		name: "Bot Asesor Comercial IA (InTouch)",
		email: EMAIL,
		emailVerified: true,
	},
	select: { id: true },
});

// Idempotente: si ya hay una key vigente de esta integración, NO se emite
// otra. Emitir una key nueva en cada corrida de la semilla deja credenciales
// válidas huérfanas que nadie revoca.
const NOMBRE_KEY = "wsp_intouch — ingesta de leads";
const existente = await db.apikey.findFirst({
	where: { referenceId: user.id, name: NOMBRE_KEY, enabled: true },
	select: { id: true, start: true, expiresAt: true },
});

if (existente) {
	console.log(`INTOUCH_INGEST_USER_ID=${user.id}`);
	console.log(
		`Ya existe una key vigente (id ${existente.id}, prefijo ${existente.start}, ` +
			`vence ${existente.expiresAt?.toISOString() ?? "nunca"}). ` +
			"No se emite otra. Para rotarla: revocala explícitamente y volvé a correr esto.",
	);
	process.exit(0);
}

const created = await auth.api.createApiKey({
	body: { name: NOMBRE_KEY, userId: user.id, expiresIn: 365 * 24 * 60 * 60 },
});

// La key NO se imprime: se escribe a un archivo con permisos 600 que el
// operador mueve a la configuración del bot y borra. En pantalla va sólo la
// huella, que sirve para identificarla y no para usarla.
const destino = "/tmp/lead_sink_token";
await Bun.write(destino, created.key);
await Bun.$`chmod 600 ${destino}`;

const huella = new Bun.CryptoHasher("sha256").update(created.key).digest("hex");
console.log(`INTOUCH_INGEST_USER_ID=${user.id}`);
console.log(`Key escrita en ${destino} (permisos 600). Moverla y borrar el archivo.`);
console.log(`Huella sha256: ${huella.slice(0, 16)}…  prefijo: ${created.start}`);
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm \
  -e DATABASE_URL="postgresql://crm_owner:$POSTGRES_PASSWORD@postgres:5432/crm?schema=public" \
  tools bun tools/seed-ingest-principal.ts
```

Expected: el id del usuario, la ruta del archivo y la huella. **La key no
aparece en pantalla.**

**Si `createApiKey` rechaza el `userId`**, el supuesto del API de servidor es
falso: parar acá y reportarlo — es una decisión de diseño, no algo que se
parchee inventando el hash de la key a mano.

- [ ] **Step 3: Verificar que la key autentica de verdad**

No alcanza con que el script la haya emitido.

```bash
KEY=$(cat /tmp/lead_sink_token)
curl -s -o /dev/null -w 'con key: %{http_code}\n' -H "x-api-key: $KEY" http://127.0.0.1:3006/api/users.me
curl -s -o /dev/null -w 'sin key: %{http_code}\n' http://127.0.0.1:3006/api/users.me
```

Expected: `con key: 200` y `sin key: 401`. Si la primera da 401, la key no está autenticando y las Tasks 9 y 14 no se pueden validar.

- [ ] **Step 4: Medir el alcance REAL de la key — decide el diseño**

Éste es el paso que define si la credencial sirve. Se prueba contra las rutas
que el bot **no** tiene por qué poder usar:

```bash
KEY=$(cat /tmp/lead_sink_token)
for ruta in api/contacts.list api/companies.list api/users.list api/settings.get api/deals.list; do
  printf '%-24s %s\n' "$ruta" \
    "$(curl -s -o /dev/null -w '%{http_code}' -H "x-api-key: $KEY" http://127.0.0.1:3006/$ruta)"
done
```

(Los nombres exactos de procedimiento se leen del router real; lo que importa
es probar lectura de contactos, empresas, usuarios, ajustes y oportunidades.)

**Interpretación, y es una bifurcación de diseño:**

- Si todas devuelven **401/403**: la key ya está acotada. Se sigue con la Task 3.
- Si alguna devuelve **200**: la key es una credencial de acceso total y
  chequearla en un endpoint no la limita. Hay que elegir, **documentar la
  elección antes de implementarla**, y recién entonces seguir:
  1. Sacar `enableSessionForAPIKeys` para esta key en `packages/auth`, o
     restringir por `permissions` si la versión instalada lo soporta —
     comprobando que no rompe otros usos de keys en el CRM.
  2. O montar una credencial de integración **fuera** de Better Auth: un
     secreto propio verificado por un guard de Nest sólo en `/api/ingest`, con
     su propia rotación. Es menos reuso pero alcance exacto.

Anotar el resultado y la decisión en `RUNBOOK.md`. La Task 9 prueba el
resultado final por HTTP (caso R06).

- [ ] **Step 5: Definir expiración, rotación y revocación**

La key vence en 365 días. Anotar en `RUNBOOK.md`: cómo se revoca, cómo se
emite el relevo y qué pasa con los leads pendientes durante el cambio (no se
pierden: quedan sin sellar y el barrido de la Task 12 los reintenta con el
mismo `evento_id`). Probar el rechazo de una key revocada:

```bash
# Revocar por id desde la UI del CRM o el API, y reintentar:
curl -s -o /dev/null -w 'revocada: %{http_code}\n' -H "x-api-key: $KEY" \
  http://127.0.0.1:3006/api/users.me
```

Expected: `401`.

- [ ] **Step 6: Guardar el id en `.env`, mover la key y reiniciar**

```bash
cd /home/admincrm/compai-crm
sed -i 's|^INTOUCH_INGEST_USER_ID=.*|INTOUCH_INGEST_USER_ID="<el id del paso 2>"|' .env
docker compose -f docker-compose.crm.yml up -d api
# La key va al .env.docker del bot (Task 14). Después:
shred -u /tmp/lead_sink_token 2>/dev/null || rm -f /tmp/lead_sink_token
```

- [ ] **Step 7: Commit**

```bash
git add tools/seed-ingest-principal.ts
git commit -m "feat(ingest): usuario de servicio y API key para la ingesta de wsp_intouch

Las keys del CRM se crean sin permissions y enableSessionForAPIKeys las
vuelve equivalentes a su usuario dueño: por eso la ingesta tiene un
principal dedicado, y no la key de una persona. El alcance se chequea
explícitamente contra su id."
```

---

### Task 3: El modelo `LeadIngestEvent`

**Files:**
- Modify: `/home/admincrm/compai-crm/packages/db/prisma/schema.prisma` (agregar al final)
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-event.spec.ts`

**Interfaces:**
- Produces: modelo `LeadIngestEvent` con `@@unique([origin, eventoId])` y `@@unique([origin, claveContacto, revision])`; la migración aplicada.

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-event.spec.ts
import { describe, expect, test } from "bun:test";
import { db } from "@crm/db";

const ORIGEN = "wsp_intouch";

function fila(over: Record<string, unknown> = {}) {
	return {
		origin: ORIGEN,
		eventoId: crypto.randomUUID(),
		claveContacto: crypto.randomUUID().slice(0, 32),
		revision: 1,
		payloadHash: "abc123",
		payload: { empresa: "PRUEBA INTEGRACIÓN SpA" },
		status: "created",
		...over,
	};
}

describe("LeadIngestEvent", () => {
	test("un evento_id repetido del mismo origen no se puede insertar dos veces", async () => {
		const base = fila();
		await db.leadIngestEvent.create({ data: base });
		await expect(
			db.leadIngestEvent.create({ data: { ...fila(), eventoId: base.eventoId } }),
		).rejects.toThrow();
	});

	test("una revisión repetida para la misma clave de contacto tampoco", async () => {
		const base = fila();
		await db.leadIngestEvent.create({ data: base });
		await expect(
			db.leadIngestEvent.create({
				data: { ...fila(), claveContacto: base.claveContacto, revision: 1 },
			}),
		).rejects.toThrow();
	});

	test("dos revisiones distintas de la misma clave de contacto sí conviven", async () => {
		const base = fila();
		await db.leadIngestEvent.create({ data: base });
		const segunda = await db.leadIngestEvent.create({
			data: { ...fila(), claveContacto: base.claveContacto, revision: 2 },
		});
		expect(segunda.revision).toBe(2);
	});

	test("el payload se guarda estructurado y vuelve igual", async () => {
		// El punto de §3.1 del spec: los arrays no se pierden. Un elemento con
		// una coma adentro no se puede reconstruir de un texto unido por comas.
		const canales = ["whatsapp", "correo, teléfono", "web"];
		const creada = await db.leadIngestEvent.create({
			data: fila({ payload: { canales_actuales: canales } }),
		});
		const leida = await db.leadIngestEvent.findUniqueOrThrow({
			where: { id: creada.id },
		});
		expect((leida.payload as { canales_actuales: string[] }).canales_actuales)
			.toEqual(canales);
	});
});
```

- [ ] **Step 2: Preparar la BD de test y correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun run db:test
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-event.spec.ts
```

Expected: FAIL — `db.leadIngestEvent` no existe (`Cannot read properties of undefined`).

- [ ] **Step 3: Agregar el modelo al schema**

Al final de `packages/db/prisma/schema.prisma`:

```prisma
/// Un evento de ingesta de lead desde un bot externo.
///
/// Dos claves y no una (spec §4): `claveContacto` identifica la identidad
/// comercial y nunca cambia -- el emisor la deriva del número de WhatsApp, que
/// es único por contacto. `eventoId` identifica el INTENTO, y sólo cambia
/// cuando cambia el contenido. Con una sola clave, "misma clave con payload
/// distinto" sería el caso normal de una conversación que avanzó, y forzar un
/// conflicto ahí convertiría cada nueva calificación en un error.
///
/// `payload` guarda el cuerpo recibido íntegro: es la copia reversible de los
/// arrays, que los campos dinámicos del CRM no pueden representar sin pérdida
/// (FieldValue tiene un valor por campo y FieldType no tiene MULTI_SELECT).
model LeadIngestEvent {
  id            String   @id @default(cuid())
  origin        String
  eventoId      String
  claveContacto String
  revision      Int
  payloadHash   String
  payload       Json
  status        String
  conflictReason String?
  companyId     String?
  contactId     String?
  dealId        String?
  createdAt     DateTime @default(now())

  @@unique([origin, eventoId])
  @@unique([origin, claveContacto, revision])
  @@index([origin, claveContacto])
  @@map("leadIngestEvent")
}
```

- [ ] **Step 4: Crear la migración**

```bash
cd /home/admincrm/compai-crm
docker compose exec api bunx --bun prisma migrate dev \
  --schema packages/db/prisma/schema.prisma \
  --name add_lead_ingest_event
```

Revisar el SQL generado antes de seguir: debe ser sólo `CREATE TABLE` + índices, **sin ningún `ALTER`/`DROP` sobre tablas existentes**.

```bash
cat packages/db/prisma/migrations/*add_lead_ingest_event/migration.sql
```

- [ ] **Step 5: Correr el test otra vez**

```bash
docker compose exec api bun run db:test
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-event.spec.ts
```

Expected: PASS, los 4.

- [ ] **Step 6: Commit**

```bash
git add packages/db/prisma/schema.prisma \
        packages/db/prisma/migrations \
        apps/api/test/ingest-event.spec.ts
git commit -m "feat(db): LeadIngestEvent, con las dos claves de idempotencia

Las dos restricciones únicas son la garantía estructural, no el código:
(origin, eventoId) para el replay y (origin, claveContacto, revision) para
que una revisión duplicada o fuera de orden colisione en la BD.
payload guarda el cuerpo íntegro, que es la copia reversible de los arrays."
```

---

### Task 4: Los campos dinámicos sembrados

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest-fields.ts`
- Create: `/home/admincrm/compai-crm/tools/seed-intouch-fields.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-fields.spec.ts`

**Interfaces:**
- Produces: `CAMPOS_INTOUCH` (array de definiciones), `sembrarCamposInTouch(db): Promise<number>`; 14 filas `FieldDefinition` con `@@unique([entity, key])`.

**Por qué campos dinámicos y no un fork del schema:** `FieldDefinition`/`FieldValue` ya existen sobre COMPANY/CONTACT/DEAL con `showOnSheet`/`showOnTable`/`showOnFilter`. Los campos de calificación entran ahí y se ven en la UI sin escribir frontend ni tocar tablas de upstream.

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-fields.spec.ts
import { describe, expect, test } from "bun:test";
import { db, FieldEntity } from "@crm/db";
import { CAMPOS_INTOUCH, sembrarCamposInTouch } from "../src/ingest/ingest-fields";

describe("campos dinámicos de InTouch", () => {
	test("sembrar dos veces no duplica ninguna definición", async () => {
		await sembrarCamposInTouch(db);
		await sembrarCamposInTouch(db);
		const claves = CAMPOS_INTOUCH.map((c) => c.key);
		const filas = await db.fieldDefinition.findMany({
			where: { key: { in: claves } },
			select: { entity: true, key: true },
		});
		expect(filas.length).toBe(CAMPOS_INTOUCH.length);
	});

	test("usa_ia_actualmente es CHECKBOX, para que el tri-estado sobreviva", async () => {
		// FieldValue.bool es nullable: ausente no es lo mismo que false, y esa
		// distinción ya costó datos en el bot (un false de relleno borraba lo
		// capturado en el turno anterior).
		await sembrarCamposInTouch(db);
		const campo = await db.fieldDefinition.findUniqueOrThrow({
			where: { entity_key: { entity: FieldEntity.CONTACT, key: "usa_ia_actualmente" } },
		});
		expect(campo.type).toBe("CHECKBOX");
	});

	test("todos los campos se ven en el detalle del registro", async () => {
		// Ninguno se oculta por no caber en una tarjeta (prompt §C01).
		await sembrarCamposInTouch(db);
		const claves = CAMPOS_INTOUCH.map((c) => c.key);
		const ocultos = await db.fieldDefinition.findMany({
			where: { key: { in: claves }, showOnSheet: false },
			select: { key: true },
		});
		expect(ocultos).toEqual([]);
	});

	test("los campos que el bot no llena quedan fuera del alcance del agente", async () => {
		// agentFilled por defecto es true en el schema. Estos los escribe la
		// ingesta, y el agente de research no se despliega: dejarlo en true
		// insinuaría que algo los va a completar.
		await sembrarCamposInTouch(db);
		const claves = CAMPOS_INTOUCH.map((c) => c.key);
		const delAgente = await db.fieldDefinition.findMany({
			where: { key: { in: claves }, agentFilled: true },
			select: { key: true },
		});
		expect(delAgente).toEqual([]);
	});
});
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-fields.spec.ts
```

Expected: FAIL — no existe `../src/ingest/ingest-fields`.

- [ ] **Step 3: Escribir las definiciones**

```typescript
// apps/api/src/ingest/ingest-fields.ts
//
// Los campos del contrato de wsp_intouch que el CRM no tiene nativos. Van como
// campos dinámicos (FieldDefinition/FieldValue) y no como columnas nuevas: así
// se ven en el detalle del registro sin escribir frontend y sin forkear el
// schema de upstream.
//
// Los ocho nativos NO están acá: nombre_completo, correo, telefono y cargo van
// al Contact; empresa, industria y subtipo_automotriz a la Company;
// necesidad_principal a Deal.description (y además a un campo de Contact, para
// que sobreviva cuando la política no abre Deal -- spec §3.2).
import { type Db, FieldEntity, FieldType } from "@crm/db";

export type CampoInTouch = {
	entity: FieldEntity;
	key: string;
	label: string;
	type: FieldType;
	agentBrief: string;
};

export const CAMPOS_INTOUCH: readonly CampoInTouch[] = [
	{
		entity: FieldEntity.CONTACT,
		key: "pais_ciudad",
		label: "País y ciudad",
		type: FieldType.TEXT,
		agentBrief:
			"Como lo dijo el contacto. No se separa en país y ciudad a propósito: " +
			"inferir cuál es cuál sería afirmar un hecho que nadie dio.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "necesidad_principal",
		label: "Necesidad principal",
		type: FieldType.LONG_TEXT,
		agentBrief:
			"Lo que el contacto dijo que necesita. Se guarda también acá y no sólo " +
			"en la oportunidad, porque un lead que no calificó igual explicó su necesidad.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "situacion_contact_center",
		label: "Situación de contact center",
		type: FieldType.TEXT,
		agentBrief: "Si tiene o no tiene contact center. Valores: tiene, no_tiene.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "tipo_contact_center",
		label: "Tipo de contact center",
		type: FieldType.TEXT,
		agentBrief:
			"propio, externalizado, mixto o no_tiene. Queda vacío si no se sabe " +
			"si tiene uno.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "usa_ia_actualmente",
		label: "¿Usa IA actualmente?",
		type: FieldType.CHECKBOX,
		agentBrief:
			"Tres estados y no dos: sin marcar significa que nadie preguntó, y eso " +
			"no es lo mismo que 'no usa'. El bot deja el valor sin escribir cuando " +
			"no hay evidencia.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "canales_actuales",
		label: "Canales actuales",
		type: FieldType.LONG_TEXT,
		agentBrief:
			"Texto de presentación, derivado. La lista original se conserva " +
			"estructurada en el evento de ingesta: es de ahí que hay que leerla " +
			"si se necesita procesar los elementos.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "volumen_interacciones",
		label: "Volumen de interacciones",
		type: FieldType.TEXT,
		agentBrief:
			"Como lo informó el contacto, conservando período y unidad. No se " +
			"normaliza a un número: '5 mil al mes' y '5000' no dicen lo mismo " +
			"sobre cuánta precisión dio.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "telefono_whatsapp",
		label: "Teléfono de WhatsApp",
		type: FieldType.PHONE,
		agentBrief:
			"El número por el que escribió, que lo agrega la plataforma desde los " +
			"metadatos de WhatsApp. Se guarda acá cuando el contacto ya tenía otro " +
			"teléfono cargado: pisar el que había borraría el dato de una persona.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "origen",
		label: "Origen",
		type: FieldType.TEXT,
		agentBrief:
			"Qué sistema creó el registro, por ejemplo wsp_intouch. RecordSource " +
			"del CRM no tiene un valor para bots, así que la procedencia real vive acá.",
	},
	{
		entity: FieldEntity.CONTACT,
		key: "clave_contacto",
		label: "Clave de contacto del bot",
		type: FieldType.TEXT,
		agentBrief:
			"Hash estable con el que el bot identifica a este contacto. Sirve para " +
			"rastrear de qué conversación salió el registro. No es un dato personal.",
	},
	{
		entity: FieldEntity.DEAL,
		key: "lead_score",
		label: "Calificación del bot",
		type: FieldType.TEXT,
		agentBrief:
			"HOT, WARM, COLD o NO_CALIFICADO. Lo calcula el código del bot desde " +
			"señales de la conversación, nunca el modelo. NO es una etapa del " +
			"pipeline: la etapa dice en qué punto está la oportunidad, esto dice " +
			"qué tan caliente venía el contacto.",
	},
	{
		entity: FieldEntity.DEAL,
		key: "soluciones_interes",
		label: "Soluciones de interés",
		type: FieldType.LONG_TEXT,
		agentBrief:
			"Texto de presentación, derivado. La lista original está estructurada " +
			"en el evento de ingesta.",
	},
	{
		entity: FieldEntity.DEAL,
		key: "plazo_proyecto",
		label: "Plazo del proyecto",
		type: FieldType.TEXT,
		agentBrief:
			"Como lo dijo el contacto. No se convierte a fecha: 'para el próximo " +
			"trimestre' no es un día concreto y ponerle uno sería inventarlo.",
	},
	{
		entity: FieldEntity.DEAL,
		key: "intencion",
		label: "Intención comercial",
		type: FieldType.TEXT,
		agentBrief:
			"La clasificación comercial que dio el bot. Es una lectura de la " +
			"conversación, no un compromiso del contacto.",
	},
];

/// Crea las definiciones que falten. Idempotente por `@@unique([entity, key])`:
/// se puede correr en cada despliegue sin duplicar nada.
///
/// `position` arranca en 1000 para no pelear con los campos que el equipo del
/// CRM pueda crear a mano desde la UI.
export async function sembrarCamposInTouch(db: Db): Promise<number> {
	let creados = 0;
	for (const [indice, campo] of CAMPOS_INTOUCH.entries()) {
		const resultado = await db.fieldDefinition.upsert({
			where: { entity_key: { entity: campo.entity, key: campo.key } },
			update: {
				label: campo.label,
				agentBrief: campo.agentBrief,
				showOnSheet: true,
				agentFilled: false,
			},
			create: {
				entity: campo.entity,
				key: campo.key,
				label: campo.label,
				type: campo.type,
				agentBrief: campo.agentBrief,
				// Los escribe la ingesta; el agente de research no se despliega.
				agentFilled: false,
				showOnSheet: true,
				showOnTable: false,
				showOnFilter: campo.key === "lead_score",
				position: 1000 + indice,
			},
			select: { createdAt: true, updatedAt: true },
		});
		if (resultado.createdAt.getTime() === resultado.updatedAt.getTime()) creados += 1;
	}
	return creados;
}
```

- [ ] **Step 4: Escribir el script que lo corre en el despliegue**

```typescript
// tools/seed-intouch-fields.ts
import { db } from "@crm/db";
import { sembrarCamposInTouch } from "../apps/api/src/ingest/ingest-fields";

const creados = await sembrarCamposInTouch(db);
console.log(`Campos de InTouch sembrados. Nuevos: ${creados}.`);
```

- [ ] **Step 5: Correr los tests**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-fields.spec.ts
```

Expected: PASS, los 4.

- [ ] **Step 6: Sembrar en el entorno real y commit**

```bash
docker compose exec api bun tools/seed-intouch-fields.ts
git add apps/api/src/ingest/ingest-fields.ts \
        tools/seed-intouch-fields.ts \
        apps/api/test/ingest-fields.spec.ts
git commit -m "feat(ingest): campos dinamicos del contrato de InTouch

FieldDefinition/FieldValue ya existen sobre COMPANY/CONTACT/DEAL, asi que
los 14 campos de calificacion entran ahi y se ven en el detalle sin
forkear el schema ni escribir frontend. usa_ia_actualmente va como
CHECKBOX para que FieldValue.bool conserve el tri-estado."
```

---

### Task 5: El contrato de entrada

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest.contracts.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-contract.spec.ts`

**Interfaces:**
- Produces: `leadInTouchSchema` (zod), `type LeadInTouchPayload`, `MAX_INGEST_BODY_BYTES`.

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-contract.spec.ts
import { describe, expect, test } from "bun:test";
import { leadInTouchSchema } from "../src/ingest/ingest.contracts";

function valido(over: Record<string, unknown> = {}) {
	return {
		origen: "wsp_intouch",
		clave_contacto: "a".repeat(32),
		evento_id: crypto.randomUUID(),
		revision: 1,
		telefono: "56900000001",
		nombre_completo: "PRUEBA INTEGRACIÓN Ana",
		correo: "ana@example.com",
		empresa: "PRUEBA INTEGRACIÓN SpA",
		industria: "Retail",
		lead_score: "WARM",
		solicita_consultoria: false,
		solicita_contacto_humano: false,
		canales_actuales: ["whatsapp"],
		soluciones_interes: [],
		...over,
	};
}

describe("contrato de entrada del lead", () => {
	test("un payload válido pasa", () => {
		expect(leadInTouchSchema.safeParse(valido()).success).toBe(true);
	});

	test('el string "false" NO se vuelve true', () => {
		// Boolean("false") es true en JavaScript. Coercionar por truthiness acá
		// convertiría "el contacto no pidió hablar con nadie" en "sí pidió".
		const r = leadInTouchSchema.safeParse(
			valido({ solicita_contacto_humano: "false" }),
		);
		expect(r.success).toBe(false);
	});

	test("un booleano ausente y uno en null son distintos de false", () => {
		const ausente = leadInTouchSchema.safeParse(valido());
		expect(ausente.success).toBe(true);
		expect(ausente.success && ausente.data.usa_ia_actualmente).toBeUndefined();

		const nulo = leadInTouchSchema.safeParse(valido({ usa_ia_actualmente: null }));
		expect(nulo.success).toBe(true);
		expect(nulo.success && nulo.data.usa_ia_actualmente).toBeNull();
	});

	test("una clave desconocida rechaza en vez de descartarse", () => {
		// Sin esto, un nombre de campo alucinado por el LLM del bot se perdería
		// sin que nada avise -- y una clave interna podría colarse al modelo.
		const r = leadInTouchSchema.safeParse(valido({ ownerId: "yo-elijo-el-dueno" }));
		expect(r.success).toBe(false);
	});

	test("un correo con formato inválido rechaza", () => {
		expect(leadInTouchSchema.safeParse(valido({ correo: "ana-arroba-nada" })).success)
			.toBe(false);
	});

	test("un lead_score fuera del enum rechaza", () => {
		expect(leadInTouchSchema.safeParse(valido({ lead_score: "TIBIO" })).success)
			.toBe(false);
	});

	test("un array con un elemento que no es string rechaza", () => {
		expect(leadInTouchSchema.safeParse(valido({ canales_actuales: ["ok", 7] })).success)
			.toBe(false);
	});

	test("una revision que no es entero positivo rechaza", () => {
		expect(leadInTouchSchema.safeParse(valido({ revision: 0 })).success).toBe(false);
		expect(leadInTouchSchema.safeParse(valido({ revision: 1.5 })).success).toBe(false);
	});

	test("un texto larguísimo rechaza en vez de truncarse en silencio", () => {
		expect(leadInTouchSchema.safeParse(valido({ empresa: "x".repeat(5000) })).success)
			.toBe(false);
	});

	test("el correo es opcional: el bot tiene prohibido inventarlo", () => {
		const r = leadInTouchSchema.safeParse(valido({ correo: "" }));
		expect(r.success).toBe(true);
	});
});
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-contract.spec.ts
```

Expected: FAIL — no existe el módulo.

- [ ] **Step 3: Escribir el contrato**

```typescript
// apps/api/src/ingest/ingest.contracts.ts
//
// El contrato de los 22 campos que emite wsp_intouch. zod es la biblioteca de
// validación del proyecto (packages/validation y todos los *.contracts.ts).
//
// Reglas que no se negocian:
//  - `.strict()`: una clave desconocida RECHAZA. Nunca asignación masiva, y
//    nunca un campo alucinado por el LLM perdiéndose sin aviso.
//  - Booleanos JSON reales, sin coerción: Boolean("false") es true.
//  - Los campos internos (dueño, etapa, ids, notas) NO se leen del body. Los
//    resuelve el servicio desde configuración e identidad confiable.
import { z } from "zod";

/// Tope de tamaño del cuerpo. Un body enorme es un error de entrada (400/413),
/// nunca un 500 ni una escritura parcial.
export const MAX_INGEST_BODY_BYTES = 64 * 1024;

const texto = (max: number) => z.string().trim().max(max);
const textoOpcional = (max: number) => texto(max).optional();

/// Tres estados y no dos. `undefined` es "nadie preguntó", `null` es lo mismo
/// dicho explícitamente, y `false` es "el contacto dijo que no". Fusionar los
/// tres en un booleano ya costó datos reales en el bot.
const triEstado = z.boolean().nullish();

export const leadInTouchSchema = z
	.object({
		// Transporte e idempotencia (spec §4). No son datos de negocio.
		origen: z.literal("wsp_intouch"),
		clave_contacto: z.string().regex(/^[0-9a-f]{32}$/),
		evento_id: z.string().uuid(),
		revision: z.number().int().positive(),

		// Identificación
		nombre_completo: textoOpcional(200),
		correo: z.union([z.string().trim().email().max(200), z.literal("")]).optional(),
		telefono: texto(20),
		empresa: textoOpcional(200),
		industria: textoOpcional(120),
		subtipo_automotriz: z
			.enum([
				"importador", "concesionario", "automotora", "servicio_tecnico",
				"rent_a_car", "financiera", "otro",
			])
			.or(z.literal(""))
			.optional(),
		cargo: textoOpcional(120),
		pais_ciudad: textoOpcional(120),

		// Diagnóstico
		situacion_contact_center: z.enum(["tiene", "no_tiene"]).or(z.literal("")).optional(),
		tipo_contact_center: z
			.enum(["propio", "externalizado", "mixto", "no_tiene"])
			.or(z.literal(""))
			.optional(),
		usa_ia_actualmente: triEstado,
		canales_actuales: z.array(texto(120)).max(20).optional(),
		volumen_interacciones: textoOpcional(120),
		necesidad_principal: textoOpcional(4000),
		soluciones_interes: z.array(texto(120)).max(20).optional(),
		intencion: textoOpcional(200),
		plazo_proyecto: textoOpcional(120),

		// Calificación
		lead_score: z.enum(["HOT", "WARM", "COLD", "NO_CALIFICADO"]).or(z.literal("")),
		solicita_consultoria: z.boolean(),
		solicita_contacto_humano: z.boolean(),

		// Cierre. Contenido NO confiable: se guarda como texto, nunca se
		// renderiza como HTML, y ningún agente posterior lo lee como instrucción.
		resumen_conversacion: textoOpcional(8000),
		siguiente_accion_recomendada: textoOpcional(4000),
	})
	.strict();

export type LeadInTouchPayload = z.infer<typeof leadInTouchSchema>;
```

- [ ] **Step 4: Correr los tests**

```bash
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-contract.spec.ts
```

Expected: PASS, los 10.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/ingest/ingest.contracts.ts apps/api/test/ingest-contract.spec.ts
git commit -m "feat(ingest): contrato zod de los 22 campos de InTouch

strict() para que una clave desconocida rechace en vez de perderse, y
booleanos JSON reales sin coercion: Boolean('false') es true, y coercionar
convertiria 'no pidio hablar con nadie' en 'si pidio'. Los tres campos
booleanos aceptan null/ausente porque 'nadie pregunto' no es 'dijo que no'."
```

---

### Task 6: Resolución de identidad

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest-identity.service.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-identity.spec.ts`

**Interfaces:**
- Consumes: `LeadInTouchPayload` (Task 5); `domainFromEmail` de `../companies/domain`; `splitName` de `../mailbox/participants`; `normalizeEmail` de `../crm/values`.
- Produces: `IngestIdentityService` con `resolver(tx, payload): Promise<Resolucion>` — **sin efectos**, y `aplicarIdentidad(tx, payload, plan): Promise<{ companyId: string | null; contactId: string }>` — que escribe. Tipos: `Resolucion = { ok: true; plan: PlanIdentidad } | { ok: false; motivo: string; codigo: string }` y `PlanIdentidad = { empresa: {accion: "usar"; id: string} | {accion: "crear"; nombre: string; dominio: string | null} | {accion: "ninguna"}; contacto: {accion: "usar"; id: string} | {accion: "crear"} }`.

**El punto donde una fusión equivocada cuesta datos de un tercero. Ninguna regla adivina.**

**Y una corrección de fondo sobre el diseño anterior: la resolución NO escribe.**
Antes creaba la Company y *después* podía devolver conflicto de contacto — pero
la transacción seguía y commiteaba, así que quedaba una empresa creada por un
evento que se rechazó. Efectos parciales, exactamente lo que el plan decía
evitar. Ahora son dos fases: `resolver` decide y devuelve un **plan** sin tocar
la base, y `aplicarIdentidad` lo ejecuta **sólo** si el plan completo es
aceptable.

**Y otra: no se fusiona por nombre, tampoco entre empresas sin dominio.** El
código anterior buscaba por `name` entre las de `domain: null` y decía
comprobar el origen, pero no lo comprobaba: el campo `origen` es un
`FieldValue` y la consulta no lo miraba. Acá se busca por el **vínculo de
origen**, que es lo único que prueba que esa fila la creó esta integración.

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-identity.spec.ts
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { db } from "@crm/db";
import { IngestIdentityService } from "../src/ingest/ingest-identity.service";

const servicio = new IngestIdentityService();

function payload(over: Record<string, unknown> = {}) {
	return {
		origen: "wsp_intouch" as const,
		clave_contacto: "b".repeat(32),
		evento_id: crypto.randomUUID(),
		revision: 1,
		telefono: "56900000900",
		nombre_completo: "PRUEBA INTEGRACIÓN Ana Pérez",
		empresa: "PRUEBA INTEGRACIÓN SpA",
		lead_score: "WARM" as const,
		solicita_consultoria: false,
		solicita_contacto_humano: false,
		...over,
	};
}

describe("resolución de identidad", () => {
	// Cada corrida usa su propio sufijo, y limpia SÓLO lo que ella creó. Un
	// `deleteMany` por prefijo de teléfono o de nombre puede borrar filas
	// reales que casualmente coincidan -- y acá esas filas son de un cliente.
	const creados: { contactos: string[]; empresas: string[] } = { contactos: [], empresas: [] };

	afterEach(async () => {
		for (const id of creados.contactos) {
			await db.contact.delete({ where: { id } }).catch(() => {});
		}
		for (const id of creados.empresas) {
			await db.company.delete({ where: { id } }).catch(() => {});
		}
		creados.contactos.length = 0;
		creados.empresas.length = 0;
	});

	/// Registra lo que un test crea, para que `afterEach` lo borre por id.
	async function anotar(r: { companyId: string | null; contactId: string }) {
		creados.contactos.push(r.contactId);
		if (r.companyId) creados.empresas.push(r.companyId);
		return r;
	}

	test("resolver NO escribe nada, ni cuando decide crear", async () => {
		// La garantía que hace imposible el efecto parcial: la fase de decisión
		// no toca la base, así que un conflicto descubierto al final no puede
		// dejar una empresa creada por un evento rechazado.
		const antesEmpresas = await db.company.count();
		const antesContactos = await db.contact.count();
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ correo: "ana@acme-prueba.cl" })),
		);
		expect(r.ok).toBe(true);
		expect(await db.company.count()).toBe(antesEmpresas);
		expect(await db.contact.count()).toBe(antesContactos);
	});

	test("un correo corporativo planifica la empresa por dominio", async () => {
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ correo: "ana@acme-prueba.cl" })),
		);
		expect(r.ok).toBe(true);
		const plan = (r as { plan: { empresa: Record<string, unknown> } }).plan;
		expect(plan.empresa).toMatchObject({
			accion: "crear", dominio: "acme-prueba.cl",
		});
	});

	test("un conflicto de contacto NO deja empresa creada", async () => {
		// El caso concreto del defecto: dos contactos con el mismo teléfono, y
		// una empresa que el plan habría creado.
		await db.contact.create({ data: { firstName: "A", phone: "56900000905" } });
		await db.contact.create({ data: { firstName: "B", phone: "56900000905" } });
		const antes = await db.company.count();
		const r = await db.$transaction(async (tx) => {
			const resuelta = await servicio.resolver(tx, payload({
				telefono: "56900000905", correo: "ana@empresa-nueva-prueba.cl",
			}));
			if (!resuelta.ok) return resuelta;
			await servicio.aplicarIdentidad(tx, payload(), resuelta.plan);
			return resuelta;
		});
		expect(r.ok).toBe(false);
		expect(await db.company.count()).toBe(antes);
	});

	test("un correo de dominio gratuito NO identifica empresa por dominio", async () => {
		// domainFromEmail de upstream devuelve null para gmail y 20 más. Sin
		// esto, todos los contactos con gmail caerían en una misma "empresa".
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ correo: "ana@gmail.com" })),
		);
		expect(r.ok).toBe(true);
		expect((r as { plan: { empresa: { dominio: string | null } } }).plan.empresa.dominio)
			.toBeNull();
	});

	test("sin dominio NO se fusiona con una empresa que sí tiene dominio", async () => {
		// Un "Acme" que dijo el contacto por WhatsApp no puede meterse dentro
		// del Acme real que cargó una persona.
		const real = await db.company.create({
			data: { name: "PRUEBA INTEGRACIÓN SpA", domain: "acme-real-prueba.cl" },
		});
		const r = await db.$transaction((tx) => servicio.resolver(tx, payload()));
		expect((r as { plan: { empresa: { accion: string } } }).plan.empresa.accion)
			.toBe("crear");
		expect(JSON.stringify(r)).not.toContain(real.id);
	});

	test("sin dominio NO se fusiona por nombre, ni con otra sin dominio", async () => {
		// La corrección del diseño anterior: buscaba por nombre entre las de
		// domain=null diciendo que comprobaba el origen, y no lo comprobaba.
		// Dos empresas homónimas sin dominio pueden ser dos empresas distintas.
		await db.company.create({ data: { name: "PRUEBA INTEGRACIÓN SpA" } });
		const r = await db.$transaction((tx) => servicio.resolver(tx, payload()));
		expect((r as { plan: { empresa: { accion: string } } }).plan.empresa.accion)
			.toBe("crear");
	});

	test("reusa la empresa cuando hay VÍNCULO de origen, no por nombre", async () => {
		// Lo único que prueba que esa fila la creó esta integración para este
		// contacto es el vínculo, no que el nombre coincida.
		const primera = await db.$transaction(async (tx) => {
			const r = await servicio.resolver(tx, payload());
			return servicio.aplicarIdentidad(tx, payload(), (r as { plan: never }).plan);
		});
		const segunda = await db.$transaction((tx) => servicio.resolver(tx, payload()));
		expect((segunda as { plan: { empresa: { id: string } } }).plan.empresa)
			.toMatchObject({ accion: "usar", id: primera.companyId });
	});

	test("sin dominio y sin nombre de empresa no se planifica ninguna", async () => {
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ empresa: "" })),
		);
		expect((r as { plan: { empresa: { accion: string } } }).plan.empresa.accion)
			.toBe("ninguna");
	});

	test("no se inventa website como si el sitio estuviera verificado", async () => {
		const r = await db.$transaction(async (tx) => {
			const resuelta = await servicio.resolver(tx, payload({
				correo: "ana@sitio-no-verificado-prueba.cl",
			}));
			return servicio.aplicarIdentidad(tx, payload(), (resuelta as { plan: never }).plan);
		});
		const empresa = await db.company.findUniqueOrThrow({
			where: { id: r.companyId! },
		});
		expect(empresa.website).toBeNull();
	});

	test("varios contactos con el mismo teléfono es conflicto, no una adivinanza", async () => {
		await db.contact.create({ data: { firstName: "Ana", phone: "56900000902" } });
		await db.contact.create({ data: { firstName: "Otra", phone: "56900000902" } });
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ telefono: "56900000902" })),
		);
		expect(r.ok).toBe(false);
		expect((r as { codigo: string }).codigo).toBe("telefono_ambiguo");
	});

	test("dos claves de contacto distintas que comparten teléfono no se fusionan", async () => {
		// Carrera que un lock por clave de contacto NO cubre: son claves
		// distintas, así que toman locks distintos y corren en paralelo.
		const uno = payload({ clave_contacto: "b1".repeat(16), telefono: "56900000906" });
		const dos = payload({ clave_contacto: "b2".repeat(16), telefono: "56900000906" });
		const [ra, rb] = await Promise.all([
			db.$transaction(async (tx) => {
				const r = await servicio.resolver(tx, uno);
				return r.ok ? servicio.aplicarIdentidad(tx, uno, r.plan) : r;
			}),
			db.$transaction(async (tx) => {
				const r = await servicio.resolver(tx, dos);
				return r.ok ? servicio.aplicarIdentidad(tx, dos, r.plan) : r;
			}),
		]);
		// Resultado determinista: o comparten el contacto, o una de las dos es
		// conflicto. Lo que NO puede pasar es dos contactos con el mismo
		// teléfono creados por esta ingesta.
		expect(await db.contact.count({ where: { phone: "56900000906" } })).toBe(1);
		expect([ra, rb].filter((r) => "contactId" in r).length).toBeGreaterThanOrEqual(1);
	});

	test("teléfono y correo apuntando a contactos distintos es conflicto", async () => {
		await db.contact.create({ data: { firstName: "Ana", phone: "56900000903" } });
		await db.contact.create({
			data: { firstName: "Otra", email: "otra-prueba@example.com" },
		});
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({
				telefono: "56900000903",
				correo: "otra-prueba@example.com",
			})),
		);
		expect(r.ok).toBe(false);
		expect((r as { codigo: string }).codigo).toBe("identidad_dividida");
	});

	test("un contacto hallado por correo NO pierde el teléfono que ya tenía", async () => {
		const existente = await db.contact.create({
			data: {
				firstName: "Ana", email: "ana-fija-prueba@example.com",
				phone: "56911111111",
			},
		});
		const p = payload({
			correo: "ana-fija-prueba@example.com", telefono: "56900000904",
		});
		const r = await db.$transaction(async (tx) => {
			const resuelta = await servicio.resolver(tx, p);
			// Un match sólo por correo con teléfono incompatible es CANDIDATO,
			// no identidad: un correo escrito en una conversación no prueba
			// posesión. Sin regla de identidad autorizada, es conflicto.
			expect(resuelta.ok).toBe(false);
			return resuelta;
		});
		expect((r as { codigo: string }).codigo).toBe("telefono_incompatible");
		const despues = await db.contact.findUniqueOrThrow({ where: { id: existente.id } });
		expect(despues.phone).toBe("56911111111");
		expect(await db.fieldValue.count({ where: { contactId: existente.id } })).toBe(0);
	});

	test("un contacto nuevo se crea con el nombre partido y sin inventar apellido", async () => {
		const p = payload({ nombre_completo: "Ana" });
		const r = await db.$transaction(async (tx) => {
			const resuelta = await servicio.resolver(tx, p);
			return servicio.aplicarIdentidad(tx, p, (resuelta as { plan: never }).plan);
		});
		const contacto = await db.contact.findUniqueOrThrow({ where: { id: r.contactId } });
		expect(contacto.firstName).toBe("Ana");
		expect(contacto.lastName).toBeNull();
	});
});
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-identity.spec.ts
```

Expected: FAIL — no existe el módulo.

- [ ] **Step 3: Escribir el servicio**

```typescript
// apps/api/src/ingest/ingest-identity.service.ts
//
// A qué Company y a qué Contact corresponde un lead del bot.
//
// DOS FASES, y es la corrección más importante de este servicio: `resolver`
// DECIDE sin escribir nada, y `aplicarIdentidad` ejecuta el plan. La versión
// anterior creaba la Company y después podía devolver conflicto de contacto,
// pero la transacción seguía y commiteaba: quedaba una empresa creada por un
// evento que se rechazó. Con el plan separado, un conflicto descubierto al
// final no puede dejar efectos.
//
// Ninguna regla adivina: los casos ambiguos devuelven conflicto con un código
// estable y los resuelve una persona.
import { type Prisma, RecordSource } from "@crm/db";
import { Injectable, Logger } from "@nestjs/common";
import { domainFromEmail } from "../companies/domain";
import { normalizeEmail } from "../crm/values";
import { splitName } from "../mailbox/participants";
import type { LeadInTouchPayload } from "./ingest.contracts";

export type PlanIdentidad = {
	empresa:
		| { accion: "usar"; id: string }
		| { accion: "crear"; nombre: string; dominio: string | null }
		| { accion: "ninguna" };
	contacto: { accion: "usar"; id: string } | { accion: "crear" };
};

export type Resolucion =
	| { ok: true; plan: PlanIdentidad }
	| { ok: false; motivo: string; codigo: string };

@Injectable()
export class IngestIdentityService {
	private readonly logger = new Logger(IngestIdentityService.name);

	/// Decide a qué entidades corresponde el lead. **No escribe.**
	async resolver(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
	): Promise<Resolucion> {
		const correo = normalizeEmail(payload.correo ?? "");

		// 1. El vínculo de origen manda sobre cualquier heurística: si esta
		// integración ya resolvió esta clave de contacto, se reusa. Es lo único
		// que prueba procedencia -- que dos nombres de empresa coincidan, no.
		const vinculo = await tx.leadIngestEvent.findFirst({
			where: {
				origin: payload.origen,
				claveContacto: payload.clave_contacto,
				contactId: { not: null },
			},
			orderBy: { revision: "desc" },
			select: { contactId: true, companyId: true },
		});

		if (vinculo?.contactId) {
			const contacto = await tx.contact.findUnique({
				where: { id: vinculo.contactId },
				select: { id: true, archivedAt: true },
			});
			// Si el contacto fue borrado o archivado, NO se recrea en silencio:
			// alguien decidió sacarlo y un replay no puede resucitarlo.
			if (!contacto || contacto.archivedAt) {
				return {
					ok: false,
					codigo: "vinculo_roto",
					motivo:
						`El contacto ${vinculo.contactId} de este vínculo ya no está activo. ` +
						"No se recrea automáticamente: hace falta decisión humana.",
				};
			}
			const empresa = vinculo.companyId
				? ({ accion: "usar", id: vinculo.companyId } as const)
				: await this.planificarEmpresa(tx, payload, correo);
			return { ok: true, plan: { empresa, contacto: { accion: "usar", id: contacto.id } } };
		}

		// 2. Origen nuevo: buscar candidatos, sin fusionar nada por su cuenta.
		const contacto = await this.planificarContacto(tx, payload, correo);
		if (!contacto.ok) return contacto;

		const empresa = await this.planificarEmpresa(tx, payload, correo);
		return { ok: true, plan: { empresa, contacto: contacto.valor } };
	}

	private async planificarEmpresa(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		correo: string | null,
	): Promise<PlanIdentidad["empresa"]> {
		const dominio = correo ? domainFromEmail(correo) : null;

		// Un dominio corporativo es la única identificación razonable, y aun así
		// no prueba identidad legal: agencias y grupos comparten dominio. Se
		// reusa una Company existente sólo por coincidencia exacta de dominio.
		if (dominio) {
			const existente = await tx.company.findFirst({
				where: { domain: dominio, archivedAt: null },
				select: { id: true },
			});
			if (existente) return { accion: "usar", id: existente.id };
			return { accion: "crear", nombre: payload.empresa || dominio, dominio };
		}

		// SIN dominio NO se busca por nombre -- ni entre las que también tienen
		// `domain: null`. Dos empresas homónimas pueden ser dos empresas, y el
		// nombre lo declaró un contacto por WhatsApp. Si más adelante resulta
		// que son la misma, la fusiona una persona; separarlas después es
		// posible, desfusionar no.
		if (!payload.empresa) return { accion: "ninguna" };
		return { accion: "crear", nombre: payload.empresa, dominio: null };
	}

	private async planificarContacto(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		correo: string | null,
	): Promise<{ ok: true; valor: PlanIdentidad["contacto"] } | Resolucion> {
		// `Contact.phone` no tiene índice único (el único es
		// `@@unique([email]) where archivedAt: null`), así que puede haber varios.
		const porTelefono = await tx.contact.findMany({
			where: { phone: payload.telefono, archivedAt: null },
			select: { id: true },
			take: 2,
		});
		if (porTelefono.length > 1) {
			return {
				ok: false,
				codigo: "telefono_ambiguo",
				motivo:
					`Hay más de un contacto con el teléfono ${payload.telefono}. ` +
					"No se elige uno por adivinanza.",
			};
		}

		const porCorreo = correo
			? await tx.contact.findFirst({
					where: { email: correo, archivedAt: null },
					select: { id: true, phone: true },
				})
			: null;
		const unoPorTelefono = porTelefono[0] ?? null;

		if (unoPorTelefono && porCorreo && unoPorTelefono.id !== porCorreo.id) {
			return {
				ok: false,
				codigo: "identidad_dividida",
				motivo:
					"El teléfono y el correo apuntan a contactos distintos. Son dos " +
					"personas o un dato mal cargado; fusionarlos borraría a una.",
			};
		}

		if (unoPorTelefono) return { ok: true, valor: { accion: "usar", id: unoPorTelefono.id } };

		if (porCorreo) {
			// Un correo escrito en una conversación NO prueba que la persona sea
			// la dueña de ese contacto ni que trabaje ahí. Si el contacto hallado
			// por correo ya tiene OTRO teléfono, vincular sería atribuirle a un
			// tercero una conversación que no tuvo.
			if (porCorreo.phone && porCorreo.phone !== payload.telefono) {
				return {
					ok: false,
					codigo: "telefono_incompatible",
					motivo:
						`El contacto con ese correo ya tiene el teléfono ${porCorreo.phone}. ` +
						"Vincularlo atribuiría esta conversación a otra persona.",
				};
			}
			return { ok: true, valor: { accion: "usar", id: porCorreo.id } };
		}

		return { ok: true, valor: { accion: "crear" } };
	}

	/// Ejecuta un plan ya aceptado. Se llama **después** de que la resolución
	/// completa dio ok, así que no puede dejar efectos de un evento rechazado.
	async aplicarIdentidad(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		plan: PlanIdentidad,
	): Promise<{ companyId: string | null; contactId: string }> {
		let companyId: string | null = null;
		if (plan.empresa.accion === "usar") {
			companyId = plan.empresa.id;
		} else if (plan.empresa.accion === "crear") {
			const creada = await tx.company.create({
				data: {
					name: plan.empresa.nombre,
					domain: plan.empresa.dominio,
					// NO se inventa `website`: derivarlo del dominio lo presentaría
					// como un sitio verificado, y nadie lo verificó.
					industry: payload.industria || null,
					subIndustry: payload.subtipo_automotriz || null,
				},
				select: { id: true },
			});
			companyId = creada.id;
		}

		if (plan.contacto.accion === "usar") {
			// Los nativos se llenan sólo si están vacíos: no se pisa lo que un
			// comercial corrigió a mano. `phone` y `companyId` NO se tocan --
			// una observación del bot no reemplaza un dato verificado.
			const actual = await tx.contact.findUniqueOrThrow({
				where: { id: plan.contacto.id },
				select: { title: true, email: true, companyId: true },
			});
			await tx.contact.update({
				where: { id: plan.contacto.id },
				data: {
					title: actual.title ? undefined : payload.cargo || undefined,
					email: actual.email ? undefined : normalizeEmail(payload.correo ?? "") ?? undefined,
					companyId: actual.companyId ? undefined : companyId ?? undefined,
				},
			});
			return { companyId: actual.companyId ?? companyId, contactId: plan.contacto.id };
		}

		const nombre = splitName(
			payload.nombre_completo ?? null,
			normalizeEmail(payload.correo ?? "") ?? "",
		);
		const creado = await tx.contact.create({
			data: {
				firstName: nombre.firstName,
				// `splitName` devuelve null cuando no hay apellido: no se inventa.
				lastName: nombre.lastName,
				email: normalizeEmail(payload.correo ?? ""),
				phone: payload.telefono,
				title: payload.cargo || null,
				companyId,
				source: RecordSource.IMPORT,
			},
			select: { id: true },
		});
		return { companyId, contactId: creado.id };
	}
}
```

- [ ] **Step 4: Correr los tests**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools \
  bun test apps/api/test/ingest-identity.spec.ts
```

Expected: PASS, los 13. Contra **Postgres real** — con SQLite no se puede
afirmar nada sobre restricciones ni carreras.

- [ ] **Step 5: Verificar que la suite no dejó basura**

```bash
cd /home/admincrm/compai-crm && git status --short
```

Expected: sólo los archivos que se van a commitear. Un test que deja el repo sucio no se acepta.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/ingest/ingest-identity.service.ts \
        apps/api/test/ingest-identity.spec.ts
git commit -m "feat(ingest): resolucion de identidad en dos fases, sin efectos parciales

resolver() DECIDE sin escribir y aplicarIdentidad() ejecuta el plan. Antes se
creaba la Company y despues podia devolver conflicto de contacto, pero la
transaccion commiteaba igual: quedaba una empresa creada por un evento
rechazado.

El vinculo de origen manda sobre toda heuristica. Sin dominio NO se fusiona
por nombre, tampoco entre empresas sin dominio: dos homonimas pueden ser dos
empresas, y desfusionar no se puede. No se inventa website desde el dominio.

Cuatro codigos de conflicto en vez de elegir: telefono_ambiguo,
identidad_dividida, telefono_incompatible y vinculo_roto. Un correo escrito
en una conversacion no prueba posesion, asi que un match por correo con
telefono incompatible tampoco vincula."
```

---

### Task 7: La escritura comercial

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest-write.service.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-write.spec.ts`

**Interfaces:**
- Consumes: el resultado de `aplicarIdentidad` (Task 6); `CAMPOS_INTOUCH` (Task 4); `LeadInTouchPayload` (Task 5).
- Produces: `IngestWriteService` con `escribir(tx, payload, identidad, ownerId): Promise<{ dealId: string | null }>` donde `identidad: { companyId: string | null; contactId: string }`, y las funciones puras `debeAbrirOportunidad(payload): boolean`, `textoDeLista(valores): string` y `claveDeEfecto(payload, tipo): string`.

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-write.spec.ts
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { ActivityType, DealStage, db } from "@crm/db";
import { sembrarCamposInTouch } from "../src/ingest/ingest-fields";
import {
	debeAbrirOportunidad, IngestWriteService, textoDeLista,
} from "../src/ingest/ingest-write.service";

const servicio = new IngestWriteService();

async function duenoDePrueba(): Promise<string> {
	const u = await db.user.upsert({
		where: { email: "dueno-prueba@example.com" },
		update: {},
		create: {
			id: crypto.randomUUID(), name: "Dueño de prueba",
			email: "dueno-prueba@example.com", emailVerified: true,
		},
		select: { id: true },
	});
	return u.id;
}

function payload(over: Record<string, unknown> = {}) {
	return {
		origen: "wsp_intouch" as const,
		clave_contacto: "c".repeat(32),
		evento_id: crypto.randomUUID(),
		revision: 1,
		telefono: "56900000910",
		nombre_completo: "PRUEBA INTEGRACIÓN Ana",
		empresa: "PRUEBA INTEGRACIÓN SpA",
		necesidad_principal: "Automatizar la atención de postventa",
		lead_score: "WARM" as const,
		solicita_consultoria: false,
		solicita_contacto_humano: false,
		...over,
	};
}

describe("política de apertura de oportunidad", () => {
	test("HOT y WARM abren", () => {
		expect(debeAbrirOportunidad(payload({ lead_score: "HOT" }))).toBe(true);
		expect(debeAbrirOportunidad(payload({ lead_score: "WARM" }))).toBe(true);
	});

	test("COLD y NO_CALIFICADO no abren", () => {
		expect(debeAbrirOportunidad(payload({ lead_score: "COLD" }))).toBe(false);
		expect(debeAbrirOportunidad(payload({ lead_score: "NO_CALIFICADO" }))).toBe(false);
	});

	test("una solicitud explícita abre aunque el score sea frío", () => {
		// Pedir hablar con una persona es un hecho comercial, no una temperatura.
		expect(debeAbrirOportunidad(
			payload({ lead_score: "COLD", solicita_contacto_humano: true }),
		)).toBe(true);
		expect(debeAbrirOportunidad(
			payload({ lead_score: "NO_CALIFICADO", solicita_consultoria: true }),
		)).toBe(true);
	});
});

describe("texto derivado de una lista", () => {
	test("une los elementos para presentación", () => {
		expect(textoDeLista(["whatsapp", "correo"])).toBe("whatsapp, correo");
	});

	test("una lista vacía da texto vacío", () => {
		expect(textoDeLista([])).toBe("");
		expect(textoDeLista(undefined)).toBe("");
	});
});

describe("escritura comercial", () => {
	let ownerId: string;

	beforeEach(async () => {
		ownerId = await duenoDePrueba();
		await sembrarCamposInTouch(db);
	});

	async function corrida(over: Record<string, unknown> = {}) {
		const p = payload(over);
		return db.$transaction(async (tx) => {
			const empresa = await tx.company.create({
				data: { name: "PRUEBA INTEGRACIÓN SpA" }, select: { id: true },
			});
			const contacto = await tx.contact.create({
				data: { firstName: "PRUEBA", lastName: "Ana", companyId: empresa.id },
				select: { id: true },
			});
			const r = await servicio.escribir(
				tx, p,
				{ ok: true, companyId: empresa.id, contactId: contacto.id },
				ownerId,
			);
			return { ...r, contactId: contacto.id, companyId: empresa.id };
		});
	}

	test("la etapa inicial es QUALIFIED_TO_BUY, nunca DEMO_BOOKED", async () => {
		// DEMO_BOOKED afirmaría una demo agendada que nunca ocurrió -- y es el
		// default del schema, así que hay que fijarla explícitamente.
		const r = await corrida();
		const deal = await db.deal.findUniqueOrThrow({ where: { id: r.dealId! } });
		expect(deal.stage).toBe(DealStage.QUALIFIED_TO_BUY);
	});

	test("un lead COLD no abre oportunidad pero sí conserva la necesidad", async () => {
		const r = await corrida({ lead_score: "COLD", telefono: "56900000911" });
		expect(r.dealId).toBeNull();
		const valor = await db.fieldValue.findFirst({
			where: { contactId: r.contactId, field: { key: "necesidad_principal" } },
		});
		expect(valor?.text).toBe("Automatizar la atención de postventa");
	});

	test("los arrays quedan como texto derivado en su campo", async () => {
		const r = await corrida({
			canales_actuales: ["whatsapp", "correo"], telefono: "56900000912",
		});
		const valor = await db.fieldValue.findFirst({
			where: { contactId: r.contactId, field: { key: "canales_actuales" } },
		});
		expect(valor?.text).toBe("whatsapp, correo");
	});

	test("usa_ia_actualmente en false se escribe; ausente no se escribe", async () => {
		const conFalse = await corrida({
			usa_ia_actualmente: false, telefono: "56900000913",
		});
		const escrito = await db.fieldValue.findFirst({
			where: { contactId: conFalse.contactId, field: { key: "usa_ia_actualmente" } },
		});
		expect(escrito?.bool).toBe(false);

		const sinDato = await corrida({ telefono: "56900000914" });
		const ausente = await db.fieldValue.findFirst({
			where: { contactId: sinDato.contactId, field: { key: "usa_ia_actualmente" } },
		});
		expect(ausente).toBeNull();
	});

	test("el resumen queda como NOTE en el timeline", async () => {
		const r = await corrida({
			resumen_conversacion: "Pidió precios de la solución de voz.",
			telefono: "56900000915",
		});
		const nota = await db.activity.findFirst({
			where: { contactId: r.contactId, type: ActivityType.NOTE },
		});
		expect(nota?.body).toContain("Pidió precios");
	});

	test("solicita_contacto_humano deja una TASK con vencimiento", async () => {
		// Lo que vuelve accionable la petición, en vez de un booleano olvidado.
		const r = await corrida({
			solicita_contacto_humano: true, telefono: "56900000916",
		});
		const tarea = await db.activity.findFirst({
			where: { contactId: r.contactId, type: ActivityType.TASK },
		});
		expect(tarea).not.toBeNull();
		expect(tarea?.dueAt).not.toBeNull();
		expect(tarea?.completedAt).toBeNull();
	});

	test("solicita_consultoria TAMBIÉN deja seguimiento accionable", async () => {
		// Estaba omitido en el diseño anterior: pedir una consultoría es una
		// solicitud explícita igual que pedir hablar con alguien, y quedaba
		// sólo como un booleano en un campo.
		const r = await corrida({
			solicita_consultoria: true, telefono: "56900000919",
		});
		const tarea = await db.activity.findFirst({
			where: { contactId: r.contactId, type: ActivityType.TASK },
		});
		expect(tarea).not.toBeNull();
		expect(tarea?.subject).toContain("consultoría");
	});

	test("el mismo true repetido en otro snapshot NO crea otra tarea", async () => {
		// El emisor manda el objeto completo en cada revisión, así que
		// `solicita_contacto_humano: true` vuelve a llegar en todas. Sin clave
		// de efecto, el comercial recibe una tarea nueva por cada mensaje.
		const p = { solicita_contacto_humano: true, telefono: "56900000920" };
		const primera = await corrida(p);
		await corrida({ ...p, evento_id: crypto.randomUUID(), revision: 2 });
		expect(await db.activity.count({
			where: { contactId: primera.contactId, type: ActivityType.TASK },
		})).toBe(1);
	});

	test("una tarea ya completada no se reabre ni se duplica", async () => {
		const p = { solicita_contacto_humano: true, telefono: "56900000921" };
		const primera = await corrida(p);
		const tarea = await db.activity.findFirstOrThrow({
			where: { contactId: primera.contactId, type: ActivityType.TASK },
		});
		await db.activity.update({
			where: { id: tarea.id }, data: { completedAt: new Date() },
		});
		await corrida({ ...p, evento_id: crypto.randomUUID(), revision: 2 });
		const despues = await db.activity.findUniqueOrThrow({ where: { id: tarea.id } });
		expect(despues.completedAt).not.toBeNull();
		expect(await db.activity.count({
			where: { contactId: primera.contactId, type: ActivityType.TASK },
		})).toBe(1);
	});

	test("una nota idéntica no se repite sin información nueva", async () => {
		const p = {
			resumen_conversacion: "Pidió precios.", telefono: "56900000922",
		};
		const primera = await corrida(p);
		await corrida({ ...p, evento_id: crypto.randomUUID(), revision: 2 });
		expect(await db.activity.count({
			where: { contactId: primera.contactId, type: ActivityType.NOTE },
		})).toBe(1);
	});

	test("no deja TASK cuando nadie pidió hablar con una persona", async () => {
		const r = await corrida({ telefono: "56900000917" });
		const tarea = await db.activity.findFirst({
			where: { contactId: r.contactId, type: ActivityType.TASK },
		});
		expect(tarea).toBeNull();
	});

	test("el dueño de la oportunidad es el configurado, no algo del body", async () => {
		const r = await corrida({ telefono: "56900000918" });
		const deal = await db.deal.findUniqueOrThrow({ where: { id: r.dealId! } });
		expect(deal.ownerId).toBe(ownerId);
	});
});
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-write.spec.ts
```

Expected: FAIL — no existe el módulo.

- [ ] **Step 3: Escribir el servicio**

```typescript
// apps/api/src/ingest/ingest-write.service.ts
//
// Qué se escribe en el CRM cuando llega un lead del bot.
//
// La regla que ordena todo: no se afirma un hecho que no ocurrió. Por eso la
// etapa inicial es QUALIFIED_TO_BUY y no DEMO_BOOKED (el bot no agenda nada),
// y por eso `lead_score` viaja como campo y no como etapa.
import {
	ActivityType, DealStage, FieldEntity, type Prisma,
} from "@crm/db";
import { Injectable, Logger } from "@nestjs/common";
import { CAMPOS_INTOUCH } from "./ingest-fields";
import type { LeadInTouchPayload } from "./ingest.contracts";


/// Días que se le dan a la tarea de contactar cuando alguien pidió hablar con
/// una persona. Corto a propósito: un lead que pidió contacto humano y espera
/// una semana ya se perdió.
const DIAS_PARA_CONTACTAR = 1;

/// Identifica un efecto que debe ocurrir UNA vez por contacto, aunque el dato
/// que lo provoca vuelva a llegar en cada revisión.
///
/// El emisor manda el objeto completo en cada snapshot, así que
/// `solicita_contacto_humano: true` reaparece en todas. Sin esta clave, el
/// comercial recibe una tarea nueva por cada mensaje que escriba el contacto
/// -- la forma más rápida de que apaguen las notificaciones.
export function claveDeEfecto(payload: LeadInTouchPayload, tipo: string): string {
	return `${payload.origen}:${payload.clave_contacto}:${tipo}`;
}

/// Cuándo el lead justifica abrir una oportunidad en el pipeline.
///
/// COLD y NO_CALIFICADO NO abren: entran como Company + Contact, se ven en el
/// listado, y no ensucian el embudo ni sus métricas. Pero una solicitud
/// explícita abre igual, porque pedir consultoría o pedir hablar con una
/// persona es un hecho comercial y no una temperatura.
export function debeAbrirOportunidad(payload: LeadInTouchPayload): boolean {
	if (payload.solicita_contacto_humano || payload.solicita_consultoria) return true;
	return payload.lead_score === "HOT" || payload.lead_score === "WARM";
}

/// El texto de presentación de una lista. DERIVADO: la lista original se
/// conserva estructurada en `LeadIngestEvent.payload`, que es de donde hay que
/// leerla si se necesitan los elementos. Unir por comas no es reversible -- un
/// elemento que contenga una coma no se puede volver a separar.
export function textoDeLista(valores: readonly string[] | undefined): string {
	return (valores ?? []).join(", ");
}

type ValorCampo = { text?: string; bool?: boolean };

@Injectable()
export class IngestWriteService {
	private readonly logger = new Logger(IngestWriteService.name);

	async escribir(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		identidad: { companyId: string | null; contactId: string },
		ownerId: string,
	): Promise<{ dealId: string | null }> {
		const dealId = debeAbrirOportunidad(payload)
			? await this.oportunidad(tx, payload, identidad, ownerId)
			: null;

		await this.campos(tx, payload, identidad.contactId, dealId);
		await this.timeline(tx, payload, identidad.contactId, dealId, ownerId);

		return { dealId };
	}

	private async oportunidad(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		identidad: { companyId: string | null; contactId: string },
		ownerId: string,
	): Promise<string | null> {
		// Deal.companyId es obligatorio: sin empresa no hay oportunidad que abrir.
		if (!identidad.companyId) {
			this.logger.warn({
				message: "Lead sin empresa: no se abre oportunidad",
				contactId: identidad.contactId,
			});
			return null;
		}

		const existente = await tx.deal.findFirst({
			where: {
				companyId: identidad.companyId,
				archivedAt: null,
				contacts: { some: { contactId: identidad.contactId } },
			},
			select: { id: true },
		});
		// Una oportunidad ya abierta NO se reabre ni se le mueve la etapa: ningún
		// evento del bot devuelve un CLOSED_WON a QUALIFIED_TO_BUY.
		if (existente) return existente.id;

		const deal = await tx.deal.create({
			data: {
				name: `${payload.empresa || "Contacto de WhatsApp"} — Asesor Comercial IA`,
				description: payload.necesidad_principal || null,
				companyId: identidad.companyId,
				ownerId,
				// Explícita a propósito: el default del schema es DEMO_BOOKED, que
				// afirmaría una demo agendada que el bot nunca agendó.
				//
				// QUALIFIED_TO_BUY es la menos incorrecta del enum instalado, no
				// la correcta: afirma que alguien calificó al contacto como apto
				// para comprar, y lo que pasó es que un bot recogió antecedentes.
				// Se eligió DELIBERADAMENTE y queda anotado en RUNBOOK.md; si el
				// equipo comercial define una etapa de entrada, se cambia acá y
				// en el test que la ancla.
				stage: DealStage.QUALIFIED_TO_BUY,
				contacts: { create: { contactId: identidad.contactId } },
			},
			select: { id: true },
		});
		return deal.id;
	}

	/// Los campos dinámicos. Los del bot se sobreescriben; un campo ausente
	/// NUNCA borra un valor existente (spec §4).
	private async campos(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		contactId: string,
		dealId: string | null,
	): Promise<void> {
		const valores: Record<string, ValorCampo | undefined> = {
			pais_ciudad: this.texto(payload.pais_ciudad),
			necesidad_principal: this.texto(payload.necesidad_principal),
			situacion_contact_center: this.texto(payload.situacion_contact_center),
			tipo_contact_center: this.texto(payload.tipo_contact_center),
			usa_ia_actualmente:
				payload.usa_ia_actualmente === null ||
				payload.usa_ia_actualmente === undefined
					? undefined
					: { bool: payload.usa_ia_actualmente },
			canales_actuales: this.texto(textoDeLista(payload.canales_actuales)),
			volumen_interacciones: this.texto(payload.volumen_interacciones),
			telefono_whatsapp: this.texto(payload.telefono),
			origen: this.texto(payload.origen),
			clave_contacto: this.texto(payload.clave_contacto),
			lead_score: this.texto(payload.lead_score),
			soluciones_interes: this.texto(textoDeLista(payload.soluciones_interes)),
			plazo_proyecto: this.texto(payload.plazo_proyecto),
			intencion: this.texto(payload.intencion),
		};

		for (const campo of CAMPOS_INTOUCH) {
			const valor = valores[campo.key];
			if (!valor) continue;

			const definicion = await tx.fieldDefinition.findUnique({
				where: { entity_key: { entity: campo.entity, key: campo.key } },
				select: { id: true },
			});
			if (!definicion) {
				// La semilla de la Task 4 no corrió. Se avisa y se sigue: el resto
				// del lead vale más que este campo.
				this.logger.warn({
					message: "Falta la definición de un campo de InTouch",
					key: campo.key,
				});
				continue;
			}

			if (campo.entity === FieldEntity.CONTACT) {
				await tx.fieldValue.upsert({
					where: { fieldId_contactId: { fieldId: definicion.id, contactId } },
					update: valor,
					create: { fieldId: definicion.id, contactId, ...valor },
				});
			} else if (campo.entity === FieldEntity.DEAL && dealId) {
				await tx.fieldValue.upsert({
					where: { fieldId_dealId: { fieldId: definicion.id, dealId } },
					update: valor,
					create: { fieldId: definicion.id, dealId, ...valor },
				});
			}
		}
	}

	/// Un valor de texto sólo si hay algo que escribir. Vacío devuelve
	/// `undefined` para que el llamador no lo escriba y no pise lo que había.
	private texto(valor: string | undefined): ValorCampo | undefined {
		const limpio = (valor ?? "").trim();
		return limpio ? { text: limpio } : undefined;
	}

	/// El timeline. El resumen va como NOTE porque es el lugar natural para
	/// texto libre no confiable y le da historia al comercial; la petición de
	/// contacto humano va además como TASK con vencimiento, que es lo que la
	/// hace accionable.
	private async timeline(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		contactId: string,
		dealId: string | null,
		ownerId: string,
	): Promise<void> {
		const partes = [
			payload.resumen_conversacion?.trim(),
			payload.siguiente_accion_recomendada?.trim()
				? `Siguiente acción sugerida por el bot: ${payload.siguiente_accion_recomendada.trim()}`
				: null,
		].filter(Boolean);

		if (partes.length > 0) {
			const cuerpo = partes.join("\n\n");
			// Una nota idéntica no aporta: el extractor reescribe el resumen en
			// casi cada turno, y el timeline se vuelve ilegible.
			const repetida = await tx.activity.findFirst({
				where: { contactId, type: ActivityType.NOTE, body: cuerpo },
				select: { id: true },
			});
			if (repetida) return;

			await tx.activity.create({
				data: {
					type: ActivityType.NOTE,
					subject: `Conversación con el Asesor Comercial IA (${payload.lead_score || "sin calificar"})`,
					// Texto del contacto y del modelo: se guarda como texto y nunca
					// se renderiza como HTML.
					body: cuerpo,
					occurredAt: new Date(),
					contactId,
					dealId,
					createdById: ownerId,
					meta: { origen: payload.origen, revision: payload.revision },
				},
			});
		}

		// Las DOS solicitudes explícitas generan seguimiento. La consultoría
		// estaba omitida en el diseño anterior y quedaba sólo como un booleano
		// en un campo, que es exactamente lo que el encargo prohíbe.
		const solicitudes: Array<{ tipo: string; asunto: string }> = [];
		if (payload.solicita_contacto_humano) {
			solicitudes.push({
				tipo: "contacto_humano",
				asunto: "El contacto pidió hablar con una persona",
			});
		}
		if (payload.solicita_consultoria) {
			solicitudes.push({
				tipo: "consultoria",
				asunto: "El contacto pidió una consultoría",
			});
		}

		for (const solicitud of solicitudes) {
			const clave = claveDeEfecto(payload, solicitud.tipo);
			// Idempotente por clave de efecto, incluyendo las ya completadas: una
			// tarea que el comercial cerró NO se vuelve a abrir porque el mismo
			// true llegó en otro snapshot.
			const yaExiste = await tx.activity.findFirst({
				where: { contactId, type: ActivityType.TASK, meta: { path: ["clave"], equals: clave } },
				select: { id: true },
			});
			if (yaExiste) continue;

			const vence = new Date();
			vence.setDate(vence.getDate() + DIAS_PARA_CONTACTAR);
			await tx.activity.create({
				data: {
					type: ActivityType.TASK,
					subject: solicitud.asunto,
					body:
						"Pedido explícito durante la conversación con el bot. " +
						`Teléfono de WhatsApp: ${payload.telefono}.`,
					dueAt: vence,
					contactId,
					dealId,
					// `createdById` es el AUTOR técnico, no el asignatario: Activity
					// no tiene campo de asignación. Quien la trabaja es el dueño de
					// la oportunidad, y por eso el dueño se resuelve de configuración.
					createdById: ownerId,
					meta: {
						origen: payload.origen, revision: payload.revision, clave,
					},
				},
			});
		}
	}
}
```

- [ ] **Step 4: Correr los tests**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools \
  bun test apps/api/test/ingest-write.spec.ts
```

Expected: PASS, los 18.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/ingest/ingest-write.service.ts \
        apps/api/test/ingest-write.spec.ts
git commit -m "feat(ingest): escritura comercial con etapa coherente con los hechos

Etapa inicial QUALIFIED_TO_BUY y explicita: el default del schema es
DEMO_BOOKED, que afirmaria una demo agendada que el bot nunca agendo.
lead_score viaja como campo, no como etapa.

COLD y NO_CALIFICADO no abren oportunidad pero conservan la necesidad en
un campo de Contact. Una solicitud explicita abre igual, porque pedir
hablar con una persona es un hecho comercial y no una temperatura, y deja
una TASK con vencimiento -- no un booleano olvidado."
```

---

### Task 8: Idempotencia, orden y conflictos

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest.service.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-idempotency.spec.ts`

**Interfaces:**
- Consumes: `IngestIdentityService` (Task 6), `IngestWriteService` (Task 7), `leadInTouchSchema` (Task 5), `lockIdempotencyKey` de `@crm/db/idempotency`.
- Produces: `IngestService` con `ingerir(payload, ownerId): Promise<ResultadoIngesta>`, y `type ResultadoIngesta = { estado: "created" | "replayed" | "updated"; companyId: string | null; contactId: string; dealId: string | null; revision: number; eventoId: string } | { estado: "conflict" | "stale"; motivo: string }`.

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-idempotency.spec.ts
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { db } from "@crm/db";
import { IngestIdentityService } from "../src/ingest/ingest-identity.service";
import { IngestService } from "../src/ingest/ingest.service";
import { IngestWriteService } from "../src/ingest/ingest-write.service";
import { sembrarCamposInTouch } from "../src/ingest/ingest-fields";

const servicio = new IngestService(
	db, new IngestIdentityService(), new IngestWriteService(),
);

let ownerId: string;

function payload(over: Record<string, unknown> = {}) {
	return {
		origen: "wsp_intouch" as const,
		clave_contacto: "d".repeat(32),
		evento_id: crypto.randomUUID(),
		revision: 1,
		telefono: "56900000920",
		nombre_completo: "PRUEBA INTEGRACIÓN Ana",
		empresa: "PRUEBA INTEGRACIÓN Idem SpA",
		correo: "ana-idem@empresa-idem-prueba.cl",
		lead_score: "WARM" as const,
		solicita_consultoria: false,
		solicita_contacto_humano: false,
		...over,
	};
}

describe("idempotencia de la ingesta", () => {
	beforeEach(async () => {
		const u = await db.user.upsert({
			where: { email: "dueno-idem@example.com" },
			update: {},
			create: {
				id: crypto.randomUUID(), name: "Dueño idem",
				email: "dueno-idem@example.com", emailVerified: true,
			},
			select: { id: true },
		});
		ownerId = u.id;
		await sembrarCamposInTouch(db);
		// Sólo lo de ESTA clave de contacto, que es propia de la suite.
		await db.leadIngestEvent.deleteMany({ where: { claveContacto: "d".repeat(32) } });
	});

	// Igual que en el spec de identidad: se borra por id, nunca por prefijo.
	afterEach(async () => {
		const eventos = await db.leadIngestEvent.findMany({
			where: { claveContacto: { in: ["d".repeat(32), "e".repeat(32), "e1".repeat(16), "f1".repeat(16)] } },
			select: { contactId: true, companyId: true, dealId: true },
		});
		for (const e of eventos) {
			if (e.dealId) await db.deal.delete({ where: { id: e.dealId } }).catch(() => {});
			if (e.contactId) await db.contact.delete({ where: { id: e.contactId } }).catch(() => {});
			if (e.companyId) await db.company.delete({ where: { id: e.companyId } }).catch(() => {});
		}
		await db.leadIngestEvent.deleteMany({
			where: { claveContacto: { in: ["d".repeat(32), "e".repeat(32), "e1".repeat(16), "f1".repeat(16)] } },
		});
	});

	test("el primer envío crea", async () => {
		const r = await servicio.ingerir(payload(), ownerId);
		expect(r.estado).toBe("created");
	});

	test("el mismo evento repetido es replay: mismos ids, cero escrituras nuevas", async () => {
		const p = payload();
		const primero = await servicio.ingerir(p, ownerId);
		const segundo = await servicio.ingerir(p, ownerId);

		expect(segundo.estado).toBe("replayed");
		expect(segundo).toMatchObject({
			contactId: (primero as { contactId: string }).contactId,
			dealId: (primero as { dealId: string | null }).dealId,
		});
		expect(await db.deal.count({
			where: { company: { name: "PRUEBA INTEGRACIÓN Idem SpA" } },
		})).toBe(1);
	});

	test("N envíos concurrentes del mismo evento dejan un solo efecto", async () => {
		// Es la prueba de que el advisory lock y las restricciones únicas
		// resuelven la carrera real, no una intención escrita en un comentario.
		const p = payload();
		const resultados = await Promise.all(
			Array.from({ length: 5 }, () => servicio.ingerir(p, ownerId)),
		);
		const creados = resultados.filter((r) => r.estado === "created");
		expect(creados.length).toBe(1);
		expect(await db.contact.count({ where: { phone: "56900000920" } })).toBe(1);
		expect(await db.deal.count({
			where: { company: { name: "PRUEBA INTEGRACIÓN Idem SpA" } },
		})).toBe(1);
	});

	test("el mismo evento_id con payload distinto es conflicto, sin sobrescritura", async () => {
		const p = payload();
		await servicio.ingerir(p, ownerId);
		const r = await servicio.ingerir({ ...p, empresa: "Otra SpA" }, ownerId);

		expect(r.estado).toBe("conflict");
		const empresa = await db.company.findFirst({
			where: { name: "Otra SpA" }, select: { id: true },
		});
		expect(empresa).toBeNull();
	});

	test("un evento nuevo con revisión mayor actualiza", async () => {
		await servicio.ingerir(payload(), ownerId);
		const r = await servicio.ingerir(
			payload({
				evento_id: crypto.randomUUID(), revision: 2, lead_score: "HOT",
				cargo: "Gerente de Operaciones",
			}),
			ownerId,
		);
		expect(r.estado).toBe("updated");
		expect((r as { revision: number }).revision).toBe(2);
	});

	test("una revisión vieja llegando después NO pisa a la nueva", async () => {
		await servicio.ingerir(payload({ revision: 1 }), ownerId);
		await servicio.ingerir(
			payload({ evento_id: crypto.randomUUID(), revision: 5, cargo: "Gerenta" }),
			ownerId,
		);
		const tarde = await servicio.ingerir(
			payload({ evento_id: crypto.randomUUID(), revision: 3, cargo: "Analista" }),
			ownerId,
		);

		expect(tarde.estado).toBe("stale");
		const contacto = await db.contact.findFirstOrThrow({
			where: { phone: "56900000920" },
		});
		expect(contacto.title).toBe("Gerenta");
	});

	test("una actualización no reinicia la etapa de la oportunidad", async () => {
		const primero = await servicio.ingerir(payload(), ownerId);
		await db.deal.update({
			where: { id: (primero as { dealId: string }).dealId },
			data: { stage: "CLOSED_WON" },
		});
		await servicio.ingerir(
			payload({ evento_id: crypto.randomUUID(), revision: 2 }), ownerId,
		);
		const deal = await db.deal.findUniqueOrThrow({
			where: { id: (primero as { dealId: string }).dealId },
		});
		expect(deal.stage).toBe("CLOSED_WON");
	});

	test("repetir un evento que quedó en CONFLICTO sigue siendo conflicto", async () => {
		// El bug del diseño anterior: el replay se decidía sólo comparando el
		// hash, y un evento en conflicto se guarda CON su hash. Así que el
		// reintento devolvía `status: "replayed"` con `contactId: ""` -- un
		// éxito falso con un id vacío.
		await db.contact.create({ data: { firstName: "A", phone: "56900000925" } });
		await db.contact.create({ data: { firstName: "B", phone: "56900000925" } });
		const p = payload({ telefono: "56900000925", clave_contacto: "e1".repeat(16) });

		const primero = await servicio.ingerir(p, ownerId);
		expect(primero.estado).toBe("conflict");

		const segundo = await servicio.ingerir(p, ownerId);
		expect(segundo.estado).toBe("conflict");
		expect(JSON.stringify(segundo)).not.toContain('"contactId":""');
	});

	test("ningún resultado exitoso lleva contactId vacío", async () => {
		const r = await servicio.ingerir(payload(), ownerId);
		if (r.estado === "created" || r.estado === "replayed" || r.estado === "updated") {
			expect(r.contactId).toBeTruthy();
		}
	});

	test("el mismo evento_id con OTRA revisión es conflicto, aunque el hash coincida", async () => {
		// El evento identifica un intento: si vuelve con otra revisión, el
		// emisor está diciendo algo distinto de lo que dijo, y eso no es un
		// reintento aunque el contenido de negocio sea idéntico.
		const p = payload();
		await servicio.ingerir(p, ownerId);
		const r = await servicio.ingerir({ ...p, revision: 7 }, ownerId);
		expect(r.estado).toBe("conflict");
	});

	test("el cursor avanza sólo con revisiones APLICADAS, no con conflictos", async () => {
		// Un conflicto con revisión alta no puede bloquear una revisión válida
		// más baja que llegue después: el cursor es "última aplicada".
		await servicio.ingerir(payload({ revision: 1 }), ownerId);
		await db.contact.create({ data: { firstName: "X", phone: "56900000926" } });
		await db.contact.create({ data: { firstName: "Y", phone: "56900000926" } });
		// Revisión 9, pero en conflicto: NO debe mover el cursor.
		const conflictivo = await servicio.ingerir(
			payload({ evento_id: crypto.randomUUID(), revision: 9, telefono: "56900000926" }),
			ownerId,
		);
		expect(conflictivo.estado).toBe("conflict");
		// Revisión 2, válida: tiene que aplicarse.
		const valido = await servicio.ingerir(
			payload({ evento_id: crypto.randomUUID(), revision: 2, cargo: "Gerenta" }),
			ownerId,
		);
		expect(valido.estado).toBe("updated");
	});

	test("un fallo escribiendo deja cero efectos y el cursor sin avanzar", async () => {
		const antesContactos = await db.contact.count();
		const antesEventos = await db.leadIngestEvent.count();
		const romper = { ...servicio } as unknown as { escritura: { escribir: unknown } };
		await expect(
			servicio.ingerirConEscrituraQueFalla(payload({ clave_contacto: "f1".repeat(16) }), ownerId),
		).rejects.toThrow();
		expect(await db.contact.count()).toBe(antesContactos);
		expect(await db.leadIngestEvent.count()).toBe(antesEventos);
	});

	test("el hash del receptor ignora los campos de transporte", async () => {
		// Si evento_id o revision entraran al hash, dos reintentos del mismo
		// envio tendrian hashes distintos y el replay se leeria como conflicto.
		const { hashDelPayload } = await import("../src/ingest/ingest.service");
		const base = payload();
		expect(hashDelPayload(base)).toBe(
			hashDelPayload({ ...base, evento_id: crypto.randomUUID(), revision: 9 }),
		);
		expect(hashDelPayload(base)).not.toBe(
			hashDelPayload({ ...base, cargo: "Gerenta" }),
		);
	});

	test("un conflicto de identidad no escribe nada", async () => {
		await db.contact.create({ data: { firstName: "A", phone: "56900000921" } });
		await db.contact.create({ data: { firstName: "B", phone: "56900000921" } });
		const antes = await db.leadIngestEvent.count();
		const r = await servicio.ingerir(
			payload({ telefono: "56900000921", clave_contacto: "e".repeat(32) }),
			ownerId,
		);
		expect(r.estado).toBe("conflict");
		// El evento SÍ se registra (con su motivo) para que el fallo sea
		// diagnosticable, pero ninguna fila comercial se creó.
		expect(await db.leadIngestEvent.count()).toBe(antes + 1);
		expect(await db.deal.count({
			where: { company: { name: "PRUEBA INTEGRACIÓN Idem SpA" } },
		})).toBe(0);
	});
});
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools \
  bun test apps/api/test/ingest-idempotency.spec.ts
```

Expected: FAIL — no existe `IngestService`.

- [ ] **Step 3: Escribir el servicio**

```typescript
// apps/api/src/ingest/ingest.service.ts
//
// La orquestación de una ingesta: idempotencia, orden y conflictos.
//
// Dos claves y no una (spec §4). `clave_contacto` es la identidad comercial y
// no cambia nunca. `evento_id` es el intento, y sólo cambia cuando cambia el
// contenido. Con una sola clave, "misma clave con payload distinto" sería el
// caso normal de una conversación que avanzó.
import { createHash } from "node:crypto";
import { type Db, Prisma } from "@crm/db";
import { lockIdempotencyKey } from "@crm/db/idempotency";
import { Injectable, Logger } from "@nestjs/common";
import { InjectDatabase } from "../database/database.constants";
import { IngestIdentityService } from "./ingest-identity.service";
import type { LeadInTouchPayload } from "./ingest.contracts";
import { IngestWriteService } from "./ingest-write.service";

export type ResultadoIngesta =
	| {
			estado: "created" | "replayed" | "updated";
			companyId: string | null;
			contactId: string;
			dealId: string | null;
			revision: number;
			eventoId: string;
		}
	| { estado: "conflict" | "stale"; motivo: string };

/// Hash del contenido de negocio. Excluye `evento_id` y `revision`: son del
/// transporte, y si entraran, cada reintento parecería contenido nuevo.
export function hashDelPayload(payload: LeadInTouchPayload): string {
	const { evento_id: _e, revision: _r, ...negocio } = payload;
	const ordenado = Object.keys(negocio)
		.sort()
		.map((clave) => [clave, (negocio as Record<string, unknown>)[clave]]);
	return createHash("sha256").update(JSON.stringify(ordenado)).digest("hex");
}

@Injectable()
export class IngestService {
	private readonly logger = new Logger(IngestService.name);

	constructor(
		@InjectDatabase() private readonly db: Db,
		private readonly identidad: IngestIdentityService,
		private readonly escritura: IngestWriteService,
	) {}

	async ingerir(
		payload: LeadInTouchPayload,
		ownerId: string,
	): Promise<ResultadoIngesta> {
		const hash = hashDelPayload(payload);

		return this.db.$transaction(async (tx) => {
			// Advisory lock por clave de contacto, la infraestructura de
			// idempotencia que ya trae el proyecto. Serializa los envíos
			// concurrentes del MISMO contacto en vez de dejarlos chocar contra la
			// restricción única y tener que releer.
			await lockIdempotencyKey(tx, `ingest:${payload.origen}:${payload.clave_contacto}`);

			const mismoEvento = await tx.leadIngestEvent.findUnique({
				where: {
					origin_eventoId: { origin: payload.origen, eventoId: payload.evento_id },
				},
			});

			if (mismoEvento) {
				// ORDEN DE LOS CHEQUEOS, y acá estaba un bug del diseño anterior:
				// un evento en CONFLICTO se guarda con su hash, así que decidir el
				// replay sólo por hash devolvía `status: "replayed"` con
				// `contactId: ""` -- un éxito falso con id vacío que el emisor
				// habría reintentado para siempre. El estado se mira PRIMERO.
				if (mismoEvento.status === "conflict") {
					return {
						estado: "conflict" as const,
						motivo:
							mismoEvento.conflictReason ??
							"Este evento ya quedó en conflicto y necesita resolución humana.",
					};
				}

				// El evento identifica un INTENTO. Si vuelve con otra revisión, el
				// emisor está afirmando algo distinto: es conflicto aunque el
				// contenido de negocio sea idéntico.
				if (mismoEvento.revision !== payload.revision) {
					return {
						estado: "conflict" as const,
						motivo:
							`El evento ${payload.evento_id} se registró con revisión ` +
							`${mismoEvento.revision} y ahora llega con ${payload.revision}.`,
					};
				}

				if (mismoEvento.payloadHash !== hash) {
					// Mismo intento con otro contenido: nunca sobrescritura silenciosa.
					return {
						estado: "conflict" as const,
						motivo:
							`El evento ${payload.evento_id} ya se registró con otro contenido. ` +
							"Una calificación nueva tiene que llevar un evento_id nuevo.",
					};
				}

				if (!mismoEvento.contactId) {
					// Estado aplicado pero sin contacto: es incoherente y no se
					// devuelve como éxito. Nunca un 2xx con contactId vacío.
					return {
						estado: "conflict" as const,
						motivo:
							`El evento ${payload.evento_id} está marcado como aplicado pero ` +
							"no tiene contacto asociado. Requiere revisión.",
					};
				}

				// Reintento del mismo envío: se devuelve lo ya creado, sin escribir
				// nada y sin volver a resolver identidad.
				return {
					estado: "replayed" as const,
					companyId: mismoEvento.companyId,
					contactId: mismoEvento.contactId,
					dealId: mismoEvento.dealId,
					revision: mismoEvento.revision,
					eventoId: mismoEvento.eventoId,
				};
			}

			// El cursor es la última revisión **APLICADA**, no la máxima recibida:
			// un evento que quedó en conflicto con revisión alta no puede
			// bloquear una revisión válida más baja que llegue después.
			const ultimo = await tx.leadIngestEvent.findFirst({
				where: {
					origin: payload.origen,
					claveContacto: payload.clave_contacto,
					status: { in: ["created", "updated"] },
				},
				orderBy: { revision: "desc" },
			});

			// Una revisión que no avanza llegó fuera de orden: no pisa a la nueva.
			//
			// Esto SUPONE que el emisor manda snapshots completos y no parches --
			// verificado en `payload_del_lead`, que serializa el modelo entero en
			// cada envío. Con parches habría que aplicarlos en orden en vez de
			// descartarlos, y la Task 10 ancla ese supuesto con un test.
			if (ultimo && payload.revision <= ultimo.revision) {
				return {
					estado: "stale" as const,
					motivo:
						`La revisión ${payload.revision} es anterior o igual a la ` +
						`${ultimo.revision} que ya se aplicó. No se sobrescribe con datos viejos.`,
				};
			}

			const resuelta = await this.identidad.resolver(tx, payload);
			if (!resuelta.ok) {
				// El evento se registra con su motivo para que el conflicto sea
				// diagnosticable y reejecutable, pero ninguna fila comercial se
				// crea -- y no puede haberse creado, porque `resolver` no escribe.
				//
				// Este registro NO avanza el cursor: su `status` es "conflict" y
				// la consulta del cursor sólo mira created/updated.
				await tx.leadIngestEvent.create({
					data: {
						origin: payload.origen,
						eventoId: payload.evento_id,
						claveContacto: payload.clave_contacto,
						revision: payload.revision,
						payloadHash: hash,
						payload: payload as unknown as Prisma.InputJsonValue,
						status: "conflict",
						conflictReason: resuelta.motivo,
					},
				});
				return { estado: "conflict" as const, motivo: resuelta.motivo };
			}

			// El plan se ejecuta recién acá: la resolución no escribió nada, así
			// que un conflicto descubierto arriba no dejó efectos parciales.
			const identidad = await this.identidad.aplicarIdentidad(tx, payload, resuelta.plan);
			const { dealId } = await this.escritura.escribir(tx, payload, identidad, ownerId);
			const estado = ultimo ? ("updated" as const) : ("created" as const);

			await tx.leadIngestEvent.create({
				data: {
					origin: payload.origen,
					eventoId: payload.evento_id,
					claveContacto: payload.clave_contacto,
					revision: payload.revision,
					payloadHash: hash,
					// El cuerpo íntegro: la copia reversible de los arrays.
					payload: payload as unknown as Prisma.InputJsonValue,
					status: estado,
					companyId: identidad.companyId,
					contactId: identidad.contactId,
					dealId,
				},
			});

			return {
				estado,
				companyId: identidad.companyId,
				contactId: identidad.contactId,
				dealId,
				revision: payload.revision,
				eventoId: payload.evento_id,
			};
		});
	}
}
```

- [ ] **Step 4: Correr los tests**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools \
  bun test apps/api/test/ingest-idempotency.spec.ts
```

Expected: PASS, los 14. Dos son los que importan: el de **concurrencia** (si
falla, el advisory lock no está serializando) y el de **repetir un conflicto**
(si devuelve `replayed`, volvió el éxito falso con `contactId` vacío).

Nota sobre el test del rollback: `ingerirConEscrituraQueFalla` es un helper de
prueba que inyecta un `IngestWriteService` cuyo `escribir` lanza. Escribirlo en
el spec, no en el servicio.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/ingest/ingest.service.ts \
        apps/api/test/ingest-idempotency.spec.ts
git commit -m "feat(ingest): idempotencia de dos claves, con orden y conflictos

Reusa lockIdempotencyKey de packages/db (advisory lock de Postgres), que ya
existia: serializa los envios concurrentes del mismo contacto en vez de
dejarlos chocar contra la restriccion unica.

Tres correcciones sobre el disenio anterior:
- El estado se mira ANTES del hash. Un evento en conflicto se guarda CON su
  hash, asi que decidir el replay por hash devolvia status replayed con
  contactId vacio: un exito falso que el emisor habria reintentado siempre.
- El cursor es la ultima revision APLICADA, no la maxima recibida. Un
  conflicto con revision alta no puede bloquear una revision valida menor.
- El mismo evento_id con otra revision es conflicto aunque el hash de
  negocio coincida: el evento identifica un intento, no un contenido.

Ningun resultado exitoso puede llevar contactId vacio."
```

---

### Task 9: El endpoint y su alcance

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest.controller.ts`
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest.module.ts`
- Modify: `/home/admincrm/compai-crm/apps/api/src/app.module.ts` (agregar `IngestModule` a `imports`)
- Modify: `/home/admincrm/compai-crm/apps/api/src/create-app.ts` (parser JSON acotado a la ruta de ingesta)
- Modify: `/home/admincrm/compai-crm/apps/api/src/config/env.validation.ts` (agregar `INTOUCH_INGEST_USER_ID` e `INTOUCH_LEAD_OWNER_EMAIL`)
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-endpoint.spec.ts`

**Interfaces:**
- Consumes: `IngestService` (Task 8), `leadInTouchSchema` y `MAX_INGEST_BODY_BYTES` (Task 5).
- Produces: `POST /api/ingest/intouch-lead`.

**Lo que la Task 2 dejó verificado y acá se usa:** una API key **no tiene alcance propio**. El alcance se pone acá, explícito, y se comprueba con cinco casos.

**Y una trampa verificada en `create-app.ts`:** la app Nest arranca con **`bodyParser: false`** y no registra ningún parser propio. O sea que **`@Body()` llega `undefined`** en un controlador REST común — es exactamente por eso que `tracking.controller.ts` lee el body crudo desde `@Req()` con un zod que acepta string o JSON. Así que la ruta de ingesta necesita su propio parser, acotado a ella (Step 4). Sin eso el endpoint falla en el primer request y el síntoma parece un problema de validación.

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-endpoint.spec.ts
import { beforeAll, describe, expect, test } from "bun:test";
import { db } from "@crm/db";
import request from "supertest";
import { createApp } from "../src/create-app";

let servidor: unknown;
let keyDeIngesta: string;
let keyDeOtro: string;
let cookieDeComercial: string;

function cuerpo(over: Record<string, unknown> = {}) {
	return {
		origen: "wsp_intouch",
		clave_contacto: "f".repeat(32),
		evento_id: crypto.randomUUID(),
		revision: 1,
		telefono: "56900000930",
		nombre_completo: "PRUEBA INTEGRACIÓN Ana",
		empresa: "PRUEBA INTEGRACIÓN Endpoint SpA",
		lead_score: "WARM",
		solicita_consultoria: false,
		solicita_contacto_humano: false,
		...over,
	};
}

describe("POST /api/ingest/intouch-lead", () => {
	beforeAll(async () => {
		const app = await createApp();
		await app.init();
		servidor = app.getHttpServer();
		// Las dos keys se preparan con el mismo API de servidor que la Task 2.
		keyDeIngesta = process.env.TEST_INGEST_KEY ?? "";
		keyDeOtro = process.env.TEST_OTHER_USER_KEY ?? "";
		cookieDeComercial = process.env.TEST_SESSION_COOKIE ?? "";
		expect(keyDeIngesta).not.toBe("");
		expect(keyDeOtro).not.toBe("");
		expect(cookieDeComercial).not.toBe("");
	});

	test("la key de ingesta NO sirve para leer el resto del CRM", async () => {
		// La comprobación que decide si la credencial está realmente acotada.
		// Una key de usuario con acceso total, verificada sólo en esta ruta,
		// sigue sirviendo para leer contactos por cualquier otra.
		for (const ruta of [
			"/api/contacts.list", "/api/companies.list",
			"/api/users.list", "/api/deals.list",
		]) {
			const r = await request(servidor).get(ruta).set("x-api-key", keyDeIngesta);
			expect([401, 403, 404]).toContain(r.status);
		}
	});

	test("con la key del principal de ingesta: 201 con ids", async () => {
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta)
			.send(cuerpo());
		expect(r.status).toBe(201);
		expect(r.body.status).toBe("created");
		expect(r.body.contactId).toBeTruthy();
	});

	test("con la key VÁLIDA DE OTRO USUARIO: 403", async () => {
		// El caso que pasaría en silencio si se asumiera que las keys tienen
		// permisos. No los tienen: una key equivale a ser su usuario dueño.
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeOtro)
			.send(cuerpo({ evento_id: crypto.randomUUID() }));
		expect(r.status).toBe(403);
	});

	test("sin key: 401", async () => {
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.send(cuerpo({ evento_id: crypto.randomUUID() }));
		expect(r.status).toBe(401);
	});

	test("con una key inventada: 401", async () => {
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", "crm_inventada_0000000000")
			.send(cuerpo({ evento_id: crypto.randomUUID() }));
		expect(r.status).toBe(401);
	});

	test("cookie de sesión VÁLIDA + key inventada: rechazo", async () => {
		// El caso que confunde identidades: si el guard resuelve la sesión de la
		// cookie cuando la key no sirve, una key basura pasaría siempre que el
		// navegador traiga una sesión -- y peor, con la identidad de esa persona.
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", "crm_inventada_0000000000")
			.set("Cookie", cookieDeComercial)
			.send(cuerpo({ evento_id: crypto.randomUUID() }));
		expect([401, 403]).toContain(r.status);
	});

	test("sólo cookie de comercial, sin key: rechazo", async () => {
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("Cookie", cookieDeComercial)
			.send(cuerpo({ evento_id: crypto.randomUUID() }));
		expect([401, 403]).toContain(r.status);
	});

	test("Content-Type no admitido: 415", async () => {
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta)
			.set("Content-Type", "text/plain")
			.send("origen=wsp_intouch");
		expect(r.status).toBe(415);
	});

	test("sin dueño configurado: 503, no 409", async () => {
		// El diseño anterior documentaba 503 y lanzaba ConflictException.
		// El emisor clasifica: 503 es reintentable, 409 es intervención humana.
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta)
			.set("x-test-sin-dueno", "1")   // el fixture desconfigura el dueño
			.send(cuerpo({ evento_id: crypto.randomUUID() }));
		expect(r.status).toBe(503);
	});

	test("JSON inválido: 400, no 500", async () => {
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta)
			.set("Content-Type", "application/json")
			.send("{esto no es json");
		expect(r.status).toBe(400);
	});

	test("un body enorme: 413, sin escritura parcial", async () => {
		const antes = await db.leadIngestEvent.count();
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta)
			.send(cuerpo({ resumen_conversacion: "x".repeat(200_000) }));
		expect([400, 413]).toContain(r.status);
		expect(await db.leadIngestEvent.count()).toBe(antes);
	});

	test("un campo inválido: 400 con el nombre del campo y sin datos sensibles", async () => {
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta)
			.send(cuerpo({ lead_score: "TIBIO" }));
		expect(r.status).toBe(400);
		expect(JSON.stringify(r.body)).toContain("lead_score");
		expect(JSON.stringify(r.body)).not.toContain("postgresql://");
	});

	test("un conflicto de idempotencia: 409", async () => {
		const base = cuerpo({ evento_id: crypto.randomUUID() });
		await request(servidor).post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta).send(base);
		const r = await request(servidor).post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta).send({ ...base, empresa: "Otra SpA" });
		expect(r.status).toBe(409);
	});

	test("un replay: 200 con los mismos ids", async () => {
		const base = cuerpo({ evento_id: crypto.randomUUID() });
		const primero = await request(servidor).post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta).send(base);
		const segundo = await request(servidor).post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta).send(base);
		expect(segundo.status).toBe(200);
		expect(segundo.body.status).toBe("replayed");
		expect(segundo.body.contactId).toBe(primero.body.contactId);
	});

	test("un lead COLD responde éxito con dealId null", async () => {
		// El emisor tiene que aceptar esto como éxito: la política crea
		// contactos sin oportunidad a propósito.
		const r = await request(servidor)
			.post("/api/ingest/intouch-lead")
			.set("x-api-key", keyDeIngesta)
			.send(cuerpo({
				evento_id: crypto.randomUUID(), clave_contacto: "a1".repeat(16),
				telefono: "56900000931", lead_score: "COLD",
			}));
		expect(r.status).toBe(201);
		expect(r.body.contactId).toBeTruthy();
		expect(r.body.dealId).toBeNull();
	});
});
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools bun test apps/api/test/ingest-endpoint.spec.ts
```

Expected: FAIL — la ruta devuelve 404.

- [ ] **Step 3: Escribir el controlador**

```typescript
// apps/api/src/ingest/ingest.controller.ts
//
// El receptor de leads de wsp_intouch.
//
// SOBRE EL ALCANCE, que es el punto delicado: omitir @AllowAnonymous() hace
// que el guard exija sesión, pero eso NO acota nada. Verificado en
// packages/auth/src/auth.ts: las keys se crean sin `permissions` y
// `enableSessionForAPIKeys: true` las vuelve equivalentes a su usuario dueño.
// Sin los dos chequeos de abajo, la key de CUALQUIER usuario del CRM podría
// crear leads.
import { API_KEY_HEADER } from "@crm/auth";
import type { Db } from "@crm/db";
import {
	BadRequestException, Body, ConflictException, Controller, ForbiddenException,
	Headers, HttpCode, Logger, Post, Req, Res, ServiceUnavailableException,
	UnauthorizedException, UnsupportedMediaTypeException,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import {
	ApiConflictResponse, ApiCreatedResponse, ApiForbiddenResponse, ApiHeader,
	ApiOkResponse, ApiOperation, ApiTags,
} from "@nestjs/swagger";
import { Session, type UserSession } from "@thallesp/nestjs-better-auth";
import type { Request, Response } from "express";
import type { EnvironmentVariables } from "../config/env.validation";
import { InjectDatabase } from "../database/database.constants";
import { leadInTouchSchema, MAX_INGEST_BODY_BYTES } from "./ingest.contracts";
import { IngestService } from "./ingest.service";

@ApiTags("Ingest")
@Controller("api/ingest")
export class IngestController {
	private readonly logger = new Logger(IngestController.name);

	constructor(
		@InjectDatabase() private readonly db: Db,
		private readonly ingest: IngestService,
		private readonly config: ConfigService<EnvironmentVariables, true>,
	) {}

	@Post("intouch-lead")
	@HttpCode(200)
	@ApiOperation({
		summary: "Recibe un lead calificado por el bot comercial de InTouch",
	})
	@ApiHeader({ name: API_KEY_HEADER, required: true })
	@ApiCreatedResponse({ description: "Lead creado" })
	@ApiOkResponse({ description: "Replay o actualización" })
	@ApiForbiddenResponse({ description: "La credencial no es la de ingesta" })
	@ApiConflictResponse({ description: "Conflicto de idempotencia o de identidad" })
	async intouchLead(
		@Session() sesion: UserSession,
		@Headers(API_KEY_HEADER) apiKey: string | undefined,
		@Body() body: unknown,
		@Req() req: Request,
		@Res({ passthrough: true }) res: Response,
	) {
		// 1. Tiene que venir por API key. Una sesión de cookie de navegador NO
		// sirve: el espejo de SessionOnlyMiddleware, para que el navegador de un
		// comercial autenticado no pueda postear leads.
		//
		// Y el chequeo va ANTES de mirar `sesion`: si la key es inválida, el
		// guard puede haber resuelto la sesión desde la COOKIE, y entonces
		// `sesion.user` es el comercial, no la integración. Sin este orden, una
		// key basura pasaría siempre que el navegador traiga sesión.
		if (!apiKey) {
			throw new UnauthorizedException(
				"Esta ruta se usa sólo con credencial de integración.",
			);
		}
		const tipo = (req.headers["content-type"] ?? "").split(";")[0].trim();
		if (tipo !== "application/json") {
			throw new UnsupportedMediaTypeException(
				"Esta ruta acepta sólo application/json.",
			);
		}

		// 2. Y tiene que ser la key del principal de ingesta. Las keys del CRM no
		// llevan permisos: sin esto, la de cualquier usuario serviría.
		// Que la sesión provenga de la KEY y no de la cookie: si el guard
		// resolvió una sesión de navegador, esto la descarta.
		const principal = this.config.get("INTOUCH_INGEST_USER_ID", { infer: true });
		if (!principal || sesion.user.id !== principal) {
			this.logger.warn({
				message: "Credencial fuera de alcance en la ingesta de InTouch",
				userId: sesion.user.id,
			});
			throw new ForbiddenException("Esta credencial no puede ingerir leads.");
		}

		// Cinturón, no la primera defensa: el `express.json({ limit })` de
		// create-app ya rechaza con 413 antes de parsear. Esto cubre el caso de
		// que alguien saque ese parser sin darse cuenta.
		const tamano = Buffer.byteLength(JSON.stringify(body ?? {}), "utf8");
		if (tamano > MAX_INGEST_BODY_BYTES) {
			res.status(413);
			return { status: "error", message: "El cuerpo excede el tamaño permitido." };
		}

		const analizado = leadInTouchSchema.safeParse(body);
		if (!analizado.success) {
			// Errores por campo, sin nada sensible: nunca error.message crudo,
			// SQL, stack ni secretos.
			throw new BadRequestException({
				status: "invalid",
				errores: analizado.error.issues.map((i) => ({
					campo: i.path.join("."),
					problema: i.message,
				})),
			});
		}

		const ownerId = await this.dueno();
		const resultado = await this.ingest.ingerir(analizado.data, ownerId);

		if (resultado.estado === "conflict") throw new ConflictException({
			status: "conflict", motivo: resultado.motivo,
		});
		if (resultado.estado === "stale") throw new ConflictException({
			status: "stale", motivo: resultado.motivo,
		});

		if (resultado.estado === "created") res.status(201);

		return {
			status: resultado.estado,
			companyId: resultado.companyId,
			contactId: resultado.contactId,
			dealId: resultado.dealId,
			revision: resultado.revision,
			eventoId: resultado.eventoId,
		};
	}

	/// El dueño de los leads del bot. `Deal.ownerId` es obligatorio, y no se
	/// improvisa: si no está configurado o no existe, se responde 503 y el lead
	/// queda pendiente en el emisor, que lo reintentará con la misma clave.
	private async dueno(): Promise<string> {
		const correo = this.config.get("INTOUCH_LEAD_OWNER_EMAIL", { infer: true });
		const usuario = correo
			? await this.db.user.findUnique({
					where: { email: correo }, select: { id: true },
				})
			: null;
		if (!usuario) {
			this.logger.error({
				message: "INTOUCH_LEAD_OWNER_EMAIL no resuelve a un usuario del CRM",
			});
			// 503 DE VERDAD. El diseño anterior documentaba 503 y lanzaba
			// ConflictException, o sea 409 -- y el emisor clasifica por status:
			// 503 es transitorio y se reintenta, 409 es intervención humana. Un
			// 409 acá dejaba el lead esperando a una persona por un problema de
			// configuración que se arregla solo al configurarla.
			throw new ServiceUnavailableException({
				status: "unavailable",
				motivo: "El dueño de los leads no está configurado en el CRM.",
			});
		}
		return usuario.id;
	}
}
```

- [ ] **Step 4: Registrar el parser JSON de la ruta de ingesta**

En `apps/api/src/create-app.ts`, después de `app.use(helmet())` y **antes** de `app.init()`, agregar:

```typescript
	// La app arranca con bodyParser: false (arriba), así que @Body() llegaría
	// undefined. Este parser va ACOTADO a la ruta de ingesta y no global, para
	// no cambiar cómo llegan los cuerpos al resto de la API -- el puente REST
	// de tRPC y el handler de Better Auth leen los suyos a su manera.
	//
	// El `limit` es lo que hace que un cuerpo enorme se rechace ANTES de
	// parsearlo: express responde 413 solo, sin cargarlo en memoria.
	app.use(
		"/api/ingest",
		express.json({ limit: MAX_INGEST_BODY_BYTES, type: "application/json" }),
	);
```

Y los imports que hacen falta arriba del archivo:

```typescript
import express from "express";
import { MAX_INGEST_BODY_BYTES } from "./ingest/ingest.contracts";
```

Verificar que el 413 sale de verdad y no un 500:

```bash
cd /home/admincrm/compai-crm
docker compose up -d api
python3 -c "print('{"x":"' + 'a'*200000 + '"}')" > /tmp/grande.json
curl -s -o /dev/null -w '%{http_code}
' -X POST \
  -H 'Content-Type: application/json' -H "x-api-key: $KEY" \
  --data @/tmp/grande.json \
  http://127.0.0.1:3006/api/ingest/intouch-lead
```

Expected: `413`.

- [ ] **Step 5: Escribir el módulo y registrarlo**

```typescript
// apps/api/src/ingest/ingest.module.ts
import { Module } from "@nestjs/common";
import { IngestController } from "./ingest.controller";
import { IngestIdentityService } from "./ingest-identity.service";
import { IngestService } from "./ingest.service";
import { IngestWriteService } from "./ingest-write.service";

@Module({
	controllers: [IngestController],
	providers: [IngestService, IngestIdentityService, IngestWriteService],
	exports: [IngestService],
})
export class IngestModule {}
```

En `apps/api/src/app.module.ts`, agregar el import y sumarlo al array `imports`:

```typescript
import { IngestModule } from "./ingest/ingest.module";
// … y dentro de imports: [ … , IngestModule ],
```

En `apps/api/src/config/env.validation.ts`, agregar las dos variables al esquema existente (opcionales, porque el CRM tiene que arrancar sin ellas y fallar sólo al ingerir):

```typescript
INTOUCH_INGEST_USER_ID: z.string().optional(),
INTOUCH_LEAD_OWNER_EMAIL: z.string().email().optional(),
```

- [ ] **Step 6: Preparar las dos keys de prueba y correr los tests**

```bash
cd /home/admincrm/compai-crm
# La key del principal de ingesta ya existe (Task 2). La del "otro usuario"
# se crea igual, con otro correo, para el caso de 403.
docker compose exec api bun tools/seed-ingest-principal.ts   # reusa la existente
docker compose -f docker-compose.crm.yml run --rm \
  -e TEST_INGEST_KEY="$(cat /tmp/lead_sink_token)" \
  -e TEST_OTHER_USER_KEY="<key de otro usuario>" \
  -e TEST_SESSION_COOKIE="<cookie de sesión de un comercial de prueba>" \
  tools bun test apps/api/test/ingest-endpoint.spec.ts
```

Expected: PASS, los 16. **Tres son bloqueantes si fallan**, no detalles:

- *key válida de otro usuario → 403*: sin esto la credencial de cualquiera ingiere.
- *cookie válida + key inventada → rechazo*: si pasa, el guard está usando la identidad de la cookie.
- *la key de ingesta no lee el resto del CRM*: si lee, la credencial no está acotada y hay que volver al Step 4 de la Task 2.

- [ ] **Step 7: Poner el rate limit en el gateway**

El plugin `apiKey` tiene `rateLimit: { enabled: false }`, así que el límite va en nginx. En `/home/admincrm/gateway/nginx.conf`, junto a la zona `login` que ya existe:

```nginx
limit_req_zone $binary_remote_addr zone=ingest:10m rate=60r/m;
```

Y en el bloque de la Task 14 se aplica con `limit_req zone=ingest burst=20 nodelay;`.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/ingest/ingest.controller.ts \
        apps/api/src/ingest/ingest.module.ts \
        apps/api/src/app.module.ts \
        apps/api/src/create-app.ts \
        apps/api/src/config/env.validation.ts \
        apps/api/test/ingest-endpoint.spec.ts
git commit -m "feat(ingest): endpoint POST /api/ingest/intouch-lead con alcance explicito

Omitir @AllowAnonymous() exige sesion pero NO acota: las keys del CRM se
crean sin permissions y enableSessionForAPIKeys las vuelve equivalentes a
su usuario dueno. Asi que el controlador exige (a) que venga por x-api-key
y no por cookie de navegador, y (b) que sea la key del principal de
ingesta. El caso 'key valida de otro usuario' esta cubierto por un test:
es el que pasaria en silencio si se asumiera lo contrario.

Sin dueno configurado responde 503 y el lead queda pendiente en el emisor,
en vez de inventar un dueno."
```

---

### Task 10: El emisor aprende `evento_id` y `revision`

**Files:**
- Modify: `/home/admincrm/wsp_intouch/bot/models.py` (clase `LeadInTouch`)
- Create: `/home/admincrm/wsp_intouch/bot/migrations/0038_lead_intouch_evento.py` (la genera `makemigrations`)
- Modify: `/home/admincrm/wsp_intouch/bot/business/lead_intouch.py`
- Modify: `/home/admincrm/wsp_intouch/bot/tests/test_despachador_lead.py`

**Interfaces:**
- Consumes: `LeadInTouch` con los 21 campos, `clave_idempotencia(lead)`, `payload_del_lead(lead)`.
- Produces: campos `evento_id` (uuid4, str), `payload_hash` (str), `revision` (int) en `LeadInTouch`; funciones `clave_contacto(lead) -> str`, `hash_de_negocio(payload) -> str`, `marcar_evento(lead) -> bool`; `payload_del_lead` pasa a incluir `clave_contacto`, `evento_id` y `revision`.

- [ ] **Step 1: Escribir los tests que fallan**

Al final de `bot/tests/test_despachador_lead.py`:

```python
class EventoTest(TestCase):
    """El evento_id y la revision, que son lo que hace idempotente al despacho.

    La clave de contacto sola no alcanza: identifica a la persona, no al
    intento. Con una sola clave, "misma clave con otro contenido" seria el caso
    NORMAL de una conversacion que avanzo, y el receptor no podria distinguir un
    reintento de una calificacion nueva.
    """

    def setUp(self):
        self.conv = Conversation.objects.create(wa_id="56900000020")
        self.lead = LeadInTouch.objects.create(
            conversation=self.conv, empresa="Acme SpA")

    def test_el_primer_marcado_abre_evento_y_revision_uno(self):
        from bot.business.lead_intouch import marcar_evento

        self.assertTrue(marcar_evento(self.lead))
        self.lead.refresh_from_db()
        self.assertNotEqual(self.lead.evento_id, "")
        self.assertEqual(self.lead.revision, 1)

    def test_sin_cambios_no_abre_evento_nuevo(self):
        # Es lo que permite que un reintento lleve la MISMA clave: si el
        # contenido no cambio, no es un intento nuevo.
        from bot.business.lead_intouch import marcar_evento

        marcar_evento(self.lead)
        primero = self.lead.evento_id
        self.assertFalse(marcar_evento(self.lead))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.evento_id, primero)
        self.assertEqual(self.lead.revision, 1)

    def test_un_cambio_de_contenido_abre_evento_nuevo_y_sube_la_revision(self):
        from bot.business.lead_intouch import marcar_evento

        marcar_evento(self.lead)
        primero = self.lead.evento_id
        self.lead.cargo = "Gerenta de Operaciones"
        self.lead.save()
        self.assertTrue(marcar_evento(self.lead))
        self.lead.refresh_from_db()
        self.assertNotEqual(self.lead.evento_id, primero)
        self.assertEqual(self.lead.revision, 2)

    def test_la_revision_solo_avanza(self):
        from bot.business.lead_intouch import marcar_evento

        for indice, cargo in enumerate(["A", "B", "C"], start=1):
            self.lead.cargo = cargo
            self.lead.save()
            marcar_evento(self.lead)
            self.lead.refresh_from_db()
            self.assertEqual(self.lead.revision, indice)

    def test_la_clave_de_contacto_no_cambia_entre_revisiones(self):
        from bot.business.lead_intouch import clave_contacto, marcar_evento

        antes = clave_contacto(self.lead)
        self.lead.cargo = "Otra cosa"
        self.lead.save()
        marcar_evento(self.lead)
        self.assertEqual(clave_contacto(self.lead), antes)

    def test_el_hash_ignora_los_campos_de_transporte(self):
        # Si evento_id o revision entraran al hash, cada reintento pareceria
        # contenido nuevo y el despacho no seria idempotente nunca.
        from bot.business.lead_intouch import hash_de_negocio

        base = {"empresa": "Acme SpA", "evento_id": "uno", "revision": 1}
        otro = {"empresa": "Acme SpA", "evento_id": "dos", "revision": 9}
        self.assertEqual(hash_de_negocio(base), hash_de_negocio(otro))

    def test_el_payload_es_un_SNAPSHOT_completo_y_no_un_patch(self):
        """De esto depende todo el algoritmo de orden del receptor.

        El receptor DESCARTA una revision anterior a la ultima aplicada. Eso
        solo es correcto si cada envio trae el estado completo: con parches,
        descartar una revision vieja perderia los campos que solo venian ahi.

        `payload_del_lead` serializa _CAMPOS_DEL_PAYLOAD entero en cada envio,
        asi que es un snapshot. Este test lo ANCLA: si alguien lo convierte en
        un diff para ahorrar bytes, rompe la idempotencia del receptor y tiene
        que verlo aca.
        """
        from bot.business.lead_intouch import _CAMPOS_DEL_PAYLOAD, marcar_evento

        marcar_evento(self.lead)
        # Un segundo envio que solo cambia un campo sigue trayendo TODOS.
        self.lead.cargo = "Gerenta"
        self.lead.save()
        marcar_evento(self.lead)
        payload = payload_del_lead(self.lead)
        for campo in _CAMPOS_DEL_PAYLOAD:
            self.assertIn(campo, payload, f"{campo} falta: el payload dejo de ser snapshot")

    def test_los_dos_solicita_son_booleanos_reales_y_usa_ia_es_triestado(self):
        """La semantica que el contrato del receptor tiene que respetar.

        `usa_ia_actualmente` es null=True en el modelo: sus tres estados son
        si / no / no se sabe. Los dos `solicita_*` son BooleanField(default=False)
        sin null, asi que en el cable son siempre booleanos reales -- nunca
        null. El receptor los valida como z.boolean() obligatorio y eso es
        correcto; documentado aca para que nadie lo "arregle" haciendolos
        nullable sin cambiar el receptor.
        """
        payload = payload_del_lead(self.lead)
        self.assertIsNone(payload["usa_ia_actualmente"])
        self.assertIs(payload["solicita_consultoria"], False)
        self.assertIs(payload["solicita_contacto_humano"], False)

    def test_el_payload_lleva_las_tres_claves(self):
        from bot.business.lead_intouch import marcar_evento

        marcar_evento(self.lead)
        self.lead.refresh_from_db()
        payload = payload_del_lead(self.lead)
        self.assertEqual(len(payload["clave_contacto"]), 32)
        self.assertEqual(payload["evento_id"], self.lead.evento_id)
        self.assertEqual(payload["revision"], 1)
```

- [ ] **Step 2: Correrlos**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py test bot.tests.test_despachador_lead.EventoTest -v2
```

Expected: FAIL — `cannot import name 'marcar_evento'`.

- [ ] **Step 3: Agregar los campos al modelo**

En `bot/models.py`, dentro de `LeadInTouch`, en el bloque `# Trazabilidad`, después de `despachado_en`:

```python
    # Idempotencia del despacho (spec de la integración con el CRM §4). Son
    # TRES cosas distintas y por eso son tres campos:
    #
    # - La clave de contacto se deriva del wa_id y no se guarda: es estable por
    #   definición y se recalcula (ver `clave_contacto`).
    # - `evento_id` identifica el INTENTO. Se regenera SÓLO cuando cambia el
    #   contenido, así que todos los reintentos de un mismo envío lo comparten
    #   y el receptor los reconoce como el mismo hecho.
    # - `revision` da el orden. El receptor rechaza una revisión que no avanza,
    #   para que un envío que llegó tarde no pise a uno más nuevo.
    evento_id = models.CharField(
        max_length=36, blank=True, default="",
        help_text="El intento de despacho vigente. Se regenera cuando cambia "
                  "el contenido, nunca en un reintento.")
    payload_hash = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Hash del contenido de negocio del último evento abierto. "
                  "Es cómo se decide si un cambio amerita un evento nuevo.")
    revision = models.PositiveIntegerField(
        default=0,
        help_text="Orden de los eventos de este lead. Sólo avanza.")
```

- [ ] **Step 4: Generar la migración y revisarla**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py makemigrations bot --name lead_intouch_evento
cat bot/migrations/0038_lead_intouch_evento.py
```

Expected: sólo tres `AddField` sobre `leadintouch`. **La migración contra la BD real exige confirmación explícita del usuario** (Global Constraints) y se aplica en la Task 14.

- [ ] **Step 5: Escribir las funciones**

En `bot/business/lead_intouch.py`, reemplazar `clave_idempotencia` y agregar lo nuevo:

```python
def clave_contacto(lead) -> str:
    """La identidad comercial del contacto, estable para siempre.

    Se llamaba `clave_idempotencia`, y el nombre mentía: `Conversation.wa_id`
    es `unique=True`, así que hay UNA conversación por número y esta clave
    identifica al CONTACTO, no a una conversación. La misma persona que vuelve
    a escribir meses después reusa la misma fila y la misma clave -- que es
    justo lo que el CRM necesita para no abrir un registro nuevo.

    Va hasheada: viaja a otro sistema y no tiene por qué llevar el teléfono en
    claro cuando un hash cumple la misma función.
    """
    import hashlib

    crudo = f"wsp_intouch:{lead.conversation.wa_id}"
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:32]


# Alias del nombre viejo, para no romper llamadas que queden en el repo.
clave_idempotencia = clave_contacto


# Los campos del payload que son TRANSPORTE y no contenido. Si entraran al
# hash, cada reintento parecería contenido nuevo y el despacho no sería
# idempotente nunca.
_CAMPOS_DE_TRANSPORTE = frozenset({"evento_id", "revision"})


def hash_de_negocio(payload: dict) -> str:
    """Hash del contenido comercial de un payload, ignorando el transporte.

    Ordenado y serializado de forma estable: dos payloads con las mismas claves
    en otro orden tienen que dar el mismo hash, o cada turno abriría un evento
    nuevo sin que nada hubiera cambiado.
    """
    import hashlib
    import json

    negocio = {k: v for k, v in payload.items() if k not in _CAMPOS_DE_TRANSPORTE}
    serializado = json.dumps(negocio, sort_keys=True, ensure_ascii=False,
                             default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def marcar_evento(lead) -> bool:
    """Abre un evento nuevo si el contenido del lead cambió. True si lo abrió.

    Es lo que separa "reintento del mismo envío" de "nueva actualización
    comercial", que es la distinción que el receptor no puede hacer solo.
    """
    import uuid

    payload = payload_del_lead(lead)
    hash_actual = hash_de_negocio(payload)
    if lead.evento_id and lead.payload_hash == hash_actual:
        return False

    lead.evento_id = str(uuid.uuid4())
    lead.payload_hash = hash_actual
    lead.revision = (lead.revision or 0) + 1
    # `despachado_en` se limpia: hay contenido nuevo que todavía no llegó al
    # destino, y dejarlo sellado escondería el lead de los pendientes.
    lead.despachado_en = None
    lead.save(update_fields=["evento_id", "payload_hash", "revision", "despachado_en"])
    return True
```

Y en `payload_del_lead`, reemplazar la línea de la clave por las tres:

```python
    payload["clave_contacto"] = clave_contacto(lead)
    payload["evento_id"] = lead.evento_id
    payload["revision"] = lead.revision
```

- [ ] **Step 6: Ajustar el test viejo que usaba el nombre anterior**

En `IdempotenciaTest`, la aserción de `payload_del_lead` ya no busca `clave_idempotencia`. Cambiar en `PayloadTest.test_lleva_los_campos_del_contrato_y_la_clave`:

```python
        self.assertIn("clave_contacto", payload)
```

- [ ] **Step 7: Correr los tests**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py test bot.tests.test_despachador_lead -v2
```

Expected: PASS, todos — los 9 nuevos y los 11 que ya estaban.

- [ ] **Step 8: Verificar que nada más usaba el nombre viejo**

```bash
grep -rn "clave_idempotencia" --include=*.py --include=*.tsx /home/admincrm/wsp_intouch | grep -v migrations
```

Expected: sólo el alias, su definición y los tests. Si aparece otro llamador, el alias lo cubre — pero hay que verlo, no suponerlo.

- [ ] **Step 9: Commit**

```bash
cd /home/admincrm/wsp_intouch
git add bot/models.py bot/migrations/0038_lead_intouch_evento.py \
        bot/business/lead_intouch.py bot/tests/test_despachador_lead.py
git commit -m "feat(lead): evento_id y revision, para que el despacho sea idempotente

La clave sola identificaba al contacto y no al intento, asi que el receptor
no podia distinguir un reintento de una calificacion nueva. Ahora hay tres
cosas: clave_contacto (estable, derivada del wa_id que es unique),
evento_id (se regenera solo si cambia el contenido) y revision (el orden,
solo avanza).

clave_idempotencia se renombra a clave_contacto porque el nombre mentia;
queda un alias. El hash ignora los campos de transporte: si entraran, cada
reintento pareceria contenido nuevo.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: El emisor valida la respuesta

**Files:**
- Modify: `/home/admincrm/wsp_intouch/bot/business/lead_intouch.py` (`_enviar_al_sink`, `_despachar_si_corresponde`)
- Modify: `/home/admincrm/wsp_intouch/config/settings.py` (agregar `LEAD_SINK_TOKEN`)
- Modify: `/home/admincrm/wsp_intouch/.env.docker.example`
- Create: `/home/admincrm/wsp_intouch/bot/tests/test_respuesta_receptor.py`

**Interfaces:**
- Consumes: `marcar_evento`, `payload_del_lead` (Task 10); el contrato de respuesta de la Task 9.
- Produces: `_enviar_al_sink(payload) -> dict | None` (antes devolvía `bool`); `settings.LEAD_SINK_TOKEN`.

**El bug que esto arregla:** hoy `_enviar_al_sink` sólo mira el código HTTP. Un 2xx con un body vacío, una página de login o un HTML de error se registran como éxito, y el lead queda sellado sin haber llegado.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# bot/tests/test_respuesta_receptor.py
"""Lo que el emisor exige de la respuesta del receptor.

Un raise_for_status exitoso no prueba que el lead este en el pipeline: hay que
mirar el cuerpo. Y al reves, `dealId: null` SI es exito -- la politica del CRM
crea contactos sin oportunidad a proposito, y tratarlo como fallo dejaria
reintentando para siempre un lead que ya llego.
"""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from bot.business.lead_intouch import _enviar_al_sink, _registrar_lead_impl
from bot.models import Conversation, LeadInTouch


def _respuesta(cuerpo, status=201, content_type="application/json"):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = (
        cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode("utf-8"))
    resp.headers = {"Content-Type": content_type}
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: None
    return resp


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/api/ingest/intouch-lead",
                   LEAD_SINK_TOKEN="crm_prueba")
class RespuestaTest(TestCase):
    def _enviar(self, resp):
        with patch("urllib.request.urlopen", return_value=resp):
            return _enviar_al_sink({"origen": "wsp_intouch"})

    def test_un_created_con_ids_es_exito(self):
        r = self._enviar(_respuesta(
            {"status": "created", "contactId": "c1", "dealId": "d1"}))
        self.assertEqual(r["contactId"], "c1")

    def test_un_deal_id_nulo_tambien_es_exito(self):
        # Un lead COLD entra como contacto sin oportunidad: es la politica,
        # no una falla.
        r = self._enviar(_respuesta(
            {"status": "created", "contactId": "c1", "dealId": None}))
        self.assertEqual(r["contactId"], "c1")

    def test_un_replay_es_exito(self):
        r = self._enviar(_respuesta(
            {"status": "replayed", "contactId": "c1", "dealId": "d1"}, status=200))
        self.assertEqual(r["status"], "replayed")

    def test_un_2xx_sin_contact_id_es_fallo_de_contrato(self):
        self.assertIsNone(self._enviar(_respuesta({"status": "created"})))

    def test_un_2xx_con_status_desconocido_es_fallo(self):
        self.assertIsNone(self._enviar(_respuesta(
            {"status": "quiza", "contactId": "c1"})))

    def test_un_2xx_con_html_es_fallo(self):
        # El sintoma de un redirect a una pagina de login.
        self.assertIsNone(self._enviar(_respuesta(
            b"<html><body>Sign in</body></html>", status=200,
            content_type="text/html")))

    def test_un_2xx_con_json_invalido_es_fallo(self):
        self.assertIsNone(self._enviar(_respuesta(b"{roto", status=201)))

    def test_un_conflicto_es_fallo_y_no_se_sella(self):
        self.assertIsNone(self._enviar(_respuesta(
            {"status": "conflict", "motivo": "otro contenido"}, status=409)))

    def test_manda_el_token_en_la_cabecera(self):
        with patch("urllib.request.urlopen") as abrir:
            abrir.return_value = _respuesta(
                {"status": "created", "contactId": "c1", "dealId": None})
            _enviar_al_sink({"origen": "wsp_intouch"})
        peticion = abrir.call_args[0][0]
        self.assertEqual(peticion.get_header("X-api-key"), "crm_prueba")

    def test_el_token_no_aparece_en_los_logs(self):
        with self.assertLogs("bot.business.lead_intouch", level="DEBUG") as registro:
            with patch("urllib.request.urlopen", side_effect=RuntimeError("caido")):
                conv = Conversation.objects.create(wa_id="56900000030")
                _registrar_lead_impl("56900000030", {"empresa": "Acme SpA"})
        self.assertNotIn("crm_prueba", "\n".join(registro.output))


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/api/ingest/intouch-lead",
                   LEAD_SINK_TOKEN="crm_prueba")
class SelloTest(TestCase):
    def setUp(self):
        Conversation.objects.create(wa_id="56900000031")

    def test_un_exito_sella_y_guarda_el_id_del_crm(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "created", "contactId": "c1", "dealId": "d1"}):
            _registrar_lead_impl("56900000031", {"empresa": "Acme SpA"})
        lead = LeadInTouch.objects.get()
        self.assertIsNotNone(lead.despachado_en)
        self.assertEqual(lead.crm_contact_id, "c1")
        self.assertEqual(lead.crm_deal_id, "d1")

    def test_un_fallo_de_contrato_no_sella(self):
        with patch("bot.business.lead_intouch._enviar_al_sink", return_value=None):
            _registrar_lead_impl("56900000031", {"empresa": "Acme SpA"})
        self.assertIsNone(LeadInTouch.objects.get().despachado_en)
```

- [ ] **Step 2: Correrlos**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py test bot.tests.test_respuesta_receptor -v2
```

Expected: FAIL — `LEAD_SINK_TOKEN` no existe y `crm_contact_id` tampoco.

- [ ] **Step 3: Agregar la configuración**

En `config/settings.py`, junto a `LEAD_SINK_URL`:

```python
# Credencial de integración del receptor. Fuera del código y fuera de los logs:
# el CRM la trata como si fuera el usuario dueño de la key (sus API keys no
# llevan permisos propios), así que filtrarla es filtrar ese acceso completo.
LEAD_SINK_TOKEN = os.environ.get("LEAD_SINK_TOKEN", "")
```

En `.env.docker.example`, junto a `LEAD_SINK_URL`:

```bash
# Con LEAD_SINK=http: el endpoint del CRM y su API key. La URL se resuelve
# desde la red del contenedor (crm_ingest), no desde el host.
# LEAD_SINK_URL=http://crm-api:3001/api/ingest/intouch-lead
LEAD_SINK_TOKEN=
```

- [ ] **Step 4: Agregar los dos ids del CRM al modelo**

En `bot/models.py`, `LeadInTouch`, después de `revision`:

```python
    crm_contact_id = models.CharField(
        max_length=40, blank=True, default="",
        help_text="Id del contacto en el CRM, como lo devolvió el receptor. "
                  "Es lo que permite ir del lead a su ficha sin adivinar.")
    crm_deal_id = models.CharField(
        max_length=40, blank=True, default="",
        help_text="Id de la oportunidad, si la política del CRM abrió una. "
                  "Vacío es válido: un lead frío entra como contacto sin oportunidad.")
```

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py makemigrations bot --name lead_intouch_ids_crm
```

- [ ] **Step 5: Reescribir `_enviar_al_sink`**

```python
# Los estados de respuesta que el receptor puede devolver y que significan
# "el lead está en el CRM". Explícito y no "cualquier 2xx": un 2xx con un
# cuerpo incompleto es un fallo de contrato, no un éxito.
_ESTADOS_DE_EXITO = frozenset({"created", "replayed", "updated"})


def _enviar_al_sink(payload: dict) -> dict | None:
    """POST al receptor. Devuelve su cuerpo si aceptó el lead, None si no.

    ANTES DEVOLVÍA UN BOOLEANO MIRANDO SÓLO EL CÓDIGO HTTP, y eso convertía
    tres fallas distintas en éxito: un 2xx con el cuerpo vacío, un redirect a
    una página de login (que llega como 200 con HTML) y un JSON roto. Un
    `raise_for_status` exitoso no prueba que el lead esté en el pipeline.

    stdlib `urllib` y no `requests`, que no está en requirements -- mismo
    criterio que bot/notify.py y utils/dios_registration.py.
    """
    import json
    import urllib.error
    import urllib.request

    destino = getattr(settings, "LEAD_SINK_URL", "")
    if not destino:
        logger.warning("[lead] LEAD_SINK=http pero LEAD_SINK_URL está vacío")
        return None

    cabeceras = {"Content-Type": "application/json"}
    token = getattr(settings, "LEAD_SINK_TOKEN", "")
    if token:
        cabeceras["x-api-key"] = token
    else:
        logger.warning("[lead] LEAD_SINK_TOKEN está vacío: el receptor va a rechazar")

    req = urllib.request.Request(
        destino, data=json.dumps(payload).encode("utf-8"),
        headers=cabeceras, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            crudo = resp.read()
            status_http = resp.status
            tipo = (resp.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as error:
        # 4xx y 5xx llegan acá. El motivo se loguea, el cuerpo del error no:
        # puede traer detalle interno del receptor.
        logger.warning("[lead] el receptor respondió %s", error.code)
        return None

    if not (200 <= status_http < 300):
        logger.warning("[lead] el receptor respondió %s", status_http)
        return None

    if "json" not in tipo:
        # El síntoma de un redirect a login: 200 con HTML.
        logger.warning("[lead] el receptor respondió %s en vez de JSON", tipo or "sin tipo")
        return None

    try:
        cuerpo = json.loads(crudo)
    except (ValueError, TypeError):
        logger.warning("[lead] el receptor respondió un JSON que no se puede leer")
        return None

    if not isinstance(cuerpo, dict):
        logger.warning("[lead] el receptor respondió algo que no es un objeto")
        return None

    if cuerpo.get("status") not in _ESTADOS_DE_EXITO:
        logger.warning("[lead] el receptor respondió status=%r", cuerpo.get("status"))
        return None

    if not cuerpo.get("contactId"):
        # `dealId` vacío SÍ es válido (un lead frío no abre oportunidad), pero
        # sin contacto no hay nada en el CRM que mirar.
        logger.warning("[lead] el receptor no devolvió contactId")
        return None

    return cuerpo
```

- [ ] **Step 6: Ajustar `_despachar_si_corresponde`**

Reemplazar el bloque `try` por:

```python
    try:
        marcar_evento(lead)
        respuesta = _enviar_al_sink(payload_del_lead(lead))
        if respuesta is not None:
            lead.despachado_en = timezone.now()
            lead.crm_contact_id = respuesta.get("contactId") or ""
            lead.crm_deal_id = respuesta.get("dealId") or ""
            lead.save(update_fields=["despachado_en", "crm_contact_id", "crm_deal_id"])
        else:
            logger.warning("[lead] el destino externo no aceptó el lead de %s", wa_id)
    except Exception:
        logger.warning("[lead] no pude despachar el lead de %s", wa_id, exc_info=True)
```

Y agregar `marcar_evento` al filtro de la consulta, que ahora tiene que traer también los leads con contenido nuevo:

```python
    lead = LeadInTouch.objects.filter(conversation__wa_id=wa_id).first()
    if lead is None:
        return
```

(El sello lo limpia `marcar_evento` cuando hay contenido nuevo, así que ya no hace falta filtrar por `despachado_en__isnull=True` acá.)

- [ ] **Step 7: Actualizar el test viejo cuya expectativa cambió a propósito**

`test_no_despacha_dos_veces_el_mismo_lead` (en `test_despachador_lead.py`) afirmaba `enviar.call_count == 1` después de dos escrituras con datos distintos. **Eso ahora es incorrecto y el cambio es deseado**: la segunda escritura agrega `cargo`, o sea contenido nuevo, así que `marcar_evento` abre un evento nuevo y el lead **se vuelve a despachar** — que es exactamente lo que el spec §4 llama "nueva actualización comercial". Lo que no debe pasar es despachar dos veces el *mismo* contenido.

Reemplazar el test por:

```python
    def test_no_despacha_dos_veces_el_mismo_contenido(self):
        # Antes este test afirmaba que dos escrituras dan UN despacho. Con
        # evento_id eso cambio a proposito: la segunda escritura de abajo NO
        # cambia el contenido, y por eso no hay segundo despacho. Un cambio
        # real si tiene que despacharse -- es una actualizacion comercial, no
        # un reintento.
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "created", "contactId": "c1",
                                 "dealId": None}) as enviar:
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
        self.assertEqual(enviar.call_count, 1)

    def test_un_cambio_real_si_se_despacha_otra_vez(self):
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "updated", "contactId": "c1",
                                 "dealId": None}) as enviar:
            _registrar_lead_impl("56900000011", {"empresa": "Acme SpA"})
            _registrar_lead_impl("56900000011", {"cargo": "Gerenta"})
        self.assertEqual(enviar.call_count, 2)
```

Los otros tests de `EncendidoTest` que mockean `_enviar_al_sink` con `return_value=True` o `False` también hay que ajustarlos: la función ahora devuelve un `dict` o `None`, no un booleano. `True` pasa a ser `{"status": "created", "contactId": "c1", "dealId": None}` y `False` pasa a ser `None`.

- [ ] **Step 8: Correr los tests**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py test bot.tests.test_respuesta_receptor bot.tests.test_despachador_lead -v2
```

Expected: PASS, todos. Si alguno de `EncendidoTest` falla con `TypeError`, quedó un mock devolviendo booleano.

- [ ] **Step 9: Commit**

```bash
git add bot/business/lead_intouch.py bot/models.py \
        bot/migrations/0039_lead_intouch_ids_crm.py \
        bot/tests/test_despachador_lead.py \
        config/settings.py .env.docker.example \
        bot/tests/test_respuesta_receptor.py
git commit -m "fix(lead): validar la respuesta del receptor, no solo el codigo HTTP

_enviar_al_sink mirava solo resp.status, asi que tres fallas distintas se
registraban como exito: un 2xx con cuerpo vacio, un redirect a login (que
llega como 200 con HTML) y un JSON roto. Un lead quedaba sellado sin haber
llegado a ningun lado.

Ahora exige JSON, un status conocido y contactId. dealId vacio SI es exito:
un lead frio entra como contacto sin oportunidad, y tratarlo como fallo
dejaria reintentando para siempre algo que ya llego.

Guarda los dos ids del CRM, para poder ir del lead a su ficha.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Recuperación de pendientes

**Files:**
- Create: `/home/admincrm/wsp_intouch/bot/management/commands/despachar_leads_pendientes.py`
- Create: `/home/admincrm/wsp_intouch/bot/tests/test_despachar_pendientes.py`
- Modify: `/home/admincrm/wsp_intouch/docker-compose.yml` (sidecar `cron`)

**Interfaces:**
- Consumes: `_enviar_al_sink`, `payload_del_lead`, `marcar_evento` (Tasks 10 y 11).
- Produces: comando `despachar_leads_pendientes`; contenedor `wsp_intouch-cron-1`.

**El agujero que esto tapa:** hoy el despacho corre **sólo** cuando llega un turno nuevo. Si el envío falla y la conversación termina ahí, el lead no llega nunca y nadie se entera.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# bot/tests/test_despachar_pendientes.py
"""El barrido que recupera los leads que no se pudieron despachar.

Sin esto, el despacho solo ocurre cuando llega un turno nuevo: si el envio
falla y la conversacion termina ahi, el lead no llega nunca. Es la falla que
el spec §5 punto 4 y las pruebas I09/I10 cubren.
"""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from bot.models import Conversation, LeadInTouch


def _lead(wa_id, **kwargs):
    conv = Conversation.objects.create(wa_id=wa_id)
    return LeadInTouch.objects.create(conversation=conv, **kwargs)


@override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x",
                   LEAD_SINK_TOKEN="crm_prueba")
class PendientesTest(TestCase):
    def test_despacha_el_que_quedo_sin_sellar(self):
        _lead("56900000040", empresa="Acme SpA")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "created", "contactId": "c1", "dealId": None}) as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_called_once()
        self.assertIsNotNone(LeadInTouch.objects.get().despachado_en)

    def test_no_toca_el_que_ya_se_despacho(self):
        _lead("56900000041", empresa="Acme SpA", despachado_en=timezone.now(),
              evento_id="ya", payload_hash="x", revision=1)
        with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_not_called()

    def test_reintenta_con_el_mismo_evento_id(self):
        # El punto de I09: si el CRM ya hizo commit y se perdio la respuesta,
        # reintentar con la MISMA clave devuelve el mismo id en vez de duplicar.
        lead = _lead("56900000042", empresa="Acme SpA")
        with patch("bot.business.lead_intouch._enviar_al_sink", return_value=None):
            call_command("despachar_leads_pendientes", stdout=StringIO())
        lead.refresh_from_db()
        primero = lead.evento_id
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "replayed", "contactId": "c1", "dealId": None}) as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviado = enviar.call_args[0][0]
        self.assertEqual(enviado["evento_id"], primero)

    def test_un_fallo_no_detiene_a_los_demas(self):
        _lead("56900000043", empresa="Una SpA")
        _lead("56900000044", empresa="Otra SpA")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   side_effect=[RuntimeError("caido"),
                                {"status": "created", "contactId": "c2", "dealId": None}]):
            call_command("despachar_leads_pendientes", stdout=StringIO())
        sellados = LeadInTouch.objects.exclude(despachado_en=None).count()
        self.assertEqual(sellados, 1)

    def test_con_el_sink_apagado_no_hace_nada(self):
        _lead("56900000045", empresa="Acme SpA")
        with override_settings(LEAD_SINK="none"):
            with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
                call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_not_called()

    def test_no_despacha_un_lead_sin_ningun_antecedente(self):
        # Una fila vacia no es un lead que valga la pena mandar.
        _lead("56900000046")
        with patch("bot.business.lead_intouch._enviar_al_sink") as enviar:
            call_command("despachar_leads_pendientes", stdout=StringIO())
        enviar.assert_not_called()
```

- [ ] **Step 2: Correrlos**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py test bot.tests.test_despachar_pendientes -v2
```

Expected: FAIL — `Unknown command: 'despachar_leads_pendientes'`.

- [ ] **Step 3: Escribir el comando**

```python
# bot/management/commands/despachar_leads_pendientes.py
"""Manda al CRM los leads que quedaron sin despachar.

POR QUÉ EXISTE: `_despachar_si_corresponde` corre dentro del turno, o sea sólo
cuando el contacto vuelve a escribir. Si el envío falla y la conversación
termina ahí, el lead se queda en la tabla y no llega nunca -- y el chat salió
igual de bien, así que nadie se entera. Este barrido es la red.

Reintenta con el MISMO `evento_id`: si el receptor ya había hecho commit y se
perdió la respuesta, devuelve el mismo id en vez de crear otra oportunidad.
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from bot.business.lead_intouch import (
    ANTECEDENTES_QUE_ABREN_LEAD, _enviar_al_sink, payload_del_lead,
)
from bot.models import LeadInTouch

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Despacha al CRM los leads pendientes o con contenido nuevo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite", type=int, default=100,
            help="Cuántos leads mirar por corrida. Acota el trabajo de un "
                 "barrido que corre cada hora.")

    def handle(self, *args, **opciones):
        sink = getattr(settings, "LEAD_SINK", "none")
        if sink == "none":
            self.stdout.write("LEAD_SINK=none: no hay destino, no hago nada.")
            return

        pendientes = (LeadInTouch.objects
                      .select_related("conversation")
                      .filter(despachado_en__isnull=True)
                      .order_by("actualizado")[:opciones["limite"]])

        despachados = 0
        fallidos = 0
        for lead in pendientes:
            if not self._vale_la_pena(lead):
                continue
            try:
                # `marcar_evento` NO se llama acá: si el lead ya tiene un
                # evento abierto, hay que reintentar ESE, no abrir otro. Sólo
                # se abre uno si nunca tuvo.
                if not lead.evento_id:
                    from bot.business.lead_intouch import marcar_evento
                    marcar_evento(lead)

                respuesta = _enviar_al_sink(payload_del_lead(lead))
                if respuesta is None:
                    fallidos += 1
                    continue

                lead.despachado_en = timezone.now()
                lead.crm_contact_id = respuesta.get("contactId") or ""
                lead.crm_deal_id = respuesta.get("dealId") or ""
                lead.save(update_fields=[
                    "despachado_en", "crm_contact_id", "crm_deal_id"])
                despachados += 1
            except Exception:
                # Un lead que revienta no puede detener a los demás.
                fallidos += 1
                logger.warning("[lead] falló el despacho diferido de %s",
                               lead.conversation.wa_id, exc_info=True)

        self.stdout.write(
            f"Leads despachados: {despachados}. Con fallo: {fallidos}.")

    def _vale_la_pena(self, lead) -> bool:
        """Una fila sin ningún antecedente no es un lead que mandar.

        Mismo criterio que la guarda de apertura de `registrar_lead_del_turno`:
        el extractor puede haber creado la fila y no haber capturado nada útil.
        """
        return any(getattr(lead, campo, None)
                   for campo in ANTECEDENTES_QUE_ABREN_LEAD)
```

- [ ] **Step 4: Correr los tests**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py test bot.tests.test_despachar_pendientes -v2
```

Expected: PASS, los 6.

- [ ] **Step 5: Agregar el sidecar cron**

En `docker-compose.yml`, al mismo nivel que `web` — copiando la forma del `cron` de `wsp_pompeyo`:

```yaml
  # Sidecar cron: barrido de leads pendientes de despacho al CRM.
  # Cada 15 minutos, porque un lead que pidió contacto humano y espera una
  # hora ya perdió su ventana. El comando es barato: sale limpio si no hay
  # nada pendiente o si LEAD_SINK=none.
  cron:
    build: .
    env_file:
      - .env.docker
    environment:
      DJANGO_SETTINGS_MODULE: config.settings_docker
    volumes:
      - .:/app
      - ./dios.json:/app/dios.json:ro
    command: >
      sh -c "while true; do
               python manage.py despachar_leads_pendientes || true;
               sleep 900;
             done"
    restart: unless-stopped
    depends_on:
      - web
```

- [ ] **Step 6: Commit**

```bash
git add bot/management/commands/despachar_leads_pendientes.py \
        bot/tests/test_despachar_pendientes.py docker-compose.yml
git commit -m "feat(lead): barrido de leads pendientes de despacho

El despacho corria solo dentro del turno, o sea cuando el contacto volvia a
escribir: si el envio fallaba y la conversacion terminaba ahi, el lead no
llegaba nunca y el chat salia igual de bien, asi que nadie se enteraba.

Reintenta con el MISMO evento_id, que es lo que hace que un timeout despues
del commit devuelva el mismo id en vez de crear otra oportunidad. Sidecar
cron cada 15 minutos, con la forma del de wsp_pompeyo.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: El panel muestra el estado de despacho

**Files:**
- Modify: `/home/admincrm/wsp_intouch/admin_panel/views.py` (`api_leads_intouch`, ~línea 1329)
- Modify: `/home/admincrm/wsp_intouch/admin_panel/urls.py` (ruta nueva)
- Modify: `/home/admincrm/wsp_intouch/frontend/src/pages/LeadsPage.tsx`
- Modify: `/home/admincrm/wsp_intouch/admin_panel/tests_intouch.py`

**Interfaces:**
- Consumes: `LeadInTouch.despachado_en`, `crm_contact_id`, `crm_deal_id`, `evento_id` (Tasks 10 y 11).
- Produces: `estado_despacho` (`"despachado" | "pendiente" | "sin_destino"`), `crm_contact_id`, `crm_deal_id` en la respuesta de `GET /api/leads`; `POST /api/leads/<id>/reintentar`.

**Por qué:** el CRM pasa a ser el pipeline, y el panel del bot queda como **diagnóstico**. Es el único lugar donde un lead que no llegó es visible — hoy `despachado_en` sólo se puede mirar por SQL.

- [ ] **Step 1: Escribir los tests que fallan**

Al final de `admin_panel/tests_intouch.py`:

```python
class EstadoDespachoTest(TestCase):
    """Que el panel muestre si el lead llego al CRM, y deje reintentarlo.

    Es lo que vuelve accionable un fallo de despacho: sin esto, un lead que no
    llego solo se puede ver por SQL.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        User.objects.create_user("panel", password="x")
        self.client.login(username="panel", password="x")

    def _lead(self, wa_id, **kwargs):
        from bot.models import Conversation, LeadInTouch

        conv = Conversation.objects.create(wa_id=wa_id)
        return LeadInTouch.objects.create(
            conversation=conv, empresa="Acme SpA", **kwargs)

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_un_lead_despachado_trae_los_ids_del_crm(self):
        from django.utils import timezone

        self._lead("56900000050", despachado_en=timezone.now(),
                   crm_contact_id="c1", crm_deal_id="d1")
        datos = json.loads(self.client.get("/demo/api/leads").content)
        fila = datos["leads"][0]
        self.assertEqual(fila["estado_despacho"], "despachado")
        self.assertEqual(fila["crm_contact_id"], "c1")
        self.assertEqual(fila["crm_deal_id"], "d1")

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_un_lead_sin_sellar_queda_pendiente(self):
        self._lead("56900000051")
        datos = json.loads(self.client.get("/demo/api/leads").content)
        self.assertEqual(datos["leads"][0]["estado_despacho"], "pendiente")

    @override_settings(LEAD_SINK="none")
    def test_con_el_sink_apagado_no_dice_pendiente(self):
        # Con el destino apagado TODOS los leads estarian "pendientes" y la
        # señal se pierde: un estado propio evita el falso rojo.
        self._lead("56900000052")
        datos = json.loads(self.client.get("/demo/api/leads").content)
        self.assertEqual(datos["leads"][0]["estado_despacho"], "sin_destino")

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_el_reintento_despacha_y_devuelve_el_estado_nuevo(self):
        lead = self._lead("56900000053")
        with patch("bot.business.lead_intouch._enviar_al_sink",
                   return_value={"status": "created", "contactId": "c9", "dealId": None}):
            resp = self.client.post(f"/demo/api/leads/{lead.id}/reintentar")
        self.assertEqual(resp.status_code, 200)
        datos = json.loads(resp.content)
        self.assertEqual(datos["estado_despacho"], "despachado")
        self.assertEqual(datos["crm_contact_id"], "c9")

    @override_settings(LEAD_SINK="http", LEAD_SINK_URL="http://crm-api:3001/x")
    def test_un_reintento_que_falla_lo_dice_sin_reventar(self):
        lead = self._lead("56900000054")
        with patch("bot.business.lead_intouch._enviar_al_sink", return_value=None):
            resp = self.client.post(f"/demo/api/leads/{lead.id}/reintentar")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["estado_despacho"], "pendiente")

    def test_el_reintento_exige_POST(self):
        lead = self._lead("56900000055")
        self.assertEqual(
            self.client.get(f"/demo/api/leads/{lead.id}/reintentar").status_code, 405)

    def test_un_lead_que_no_existe_da_404(self):
        self.assertEqual(
            self.client.post("/demo/api/leads/999999/reintentar").status_code, 404)
```

Agregar al principio del archivo, si no están:

```python
from unittest.mock import patch

from django.test import override_settings
```

- [ ] **Step 2: Correrlos**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py test admin_panel.tests_intouch.EstadoDespachoTest -v2
```

Expected: FAIL — `estado_despacho` no está en la respuesta y la ruta de reintento da 404.

- [ ] **Step 3: Agregar el estado a `api_leads_intouch`**

En `admin_panel/views.py`, reemplazar la línea `"despachado": bool(lead.despachado_en),` por:

```python
                "estado_despacho": _estado_despacho(lead),
                "crm_contact_id": lead.crm_contact_id,
                "crm_deal_id": lead.crm_deal_id,
                # Se conserva por compatibilidad con el frontend viejo.
                "despachado": bool(lead.despachado_en),
```

Y agregar antes de la vista:

```python
def _estado_despacho(lead) -> str:
    """En qué estado está el envío de este lead al CRM.

    Tres estados y no un booleano: con `LEAD_SINK=none` TODOS los leads
    estarían "pendientes" y el rojo del panel dejaría de significar algo. Un
    estado propio para "no hay destino configurado" mantiene la señal.
    """
    if settings.LEAD_SINK == "none":
        return "sin_destino"
    return "despachado" if lead.despachado_en else "pendiente"
```

- [ ] **Step 4: Escribir la vista de reintento**

Después de `api_leads_intouch`:

```python
@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_lead_reintentar(request, lead_id: int):
    """Reintenta el despacho de un lead al CRM, a pedido de una persona.

    Va con el MISMO `evento_id` que el intento anterior: si el receptor ya
    había hecho commit y se perdió la respuesta, devuelve el mismo id en vez
    de crear otra oportunidad. Por eso NO se llama a `marcar_evento` acá.

    No propaga el error: un reintento que falla informa el estado y deja el
    lead reintentable, que es exactamente lo que ya era.
    """
    from bot.business.lead_intouch import _enviar_al_sink, payload_del_lead
    from bot.models import LeadInTouch

    lead = get_object_or_404(
        LeadInTouch.objects.select_related("conversation"), pk=lead_id)

    if settings.LEAD_SINK == "none":
        return JsonResponse({
            "estado_despacho": "sin_destino",
            "detalle": "No hay destino configurado (LEAD_SINK=none).",
            "crm_contact_id": lead.crm_contact_id,
            "crm_deal_id": lead.crm_deal_id,
        })

    if not lead.evento_id:
        from bot.business.lead_intouch import marcar_evento
        marcar_evento(lead)

    try:
        respuesta = _enviar_al_sink(payload_del_lead(lead))
    except Exception:
        logger.warning("[lead] falló el reintento manual de %s", lead_id, exc_info=True)
        respuesta = None

    if respuesta is not None:
        lead.despachado_en = timezone.now()
        lead.crm_contact_id = respuesta.get("contactId") or ""
        lead.crm_deal_id = respuesta.get("dealId") or ""
        lead.save(update_fields=["despachado_en", "crm_contact_id", "crm_deal_id"])

    return JsonResponse({
        "estado_despacho": _estado_despacho(lead),
        "crm_contact_id": lead.crm_contact_id,
        "crm_deal_id": lead.crm_deal_id,
    })
```

**Verificado en el archivo real**: `get_object_or_404`, `timezone`, `csrf_exempt` y `require_http_methods` ya están importados (líneas 15-19), y la convención del archivo es `@csrf_exempt` + `@require_http_methods(["POST"])` — **no** `@require_POST`. Pero **no hay ningún `logger` en `admin_panel/views.py`**, así que hay que agregarlo arriba:

```python
import logging

logger = logging.getLogger(__name__)
```

Como las vistas van con `@csrf_exempt`, el `fetch` del frontend **no** necesita cabecera CSRF.

En `admin_panel/urls.py`, después de la ruta de `api_leads_intouch`:

```python
    path("api/leads/<int:lead_id>/reintentar", views.api_lead_reintentar,
         name="api_lead_reintentar"),
```

- [ ] **Step 5: Correr los tests**

```bash
cd /home/admincrm/wsp_intouch
USE_SQLITE=true python manage.py test admin_panel.tests_intouch -v2
```

Expected: PASS, los 7 nuevos y los que ya estaban.

- [ ] **Step 6: Actualizar el frontend**

En `frontend/src/pages/LeadsPage.tsx`:

1. En el tipo `Lead`, reemplazar `despachado: boolean;` por:

```typescript
  estado_despacho: 'despachado' | 'pendiente' | 'sin_destino';
  crm_contact_id: string;
  crm_deal_id: string;
```

2. En la columna de `actualizado` (~línea 199), reemplazar el bloque de `sinkActivo && !row.despachado` por:

```tsx
          {row.estado_despacho === 'pendiente' && (
            <span title="Sin despachar al CRM — se reintenta cada 15 minutos" className="text-danger">
              <Icon name="feather-alert-triangle" size="sm" />
            </span>
          )}
          {row.estado_despacho === 'despachado' && row.crm_contact_id && (
            <span title={`En el CRM: contacto ${row.crm_contact_id}`} className="text-success">
              <Icon name="feather-check-circle" size="sm" />
            </span>
          )}
```

3. En `acciones` (~línea 209), agregar el reintento:

```tsx
  const reintentar = async (row: Lead) => {
    setReintentando(row.id);
    try {
      const resp = await fetch(`${BASE}/api/leads/${row.id}/reintentar`, {
        method: 'POST',
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await cargar();
    } catch {
      setError('No se pudo reintentar el despacho. Volvé a intentarlo en un momento.');
    } finally {
      setReintentando(null);
    }
  };

  const acciones = [
    { label: 'Ver detalle', icon: 'feather-eye', onClick: (row: Lead) => setSeleccionado(row) },
    {
      label: 'Reintentar despacho',
      icon: 'feather-refresh-cw',
      onClick: reintentar,
      hidden: (row: Lead) => row.estado_despacho !== 'pendiente',
    },
  ];
```

Agregar el estado `const [reintentando, setReintentando] = useState<number | null>(null);` junto a los otros `useState`, y reusar la función de carga que ya existe en el archivo. **Leer primero cómo `HandoffPanel.tsx` o `IncidentsPanel.tsx` arman la URL base y manejan el error** y copiar ese patrón, no inventar uno. La vista va con `@csrf_exempt` (la convención de `admin_panel/views.py`), así que no hace falta token CSRF.

**Antes de escribir el JSX**: verificar las props reales de `DataTable` y de las acciones en el `.d.ts` instalado, no en la skill de duralux (está desactualizada):

```bash
cd /home/admincrm/wsp_intouch/frontend
grep -n "actions\|hidden" node_modules/@duralux/ui/dist/index.d.ts | head -20
```

Si `actions` no soporta `hidden`, filtrar el array antes de pasarlo.

- [ ] **Step 7: Compilar el frontend**

```bash
cd /home/admincrm/wsp_intouch/frontend
corepack pnpm@9.15.0 install
corepack pnpm@9.15.0 build
```

Expected: build sin errores de tipos. (`pnpm` global v11 rompe con el Node del host; hay que usar `corepack pnpm@9.15.0`.)

- [ ] **Step 8: Commit**

```bash
cd /home/admincrm/wsp_intouch
git add admin_panel/views.py admin_panel/urls.py \
        admin_panel/tests_intouch.py frontend/src/pages/LeadsPage.tsx
git commit -m "feat(panel): estado de despacho al CRM y reintento manual

El CRM pasa a ser el pipeline y el panel del bot queda como diagnostico:
es el unico lugar donde un lead que no llego es visible, y hoy
despachado_en solo se podia mirar por SQL.

Tres estados y no un booleano: con LEAD_SINK=none todos los leads
estarian 'pendientes' y el rojo dejaria de significar algo. El reintento
va con el mismo evento_id, asi que no puede duplicar una oportunidad.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Conectar los dos extremos y probar la ruta completa

**Files:**
- Modify: `/home/admincrm/wsp_intouch/docker-compose.yml` (red `crm_ingest` en `web` y `cron`)
- Modify: `/home/admincrm/wsp_intouch/.env.docker` (encender el sink)
- Modify: `/home/admincrm/gateway/nginx.conf` (bloque `/crm-api/`)
- Create: `/home/admincrm/wsp_intouch/docs/EVIDENCIA_INTEGRACION_CRM.md`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: la ruta `bot → CRM` funcionando; la evidencia de I01, I09, I10, I11 y I12.

**Bloqueo declarado:** esta tarea necesita el proceso del bot arriba, o sea el login SQL `intouch_login_qa`. **No** necesita credenciales de Meta ni el RAG en Supabase.

- [ ] **Step 1: Coordinar antes de tocar nada**

Este working tree puede tener más de una sesión, y el contenedor que corre es producción.

```bash
cd /home/admincrm/wsp_intouch && git status --short
cd /home/admincrm/gateway && git status --short
```

Y avisar a las sesiones peer con `ListAgents` / `SendMessage` antes del rebuild y de la migración.

- [ ] **Step 2: Enganchar el bot a la red del CRM**

En `/home/admincrm/wsp_intouch/docker-compose.yml`, agregar a los servicios `web` y `cron`:

```yaml
    networks:
      - default
      - crm_ingest
```

Y al final del archivo:

```yaml
networks:
  crm_ingest:
    external: true
```

- [ ] **Step 3: Encender el sink**

En `.env.docker` (no en el `.example`):

```bash
LEAD_SINK=http
LEAD_SINK_URL=http://crm-api:3001/api/ingest/intouch-lead
LEAD_SINK_TOKEN=<la key de la Task 2>
```

- [ ] **Step 4: Aplicar las migraciones del bot — CON CONFIRMACIÓN EXPLÍCITA**

**Parar acá y pedirle al usuario la confirmación de la migración contra la BD real** (Global Constraints). Recién con el sí:

```bash
cd /home/admincrm/wsp_intouch
docker compose exec web python manage.py migrate bot --plan     # mostrar qué haría
docker compose exec web python manage.py migrate bot
```

- [ ] **Step 5: Levantar y verificar la URL DESDE EL CONTENEDOR DEL BOT**

Que la URL responda desde el host no prueba nada: el bot resuelve por otra red.

```bash
cd /home/admincrm/wsp_intouch
docker compose up -d web cron
docker compose exec web python -c "
import urllib.request
req = urllib.request.Request('http://crm-api:3001/api/health')
print(urllib.request.urlopen(req, timeout=5).status)
"
```

Expected: `200`. Si falla, la red `crm_ingest` no está enganchada en los dos lados.

- [ ] **Step 6: Publicar el endpoint en el gateway con su rate limit**

En `/home/admincrm/gateway/nginx.conf`, en el `http {}` junto a la zona `login`:

```nginx
limit_req_zone $binary_remote_addr zone=ingest:10m rate=60r/m;
```

Y en el `server` de 443, **antes** de cualquier `location /crm/`:

```nginx
    # API del CRM. El bot NO pasa por acá (le pega directo por la red
    # crm_ingest); esto es para diagnóstico y para el frontend del CRM.
    location /crm-api/ {
      limit_req zone=ingest burst=20 nodelay;
      proxy_pass http://127.0.0.1:3006/;
      proxy_hide_header X-Content-Type-Options;
      proxy_hide_header Referrer-Policy;
      add_header X-Content-Type-Options "nosniff" always;
      add_header Referrer-Policy "strict-origin-when-cross-origin" always;
      add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
      add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
    }
```

Aplicar — `nginx -s reload` **no alcanza** con este bind-mount:

```bash
cd /home/admincrm/gateway
docker compose exec nginx nginx -t          # validar la sintaxis primero
docker compose up -d --force-recreate nginx
curl -sk -o /dev/null -w '%{http_code}\n' https://172.20.21.249/crm-api/api/health
```

Expected: `200`.

- [ ] **Step 7: I01 — un lead sintético recorre toda la ruta**

```bash
cd /home/admincrm/wsp_intouch
docker compose exec web python manage.py shell <<'EOF'
from bot.models import Conversation, LeadInTouch
from bot.business.lead_intouch import _despachar_si_corresponde

conv, _ = Conversation.objects.get_or_create(wa_id="56900000099")
LeadInTouch.objects.update_or_create(
    conversation=conv,
    defaults=dict(
        nombre_completo="PRUEBA INTEGRACIÓN Ana Pérez",
        correo="ana@example.com",
        empresa="PRUEBA INTEGRACIÓN SpA",
        industria="Retail",
        cargo="Gerenta de Operaciones",
        necesidad_principal="Automatizar la atención de postventa",
        canales_actuales=["whatsapp", "correo, teléfono"],
        soluciones_interes=["voz IA"],
        lead_score="HOT",
        solicita_contacto_humano=True,
        resumen_conversacion="Pidió hablar con un ejecutivo.",
        siguiente_accion_recomendada="Llamar mañana por la mañana.",
    ),
)
_despachar_si_corresponde("56900000099")

lead = LeadInTouch.objects.get(conversation__wa_id="56900000099")
print("despachado_en:", lead.despachado_en)
print("crm_contact_id:", lead.crm_contact_id)
print("crm_deal_id:", lead.crm_deal_id)
print("evento_id:", lead.evento_id, "revision:", lead.revision)
EOF
```

Expected: `despachado_en` con fecha, y los dos ids del CRM no vacíos.

- [ ] **Step 8: I02 — verificar en el CRM que llegó completo**

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun -e '
import { db } from "@crm/db";
const contacto = await db.contact.findFirstOrThrow({
  where: { phone: "56900000099" },
  include: {
    company: true,
    deals: { include: { deal: { include: { fieldValues: { include: { field: true } } } } } },
    fieldValues: { include: { field: true } },
    activities: true,
  },
});
console.log("empresa:", contacto.company?.name, contacto.company?.domain);
console.log("etapa:", contacto.deals[0]?.deal.stage);
console.log("campos contacto:", contacto.fieldValues.map(v => `${v.field.key}=${v.text ?? v.bool}`));
console.log("campos deal:", contacto.deals[0]?.deal.fieldValues.map(v => `${v.field.key}=${v.text}`));
console.log("actividades:", contacto.activities.map(a => `${a.type}/${a.subject}`));
const evento = await db.leadIngestEvent.findFirstOrThrow({
  where: { contactId: contacto.id },
});
console.log("array reconstruido:", (evento.payload as any).canales_actuales);
'
```

Expected: etapa `QUALIFIED_TO_BUY`; una `NOTE` y una `TASK`; y el array `["whatsapp", "correo, teléfono"]` **exacto**, con la coma interna intacta — es la prueba I18 en el entorno real.

- [ ] **Step 9: I09 — respuesta perdida DESPUÉS del commit, sin duplicar**

El punto de esta prueba es que el receptor **sí** escribió y el emisor **no**
se enteró. Un mock de `_enviar_al_sink` no lo demuestra: evita la llamada, así
que nunca hubo commit del otro lado. Hace falta que la petición llegue de
verdad y que se descarte la respuesta.

Un proxy mínimo en el medio que reenvía y corta:

```python
# /tmp/proxy_corta.py
"""Reenvia el POST al receptor y CORTA antes de devolver la respuesta.

Asi el receptor hace commit de verdad y el emisor ve la red cortada, que es
exactamente el escenario de I09 y lo que un mock no puede reproducir.
"""
import http.server
import urllib.request

DESTINO = "http://crm-api:3001/api/ingest/intouch-lead"


class Corta(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        cuerpo = self.rfile.read(int(self.headers["Content-Length"]))
        req = urllib.request.Request(
            DESTINO, data=cuerpo, method="POST",
            headers={"Content-Type": "application/json",
                     "x-api-key": self.headers.get("x-api-key", "")})
        try:
            urllib.request.urlopen(req, timeout=20).read()
        except Exception as error:
            print("el receptor respondio con error:", error, flush=True)
        else:
            print("el receptor APLICO el evento; corto la respuesta", flush=True)
        # Sin status line: el emisor ve la conexion cortada.
        self.connection.close()


http.server.HTTPServer(("0.0.0.0", 9999), Corta).serve_forever()
```

```bash
cd /home/admincrm/wsp_intouch
docker compose cp /tmp/proxy_corta.py web:/tmp/proxy_corta.py
docker compose exec -d web python /tmp/proxy_corta.py
```

Se apunta el emisor al proxy y se despacha:

```bash
docker compose exec -e LEAD_SINK_URL="http://127.0.0.1:9999/x" web \
  python manage.py shell -c "
from bot.business.lead_intouch import _despachar_si_corresponde
from bot.models import LeadInTouch
l = LeadInTouch.objects.get(conversation__wa_id='56900000099')
l.despachado_en = None; l.save(update_fields=['despachado_en'])
_despachar_si_corresponde('56900000099')
l.refresh_from_db()
print('sin sellar (correcto):', l.despachado_en is None, '| evento:', l.evento_id)
"
docker compose logs --tail 5 web | grep 'APLICO el evento'
```

Expected: el log del proxy dice que el receptor **aplicó** el evento, y el
lead queda **sin sellar** — que es el estado correcto: el emisor no puede
afirmar entrega si no vio la respuesta.

Y el reintento, con el **mismo** `evento_id`, contra el receptor directo:

```bash
docker compose exec web python manage.py despachar_leads_pendientes
```

Expected: `Leads despachados: 1`. Y en el CRM, **una** oportunidad:

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools bun -e '
import { db } from "@crm/db";
const ids = await Bun.file("/tmp/ids-prueba.json").json();
console.log("oportunidades:", await db.deal.count({ where: { companyId: ids.companyId } }));
console.log("eventos:", await db.leadIngestEvent.count({ where: { claveContacto: ids.claveContacto } }));
console.log("estados:", (await db.leadIngestEvent.findMany({
  where: { claveContacto: ids.claveContacto }, select: { status: true, revision: true },
})));
'
```

Expected: `oportunidades: 1`, y el segundo evento con estado `replayed` — no
un `created` nuevo. Si sale 2, la idempotencia no funciona con el emisor real
y es un bloqueante.

Al terminar, matar el proxy:

```bash
cd /home/admincrm/wsp_intouch
docker compose exec web pkill -f proxy_corta.py
docker compose exec web rm -f /tmp/proxy_corta.py
```

- [ ] **Step 10: I10 — el barrido recupera un pendiente tras un reinicio**

```bash
cd /home/admincrm/wsp_intouch
docker compose exec web python manage.py shell -c "
from bot.models import LeadInTouch
l = LeadInTouch.objects.get(conversation__wa_id='56900000099')
l.despachado_en = None; l.save(update_fields=['despachado_en'])
print('dejado pendiente')
"
docker compose restart web cron
docker compose exec cron python manage.py despachar_leads_pendientes
docker compose exec web python manage.py shell -c "
from bot.models import LeadInTouch
print(LeadInTouch.objects.get(conversation__wa_id='56900000099').despachado_en)
"
```

Expected: una fecha, y **una sola** oportunidad en el CRM (repetir la consulta del paso 9).

- [ ] **Step 11: I11 — el bot no registra éxito falso**

```bash
cd /home/admincrm/wsp_intouch
docker compose exec web python manage.py shell <<'EOF'
from unittest.mock import MagicMock, patch
from bot.business.lead_intouch import _enviar_al_sink

def resp(cuerpo, status=200, tipo="text/html"):
    r = MagicMock(); r.status = status; r.read.return_value = cuerpo
    r.headers = {"Content-Type": tipo}
    r.__enter__ = lambda s: s; r.__exit__ = lambda s, *a: None
    return r

casos = {
    "HTML de login": resp(b"<html>Sign in</html>"),
    "2xx sin ids": resp(b'{"status":"created"}', 201, "application/json"),
    "JSON roto": resp(b"{roto", 201, "application/json"),
}
for nombre, r in casos.items():
    with patch("urllib.request.urlopen", return_value=r):
        print(nombre, "->", _enviar_al_sink({"origen": "wsp_intouch"}))
EOF
```

Expected: `None` en los tres.

- [ ] **Step 12: I12 — la petición de contacto humano es accionable**

Ya verificada en el paso 8 (la `TASK`). Confirmar que tiene vencimiento y no está completada:

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun -e '
import { ActivityType, db } from "@crm/db";
const t = await db.activity.findFirstOrThrow({
  where: { type: ActivityType.TASK, contact: { phone: "56900000099" } },
});
console.log("vence:", t.dueAt, "completada:", t.completedAt, "asunto:", t.subject);
'
```

Expected: `dueAt` con fecha, `completedAt` en `null`.

- [ ] **Step 13: I16 — los logs no filtran nada, probado con canario**

Una regex que no encuentra nada **no prueba** que no haya secretos: prueba que
esa regex no coincidió. Primero se comprueba que la búsqueda *funciona*, con un
valor canario que sí aparecería si algo se filtrara:

```bash
CANARIO="crm_canario_$(openssl rand -hex 6)"
cd /home/admincrm/wsp_intouch
docker compose exec -e LEAD_SINK_TOKEN="$CANARIO" web python manage.py shell -c "
from bot.business.lead_intouch import _enviar_al_sink
print(_enviar_al_sink({'origen': 'wsp_intouch'}))
"
```

Y recién ahí la búsqueda, contando coincidencias en vez de imprimirlas:

```bash
for repo in /home/admincrm/wsp_intouch /home/admincrm/compai-crm; do
  cd $repo
  for patron in "$CANARIO" 'x-api-key' 'postgresql://' 'Traceback'; do
    n=$(docker compose logs --tail 500 2>/dev/null | grep -ciE "$patron")
    printf '%-16s %-18s %s coincidencias\n' "$(basename $repo)" "$patron" "$n"
  done
done
```

Expected: `0` en todas. Si el canario aparece, el token se está logueando.
Repetir con el CRM caído: el camino de error es donde los secretos se filtran.

- [ ] **Step 14: Limpiar los datos de prueba POR ID**

**Nunca por prefijo.** Un `startsWith: "569000000"` o
`name: { startsWith: "PRUEBA" }` puede borrar filas reales que casualmente
coincidan — y en el CRM esas filas son de un cliente. Se borra por los ids
que los pasos anteriores anotaron en `/tmp/ids-prueba.json`:

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools bun -e '
import { db } from "@crm/db";
const ids = await Bun.file("/tmp/ids-prueba.json").json();
// Orden de dependencias: actividades y FieldValue caen por cascade al borrar
// el contacto; el evento y el deal se borran explícitos.
await db.leadIngestEvent.deleteMany({ where: { claveContacto: ids.claveContacto } });
if (ids.dealId) await db.deal.delete({ where: { id: ids.dealId } });
await db.contact.delete({ where: { id: ids.contactId } });
if (ids.companyId) await db.company.delete({ where: { id: ids.companyId } });
console.log("borrados por id");
'
```

Del lado del bot, por `wa_id` **exacto** y nada más:

```bash
cd /home/admincrm/wsp_intouch
docker compose exec web python manage.py shell -c "
from bot.models import Conversation
borradas, _ = Conversation.objects.filter(wa_id='56900000099').delete()
print('conversaciones borradas:', borradas)
"
```

- [ ] **Step 15: Escribir la evidencia**

Crear `docs/EVIDENCIA_INTEGRACION_CRM.md` con una fila por prueba: **ID, comando o pasos, entorno, versión, esperado, observado y PASS/FAIL/NO EJECUTADO**. Las de los grupos B y D van como NO EJECUTADO con su bloqueo exacto nombrado — `NO APLICA` necesita un motivo de alcance y no sirve para omitir un control.

- [ ] **Step 16: Actualizar la ficha central y el hilo**

- `docs-repo/apps/` — ficha del CRM con rutas, puertos, versión y estado. **Ojo**: `docs-repo` tiene cambios sin commitear de otra sesión (`apps/call_reviews.md`, `apps/wsp_platform.md`, `integracion-satelite.md`): `git add` sólo del archivo nuevo.
- `wsp_intouch/hilo.md` — recap de la sesión, con el formato que ya usa el archivo.

- [ ] **Step 17: Commit**

```bash
cd /home/admincrm/wsp_intouch
git add docker-compose.yml docs/EVIDENCIA_INTEGRACION_CRM.md hilo.md
git commit -m "feat(lead): conectar el despacho al CRM y evidencia de la ruta completa

La URL se verifica DESDE el contenedor del bot, no desde el host: resuelve
por la red crm_ingest y que responda en el host no prueba nada.

Evidencia de I01, I02, I09, I10, I11, I12, I16 y I18. Los grupos B y D
quedan NO EJECUTADO con su bloqueo nombrado: el acceso del comercial es el
plan hermano, y el E2E conversacional espera credenciales de Meta.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

```bash
cd /home/admincrm/gateway
git add nginx.conf
git commit -m "feat(gateway): publicar la API del CRM en /crm-api/ con rate limit

El plugin apiKey del CRM tiene rateLimit apagado, asi que el limite va aca.
El bot no pasa por este bloque: le pega directo por la red crm_ingest."
```

---

---

### Task 15: Resolver un conflicto sin editar el evento

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest-conflicts.router.ts`
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest-conflicts.contracts.ts`
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest-resolve.service.ts`
- Modify: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest.module.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-resolve.spec.ts`

**Interfaces:**
- Consumes: `LeadIngestEvent` (Task 3), `IngestService` (Task 8), `IngestIdentityService` (Task 6).
- Produces: procedimientos tRPC `ingest.conflicts.list` y `ingest.conflicts.resolve({ eventoId, contactId, motivo })`; `IngestResolveService.resolver(...)`.

**Por qué esta tarea existe:** las Tasks 6 y 8 producen conflictos a propósito
—es lo correcto frente a una identidad ambigua— pero sin esto **los conflictos
se acumulan y nadie puede hacer nada con ellos**. El emisor reintenta, el
receptor vuelve a decir conflicto, y el lead no llega nunca. Repetirlo por cron
no lo resuelve.

Va **detrás de la sesión de GranCRM**, no de la key de ingesta: es una
operación humana. La key del bot no puede resolver sus propios conflictos.

- [ ] **Step 1: Escribir los tests que fallan**

```typescript
// apps/api/test/ingest-resolve.spec.ts
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { db } from "@crm/db";
import { IngestResolveService } from "../src/ingest/ingest-resolve.service";
import { IngestIdentityService } from "../src/ingest/ingest-identity.service";
import { IngestWriteService } from "../src/ingest/ingest-write.service";
import { IngestService } from "../src/ingest/ingest.service";

const ingesta = new IngestService(db, new IngestIdentityService(), new IngestWriteService());
const resolver = new IngestResolveService(db, ingesta);

let ownerId: string;
let actorId: string;

async function conflictoDePrueba(clave: string, telefono: string) {
	await db.contact.create({ data: { firstName: "A", phone: telefono } });
	await db.contact.create({ data: { firstName: "B", phone: telefono } });
	const p = {
		origen: "wsp_intouch" as const, clave_contacto: clave,
		evento_id: crypto.randomUUID(), revision: 1, telefono,
		empresa: "PRUEBA INTEGRACIÓN Conflicto SpA", lead_score: "WARM" as const,
		solicita_consultoria: false, solicita_contacto_humano: false,
	};
	const r = await ingesta.ingerir(p, ownerId);
	expect(r.estado).toBe("conflict");
	return p;
}

describe("resolución de conflictos", () => {
	beforeEach(async () => {
		const dueno = await db.user.upsert({
			where: { email: "dueno-res@example.com" }, update: {},
			create: { id: crypto.randomUUID(), name: "Dueño", email: "dueno-res@example.com", emailVerified: true },
			select: { id: true },
		});
		ownerId = dueno.id;
		const actor = await db.user.upsert({
			where: { email: "actor-res@example.com" }, update: {},
			create: { id: crypto.randomUUID(), name: "Actor", email: "actor-res@example.com", emailVerified: true },
			select: { id: true },
		});
		actorId = actor.id;
	});

	test("lista los conflictos con su código y su motivo", async () => {
		await conflictoDePrueba("c0".repeat(16), "56900000940");
		const filas = await resolver.listar();
		const fila = filas.find((f) => f.claveContacto === "c0".repeat(16));
		expect(fila?.status).toBe("conflict");
		expect(fila?.conflictReason).toContain("teléfono");
	});

	test("asociar una identidad aplica el evento sin editar su payload", async () => {
		const p = await conflictoDePrueba("c1".repeat(16), "56900000941");
		const elegido = await db.contact.findFirstOrThrow({ where: { phone: "56900000941" } });
		const antes = await db.leadIngestEvent.findUniqueOrThrow({
			where: { origin_eventoId: { origin: p.origen, eventoId: p.evento_id } },
		});

		const r = await resolver.resolver({
			eventoId: p.evento_id, contactId: elegido.id,
			motivo: "Es la misma persona, el duplicado se fusiona aparte.",
			actorId, ownerId,
		});

		expect(r.estado).toBe("created");
		expect(r.contactId).toBe(elegido.id);
		// El payload original NO se toca: una corrección de contenido exige un
		// evento nuevo del productor, no editar el ledger.
		const despues = await db.leadIngestEvent.findUniqueOrThrow({
			where: { origin_eventoId: { origin: p.origen, eventoId: p.evento_id } },
		});
		expect(despues.payload).toEqual(antes.payload);
		expect(despues.payloadHash).toBe(antes.payloadHash);
	});

	test("registra actor y motivo", async () => {
		const p = await conflictoDePrueba("c2".repeat(16), "56900000942");
		const elegido = await db.contact.findFirstOrThrow({ where: { phone: "56900000942" } });
		await resolver.resolver({
			eventoId: p.evento_id, contactId: elegido.id,
			motivo: "Mismo contacto verificado por teléfono.", actorId, ownerId,
		});
		const fila = await db.leadIngestEvent.findUniqueOrThrow({
			where: { origin_eventoId: { origin: p.origen, eventoId: p.evento_id } },
		});
		expect(fila.resolvedById).toBe(actorId);
		expect(fila.resolutionReason).toContain("verificado");
		expect(fila.resolvedAt).not.toBeNull();
	});

	test("resolver dos veces el mismo conflicto es idempotente", async () => {
		const p = await conflictoDePrueba("c3".repeat(16), "56900000943");
		const elegido = await db.contact.findFirstOrThrow({ where: { phone: "56900000943" } });
		const datos = {
			eventoId: p.evento_id, contactId: elegido.id,
			motivo: "Mismo contacto.", actorId, ownerId,
		};
		const primera = await resolver.resolver(datos);
		const segunda = await resolver.resolver(datos);
		expect(segunda.contactId).toBe(primera.contactId);
		expect(await db.deal.count({
			where: { company: { name: "PRUEBA INTEGRACIÓN Conflicto SpA" } },
		})).toBe(1);
	});

	test("NO pisa una revisión posterior ya aplicada", async () => {
		// Un conflicto viejo que alguien resuelve tarde no puede sobrescribir
		// una calificación más nueva que ya entró.
		const p = await conflictoDePrueba("c4".repeat(16), "56900000944");
		const elegido = await db.contact.findFirstOrThrow({ where: { phone: "56900000944" } });
		// Llega y se aplica la revisión 5.
		await ingesta.ingerir(
			{ ...p, evento_id: crypto.randomUUID(), revision: 5, telefono: "56900000945", cargo: "Gerenta" },
			ownerId,
		);
		const r = await resolver.resolver({
			eventoId: p.evento_id, contactId: elegido.id,
			motivo: "Resuelto tarde.", actorId, ownerId,
		});
		expect(r.estado).toBe("stale");
	});

	test("un evento que no está en conflicto no se puede resolver", async () => {
		const r = await resolver.resolver({
			eventoId: crypto.randomUUID(), contactId: "cualquiera",
			motivo: "x", actorId, ownerId,
		}).catch((e) => e);
		expect(String(r)).toMatch(/no está en conflicto|no existe/i);
	});
});
```

- [ ] **Step 2: Correrlos**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools \
  bun test apps/api/test/ingest-resolve.spec.ts
```

Expected: FAIL — no existe `IngestResolveService`.

- [ ] **Step 3: Agregar los campos de auditoría al modelo**

En `packages/db/prisma/schema.prisma`, dentro de `LeadIngestEvent`:

```prisma
  resolvedById     String?
  resolvedAt       DateTime?
  resolutionReason String?
  /// Identidad que una persona asoció al resolver el conflicto. Se guarda
  /// aparte del payload: el payload es lo que dijo el productor y no se edita.
  resolvedContactId String?
```

```bash
docker compose -f docker-compose.crm.yml run --rm \
  -e DATABASE_URL="postgresql://crm_owner:$POSTGRES_PASSWORD@postgres:5432/crm?schema=public" \
  tools bunx --bun prisma migrate dev \
    --schema packages/db/prisma/schema.prisma --name lead_ingest_resolution
```

Revisar el SQL: sólo `ALTER TABLE ... ADD COLUMN` sobre `leadIngestEvent`.

- [ ] **Step 4: Escribir el servicio**

```typescript
// apps/api/src/ingest/ingest-resolve.service.ts
//
// La salida de un conflicto de ingesta.
//
// Las Tasks 6 y 8 producen conflictos a propósito: frente a una identidad
// ambigua, no adivinar es lo correcto. Pero sin esto los conflictos se
// acumulan y el lead no llega nunca -- el emisor reintenta, el receptor vuelve
// a decir conflicto, y repetirlo por cron no lo resuelve.
//
// REGLA QUE ORDENA TODO: se asocia una identidad, NO se edita el evento. El
// payload es lo que afirmó el productor; corregir el contenido exige un evento
// nuevo del bot, no reescribir el ledger.
import { type Db, Prisma } from "@crm/db";
import { BadRequestException, Injectable, Logger, NotFoundException } from "@nestjs/common";
import { InjectDatabase } from "../database/database.constants";
import { IngestService, type ResultadoIngesta } from "./ingest.service";

const ORIGEN = "wsp_intouch";

@Injectable()
export class IngestResolveService {
	private readonly logger = new Logger(IngestResolveService.name);

	constructor(
		@InjectDatabase() private readonly db: Db,
		private readonly ingesta: IngestService,
	) {}

	async listar() {
		return this.db.leadIngestEvent.findMany({
			where: { status: "conflict", resolvedAt: null },
			orderBy: { createdAt: "desc" },
			take: 200,
			select: {
				id: true, eventoId: true, claveContacto: true, revision: true,
				conflictReason: true, status: true, createdAt: true,
			},
		});
	}

	async resolver(entrada: {
		eventoId: string;
		contactId: string;
		motivo: string;
		actorId: string;
		ownerId: string;
	}): Promise<ResultadoIngesta & { contactId?: string }> {
		const evento = await this.db.leadIngestEvent.findUnique({
			where: { origin_eventoId: { origin: ORIGEN, eventoId: entrada.eventoId } },
		});
		if (!evento) throw new NotFoundException("Ese evento no existe.");

		// Idempotente: resolver dos veces devuelve lo mismo y no repite efectos.
		if (evento.resolvedAt && evento.contactId) {
			return {
				estado: "replayed",
				companyId: evento.companyId,
				contactId: evento.contactId,
				dealId: evento.dealId,
				revision: evento.revision,
				eventoId: evento.eventoId,
			};
		}
		if (evento.status !== "conflict") {
			throw new BadRequestException("Ese evento no está en conflicto.");
		}

		const contacto = await this.db.contact.findUnique({
			where: { id: entrada.contactId },
			select: { id: true, archivedAt: true },
		});
		if (!contacto || contacto.archivedAt) {
			throw new BadRequestException("Ese contacto no existe o está archivado.");
		}

		// Se reejecuta el MISMO payload, con la identidad ya decidida. La
		// reejecución revalida unicidad y orden en transacción, así que si
		// mientras tanto se aplicó una revisión posterior, responde `stale` y no
		// la pisa.
		const payload = evento.payload as Prisma.JsonObject;
		const resultado = await this.ingesta.ingerirConIdentidadResuelta(
			payload as never,
			entrada.ownerId,
			{ contactId: contacto.id },
		);

		await this.db.leadIngestEvent.update({
			where: { id: evento.id },
			data: {
				resolvedById: entrada.actorId,
				resolvedAt: new Date(),
				resolutionReason: entrada.motivo,
				resolvedContactId: contacto.id,
			},
		});

		this.logger.log({
			message: "Conflicto de ingesta resuelto",
			eventoId: evento.eventoId,
			actorId: entrada.actorId,
			resultado: resultado.estado,
		});

		return resultado;
	}
}
```

- [ ] **Step 5: Agregar el punto de entrada al `IngestService`**

En `ingest.service.ts`, un método hermano de `ingerir` que salta la resolución
de identidad porque ya viene decidida por una persona:

```typescript
	/// Igual que `ingerir`, pero con la identidad ya resuelta por una persona
	/// (Task 15). No vuelve a resolver: eso devolvería el mismo conflicto.
	///
	/// Reusa el resto tal cual -- lock, chequeo de estado, cursor de revisión
	/// aplicada y escritura en la misma transacción -- para que una resolución
	/// manual no pueda saltarse las garantías de orden.
	async ingerirConIdentidadResuelta(
		payload: LeadInTouchPayload,
		ownerId: string,
		identidadElegida: { contactId: string },
	): Promise<ResultadoIngesta> {
		return this.aplicar(payload, ownerId, { forzarContactId: identidadElegida.contactId });
	}
```

Y refactorizar `ingerir` para que delegue en un `aplicar(payload, ownerId, opciones)`
privado que, cuando recibe `forzarContactId`, usa
`{ empresa: {accion:"ninguna"}, contacto: {accion:"usar", id: forzarContactId} }`
como plan en vez de llamar a `resolver`. **El resto del algoritmo no cambia**:
mismo lock, mismo chequeo de estado, mismo cursor y misma transacción.

- [ ] **Step 6: Exponer los dos procedimientos tRPC**

`ingest-conflicts.router.ts`, siguiendo la forma de los demás `*.router.ts` del
repo. Dos puntos que no se negocian:

- Van con el middleware de sesión del CRM, **no** con la key de ingesta: es una
  operación humana y queda protegida por el acceso de GranCRM (plan hermano).
- Usar `SessionOnlyMiddleware`, que ya existe justamente para eso: rechaza una
  petición que traiga `x-api-key`. Así la credencial del bot no puede resolver
  sus propios conflictos.

- [ ] **Step 7: Correr los tests**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools \
  bun test apps/api/test/ingest-resolve.spec.ts
```

Expected: PASS, los 6. El que importa es el de la revisión posterior: si
devuelve `created` en vez de `stale`, una resolución tardía pisa datos nuevos.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/ingest/ingest-resolve.service.ts \
        apps/api/src/ingest/ingest-conflicts.router.ts \
        apps/api/src/ingest/ingest-conflicts.contracts.ts \
        apps/api/src/ingest/ingest.module.ts \
        apps/api/src/ingest/ingest.service.ts \
        packages/db/prisma/schema.prisma packages/db/prisma/migrations \
        apps/api/test/ingest-resolve.spec.ts
git commit -m "feat(ingest): resolver un conflicto sin editar el evento

Las Tasks 6 y 8 producen conflictos a proposito, pero sin salida se
acumulaban: el emisor reintentaba, el receptor volvia a decir conflicto y el
lead no llegaba nunca.

Se asocia una identidad, NO se edita el payload: es lo que afirmo el
productor, y corregir contenido exige un evento nuevo del bot. Queda actor,
motivo y fecha. Reusa el algoritmo completo -- lock, cursor y transaccion --
asi que una resolucion tardia responde stale en vez de pisar una revision
posterior ya aplicada.

Va detras de la sesion de GranCRM con SessionOnlyMiddleware: la key del bot
no puede resolver sus propios conflictos."
```

---

## Estado final esperado de este plan

Con las 15 tareas en verde: **VALIDADO EN PRUEBAS, PENDIENTE DE DESPLIEGUE** — la ruta `emisor del bot → persistencia correcta → oportunidad en el pipeline` comprobada con reintentos y fallos reales, y el acceso del comercial cubierto por el plan hermano.

Lo que **no** se puede declarar con esto: que el bot capture los datos por sí solo en una conversación real. Eso es el Grupo D y espera credenciales de Meta.
