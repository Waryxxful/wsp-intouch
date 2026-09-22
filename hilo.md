# hilo.md — bitácora por sesión

## 2026-09-09 — Task 1: repo, identidad y línea base

Copiado el árbol de `wsp_cavem` @ `2cfa257` (ver `.origen-cavem`) sin su
historia de git. Cliente nuevo `intouch` agregado a `CLIENTE_CHOICES`
(`renault`/`astara`/`cavem` se conservan porque la suite heredada los usa en
sus fixtures). Slug `intouch`, puerto backend `8040`, dev server Vite `8041`,
scope de module federation `wsp_intouch`. `dios.json.example` reescrito con
la identidad y `docker-compose.yml` publica `8040:8000`. Migración
`bot/migrations/0035_cliente_intouch.py` generada para el choice nuevo.

Borrado lo específico de Cavem (Step 2 del plan): `seed_cavem.py`,
`importar_stock_cavem.py`, `stock_cavem.csv`, `prompt_ventas.md`,
`schema_astara.sql`, los docs de Cavem, y todo `bot/fixtures/rag/*.md`.

Nota aparte (no del plan): la copia con `rsync` trajo
`.claude/worktrees/agent-*` de `wsp_cavem` — worktrees de otras sesiones cuyo
`.git` apunta literalmente al `.git` real de `wsp_cavem` (`gitdir:
/home/admincrm/wsp_cavem/.git/worktrees/...`). Se borró esa carpeta completa
del árbol de `wsp_intouch` antes de tocar nada más: dejarla habría sido
arrastrar un puntero vivo al repo ajeno que la task tiene prohibido tocar.

### Línea base de tests (imagen `wsp_intouch-web`, comando de Global
Constraints, `CLIENTE_ACTIVO=renault`)

**Total: 1598 tests. 8 fallas (todas `ERROR`, cero `FAIL`), 10 skipped
(pre-existentes, chequeos de red que se saltean solos — no relacionados con
esta task).**

Grupos de fallas:

1. **Lead de autos (stock de usados) — 3 ERROR.** `bot.tests.test_usados_cavem`
   y `bot.tests.test_simulator_code_evaluators` no cargan (`ModuleNotFoundError:
   bot.management.commands.importar_stock_cavem`, borrado en el Step 2); y
   `bot.tests.test_graph.BusinessActionNodeMultiplesToolCallsTest
   .test_ejecuta_las_dos_tool_calls_de_un_mismo_turno` cae en cascada porque
   importa `crear_vehiculo` desde `test_usados_cavem`. Esperado: se resuelve
   en las Tasks 7 a 10.

2. **Binding/seed del especialista `ventas` — 4 ERROR.**
   `bot.tests.test_seed_cavem.SeedCavemPromptsTest` (sus 4 tests) llama a
   `call_command("seed_cavem")`, comando borrado en el Step 2
   (`CommandError: Unknown command: 'seed_cavem'`). Ese comando era el que
   publicaba el `PromptVersion` de `custom:ventas`. Esperado: se resuelve
   cuando el especialista `comercial` reemplace a `ventas` (Tasks 8-9).

3. **Fixture del prompt (`doctor.py::chequear_prompt_contra_fixture`) — 0
   fallas, PERO con una salvedad a anotar.** El chequeo hace
   `if fixture_ventas.is_file():` antes de comparar contra
   `bot/fixtures/prompt_ventas.md` (borrado en el Step 2) — con el archivo
   ausente, simplemente omite esa comparación en vez de fallar, y ningún test
   de `bot/tests/test_doctor.py` ejercita ese chequeo puntual (no lo
   importa). Es decir: la degradación es silenciosa, no hay un test rojo que
   la marque en esta línea base. Se resuelve recién en la Task 16, que
   reapunta el chequeo al fixture nuevo (`prompt_comercial.md`) y agrega
   `test_doctor_intouch.py`.

4. **Conocimiento del RAG (`bot/fixtures/rag/*.md`) — 1 ERROR.**
   `bot.tests.test_cargar_conocimiento_rag.CargarConocimientoRagTest
   .test_los_documentos_reales_del_repo_se_cargan` (`CommandError: no hay
   archivos .md en /app/bot/fixtures/rag`). Este test corre
   `cargar_conocimiento_rag` contra el directorio real del repo y espera 5
   páginas; el Step 2 borró los 5 `.md` de Cavem sin reemplazo. No estaba
   entre los tres grupos que anticipaba el enunciado de la task (se reportó
   como concern al coordinador, quien confirmó que es consecuencia directa y
   prevista del propio Step 2, no señal de una copia mala). Se resuelve
   cuando la Task 12 cargue el conocimiento propio de InTouch.

