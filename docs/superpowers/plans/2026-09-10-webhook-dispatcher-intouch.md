# Dispatcher de webhook para la App de Meta de InTouch — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que Meta entregue los mensajes de todos los números de la App de InTouch a una URL propia (`/intouch/webhook`) atendida por un dispatcher dedicado, que despacha al bot correcto según el `phone_number_id`.

**Architecture:** Un servicio FastAPI nuevo (`wsp_webhook_intouch`, puerto 6030) valida la firma HMAC de Meta, responde 200 de inmediato y reenvía el body crudo a la ruta `/internal/webhook` del bot que le corresponda según un `BOT_MAP` de configuración. Esa ruta interna, que hoy no valida nada, pasa a exigir un secreto compartido en un header.

**Tech Stack:** FastAPI 0.115.0, uvicorn 0.32.0, httpx 0.27.0, python:3.12-slim, Django (los bots), nginx (gateway).

**Spec:** `docs/superpowers/specs/2026-09-10-webhook-dispatcher-intouch-design.md`

## Global Constraints

- **Español correcto y con tildes** en todo lo que un LLM vaya a leer: prompts, docstrings de tools, mensajes de error. Los mensajes de log y los comentarios de código siguen la convención del repo (sin tildes, ASCII) — mirar el archivo vecino antes de escribir.
- **`git add` explícito, nunca `-a` ni `-A`.** Puede haber más de una sesión sobre el mismo working tree. `git status` antes de cualquier reload.
- **El contenedor que corre acá es producción.** Antes de cualquier rebuild o recreate: `ListAgents` y `SendMessage` a las sesiones activas.
- **Puerto del dispatcher: 6030.** El 6020 es de `wsp_webhook` (otro portfolio, en producción).
- **Header del secreto: `X-Internal-Token`.** Exacto, sin variantes.
- **Nombre del setting: `WEBHOOK_INTERNAL_TOKEN`.** Mismo nombre en los dos bots y en el dispatcher.
- **`BOT_MAP` usa `host.docker.internal`, nunca una IP literal.** Este host responde a `172.20.21.249` y `172.20.21.248` a la vez.
- **El body se reenvía byte por byte** (`content=body`), nunca reserializado.
- **Criterio de «hecho»: evidencia observada, no exit code.** El mismo día de este plan, `reindexar_conocimiento_rag` salió con código 0 habiendo fallado 1 de 7 páginas.

Comando de tests de `wsp_intouch` (de su `CLAUDE.md`):

```bash
docker run --rm -v $PWD:/app -w /app --user 1000:1000 \
  -e USE_SQLITE=true -e HOME=/tmp -e DJANGO_SETTINGS_MODULE=config.settings \
  -e CLIENTE_ACTIVO=renault -e OPENROUTER_API_KEY=dummy \
  --entrypoint python wsp_intouch-web manage.py test bot admin_panel
```

Para `wsp_cavem` es el mismo comando con la imagen `wsp_cavem-web`.

---

## Estructura de archivos

| Archivo | Responsabilidad | Tarea |
|---|---|---|
| `wsp_intouch/config/settings.py` | Leer `WEBHOOK_INTERNAL_TOKEN` del entorno | 1 |
| `wsp_intouch/bot/whatsapp/webhooks.py` | `internal_webhook` verifica el token | 1 |
| `wsp_intouch/bot/tests/test_webhooks.py` | Tests del token | 1 |
| `wsp_intouch/bot/management/commands/doctor.py` | Chequeo de que el token esté configurado | 2 |
| `wsp_cavem/` (los tres equivalentes) | Lo mismo, para que el rollback funcione | 3 |
| `wsp_webhook_intouch/webhook.py` | Verificación, validación de firma, despacho | 4 |
| `wsp_webhook_intouch/{Dockerfile,docker-compose.yml,requirements.txt,.env.example}` | Empaquetado y config | 4 |
| `gateway/nginx.conf` | `/intouch/webhook` → `:6030` | 5 |

**Orden y seguridad:** las tareas 1 a 4 **no tocan el tráfico** — Meta sigue entregando a Cavem hasta que el usuario repunte. Verificado que nadie llama hoy a `internal/webhook` en ninguno de los dos bots: `/cavem/webhook` va a `:8030/webhook` y `/intouch/webhook` va a `:8040/webhook`, las dos rutas que sí validan firma.

---

### Task 1: El token en `internal_webhook` de wsp_intouch

**Files:**
- Modify: `config/settings.py:157` (después de `WHATSAPP_APP_SECRET`)
- Modify: `bot/whatsapp/webhooks.py:142-147`
- Test: `bot/tests/test_webhooks.py` (agregar al final)

**Interfaces:**
- Consumes: nada de tareas anteriores.
- Produces: el setting `WEBHOOK_INTERNAL_TOKEN: str` y el contrato del endpoint `POST /internal/webhook` — header `X-Internal-Token`; `200` con token correcto, `403` con token ausente o incorrecto, `503` si el setting está vacío. La tarea 4 depende de este contrato.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `bot/tests/test_webhooks.py`:

