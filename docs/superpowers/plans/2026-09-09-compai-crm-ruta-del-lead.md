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
- **Tests del CRM**: son de integración real y escriben filas. Exigen `TEST_DATABASE_URL` distinta de `DATABASE_URL`, con nombre terminado en `_test`.
- **`bun` no está en el host** (sólo Node v22.23.2). Todo comando `bun` corre dentro de un contenedor.
- **Criterio de un test que no rompe el repo**: `git status` limpio DESPUÉS de correr la suite.

---

### Task 1: CRM vendored, Postgres y build reproducible

**Files:**
- Create: `/home/admincrm/compai-crm/` (clon de `https://github.com/trycompai/crm.git`, tag/commit de v1.15.3)
- Create: `/home/admincrm/compai-crm/docker-compose.override.yml`
- Create: `/home/admincrm/compai-crm/Dockerfile.api`
- Create: `/home/admincrm/compai-crm/Dockerfile.app`
- Create: `/home/admincrm/compai-crm/.env`
- Create: `/home/admincrm/compai-crm/RUNBOOK.md`

**Interfaces:**
- Produces: contenedores `crm-postgres`, `crm-api` (interno 3001, publicado `127.0.0.1:3006`), `crm-app` (interno 3000, publicado `127.0.0.1:3005`); red externa `crm_ingest`; `GET /api/health` respondiendo 200.

- [ ] **Step 1: Clonar y fijar la versión**

```bash
cd /home/admincrm
git clone https://github.com/trycompai/crm.git compai-crm
cd compai-crm
git remote rename origin upstream
git log --oneline -1                    # anotar el commit exacto en RUNBOOK.md
git checkout -b local/integracion-intouch
```

Verificar que la versión es la esperada:

```bash
grep '"version"' package.json          # espera: "1.15.3"
```

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

- [ ] **Step 5: Escribir `docker-compose.override.yml`**

El `docker-compose.yml` de upstream sólo levanta Postgres y se deja intacto; esto lo extiende.

```yaml
services:
  postgres:
    # Upstream publica 5432 al host. Acá NO: nada de exponer Postgres
    # (spec §1, prompt §E02). Se sobreescribe con una lista vacía.
    ports: []
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
    command: >
      sh -c "bun run db:deploy && bun apps/api/src/main.ts"

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

networks:
  crm_interna:
    driver: bridge
  crm_ingest:
    external: true
```

- [ ] **Step 6: Escribir `.env`**

Sólo lo que el código de esta versión usa. Sin `GEMINI_API_KEY`: no aparece en v1.15.3.

```bash
cat > /home/admincrm/compai-crm/.env <<'EOF'
DATABASE_URL="postgresql://postgres:postgres@postgres:5432/crm?schema=public"
TEST_DATABASE_URL="postgresql://postgres:postgres@postgres:5432/crm_test?schema=public"

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
openssl rand -base64 32   # pegar en BETTER_AUTH_SECRET
```

- [ ] **Step 7: Levantar y verificar que la API responde**

```bash
cd /home/admincrm/compai-crm
docker compose up -d --build postgres api
docker compose logs -f api | head -40
```

Expected: `API listening on http://localhost:3001` y las migraciones aplicadas por `db:deploy`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3006/api/health
```

Expected: `200`.

- [ ] **Step 8: Verificar que Postgres NO está expuesto**

```bash
ss -ltn | grep 5432 || echo "OK: 5432 no escucha en el host"
```

Expected: la línea `OK:`. Si aparece un listener, el `ports: []` del override no se aplicó.

- [ ] **Step 9: Confirmar que las migraciones quedaron limpias**

```bash
docker compose exec api bunx --bun prisma migrate status --schema packages/db/prisma/schema.prisma
```

Expected: `Database schema is up to date!` y ninguna migración pendiente ni deriva.

- [ ] **Step 10: Commit**

```bash
cd /home/admincrm/compai-crm
git add Dockerfile.api Dockerfile.app docker-compose.override.yml RUNBOOK.md
git commit -m "infra: build y compose para desplegar el CRM en GranCRM-QA