Con estos cuatro grupos la línea base queda con sus 8 fallas totalmente
explicadas: ninguna falla aparece fuera de ellos, y ninguna otra task puede
aumentar ese número de 8.

## 2026-09-09 — Fix rounds de la review de la Task 1

### Round 1 (Critical + Important + Minor)

Critical: 56 ocurrencias de `/cavem/api` en 26 archivos (frontend + 2 de
`admin_panel/`) hacían que el panel de InTouch, servido bajo
`/mf/wsp_intouch/`, hiciera fetch al puerto 8030 — Cavem en producción.
Reemplazo mecánico a `/intouch/api`. Deuda anotada en un comentario de
`frontend/src/App.tsx`: unificar esas rutas absolutas con el `apiBase` del
contract es el refactor de fondo, no se hizo esa noche (27 archivos sin
tests que los cubran).

Important (planteado en el round 1, **revertido en el round 3, ver abajo**):
se había reescrito el dataset de demo (`admin_panel/proyeccion.py`, 959
líneas: diálogos, una sucursal y 3 campañas de WhatsApp con la identidad de
Cavem/"Auto IA") a una narrativa B2B de InTouch. Eso rompió 3 tests reales
de `admin_panel/tests_proyeccion.py` que protegen defensas de verdad
(anti-alucinación de montos, cobertura de diálogo completo) y nunca se
verificó en el momento (se corrió sólo `admin_panel.tests`, no
`admin_panel.tests_proyeccion`). El round 3 revirtió ese contenido
completo: ver la sección de abajo, es la versión final.

### Round 2 (gate por cliente) + Round 3 (revert del contenido, se queda sólo el gate)

El round 2 gateó `admin_panel/proyeccion.py` por `CLIENTE_ACTIVO`, pero
sobre el dataset YA REESCRITO del round 1 -- combinación que dejó 3 tests en
rojo (ver el punto anterior). El round 3 corrigió el orden: **una vez que
existe el gate, reescribir el contenido no compra nada** (a nadie se le
sirve ya) **y sí cuesta** (rompe tests que protegen defensas reales). La
combinación correcta era gate + dataset intacto. Se revirtieron
`admin_panel/proyeccion.py` y `admin_panel/tests_proyeccion.py` al estado
anterior al round 1 (`git checkout 9429197 --`) y se reaplicó **sólo** el
gate encima.

**Estado final: `admin_panel/proyeccion.py` queda con su dataset automotriz
de Cavem/"Auto IA" 100% INTACTO (nunca se sirve a ningún cliente fuera del
set heredado) y gateado por `CLIENTE_ACTIVO`.** Las 7 funciones públicas
(`leads`, `reservas`, `campanas`, `dashboard`, `conversaciones`, `mensajes`,
`es_id_proyectado`) devuelven vacío para cualquier cliente fuera de
`{renault, astara, cavem}` (constante `_CLIENTES_CON_DEMO_AUTOMOTRIZ`), vía
el decorador `_gatear_demo_automotriz`. Con `CLIENTE_ACTIVO=intouch`: listas
vacías, `dashboard()`/`conversaciones()`/`mensajes()` con
`es_proyeccion: False`, y `es_id_proyectado()` siempre `False`. Test propio
agregado (`DemoAutomotrizGateadaPorClienteTest` en `tests_proyeccion.py`, 5
tests con `override_settings(CLIENTE_ACTIVO="intouch")` + 1 que fija que
renault/astara/cavem siguen viendo el dataset completo).

**Deuda anotada, explícita, para que no se pierda:**

1. **El dataset de demo queda conservado intacto y gateado por cliente,
   sin narrativa propia para InTouch.** Sigue siendo la demo automotriz de
   Cavem (sus 63 tests la protegen, incluida la defensa anti-alucinación de
   `test_la_simulacion_de_la_tucson_sale_de_la_tool_real`), simplemente ya
   no se le sirve a este bot. Si algún día InTouch quiere su propio dataset
   de demo, es trabajo propio, con su propia narrativa B2B coherente de
   punta a punta (`leads()`, `reservas()`, `campanas()`, `conversaciones()`,
   `_TRANSCRIPCIONES`) y su propio spec -- no algo que se improvisa
   reescribiendo encima del dataset de otro cliente.
2. **Deuda del `apiBase` (Round 1, Critical -- esta sí se queda).** Los
   ~26 archivos de `frontend/src/` usan rutas absolutas `/intouch/api/...`
   en vez de leer el `apiBase` que el shell pasa por el contract (ver
   comentario en `App.tsx`). Refactor de fondo pendiente, sin tests de
   frontend que lo cubran hoy.