```python
@override_settings(WEBHOOK_INTERNAL_TOKEN="token-de-prueba")
class InternalWebhookTokenTest(TestCase):
    """La ruta interna la llama el dispatcher, no Meta: no hay firma que
    validar, asi que el unico control es este token."""

    def setUp(self):
        self.client = Client()
        self.body = b'{"entry": []}'

    @patch("bot.whatsapp.webhooks._dispatch")
    def test_token_correcto_despacha(self, mock_dispatch):
        response = self.client.post(
            "/internal/webhook",
            data=self.body,
            content_type="application/json",
            HTTP_X_INTERNAL_TOKEN="token-de-prueba",
        )
        self.assertEqual(response.status_code, 200)
        mock_dispatch.assert_called_once()

    @patch("bot.whatsapp.webhooks._dispatch")
    def test_token_incorrecto_devuelve_403_y_no_despacha(self, mock_dispatch):
        response = self.client.post(
            "/internal/webhook",
            data=self.body,
            content_type="application/json",
            HTTP_X_INTERNAL_TOKEN="token-equivocado",
        )
        self.assertEqual(response.status_code, 403)
        mock_dispatch.assert_not_called()

    @patch("bot.whatsapp.webhooks._dispatch")
    def test_sin_header_devuelve_403_y_no_despacha(self, mock_dispatch):
        response = self.client.post(
            "/internal/webhook",
            data=self.body,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        mock_dispatch.assert_not_called()


@override_settings(WEBHOOK_INTERNAL_TOKEN="")
class InternalWebhookSinTokenConfiguradoTest(TestCase):
    """Falla cerrado. La alternativa natural -- "si no hay token configurado,
    no validar" -- convierte un deploy con la variable olvidada en un endpoint
    abierto sin ningun sintoma."""

    @patch("bot.whatsapp.webhooks._dispatch")
    def test_setting_vacio_devuelve_503_y_no_despacha(self, mock_dispatch):
        response = Client().post(
            "/internal/webhook",
            data=b'{"entry": []}',
            content_type="application/json",
            HTTP_X_INTERNAL_TOKEN="lo-que-sea",
        )
        self.assertEqual(response.status_code, 503)
        mock_dispatch.assert_not_called()
```

`mock_dispatch.assert_not_called()` es el punto de cada test negativo. Un test que sólo mire el código de estado pasaría con un endpoint que rechaza *y además* procesa el mensaje.

- [ ] **Step 2: Correr los tests y verificar que fallan**

```bash
cd /home/admincrm/wsp_intouch
docker run --rm -v $PWD:/app -w /app --user 1000:1000 \
  -e USE_SQLITE=true -e HOME=/tmp -e DJANGO_SETTINGS_MODULE=config.settings \
  -e CLIENTE_ACTIVO=renault -e OPENROUTER_API_KEY=dummy \
  --entrypoint python wsp_intouch-web manage.py test \
  bot.tests.test_webhooks.InternalWebhookTokenTest \
  bot.tests.test_webhooks.InternalWebhookSinTokenConfiguradoTest
```

Esperado: **FAIL**. Los tres primeros dan 200 en vez de 403/200 según corresponda, y el cuarto da 200 en vez de 503. Si alguno diera error de atributo por `WEBHOOK_INTERNAL_TOKEN` inexistente, también cuenta como el rojo esperado.

- [ ] **Step 3: Agregar el setting**

En `config/settings.py`, inmediatamente después de la línea 157 (`WHATSAPP_APP_SECRET = ...`):

```python
# Secreto compartido con el dispatcher de webhook (wsp_webhook_intouch). La
# ruta /internal/webhook no recibe la firma de Meta -- el dispatcher ya la
# valido y no la reenvia -- asi que este token es su unico control de acceso.
# Sin default: si falta, la vista responde 503 en vez de quedar abierta.
WEBHOOK_INTERNAL_TOKEN = os.environ.get("WEBHOOK_INTERNAL_TOKEN", "")
```

- [ ] **Step 4: Implementar la verificación**

Reemplazar `internal_webhook` en `bot/whatsapp/webhooks.py` (líneas 142-147) por:

```python
@csrf_exempt
@require_http_methods(["POST"])
def internal_webhook(request):
    """Entrada del dispatcher de webhook, no de Meta. Meta firma sus pedidos y
    el dispatcher valida esa firma, pero NO la reenvia -- asi que aca el unico
    control es el token compartido. Falla cerrado a proposito: sin token
    configurado esto responde 503, porque un deploy que se olvido la variable
    tiene que romper fuerte y no quedar abierto en silencio."""
    esperado = settings.WEBHOOK_INTERNAL_TOKEN
    if not esperado:
        logger.error(
            "WEBHOOK_INTERNAL_TOKEN no esta configurado: /internal/webhook "
            "rechaza todo hasta que se setee. Ver manage.py doctor."
        )
        return JsonResponse(
            {"error": "webhook interno sin token configurado"}, status=503
        )

    recibido = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(recibido, esperado):
        logger.warning("POST a /internal/webhook con token invalido o ausente")
        return HttpResponseForbidden("token interno invalido")

    data = json.loads(request.body or "{}")
    _dispatch(data)
    return JsonResponse({"status": "ok"})
```