Upstream no trae Dockerfile y su compose sólo levanta Postgres. Postgres
deja de publicar puerto y los dos servicios publican sólo en loopback: el
gateway corre en network_mode host y llega por 127.0.0.1."
```

`.env` no se commitea (lo cubre `.gitignore` de upstream; verificar con `git status`).

---

### Task 2: El principal de ingesta y su API key

**Files:**
- Create: `/home/admincrm/compai-crm/tools/seed-ingest-principal.ts`
- Modify: `/home/admincrm/compai-crm/.env` (rellenar `INTOUCH_INGEST_USER_ID`)

**Interfaces:**
- Consumes: contenedores de la Task 1.
- Produces: un `User` de servicio con `email` `bot-intouch@in-touchcrm.cl`; una API key `crm_…`; el valor de `INTOUCH_INGEST_USER_ID` (cuid del User).

**Por qué un usuario de servicio y no la key de una persona:** verificado en `packages/auth/src/auth.ts` — las keys se crean **sin `permissions`** y `enableSessionForAppKeys: true` las vuelve equivalentes a su usuario dueño. Una key no tiene alcance propio; el alcance lo pone la Task 9 comparando contra este id.

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

const created = await auth.api.createApiKey({
	body: {
		name: "wsp_intouch — ingesta de leads",
		userId: user.id,
		expiresIn: 365 * 24 * 60 * 60,
	},
});

console.log(`INTOUCH_INGEST_USER_ID=${user.id}`);
console.log(`LEAD_SINK_TOKEN=${created.key}`);
console.log("Guardá la key ahora: no se puede volver a leer.");
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun tools/seed-ingest-principal.ts
```

Expected: las dos líneas con valores. **Si `createApiKey` rechaza el `userId`**, el supuesto del API de servidor es falso: parar acá y reportarlo — es una decisión de diseño, no algo que se parchee inventando el hash de la key a mano.

- [ ] **Step 3: Verificar que la key autentica de verdad**

No alcanza con que el script la haya impreso. Se comprueba contra un endpoint que ya existe:

```bash
KEY='<la key del paso 2>'
curl -s -o /dev/null -w 'con key: %{http_code}\n' -H "x-api-key: $KEY" http://127.0.0.1:3006/api/users.me
curl -s -o /dev/null -w 'sin key: %{http_code}\n' http://127.0.0.1:3006/api/users.me
```

Expected: `con key: 200` y `sin key: 401`. Si la primera da 401, la key no está autenticando y las Tasks 9 y 14 no se pueden validar.

- [ ] **Step 4: Guardar el id en `.env` y reiniciar la API**

```bash
cd /home/admincrm/compai-crm
sed -i 's|^INTOUCH_INGEST_USER_ID=.*|INTOUCH_INGEST_USER_ID="<el id del paso 2>"|' .env
docker compose up -d api
```

- [ ] **Step 5: Commit**

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
docker compose exec api bun test apps/api/test/ingest-event.spec.ts
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
docker compose exec api bun test apps/api/test/ingest-event.spec.ts
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
docker compose exec api bun test apps/api/test/ingest-fields.spec.ts
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
docker compose exec api bun test apps/api/test/ingest-fields.spec.ts
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
docker compose exec api bun test apps/api/test/ingest-contract.spec.ts
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
docker compose exec api bun test apps/api/test/ingest-contract.spec.ts
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
- Produces: `IngestIdentityService` con `resolver(tx, payload): Promise<ResolucionIdentidad>`, y el tipo `ResolucionIdentidad = { ok: true; companyId: string | null; contactId: string } | { ok: false; motivo: string }`.