### Línea base de tests — vuelve a 8 (confirmado, no asumido)

Corrida completa después del revert del round 3: **1603 tests (1598 + 5
nuevos del test del gate), 8 fallas (todas `ERROR`, cero `FAIL`), 10
skipped.** Mismas 4 causas originales de la línea base de la Task 1, sin
ninguna nueva:

1. Lead de autos (stock de usados) — 3 ERROR (ver arriba).
2. Binding/seed del especialista `ventas` — 4 ERROR (ver arriba).
3. Fixture del prompt (`doctor.py::chequear_prompt_contra_fixture`) — 0
   fallas, degradación silenciosa (ver arriba).
4. Conocimiento del RAG (`bot/fixtures/rag/*.md`) — 1 ERROR (ver arriba).

Los 3 `FAIL` que había introducido el round 1 (`test_la_simulacion_de_la_
tucson_sale_de_la_tool_real`, `test_tres_conversaciones_tienen_dialogo_
completo`, `test_los_vehiculos_del_lead_aparecen_en_su_conversacion`)
desaparecieron con el revert: el contenido que hacían fallar volvió a ser
el original.

---

## 2026-09-09 — cierre de la sesión: las 20 tasks implementadas

Plan completo (`docs/superpowers/plans/2026-09-09-bot-intouch-comercial.md`)
ejecutado con subagentes: un implementer por task, reviews entre medio y un fix
consolidado al final. 27 commits.

### Números de partida

Lo que hay que anotar acá es lo que nadie va a poder reconstruir después.

| Qué | Valor | Cuándo se midió |
|---|---|---|
| Suite completa | **1.852 tests, 0 fallas, 13 skipped** | al cierre real, medido de la corrida |
| Suite antes de cerrar los tests huérfanos | 1.763 tests, 11 fallas — y ocultaba 79 tests tras un `ImportError` | mitad de sesión |
| Línea base heredada al copiar el árbol | 8 fallas ERROR, 4 causas identificadas | Task 1 |
| `test_graph` tras reescribir `AGENTS` | 117 tests, OK — las 9 defensas conservadas | commit `cd37db3` |
| `admin_panel` | 261 tests, OK (255 previos + 6 del panel de leads) | Task 18 |
| Recall del RAG | **sin medir** — falta indexar | pendiente de credenciales |
| Latencia | **sin medir** — el simulador no se corrió | pendiente de confirmación |

Las 11 fallas están todas en archivos heredados del vertical automotriz, ninguna
en código de InTouch. 9 de ellas son dos archivos huérfanos (`test_seed_cavem.py`
prueba un comando borrado; `ReglasDeCalidadDelPromptTest` prueba reglas de
sucursales y precios que este bot no tiene).

### Lo que quedó bloqueado, y por qué

Login SQL `intouch_login_qa` con `DEFAULT_SCHEMA=intouch`; credenciales de Meta;
pegar el DDL de `docs/rag_schema_intouch.sql` en Supabase (su editor es web);
confirmar `GRANCRM_TENANT_SLUG` (la cuenta `qaintouch` existe, pero define a qué
cuenta llegan las notificaciones de lead HOT: es decisión de negocio).

Ver `docs/PENDIENTE_CREDENCIALES.md` y `docs/DEPLOY_INTOUCH.md`.

### Deuda declarada

1. **Nueve tasks sin review formal** (8, 10, 12–18): tienen código y tests
   propios en verde, pero sólo las tasks 1–7, 9 y 11 pasaron por un revisor
   dedicado. Se hizo **una review agrupada** en vez de nueve individuales, y esa
   decisión se justificó sola: encontró 4 Critical, y 3 eran de **interacción**
   entre tasks, que una review por task no ve por definición.
2. **El frontend no se compiló.** Los tipos se verificaron a mano contra el
   `.d.ts` instalado; falta `pnpm build` y el `cp` a `staticfiles`.
3. **El drift de tres repos** (biblia §VI.1): este bot es el tercero con el mismo
   grafo copiado a mano. Lo que aporta al diseño de `wsp-bot-core`: hay que
   parametrizar el vertical, el catálogo, el modelo de lead y las secciones del
   `doctor`.
4. **El frontend usa rutas absolutas** en vez del `apiBase` del contract (26
   archivos sin tests).
5. **`comercial.py` lee su fixture a nivel de módulo**: si falta, el traceback es
   crudo. Merece un `ImproperlyConfigured` con la instrucción de restaurarlo.

### Lo que esta sesión le enseñó al stack