`hmac.compare_digest` y no `==`: la comparación de tiempo constante es el punto. `hmac`, `json`, `settings`, `logger`, `HttpResponseForbidden` y `JsonResponse` ya están importados en este archivo (líneas 1-13).

- [ ] **Step 5: Correr los tests nuevos y verificar que pasan**

El mismo comando del Step 2. Esperado: **OK (4 tests)**.

- [ ] **Step 6: Correr la suite completa**

```bash
cd /home/admincrm/wsp_intouch
docker run --rm -v $PWD:/app -w /app --user 1000:1000 \
  -e USE_SQLITE=true -e HOME=/tmp -e DJANGO_SETTINGS_MODULE=config.settings \
  -e CLIENTE_ACTIVO=renault -e OPENROUTER_API_KEY=dummy \
  --entrypoint python wsp_intouch-web manage.py test bot admin_panel
```

Esperado: **1910 tests, 0 fallas, 13 skipped** (los 1906 de hoy más los 4 nuevos). Si el total no cierra, averiguar por qué antes de seguir.

- [ ] **Step 7: Verificar que la suite no ensució el working tree**

```bash
git status --short
```

Esperado: sólo los archivos que se editaron a propósito. Si aparece algo más, un test escribió donde no debía y eso se arregla antes de commitear.

- [ ] **Step 8: Commit**

```bash
cd /home/admincrm/wsp_intouch
git add config/settings.py bot/whatsapp/webhooks.py bot/tests/test_webhooks.py
git commit -m "feat(webhook): /internal/webhook exige un token compartido

La ruta interna la llama el dispatcher, no Meta, asi que no recibe firma
que validar y hasta ahora no validaba nada: cualquiera que alcanzara el
puerto publicado podia inyectar mensajes de WhatsApp. Falla cerrado -- sin
WEBHOOK_INTERNAL_TOKEN configurado responde 503, no pasa de largo.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: El chequeo del `doctor`

**Files:**
- Modify: `bot/management/commands/doctor.py` (una función nueva + la sección `config` en la línea ~738)

**Interfaces:**
- Consumes: el setting `WEBHOOK_INTERNAL_TOKEN` de la Task 1.
- Produces: `chequear_token_interno(opciones)`, un generador de `Hallazgo` registrado en `SECCIONES["config"]`.

- [ ] **Step 1: Escribir la función de chequeo**

En `bot/management/commands/doctor.py`, después de `chequear_variables_obligatorias` (termina en la línea ~127):

```python
def chequear_token_interno(opciones):
    """El token que separa /internal/webhook de cualquiera que alcance el
    puerto. Sin el, la vista responde 503 y el bot deja de recibir del
    dispatcher -- o sea que un olvido acá se ve como 'el bot no contesta'."""
    token = getattr(settings, "WEBHOOK_INTERNAL_TOKEN", "")
    if not token:
        yield Hallazgo(
            FALLA, "WEBHOOK_INTERNAL_TOKEN no esta configurado",
            "/internal/webhook responde 503 y el dispatcher no puede entregar "
            "mensajes. Generar uno con `openssl rand -hex 32` y ponerlo IGUAL "
            "en el .env.docker de este bot y en el .env de wsp_webhook_intouch.",
        )
    elif len(token) < 32:
        yield Hallazgo(
            AVISO, f"WEBHOOK_INTERNAL_TOKEN es corto ({len(token)} chars)",
            "conviene 64 hex de `openssl rand -hex 32`.",
        )
    else:
        yield Hallazgo(OK, "WEBHOOK_INTERNAL_TOKEN configurado")
```

`OK`, `AVISO` y `FALLA` se definen juntos en `doctor.py:41`, y `Hallazgo` es `(nivel, titulo, detalle)` — verificado, no hace falta buscarlos.

- [ ] **Step 2: Registrarlo en la sección `config`**

En `SECCIONES` (línea ~738), agregar `chequear_token_interno` al final de la lista de `"config"`:

```python
    "config": [chequear_variables_obligatorias, chequear_cliente_activo,
               chequear_rag_schema, chequear_schema_efectivo, chequear_langfuse,
               chequear_token_interno],
```

- [ ] **Step 3: Verificar que el chequeo marca la falla**

Todavía no está el valor en el `.env.docker`, así que tiene que salir en rojo:

```bash
cd /home/admincrm/wsp_intouch
docker compose exec -T web python manage.py doctor --seccion config
```

Esperado: la línea `✗ WEBHOOK_INTERNAL_TOKEN no esta configurado` y salida con código != 0.

- [ ] **Step 4: Generar el token y ponerlo en el `.env.docker`**

```bash
openssl rand -hex 32
```

Guardar ese valor — la Task 3 y la Task 4 usan **el mismo**. Agregarlo al final de `/home/admincrm/wsp_intouch/.env.docker`:

```
# Secreto compartido con wsp_webhook_intouch. Mismo valor en los dos lados y
# tambien en wsp_cavem (lo necesita el rollback del traspaso del numero).
WEBHOOK_INTERNAL_TOKEN=<el valor generado>
```

- [ ] **Step 5: Recargar el bot y verificar el verde**

Coordinar antes, que el contenedor es producción:

```bash
cd /home/admincrm/wsp_intouch
git status --short
docker compose up -d web        # una variable nueva del .env exige recreate, no basta kill -HUP
docker compose exec -T web python manage.py doctor
```

Esperado: el chequeo en `✓`, y el `doctor` completo **sin ninguna falla nueva** (los 2 avisos conocidos —reasoning effort y PromptVersion— siguen ahí y no son de esta tarea).

- [ ] **Step 6: Commit**

```bash
cd /home/admincrm/wsp_intouch
git add bot/management/commands/doctor.py
git commit -m "feat(doctor): chequear que WEBHOOK_INTERNAL_TOKEN este configurado