**El punto donde una fusión equivocada cuesta datos de un tercero. Ninguna regla adivina.**

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-identity.spec.ts
import { beforeEach, describe, expect, test } from "bun:test";
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
	beforeEach(async () => {
		await db.contact.deleteMany({ where: { phone: { startsWith: "569000009" } } });
		await db.company.deleteMany({ where: { name: { startsWith: "PRUEBA INTEGRACIÓN" } } });
	});

	test("un correo corporativo identifica la empresa por dominio", async () => {
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ correo: "ana@acme-prueba.cl" })),
		);
		expect(r.ok).toBe(true);
		const empresa = await db.company.findUniqueOrThrow({
			where: { id: (r as { companyId: string }).companyId },
		});
		expect(empresa.domain).toBe("acme-prueba.cl");
	});

	test("un correo de dominio gratuito NO crea empresa por dominio", async () => {
		// domainFromEmail de upstream devuelve null para gmail y 20 más. Sin
		// esto, todos los contactos con gmail caerían en una misma "empresa".
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ correo: "ana@gmail.com" })),
		);
		expect(r.ok).toBe(true);
		const empresa = await db.company.findUniqueOrThrow({
			where: { id: (r as { companyId: string }).companyId },
		});
		expect(empresa.domain).toBeNull();
	});

	test("sin dominio NO se fusiona con una empresa que sí tiene dominio", async () => {
		// El caso que importa: un "Acme" que dijo el contacto por WhatsApp no
		// puede meterse dentro del Acme real que cargó una persona.
		const real = await db.company.create({
			data: { name: "PRUEBA INTEGRACIÓN SpA", domain: "acme-real-prueba.cl" },
		});
		const r = await db.$transaction((tx) => servicio.resolver(tx, payload()));
		expect(r.ok).toBe(true);
		expect((r as { companyId: string }).companyId).not.toBe(real.id);
	});

	test("sin dominio reusa la empresa que el mismo origen ya había creado", async () => {
		const primera = await db.$transaction((tx) => servicio.resolver(tx, payload()));
		const segunda = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ telefono: "56900000901" })),
		);
		expect((primera as { companyId: string }).companyId)
			.toBe((segunda as { companyId: string }).companyId);
	});

	test("sin dominio y sin nombre de empresa no se crea ninguna", async () => {
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ empresa: "" })),
		);
		expect(r.ok).toBe(true);
		expect((r as { companyId: string | null }).companyId).toBeNull();
	});

	test("varios contactos con el mismo teléfono es conflicto, no una adivinanza", async () => {
		await db.contact.create({ data: { firstName: "Ana", phone: "56900000902" } });
		await db.contact.create({ data: { firstName: "Otra", phone: "56900000902" } });
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({ telefono: "56900000902" })),
		);
		expect(r.ok).toBe(false);
		expect((r as { motivo: string }).motivo).toContain("teléfono");
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
		expect((r as { motivo: string }).motivo).toContain("distintos");
	});

	test("un contacto hallado por correo NO pierde el teléfono que ya tenía", async () => {
		const existente = await db.contact.create({
			data: {
				firstName: "Ana", email: "ana-fija-prueba@example.com",
				phone: "56911111111",
			},
		});
		const r = await db.$transaction((tx) =>
			servicio.resolver(tx, payload({
				correo: "ana-fija-prueba@example.com", telefono: "56900000904",
			})),
		);
		expect((r as { contactId: string }).contactId).toBe(existente.id);
		const despues = await db.contact.findUniqueOrThrow({ where: { id: existente.id } });
		expect(despues.phone).toBe("56911111111");
	});

	test("un contacto nuevo se crea con el nombre partido", async () => {
		const r = await db.$transaction((tx) => servicio.resolver(tx, payload()));
		const contacto = await db.contact.findUniqueOrThrow({
			where: { id: (r as { contactId: string }).contactId },
		});
		expect(contacto.firstName).toBe("PRUEBA");
		expect(contacto.lastName).toBe("INTEGRACIÓN Ana Pérez");
	});
});
```

- [ ] **Step 2: Correrlo**

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun test apps/api/test/ingest-identity.spec.ts
```

Expected: FAIL — no existe el módulo.

- [ ] **Step 3: Escribir el servicio**