Cinco veces apareció el mismo modo de falla: **cambiar un registro central rompe
en silencio lo que hardcodea sus valores.** Reformatear `CLIENTE_CHOICES` mató el
generador del schema del RAG (exit 70, y ese script existe para no escribir el
nombre del schema a mano — el error que mandó 297 chunks al schema equivocado).
Cambiar `CATEGORIAS_RAG` dejó ~20 fixtures probando otra cosa sin fallar.
Reescribir `AGENTS` dejó 9 tests del grafo esperando slugs muertos. Reemplazar los
escenarios semilla rompió el test de su migración. Y la quinta, la peor: un test
reseedeaba la base invocando por `importlib` la función de siembra de la
**migración vieja**, resucitando en cada corrida los escenarios que la nueva había
borrado — sin romper ningún assert, porque Django corre esas pruebas al final. La
encontró un `grep` exigido como parte de la defensa, no un test en rojo.

Está en `docs-repo/biblia_bots.md` §IV.1 con los cinco casos, y §V.2 con dos
notas: un test que no falla ante el defecto que persigue no es un test, y un test
no puede borrar ni dejar modificado un archivo trackeado del repo (caso real: los
tests del `doctor` borraban el fixture del prompt en cada corrida, y como se lee
en tiempo de import, después no arrancaba ni `manage.py check`).

---

## 2026-09-21 — Latencia: medir primero, y corregir la auditoría del 15-09

El pedido era ejecutar `auditoria latencia/auditoria-latencia-wsp-intouch.md`.
Lo primero que apareció al medir es que **su premisa era falsa**, así que la
sesión terminó siendo medición + dos arreglos + la corrección del documento.

### Lo que cambió respecto de lo que creíamos

**El turno es 5,94 s de media y 3,99 s de mediana**, no 7,6 s (n=56 turnos
reales del 2026-09-15, Langfuse). Los 7,6 s son la media de `extract-metadata`
(7,72 s), que corre en la cola **después** del envío. Se venía priorizando
contra un número que medía otra cosa.

Partido en tres tramos: CABEZA (ORM/contexto) 0,05 s p50 · LLM 4,75 s de media ·
COLA (lead/CRM/POST a Meta) 0,74 s p50. **El turno es LLM.** Eso tira abajo tres
hallazgos de la auditoría (cola serial, contexto duplicado, trabajo repetido):
son ciertos en el código y valen ~0 segundos. Y su hallazgo "Alta" sobre el CRM
antes de WhatsApp es real pero cuesta 0,74 s — riesgo de cola, no de media.

La auditoría además reabría tres experimentos que la biblia §III.2 ya había
descartado con evidencia (streaming, bajar el razonamiento final, prefetch de
tool). Corrió desde GitHub en otra máquina, sin acceso a `docs-repo` ni a
Langfuse; lo dice en sus propios límites. **Sirve de recordatorio: una auditoría
sin acceso a las mediciones del stack repite lo que ya se pagó.**

### Lo hecho

1. **`manage.py medir_latencia`** — el banco reproducible que pedía la biblia
   §VI.3 desde hacía tres auditorías. Falta portarlo a `wsp_cavem`.
2. **Bypass del ruteo con un solo especialista registrado** — `classify-intent`
   costaba 1,01 s de media en el **100 %** de los turnos para elegir entre una
   opción. Verificado que no podía devolver otra cosa: el fallback de output
   inválido y el override de `intencion_compra_real` también terminan en el
   único slug. Agravante: `_AGENTE_ESPECULADO` sigue siendo `"ventas"`, que no
   está en el registro, así que el bot pagaba el ruteo **y** perdía la
   especulación que lo compensa en cavem.
3. **Instrumentación de la CABEZA** — `preparar-contexto`,
   `antecedentes-faltantes` y `resolver-especialista`.

### Tres cosas que esta sesión aprendió a los golpes

**No se arregla a ciegas lo que no se pudo reproducir.** Dos turnos mostraron
5,9 s de CABEZA, y los dos eran los únicos precedidos por más de `CONN_MAX_AGE`
de silencio — 2 de 2 en 61 turnos. La causa "obvia" era el handshake a SQL
Server, que este repo ya tenía medido lento e intermitente en
`config/settings.py`. Pero la sonda contra el servidor actual dio **0,01–0,04 s,
seis veces seguidas**. La correlación es real; el mecanismo no. Se instrumentó
en vez de inventar un arreglo.

**Correr la suite con `docker exec` contamina Langfuse de producción.** El
contenedor tiene las credenciales; el `docker run` documentado en el `CLAUDE.md`
no. Tres corridas dejaron 48 `run-business-action` de 0,01 s en el proyecto, que
al mezclarse duplicaban el conteo de tools por turno y casi me hacen reportar
una regresión inexistente. `medir_latencia` ahora excluye lo que no cuelga de un
turno y lo informa aparte. **Y el proyecto de Langfuse está compartido con
cavem:** 6.125 de 6.423 observaciones eran de otro bot.

