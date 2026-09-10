# Pendientes — wsp_intouch

Estado al **2026-09-10 12:55 UTC**, verificado contra el sistema corriendo (no
de memoria). Cada afirmación dice cómo se comprobó.

---

## 0. Lo urgente: el orden del apagado de Cavem

**Hoy los dos bots están arriba y los dos responden el webhook.** Verificado:

```
POST /cavem/webhook   -> 403   (llega a Django, rechaza la firma)
POST /intouch/webhook -> 403   (idem)
```

Un `403` ahí es la respuesta correcta a una petición sin firma válida: significa
que la ruta llega al bot. Si alguno diera `502`, no habría nadie detrás.

**El orden importa y hoy está al revés de lo que hace falta.** Meta sigue
apuntando a `/cavem/webhook`, así que:

- **Apagar Cavem AHORA = perder los mensajes.** Meta seguiría entregando a una
  ruta cuyo backend no existe (`502`), y WhatsApp no reintenta indefinidamente.
- El orden correcto es: **(1)** repuntar Meta, **(2)** verificar que entra por
  InTouch, **(3)** recién entonces apagar Cavem.

**Dato que baja el riesgo:** en las últimas 24 h hubo **cero tráfico real** de
Meta. Los 3 POST que registra Cavem son todos `curl/7.81.0` — pruebas de esta
sesión. O sea que hoy nadie le está escribiendo al número, y la ventana entre
(1) y (3) es de bajo impacto. Pero eso no prueba que Meta no apunte ahí; sólo
que no llegó nada.

### 0.1 Repuntar el webhook en Meta — **del usuario**

```
https://qadash.in-touchcrm.cl/intouch/webhook
```

**Sin `/wsp/`.** Los runbooks de los dos bots decían
`https://<host>/wsp/<slug>/webhook`, y **esa ruta está mal**: cae en el
catch-all de la SPA y devuelve HTML, no Django. Verificado: antes de agregar el
bloque de nginx, `POST /intouch/webhook` daba **405**; ahora da 403.

El handshake de verificación ya funciona por nginx (probado):

- con el `WHATSAPP_VERIFY_TOKEN` correcto → devuelve el `hub.challenge`
- con un token incorrecto → `403`

### 0.2 Verificar que entra por InTouch

Después de repuntar, mandar un mensaje real al número y confirmar:

```bash
docker compose -f /home/admincrm/wsp_intouch/docker-compose.yml logs -f web | grep -E "POST /webhook|wa_id"
```

Tiene que aparecer un `POST /webhook` con user-agent de Meta (no `curl`).

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

### 1.1 El DDL del RAG en Supabase — **es la única falla del `doctor`**

```
✗ match_documentos no responde en el schema 'intouch'
    Error PGRST106: Invalid schema: intouch
```

El DDL está emitido y listo en `docs/rag_schema_intouch.sql`. Hay que pegarlo en
el SQL editor de Supabase (es web, no hay acceso por CLI desde el host) y
**además** agregar `intouch` en *Settings → API → Exposed schemas* — eso no es
SQL y se olvida siempre; sin eso todo falla con `PGRST106` aunque la tabla exista.

**Consecuencia mientras no esté:** el bot conversa, califica y usa su catálogo de
soluciones, pero **no puede responder preguntas de fondo** (cómo funciona una
solución, políticas, tratamiento de datos). El especialista va a decir
honestamente que no tiene el dato, que es el comportamiento diseñado — pero es
media funcionalidad menos.

### 1.2 Indexar el conocimiento, después del DDL

```bash
cd /home/admincrm/wsp_intouch
docker compose exec web python manage.py cargar_conocimiento_rag
docker compose exec web python manage.py reindexar_conocimiento_rag --cliente intouch
docker compose exec web python manage.py doctor --seccion rag   # verificar que indexó CHUNKS
```

**Verificar que indexó chunks, no que el comando terminó sin error.** El
reindexado indexa `ScrapedPage`, no archivos: en el bot hermano el comando corría,
imprimía "0 páginas reindexadas" y el RAG quedaba vacío. Es el modo de falla más
caro de esta parte, porque el bot arranca perfecto y contesta cualquier cosa.

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

### 2.1 El destino externo del lead está apagado (`LEAD_SINK=none`)

El circuito emisor → receptor **está probado de punta a punta** contra el CRM
real, en sus dos ramas (creación y replay). Pero en el `.env.docker` el sink
sigue en `none` a propósito: encenderlo es una decisión de operación, no de
código. Para encenderlo:

```
LEAD_SINK=http
LEAD_SINK_URL=http://crm-api:3001/api/ingest/intouch-lead
LEAD_SINK_TOKEN=<el de /home/admincrm/.ingest_secret_for_bot>
```

Y hay que engancharle al servicio `web` la red `crm_ingest` en el
`docker-compose.yml` — hoy no está declarada, y el E2E se corrió con
`docker run --network crm_ingest` justamente para no tocar el compose.

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

El scraping estructurado (`extract_catalog`) tiene un guard que falla ruidoso si
alguien configura una `ScrapingSource`, porque el prompt se reescribió a B2B y el
parser sigue esperando el JSON del vertical viejo. Adaptarlo es trabajo con su
propio diseño; este bot no lo usa (su conocimiento entra por `.md`).

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

**Sin medir todavía:** el recall del RAG (falta indexar) y la latencia (el
simulador no se corrió: exige confirmación puntual y gasta llamadas reales).

---

## 4. Por qué Cavem no se apagó cuando debía

Queda escrito porque es una lección de proceso, no un olvido técnico.

El apagado estaba en `docs/PROCEDIMIENTO_TRASPASO_NUMERO.md` desde que se
escribió. Se preguntó explícitamente si se ejecutaba; la respuesta del usuario
atendió otro tema de ese mismo mensaje (el login SQL) y el apagado **nunca quedó
autorizado**. No se asumió, y quedó pendiente sin que nadie lo notara hasta que
el usuario preguntó.

**La lección:** cuando una pregunta con varias partes vuelve respondida a medias,
hay que repreguntar la parte que faltó en vez de dejarla en el documento. Un
pendiente que vive sólo en un procedimiento escrito no se ejecuta solo.