```typescript
// apps/api/src/ingest/ingest-identity.service.ts
//
// A qué Company y a qué Contact corresponde un lead del bot.
//
// Es el punto donde una fusión equivocada cuesta datos de un tercero, así que
// ninguna regla adivina: los casos ambiguos devuelven conflicto y los resuelve
// una persona. Un conflicto deja el lead pendiente y visible en el panel del
// bot, que es lo que lo vuelve accionable en vez de perdido.
import { type Prisma, RecordSource } from "@crm/db";
import { Injectable, Logger } from "@nestjs/common";
import { domainFromEmail } from "../companies/domain";
import { normalizeEmail } from "../crm/values";
import { splitName } from "../mailbox/participants";
import type { LeadInTouchPayload } from "./ingest.contracts";

export type ResolucionIdentidad =
	| { ok: true; companyId: string | null; contactId: string }
	| { ok: false; motivo: string };

@Injectable()
export class IngestIdentityService {
	private readonly logger = new Logger(IngestIdentityService.name);

	async resolver(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
	): Promise<ResolucionIdentidad> {
		const correo = normalizeEmail(payload.correo ?? "");
		const companyId = await this.empresa(tx, payload, correo);
		const contacto = await this.contacto(tx, payload, correo, companyId);
		return contacto;
	}

	/// La empresa. Sólo el dominio corporativo es identificación fuerte.
	private async empresa(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		correo: string | null,
	): Promise<string | null> {
		const dominio = correo ? domainFromEmail(correo) : null;

		if (dominio) {
			const existente = await tx.company.findFirst({
				where: { domain: dominio, archivedAt: null },
				select: { id: true },
			});
			if (existente) return existente.id;
			const creada = await tx.company.create({
				data: {
					name: payload.empresa || dominio,
					domain: dominio,
					website: `https://${dominio}`,
					industry: payload.industria || null,
					subIndustry: payload.subtipo_automotriz || null,
				},
				select: { id: true },
			});
			return creada.id;
		}

		// Sin dominio no se fusiona por nombre. Se busca sólo entre companies
		// que TAMPOCO tengan dominio: así un "Acme" que dijo un contacto por
		// WhatsApp nunca se mete dentro del Acme real que cargó una persona.
		if (!payload.empresa) return null;

		const porNombre = await tx.company.findFirst({
			where: { name: payload.empresa, domain: null, archivedAt: null },
			select: { id: true },
		});
		if (porNombre) return porNombre.id;

		const creada = await tx.company.create({
			data: {
				name: payload.empresa,
				industry: payload.industria || null,
				subIndustry: payload.subtipo_automotriz || null,
			},
			select: { id: true },
		});
		return creada.id;
	}

	/// El contacto. `Contact.phone` no tiene índice único (el único es
	/// `@@unique([email]) where archivedAt: null`), así que la búsqueda por
	/// teléfono puede devolver varias filas.
	private async contacto(
		tx: Prisma.TransactionClient,
		payload: LeadInTouchPayload,
		correo: string | null,
		companyId: string | null,
	): Promise<ResolucionIdentidad> {
		const porTelefono = await tx.contact.findMany({
			where: { phone: payload.telefono, archivedAt: null },
			select: { id: true },
			take: 2,
		});
		if (porTelefono.length > 1) {
			return {
				ok: false,
				motivo:
					`Hay más de un contacto con el teléfono ${payload.telefono}. ` +
					"No se elige uno por adivinanza: lo resuelve una persona.",
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
				motivo:
					"El teléfono y el correo apuntan a contactos distintos " +
					`(${unoPorTelefono.id} y ${porCorreo.id}). Son dos personas o un ` +
					"dato mal cargado; fusionarlos borraría a una de las dos.",
			};
		}

		const existente = unoPorTelefono ?? porCorreo;
		const nombre = splitName(payload.nombre_completo ?? null, correo ?? "");

		if (existente) {
			// NO se sobreescribe `phone`: si el contacto ya tenía otro número,
			// pisarlo borraría el dato de una persona. El de WhatsApp va a su
			// campo dinámico (Task 7).
			await tx.contact.update({
				where: { id: existente.id },
				data: {
					// Los nativos se llenan sólo si están vacíos: no le pisamos al
					// comercial lo que corrigió a mano (spec §4).
					...(payload.cargo ? { title: undefined } : {}),
					companyId: companyId ?? undefined,
				},
			});
			return { ok: true, companyId, contactId: existente.id };
		}

		const creado = await tx.contact.create({
			data: {
				firstName: nombre.firstName,
				lastName: nombre.lastName,
				email: correo,
				phone: payload.telefono,
				title: payload.cargo || null,
				companyId,
				source: RecordSource.IMPORT,
			},
			select: { id: true },
		});
		return { ok: true, companyId, contactId: creado.id };
	}
}
```

- [ ] **Step 4: Correr los tests**

```bash
docker compose exec api bun test apps/api/test/ingest-identity.spec.ts
```

Expected: PASS, los 9.

- [ ] **Step 5: Verificar que la suite no dejó basura**

```bash
cd /home/admincrm/compai-crm && git status --short
```

Expected: sólo los archivos que se van a commitear. Un test que deja el repo sucio no se acepta.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/ingest/ingest-identity.service.ts \
        apps/api/test/ingest-identity.spec.ts
git commit -m "feat(ingest): resolucion de identidad sin adivinanzas

Reusa domainFromEmail de upstream, que ya descarta gmail y 20 proveedores
mas: sin eso todos los contactos con correo personal caerian en una misma
empresa. Sin dominio NO se fusiona por nombre, para que un 'Acme' dicho por
WhatsApp no entre en el Acme real que cargo una persona.

Dos casos devuelven conflicto en vez de elegir: varios contactos con el
mismo telefono, y telefono y correo apuntando a contactos distintos. Y un
contacto hallado por correo nunca pierde el telefono que ya tenia."
```

