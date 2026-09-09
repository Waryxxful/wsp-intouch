# Integración Comp AI CRM — el acceso del comercial · Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un comercial autorizado entre a `/crm/` desde GranCRM con su sesión de siempre, sin un segundo login, y que nadie de otra cuenta pueda entrar.

**Architecture:** El CRM no tiene login usuario/contraseña — sólo Google, Microsoft o un IdP OIDC/SAML — y el orquestador de GranCRM no es proveedor de identidad. El contrato estándar del ecosistema es la cookie `grancrm_session` con un JWT HS256, y este plan lo **porta** a TypeScript por primera vez, delegando la validación en `GET /api/session/` del orquestador para heredar la revocación de sesión que sólo él aplica. Aparte, `/crm/` se publica en el gateway con `basePath` de build y una CSP propia.

**Tech Stack:** Better Auth 1.6, NestJS 11, Next.js 16 (App Router), nginx del gateway en `network_mode: host`.

**Spec:** `docs/superpowers/specs/2026-09-09-integracion-compai-crm-design.md` (§6.2, §6.3, §6.4, §1.1)

**Plan hermano:** `docs/superpowers/plans/2026-09-09-compai-crm-ruta-del-lead.md` — **hay que ejecutarlo primero**: las Tasks 1 y 2 de ahí levantan el CRM, que es la precondición de todo esto.

## Global Constraints

- **Este plan escribe código de autenticación propio.** Va con **review de seguridad dedicada antes de habilitarlo** en el gateway, no después.
- **Un solo login.** El CRM nunca implementa el suyo: sólo consume la identidad que ya emitió DIOS.
- **No se desactiva ningún control de sesión para arreglar un login roto** (cookies `secure`, `httpOnly`, `sameSite`, ni los `trustedOrigins`).
- **`GRANCRM_JWT_SECRET` NO se distribuye al CRM.** El puente delega en el orquestador; el CRM no decodifica el JWT y por eso no necesita el secreto.
- **Se lee `rol_real`, nunca `rol`.** El modo compatibilidad de DIOS colapsa `agente` y `supervisor` en `ejecutivo`, y `admin_ti` en `sa` — es la lección que ya pagó InciTrack (commit `95d5368`).
- **El CRM es single-workspace**: `Company`, `Contact` y `Deal` no tienen `organizationId`. No aísla datos entre cuentas y no se puede presentar como si lo hiciera.
- **El contenedor que corre acá es producción.** `git add` explícito, `git status` antes de cualquier reload, y coordinar por `SendMessage` antes de recrear el gateway.
- **Todo lo que un LLM va a leer se escribe en español correcto, con tildes.**
- **El gateway se aplica con `docker compose up -d --force-recreate nginx`.** `nginx -s reload` no alcanza con este bind-mount.

---

### Task 1: Registrar el CRM en DIOS

**Files:**
- Create: `/home/admincrm/compai-crm/dios.json`
- Modify: `/home/admincrm/compai-crm/.env` (`CRM_APLICACION_ID`, `CRM_CUENTA_SLUG`, `GRANCRM_ORQUESTADOR_URL`, `CRM_ROLES_PERMITIDOS`)

**Interfaces:**
- Produces: el registro `Aplicacion` del CRM con su `id` numérico; las cuatro variables de entorno que consume la Task 2.

**Modo `iframe` y no `spa_remote`:** `spa_remote` exige exponer un `remoteEntry.js` de Module Federation, y esto es una app Next.js completa que no lo tiene. El shell la embebe.

- [ ] **Step 1: Escribir el manifiesto**

```bash
cat > /home/admincrm/compai-crm/dios.json <<'EOF'
{
  "nombre": "CRM Comercial",
  "url_interna": "http://172.20.21.249:3005",
  "url_publica": "/crm/",
  "icono": "feather-briefcase",
  "categoria": "Comercial",
  "descripcion": "Pipeline comercial: empresas, contactos y oportunidades, con los leads que califica el Asesor Comercial IA",
  "modo": "iframe",
  "slug": "crm",
  "route_prefix": "/crm/",
  "contract_version": "1"
}
EOF
```