**Correr la suite con el comando equivocado inventa fallas.** Sin
`CLIENTE_ACTIVO=renault` aparecen 4 fallas que no existen — los managers
filtrados por cliente dejan los fixtures invisibles, exactamente lo que
advierte el `CLAUDE.md` de este repo. Llegué a reportarlas como deuda
preexistente antes de darme cuenta.

---

## 2026-09-22 — La ronda de tool: el RAG de 2,97 s a ~0,9 s

Desglose de los 19 turnos con tool: gen#1 (decide la tool) 1,56 s · ejecución
2,41 s · gen#2 (redacta) 2,82 s. La tool dominante es el RAG (50 % de las
llamadas), y su ejecución era **un span opaco**: hubo que escribir sondas
descartables para ver qué había adentro.

Adentro había tres clientes de red **reconstruidos en cada consulta**:
embedding 1336 ms, rerank 484 ms, construcción 220 ms, RPC 460 ms. Casi todo el
costo del embedding no era calcular el vector: era rehacer el TLS.

### La trampa, que casi shippeo

El arreglo obvio es cachear los clientes. **Con clientes async eso rompe**, y de
la peor forma: `async_to_sync` (por donde entra el webhook) crea un event loop
nuevo en cada request, así que un cliente async a nivel proceso queda atado a un
loop cerrado. Medido: *"Event loop is closed"* en **2 de 4 requests** —
intermitente, indistinguible de una caída del proveedor.

La forma correcta es **cliente síncrono cacheado por proceso, corrido con
`asyncio.to_thread`**. Sobrevive a los loops, conserva la conexión, y de paso
saca las tres llamadas del event loop — el RPC a Supabase bloqueaba ~459 ms a
todos los contactos que comparten el proceso.

Resultado: la consulta completa pasó de **2969 ms a ~900 ms** en estado estable.

### La puerta de calidad, por primera vez

`bot/rag_eval` tenía 19 preguntas sembradas y **cero resultados**: nunca se
había corrido. Se corrió antes y después:

| | recall | relevancia |
|---|---|---|
| antes | 18/19 | 4,56 |
| después | **18/19** | **4,61** |

Sin esa línea base el cambio no era decidible, sólo plausible. Ahora hay serie
de tiempo versionada para el próximo.

### Lo que atajó el doctor

Su chequeo de dimensión del embedding lee el **código fuente** de la función; al
mover el cliente a una factory quedó mirando un lugar vacío, y se degradaba a
AVISO en vez de FALLA — o sea que el bot habría seguido pasando el doctor con la
dimensión sin verificar. Lo agarró su propio test, no el doctor corriendo.

### Lo que NO se hizo, a propósito

Lo estructural de la ronda de tool sigue abierto: gen#1 no produce texto para el
contacto, y la tool va entre dos generaciones completas. La salida candidata es
lanzar la recuperación en paralelo con gen#1 usando el mensaje crudo del
contacto. Antes de construirla hay que medir la tasa de acierto de esa consulta
contra la que realmente pide gen#1 — que es exactamente la pregunta que hundió
el prefetch en cavem (36 % de tool errada).

## 2026-09-22 — Demo 15-09, frentes A a E

Plan: `auditoria latencia/plan-mejoras-15-09-demo-intouch.md`. Sin commit y
sin reiniciar el contenedor.

- `crear_caso` pide decir «dejo tu caso listo para que el equipo lo tome» y
  avisa `caso_equipo` solo al crear el incidente. El prompt reconoce el
  derecho a reclamar y no tutoriza un reclamo ante el SERNAC. La frase de
  escape deja de ser la salida del precio, el plazo y la integración.
- Cada turno del comercial recibe `https://in-touch.cl`, los tres modelos y
  la franja de `BusinessHours`. «Copiloto» no se inventa: se dice que no está
  en el catálogo. El armado del prompt va por `sync_to_async`.
- `preferencia_horaria` viaja al CRM y al detalle de leads del panel. Con
  `TEXTO_CONSENTIMIENTO` vacío el teléfono sigue saliendo, y el doctor queda
  en FALLA si `LEAD_SINK` no es `none`. Con el texto puesto, el número no
  sale hasta un consentimiento otorgado y la burbuja se manda una vez.
- No se cargaron las cifras de la home (falta la ficha firmada) ni se tocó
  la ronda de tool. 119 tests de estos módulos, OK.
