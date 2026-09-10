# Integración Comp AI CRM ↔ wsp_intouch — evidencia

Última corrida: **2026-09-10**. Entorno: `172.20.21.249` (GranCRM-QA).

| Componente | Versión |
|---|---|
| CRM | `trycompai/crm` tag **v1.15.3**, SHA `3c3e07a` |
| Receptor | `compai-crm` rama `local/integracion-intouch`, commit `f9402e3` |
| Emisor | `wsp_intouch` rama `master`, commit `c8c1356` |
| Runtime CRM | bun 1.3.12, Node ≥22, Prisma 7.9.1, PostgreSQL 17 |

**Estado: IMPLEMENTADO EN LOS DOS EXTREMOS, E2E PENDIENTE.** El receptor está
completo y probado; el emisor también, por la sesión `admincrm-3a`; falta
correr el circuito conectado, bloqueado por un dato de negocio.

---

## Grupo A — receptor, sin ningún bloqueo

**98 tests**, `bun test` contra **PostgreSQL real** (nunca SQLite: ninguna
garantía sobre restricciones o carreras se afirma con otro motor). Corren en
el servicio `tools`, cuyo rol de BD **no puede conectarse a la base de la
app** — probado, no supuesto.

```
docker compose -f docker-compose.crm.yml run --rm \
  -e INTOUCH_INGEST_SECRET=… -e INTOUCH_LEAD_OWNER_EMAIL=… \
  -e BETTER_AUTH_SECRET=… -e ALLOWED_SIGN_IN=… \
  tools sh -c 'cd apps/api && bun test test/ingest-*.spec.ts'
→ 98 pass, 0 fail, 8 files
```