Sin `secret`, `remote_entry_url` ni `remote_scope`: no aplican al modo `iframe`. Sin `source_host`/`source_db`/`schemas`: el CRM usa su propio Postgres y **no** participa del tenant routing de SQL Server — declararlo insinuaría que DIOS puede sincronizarle schemas.

- [ ] **Step 2: Registrarlo y anotar el id**

El registro lo hace un SA desde el panel, o el endpoint interno del orquestador. **Pedirle al usuario que confirme el alta** — es un cambio de acceso, y esta guía no lo autoriza por sí sola.

```bash
cd /home/admincrm/orquestador
docker compose exec web python manage.py shell -c "
from core.models import Aplicacion
for a in Aplicacion.objects.all().values('id', 'nombre', 'slug', 'activo'):
    print(a)
"
```

Anotar el `id` del CRM: es lo que el puente compara contra el claim `apps`.

- [ ] **Step 3: Confirmar a qué cuenta pertenece la instancia**

```bash
cd /home/admincrm/orquestador
docker compose exec web python manage.py shell -c "
from core.models import Cuenta
for c in Cuenta.objects.filter(estado='activo').values('slug', 'nombre', 'db_name'):
    print(c)
"
```

**Dato bloqueante:** cuál de esos `slug` es el dueño del CRM. Es una decisión de negocio, no técnica: define quién entra.

- [ ] **Step 4: Guardar la configuración**

En `/home/admincrm/compai-crm/.env`:

```bash
GRANCRM_ORQUESTADOR_URL="http://172.20.21.249:9000"
CRM_APLICACION_ID="<el id del paso 2>"
CRM_CUENTA_SLUG="<el slug del paso 3>"
# Roles de rol_real que pueden entrar. Coma separada, sin espacios.
CRM_ROLES_PERMITIDOS="admin_cuenta,supervisor,agente,admin_ti"
# Si un SA en modo "ver como" puede entrar. Entra, porque asi funciona el
# soporte, pero nunca es dueno de una oportunidad y queda registrado.
CRM_PERMITE_VER_COMO="true"
```

- [ ] **Step 5: Commit**

```bash
cd /home/admincrm/compai-crm
git add dios.json
git commit -m "feat(auth): manifiesto de DIOS para el CRM, en modo iframe

spa_remote exigiria exponer un remoteEntry.js de Module Federation, y esto
es una app Next.js completa que no lo tiene. Sin source_db ni schemas: el
CRM usa su propio Postgres y no participa del tenant routing de SQL Server."
```

---

### Task 2: El puente de sesión

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/grancrm/grancrm-session.service.ts`
- Create: `/home/admincrm/compai-crm/apps/api/src/grancrm/grancrm.controller.ts`
- Create: `/home/admincrm/compai-crm/apps/api/src/grancrm/grancrm.module.ts`
- Modify: `/home/admincrm/compai-crm/apps/api/src/app.module.ts`
- Modify: `/home/admincrm/compai-crm/apps/api/src/config/env.validation.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/grancrm-bridge.spec.ts`

**Interfaces:**
- Consumes: las variables de la Task 1.
- Produces: `GET /api/grancrm/entrar` (establece la sesión y redirige a la app); `GranCRMSessionService.identidad(cookie): Promise<Identidad | RechazoAcceso>`; `type Identidad = { email: string; nombre: string; rolReal: string; tenantId: string; verComoSa: boolean }`.

**Por qué delegar y no decodificar:** verificado en el orquestador — `core/decorators.py` chequea `SesionActiva` por `jti`, pero `decode_token()` no. Un satélite que decodifica el JWT localmente **no tiene revocación**: el token de alguien que cerró sesión sigue válido hasta 8 horas. Delegando en `/api/session/` eso se hereda, y el CRM no necesita el secreto.

- [ ] **Step 1: Escribir los tests que fallan**

```typescript
// apps/api/test/grancrm-bridge.spec.ts
import { beforeEach, describe, expect, mock, test } from "bun:test";
import { GranCRMSessionService } from "../src/grancrm/grancrm-session.service";

