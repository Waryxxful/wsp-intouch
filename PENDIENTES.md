# Pendientes — wsp_intouch

Estado al **2026-09-10 12:55 UTC**, verificado contra el sistema corriendo (no
de memoria). Cada afirmación dice cómo se comprobó. **Actualizado el
2026-09-23**: lo abierto de esa fecha está en la sección siguiente.

---

## RETOMAR ACÁ — chat 9 del gerente comercial (2026-09-23)

Felipe (gerente comercial) probó el bot en el chat 9 del panel
(`/wsp/intouch/chat/9`, wa_id 56996249863, 38 turnos). Lo hecho está en
`hilo.md` del 2026-09-23. Lo que queda abierto, por prioridad:

### A. Derivación y CRM — el usuario lo dejó para después (era una prueba)

1. **El lead nunca llegó al CRM.** 20 rechazos con 400; `despachado_en` vacío.
   El cuerpo del 400 no se loguea (`bot/business/lead_intouch.py:498`), así que
   no se sabe el motivo. Primer paso: loguear el cuerpo.
2. **El aviso de derivación dio 404.** `[notify] no se pudo notificar
   caso_equipo a http://172.20.21.249:9000/internal/notify/`. El Incident 6
   quedó abierto sin que nadie se enterara. El bot prometió seis veces "te
   contacta hoy a las 17:00".
3. **El score se degrada.** Llegó a HOT a las 12:45 UTC y terminó en
   `NO_CALIFICADO`: un turno con señales en contra lo recalcula a la baja
   (`lead_intouch.py:188-190`; el guard solo cubre los turnos SIN señales).
   Tiene que ser monótono dentro de la conversación.
4. **`doctor` debería fallar** si el receptor del lead o el notify no
   responden, en vez de descubrirlo en una conversación.

### B. Extractor de metadatos

- `resumen_conversacion` y `necesidad_principal` describen el último turno
  («servicios más vendidos»), no el caso. `soluciones_interes` trae frases que
  no son soluciones. `canales_actuales` perdió «voz». No quedó la secuencia
  «WhatsApp primero, después llamada».
- `flow_data` acumula claves basura (`mensaje`, `contacto_turno`, `fecha_hoy`,
  `mensaje_clave`, `prefijo_horario`, `correo` y `email` duplicados):
  `normalizar_flow_data` no filtra contra una lista de campos conocidos.
- 4 timeouts de 8,5 s en el chat 9: esos turnos quedaron sin clasificar.

### C. Comportamiento del bot (prompt)

- No ofrece la **consultoría gratuita de 30 minutos**, que es el llamado
  principal del sitio (ya está en el RAG). Ni siquiera cuando le piden precio.
- Confunde «proceso de implementación» con «plazo» (mensaje 266). Las etapas
  se pueden explicar con `sobre-intouch.md`.
- Se contradice con el consentimiento (276 explica la ley, 278 dice que no
  tiene el detalle).
- Vuelve a saludar a mitad de conversación; repite el cierre «hoy a las 17:00,
  no queda agendado» seis turnos seguidos; inventó una «campaña anterior» (256).
- **Nombre del bot: falta la decisión del gerente.** Se negó a tener uno y
  después eligió «Matías»; Felipe se despidió de «Matías».
- Detalles vistos al validar la regla de clientes: a veces justifica con «es
  información confidencial» (no autorizado, pero plausible), y con la
  industria desconocida una vez usó «líder en automotriz» en vez de la versión
  general.

### D. Corte de mensajes (`ded886e`, trabajo del 22-09)

`_empaquetar_sin_cortar` une las unidades con un espacio: se pierden los saltos
de párrafo y **una lista llega corrida en una línea** («- Tasa 83% - CSAT
91% - …», visto en la respuesta de métricas). Las pruebas del 22-09 esperan ese
comportamiento; cambiarlo es cambiar ese contrato.

### E. Datos que dependen del gerente