| ID | Prueba | Esperado | Observado | Estado |
|---|---|---|---|---|
| A01 | Restricción única `(origin, eventoId)` | Rechazo | `P2002` de Postgres | **PASS** |
| A02 | Restricción única `(origin, claveContacto, revision)` | Rechazo | `P2002`: `Unique constraint failed on the fields: (origin, claveContacto, revision)` | **PASS** |
| A03 | Array con coma dentro de un elemento | Se reconstruye exacto | `["whatsapp","correo, teléfono","web"]` íntegro desde `payload` | **PASS** |
| A04 | Los 14 campos dinámicos, semilla dos veces | Sin duplicar | 14 definiciones, idempotente | **PASS** |
| A05 | Semilla no pisa personalización ajena | `showOnTable`/`position` intactos | Intactos | **PASS** |
| A06 | `"false"` como string | **No** se vuelve `true` | Rechazado por el contrato | **PASS** |
| A07 | `usa_ia_actualmente` ausente / null / false | Tres estados distintos | Distinguidos | **PASS** |
| A08 | Clave desconocida en el body | Rechazo | 400 | **PASS** |
| A09 | `resolver` no escribe, ni al decidir crear | Cero filas | Cero empresas, cero contactos | **PASS** |
| A10 | Conflicto de contacto tras planificar empresa | **Cero** empresas creadas | Cero | **PASS** |
| A11 | Correo de dominio gratuito | No identifica empresa | `dominio: null` | **PASS** |
| A12 | Sin dominio, empresa homónima | **No** fusiona | `accion: crear` | **PASS** |
| A13 | Varios contactos con el mismo teléfono | Conflicto, cero escrituras | `telefono_ambiguo` | **PASS** |
| A14 | Teléfono y correo a contactos distintos | Conflicto | `identidad_dividida` | **PASS** |
| A15 | Match por correo con otro teléfono | Conflicto, no vincula | `telefono_incompatible` | **PASS** |
| A16 | Vínculo a contacto archivado | Conflicto, no recrea | `vinculo_roto` | **PASS** |
| A17 | Etapa inicial del Deal | `QUALIFIED_TO_BUY`, nunca `DEMO_BOOKED` | `QUALIFIED_TO_BUY` | **PASS** |
| A18 | Lead COLD | Sin Deal, conserva la necesidad | `dealId: null`, campo presente | **PASS** |
| A19 | `solicita_consultoria` | Genera seguimiento | `Activity(TASK)` con vencimiento | **PASS** |
| A20 | Mismo `true` en otro snapshot | **No** duplica la tarea | 1 tarea | **PASS** |
| A21 | Tarea ya completada | No se reabre | `completedAt` intacto | **PASS** |
| A22 | Nota idéntica repetida | No se repite | 1 nota | **PASS** |
| A23 | Sin empresa resoluble | No inventa una para la FK | `dealId: null`, log de aviso | **PASS** |
| A24 | Mismo evento repetido | Replay, mismos ids, cero efectos | `replayed` | **PASS** |
| A25 | **5 peticiones concurrentes** del mismo evento | Una sola aplicación | 1 `created`, 1 contacto, 1 Deal | **PASS** |
| A26 | Mismo `evento_id`, payload distinto | Conflicto, sin sobrescritura | 409, empresa no creada | **PASS** |
| A27 | Mismo `evento_id`, otra revisión | Conflicto aunque el hash coincida | 409 | **PASS** |
| A28 | Revisión vieja llegando después | No pisa a la nueva | `stale`, valor nuevo intacto | **PASS** |
| A29 | Repetir un evento **en conflicto** | Sigue en conflicto, nunca éxito con `contactId` vacío | `conflict` | **PASS** |
| A30 | Conflicto con revisión alta, luego válida menor | El cursor mira la **aplicada** | La menor se aplica | **PASS** |
| A31 | Actualización sobre Deal `CLOSED_WON` | No reinicia la etapa | `CLOSED_WON` intacto | **PASS** |
| A32 | Resolver un conflicto | No edita el payload | `payload` y `payloadHash` idénticos | **PASS** |
| A33 | Resolver deja auditoría | Actor, motivo, fecha, contacto | Los cuatro | **PASS** |
| A34 | Resolver dos veces | Idempotente | 1 Deal | **PASS** |
| A35 | Resolver tarde, con revisión posterior aplicada | `stale`, conflicto **abierto** | `stale`, sin sello | **PASS** |
| A36 | **Tras resolver, el reintento del emisor** | `replayed` con los ids | `replayed`, mismos `contactId`/`companyId`/`dealId`, 1 Deal | **PASS** |
| A37 | Conflicto repetido | Sale antes de resolver identidad | Cero empresas, cero contactos | **PASS** |

## Grupo B — el endpoint por HTTP, contra la API real

Corrido con `curl` contra `crm-api` ya rebuildeado, no sólo en la suite.

| ID | Prueba | Esperado | Observado | Estado |
|---|---|---|---|---|
| B01 | Sin credencial | 401 | `401` | **PASS** |
| B02 | Secreto en el header `x-api-key` | 401 (no abre) | `401` | **PASS** |
| B03 | Secreto inventado | 401 | `401` | **PASS** |
| B04 | Secreto de largo distinto | 401, sin colgarse | `401` | **PASS** |
| B05 | Cuerpo inválido con credencial buena | 400 | `400` | **PASS** |
| B06 | Cuerpo de **200 KB** | 413 | `413` | **PASS** |
| B07 | `Content-Type: text/plain` | 415 | `415` | **PASS** |
| B08 | **Payload válido sin dueño configurado** | **503**, cero escrituras | `503 {"status":"unavailable","motivo":"El dueño de los leads no está configurado en el CRM.","requestId":"3f6b9837…"}`; 0 eventos, 0 contactos | **PASS** |
| B09 | Secretos en los logs (canario) | 0 coincidencias | 0 para el secreto, `postgresql://`, `Traceback` y `BETTER_AUTH` | **PASS** |
| B10 | 401 de tRPC con `NODE_ENV=production` | Sin stack ni rutas internas | `{"code":"UNAUTHORIZED","httpStatus":401,"path":"contacts.list"}` | **PASS** |

**B08 es el que importa para el emisor**: 503 y no 409, así que lo clasifica
como transitorio y reintenta — verificado también del lado del emisor, que lo
lee como `fallo`.

