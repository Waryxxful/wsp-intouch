# Traspaso del número de Cavem a InTouch

Desactivar `wsp_cavem` **sin borrarlo** y pasarle su número de WhatsApp y sus
credenciales de Meta a `wsp_intouch`, **sin que se mezcle nada** entre los dos.

> **Nada de este documento está ejecutado.** Cavem sigue corriendo (healthy).
> El orden importa: los pasos 1 y 2 van **antes** de apagar Cavem, porque si no
> el número queda sin ningún bot atendiendo.

---

## Lo primero: reutilizar el número NO desbloquea el despliegue

Las credenciales de Meta y el login SQL son cosas **independientes**. El número
se puede reutilizar hoy; el login **no se puede reutilizar nunca**, y sigue
faltando.

| | Se reutiliza | Por qué |
|---|---|---|
| Las cinco `WHATSAPP_*` | **Sí** | Son del número y de la Meta App, no del bot |
| `DB_USER` / `DB_PASSWORD` (`cavem_login_qa`) | **JAMÁS** | Ver abajo |

**Por qué el login no**: `DB_SCHEMA` del `.env` es decorativo — el schema donde
el bot escribe lo fija el `DEFAULT_SCHEMA` del login. Con el login de Cavem,
InTouch escribiría en el schema `cavem`. Y como `Conversation` **no tiene columna
`cliente`** y su `wa_id` es `UNIQUE`, no serían dos conjuntos de datos en la
misma tabla: **serían las mismas filas**. Un contacto que ya habló con Cavem
reanudaría *esa* conversación dentro de InTouch, con su `flow_state`, su
`flow_data` y su historial. El daño lo hace el `migrate` del arranque, antes del
primer mensaje.