Sin el token la vista interna responde 503 y el sintoma que ve el usuario
es 'el bot no contesta'. El doctor lo dice antes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

El `.env.docker` no se commitea.

---

### Task 3: Lo mismo en wsp_cavem, para que el rollback exista

**Files:**
- Modify: `/home/admincrm/wsp_cavem/config/settings.py`
- Modify: `/home/admincrm/wsp_cavem/bot/whatsapp/webhooks.py:142-147`
- Modify: `/home/admincrm/wsp_cavem/bot/management/commands/doctor.py`
- Test: `/home/admincrm/wsp_cavem/bot/tests/test_webhooks.py`

**Interfaces:**
- Consumes: el mismo valor de token de la Task 2, Step 4.
- Produces: el mismo contrato de `/internal/webhook` en `:8030`, que es lo que hace posible el rollback de §5.2 del spec.

**Por qué esta tarea existe:** Cavem se va a apagar, pero el rollback del traspaso consiste en devolverle el número cambiando una línea del `BOT_MAP`. Si en ese momento hay que además configurarle un token, no es un rollback. Verificado que su `internal_webhook` (`bot/whatsapp/webhooks.py:144`) es idéntico al de InTouch y que hoy nadie lo llama (`/cavem/webhook` va a `:8030/webhook`, la ruta que valida firma).

- [ ] **Step 1: Confirmar que el código de partida es idéntico**

```bash
diff /home/admincrm/wsp_cavem/bot/whatsapp/webhooks.py \
     /home/admincrm/wsp_intouch/bot/whatsapp/webhooks.py
```

Si `internal_webhook` difiere en algo más que el cambio ya hecho en InTouch, parar y revisar antes de copiar.

- [ ] **Step 2: Aplicar los mismos cambios de las Tasks 1 y 2**

Los cuatro bloques de código de las Tasks 1 y 2 se aplican tal cual: el setting, la vista, los tests y el chequeo del `doctor`. No hay nada específico de Cavem. Repetirlos, no referenciarlos de memoria.

- [ ] **Step 3: Correr la suite de Cavem**

```bash
cd /home/admincrm/wsp_cavem
docker run --rm -v $PWD:/app -w /app --user 1000:1000 \
  -e USE_SQLITE=true -e HOME=/tmp -e DJANGO_SETTINGS_MODULE=config.settings \
  -e CLIENTE_ACTIVO=renault -e OPENROUTER_API_KEY=dummy \
  --entrypoint python wsp_cavem-web manage.py test bot admin_panel
```

Esperado: verde, con 4 tests más que la corrida anterior.

- [ ] **Step 4: Poner el token y recargar**

El **mismo valor** de la Task 2, Step 4, en `/home/admincrm/wsp_cavem/.env.docker`. Después:

```bash
cd /home/admincrm/wsp_cavem
git status --short
docker compose up -d web
docker compose exec -T web python manage.py doctor --seccion config
```

Esperado: el chequeo del token en `✓`.

- [ ] **Step 5: Commit**

```bash
cd /home/admincrm/wsp_cavem
git add config/settings.py bot/whatsapp/webhooks.py bot/tests/test_webhooks.py \
        bot/management/commands/doctor.py
git commit -m "feat(webhook): /internal/webhook exige el token compartido

Mismo cambio que en wsp_intouch. Aca no es para operar sino para que el
rollback del traspaso del numero funcione sin configurar nada en medio del
incidente: devolverle el numero a Cavem tiene que ser una linea del BOT_MAP.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: El dispatcher `wsp_webhook_intouch`

**Files:**
- Create: `/home/admincrm/wsp_webhook_intouch/webhook.py`
- Create: `/home/admincrm/wsp_webhook_intouch/Dockerfile`
- Create: `/home/admincrm/wsp_webhook_intouch/docker-compose.yml`
- Create: `/home/admincrm/wsp_webhook_intouch/requirements.txt`
- Create: `/home/admincrm/wsp_webhook_intouch/.env.example`
- Create: `/home/admincrm/wsp_webhook_intouch/.gitignore`
- Create: `/home/admincrm/wsp_webhook_intouch/.env` (no versionado)

**Interfaces:**
- Consumes: el contrato de `/internal/webhook` de la Task 1 (header `X-Internal-Token`).
- Produces: un servicio HTTP en `127.0.0.1:6030` con `GET /webhook` (handshake) y `POST /webhook` (recepción). La Task 5 le apunta nginx.

- [ ] **Step 1: Crear el directorio y los archivos de empaquetado**

```bash
mkdir -p /home/admincrm/wsp_webhook_intouch
cd /home/admincrm/wsp_webhook_intouch
```

`requirements.txt` (mismas versiones exactas que `wsp_webhook`, que está probado en producción):

```
fastapi==0.115.0
uvicorn==0.32.0
httpx==0.27.0
```

`Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY webhook.py .
CMD ["uvicorn", "webhook:app", "--host", "0.0.0.0", "--port", "6030"]
```

`docker-compose.yml`:

```yaml
services:
  web:
    build: .
    ports:
      - "6030:6030"
    env_file:
      - .env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