const CONFIG = {
	GRANCRM_ORQUESTADOR_URL: "http://orquestador:9000",
	CRM_APLICACION_ID: "7",
	CRM_CUENTA_SLUG: "qaintouch",
	CRM_ROLES_PERMITIDOS: "admin_cuenta,supervisor,agente",
	CRM_PERMITE_VER_COMO: "true",
} as Record<string, string>;

function servicio(over: Record<string, string> = {}) {
	const config = { get: (k: string) => ({ ...CONFIG, ...over })[k] };
	return new GranCRMSessionService(config as never);
}

function sesionDeDios(over: Record<string, unknown> = {}) {
	return {
		ok: true,
		json: async () => ({
			user_id: 42,
			email: "ana@in-touchcrm.cl",
			nombre: "Ana Pérez",
			rol: "ejecutivo",
			rol_real: "agente",
			tenant_id: "qaintouch",
			apps: [3, 7, 9],
			...over,
		}),
	};
}

describe("puente de sesión de GranCRM", () => {
	beforeEach(() => {
		globalThis.fetch = mock(async () => sesionDeDios()) as never;
	});

	test("una sesión válida de la cuenta correcta entra", async () => {
		const r = await servicio().identidad("token-de-prueba");
		expect(r.ok).toBe(true);
		expect((r as { email: string }).email).toBe("ana@in-touchcrm.cl");
	});

	test("lee rol_real y no rol", async () => {
		// El modo compatibilidad de DIOS colapsa agente y supervisor en
		// "ejecutivo": mirar `rol` perdería la distinción que la lista blanca
		// necesita, y es el bug que ya pagó InciTrack.
		const r = await servicio().identidad("t");
		expect((r as { rolReal: string }).rolReal).toBe("agente");
	});

	test("sin cookie no entra", async () => {
		const r = await servicio().identidad(undefined);
		expect(r.ok).toBe(false);
	});

	test("si el orquestador rechaza la cookie, no entra", async () => {
		globalThis.fetch = mock(async () => ({ ok: false, status: 401 })) as never;
		const r = await servicio().identidad("token-revocado");
		expect(r.ok).toBe(false);
	});

	test("un usuario de OTRA cuenta no entra", async () => {
		// El chequeo que faltaba. El CRM es single-workspace: si entra alguien
		// de otra cuenta, ve los mismos datos comerciales que todos.
		globalThis.fetch = mock(async () =>
			sesionDeDios({ tenant_id: "otra-cuenta" })) as never;
		const r = await servicio().identidad("t");
		expect(r.ok).toBe(false);
		expect((r as { motivo: string }).motivo).toContain("cuenta");
	});

	test("un usuario sin la app habilitada no entra", async () => {
		globalThis.fetch = mock(async () => sesionDeDios({ apps: [3, 9] })) as never;
		const r = await servicio().identidad("t");
		expect(r.ok).toBe(false);
		expect((r as { motivo: string }).motivo).toContain("aplicación");
	});

	test("un rol fuera de la lista blanca no entra", async () => {
		globalThis.fetch = mock(async () =>
			sesionDeDios({ rol_real: "invitado" })) as never;
		const r = await servicio().identidad("t");
		expect(r.ok).toBe(false);
		expect((r as { motivo: string }).motivo).toContain("rol");
	});

	test("un SA en modo ver-como entra y queda marcado", async () => {
		globalThis.fetch = mock(async () =>
			sesionDeDios({ view_as_sa: true, rol_real: "admin_cuenta" })) as never;
		const r = await servicio().identidad("t");
		expect(r.ok).toBe(true);
		expect((r as { verComoSa: boolean }).verComoSa).toBe(true);
	});

	test("con ver-como deshabilitado, un SA impersonando no entra", async () => {
		globalThis.fetch = mock(async () =>
			sesionDeDios({ view_as_sa: true })) as never;
		const r = await servicio({ CRM_PERMITE_VER_COMO: "false" }).identidad("t");
		expect(r.ok).toBe(false);
	});

	test("si falta la configuración de cuenta, NO entra nadie", async () => {
		// Falla cerrado: una variable vacía no puede convertirse en "cualquier
		// cuenta sirve".
		const r = await servicio({ CRM_CUENTA_SLUG: "" }).identidad("t");
		expect(r.ok).toBe(false);
	});

	test("si el orquestador no responde, no entra", async () => {
		globalThis.fetch = mock(async () => {
			throw new Error("sin red");
		}) as never;
		const r = await servicio().identidad("t");
		expect(r.ok).toBe(false);
	});
});
```

- [ ] **Step 2: Correrlos**

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun test apps/api/test/grancrm-bridge.spec.ts
```

