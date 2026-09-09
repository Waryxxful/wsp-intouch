# Integración Comp AI CRM ↔ wsp_intouch — diseño

Fecha: 2026-09-09 · Estado: **DISEÑO, SIN IMPLEMENTAR** · Revisado el 2026-09-09
tras una revisión externa de los planes.

Los planes hermanos (`docs/superpowers/plans/2026-09-09-compai-crm-*.md`) son la
fuente para ejecutar; este documento es el diseño y el registro de lo que se
inspeccionó en el servidor. Las secciones §2.3, §2.4, §6.1 y §6.3 se corrigieron
después de esa revisión y llevan la marca **[corregido]**.

Origen del encargo: `/home/admincrm/prompt/prompt_integracion_crm_bot.md` (v1.0).
Ese prompt se redactó sin inspeccionar este servidor y lo dice explícitamente.
Este documento es el resultado de la inspección, y **corrige** al prompt donde
los hechos del disco lo contradicen. Cada corrección está marcada y
justificada; ninguna se aplica en silencio.

Objetivo: el bot comercial califica un contacto → el lead llega al CRM sin
duplicados indebidos → el comercial autorizado lo ve y lo gestiona en el
pipeline entrando por GranCRM.

---

## 0. Resumen de lo que ya existe (y de lo que el prompt supone mal)

### Verificado en el servidor `172.20.21.249` (GranCRM-QA), 2026-09-09

| El prompt dice | La realidad |
|---|---|
| Carpeta `/home/admincrm/grancrm/compai-crm` | **No existe.** No hay ningún checkout del CRM. Se instala de cero |
| Red Docker `grancrm-net` | **No existe.** Las redes reales son `grancrm_default`, `orquestador_default`, `wsp_*_default`. El gateway nginx corre con `network_mode: host` |
| BD PostgreSQL | **No hay ningún Postgres en el host.** El stack usa SQL Server `172.20.21.50:1433` + Supabase (RAG). Postgres 17 es infraestructura nueva |
| Puerto 3005, acceso `/crm/` | Ambos libres. `nginx.conf` tiene 50 `location` y ninguno es `/crm` |
| Tool `execute_submit_lead()` | **No existe y no se va a crear.** Ver §0.2 |
| `CRM_WEBHOOK_URL` | El emisor lee `LEAD_SINK` / `LEAD_SINK_URL` (`config/settings.py:306`) |
| "20 campos" / "22 campos" | Son **22** y coinciden exactos con `LeadInTouch` + `wa_id`. El prompt acertó el número corregido |

Runtimes: Node v22.23.2 (cumple el `>=22` del CRM), **`bun` no instalado**
(el CRM lo declara como `packageManager`), Docker 29.4.0, 89 GB libres.

`wsp_intouch` **nunca se levantó**: no hay contenedor. Sigue bloqueado por sus
tres credenciales propias (login SQL `intouch_login_qa`, credenciales de Meta,
`docs/rag_schema_intouch.sql` en Supabase). Eso ordena las pruebas (§9), no el
diseño.

### 0.1 — El CRM upstream, inspeccionado

`trycompai/crm@release`, v1.15.3, último push 2026-09-02, MIT, 10,1k estrellas.

- Monorepo **bun 1.3.12 + turbo**: `apps/app` (Next.js 16, App Router),
  `apps/api` (**NestJS + tRPC**, no Next.js route handlers), `apps/agent`
  (agente de research, deployment propio), `packages/db` (Prisma, schema de
  1.585 líneas, 60 migraciones), `packages/auth`, `packages/validation`.
- Su `docker-compose.yml` **sólo levanta Postgres**. No hay Dockerfile para
  app ni api: los construimos nosotros.
- **La referencia editorial del prompt §3 se confirma**: `prisma/schema.prisma`
  y `app/api/leads/route.ts` en la raíz no existen. El receptor va en
  `apps/api/` (NestJS), como el prompt anticipó en el ejemplo
  `repositorio_distinto`.

Cuatro descubrimientos que **ahorran** trabajo (reuso, no invención):

1. **Campos dinámicos ya existen.** `FieldDefinition`/`FieldValue` sobre
   `COMPANY`/`CONTACT`/`DEAL`, con `showOnSheet`/`showOnTable`/`showOnFilter`.
   Los campos de calificación entran ahí **sin forkear el schema Prisma** y se
   ven en la UI sin escribir frontend.
2. **Ingesta idempotente ya tiene precedente.** `FormSubmission.dedupeKey
   @unique` + `tracking-ingest.service.ts` + `tracking-filing.service.ts`, y
   controladores REST planos (`tracking.controller.ts`). El patrón se copia.
3. **Autenticación de integraciones ya existe.** Plugin `apiKey` de Better
   Auth, header `x-api-key`, prefijo `crm_`. Con una limitación grave: §6.
4. **Resolución de dominios ya existe.** `apps/api/src/companies/domain.ts`
   exporta `domainFromEmail()`, que devuelve `null` para 21 proveedores
   gratuitos (gmail, hotmail, outlook, proton…) y para dominios de máquina.
   **No hay que escribir esa lista**, y resuelve por reuso el riesgo de fusionar
   empresas por un correo personal.

Y dos que **restringen** el diseño:

5. **Es single-workspace, no multi-tenant.** `Company`, `Contact` y `Deal` **no
   tienen `organizationId`**. `Organization` existe sólo como perfil de
   workspace + SSO. Consecuencia en §6.4 y §9 (I14).
6. **No tiene login usuario/contraseña.** `.env.example` dice literal que
   `ALLOWED_SIGN_IN` *"is the entire authorisation model"*. Las únicas vías son
   Google OAuth, Microsoft Entra, o un IdP OIDC/SAML en Settings → SSO. Eso
   choca con la regla de oro del contrato de satélites. Ver §6.

### 0.2 — El emisor ya existe, y no es una tool

`bot/business/lead_intouch.py` ya tiene el modelo `LeadInTouch` (21 campos +
`wa_id` = los 22 del contrato), `payload_del_lead()`, `clave_idempotencia()`,
`_enviar_al_sink()` y el switch `LEAD_SINK=none|http`.

**No hay ni habrá `execute_submit_lead()`.** Se midió en Cavem sobre 324
turnos: `registrar_datos_lead` como tool costaba una segunda ronda de
herramientas — 4,53 s de mediana en el 17,3 % de los turnos. El lead lo escribe
el **extractor de metadatos**, después de que el mensaje ya salió al contacto.

Consecuencia de diseño que está en el prompt del bot y no se toca: **el bot no
puede decir "tu solicitud quedó registrada"** en el mismo turno, porque cuando
redacta esa frase el lead todavía no se escribió. Esto **reemplaza** a §6 y §7
del prompt de origen, que asumen una tool síncrona; el resto de §6 (validar
status, JSON y esquema; timeouts; persistir estado de envío; contingencia
honesta) **sí aplica** y está en §5.

---

## 1. Topología

Proyecto compose nuevo en **`/home/admincrm/compai-crm`** — no dentro de
`grancrm/`, que es un repo propio: anidar un monorepo dentro de otro repo
ensucia los dos `git status`, y este working tree ya es compartido por varias
sesiones.

Clon vendored de `trycompai/crm@release` fijado en v1.15.3, con `upstream` como
remoto y los parches locales en una rama, para que un merge futuro siga siendo
barato. **No se actualiza a upstream durante la integración** (prompt §3).

| Contenedor | Qué | Puerto publicado |
|---|---|---|
| `crm-postgres` | `postgres:17-alpine`, volumen nombrado | **ninguno** |
| `crm-api` | NestJS con bun | `127.0.0.1:3006` |
| `crm-app` | Next.js con bun | `127.0.0.1:3005` |
| `crm-agent` | **no se despliega** | — |