```

`.gitignore`:

```
.env
__pycache__/
```

- [ ] **Step 2: Escribir `webhook.py`**

```python
"""Dispatcher de webhook de la App de Meta de InTouch.

COPIA de /home/admincrm/wsp_webhook/webhook.py (que atiende la App del otro
portfolio comercial en el puerto 6020), con dos diferencias: el puerto y el
envio de X-Internal-Token. Un webhook por App de Meta porque cada App tiene su
propio App Secret y la validacion de firma usa uno solo.

SI ARREGLAS ALGO ACA, REPLICALO EN EL HERMANO. Son dos copias a mano y ya lo
sabemos: si aparece una tercera App, corresponde extraer un wsp-webhook-core en
vez de una tercera copia. Ver
wsp_intouch/docs/superpowers/specs/2026-09-10-webhook-dispatcher-intouch-design.md
"""
import os, json, hmac, hashlib
import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse, Response

app = FastAPI()

VERIFY_TOKEN: str = os.environ["VERIFY_TOKEN"]
APP_SECRET: str = os.environ.get("APP_SECRET", "")
BOT_MAP: dict[str, str] = json.loads(os.environ.get("BOT_MAP", "{}"))
INTERNAL_TOKEN: str = os.environ.get("WEBHOOK_INTERNAL_TOKEN", "")


@app.get("/webhook", response_class=PlainTextResponse)
def verify(request: Request):
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return request.query_params.get("hub.challenge", "")
    return Response(content="Forbidden", status_code=403)


@app.post("/webhook")
async def receive(request: Request, bg: BackgroundTasks):
    body = await request.body()

    if APP_SECRET:
        sig = request.headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return Response(content="Invalid signature", status_code=403)

    bg.add_task(dispatch, body)
    return Response(content="OK", status_code=200)


async def dispatch(body: bytes):
    # El body se reenvia CRUDO, no reserializado: si esto hiciera loads+dumps,
    # cualquier revalidacion de firma aguas abajo fallaria por un espacio.
    data = json.loads(body)
    headers = {"Content-Type": "application/json"}
    if INTERNAL_TOKEN:
        headers["X-Internal-Token"] = INTERNAL_TOKEN
    async with httpx.AsyncClient(timeout=10) as client:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                phone_id = change.get("value", {}).get("metadata", {}).get("phone_number_id", "")
                url = BOT_MAP.get(phone_id)
                if url:
                    try:
                        r = await client.post(url, content=body, headers=headers)
                        print(f"[webhook] dispatched phone_id={phone_id} -> {url} status={r.status_code}")
                        if r.status_code >= 400:
                            print(f"[webhook] EL BOT RECHAZO el mensaje: {r.status_code} {r.text[:200]}")
                    except Exception as e:
                        print(f"[webhook] error forwarding to {url}: {e}")
                else:
                    print(f"[webhook] no bot mapped for phone_number_id={phone_id!r}")
```

Diferencia con el hermano que vale la pena notar: el original descarta la respuesta del bot. Acá se loguea el status, porque ahora el bot puede rechazar por token (403) o por falta de configuración (503) y sin eso ese fallo sería invisible.

- [ ] **Step 3: Escribir `.env.example` y `.env`**

`.env.example` (se versiona, sin secretos):

```
# Verify token de la App de Meta de InTouch. Mismo valor que el
# WHATSAPP_VERIFY_TOKEN de wsp_intouch/.env.docker.
VERIFY_TOKEN=

# App Secret de la App de Meta de InTouch (Configuracion -> Basica).
# Mismo valor que el WHATSAPP_APP_SECRET de wsp_intouch/.env.docker.
APP_SECRET=

# Secreto compartido con la ruta /internal/webhook de los bots. Mismo valor
# que el WEBHOOK_INTERNAL_TOKEN de wsp_intouch y wsp_cavem.
WEBHOOK_INTERNAL_TOKEN=

# phone_number_id -> URL interna del bot que lo atiende.
# host.docker.internal y NO una IP: este host responde a 172.20.21.249 y
# 172.20.21.248 a la vez (conflicto de cloud-init sin correccion persistente),
# y una IP fija acá se traduce en mensajes perdidos en silencio tras un reboot.
BOT_MAP={"1266650873204031": "http://host.docker.internal:8040/internal/webhook"}
```

`.env` real: los mismos campos con los valores de `wsp_intouch/.env.docker` (`WHATSAPP_VERIFY_TOKEN` → `VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` → `APP_SECRET`, y el token de la Task 2).

Verificar que el `APP_SECRET` es el de la App B comparando huellas, sin imprimir secretos:

```bash
for f in /home/admincrm/wsp_webhook_intouch/.env:APP_SECRET \
         /home/admincrm/wsp_intouch/.env.docker:WHATSAPP_APP_SECRET; do
  file="${f%%:*}"; var="${f##*:}"
  val=$(grep -E "^${var}=" "$file" | head -1 | cut -d= -f2-)
  echo "$var $(printf '%s' "$val" | sha256sum | cut -c1-12)"