**B10 antes de `NODE_ENV=production`** devolvía al cliente
`"stack":"TRPCError: UNAUTHORIZED\n at use (/app/apps/api/src/trpc/middlewares/auth.middleware.ts:18:14) at callRecursive (/app/node_modules/.bun/@trpc+server@11.18.0+…)"`
— rutas internas del servidor y versiones exactas de dependencias, en una
respuesta a alguien **sin credencial**.

## Grupo C — aislamiento de la instalación

| ID | Prueba | Esperado | Observado | Estado |
|---|---|---|---|---|
| C01 | Postgres publicado al host | No escucha | `5432` no escucha | **PASS** |
| C02 | API expuesta a la LAN | Sólo loopback | `127.0.0.1:3006` | **PASS** |
| C03 | **`crm_test` → base de la app** | Rechazo | `FATAL: permission denied for database "crm"` | **PASS** |
| C04 | `crm_app` cambia el esquema | Rechazo | `must be owner of table` | **PASS** |
| C05 | `crm_app` lee y escribe sus tablas | SELECT/INSERT/UPDATE/DELETE | Los cuatro en 61 tablas | **PASS** |
| C06 | `.env` o `.git` dentro de la imagen | Ninguno | Ninguno | **PASS** |
| C07 | Migraciones aplicadas | Sin deriva | `Database schema is up to date!`, 63 tablas | **PASS** |
| C08 | Telemetría de terceros | Apagada | `Anonymous usage telemetry is off for this install` | **PASS** |
| C09 | Agente de research | No desplegado | `No agent bridge secret` | **PASS** |

## Grupo D — E2E conectado: **NO EJECUTADO**

| ID | Prueba | Bloqueo exacto |
|---|---|---|
| D01 | Lead sintético del emisor real → pipeline | **`INTOUCH_LEAD_OWNER_EMAIL` sin definir.** Decisión de negocio del usuario: a qué usuario del CRM se le asignan las oportunidades. `Deal.ownerId` es obligatorio y no se inventa |
| D02 | Los 22 campos visibles en el detalle del contacto | Igual que D01, más abrir el CRM en el navegador (plan de acceso) |
| D03 | Respuesta perdida después del commit | Igual que D01. El proxy está escrito en el plan (Task 14) |
| D04 | El barrido recupera un pendiente tras reiniciar | Igual que D01 |
| D05 | Escritura sintética en `QAIntouch` | **Autorización del usuario** para escribir en producción. Son dos filas en tablas con cero filas, limpiables por `wa_id` exacto |
| D06 | Conversación real de WhatsApp | Credenciales de Meta |

## Grupo E — acceso del comercial: **NO EJECUTADO**

Es el otro subsistema
(`docs/superpowers/plans/2026-09-09-compai-crm-acceso-comercial.md`, 5 tareas).
Bloqueado en `CRM_CUENTA_SLUG`: a qué cuenta de GranCRM pertenece la
instancia, que define quién puede entrar.

---

## Decisiones abiertas, no bloqueantes

1. **`enableSessionForAPIKeys` afecta a TODAS las keys del CRM.** Cualquier key
   que un usuario cree en Settings abre la API completa con su identidad —
   medido: `POST /rest/contacts/search` y `POST /rest/contacts` responden 200.
   Apagarlo endurece la instalación entera pero cambia una función que upstream
   ofrece a los usuarios. **No se toca sin decisión explícita.**
2. **La etapa inicial `QUALIFIED_TO_BUY`** se eligió por descarte razonado, no
   porque sea correcta: afirma una calificación humana cuando lo que hubo fue
   un bot recogiendo antecedentes. Si el equipo comercial define una etapa de
   entrada, se cambia en un lugar y en el test que la ancla.
3. **`RecordSource.IMPORT`** para los contactos del bot: el enum no tiene valor
   para bots, y agregarlo rompería mapas de etiquetas de upstream en silencio.
   La procedencia real vive en el campo dinámico `origen`.
