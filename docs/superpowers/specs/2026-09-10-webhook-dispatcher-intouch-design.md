# Dispatcher de webhook propio para la App de Meta de InTouch — diseño

Fecha: 2026-09-10 · Estado: **DISEÑO, SIN IMPLEMENTAR**

Objetivo: que Meta entregue los mensajes de **todos** los números de la App de
InTouch a una URL propia y estable, y que un mapa de configuración decida qué
bot atiende cada número — en vez de que Meta apunte a la ruta interna de un bot
en particular.

Encargo del usuario (2026-09-10), literal en lo que fija el alcance:

> «quiero mantener lo de varios números al mismo webhook pero 1 webhook por app,
> porque lo que está en `wsp_webhook` es incluso de otro portfolio comercial y es
> otra app; entonces ahora todos los números de InTouch van a pasar por la app
> que hice ahora para InTouch y eso sería `wsp_webhook_intouch`.»

---

## 0. Lo que ya existe en el servidor

Todo lo de esta sección está **verificado en el host el 2026-09-10**, no
supuesto. Corrige en dos puntos a la documentación vigente.

### 0.1 `wsp_webhook` no está «en mantenimiento»: está en producción

`/home/admincrm/CLAUDE.md` lo lista junto a `wsp_pompeyo` como «anteriores, en
mantenimiento». Los hechos:

| Qué | Estado verificado |
|---|---|
| Contenedor `wsp_webhook-web-1` | **Up hace 3 semanas**, `0.0.0.0:6020->6020` |
| Publicado en el gateway | Sí — `nginx.conf`, `location = /webhook` → `127.0.0.1:6020` |
| `BOT_MAP` | 3 entradas; una de ellas despacha a `:8020`, o sea **wsp_demo en producción** |

O sea que el patrón que pide el encargo ya está corriendo. Lo que falta no es
inventarlo: es una segunda instancia para otra App de Meta.

**Acción documental:** corregir esa fila del `CLAUDE.md` raíz. Un doc que
declara «en mantenimiento» un componente que está en el camino crítico de
producción es exactamente la clase de trampa que ya costó caro con
`docs-repo/notificaciones.md`.

### 0.2 Hay dos Apps de Meta, no una

Comparando huellas SHA-256 de los secretos (nunca los secretos):

| App | Huella `APP_SECRET` | Quién la usa | `phone_number_id` |
|---|---|---|---|
| **A** | `7caaa26e…` | `wsp_demo` (:8020) y el dispatcher `wsp_webhook` | `1131838863357028` |
| **B** | `86261797…` | `wsp_cavem` (:8030) y `wsp_intouch` (:8040) | `1266650873204031` |

Los dos bots de la App B comparten `WHATSAPP_PHONE_ID` **y** `WHATSAPP_BUSINESS_ACCOUNT_ID`:
es un solo número físico, `+56 2 2927 3658`, con dos bots capaces de atenderlo.

Esto explica por qué `wsp_intouch` hoy **no** pasa por el dispatcher, y está
escrito en el propio `nginx.conf` (línea ~415):

> «Reusa el número, el token y el App Secret de Cavem, así que tampoco pasa por
> el dispatcher de abajo: ese valida la firma con un único `APP_SECRET` distinto.»

El bloqueo real es `wsp_webhook/webhook.py:9`: `APP_SECRET` es **una sola
variable de entorno**. Un dispatcher no puede validar la firma de dos Apps.

### 0.3 El bot ya tiene las dos puertas de entrada

`wsp_intouch/config/urls.py`:

```python
path("webhook",          webhooks.webhook)           # valida firma HMAC de Meta
path("internal/webhook", webhooks.internal_webhook)  # NO valida nada
```

`internal_webhook` (`bot/whatsapp/webhooks.py:144-147`) hace `json.loads` y
despacha. Sin firma, sin token, sin nada. Es la puerta que espera el
dispatcher, y es el mismo diseño con el que hoy funciona wsp_demo.

### 0.4 La URL pública ya está provisionada y probada

`location = /intouch/webhook` ya existe en el gateway y responde. Verificado
hoy: handshake `GET` devuelve el `hub.challenge` con el token correcto y `403`
con uno incorrecto; `POST` sin firma da `403` (o sea, llega a Django). El match
exacto (`=`) es obligatorio: sin él la ruta cae en el catch-all de la SPA y
devuelve `405`.