Expected: FAIL — no existe el módulo.

- [ ] **Step 3: Escribir el servicio**

```typescript
// apps/api/src/grancrm/grancrm-session.service.ts
//
// Quién es el usuario que llega desde GranCRM, y si puede entrar.
//
// POR QUÉ ESTO EXISTE: el CRM no tiene login usuario/contraseña -- sólo Google,
// Microsoft o un IdP OIDC/SAML -- y el orquestador de GranCRM no es proveedor
// de identidad (no hay OIDC, OAuth ni SAML en su código). El contrato estándar
// del ecosistema es la cookie `grancrm_session` con un JWT HS256, y esto lo
// consume por primera vez desde TypeScript.
//
// POR QUÉ DELEGA EN /api/session/ EN VEZ DE DECODIFICAR EL JWT: la revocación
// de sesión sólo la aplica el orquestador (`core/decorators.py` chequea
// `SesionActiva` por `jti`; `decode_token()` no). Un satélite que decodifica
// localmente deja válido 8 horas el token de alguien que ya cerró sesión.
// Delegar hereda la revocación, y además el CRM nunca necesita el secreto.
import { Injectable, Logger } from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import type { EnvironmentVariables } from "../config/env.validation";

export type Identidad = {
	ok: true;
	email: string;
	nombre: string;
	rolReal: string;
	tenantId: string;
	verComoSa: boolean;
};

export type RechazoAcceso = { ok: false; motivo: string };

/// Cuánto se espera al orquestador. Corto: es una llamada en el camino de un
/// login, y un usuario esperando 30 segundos ya se fue.
const TIMEOUT_MS = 5_000;

@Injectable()
export class GranCRMSessionService {
	private readonly logger = new Logger(GranCRMSessionService.name);

	constructor(
		private readonly config: ConfigService<EnvironmentVariables, true>,
	) {}

	async identidad(cookie: string | undefined): Promise<Identidad | RechazoAcceso> {
		if (!cookie) return { ok: false, motivo: "No hay sesión de GranCRM." };

		const base = this.config.get("GRANCRM_ORQUESTADOR_URL", { infer: true });
		const cuentaEsperada = this.config.get("CRM_CUENTA_SLUG", { infer: true });
		const appId = this.config.get("CRM_APLICACION_ID", { infer: true });
		const roles = (this.config.get("CRM_ROLES_PERMITIDOS", { infer: true }) ?? "")
			.split(",")
			.map((r) => r.trim())
			.filter(Boolean);

		// Falla cerrado: una variable vacía NO se convierte en "cualquiera pasa".
		if (!base || !cuentaEsperada || !appId || roles.length === 0) {
			this.logger.error({
				message: "El puente de GranCRM está sin configurar: no entra nadie",
			});
			return { ok: false, motivo: "El acceso no está configurado." };
		}

		let datos: Record<string, unknown>;
		try {
			const resp = await fetch(`${base}/api/session/`, {
				headers: { Cookie: `grancrm_session=${cookie}` },
				signal: AbortSignal.timeout(TIMEOUT_MS),
			});
			if (!resp.ok) {
				// 401/403 del orquestador: la cookie es inválida, expiró o la
				// sesión fue revocada.
				return { ok: false, motivo: "La sesión de GranCRM no es válida." };
			}
			datos = (await resp.json()) as Record<string, unknown>;
		} catch (causa) {
			this.logger.warn({
				message: "No pude consultar la sesión al orquestador",
				causa: causa instanceof Error ? causa.message : String(causa),
			});
			return { ok: false, motivo: "No se pudo verificar la sesión." };
		}

		// 1. La cuenta. Es el chequeo que hace que un CRM single-workspace no
		// admita a otras cuentas del ecosistema: sin esto, cualquiera con la app
		// habilitada vería los mismos datos comerciales.
		if (datos.tenant_id !== cuentaEsperada) {
			this.logger.warn({
				message: "Acceso rechazado: cuenta ajena",
				tenantId: String(datos.tenant_id),
			});
			return {
				ok: false,
				motivo: "Este CRM pertenece a otra cuenta de GranCRM.",
			};
		}

		// 2. La aplicación habilitada para ese usuario.
		const apps = Array.isArray(datos.apps) ? datos.apps.map(Number) : [];
		if (!apps.includes(Number(appId))) {
			return { ok: false, motivo: "No tenés esta aplicación habilitada." };
		}

		// 3. El rol. Se lee `rol_real` y NO `rol`: el modo compatibilidad de DIOS
		// colapsa agente y supervisor en "ejecutivo", y admin_ti en "sa".
		const rolReal = String(datos.rol_real ?? datos.rol ?? "");
		if (!roles.includes(rolReal)) {
			return { ok: false, motivo: `El rol ${rolReal} no tiene acceso al CRM.` };
		}

		const verComoSa = Boolean(datos.view_as_sa);
		const permiteVerComo =
			this.config.get("CRM_PERMITE_VER_COMO", { infer: true }) === "true";
		if (verComoSa && !permiteVerComo) {
			return { ok: false, motivo: "El modo ver-como no tiene acceso al CRM." };
		}
		if (verComoSa) {
			this.logger.log({
				message: "Entrada en modo ver-como (impersonación de SA)",
				email: String(datos.email),
			});
		}

		return {
			ok: true,
			email: String(datos.email),
			nombre: String(datos.nombre ?? datos.email),
			rolReal,
			tenantId: String(datos.tenant_id),
			verComoSa,
		};
	}
}
```