- El único teléfono y el único correo publicados en in-touch.cl están bajo
  **«Recursos Humanos»** (+56 2 2927 3619, rrhh@in-touchcrm.cl). El bot los da
  con ese rótulo. Si hay un contacto comercial, se publica en el sitio y se
  vuelve a scrapear: `ContactoInstitucional` lo toma solo.

### F. Menores

- El acuse de las 13:11:51 UTC quedó guardado como mensaje con el texto
  literal «None» (llega por `/internal/webhook`).
- Dos turnos de RAG tardaron 11,8 s y 10,1 s (mensajes 269 y 277), cuando lo
  esperable es ~7 s: mirarlos en Langfuse.
- La solución de contactos no es universal: la decisión es
  `CLIENTE_ACTIVO != "intouch"` en `catalogo_estructurado_disponible()`. La
  versión universal es el paquete `verticals/` (biblia §VI.5).

### Lo que NO es de esta sesión y quedó sin commitear

- `auditoria latencia/`: los `.md` movidos a `hecho/`, el xlsx y
  `scripts/propuestasprompt/`. Ninguna de las otras sesiones de Claude los
  reconoce como suyos.
- `docs-repo/biblia_bots.md`: ~180 líneas de otra sesión (de esta sesión se
  commiteó solo el párrafo de §VI.5, `a9a2d76`).

---

## 0. Lo urgente: el orden del apagado de Cavem

**Meta sigue apuntando a `/cavem/webhook`.** Verificado el 2026-09-10 **contra
la API de Meta**, no supuesto:

```
GET /v21.0/2267950390607058/subscriptions
  callback_url: "https://qadash.in-touchcrm.cl/cavem/webhook"
  active: true
```

**El orden importa y hoy sigue al revés de lo que hace falta:**

- **Apagar Cavem AHORA = perder los mensajes.** Meta seguiría entregando a una
  ruta cuyo backend no existe (`502`), y WhatsApp no reintenta indefinidamente.
- El orden correcto es: **(1)** repuntar Meta, **(2)** verificar que entra por
  InTouch, **(3)** recién entonces apagar Cavem.

**Dato que baja el riesgo:** al 2026-09-10 no hay tráfico real de Meta a ese
número; los POST registrados son todos `curl` de las pruebas. La ventana entre
(1) y (3) es de bajo impacto hoy, y crece cuando el número empiece a usarse.

### 0.1 Repuntar el webhook — **NO hace falta el panel de Meta**

Esto estaba mal en las versiones anteriores de este documento y **costó horas de
bloqueo innecesario**: se daba por hecho que había que entrar a la consola web de
Meta. No es así. El `WHATSAPP_TOKEN` del `.env.docker` es de un **usuario de
sistema** de la App `bots-intouch`, con `whatsapp_business_management` y sin
vencimiento. Verificado con `debug_token`:

```
app_id: 2267950390607058 · application: bots-intouch · type: SYSTEM_USER
scopes: whatsapp_business_management, whatsapp_business_messaging, ...
```

O sea que el repunte se hace por la Graph API, desde este host:

```bash
cd /home/admincrm/wsp_intouch
APPID=2267950390607058
AS=$(grep '^WHATSAPP_APP_SECRET=' .env.docker | cut -d= -f2-)
VT=$(grep '^WHATSAPP_VERIFY_TOKEN=' .env.docker | cut -d= -f2-)
curl -s -X POST "https://graph.facebook.com/v21.0/$APPID/subscriptions" \
  -d "object=whatsapp_business_account" \
  -d "callback_url=https://qadash.in-touchcrm.cl/intouch/webhook" \
  -d "verify_token=$VT" \
  -d "fields=account_alerts,account_review_update,account_update,calls,message_template_quality_update,message_template_status_update,messages,phone_number_name_update,phone_number_quality_update,security,message_template_components_update" \
  -d "access_token=$APPID|$AS"
```

Tiene que devolver `{"success":true}`. **Pasar `fields` completo**: el POST
reemplaza la suscripción, así que omitirlo desuscribe campos que hoy están
activos. La lista de arriba es la que estaba vigente el 2026-09-10.