**Consecuencia de diseño:** no hace falta inventar una URL nueva. Se reapunta
esta, y todo lo ya verificado sigue valiendo.

---

## 1. Decisiones tomadas y descartadas

| Decisión | Elegida | Descartada, y por qué |
|---|---|---|
| Alcance | **Un dispatcher por App de Meta** | Un dispatcher único multi-App: el usuario separa portfolios comerciales a propósito; el aislamiento es requisito de negocio |
| Forma | **Repo propio con código copiado** | Una imagen con dos contenedores: menos drift, pero acopla los dos portfolios en un repo y un deploy malo los tumba juntos |
| URL | **Reusar `/intouch/webhook`** | Una URL nueva: perdería el handshake ya probado sin ganar nada |
| Cavem | **El número pasa a InTouch y Cavem se apaga** | Dejarlo arriba sin tráfico, o dos números en paralelo |
| `internal/webhook` | **Secreto compartido en un header** | Bind a `127.0.0.1`: rompería al orquestador y al launcher, que llegan al bot por IP de host |

### 1.1 Sobre el drift: es un costo aceptado, no un descuido

Éste es el segundo dispatcher con el mismo código copiado a mano. Es la versión
chica del «drift de tres repos» (biblia §VI.1). Se acepta porque son ~60 líneas
sin lógica de dominio y porque el aislamiento entre portfolios lo pidió el
negocio. Se mitiga así:

- `webhook.py` arranca con una cabecera que dice de dónde salió y que todo
  arreglo debe replicarse en el hermano.
- Si aparece una **tercera** App de Meta, ahí sí corresponde extraer un
  `wsp-webhook-core` en vez de una tercera copia.

---

## 2. Arquitectura

### 2.1 Componentes

**Nuevo: `/home/admincrm/wsp_webhook_intouch/`**

```
webhook.py            FastAPI: verificación, validación de firma, despacho
Dockerfile            python:3.12-slim, uvicorn en :6030
docker-compose.yml    puerto 6030, extra_hosts host.docker.internal
requirements.txt      fastapi==0.115.0, uvicorn==0.32.0, httpx==0.27.0
.env.example          documentado, sin secretos
.env                  NO versionado
```

Es el mismo archivo que `wsp_webhook/webhook.py` más el envío del header de
`§2.3`, y con el puerto cambiado.

**Modificado: `wsp_intouch`**

- `bot/whatsapp/webhooks.py` — `internal_webhook` verifica el token
- `config/settings.py` — `WEBHOOK_INTERNAL_TOKEN`
- `bot/management/commands/doctor.py` — chequeo del token
- `.env.docker` — el valor

**Modificado: `wsp_cavem`**

- El mismo `WEBHOOK_INTERNAL_TOKEN` y la misma verificación en su
  `internal_webhook` (`bot/whatsapp/webhooks.py:144`, verificado idéntico al de
  InTouch). No es para que Cavem opere: es para que el rollback de §5.2 funcione
  sin configurar nada en medio del incidente.

**Modificado: `gateway/nginx.conf`**

- `location = /intouch/webhook`: `proxy_pass` de `127.0.0.1:8040/webhook` a
  `127.0.0.1:6030`

### 2.2 El mapa de números

`.env` de `wsp_webhook_intouch`:

```
BOT_MAP={"1266650873204031": "http://host.docker.internal:8040/internal/webhook"}
```

**`host.docker.internal`, no una IP fija.** `wsp_webhook` usa IPs literales
(`172.20.21.249`, `172.20.21.248`), y acá eso sería una bomba: este host
responde **a las dos IPs a la vez** (verificado con `hostname -I`), que es el
conflicto de cloud-init diagnosticado el 2026-07-21 y arreglado **sólo en
runtime** — la corrección persistente sigue abierta. Un reboot puede reordenar
eso y dejar el `BOT_MAP` apuntando al host equivocado, con un síntoma
malísimo: mensajes que se pierden en silencio. `host.docker.internal` ya está
declarado en el `docker-compose` heredado (`extra_hosts: host-gateway`) y no
depende de qué IP quedó arriba.

Agregar un número futuro de la App B = una entrada más en este JSON. Nada de
código, nada de Meta.