done
```

Esperado: las dos huellas iguales, y iguales a `86261797…`.

- [ ] **Step 4: Levantar el contenedor**

```bash
cd /home/admincrm/wsp_webhook_intouch
docker compose up -d --build
docker compose ps
```

Esperado: `Up`, con `0.0.0.0:6030->6030`.

- [ ] **Step 5: Verificar el handshake y la firma, contra el puerto directo**

Todavía sin nginx: se prueba el servicio, no el ruteo.

```bash
cd /home/admincrm/wsp_webhook_intouch
VT=$(grep '^VERIFY_TOKEN=' .env | cut -d= -f2-)
AS=$(grep '^APP_SECRET=' .env | cut -d= -f2-)

# 6. handshake correcto -> devuelve el challenge
curl -s "http://127.0.0.1:6030/webhook?hub.mode=subscribe&hub.verify_token=$VT&hub.challenge=12345"
echo

# 7. handshake con token malo -> 403
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://127.0.0.1:6030/webhook?hub.mode=subscribe&hub.verify_token=malo&hub.challenge=12345"

# 8/9. POST con firma valida -> 200 ; con firma invalida -> 403
BODY='{"entry":[{"changes":[{"value":{"metadata":{"phone_number_id":"1266650873204031"}}}]}]}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$AS" | awk '{print $2}')"
curl -s -o /dev/null -w "firma valida:  %{http_code}\n" -X POST http://127.0.0.1:6030/webhook \
  -H "Content-Type: application/json" -H "X-Hub-Signature-256: $SIG" -d "$BODY"
curl -s -o /dev/null -w "firma invalida: %{http_code}\n" -X POST http://127.0.0.1:6030/webhook \
  -H "Content-Type: application/json" -H "X-Hub-Signature-256: sha256=deadbeef" -d "$BODY"

# 10. phone_number_id desconocido -> 200 y log de "no mapeado"
BODY2='{"entry":[{"changes":[{"value":{"metadata":{"phone_number_id":"000000"}}}]}]}'
SIG2="sha256=$(printf '%s' "$BODY2" | openssl dgst -sha256 -hmac "$AS" | awk '{print $2}')"
curl -s -o /dev/null -w "desconocido:   %{http_code}\n" -X POST http://127.0.0.1:6030/webhook \
  -H "Content-Type: application/json" -H "X-Hub-Signature-256: $SIG2" -d "$BODY2"

docker compose logs --tail 20 web
```

Esperado: `12345`; `403`; `200`; `403`; `200`. En los logs, un `dispatched … status=200` para el válido y un `no bot mapped for phone_number_id='000000'` para el último.

**El `status=200` de ese log es el chequeo que importa:** prueba que el dispatcher llegó al bot **y** que el token fue aceptado. Un `status=403` significa que los tokens no coinciden; un `503`, que al bot le falta la variable.

- [ ] **Step 6: Inicializar git y commitear**

```bash
cd /home/admincrm/wsp_webhook_intouch
git init -q
git add .gitignore Dockerfile docker-compose.yml requirements.txt webhook.py .env.example
git status --short   # verificar que .env NO aparece
git commit -m "feat: dispatcher de webhook para la App de Meta de InTouch

Un webhook por App de Meta: esta atiende los numeros de InTouch en el puerto
6030, valida la firma con el App Secret de esa App y despacha por
phone_number_id. Copia de wsp_webhook (App del otro portfolio, puerto 6020)
mas el envio de X-Internal-Token y el log del status del bot.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

Verificar en el `git status` del Step 6 que `.env` **no** está entre los archivos agregados.

---

### Task 5: Cablear nginx

**Files:**
- Modify: `/home/admincrm/gateway/nginx.conf` — el `proxy_pass` de `location = /intouch/webhook` (línea ~420)

**Interfaces:**
- Consumes: el servicio en `127.0.0.1:6030` de la Task 4.
- Produces: `https://qadash.in-touchcrm.cl/intouch/webhook` atendido por el dispatcher. Es la URL que el usuario configura en Meta.

**Coordinación obligatoria antes de empezar:** `nginx.conf` es compartido por todas las apps y al 2026-09-10 hay otra sesión (`admincrm-9a`) editándolo. `ListAgents` y `SendMessage` antes de escribir.

- [ ] **Step 1: Avisar a las sesiones activas y releer el archivo**

```bash
grep -n "intouch/webhook" -A 4 /home/admincrm/gateway/nginx.conf
stat -c 'inodo=%i' /home/admincrm/gateway/nginx.conf
```

Anotar el inodo: el Step 3 verifica que no cambió.

- [ ] **Step 2: Cambiar la línea, preservando el inodo**