**Es reversible:** el mismo comando con `/cavem/webhook` lo devuelve.

**Sin `/wsp/`** en la URL: esa ruta cae en el catch-all de la SPA y devuelve
HTML, no Django.

El handshake ya está probado por el camino nuevo (nginx → dispatcher `:6030`):
con el `WHATSAPP_VERIFY_TOKEN` correcto devuelve el `hub.challenge`, con uno
incorrecto `403`.

### 0.2 Verificar que entra por InTouch

Después de repuntar, mandar un mensaje real al número y confirmar. **Ojo:**
Meta ya no le habla al bot directo — le habla al dispatcher
`wsp_webhook_intouch` (puerto 6030), que valida la firma y reenvía. Por
eso el criterio viejo (`POST /webhook` con user-agent de Meta, en los logs
del bot) ya no aplica en ninguna de sus dos mitades: lo que el bot recibe
es `POST /internal/webhook`, y el user-agent que ve siempre es
`python-httpx` — nunca Meta, porque Meta nunca le habla directo.

El criterio real:

```bash
# en los logs del DISPATCHER, no del bot:
docker compose -f /home/admincrm/wsp_webhook_intouch/docker-compose.yml logs -f web | grep dispatched
```

Tiene que aparecer `dispatched phone_id=… status=200`.

Y la prueba concluyente, en la base del bot: que aparezca una `Conversation`
nueva para el `wa_id` que mandó el mensaje.

### 0.3 Apagar Cavem — **tres capas, todas reversibles**

```bash
# 1. el contenedor
cd /home/admincrm/wsp_cavem
git status --short                 # que no haya trabajo sin commitear
docker compose stop                # `restart: unless-stopped` respeta el stop al reboot

# 2. sacarlo del launcher y de la sincronización de schemas
#    Django admin del orquestador -> Aplicacion "Auto IA — Cavem" -> activo = False
#    (sobrevive a un re-registro: register_app no pisa ese flag)

# 3. su bloque de nginx puede quedarse (no molesta) o comentarse
#    /home/admincrm/gateway/nginx.conf, líneas ~379-383
#    Si se toca: docker compose -f /home/admincrm/gateway/docker-compose.yml up -d --force-recreate nginx
```

**Sin el paso 2**, Cavem queda listado en el launcher con su tile en `502`, y el
orquestador sigue replicando su schema a las bases de tenant. No hay heartbeat:
nadie escribe el estado `caido`, así que apagar el contenedor no lo desregistra.

**Nada de esto borra datos de Cavem**: su schema (45 tablas), sus conversaciones
y sus volúmenes de media quedan donde están. Verificado hoy: `SCHEMA_NAME()=cavem`,
45 tablas, 1 conversación.

---

## 1. Bloqueado en credenciales o accesos — **del usuario**

### 1.1 y 1.2 El RAG — **RESUELTO el 2026-09-10**

Estaba anotado como bloqueado porque se suponía que a Supabase sólo se llegaba
por su editor web. **Era falso:** hay un MCP de Supabase conectado al proyecto
`intouch` (ref `wrnbvrhwcwabmzfrhgig`, el mismo del `SUPABASE_URL`), así que el
DDL se aplicó por ahí.

Lo que sí requirió al usuario fue el dropdown de *Settings → API → Exposed
schemas*: se verificó que no hay vía SQL (el rol `authenticator` de este
proyecto no tiene `pgrst.db_schemas`; Supabase lo administra fuera de la base).

Estado verificado:

| | |
|---|---|
| Schema `intouch` | idéntico a `renault` en columnas (9/9), índices (hnsw + gin + pkey), RLS, grants y firma de `match_documentos` |
| Vecinos | intactos — renault 323 filas, astara 1852, cavem 46 |
| Indexado | **7/7 documentos, 51 chunks, todos con embedding** |
| `doctor --seccion rag` | **3 ok · 0 fallas** |