### 2.3 El parche de `internal/webhook`

Hoy cualquiera que alcance `:8040` puede inyectar mensajes de WhatsApp
arbitrarios sin credencial: `internal_webhook` no valida nada y los contenedores
publican en `0.0.0.0` (verificado: `:8030`, `:8040`, `:6020`). **No se pudo
verificar el firewall del host** (no hay `sudo` sin contraseña en esta sesión),
así que no se afirma ni que sea alcanzable desde internet ni que no lo sea. El
agujero es preexistente —wsp_demo ya vive así— pero este diseño lo extendería a
InTouch, y no se hereda en silencio.

El dispatcher manda:

```
X-Internal-Token: <WEBHOOK_INTERNAL_TOKEN>
```

y `internal_webhook` lo compara con `hmac.compare_digest` (no con `==`: la
comparación de tiempo constante es el punto).

**Falla cerrado, y ésta es la parte que importa:**

| Situación | Respuesta |
|---|---|
| Token correcto | `200`, despacha |
| Token ausente o incorrecto | `403`, no despacha |
| `WEBHOOK_INTERNAL_TOKEN` vacío en el bot | **`503` y log de error** — no «abierto» |

La tercera fila es deliberada. La alternativa natural —«si no hay token
configurado, no validar»— convierte un despliegue con la variable olvidada en
un endpoint abierto sin ningún síntoma. Es exactamente el modo de falla que ya
se pagó en este ecosistema: la cosa arranca perfecto y falla en silencio. Un
`503` rompe fuerte y se ve.

Se agrega además un chequeo al `doctor` para que la variable no se pueda
olvidar.

### 2.4 Flujo completo

```
Meta
  └─ POST https://qadash.in-touchcrm.cl/intouch/webhook
       └─ nginx (match exacto) → 127.0.0.1:6030
            └─ dispatcher: valida firma HMAC con el APP_SECRET de la App B
                 ├─ firma inválida → 403, fin
                 └─ firma válida → responde 200 a Meta INMEDIATAMENTE
                      └─ background: POST a BOT_MAP[phone_number_id]
                           con X-Internal-Token y el body crudo sin tocar
                           └─ wsp_intouch :8040 /internal/webhook
                                ├─ token malo → 403
                                └─ token OK → _dispatch → el grafo
```

El body se reenvía **byte por byte** (`content=body`), no reserializado. No es
cosmético: si el dispatcher hiciera `json.loads` + `json.dumps`, cualquier
revalidación de firma aguas abajo fallaría.

---

## 3. Manejo de errores

| Falla | Comportamiento | Nota |
|---|---|---|
| Firma de Meta inválida | `403` | Igual que hoy |
| `phone_number_id` sin mapear | Log, `200` a Meta | Igual que hoy |
| Bot caído o lento | Log de error | **Deuda: el mensaje se pierde** |
| Token interno malo | `403` del bot | Nuevo |
| Token no configurado | `503` del bot | Nuevo, falla cerrado |

### 3.1 Deuda que este diseño NO resuelve, y hay que decirla

El dispatcher le contesta `200` a Meta **antes** de entregarle al bot. Si el bot
está caído, Meta ya se dio por satisfecho y **no reintenta**: ese mensaje se
perdió, y sólo queda la línea de log.

Es el comportamiento actual de `wsp_webhook` en producción y no se cambia acá
—resolverlo bien es una cola persistente con reintentos, que es su propio
diseño— pero queda escrito para que sea una decisión y no una sorpresa.

---

## 4. Testing

**`wsp_intouch` (por TDD, el repo tiene 1906 tests en verde):**

1. `internal_webhook` con token correcto → `200` y despacha
2. con token incorrecto → `403` y **no** despacha
3. sin el header → `403` y **no** despacha
4. con `WEBHOOK_INTERNAL_TOKEN` vacío → `503`
5. el `doctor` marca falla si la variable no está

«No despacha» se verifica con un mock del despachador. Un test que sólo mire el
código de estado pasaría con un endpoint que rechaza *y además* procesa.

**Dispatcher (`curl`, sin framework nuevo para 60 líneas):**