**Con `cat >` y nunca con `mv` ni `sed -i`.** Los dos renombran el archivo y cambian el inodo, y el contenedor de nginx sigue viendo el archivo viejo por el bind-mount. Ése es el origen del mito de que «`nginx -s reload` no aplica los cambios». Reportado por la sesión peer el 2026-09-10: le pasó, y `nginx -t` le dijo «syntax is ok» **validando el archivo viejo**.

```bash
cd /home/admincrm/gateway
cp nginx.conf /tmp/nginx.conf.nuevo
python3 - <<'PY'
from pathlib import Path
p = Path("/tmp/nginx.conf.nuevo")
s = p.read_text(encoding="utf-8")
viejo = """    location = /intouch/webhook {
      proxy_pass http://127.0.0.1:8040/webhook;"""
nuevo = """    location = /intouch/webhook {
      # Al dispatcher de la App de Meta de InTouch (wsp_webhook_intouch), no
      # directo al bot: un webhook por App, y el BOT_MAP decide que numero
      # atiende cual bot. Cambiar ese JSON es como se traspasa un numero.
      proxy_pass http://127.0.0.1:6030/webhook;"""
assert s.count(viejo) == 1, f"esperaba 1 coincidencia, hay {s.count(viejo)}"
p.write_text(s.replace(viejo, nuevo), encoding="utf-8")
print("ok")
PY
cat /tmp/nginx.conf.nuevo > nginx.conf     # preserva el inodo
```