Antecedente idéntico en este stack: Cavem arrancó una vez con el login de
`wsp_demo` y aplicó 5 migraciones en el schema de producción de Renault/Astara
(`PENDIENTES.md` #12).

**El script está listo**: `docs/sql/crear_login_intouch.sql`. Replica lo que hace
el provisionador del orquestador, es idempotente, y trae la verificación
obligatoria al final.

---

## Paso 1 — Crear el login (DBA)

Correr `docs/sql/crear_login_intouch.sql` y **verificar** con el login nuevo:

```sql
SELECT DB_NAME(), SCHEMA_NAME(), CURRENT_USER;
-- tiene que decir:  QAIntouch | intouch | intouch_login_qa
```

Completar `DB_USER` y `DB_PASSWORD` en `wsp_intouch/.env.docker`.

## Paso 2 — Dejar InTouch listo para recibir, con Cavem todavía arriba

Se puede hacer todo esto sin tocar Cavem:

```bash
cd /home/admincrm/wsp_intouch
docker compose up -d --build
docker compose exec web python manage.py migrate leads --database=qaintouch --noinput
docker compose exec web python manage.py migrate --noinput
docker compose exec web python manage.py seed_intouch
docker compose exec web python manage.py doctor          # cero fallas es el piso
```

Más el schema del RAG en Supabase y la carga del conocimiento — ver
`PENDIENTE_CREDENCIALES.md` §2 y §4. **Los siete `.md` esperan tu revisión antes
de indexar.**

InTouch queda escuchando en el 8040 sin recibir nada: su `location` de nginx no
existe todavía y Meta sigue apuntando a Cavem.

---

## Paso 3 — Copiar SOLO las credenciales del número

De `wsp_cavem/.env.docker` a `wsp_intouch/.env.docker`, **estas cinco y nada más**:

```
WHATSAPP_TOKEN
WHATSAPP_PHONE_ID
WHATSAPP_VERIFY_TOKEN
WHATSAPP_APP_SECRET
WHATSAPP_BUSINESS_ACCOUNT_ID
```

**No copiar** (rompen el aislamiento): `DB_USER`, `DB_PASSWORD`, `DB_SCHEMA`,
`RAG_SCHEMA`, `CLIENTE_ACTIVO`, `PUBLIC_BASE_URL`, `LANGFUSE_TRACING_ENVIRONMENT`.

Dos avisos concretos:

- **`CLIENTE_ACTIVO=cavem` es un valor legal en InTouch** (`bot/models.py` lo
  lista en `CLIENTE_CHOICES`). Si queda así, InTouch pasa los dos system checks
  y lee el RAG de Cavem sin un solo error. Tiene que decir `intouch`.
- **`WHATSAPP_APP_SECRET` vacío desactiva la validación de firma**
  (`bot/whatsapp/webhooks.py:27-28`: si está vacío, `_valid_signature` devuelve
  `True`). Un `.env` a medio llenar deja el webhook abierto.

---

## Paso 4 — Desactivar Cavem: tres capas, todas reversibles

### 4.1 Parar el contenedor

```bash
cd /home/admincrm/wsp_cavem
git status --short          # antes de tocar nada: que no haya trabajo sin commitear
docker compose stop
```

Corta el webhook, el panel y los cuatro threads que arranca `bot/apps.py::ready()`
(registro en DIOS, aviso de schema, scheduler de scraping, seguimientos). Todos
son threads daemon dentro de gunicorn: **mueren con el contenedor, no queda nada
corriendo**.

`restart: unless-stopped` respeta un `stop` explícito, así que **no revive al
reboot del host**. Los volúmenes `media_data` y `whatsapp_media_data` quedan
intactos.

Reversible con `docker compose start`.

### 4.2 Sacar su webhook del gateway — **se coordina**

`gateway/nginx.conf` lo comparten **todas** las apps del host. Avisar antes.

Comentar el bloque de las líneas 379-383:

```nginx
# location = /cavem/webhook {
#   proxy_pass http://127.0.0.1:8030/webhook;
#   ...
# }
```

y agregar el de InTouch, que ya está escrito en `DEPLOY_INTOUCH.md:246-248`:

```nginx
location = /intouch/webhook {
  proxy_pass http://127.0.0.1:6030/webhook;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto $scheme;
}
```

`6030` y no `8040`: ya no se apunta al bot directo, se apunta al dispatcher
`wsp_webhook_intouch`, que valida la firma HMAC y reenvía según
`phone_number_id`. Y el `proxy_pass` va **con** el path `/webhook` al final
— sin él, nginx reenvía la URI original completa y el dispatcher, que sólo
sirve `/webhook`, devuelve 404 en vez de 403 (medido).

Va **antes** de los catch-all, y el `= ` (match exacto) no es opcional: sin él
lo captura la SPA.

```bash
cd /home/admincrm/gateway && docker compose up -d --force-recreate nginx
```

`nginx -s reload` **no aplica** los cambios: el conf es bind-mount de un archivo.

### 4.3 Desactivar la app en el orquestador

Sin esto, Cavem **queda listado en el launcher con su tile en 502**, y el
orquestador sigue replicando su schema a las bases de tenant. No hay heartbeat:
el registro es push y nadie escribe el estado `caido`.

En el Django admin del orquestador (`/admin/`), sobre `Aplicacion` "Auto IA — Cavem":

```
activo = False
```

Es el apagado más limpio del stack: lo saca del launcher, del JWT y de
`sync_schemas`, **y sobrevive a un re-registro** — si alguien levanta el
contenedor, `register_app` no pisa ese flag (`views_internal.py:79-90`).

Reversible: el mismo checkbox.

---

## Paso 5 — Repuntar el webhook en Meta

**La URL que hay que poner NO es la que dicen los documentos.** Los dos runbooks
dicen `/wsp/<slug>/webhook`, y esa ruta cae en el catch-all de la SPA y devuelve
HTML, no Django. La ruta que funciona es sin el `/wsp/`:

```
https://qadash.in-touchcrm.cl/intouch/webhook
```

**Antes de cambiarla, leé del panel de Meta la URL que está configurada hoy** —
no se puede inferir del repo, y el número de Cavem vive en un portafolio de Meta
distinto del dispatcher, con su propia App y su propio App Secret.

Cavem **no** está en el `BOT_MAP` del dispatcher `wsp_webhook` (verificado), así
que no hay nada que sacar de ahí.

## Paso 6 — Verificación end-to-end

1. `manage.py doctor` desde el contenedor de InTouch, con red: cero fallas.
2. Desde un WhatsApp real, al número traspasado: un saludo → orienta sin abrir lead.
3. Una consulta por soluciones → llama a `listar_soluciones` (verificar en Langfuse).
4. Dar empresa, correo y necesidad → el bot **no** afirma que quedó registrado, y
   el lead aparece en el panel unos segundos después.
5. Una consulta de soporte → `crear_caso`, sin lead.

---

## Dos cosas que quedan mezcladas y no se resuelven apagando Cavem

**Langfuse comparte proyecto y keys.** Las trazas se correlacionan por el número
del **contacto** (`session_id = user_id = wa_id`), no por el bot. Con el número
reutilizado, un contacto que habló con Cavem y después con InTouch es **la misma
sesión**, y lo único que los separa es `LANGFUSE_TRACING_ENVIRONMENT` (`qa` vs
`development`), que es un filtro y no un límite de datos. Dejarlos distintos.

**La tabla `leads_lead` de la conexión `qaintouch`.** El router y el modelo son
idénticos byte a byte en los dos repos y esa conexión toma usuario y contraseña
de las **mismas** variables que la principal. Con logins distintos son dos tablas
en dos schemas; con el mismo login serían una. Es otra razón por la que el paso 1
no se saltea.

---

## Rollback

| Paso | Cómo se revierte |
|---|---|
| 4.3 `activo=False` | el mismo checkbox en el admin |
| 4.2 nginx | descomentar y `--force-recreate nginx` |
| 4.1 contenedor | `docker compose start` |
| 5 webhook | ver "Rollback del webhook" abajo — dos caminos, uno barato y uno caro |
| 1 login | `DROP USER` en QAIntouch + `DROP LOGIN` en master |

### Rollback del webhook: barato primero, Meta como último recurso

Desde que el número pasa por el dispatcher `wsp_webhook_intouch`, volver a
Cavem **no requiere entrar a Meta**. El dispatcher rutea por
`phone_number_id`, así que basta con reapuntar esa entrada en su `BOT_MAP`:

```bash
# en wsp_webhook_intouch/.env, la entrada del número traspasado:
# BOT_MAP={"<phone_number_id>": "http://host.docker.internal:8030/internal/webhook"}
cd /home/admincrm/wsp_webhook_intouch
docker compose up -d web   # NUNCA `restart`: no relee el .env
```

Cavem ya tiene el `WEBHOOK_INTERNAL_TOKEN` puesto para que esto funcione
— verificado: `/internal/webhook` responde 200 con el token correcto y 403
sin él. Confirmar en los logs del dispatcher: `dispatched … status=200`.

Sólo si el propio dispatcher estuviera comprometido o inalcanzable, el
camino caro es entrar al panel de Meta y reapuntar el webhook a
`/cavem/webhook` directamente — el rollback de último recurso, no el
primero.

Nada de esto borra datos de Cavem: su schema, sus conversaciones, sus leads y
sus volúmenes de media quedan donde están.