En `127.0.0.1` y no en `0.0.0.0`: el nginx del gateway corre en
`network_mode: host`, así que llega por loopback y nada queda expuesto a la LAN.
Postgres no publica puerto en absoluto (prompt §E02: "evita exposición externa
de Postgres").

**El puerto 3005 es la intención de exposición, no una obligación interna**
(prompt §E02): internamente `crm-app` escucha 3000 y `crm-api` 3001, sus valores
por defecto. Se mapean afuera.

Para que el bot alcance la API sin exponerla: red Docker externa **`crm_ingest`**
a la que se enganchan `crm-api` y el servicio `web` de `wsp_intouch`. El bot
resuelve `http://crm-api:3001`. Se puede crear sin downtime porque el bot
todavía no corre.

### 1.1 — Gateway

En `/home/admincrm/gateway/nginx.conf`, aplicado con
`docker compose up -d --force-recreate nginx` — **`nginx -s reload` no alcanza**
con este bind-mount, es un gotcha ya pagado en este repo.

```
location /crm-api/ { proxy_pass http://127.0.0.1:3006/; }   # antes que /crm/
location /crm/     { proxy_pass http://127.0.0.1:3005/; }
```

`/crm-api/` va declarado antes por prefijo más largo (misma mecánica que
`/wsp/demo/` sobre `/wsp/`).

Mismo origen para app y API, así que la cookie de sesión de Better Auth cubre
las dos sin `AUTH_COOKIE_DOMAIN`. Tres cosas que hay que resolver ahí:

1. **`basePath: "/crm"` + `assetPrefix`** en `next.config.ts`. Es **build-time**
   (prompt §E01): un `location` de nginx no reescribe assets, enlaces ni
   callbacks. Y `NEXT_PUBLIC_API_URL` queda **horneado en el bundle**, así que
   cambiar la URL pública exige rebuild, no sólo reiniciar.
2. **La CSP del gateway rompe Next.js.** Hoy es `script-src 'self'` sin
   `unsafe-inline`, y el App Router inyecta scripts inline. `/crm/` necesita su
   bloque de CSP propio. Ojo con la regla que ya está comentada en el archivo:
   **nginx no mergea `add_header` entre niveles** — si una `location` define uno,
   deja de heredar todos los demás y hay que repetirlos.
3. `APP_URL` y `API_URL` apuntan a la URL pública del gateway
   (`https://<host>/crm` y `https://<host>/crm-api`), y esos son los
   `trustedOrigins` de Better Auth. **No** `http://172.20.21.249:3005`
   (prompt §E03).

---

## 2. El receptor

Módulo nuevo `apps/api/src/ingest/` en la capa API que el CRM realmente usa,
copiando la estructura de `tracking/`: `ingest.controller.ts` (REST plano),
`ingest.service.ts` (resolución de identidad + escritura), `ingest.contracts.ts`
(zod), `ingest.module.ts`.

**`POST /api/ingest/intouch-lead`**, sin `@AllowAnonymous()`, más los dos
chequeos de alcance de §6.1 — que la omisión del decorador **no** aporta por sí
sola.

### 2.1 — Validación (prompt §4)

zod estricto, que es la biblioteca del proyecto (`packages/validation`, y todos
los `*.contracts.ts`). Reglas no negociables:

- `.strict()`: una clave desconocida **rechaza** con 400. Nunca asignación
  masiva.
- Booleanos: `z.boolean()` JSON real. **Ninguna coerción de truthiness** —
  `Boolean("false")` es `true`, y el prompt lo marca explícitamente.
  `null`/ausente ≠ `false`.
- JSON inválido o body sobredimensionado → **400/413, nunca 500**. Se copia el
  `MAX_BODY_BYTES` de `@crm/db/tracking`, que ya existe para esto.
- Campos internos (`ownerId`, etapa, ids de CRM, notas) **no se leen del body**.
  Se resuelven de configuración e identidad confiable.
- `resumen_conversacion` y `siguiente_accion_recomendada` son **contenido no
  confiable**: se guardan como texto, nunca se renderizan como HTML, y ningún
  agente posterior los interpreta como instrucciones de sistema.

### 2.2 — Etapa comercial: corrección al diseño anterior

`DealStage` es un enum cerrado de 7 valores, ninguno de los cuales significa
"entrante": `DEMO_BOOKED`, `QUALIFIED_TO_BUY`, `UNQUALIFIED_TO_BUY`,
`DECISION_MAKER_BOUGHT_IN`, `CONTRACT_SENT`, `CLOSED_WON`, `CLOSED_LOST`.
El default del schema es `DEMO_BOOKED`.

**Etapa inicial: `QUALIFIED_TO_BUY`, elegida deliberadamente y no por
descarte.** Es la menos incorrecta del enum instalado, no la correcta: afirma
que alguien calificó al contacto como apto para comprar, y lo que pasó es que
un bot recogió antecedentes. Queda anotada como decisión en el `RUNBOOK.md`; si
el equipo comercial define una etapa de entrada, se cambia ahí y en el test que
la ancla.

Por qué, campo por campo:
- `DEMO_BOOKED` **afirmaría una demo agendada**. El bot de InTouch no agenda
  nada. Sería un hecho falso escrito en el pipeline, y es el default del schema,
  así que **la ingesta tiene que fijar `stage` explícitamente** para no caer ahí.
- `DECISION_MAKER_BOUGHT_IN` y `CONTRACT_SENT` afirman hechos que no ocurrieron.
- `QUALIFIED_TO_BUY` es exactamente lo que pasó: la calificación del bot dio
  WARM o HOT.
- `lead_score` **no es una etapa**. Viaja como campo dinámico del Deal. HOT y
  WARM entran en la misma etapa y se distinguen por el campo.

`NO_CALIFICADO` y `COLD` **no abren Deal**. Abren Company + Contact, que se ven
en el listado sin ensuciar el pipeline ni las métricas de embudo.

Un Deal se abre cuando `lead_score` ∈ {HOT, WARM} **o** hay solicitud explícita
(`solicita_consultoria` o `solicita_contacto_humano`), porque una petición
explícita es un hecho comercial aunque el score no haya llegado.

**`Deal.stage` no se toca nunca después de crearlo** (prompt §C01): ningún
evento del bot devuelve una oportunidad `CLOSED_WON` a `QUALIFIED_TO_BUY`.

### 2.3 — Resolución de identidad **[corregido]**

**La resolución no escribe.** Son dos fases: una decide y devuelve un *plan*
sin tocar la base, la otra lo ejecuta sólo si el plan completo es aceptable. El
diseño anterior creaba la Company y *después* podía devolver conflicto de
contacto, pero la transacción commiteaba igual: quedaba una empresa creada por
un evento rechazado.

El punto donde una fusión equivocada cuesta datos reales de un tercero. Ninguna
regla adivina.

**Empresa.**
1. `domainFromEmail(correo)` — upstream, ya devuelve `null` para proveedores
   gratuitos y dominios de máquina. Si hay dominio → upsert de `Company` por
   `domain`. Es la única identificación fuerte.
2. Sin dominio: **no se fusiona por nombre, ni entre companies sin dominio.**
   Dos empresas homónimas pueden ser dos empresas distintas, y el nombre lo
   declaró un contacto por WhatsApp. Lo único que permite reusar una fila es el
   **vínculo de origen** (`LeadIngestEvent` con la misma `claveContacto`), que
   es lo que prueba que esa fila la creó esta integración para este contacto.
   Si después resulta que son la misma empresa, la fusiona una persona:
   separarlas es posible, desfusionar no.
3. Sin dominio y sin nombre de empresa → no se crea Company. El Contact queda
   sin empresa, que es la verdad.

**Contacto.** `Contact.phone` **no tiene índice único** (el único es
`@@unique([email]) where archivedAt: null`), así que la búsqueda por teléfono
puede devolver varias filas.

| Caso | Qué hace |
|---|---|
| Un solo match por teléfono | Se usa |
| **Varios** matches por teléfono | **409 conflicto, cero escrituras.** No se adivina |
| Sin teléfono, con correo (match único) | Se usa |
| Teléfono y correo apuntan a **contactos distintos** | **409 conflicto, cero escrituras.** Son dos personas o un dato mal cargado; lo resuelve un humano |
| Match por correo, y ese contacto ya tiene **otro** teléfono | **409 conflicto, cero escrituras.** Un correo escrito en una conversación no prueba posesión: vincular atribuiría esta conversación a un tercero |
| El vínculo de origen apunta a un contacto archivado o borrado | **409 conflicto.** No se recrea en silencio: alguien decidió sacarlo, y un replay no puede resucitarlo |
| Nada matchea | Se crea |

Un 409 de identidad deja el lead **pendiente y visible** en el panel del bot
(§5), que es lo que lo vuelve accionable en vez de perdido.

Nota sobre el unique parcial: existiendo un contacto **archivado** con el mismo
correo, la restricción permite crear uno nuevo. Es el comportamiento de upstream
y no lo cambiamos; queda anotado.

Los conflictos no se acumulan sin salida: hay una operación administrativa
—detrás de la sesión de GranCRM, nunca de la key del bot— que asocia una
identidad y reejecuta el mismo evento **sin editar su payload**. Corregir el
contenido exige un evento nuevo del productor. Es la Task 15 del plan.

Y una carrera que el advisory lock por clave de contacto **no** cubre: dos
claves distintas que comparten teléfono o correo toman locks distintos. Se
resuelve con las restricciones existentes más locks sobre las identidades
normalizadas en orden estable, y está cubierto por un test.

### 2.4 — La escritura

Todo en un `prisma.$transaction` (prompt §C01: "si una ingesta crea varias
filas, usa transacción para la parte local indivisible"):

1. Se ejecuta el plan de identidad de §2.3 — recién acá se escribe.
2. Company y Contact según ese plan, `source: RecordSource.IMPORT`. No se
   inventa `website` desde el dominio: lo presentaría como un sitio verificado.
3. Deal, **sólo si corresponde** (§2.2), `stage: QUALIFIED_TO_BUY`,
   `ownerId` de configuración.
4. `FieldValue` de los campos de calificación (§3).
5. `Activity(NOTE)` con resumen y siguiente acción — es el lugar natural para
   texto libre no confiable y le da timeline al comercial.
6. Si `solicita_contacto_humano` **o `solicita_consultoria`**: además un
   `Activity(TASK)` con vencimiento. **Eso** es lo que hace la petición
   accionable, y no un booleano olvidado (prompt §6 punto 7, prueba I12).
   Va con una **clave de efecto** por contacto y tipo: el emisor manda el
   objeto completo en cada revisión, así que sin la clave el comercial
   recibiría una tarea nueva por cada mensaje. Una tarea ya completada no se
   reabre.
7. `LeadIngestEvent` sellado con el payload íntegro, su hash y los tres ids.

`RecordSource` no tiene valor para bot/WhatsApp (`MANUAL`, `IMPORT`, `EMAIL`,
`CALENDAR`, `TRACKING`). Se usa `IMPORT` y la procedencia real vive en el campo
dinámico `origen` y en `LeadIngestEvent`. Agregar un valor al enum es cambiar un
registro central con mapas de etiquetas en el frontend de upstream que fallarían
en silencio; queda como deuda declarada con la lista de greps necesaria, no como
cambio de esta entrega.

---

## 3. Los 22 campos, sin pérdida

**Nativos** (8): `nombre_completo` → `Contact.firstName`/`lastName` ·
`correo` → `Contact.email` · `telefono` → `Contact.phone` ·
`cargo` → `Contact.title` · `empresa` → `Company.name` ·
`industria` → `Company.industry` · `subtipo_automotriz` → `Company.subIndustry` ·
`necesidad_principal` → `Deal.description`.

**Campos dinámicos** (`FieldDefinition` sembrado en migración, con
`showOnSheet: true`): los 11 de diagnóstico y calificación, más `origen`,
`telefono_whatsapp` y `clave_contacto`.

`usa_ia_actualmente` sobrevive como **tri-estado** porque `FieldValue.bool` es
nullable: ausente ≠ `false`. Eso preserva una corrección que ya costó datos en
el bot (un `False` de relleno borraba el `solicita_contacto_humano` capturado en
el turno anterior).

### 3.1 — Los arrays se conservan estructurados

`canales_actuales` y `soluciones_interes` son arrays. `FieldType` no tiene
`MULTI_SELECT` ni JSON, y `FieldValue` tiene `@@unique([fieldId, contactId])`,
así que **no** se pueden guardar como N valores.

Unirlos con comas no es reversible en general (un elemento que contenga una coma
se parte mal). Entonces:

- **La estructura original se conserva íntegra** en `LeadIngestEvent.payload`
  (columna `Json`), con el payload completo verbatim. Es la fuente reversible.
- **El texto de presentación se genera aparte**, como `FieldValue` `LONG_TEXT`,
  derivado del JSON y marcado como derivado en el `agentBrief` del campo.

Ninguna transformación con pérdida es la única copia de un dato.

### 3.2 — `necesidad_principal` cuando no hay Deal

Va **siempre** como campo dinámico `LONG_TEXT` a nivel de Contact, y *además* a
`Deal.description` cuando se abre Deal. Un lead COLD que explicó su necesidad no
puede perderla por no haber calificado.

### 3.3 — Lo que sí se decide no mapear

`pais_ciudad` **no se parte** a `Company.city`/`country`: el contrato dice "no
inferir como hecho". Queda como string crudo en su campo dinámico.

---

## 4. Idempotencia: dos claves, y el orden importa

La clave que hoy genera el emisor es `sha256("wsp_intouch:{wa_id}")`. Y
`Conversation.wa_id` es `unique=True`, así que hay **una** `Conversation` por
número: esa clave identifica a un **contacto**, no a una conversación — la misma
persona que vuelve a escribir meses después reusa la misma fila. Se renombra a
`clave_contacto` para que el nombre diga lo que es.

Con una sola clave, "misma clave, payload distinto" sería el caso *normal* de
una conversación que avanzó, y forzar un 409 ahí convertiría cada nueva
calificación en un error. La separación en dos claves es lo que arregla eso:

| Clave | Qué identifica | Cuándo cambia |
|---|---|---|
| `clave_contacto` | La identidad comercial | Nunca |
| `evento_id` (uuid4, nuevo) | El intento de envío | Sólo cuando cambia el hash del payload |
| `revision` (int, nuevo) | El orden | +1 con cada `evento_id` nuevo |

Los tres van al modelo `LeadInTouch` del emisor y viajan en el payload.

**Modelo nuevo en el receptor** — aditivo, sin tocar tablas existentes:

```prisma
model LeadIngestEvent {
  id            String   @id @default(cuid())
  origin        String
  eventoId      String
  claveContacto String
  revision      Int
  payloadHash   String
  payload       Json
  status        String
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

Las **dos** restricciones únicas son las que resuelven las carreras en la BD y
no en el código (prompt §C03: "el receptor impone unicidad por origen confiable
+ clave de evento en BD"). La segunda además hace colisionar una revisión
duplicada o fuera de orden.

Matriz de decisión:

| Llega | Resultado |
|---|---|
| `evento_id` nuevo, `revision` > máxima almacenada | **201 created** o **200 updated** |
| `evento_id` conocido, **mismo** `payloadHash` | **200 replayed**, mismos ids, **cero escrituras** |
| `evento_id` conocido, `payloadHash` **distinto** | **409 conflicto**, cero escrituras — es **I04**, y aplica |
| `revision` ≤ la máxima almacenada, hash distinto | **409 stale**, cero escrituras — una revisión vieja no pisa a la nueva |

Corrección explícita a mi diseño anterior: había dicho que I04 no aplicaba a
este emisor. Aplica — a nivel de **evento**, que es justamente lo que la
separación de claves hace existir. Lo que no aplica es exigir conflicto cuando
cambia el contenido de un **contacto**, que es una actualización legítima.

**Política de actualización** (prompt §C03: "no borres datos válidos por campos
ausentes"):

- Campos dinámicos del bot: se sobreescriben.
- Campos nativos: se llenan **sólo si están vacíos**. No le pisamos al comercial
  lo que corrigió a mano.
- Un campo ausente **nunca borra** un valor existente.
- `Deal.stage`: nunca se toca (§2.2).
- Cada actualización deja su `Activity`.

---

## 5. Los arreglos del emisor

Cinco, todos en `wsp_intouch`:

1. **`_enviar_al_sink` hoy sólo mira el código HTTP.** Tiene que parsear el
   JSON, exigir `status` conocido y `contactId` no vacío, y tratar HTML o un
   redirect a login como **fallo de contrato** (prueba I11). Sin esto un 2xx
   incompleto se registra como éxito. **Y `dealId: null` es un éxito válido**:
   la política de §2.2 crea contactos sin oportunidad a propósito, y el emisor
   no puede tratar eso como fallo.
2. **`LEAD_SINK_TOKEN`** → header `x-api-key`. Fuera del código, fuera de los
   logs, con rotación definida.
3. **`evento_id`, `payload_hash` y `revision`** en `LeadInTouch`; `despachado_en`
   pasa a sellar el último evento confirmado.
4. **Recuperación de pendientes.** Hoy `_despachar_si_corresponde` corre sólo
   cuando llega un turno nuevo: si el despacho falla y la conversación termina,
   el lead **no llega nunca**. Comando `despachar_leads_pendientes` en un
   contenedor cron sidecar, como ya hacen `wsp_pompeyo-cron` y
   `orquestador-cron` (pruebas I09, I10). Reintenta con el **mismo** `evento_id`.
5. **Timeouts y reintentos acotados**, con backoff. En timeout después de un
   posible commit, se reintenta con el mismo `evento_id`: si el CRM ya escribió,
   responde `replayed` con los mismos ids y no se duplica nada.

Y en el panel de Leads del bot (`frontend/src/pages/LeadsPage.tsx` +
`GET /api/leads`): columna de estado de despacho (despachado / pendiente /
fallido / conflicto, con el id del CRM) y botón de reintento. El CRM pasa a ser
el pipeline; el panel del bot queda como **diagnóstico** — y es el único lugar
donde un lead que no llegó es visible. Hoy `despachado_en` sólo se puede mirar
por SQL.

---

## 6. Autenticación

Dos caminos distintos que no se sustituyen (prompt §3 punto 6): el bot contra la
API, y el comercial contra la UI.

### 6.1 — El bot: la API key da autenticación, no alcance **[corregido]**

Verificado en `packages/auth/src/auth.ts` y `apps/api/src/api-keys/`:

- `createApiKey` se llama **sin `permissions`** → las keys no llevan scopes.
- `enableSessionForAPIKeys: true`, y `AuthMiddleware` sólo verifica que exista
  `ctx.session?.user` → **una API key equivale a ser su usuario dueño, con todo
  su acceso**.
- `rateLimit: { enabled: false }` en el plugin.
- Existe `SessionOnlyMiddleware`, cuyo único trabajo es **rechazar** requests que
  traigan `x-api-key`. Ésa es la forma que tiene upstream de acotar una key: una
  lista negra, no un modelo de permisos.

Conclusión: **omitir `@AllowAnonymous()` no demuestra nada sobre el alcance.**
El alcance se agrega explícitamente:

1. Un **usuario de servicio dedicado** en el CRM (p. ej.
   `bot-intouch@in-touchcrm.cl`) es el dueño de la key. Nunca la key de una
   persona.
2. El controlador exige que la petición **traiga `x-api-key`** y rechaza una
   sesión de cookie de navegador — el espejo de `SessionOnlyMiddleware`, para
   que el navegador de un comercial autenticado no pueda postear leads.
3. El controlador exige `session.user.id === INTOUCH_INGEST_USER_ID`. Sin esto,
   **la key de cualquier usuario del CRM podría crear leads**.
4. Rate limit: el plugin lo tiene apagado globalmente, así que va un
   `limit_req` de nginx en la `location` de ingesta.

Y **el chequeo del punto 3 no alcanza por sí solo**: una key de un usuario con
acceso total, verificada sólo en la ruta de ingesta, sigue sirviendo para leer
contactos, exportar y tocar ajustes por cualquier otra ruta. Así que se **mide**
contra qué otras rutas sirve la key (Task 2, Step 4) y, si abre el resto de la
API, el diseño de la credencial cambia antes de seguir: o se le saca
`enableSessionForAPIKeys` a esta key, o se monta una credencial de integración
fuera de Better Auth verificada por un guard sólo en `/api/ingest`.

Y el orden de los chequeos importa: si la key es inválida, el guard puede haber
resuelto la sesión desde la **cookie** del navegador, y entonces el usuario de
la sesión es un comercial y no la integración. Por eso se exige la cabecera
antes de mirar la sesión.

Se verifica empíricamente con siete casos (§9, I08), no por lectura.

### 6.2 — El comercial: puente `grancrm_session`

**Primero, si existe integración estándar.** Verificado: el orquestador **no
tiene ninguna capacidad de OIDC, OAuth ni SAML** (grep de `oidc|openid|
authorize|oauth|well-known|jwks|saml` en todo el repo: cero resultados
funcionales). El contrato de identidad del ecosistema es **la cookie
`grancrm_session` con un JWT HS256**, y cada satélite lo consume con su propia
copia de `grancrm_auth/middleware.py` — hoy hay ~5 copias divergentes.

Entonces el puente no inventa un mecanismo: **porta el contrato estándar
existente** a TypeScript, porque éste es el primer satélite no-Django. Eso es la
justificación que el encargo exige antes de escribir auth propio.

**Y se hace más delgado que las 5 copias**, por un hallazgo: la **revocación de
sesión sólo la aplica el orquestador**. `core/decorators.py` chequea
`SesionActiva.objects.filter(jti=jti).exists()`, pero `decode_token()` no. Un
satélite que decodifica el JWT localmente **no tiene revocación**: el token de
alguien que cerró sesión sigue válido hasta 8 horas.

Por eso el puente **delega en `GET {ORQUESTADOR}/api/session/`**, reenviando la
cookie, en vez de decodificar el JWT localmente:

- Gana **revocación**, que el decode local no da.
- El CRM **nunca necesita `GRANCRM_JWT_SECRET`** — un secreto menos que
  distribuir, y un radio de daño menor.
- Una sola llamada al establecer la sesión, no por request: después Better Auth
  lleva su propia cookie con `cookieCache`.

### 6.3 — Los tres chequeos de alcance

Claims reales que emite `core/jwt_utils.py::encode_token`: `jti`, `user_id`,
`login_id`, `email`, `nombre`, `tenant_id` (= `cuenta.slug`), `db_name`, `rol`,
`rol_real`, `apps` (lista de **ids** de `Aplicacion`, no slugs), `exp`, `iat`.

1. **Cuenta / instancia**: `tenant_id` debe ser igual al único
   `CRM_CUENTA_SLUG` configurado. **Éste es el chequeo que faltaba**: un CRM
   single-workspace es válido, pero no debe admitir a otras cuentas de GranCRM.
   Sin esto, cualquier cuenta del ecosistema con la app habilitada entraría a
   ver los mismos datos comerciales.
2. **Aplicación**: el id de `Aplicacion` del CRM debe estar en `apps`.
3. **Rol**: `rol_real` en una lista blanca. Se lee **`rol_real`, no `rol`** —
   el modo compatibilidad colapsa `agente` y `supervisor` en `ejecutivo`, y
   `admin_ti` en `sa`. Es la lección que InciTrack ya pagó (commit `95d5368`).

Recién después se crea la sesión de Better Auth. **La identidad se vincula por
cuenta + id estable del usuario de GranCRM, no por correo**: un upsert por
`email` permitiría tomar una cuenta existente del CRM creando un usuario de
GranCRM con ese mismo mail. Y no se marca `emailVerified`: GranCRM autenticó a
la persona, no comprobó que sea dueña de esa dirección.
`ALLOWED_SIGN_IN` sigue actuando como segunda puerta.

### 6.3.1 — La autorización se revalida, no se concede una vez **[corregido]**

El diseño anterior validaba **sólo al entrar**, y eso era un agujero real:
después el CRM andaba con su propia cookie de Better Auth, así que un comercial
que cerraba sesión en GranCRM —o al que le retiraban el rol o la cuenta— seguía
entrando hasta que expirara la sesión local.

Cada petición protegida comprueba tres cosas: que exista sesión local, que
tenga vínculo de GranCRM, y que ese vínculo siga vigente en el orquestador.
**Sin caché positiva entre peticiones** — cachear el "sí" es exactamente lo que
permite seguir operando después de una revocación; se deduplica dentro de una
misma petición. Vale para todas las superficies: tRPC, REST, SSR, server
actions, exports y streams. El menú y el middleware del frontend no cuentan.

El costo hay que **medirlo**, porque es una llamada al orquestador por petición
en una app con SSR: si abrir una pantalla dispara decenas de revalidaciones, la
salida es reducir las peticiones protegidas o negociar un TTL corto y
explícito, con el número a la vista.

**El límite, dicho sin prometer de más:** la revocación es efectiva en la
siguiente comprobación autoritativa. Lo ya enviado al navegador no se retira, y
una transacción autorizada y terminada no se cancela.

El logout limpia las dos sesiones y borra el vínculo.

`view_as_sa` (el "ver como" del SA) entra, porque así funciona el soporte, pero
**nunca es dueño de un Deal** y se registra como impersonación.

Esto sigue siendo código de auth propio: va con tests dedicados y **una review
de seguridad propia antes de habilitarlo**.

### 6.4 — Lo que el aislamiento no puede dar

`Company`, `Contact` y `Deal` **no tienen `organizationId`**: el CRM es
single-workspace. **No hay aislamiento de datos entre cuentas dentro del CRM**, y
no lo va a haber sin un trabajo que no es parte de esto.

Lo que sí se puede garantizar es lo de §6.3.1: **quién logra crear sesión**. La
prueba I14 se reformula en esos términos (§9). Presentar esto como multi-tenant
sería exactamente lo que el prompt §C02 prohíbe.

---

## 7. Lo que queda apagado

Nada de research agent (`apps/agent` no se despliega), Google/Microsoft/Slack,
mailbox sync, enriquecimiento, Vercel Blob, AI Gateway, tracking de sitios ni
landing de marketing (`IS_MARKETING` sin definir).

**`CRM_TELEMETRY_DISABLED=1`**: por defecto el CRM manda un evento diario de
conteos a un proyecto de terceros. Es tráfico saliente desde este servidor que
nadie pidió, y el prompt §8 es explícito en no habilitar capacidades que no se
necesitan para recibir y gestionar leads.

---

## 8. Build, BD y despliegue

- Se respeta el runtime del CRM: **bun 1.3.12** dentro de la imagen (no está en
  el host, y no hace falta instalarlo ahí), Node ≥22, workspaces de turbo y
  `bun.lock` tal como viene.
- Dockerfiles nuevos para `crm-app` y `crm-api` (upstream no trae ninguno), con
  generación del cliente Prisma en el build.
- Migraciones **revisables** con `prisma migrate dev` en desarrollo aislado, y
  `prisma migrate deploy` para el entorno persistente. **Nunca `db push`, nunca
  `reset` ni `--accept-data-loss`** contra datos reales. Se verifica estado y
  deriva antes de desplegar.
- Postgres con volumen nombrado, backup y readiness comprobados. Un rollback de
  código no revierte un schema.
- Variables requeridas: se derivan **del código**, no del prompt viejo.
  `GEMINI_API_KEY` no aparece en esta versión del CRM y no se configura.

---

## 9. Pruebas, con su bloqueo exacto

Datos sintéticos marcados `PRUEBA INTEGRACIÓN`, correos `@example.com`, y
automatismos externos desactivados. Nada de WhatsApp ni correo a direcciones de
ejemplo.

**Los bloqueos se separan.** Meta y el RAG bloquean el E2E conversacional; no
bloquean el receptor, el pipeline ni la navegación.

### Grupo A — sin ningún bloqueo (corre en cuanto el CRM está arriba)

| ID | Prueba | Resultado requerido |
|---|---|---|
| I03 | Mismo `evento_id`, secuencial **y concurrente** (N en paralelo) | Un solo efecto comercial; 1 `created` + N−1 `replayed`; mismos ids. Prueba que el unique + la transacción resuelven la carrera real |
| I04 | Mismo `evento_id`, payload distinto | 409, cero escrituras |
| I04b | `revision` vieja llegando después de una nueva | 409 stale, la nueva no se pisa |
| I05 | Nueva calificación del mismo contacto | `updated`, `revision`+1, sin reset de etapa ni borrado de datos válidos |
| I06 | Booleanos `false`, `true`, `null`, ausente y **el string `"false"`** | Semántica documentada; el string **no** se vuelve `true` |
| I07 | JSON inválido, body enorme, arrays/enums/correo inválidos, clave desconocida | Error de entrada controlado; **ninguna escritura parcial** |
| I08 | **Siete casos**: key del principal → 201 · key válida de **otro usuario** → 403 · sin key → 401 · key inválida → 401 · sólo cookie de comercial → 401/403 · **cookie válida + key inventada** → 401/403 · **la key de ingesta contra el resto de la API** → 401/403 | Los dos últimos son los que pasarían en silencio: uno confundiría la identidad de la cookie con la credencial, el otro deja una credencial de acceso total disfrazada de limitada |
| I16 | Logs y respuestas de error | Correlación útil; sin secretos, sin stack, sin PII innecesaria |
| I17 | Identidad: varios contactos con el mismo teléfono; teléfono y correo apuntando a contactos distintos; correo de dominio gratuito | 409 sin escrituras en los dos primeros; ninguna Company creada por gmail.com |
| I18 | Arrays con comas dentro de un elemento | El JSON de `LeadIngestEvent.payload` reconstruye el array exacto |

### Grupo B — necesita gateway + CRM desplegado

| ID | Prueba | Resultado requerido |
|---|---|---|
| I13 | `/crm/`, ruta profunda recargada, assets, API, login y logout | Flujo correcto **por el gateway**, no por acceso directo al puerto. Cookies y redirects verificados, no sólo el HTML inicial |
| I14 | Usuario de otra **cuenta** de GranCRM | **No logra crear sesión** (§6.3.1). Reformulada: el CRM es single-workspace y no aísla datos, así que lo que se prueba es el acceso, no la visibilidad |
| I15 | Build, migraciones y reinicio de servicios | Configuración reproducible, datos persistentes |
| I02 | Pipeline y detalle con un comercial autorizado | Registro visible; los 22 campos preservados o mapeados **explícitamente** |

### Grupo C — bloqueada **sólo** por el login SQL `intouch_login_qa`

Es lo que hace falta para que el proceso del bot arranque. **No** requiere Meta
ni RAG.

| ID | Prueba | Resultado requerido |
|---|---|---|
| I01 | Lead sintético inyectado en `LeadInTouch` → despacho real por el emisor | Confirmación con ids y registro comercial persistido. La URL se comprueba **desde el contenedor del bot**, no desde el host |
| I09 | CRM/BD caídos, y timeout después del commit | Contingencia visible; reintento con el mismo `evento_id`; sin duplicados |
| I10 | Reiniciar bot y receptor con un envío pendiente | El cron recupera el pendiente sin volver a crearlo |
| I11 | 2xx sin ids, HTML, redirect a login, body inválido | El bot **no** registra éxito falso |
| I12 | `solicita_contacto_humano` | `Activity(TASK)` visible y accionable para el responsable |

### Grupo D — bloqueada por credenciales de Meta (y sólo esto)

| ID | Prueba | Resultado requerido |
|---|---|---|
| I19 | Conversación real de WhatsApp de punta a punta | El extractor captura, el score califica y el lead llega al pipeline sin que nadie lo inyecte a mano |

El RAG en Supabase **no bloquea ninguna** de estas pruebas: afecta la calidad de
las respuestas del bot, no el despacho del lead.

Para cada prueba se registra comando, entorno, versión, esperado, observado y
PASS/FAIL/NO EJECUTADO. La validación funcional del emisor usa entrada sintética
sin gastar LLM; el Grupo D es el único que consume modelo, y va separado.

---

## 10. Datos que faltan (bloqueantes externos)

1. **`INTOUCH_LEAD_OWNER_EMAIL`** — qué usuario del CRM es dueño de los leads
   del bot. `Deal.ownerId` es obligatorio. Sin este dato la ingesta responde
   **503 y el lead queda pendiente**, en vez de inventar un dueño.
2. **`CRM_CUENTA_SLUG`** — a qué cuenta de GranCRM pertenece esta instancia
   (§6.3.1).
3. **Qué roles** de GranCRM pueden entrar al CRM, y si `view_as_sa` entra.
4. **Registro del CRM como `Aplicacion` en DIOS**, para tener su id (§6.3.2).
   Modo `iframe`: `spa_remote` no aplica a una app Next.js completa, que no
   expone `remoteEntry.js`.
5. **El login SQL `intouch_login_qa`** de `wsp_intouch` — desbloquea el Grupo C.
6. **Credenciales de Meta** — desbloquean el Grupo D.

`GRANCRM_JWT_SECRET` **ya no hace falta**: el puente delega en `/api/session/`
(§6.2).

---

## 11. Estado final esperado

Con los grupos A, B y C en verde: **VALIDADO EN PRUEBAS, PENDIENTE DE
DESPLIEGUE** para la parte del CRM, y el Grupo D declarado como validación
externa pendiente.

No se va a declarar "CRM conectado" por una variable de entorno, un contenedor
`healthy` ni un HTTP 201. El criterio es el del encargo: **tool/emisor del bot →
persistencia correcta → pipeline accesible al comercial autorizado**, con
reintentos y fallos comprobados.