- [ ] **Step 4: Correr los tests**

```bash
docker compose exec api bun test apps/api/test/grancrm-bridge.spec.ts
```

Expected: PASS, los 11.

- [ ] **Step 5: Escribir el controlador que establece la sesión**

```typescript
// apps/api/src/grancrm/grancrm.controller.ts
//
// La puerta de entrada desde el shell de GranCRM: valida la sesión de DIOS y
// crea la de Better Auth.
//
// `ALLOWED_SIGN_IN` sigue actuando como segunda puerta: este puente dice quién
// es y si su cuenta y rol corresponden, y esa variable dice qué dominios
// pueden existir como usuarios del CRM. Las dos tienen que dar el sí.
import { auth } from "@crm/auth";
import type { Db } from "@crm/db";
import {
	Controller, ForbiddenException, Get, Logger, Req, Res,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import { ApiExcludeEndpoint } from "@nestjs/swagger";
import { AllowAnonymous } from "@thallesp/nestjs-better-auth";
import type { Request, Response } from "express";
import type { EnvironmentVariables } from "../config/env.validation";
import { InjectDatabase } from "../database/database.constants";
import { GranCRMSessionService } from "./grancrm-session.service";

@Controller("api/grancrm")
export class GranCRMController {
	private readonly logger = new Logger(GranCRMController.name);

	constructor(
		@InjectDatabase() private readonly db: Db,
		private readonly puente: GranCRMSessionService,
		private readonly config: ConfigService<EnvironmentVariables, true>,
	) {}

	/// Entra al CRM con la sesión de GranCRM. `@AllowAnonymous` a propósito:
	/// es justamente la ruta que crea la sesión, así que no puede exigir una.
	@Get("entrar")
	@AllowAnonymous()
	@ApiExcludeEndpoint()
	async entrar(@Req() req: Request, @Res() res: Response) {
		const identidad = await this.puente.identidad(req.cookies?.grancrm_session);
		if (!identidad.ok) {
			this.logger.warn({ message: "Entrada rechazada", motivo: identidad.motivo });
			throw new ForbiddenException(identidad.motivo);
		}

		// El usuario del CRM se crea la primera vez y se reusa después. El
		// `emailVerified` viene de que DIOS ya autenticó a esta persona.
		const usuario = await this.db.user.upsert({
			where: { email: identidad.email },
			update: { name: identidad.nombre },
			create: {
				id: crypto.randomUUID(),
				email: identidad.email,
				name: identidad.nombre,
				emailVerified: true,
			},
			select: { id: true },
		});

		// La sesión de Better Auth, con sus propias cookies y su cookieCache:
		// desde acá el CRM funciona con su mecanismo de siempre, y este puente
		// no vuelve a intervenir hasta el próximo login.
		const { headers } = await auth.api.signInWithoutPassword({
			body: { userId: usuario.id },
			returnHeaders: true,
		});
		for (const [clave, valor] of headers.entries()) {
			if (clave.toLowerCase() === "set-cookie") res.append("Set-Cookie", valor);
		}

		const appUrl = this.config.get("APP_URL", { infer: true }) ?? "/crm/";
		res.redirect(appUrl.split(",")[0]);
	}
}
```