**Trampa real que costó un diagnóstico:** `reindexar_conocimiento_rag` salió con
**exit code 0 habiendo fallado 1 de 7 páginas** (`analitica-y-calidad.md`, por
un JSON inválido puntual del LLM en `_hechos_de_documento`). El exit code miente:
hay que contar los chunks. Se reindexó esa página sola y quedó completa.

### 1.3 Los siete `.md` del conocimiento esperan revisión humana

`bot/fixtures/rag/*.md` — es literalmente lo que el bot va a afirmar sobre
InTouch, y este bot no tiene precios que lo anclen a la realidad. La procedencia
de cada afirmación (qué salió del catálogo sembrado, qué del prompt aprobado y
qué es redacción propia) está en el reporte de esa task, dentro de
`.superpowers/sdd/2026-09-09-bot-intouch-comercial/task-12-report.md`.

### 1.4 `CRM_CUENTA_SLUG` — bloquea el acceso del comercial al CRM

A qué cuenta de GranCRM pertenece la instancia del CRM. **Es decisión de
negocio, no técnica**: define quién entra. Sin eso, la otra sesión no puede
cerrar el acceso del comercial (5 tareas suyas). No toca al bot.

### 1.5 `GRANCRM_TENANT_SLUG` sigue en `CHANGEME`

Es la cuenta a la que llegan las **notificaciones de lead HOT**. Verificado que
la cuenta `qaintouch` ya existe en el orquestador, así que no es una credencial a
conseguir sino **un valor a confirmar**. No se seteó a propósito: si el equipo
comercial de InTouch no vive en la cuenta interna de QA, las notificaciones irían
al lugar equivocado.

Mientras esté en `CHANGEME`, `bot/notify.py` loguea un aviso y no notifica. El
lead se guarda igual y se ve en el panel; sólo no salta la campanita.

### 1.6 La decisión sobre `enableSessionForAPIKeys` del CRM

La otra sesión midió que hoy una API key de su framework **equivale a su usuario
dueño**: con ella se podían leer contactos, empresas y oportunidades, y crear
contactos. Lo mitigó con un secreto de ingesta dedicado y un guard que corre sólo
en `/api/ingest`, así que el bot ya no depende de eso. Pero la decisión de fondo
sobre las API keys del CRM sigue abierta y es del usuario.

---

## 2. Deuda técnica conocida — no bloquea nada

### 2.1 El destino externo del lead — **ENCENDIDO el 2026-09-10**

```
LEAD_SINK=http
LEAD_SINK_URL=http://crm-api:3001/api/ingest/intouch-lead
LEAD_SINK_TOKEN=<el de /home/admincrm/.ingest_secret_for_bot>
```

La red `crm_ingest` ya está declarada en el `docker-compose.yml` (commit
`7e4dfc6`). Tres cosas de ese cambio que no son obvias:

1. **`cron` también va en la red, no sólo `web`.** Ese sidecar corre
   `despachar_leads_pendientes`, o sea el REINTENTO de los leads que fallaron en
   caliente. Sin la red, el camino que existe para recuperarse de un fallo sería
   el único que nunca funciona, en silencio y cada 15 minutos. Este documento y
   la sesión del CRM decían los dos "el servicio `web`".
2. **`default` se declara explícita.** Declarar `networks` en un servicio
   reemplaza la red default implícita; omitirla deja a `web` sin hablar con `cron`.
3. **Puerto 3001, el interno.** El 3006 está publicado en loopback del host y no
   se resuelve desde un contenedor. Y **no va por el gateway**: `/crm/api/ingest`
   está en 404 a propósito.

Verificado sin escribir nada en el CRM: desde dentro del contenedor, `crm-api`
resuelve a `172.30.0.2` y un POST **sin credencial** devuelve `401 "Falta la
credencial de ingesta"` — prueba positiva de que el bot llega Y de que el guard
corre. La huella `sha256` del secreto coincide con la del CRM.

