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
- **La autorización se revalida en cada petición protegida, no sólo al entrar.** Validar únicamente en la puerta deja viva la cookie de Better Auth después del logout de GranCRM o de retirarle el rol al usuario. Sin caché positiva entre peticiones; se puede deduplicar dentro de una misma petición.
- **`basePath` sí, `assetPrefix` no.** `assetPrefix` es para servir assets desde un CDN, no para montar la app en una subruta: `basePath` ya prefija los assets, y poner los dos puede duplicar el prefijo.
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
- Produces: `GET /api/grancrm/entrar` (establece la sesión y redirige a la app); `GranCRMSessionService.identidad(cookie): Promise<Identidad | RechazoAcceso>`; `type Identidad = { ok: true; granUserId: string; jti: string; email: string; nombre: string; rolReal: string; tenantId: string; expiraEn: Date; verComoSa: boolean }`. El modelo `GranCRMLink` lo crea la Task 3, así que **esta tarea se implementa después de su migración** o el `upsert` del vínculo no compila.

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
	granUserId: string;
	jti: string;
	email: string;
	nombre: string;
	rolReal: string;
	tenantId: string;
	expiraEn: Date;
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

		// `granUserId` y `jti` son lo que permite revalidar después (Task 3): sin
		// un identificador estable del usuario y de su sesión, no hay forma de
		// comprobar que la cookie que llega es la de esta sesión y no otra.
		if (!datos.user_id || !datos.jti) {
			this.logger.error({
				message: "La respuesta del orquestador no trae user_id o jti",
			});
			return { ok: false, motivo: "No se pudo identificar la sesión de origen." };
		}

		return {
			ok: true,
			granUserId: String(datos.user_id),
			jti: String(datos.jti),
			email: String(datos.email),
			nombre: String(datos.nombre ?? datos.email),
			rolReal,
			tenantId: String(datos.tenant_id),
			// El vencimiento del vínculo no puede pasar del de la sesión de
			// origen. Si el orquestador no lo informa, se usa su ventana de 8 h
			// (JWT_EXPIRY_HOURS de core/jwt_utils.py) y queda anotado como
			// supuesto a confirmar en A0.
			expiraEn: datos.exp
				? new Date(Number(datos.exp) * 1000)
				: new Date(Date.now() + 8 * 3_600_000),
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

		// EL VÍNCULO ES LA CLAVE DE CONFIANZA, NO EL CORREO. Un upsert por
		// `email` permitiría tomar una cuenta existente del CRM con sólo crear
		// un usuario de GranCRM que tenga ese mismo mail: el correo es un
		// atributo, no una identidad verificada.
		const vinculo = await this.db.granCRMLink.findUnique({
			where: {
				cuentaSlug_granUserId: {
					cuentaSlug: identidad.tenantId,
					granUserId: identidad.granUserId,
				},
			},
			select: { userId: true },
		});

		let usuarioId: string;
		if (vinculo) {
			usuarioId = vinculo.userId;
			await this.db.user.update({
				where: { id: usuarioId },
				data: { name: identidad.nombre },
			});
		} else {
			// Primera entrada de esta persona. Si YA existe un usuario local con
			// ese correo y sin vínculo, NO se adopta en silencio: puede ser una
			// cuenta creada por otra vía, y vincularla le entregaría sus datos a
			// quien controle ese mail en GranCRM.
			const homonimo = await this.db.user.findUnique({
				where: { email: identidad.email },
				select: { id: true },
			});
			if (homonimo) {
				this.logger.warn({
					message: "Correo ya usado por un usuario local sin vínculo de GranCRM",
					granUserId: identidad.granUserId,
				});
				throw new ForbiddenException(
					"Ya existe una cuenta local con ese correo. Un administrador tiene " +
						"que vincularla antes de poder entrar por GranCRM.",
				);
			}
			const creado = await this.db.user.create({
				data: {
					id: crypto.randomUUID(),
					email: identidad.email,
					name: identidad.nombre,
					// NO se afirma que el correo está verificado: GranCRM autenticó
					// a la persona, no comprobó que sea dueña de esa dirección. Y
					// `emailVerified` habilita flujos de recuperación en el CRM.
					emailVerified: false,
				},
				select: { id: true },
			});
			usuarioId = creado.id;
		}

		// El vínculo se crea o se refresca con la sesión de origen: es lo que la
		// Task 3 usa para revalidar. `expiraEn` nunca supera al origen.
		await this.db.granCRMLink.upsert({
			where: { userId: usuarioId },
			update: {
				granSesionJti: identidad.jti,
				cuentaSlug: identidad.tenantId,
				expiraEn: identidad.expiraEn,
			},
			create: {
				userId: usuarioId,
				granUserId: identidad.granUserId,
				cuentaSlug: identidad.tenantId,
				granSesionJti: identidad.jti,
				expiraEn: identidad.expiraEn,
			},
		});

		// La sesión de Better Auth, con sus propias cookies y su cookieCache:
		// desde acá el CRM funciona con su mecanismo de siempre, y este puente
		// no vuelve a intervenir hasta el próximo login.
		const { headers } = await auth.api.signInWithoutPassword({
			body: { userId: usuarioId },
			returnHeaders: true,
		});
		// `headers.entries()` COLAPSA los Set-Cookie múltiples en un solo valor
		// separado por comas, y así el navegador descarta todas menos una. Better
		// Auth manda varias (sesión + csrf según configuración). `getSetCookie()`
		// las devuelve como lista.
		for (const cookie of headers.getSetCookie()) {
			res.append("Set-Cookie", cookie);
		}

		// Destino FIJO y local. Si más adelante se admite volver a una ficha, el
		// parámetro se valida contra este prefijo y el mismo origen -- nunca se
		// redirige a una URL que venga de la petición, ni a variantes `//host`.
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

### Task 3: Mantener la autorización durante toda la sesión

**Files:**
- Create: `/home/admincrm/compai-crm/apps/api/src/grancrm/grancrm-authority.guard.ts`
- Create: `/home/admincrm/compai-crm/apps/api/src/grancrm/grancrm-link.service.ts`
- Modify: `/home/admincrm/compai-crm/packages/db/prisma/schema.prisma` (modelo `GranCRMLink`)
- Modify: `/home/admincrm/compai-crm/apps/api/src/trpc/middlewares/auth.middleware.ts`
- Create: `/home/admincrm/compai-crm/apps/api/test/grancrm-authority.spec.ts`

**Interfaces:**
- Consumes: `GranCRMSessionService` (Task 2).
- Produces: modelo `GranCRMLink(userId, cuentaSlug, granSesionJti, expiraEn)`; `GranCRMAuthorityGuard`; `GranCRMLinkService.vigente(userId, cookie): Promise<boolean>`.

**Ésta es la corrección central del plan anterior, y es un agujero de
seguridad real:** yo validaba la sesión de GranCRM **sólo al entrar**. Después,
el CRM funcionaba con su propia cookie de Better Auth — que dura lo que dure su
sesión. Consecuencia: un comercial que cierra sesión en GranCRM, o al que le
retiran el rol o la cuenta, **sigue entrando al CRM** con la cookie que ya
tiene, hasta que expire.

- [ ] **Step 1: Escribir los tests que fallan**

```typescript
// apps/api/test/grancrm-authority.spec.ts
import { beforeEach, describe, expect, mock, test } from "bun:test";
import { db } from "@crm/db";
import { GranCRMLinkService } from "../src/grancrm/grancrm-link.service";
import { GranCRMSessionService } from "../src/grancrm/grancrm-session.service";

const CONFIG: Record<string, string> = {
	GRANCRM_ORQUESTADOR_URL: "http://orquestador:9000",
	CRM_APLICACION_ID: "7",
	CRM_CUENTA_SLUG: "qaintouch",
	CRM_ROLES_PERMITIDOS: "admin_cuenta,supervisor,agente",
	CRM_PERMITE_VER_COMO: "false",
};

function servicios() {
	const config = { get: (k: string) => CONFIG[k] };
	const puente = new GranCRMSessionService(config as never);
	return new GranCRMLinkService(db, puente);
}

function respuestaDios(over: Record<string, unknown> = {}) {
	return {
		ok: true,
		json: async () => ({
			user_id: 42, jti: "sesion-1", email: "ana@in-touchcrm.cl",
			nombre: "Ana", rol: "ejecutivo", rol_real: "agente",
			tenant_id: "qaintouch", apps: [3, 7], ...over,
		}),
	};
}

let userId: string;

describe("autorización vigente", () => {
	beforeEach(async () => {
		const u = await db.user.upsert({
			where: { email: "ana@in-touchcrm.cl" }, update: {},
			create: {
				id: crypto.randomUUID(), name: "Ana",
				email: "ana@in-touchcrm.cl", emailVerified: false,
			},
			select: { id: true },
		});
		userId = u.id;
		await db.granCRMLink.deleteMany({ where: { userId } });
		await db.granCRMLink.create({
			data: {
				userId, cuentaSlug: "qaintouch", granSesionJti: "sesion-1",
				expiraEn: new Date(Date.now() + 3_600_000),
			},
		});
		globalThis.fetch = mock(async () => respuestaDios()) as never;
	});

	test("con sesión de GranCRM vigente, autoriza", async () => {
		expect(await servicios().vigente(userId, "cookie-buena")).toBe(true);
	});

	test("tras el LOGOUT de GranCRM, la cookie del CRM ya no autoriza", async () => {
		// El agujero que esto tapa. El orquestador rechaza la cookie porque
		// borró su SesionActiva; el CRM tiene que denegar en la siguiente
		// petición, no seguir andando con su propia sesión.
		globalThis.fetch = mock(async () => ({ ok: false, status: 401 })) as never;
		expect(await servicios().vigente(userId, "cookie-vieja")).toBe(false);
	});

	test("si le retiran el rol con el CRM abierto, deniega", async () => {
		globalThis.fetch = mock(async () => respuestaDios({ rol_real: "invitado" })) as never;
		expect(await servicios().vigente(userId, "c")).toBe(false);
	});

	test("si le retiran la app, deniega", async () => {
		globalThis.fetch = mock(async () => respuestaDios({ apps: [3] })) as never;
		expect(await servicios().vigente(userId, "c")).toBe(false);
	});

	test("si cambia de cuenta, deniega", async () => {
		globalThis.fetch = mock(async () => respuestaDios({ tenant_id: "otra" })) as never;
		expect(await servicios().vigente(userId, "c")).toBe(false);
	});

	test("una cookie de OTRA identidad no opera con el vínculo de esta", async () => {
		// Fijación de identidad: la sesión del CRM es de Ana, pero llega la
		// cookie de otra persona igualmente válida en GranCRM.
		globalThis.fetch = mock(async () =>
			respuestaDios({ user_id: 99, jti: "sesion-9", email: "otro@in-touchcrm.cl" })) as never;
		expect(await servicios().vigente(userId, "cookie-de-otro")).toBe(false);
	});

	test("sin cookie de GranCRM, deniega aunque la sesión local exista", async () => {
		expect(await servicios().vigente(userId, undefined)).toBe(false);
	});

	test("si el orquestador se cae, deniega y NO cae en la sesión local", async () => {
		globalThis.fetch = mock(async () => {
			throw new Error("sin red");
		}) as never;
		expect(await servicios().vigente(userId, "c")).toBe(false);
	});

	test("sin vínculo local, deniega", async () => {
		await db.granCRMLink.deleteMany({ where: { userId } });
		expect(await servicios().vigente(userId, "cookie-buena")).toBe(false);
	});

	test("el vencimiento local no supera el de la sesión de origen", async () => {
		await db.granCRMLink.updateMany({
			where: { userId }, data: { expiraEn: new Date(Date.now() - 1000) },
		});
		expect(await servicios().vigente(userId, "cookie-buena")).toBe(false);
	});

	test("dentro de una misma petición no consulta dos veces", async () => {
		const espia = mock(async () => respuestaDios());
		globalThis.fetch = espia as never;
		const s = servicios();
		const ctx = s.contextoDePeticion();
		await s.vigente(userId, "c", ctx);
		await s.vigente(userId, "c", ctx);
		expect(espia.mock.calls.length).toBe(1);
	});
});
```

- [ ] **Step 2: Correrlos**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools \
  bun test apps/api/test/grancrm-authority.spec.ts
```

Expected: FAIL — no existe `GranCRMLinkService` ni el modelo `GranCRMLink`.

- [ ] **Step 3: Agregar el modelo del vínculo**

```prisma
/// Vincula una sesión local del CRM con la sesión de GranCRM que la originó.
///
/// Existe para que la autorización se pueda REVALIDAR: sin el vínculo, una
/// cookie de Better Auth es indistinguible de un acceso legítimo después de
/// que GranCRM revocó la sesión o retiró el rol.
///
/// El vínculo es (proveedor + cuenta + id estable del usuario), NO el correo:
/// el correo es un atributo y usarlo como clave de confianza permitiría tomar
/// una cuenta existente creando un usuario con el mismo mail.
model GranCRMLink {
  id            String   @id @default(cuid())
  userId        String   @unique
  user          User     @relation(fields: [userId], references: [id], onDelete: Cascade)
  granUserId    String
  cuentaSlug    String
  granSesionJti String
  expiraEn      DateTime
  creadoEn      DateTime @default(now())
  actualizadoEn DateTime @updatedAt

  @@unique([cuentaSlug, granUserId])
  @@index([granSesionJti])
  @@map("granCRMLink")
}
```

Y en `User`, la relación inversa: `granCRMLink GranCRMLink?`.

```bash
docker compose -f docker-compose.crm.yml run --rm \
  -e DATABASE_URL="postgresql://crm_owner:$POSTGRES_PASSWORD@postgres:5432/crm?schema=public" \
  tools bunx --bun prisma migrate dev \
    --schema packages/db/prisma/schema.prisma --name grancrm_link
```

- [ ] **Step 4: Escribir el servicio de vínculo**

```typescript
// apps/api/src/grancrm/grancrm-link.service.ts
//
// ¿La autorización de este usuario sigue vigente en GranCRM?
//
// Se consulta en CADA petición protegida y no sólo al entrar. Sin esto, la
// cookie de Better Auth sobrevive al logout de GranCRM y a que le retiren el
// rol: el comercial sigue entrando hasta que expire su sesión local.
//
// SIN CACHÉ POSITIVA entre peticiones -- una caché de "sí, puede" es
// exactamente lo que permite seguir operando después de una revocación. Se
// deduplica dentro de una misma petición, donde no hay ventana.
import type { Db } from "@crm/db";
import { Injectable, Logger } from "@nestjs/common";
import { GranCRMSessionService } from "./grancrm-session.service";

/// Memo por petición. Un objeto vacío que el guard crea al principio y pasa a
/// cada llamada: vive lo que vive la petición y se descarta con ella.
export type ContextoPeticion = { resultado?: boolean };

@Injectable()
export class GranCRMLinkService {
	private readonly logger = new Logger(GranCRMLinkService.name);

	constructor(
		private readonly db: Db,
		private readonly puente: GranCRMSessionService,
	) {}

	contextoDePeticion(): ContextoPeticion {
		return {};
	}

	async vigente(
		userId: string,
		cookie: string | undefined,
		ctx?: ContextoPeticion,
	): Promise<boolean> {
		if (ctx?.resultado !== undefined) return ctx.resultado;
		const respuesta = await this.calcular(userId, cookie);
		if (ctx) ctx.resultado = respuesta;
		return respuesta;
	}

	private async calcular(userId: string, cookie: string | undefined): Promise<boolean> {
		const vinculo = await this.db.granCRMLink.findUnique({ where: { userId } });
		// Una sesión local sin vínculo no es un acceso de GranCRM. Puede ser un
		// usuario creado por otra vía; se deniega.
		if (!vinculo) return false;

		// El vencimiento local nunca supera al de origen.
		if (vinculo.expiraEn.getTime() <= Date.now()) return false;

		const identidad = await this.puente.identidad(cookie);
		if (!identidad.ok) return false;

		// La identidad presentada tiene que ser LA del vínculo: una cookie de
		// otra persona, aunque sea válida en GranCRM, no opera esta sesión.
		if (identidad.granUserId !== vinculo.granUserId) return false;
		if (identidad.tenantId !== vinculo.cuentaSlug) return false;

		return true;
	}
}
```

`GranCRMSessionService.identidad` tiene que devolver también `granUserId` (el
`user_id` del orquestador): agregarlo al tipo `Identidad` y al objeto que
retorna, junto a `jti`.

- [ ] **Step 5: Aplicarlo en TODAS las superficies**

No alcanza el middleware del frontend ni el menú. Auditar y cubrir:

```bash
cd /home/admincrm/compai-crm
# Inventario de superficies que devuelven datos:
grep -rn "@Controller\|@Get\|@Post" apps/api/src --include=*.ts | grep -v test | wc -l
grep -rln "use server" apps/app/src 2>/dev/null | head
grep -rn "createTRPCRouter\|\.router\.ts" apps/api/src | wc -l
```

- Procedimientos tRPC: en `apps/api/src/trpc/middlewares/auth.middleware.ts`,
  después de comprobar `ctx.session?.user`, agregar la comprobación de vigencia
  para las sesiones que tengan vínculo de GranCRM. **Ojo con no romper la key
  de ingesta**, que no tiene vínculo y tiene su propio guard: la comprobación
  se aplica cuando la sesión vino de cookie, no de `x-api-key`.
- Controladores REST: un `GranCRMAuthorityGuard` global, con la lista de rutas
  exentas explícita (`/api/grancrm/entrar`, `/api/health`, `/api/ingest/*`).
- SSR y server actions de `apps/app`: si alguna obtiene datos sin pasar por la
  API, agregar el control ahí o centralizar la lectura.
- Descargas, exports y streams: revalidar antes de cada entrega, no sólo al
  abrir la conexión.

- [ ] **Step 6: Logout**

Con el mecanismo de Better Auth (revocar la sesión y limpiar cookies con los
mismos atributos), y borrando el vínculo. Nunca limpiando cookies a mano.

- [ ] **Step 7: Correr los tests y medir el costo**

```bash
cd /home/admincrm/compai-crm
docker compose -f docker-compose.crm.yml run --rm tools \
  bun test apps/api/test/grancrm-authority.spec.ts
```

Expected: PASS, los 11.

**Y medir, porque esto es una llamada HTTP al orquestador por petición** en una
app con SSR y tRPC:

```bash
# Cuántas peticiones protegidas genera abrir el pipeline y una ficha:
# (contar en la pestaña de red del navegador, o en los logs de la API)
docker compose -f docker-compose.crm.yml logs --tail 200 api | grep -c "grancrm/session"
# Y cuánto tarda /api/session/ del orquestador:
for i in $(seq 5); do
  curl -s -o /dev/null -w '%{time_total}\n' \
    -H "Cookie: grancrm_session=$COOKIE" \
    http://172.20.21.249:9000/api/session/
done
```

**Registrar los dos números en `docs/integracion-crm/contrato-acceso.md`.** Si
abrir una pantalla dispara 30 revalidaciones de 80 ms cada una, la decisión de
"sin caché positiva" hay que tomarla con ese dato a la vista — el camino
correcto entonces es reducir el número de peticiones protegidas o negociar un
TTL corto y explícito, **no** cachear el "sí" por tiempo indefinido.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/grancrm/grancrm-link.service.ts \
        apps/api/src/grancrm/grancrm-authority.guard.ts \
        apps/api/src/grancrm/grancrm-session.service.ts \
        apps/api/src/trpc/middlewares/auth.middleware.ts \
        packages/db/prisma/schema.prisma packages/db/prisma/migrations \
        apps/api/test/grancrm-authority.spec.ts
git commit -m "feat(auth): revalidar la autorizacion de GranCRM en cada peticion

Validar solo al entrar dejaba viva la cookie de Better Auth despues del
logout de GranCRM y despues de retirarle el rol o la cuenta al usuario: el
comercial seguia entrando hasta que expirara su sesion local.

El vinculo GranCRMLink es (cuenta + id estable del usuario), no el correo:
el correo es un atributo y usarlo como clave de confianza permitiria tomar
una cuenta existente creando un usuario con el mismo mail.

Sin cache positiva entre peticiones -- es exactamente lo que permitiria
seguir operando tras una revocacion. Se deduplica dentro de una peticion."
```

---

### Task 4: Publicar `/crm/` en el gateway

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
	//
	// SIN `assetPrefix`: es para servir assets desde un CDN, no para montar la
	// app en una subruta. `basePath` ya prefija los assets, y poner los dos
	// puede terminar pidiendo /crm/crm/_next/...
	basePath: "/crm",
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

### Task 5: Probar el acceso en el navegador

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

- [ ] **Step 3b: Los casos de revocación en vivo, en el navegador**

Los tests de la Task 3 los cubren en unidad; acá se comprueban con un navegador
de verdad, que es donde aparecen los que la unidad no ve.

| Caso | Pasos | Esperado |
|---|---|---|
| Logout con el CRM abierto | Con el CRM abierto en una pestaña, cerrar sesión de GranCRM en otra. Volver al CRM y **navegar**, no sólo recargar | Deniega. No vuelve al puente en bucle: muestra un estado claro |
| Retirar el rol en caliente | Con el CRM abierto, un SA le quita el acceso a la app o le cambia el rol. Hacer una lectura y una mutación | Las dos denegadas |
| Cambio de usuario en el mismo navegador | Cerrar sesión, entrar como **otra** persona de la misma cuenta, abrir el CRM | No reusa la sesión del CRM de la identidad anterior |
| Suspender la cuenta | Suspender la cuenta de GranCRM con el CRM abierto | Deniega |
| Orquestador caído | Parar el orquestador y usar el CRM | 503 y cero datos; **no** sigue andando con la cookie local |
| Export y descarga | Iniciar una descarga y revocar el acceso a mitad | La siguiente entrega protegida se deniega |

**Y el límite que hay que escribir en la evidencia, no prometer de más:** la
revocación es efectiva en **la siguiente comprobación autoritativa**. Lo que ya
se envió al navegador no se puede retirar, y una transacción autorizada y
terminada no se cancela.

- [ ] **Step 3c: Las vías alternativas de entrada**

Inventariar qué otras formas de autenticarse quedaron habilitadas en el CRM
instalado, y cerrarlas o protegerlas:

```bash
cd /home/admincrm/compai-crm
grep -rnE "emailAndPassword|magicLink|signUp|socialProviders|genericOAuth" \
  packages/auth/src/auth.ts
```

| Vía | Qué hacer |
|---|---|
| Registro abierto (`signUp`) | Deshabilitado: un usuario sin vínculo no entra (Task 3), pero mejor que no se pueda crear |
| Usuario/contraseña | Deshabilitado si está activo |
| Google / Microsoft / SSO | Sin credenciales configuradas, y comprobado que la página de login no los ofrece |
| API keys ordinarias | No abren sesión de comercial: la Task 3 exige vínculo, y la key de ingesta no lo tiene |

Comprobarlo por HTTP, no por lectura:

```bash
for ruta in /api/auth/sign-up/email /api/auth/sign-in/email; do
  printf '%-32s %s\n' "$ruta" \
    "$(curl -sk -o /dev/null -w '%{http_code}' -X POST \
        -H 'Content-Type: application/json' -d '{}' \
        https://172.20.21.249/crm-api$ruta)"
done
```

Expected: `404` o `403`, nunca un 400 de validación — un 400 significa que el
endpoint existe y está aceptando peticiones.

- [ ] **Step 4: Pedir la review de seguridad**

Antes de dejar esto habilitado para usuarios reales: **review de seguridad dedicada del puente** (Global Constraints). Es código de autenticación propio. Lo que hay que mirar, en orden de riesgo:

1. **La revalidación no se puede saltar.** Que no quede ninguna superficie que devuelva datos sin pasar por el guard: tRPC, REST, SSR, server actions, exports y streams.
2. **El vínculo, no el correo.** Que no haya quedado ningún `findUnique({ where: { email } })` decidiendo identidad.
3. **Falla cerrado.** Con `CRM_CUENTA_SLUG` vacío, con la lista de roles vacía, o con el orquestador caído: nadie entra.
4. El manejo de `Set-Cookie` en el redirect, y que el destino no venga de la petición.
5. Que los tres chequeos de alcance sigan aplicándose al reentrar, no sólo la primera vez.

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

Con las 5 tareas en verde y la review de seguridad hecha: el comercial entra a `/crm/` desde GranCRM con un solo login, un usuario de otra cuenta recibe 403, y una sesión revocada deja de servir de inmediato.

Lo que **no** cambia con esto: el CRM sigue siendo single-workspace. Si alguna vez hacen falta varios clientes aislados en una sola instancia, es un trabajo aparte que hay que decidir antes de implementarlo.