**Antes de escribir esto**: confirmar en la versión instalada de Better Auth cómo se crea una sesión de servidor sin contraseña. `signInWithoutPassword` es el nombre que usa la documentación de Better Auth para ese flujo, pero **hay que verificarlo contra el paquete instalado** — si no existe, la alternativa es `auth.api.signInEmail` con un plugin de sesión propia, y eso cambia el diseño:

```bash
cd /home/admincrm/compai-crm
docker compose exec api bun -e '
import { auth } from "@crm/auth";
console.log(Object.keys(auth.api).filter(k => /sign|session/i.test(k)).sort());
'
```

Si no aparece un método que cree sesión por `userId`, **parar y reportar**: es una decisión de diseño, no algo que se resuelva escribiendo la cookie a mano.

- [ ] **Step 6: Escribir el módulo y registrarlo**

```typescript
// apps/api/src/grancrm/grancrm.module.ts
import { Module } from "@nestjs/common";
import { GranCRMController } from "./grancrm.controller";
import { GranCRMSessionService } from "./grancrm-session.service";

@Module({
	controllers: [GranCRMController],
	providers: [GranCRMSessionService],
	exports: [GranCRMSessionService],
})
export class GranCRMModule {}
```

Sumarlo a `imports` en `app.module.ts`, y agregar al esquema de `env.validation.ts`:

```typescript
GRANCRM_ORQUESTADOR_URL: z.string().url().optional(),
CRM_APLICACION_ID: z.string().optional(),
CRM_CUENTA_SLUG: z.string().optional(),
CRM_ROLES_PERMITIDOS: z.string().optional(),
CRM_PERMITE_VER_COMO: z.enum(["true", "false"]).default("false"),
```

`cookie-parser` tiene que estar en el stack para que `req.cookies` exista; verificar en `create-app.ts` y agregarlo si falta.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/grancrm/ apps/api/src/app.module.ts \
        apps/api/src/config/env.validation.ts \
        apps/api/test/grancrm-bridge.spec.ts
git commit -m "feat(auth): puente de sesion desde GranCRM

El CRM no tiene login usuario/contrasena y el orquestador no es proveedor
OIDC: el contrato estandar del ecosistema es la cookie grancrm_session, y
esto lo consume por primera vez desde TypeScript.