---

### Task 7: La escritura comercial

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/ingest/ingest-write.service.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/ingest-write.spec.ts`

**Interfaces:**
- Consumes: `ResolucionIdentidad` (Task 6); `CAMPOS_INTOUCH` (Task 4); `LeadInTouchPayload` (Task 5).
- Produces: `IngestWriteService` con `escribir(tx, payload, identidad, ownerId): Promise<{ dealId: string | null }>`, y las funciones puras `debeAbrirOportunidad(payload): boolean` y `textoDeLista(valores): string`.

- [ ] **Step 1: Escribir el test que falla**

```typescript
// apps/api/test/ingest-write.spec.ts
import { beforeEach, describe, expect, test } from "bun:test";
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
docker compose exec api bun test apps/api/test/ingest-write.spec.ts
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
import type { ResolucionIdentidad } from "./ingest-identity.service";

/// Días que se le dan a la tarea de contactar cuando alguien pidió hablar con
/// una persona. Corto a propósito: un lead que pidió contacto humano y espera
/// una semana ya se perdió.
const DIAS_PARA_CONTACTAR = 1;

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
		identidad: Extract<ResolucionIdentidad, { ok: true }>,
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
		identidad: Extract<ResolucionIdentidad, { ok: true }>,
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
				// afirmaría una demo agendada.
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
			await tx.activity.create({
				data: {
					type: ActivityType.NOTE,
					subject: `Conversación con el Asesor Comercial IA (${payload.lead_score || "sin calificar"})`,
					// Texto del contacto y del modelo: se guarda como texto y nunca
					// se renderiza como HTML.
					body: partes.join("\n\n"),
					occurredAt: new Date(),
					contactId,
					dealId,
					createdById: ownerId,
					meta: { origen: payload.origen, revision: payload.revision },
				},
			});
		}

		if (!payload.solicita_contacto_humano) return;

		const vence = new Date();
		vence.setDate(vence.getDate() + DIAS_PARA_CONTACTAR);
		await tx.activity.create({
			data: {
				type: ActivityType.TASK,
				subject: "El contacto pidió hablar con una persona",
				body:
					"Pedido explícito durante la conversación con el bot. " +
					`Teléfono de WhatsApp: ${payload.telefono}.`,
				dueAt: vence,
				contactId,
				dealId,
				createdById: ownerId,
				meta: { origen: payload.origen, revision: payload.revision },
			},
		});
	}
}
```

- [ ] **Step 4: Correr los tests**

```bash
docker compose exec api bun test apps/api/test/ingest-write.spec.ts
```

Expected: PASS, los 13.

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
import { beforeEach, describe, expect, test } from "bun:test";
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
		await db.leadIngestEvent.deleteMany({ where: { claveContacto: "d".repeat(32) } });
		await db.contact.deleteMany({ where: { phone: { startsWith: "569000009" } } });
		await db.company.deleteMany({ where: { name: { startsWith: "PRUEBA INTEGRACIÓN" } } });
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
docker compose exec api bun test apps/api/test/ingest-idempotency.spec.ts
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
				if (mismoEvento.payloadHash === hash) {
					// Reintento del mismo envío: se devuelve lo ya creado, sin escribir.
					return {
						estado: "replayed" as const,
						companyId: mismoEvento.companyId,
						contactId: mismoEvento.contactId ?? "",
						dealId: mismoEvento.dealId,
						revision: mismoEvento.revision,
						eventoId: mismoEvento.eventoId,
					};
				}
				// Mismo intento con otro contenido: nunca sobrescritura silenciosa.
				return {
					estado: "conflict" as const,
					motivo:
						`El evento ${payload.evento_id} ya se registró con otro contenido. ` +
						"Una calificación nueva tiene que llevar un evento_id nuevo.",
				};
			}

			const ultimo = await tx.leadIngestEvent.findFirst({
				where: { origin: payload.origen, claveContacto: payload.clave_contacto },
				orderBy: { revision: "desc" },
			});

			// Una revisión que no avanza llegó fuera de orden: no pisa a la nueva.
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
				// El evento se registra con su motivo para que el fallo sea
				// diagnosticable, pero ninguna fila comercial se crea. La
				// transacción no se aborta: perder el rastro del conflicto haría
				// que el lead desapareciera sin explicación.
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

			const { dealId } = await this.escritura.escribir(tx, payload, resuelta, ownerId);
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
					companyId: resuelta.companyId,
					contactId: resuelta.contactId,
					dealId,
				},
			});

			return {
				estado,
				companyId: resuelta.companyId,
				contactId: resuelta.contactId,
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
docker compose exec api bun test apps/api/test/ingest-idempotency.spec.ts
```