**Corregido tras medir: el `proxy_pass` va CON el path `/webhook`, no sin
él.** La primera versión de este plan decía lo contrario ("sin path,
nginx conserva el URI original"), pero medido en producción eso da 404: sin
el path, nginx reenvía la URI original completa (`/intouch/webhook`) al
dispatcher, que sólo sirve `/webhook` y no reconoce esa ruta. Con el path,
nginx reemplaza el URI matcheado por `/webhook` y el dispatcher responde
(403 sin firma válida, 200 con ella).

- [ ] **Step 3: Validar de verdad, no contra el archivo viejo**

```bash
stat -c 'inodo=%i' /home/admincrm/gateway/nginx.conf   # igual al del Step 1
docker compose -f /home/admincrm/gateway/docker-compose.yml exec nginx grep -c "6030" /etc/nginx/nginx.conf
```

El `grep` tiene que dar `1`. **Si da `0`, el contenedor está viendo el archivo viejo** y cualquier validación que hagas es sobre otro archivo. En ese caso, validar contra el archivo del disco:

```bash
docker run --rm \
  -v /home/admincrm/gateway/nginx.conf:/etc/nginx/nginx.conf:ro \
  -v /home/admincrm/gateway/certs:/etc/nginx/certs:ro \
  nginx:alpine nginx -t
```

Después, con el inodo preservado:

```bash
docker compose -f /home/admincrm/gateway/docker-compose.yml exec nginx nginx -t
docker compose -f /home/admincrm/gateway/docker-compose.yml exec nginx nginx -s reload
```

Esperado: `syntax is ok` / `test is successful`. Si el inodo **sí** cambió, hace falta `docker compose up -d --force-recreate nginx`, que corta unos segundos a todas las apps y se avisa antes.

- [ ] **Step 4: Verificar la ruta y que las vecinas no se rompieron**

```bash
for u in / /callreviews/ /incitrack/ /wsp/cavem/ /wsp/intouch/ /login/; do
  printf "%-16s %s\n" "$u" "$(curl -sk -o /dev/null -w '%{http_code}' https://qadash.in-touchcrm.cl$u)"
done

VT=$(grep '^VERIFY_TOKEN=' /home/admincrm/wsp_webhook_intouch/.env | cut -d= -f2-)
curl -sk "https://qadash.in-touchcrm.cl/intouch/webhook?hub.mode=subscribe&hub.verify_token=$VT&hub.challenge=12345"; echo
curl -sk -o /dev/null -w "sin firma: %{http_code}\n" -X POST https://qadash.in-touchcrm.cl/intouch/webhook
```

Esperado: las seis vecinas en `200`; el handshake devuelve `12345`; el POST sin firma da `403`.

- [ ] **Step 5: Chequeo 11 — que el turno llegue al grafo**

Es la prueba de integración completa sin tocar Meta:

```bash
AS=$(grep '^APP_SECRET=' /home/admincrm/wsp_webhook_intouch/.env | cut -d= -f2-)
BODY='{"entry":[{"changes":[{"value":{"metadata":{"phone_number_id":"1266650873204031"},"messages":[{"from":"56900000000","id":"wamid.test.plan","type":"text","text":{"body":"hola"}}],"contacts":[{"profile":{"name":"Prueba Plan"}}]}}]}]}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$AS" | awk '{print $2}')"
curl -sk -o /dev/null -w "%{http_code}\n" -X POST https://qadash.in-touchcrm.cl/intouch/webhook \
  -H "Content-Type: application/json" -H "X-Hub-Signature-256: $SIG" -d "$BODY"

docker compose -f /home/admincrm/wsp_webhook_intouch/docker-compose.yml logs --tail 5 web
docker compose -f /home/admincrm/wsp_intouch/docker-compose.yml logs --tail 40 web
```

Esperado: `200` de nginx; en el dispatcher un `dispatched … status=200`; en el bot, el turno procesado.

**Ojo — esto le escribe una conversación real a la base de producción** con el `wa_id` `56900000000`. Es deliberado (es la única forma de probar el camino entero sin Meta) pero hay que saberlo y borrarla después si molesta. **No inventar un `wa_id` que pueda pertenecer a alguien.**

- [ ] **Step 6: Respaldar el archivo — NO commitearlo**

`gateway/` **no tiene repo propio**: `git -C /home/admincrm/gateway rev-parse --show-toplevel` devuelve `/home/admincrm`, que está en la rama `refactor/ticket-detail-duralux`, de otro trabajo. Commitear `nginx.conf` ahí metería infraestructura compartida en una rama que no tiene nada que ver, y además arrastraría el directorio entero, que hoy figura como no rastreado.

En su lugar, dejar el respaldo fechado que ya es la convención del directorio (la sesión peer dejó uno hoy):

```bash
cp /home/admincrm/gateway/nginx.conf \
   /home/admincrm/gateway/nginx.conf.bak.$(date +%Y%m%d-%H%M%S)
ls -1t /home/admincrm/gateway/nginx.conf.bak.* | head -3
```

Y anotar el cambio en `wsp_intouch/hilo.md` (Task 6, Step 6), que es donde queda la historia de este cambio.

**Deuda que esto deja anotada:** el `nginx.conf` compartido por todas las apps no está versionado en ningún repo propio, y su historia son archivos `.bak` fechados. No se resuelve en este plan, pero merece su propio trabajo.

---

### Task 6: El traspaso (runbook, no código)

**Esta tarea NO la ejecuta un agente sin el usuario.** Los pasos 1 y 4 son del usuario, y el 5 exige visto bueno explícito.

- [ ] **Step 1 (USUARIO): Repuntar Meta**

En el panel de la App de Meta de InTouch → WhatsApp → Configuración → Webhook, cambiar la URL de callback a:

```
https://qadash.in-touchcrm.cl/intouch/webhook
```

**Sin `/wsp/`** — esa ruta cae en el catch-all de la SPA y devuelve HTML. El verify token es el mismo que ya está configurado.

- [ ] **Step 2: Verificar que entra un mensaje real**

```bash
docker compose -f /home/admincrm/wsp_webhook_intouch/docker-compose.yml logs -f web
```

Con un mensaje real al número, tiene que aparecer un `dispatched phone_id=1266650873204031 … status=200`. El criterio es **user-agent de Meta, no `curl`**.

- [ ] **Step 3: Verificar que el bot contestó por WhatsApp**

Mirar el teléfono. El chequeo no es que el log diga 200: es que llegue la respuesta.

- [ ] **Step 4 (USUARIO): Visto bueno para apagar Cavem**

Preguntar explícitamente. Está escrito en el §4 del `PENDIENTES.md` que este apagado ya quedó sin ejecutar una vez porque una pregunta de varias partes volvió respondida a medias.

- [ ] **Step 5: Apagar Cavem en sus tres capas**

```bash
cd /home/admincrm/wsp_cavem
git status --short            # que no haya trabajo sin commitear
docker compose stop           # `restart: unless-stopped` respeta el stop al reboot
```

Después, en el Django admin del orquestador: Aplicacion «Auto IA — Cavem» → `activo = False`. Sin este paso, Cavem queda en el launcher con su tile en 502 y el orquestador sigue replicando su schema. No hay heartbeat: apagar el contenedor no lo desregistra.

El bloque de nginx de Cavem puede quedarse; no molesta.

**No borra datos:** su schema (45 tablas), sus conversaciones y sus volúmenes de media quedan donde están.

- [ ] **Step 6: Actualizar la documentación**

- `PENDIENTES.md` de `wsp_intouch`: cerrar el §0 completo y el §1.1/§1.2 (el RAG se cerró el 2026-09-10: 7/7 documentos, 51 chunks). Corregir el §2.8, cuyo primer menor ya estaba implementado antes de este plan.
- `/home/admincrm/CLAUDE.md`: `wsp_webhook` **no** está «en mantenimiento» — está en producción. Agregar `wsp_webhook_intouch` a la tabla.
- `docs-repo/biblia_bots.md`: el patrón «un webhook por App de Meta» es de stack, no de este bot.
- `hilo.md` de `wsp_intouch`: la bitácora de la sesión.

---

## Rollback

| Hasta la tarea | Cómo se vuelve |
|---|---|
| 1-4 | Nada que revertir: nada está cableado y Meta sigue en Cavem |
| 5 | Devolver el `proxy_pass` a `http://127.0.0.1:8040/webhook` (con `cat >`) y recargar nginx |
| 6, pasos 1-3 | Cambiar el `BOT_MAP` a `{"1266650873204031": "http://host.docker.internal:8030/internal/webhook"}` y `docker compose up -d web` en el dispatcher — **sin entrar a Meta**. Funciona porque la Task 3 le puso el token a Cavem |
| 6, paso 5 | `docker compose start` en `wsp_cavem` y `activo=True` en el orquestador |