Delega en GET /api/session/ del orquestador en vez de decodificar el JWT:
la revocacion solo la aplica el orquestador (decode_token no chequea
SesionActiva), asi que decodificar localmente dejaria valido 8 horas el
token de alguien que cerro sesion. Y el CRM no necesita el secreto.

Tres chequeos de alcance: cuenta (que faltaba, y es lo que evita que un
CRM single-workspace admita otras cuentas), aplicacion habilitada y rol
-- leyendo rol_real, porque el modo compatibilidad colapsa agente y
supervisor en ejecutivo. Falla cerrado si falta configuracion."
```

---

### Task 3: Publicar `/crm/` en el gateway

**Files:**
- Modify: `/home/admincrm/compai-crm/apps/app/next.config.ts`
- Modify: `/home/admincrm/gateway/nginx.conf`

**Interfaces:**
- Consumes: el puente de la Task 2; `/crm-api/` que ya publicó el plan hermano.
- Produces: `https://<host>/crm/` sirviendo el CRM con assets, API y login funcionando.

- [ ] **Step 1: Poner `basePath` en el build de Next**

En `apps/app/next.config.ts`, agregar al objeto `nextConfig`:

```typescript
	// El CRM se sirve bajo /crm/ en el gateway. basePath es BUILD-TIME: un
	// location de nginx no reescribe assets, enlaces ni callbacks, así que sin
	// esto la página carga y los .js dan 404. Y NEXT_PUBLIC_API_URL queda
	// horneado en el bundle: cambiar la URL pública exige rebuild.
	basePath: "/crm",
	assetPrefix: "/crm",
```

- [ ] **Step 2: Rebuildear la app con las URLs públicas**

```bash
cd /home/admincrm/compai-crm
docker compose build --build-arg APP_URL=https://172.20.21.249/crm \
                     --build-arg API_URL=https://172.20.21.249/crm-api app
docker compose up -d app
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3005/crm/
```

Expected: `200` o `307` (redirect al login), no `404`.

- [ ] **Step 3: Agregar el bloque al gateway**

En `/home/admincrm/gateway/nginx.conf`, **después** del bloque `/crm-api/` (prefijo más largo primero):

```nginx
    # CRM Comercial (Next.js). Bloque de CSP PROPIO y no heredado: nginx no
    # mergea add_header entre niveles, y el App Router de Next inyecta scripts
    # inline que la CSP global (script-src 'self' sin unsafe-inline) bloquea.
    # El resto de las cabeceras se repite acá por el mismo motivo.
    location /crm/ {
      proxy_pass http://127.0.0.1:3005;
      proxy_hide_header X-Frame-Options;
      add_header X-Content-Type-Options "nosniff" always;
      add_header Referrer-Policy "strict-origin-when-cross-origin" always;
      add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
      add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: blob:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'self'" always;
      add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
    }
```

`proxy_pass` **sin** barra final: preserva el prefijo `/crm/`, que es lo que el `basePath` del build espera. Con barra final lo quitaría y todas las rutas darían 404.

- [ ] **Step 4: Aplicar y verificar**

```bash
cd /home/admincrm/gateway
git status --short                          # coordinar antes
docker compose exec nginx nginx -t
docker compose up -d --force-recreate nginx
```

- [ ] **Step 5: Commit**

```bash
cd /home/admincrm/compai-crm
git add apps/app/next.config.ts
git commit -m "feat(app): basePath /crm para servirlo detras del gateway"

cd /home/admincrm/gateway
git add nginx.conf
git commit -m "feat(gateway): publicar el CRM en /crm/ con CSP propia

La CSP global es script-src 'self' sin unsafe-inline y el App Router de
Next inyecta scripts inline: sin un bloque propio la app carga en blanco.
Va con todas las cabeceras repetidas porque nginx no mergea add_header
entre niveles. proxy_pass sin barra final, para preservar el prefijo que
el basePath del build espera."
```

---

### Task 4: Probar el acceso en el navegador