6. `GET` con el verify token correcto → devuelve el `hub.challenge`
7. `GET` con token incorrecto → `403`
8. `POST` con firma HMAC válida → `200`
9. `POST` con firma inválida → `403`
10. `POST` con un `phone_number_id` desconocido → `200` y log de «no mapeado»

**Integración, antes de tocar Meta:**

11. `POST` firmado a `https://qadash.in-touchcrm.cl/intouch/webhook` y verificar
    en los logs de `wsp_intouch` que el turno **llegó al grafo**

**End-to-end, después de repuntar Meta:**

12. Mensaje real de WhatsApp al número, con user-agent de Meta en el log (no `curl`)

### 4.1 El criterio de «hecho», explícito

Ninguna de estas afirmaciones vale por el exit code del comando. Este mismo día,
`reindexar_conocimiento_rag` **salió con exit code 0 habiendo fallado 1 de 7
páginas**. Cada chequeo de arriba se da por bueno con su evidencia observada, no
con la ausencia de error.

---

## 5. Puesta en marcha

Los pasos 1-4 son reversibles y **no tocan el tráfico**: Meta sigue apuntando a
Cavem hasta el paso 5.

| # | Paso | Quién | Verificación |
|---|---|---|---|
| 1 | Crear el repo, levantar el dispatcher en `:6030` | Claude | `curl` local: handshake OK |
| 2 | Token + tests en `wsp_intouch` **y en `wsp_cavem`**, desplegar los dos | Claude | Suite en verde en ambos; `doctor` en verde |
| 3 | Poner el mismo token en el `.env` del dispatcher | Claude | — |
| 4 | nginx: `/intouch/webhook` → `:6030` | Claude | Chequeo 11; y las vecinas sin romper |
| 5 | **Repuntar Meta** a `/intouch/webhook` | **Usuario** | El handshake da OK en el panel de Meta |
| 6 | Verificar que entra un mensaje real | Ambos | Chequeo 12 |
| 7 | Apagar Cavem en sus tres capas | Claude, con visto bueno | `PENDIENTES.md` §0.3 |

### 5.1 Coordinación obligatoria

El paso 4 toca `gateway/nginx.conf`, que es **compartido por todas las apps**.
Al 2026-09-10 hay otra sesión activa (`admincrm-9a`) trabajando en ese archivo
—agregó un `server { listen 8444 ssl }` para compai-crm— así que antes del paso
4 va un `SendMessage`, y la edición se hace con `cat >` y **nunca** con `mv` ni
`sed -i`: los dos cambian el inodo y dejan al contenedor viendo el archivo viejo
por el bind-mount. Después, `docker compose up -d --force-recreate nginx`, que
`nginx -s reload` solo no alcanza.

### 5.2 Rollback

| Hasta el paso | Cómo se vuelve |
|---|---|
| 1-3 | No hay nada que revertir: nada está cableado |
| 4 | Devolver el `proxy_pass` a `127.0.0.1:8040/webhook` y recrear nginx |
| 5-6 | Cambiar el valor del `BOT_MAP` a Cavem (`http://host.docker.internal:8030/internal/webhook`) — **sin entrar a Meta** |
| 7 | `docker compose start` en `wsp_cavem` y `activo=True` en el orquestador |

El rollback del paso 5-6 es el que justifica todo el diseño: hoy volver atrás
exige entrar al panel de Meta; después, es editar una línea de un JSON.

**Requisito para que ese rollback funcione:** `wsp_cavem` necesita el mismo
`WEBHOOK_INTERNAL_TOKEN`, porque su `internal/webhook` está igual de abierto que
el de InTouch. Se le pone en el paso 2, **antes** de apagarlo — un rollback que
depende de configurar algo en el momento del incidente no es un rollback.

---

## 6. Fuera de alcance

- **Backportear el token a `wsp_webhook`.** Conviene, y con esto queda
  recomendado por escrito, pero es otro portfolio comercial y puede tener dueño
  en otra sesión. No se toca sin pedirlo.
- **Cola persistente con reintentos** para el mensaje que se pierde con el bot
  caído (§3.1).
- **Bind de los bots a `127.0.0.1`.** Descartado en §1: rompería al orquestador.
- **Extraer `wsp-webhook-core`.** Recién con una tercera App (§1.1).
- **Multi-tenant dentro del dispatcher.** El `BOT_MAP` alcanza; un número más
  es una entrada más.