**Sin hacer todavía:** el E2E con un lead real (disparar uno por el bot, que la
sesión del CRM confirme `contactId`/`dealId`, y limpiar por ids, nunca por
prefijo). Ojo con el contrato: `clave_contacto` son 32 caracteres **HEX** y
`evento_id` un UUID válido.

### 2.2 El frontend usa rutas absolutas en vez del `apiBase` del contract

~26 archivos de `frontend/src/` llaman `/intouch/api/...` directo, en vez de usar
el `apiBase` que el shell pasa por el contract. Es deuda deliberada: refactorizar
26 archivos sin tests que los cubran era más riesgo que beneficio. Hay un
comentario en `App.tsx` que lo dice.

### 2.3 El dominio automotriz heredado sigue en el repo, desregistrado

Modelos, tools y especialistas de autos (`VehiculoUsado`, `Reserva`, `Servicio`,
`Sucursal`, las encuestas) están **fuera de `AGENTS` y sin tools bindeadas**, o
sea invisibles para el ruteo. Se conservan porque su cobertura de tests es la que
prueba las defensas del stack. **No agregarles datos ni chequeos del `doctor`.**

Lo mismo con el dataset de demo del panel: intacto y **gateado por cliente**, así
que las pantallas se ven vacías con datos reales. Si se quiere demo, hace falta
un dataset B2B propio.

### 2.4 `_PROMPT_HECHOS_DOCUMENTO` y el scraping estructurado