**Files:**
- Modify: `/home/admincrm/wsp_intouch/docs/EVIDENCIA_INTEGRACION_CRM.md` (grupo B)

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: la evidencia de I13 y I14.

**Esto no se puede verificar con `curl` solo**: hay que mirar cookies y redirects, no el HTML inicial.

- [ ] **Step 1: I13 — el recorrido completo**

Con un usuario real de la cuenta configurada, en el navegador:

1. Entrar a `https://172.20.21.249/` y autenticarse en GranCRM.
2. Abrir el CRM desde el menú del shell.
3. **Recargar una ruta profunda** (por ejemplo el detalle de un contacto) — es donde se cae un `basePath` mal puesto.
4. Confirmar en las herramientas de desarrollo que **ningún** `.js`, `.css` o fuente da 404.
5. Confirmar que las llamadas a `/crm-api/` responden 200 y no 401.
6. Cerrar sesión en GranCRM y confirmar que el CRM ya no deja entrar.

Registrar para cada paso: esperado, observado, PASS/FAIL.

- [ ] **Step 2: I14 — una cuenta ajena no entra**

```bash
# Con la cookie de un usuario de OTRA cuenta de GranCRM:
curl -sk -o /dev/null -w '%{http_code}\n' \
  -H "Cookie: grancrm_session=<token de otra cuenta>" \
  https://172.20.21.249/crm-api/api/grancrm/entrar
```

Expected: `403`.

**Y registrar el límite con precisión**: lo que esto prueba es **quién logra crear sesión**, no que un usuario vea menos datos. El CRM es single-workspace — `Company`, `Contact` y `Deal` no tienen `organizationId` — así que dentro del CRM no hay aislamiento entre cuentas. Escribirlo como "acceso denegado según el modelo real de autorización" y no como aislamiento multi-tenant.

- [ ] **Step 3: Probar que la revocación funciona de verdad**

Es el beneficio concreto de haber delegado en el orquestador, y hay que comprobarlo:

```bash
# 1. Con una sesión válida, anotar el jti.
# 2. Cerrar sesión en GranCRM (o borrar la SesionActiva desde el orquestador).
cd /home/admincrm/orquestador
docker compose exec web python manage.py shell -c "
from core.models import SesionActiva
print(SesionActiva.objects.filter(jti='<el jti>').delete())
"
# 3. Reintentar la entrada con la MISMA cookie.
curl -sk -o /dev/null -w '%{http_code}\n' \
  -H "Cookie: grancrm_session=<la misma cookie>" \
  https://172.20.21.249/crm-api/api/grancrm/entrar
```

Expected: `403`. Un satélite que decodificara el JWT localmente daría 200 acá hasta 8 horas después.

- [ ] **Step 4: Pedir la review de seguridad**

Antes de dejar esto habilitado para usuarios reales: **review de seguridad dedicada del puente** (Global Constraints). Es código de autenticación propio, y las tres cosas a mirar son la validación de la cookie, el manejo de `Set-Cookie` en el redirect, y que los tres chequeos de alcance no se puedan saltar con configuración vacía.

- [ ] **Step 5: Commit de la evidencia**

```bash
cd /home/admincrm/wsp_intouch
git add docs/EVIDENCIA_INTEGRACION_CRM.md
git commit -m "docs: evidencia del acceso del comercial (I13, I14, revocacion)

I14 queda registrada por lo que realmente prueba: quien logra crear
sesion. El CRM es single-workspace y no aisla datos entre cuentas, asi
que presentarlo como aislamiento multi-tenant seria falso.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Estado final esperado de este plan

Con las 4 tareas en verde y la review de seguridad hecha: el comercial entra a `/crm/` desde GranCRM con un solo login, un usuario de otra cuenta recibe 403, y una sesión revocada deja de servir de inmediato.

Lo que **no** cambia con esto: el CRM sigue siendo single-workspace. Si alguna vez hacen falta varios clientes aislados en una sola instancia, es un trabajo aparte que hay que decidir antes de implementarlo.