Expected: PASS, los 9. El de concurrencia es el que importa: si falla, el advisory lock no está serializando.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/ingest/ingest.service.ts \
        apps/api/test/ingest-idempotency.spec.ts
git commit -m "feat(ingest): idempotencia de dos claves, con orden y conflictos

Reusa lockIdempotencyKey de packages/db (advisory lock de Postgres), que ya
existia: serializa los envios concurrentes del mismo contacto en vez de
dejarlos chocar contra la restriccion unica.

Mismo evento_id con otro contenido = conflicto, nunca sobrescritura. Una
revision que no avanza = stale, para que una revision fuera de orden no
pise a la nueva. Y la etapa de una oportunidad ya abierta no se toca."
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
		expect(keyDeIngesta).not.toBe("");
		expect(keyDeOtro).not.toBe("");
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
docker compose exec api bun test apps/api/test/ingest-endpoint.spec.ts
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
	Headers, HttpCode, Logger, Post, Res,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import {
	ApiConflictResponse, ApiCreatedResponse, ApiForbiddenResponse, ApiHeader,
	ApiOkResponse, ApiOperation, ApiTags,
} from "@nestjs/swagger";
import { Session, type UserSession } from "@thallesp/nestjs-better-auth";
import type { Response } from "express";
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
		@Res({ passthrough: true }) res: Response,
	) {
		// 1. Tiene que venir por API key. Una sesión de cookie de navegador NO
		// sirve: el espejo de SessionOnlyMiddleware, para que el navegador de un
		// comercial autenticado no pueda postear leads.
		if (!apiKey) {
			throw new ForbiddenException(
				"Esta ruta se usa sólo con credencial de integración.",
			);
		}

		// 2. Y tiene que ser la key del principal de ingesta. Las keys del CRM no
		// llevan permisos: sin esto, la de cualquier usuario serviría.
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
			// 503 y no 500: es configuración faltante, y el emisor tiene que
			// reintentarlo, no descartarlo.
			throw new ConflictException({
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
		express.json({ limit: MAX_INGEST_BODY_BYTES }),
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
docker compose exec -e TEST_INGEST_KEY="<key de ingesta>" \
                    -e TEST_OTHER_USER_KEY="<key de otro usuario>" \
                    api bun test apps/api/test/ingest-endpoint.spec.ts
```

Expected: PASS, los 10. **Si el caso de 403 falla**, el alcance no está puesto y el endpoint acepta la credencial de cualquiera: es un bloqueante, no un detalle.

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

Expected: PASS, todos — los 7 nuevos y los 11 que ya estaban.

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

- [ ] **Step 9: I09 — timeout después del commit, sin duplicar**

```bash
cd /home/admincrm/wsp_intouch
# Se fuerza un fallo de red DESPUÉS de que el receptor ya escribió, cortando
# el contenedor del CRM justo después de una escritura confirmada.
docker compose exec web python manage.py shell <<'EOF'
from unittest.mock import patch
from bot.business.lead_intouch import _despachar_si_corresponde
from bot.models import LeadInTouch

lead = LeadInTouch.objects.get(conversation__wa_id="56900000099")
lead.despachado_en = None
lead.save(update_fields=["despachado_en"])

# El primer intento "se pierde" en la respuesta, con el mismo evento_id.
with patch("bot.business.lead_intouch._enviar_al_sink", return_value=None):
    _despachar_si_corresponde("56900000099")

_despachar_si_corresponde("56900000099")   # reintento real
lead.refresh_from_db()
print("estado:", lead.despachado_en, lead.crm_deal_id)
EOF
```

Y confirmar que no hay una segunda oportunidad:

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun -e '
import { db } from "@crm/db";
console.log("oportunidades:", await db.deal.count({
  where: { company: { name: "PRUEBA INTEGRACIÓN SpA" } },
}));
console.log("eventos:", await db.leadIngestEvent.count({
  where: { claveContacto: { not: "" } },
}));
'
```

Expected: `oportunidades: 1`. Si sale 2, la idempotencia no está funcionando con el emisor real.

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

- [ ] **Step 13: I16 — los logs no filtran nada**

```bash
cd /home/admincrm/wsp_intouch && docker compose logs --tail 200 web | grep -iE "x-api-key|crm_[a-z0-9]{8}|postgresql://|Traceback" || echo "OK: sin secretos ni stacks"
cd /home/admincrm/compai-crm && docker compose logs --tail 200 api | grep -iE "crm_[a-z0-9]{8}|postgresql://|password" || echo "OK: sin secretos"
```

Expected: las dos líneas `OK:`.

- [ ] **Step 14: Limpiar los datos de prueba**

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun -e '
import { db } from "@crm/db";
const c = await db.contact.findMany({ where: { phone: { startsWith: "569000000" } } });
for (const x of c) await db.contact.delete({ where: { id: x.id } });
await db.company.deleteMany({ where: { name: { startsWith: "PRUEBA INTEGRACIÓN" } } });
await db.leadIngestEvent.deleteMany({ where: { payload: { path: ["telefono"], string_starts_with: "569000000" } } });
console.log("limpio");
'
cd /home/admincrm/wsp_intouch
docker compose exec web python manage.py shell -c "
from bot.models import Conversation
Conversation.objects.filter(wa_id__startswith='569000000').delete()
print('limpio')
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

## Estado final esperado de este plan

Con las 14 tareas en verde: **VALIDADO EN PRUEBAS, PENDIENTE DE DESPLIEGUE** — la ruta `emisor del bot → persistencia correcta → oportunidad en el pipeline` comprobada con reintentos y fallos reales, y el acceso del comercial cubierto por el plan hermano.

Lo que **no** se puede declarar con esto: que el bot capture los datos por sí solo en una conversación real. Eso es el Grupo D y espera credenciales de Meta.