**Resuelto el 2026-09-23.** El sitio sí se scrapea (`ScrapingSource` 2,
https://in-touch.cl) y entra al RAG además de los `.md`. El runner ya no llama a
`extract_catalog` para este vertical (`catalogo_estructurado_disponible()`); en
su lugar guarda `ContactoInstitucional` (teléfono, correo y direcciones), que va
a la ficha de cada turno. El guard de `extract_catalog` sigue como defensa para
otros llamadores. Detalle en `hilo.md` del 2026-09-23 y en el spec §12.6.

### 2.5 Avisos del `doctor` que quedan

```
! OPENROUTER_MODEL no soporta reasoning effort='medium'
    soportados: high, low, max — OpenRouter lo remapea SIN avisar
! no hay PromptVersion activa para el prompt global
    el bot cae al SYSTEM_PROMPT del código, así que funciona
```

El primero es **heredado** y ya estaba en el bot hermano: la llamada razona a un
nivel distinto del que dice el código. El segundo se cierra con
`manage.py seed_intouch --republicar-prompt`, que **no se corrió** porque publicar
un prompt es estado de producción y va con visto bueno explícito.

### 2.6 El prompt global lleva tres párrafos sin firma del usuario

Un agente los agregó para pasar un test mío mal calibrado (un piso numérico de
tildes). Los revisé: no tocan ningún guardrail, ni la frase de escape, ni el
marco legal, y el de la lista ampliada de palabras acentuadas es incluso el
mecanismo correcto. Pero **no llevan la aprobación del usuario**. Revertirlos son
tres párrafos.

### 2.7 El drift de tres repos (biblia §VI.1)

Este bot es el **tercero** con el mismo grafo copiado a mano. Lo que aporta al
diseño de `wsp-bot-core`: además de los puntos de extensión ya identificados, hay
que parametrizar el **vertical** (4 puntos, no 3 — el cuarto es
`_PROMPT_HECHOS_DOCUMENTO`), el **modelo de catálogo** con sus tools, el **modelo
de lead** con su score, y las **secciones del `doctor`**.

### 2.8 Menores anotados

- `comercial.py:18` lee su fixture con `read_text()` a nivel de módulo. Si falta,
  el traceback es crudo; merece un `ImproperlyConfigured` con la instrucción de
  restaurarlo. Esta sesión persiguió ese fantasma dos veces.
- `--limite` del barrido se aplica por grupo y no a un total combinado.
- `AgendamientosPage`/`CampanasPage` siguen ruteadas en `App.tsx` aunque salieron
  del `nav`. Alcanzables por URL directa, muestran vacío.
- Comentario obsoleto sobre `modelo_imagen` en `cola_envio.py` (~85-92).
- El camino legacy de `modelo_imagen` por parámetro desde `graph.py` está muerto
  por construcción (el especialista no declara ese campo).

---

## 3. Lo que sí está hecho y verificado

| | Cómo se verificó |
|---|---|
| Suite | **1906 tests, 0 fallas, 13 skipped** |
| BD | `leadintouch` con 34 columnas; login propio con `DEFAULT_SCHEMA=intouch`; **0 tablas visibles del schema `cavem`** desde el login del bot |
| Cavem intacto | `SCHEMA_NAME()=cavem`, 45 tablas, 1 conversación, healthy |
| Bot corriendo | `:8040` healthy, `/healthz` 200; sidecar `cron` sin falso rojo |
| Credencial de Meta | `doctor` con red: **válida, `InTouch · +56 2 2927 3658`** |
| Webhook | handshake por nginx: devuelve el desafío con el token correcto, 403 con el malo |
| nginx | bloques agregados antes de los catch-all, `nginx -t` OK, vecinas sin romper |
| Frontend | desplegado; `/mf/wsp_intouch/remoteEntry.js` → 200 con el scope correcto |
| Registro en DIOS | verificado **en la BD del orquestador**: `activo=True`, `:8040`, `schemas=intouch` |
| Circuito con el CRM | E2E contra el receptor real, dos ramas (creación y replay), base limpia después |

**Sin medir todavía:** el recall del RAG (falta indexar). ~~La latencia~~ —
medida el 2026-09-21 contra las trazas reales, ver §4.

---

## 4. Latencia — medida el 2026-09-21

Informe completo en `auditoria latencia/2026-09-21-medicion-y-correccion.md`.
Reproducible con `manage.py medir_latencia --desde 2026-09-15`.

**El turno es 5,94 s de media y 3,99 s de mediana** (n=56, 2026-09-15). Los
7,6 s que se venían repitiendo **no son la latencia del turno**: son la media
de `extract-metadata` (7,72 s), que corre en la cola después del envío.

Reparto: CABEZA (ORM/contexto) 0,05 s p50 · LLM 4,75 s de media · COLA
(lead/CRM/POST a Meta) 0,74 s p50. **El turno es LLM**; nuestro código pesa
~0,8 s.

### 4.1 Hecho

- **`manage.py medir_latencia`** — el banco reproducible. Cierra la deuda §VI.3
  de la biblia para este repo (falta portarlo a `wsp_cavem`).
- **Bypass del ruteo con un solo especialista** — `classify-intent` costaba
  1,01 s de media en el **100 %** de los turnos para elegir entre una opción.
  Anclado por un par de tests que exigen lo contrario uno del otro, así que un
  `CustomSpecialist` creado desde el panel restituye la clasificación solo.
- **Instrumentación de la CABEZA** — `preparar-contexto`,
  `antecedentes-faltantes` y `resolver-especialista` son spans propios.

### 4.2 El RAG, medido y acelerado — 2026-09-22

La consulta al RAG pasó de **2,97 s a ~0,9 s** en estado estable, sin tocar una
sola decisión de calidad. Eran tres clientes de red reconstruidos en cada
consulta:

| etapa | antes | después |
|---|---|---|
| embedding | 1336 ms | **737 ms** |
| rerank | 484 ms | **371 ms** |
| construir los clientes | 220 ms | 0 |

**El detalle que casi me hace shippear un bug:** cachear el cliente *async* a
nivel proceso revienta, porque `async_to_sync` crea un event loop nuevo en cada
request — medido, "Event loop is closed" en 2 de 4 requests, intermitente. La
forma correcta es cliente **síncrono** cacheado, corrido con `asyncio.to_thread`;
eso además saca las tres llamadas del event loop, que es la otra mitad del
arreglo (el RPC bloqueaba ~459 ms a todos los contactos del proceso).

**Puerta de calidad:** `manage.py evaluar_rag`, que nunca se había corrido.
Recall **18/19 antes y después**, relevancia 4,56 → 4,61. Ahora hay línea base
versionada para el próximo cambio.

El `doctor` atajó el refactor: su chequeo de dimensión del embedding lee el
código fuente de la función, y al mover el cliente quedó mirando un lugar vacío
— degradándose a AVISO, no a FALLA. Corregido.

### 4.3 Abierto, por tamaño

1. **La ronda de tool, en el turno de nombrar y preguntar — medida el
   2026-09-22 por la tarde.** Historia: el 15-09, 19 de 56 turnos con tool
   medían 10,08 s contra 3,82 s. A la mañana del 22-09 el RAG ya estaba en
   ~0,9 s (§4.2) y, en la primera prueba, la tool de catálogo medía 0,02 s:
   el costo era la segunda generación. La salida que se construyó no es el
   prefetch (biblia §III.2 lo tiene descartado): es la ficha corta, una línea
   por solución activa dentro del turno, para que nombrar y preguntar no
   pidan tool. Medido con `medir_latencia`, la misma tarde, entorno
   `development`: antes de las 18:40 UTC, 7 turnos con tool a **6,32 s** de
   media; desde las 18:40, 3 turnos sin tool, una generación, **2,90 s** de
   media (p50 2,81 s, máx 3,13 s). Detalle, cifra y el costo de
   `reasoning: none` en la biblia §III.1.

   **Cerrado por ahora el 2026-09-22, al apagar.** No hay implementación
   pendiente. Tres condiciones, ninguna es una tarea: el turno corto se lee
   con `reasoning: none` y en la prueba se leyó bien (se reabre solo si
   aparece voseo, una falta grave o un dato fuera de la ficha; no se agrega
   una segunda generación); el turno de detalle, filtro o RAG sigue en dos
   generaciones, ~6,3 s, y es el camino correcto de esa pregunta; el ancho
   de 78 caracteres se cargó a las 18:57 UTC, después de la muestra de 36, y
   un vistazo al teléfono es opcional. Cavem no se toca. El relato está en
   `hilo.md`, cierre del 2026-09-22, y en la biblia §III.1.
2. **Arranque en frío: 5,9 s en el primer turno tras >600 s de silencio.**
   2 de 2, sin contraejemplo en 61 turnos. **Causa NO probada:** la sonda al
   SQL Server actual dio 0,01–0,04 s para abrir la conexión. Por eso se
   instrumentó en vez de arreglar a ciegas. Con tráfico esporádico esto golpea
   el primer turno de casi toda conversación.
3. **`extract-metadata` (7,72 s de media) retiene el único hilo de
   `cola_envio::_EJECUTOR`.** Hoy no duele con una conversación; con varias en
   paralelo las partes 2..N de todos esperan detrás. Es la deuda §VI.7 de la
   biblia.
4. **El lead y el CRM corren antes de `send_text`** (`_despachar_si_corresponde`
   con `timeout=15`, `notify` con `timeout=5`). Hoy cuesta 0,74 s p50: es
   riesgo de cola, no de media. Reclasificado a fiabilidad.
5. **`send_text` devuelve `False` y los llamadores lo ignoran**: un rechazo de
   Meta se guarda igual como mensaje `assistant`.

---

## 5. Por qué Cavem no se apagó cuando debía

Queda escrito porque es una lección de proceso, no un olvido técnico.

El apagado estaba en `docs/PROCEDIMIENTO_TRASPASO_NUMERO.md` desde que se
escribió. Se preguntó explícitamente si se ejecutaba; la respuesta del usuario
atendió otro tema de ese mismo mensaje (el login SQL) y el apagado **nunca quedó
autorizado**. No se asumió, y quedó pendiente sin que nadie lo notara hasta que
el usuario preguntó.

**La lección:** cuando una pregunta con varias partes vuelve respondida a medias,
hay que repreguntar la parte que faltó en vez de dejarla en el documento. Un
pendiente que vive sólo en un procedimiento escrito no se ejecuta solo.
