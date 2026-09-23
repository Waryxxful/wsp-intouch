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

## 2026-09-22 — Prueba de Tomas: largo, latencia y la ficha corta

Dos conversaciones de prueba, el mismo `wa_id`. Entre una y otra se borró la
memoria (conversaciones, mensajes y leads). Catálogo y prompts no se tocaron.

### Largo

El prompt pedía párrafos breves y el código partía recién a los 600
caracteres, así que un catálogo salía en cuatro burbujas. El tope pasó a
seis líneas de teléfono entre todas las burbujas del turno, y a dos burbujas
como máximo. La pregunta se conserva; lo que no entra no se manda.

El primer corte era malo: partía en dos apenas pasaba de tres líneas y
truncaba la frase («la prioridad es que el»). Se dejó de cortar una oración
a la mitad. Después, en la segunda prueba, un acuse más una pregunta que en
el teléfono son unas tres líneas igual salían en dos burbujas: el ancho
estaba en 36 caracteres y las contaba como más de seis. Quedó en 78. Ese par
(~230 caracteres) es una sola burbuja. Seis líneas, el extremo, llegan a
unos 470 caracteres.

### Latencia de la primera prueba

`medir_latencia` sobre las trazas de esa conversación, entorno `development`,
6 turnos, todos con herramienta, modelo `deepseek/deepseek-v4-flash-0731`.
El «hola» no entra: salió en 1,3 s sin modelo.

| | media |
|---|---|
| Turno | 6,33 s (p50 5,81 s, máx 7,84 s) |
| Elige la herramienta | 1,85 s |
| La ejecuta | 0,02 s |
| Redacta | 3,56 s |
| Envío | 0,72 s |

El ruteador no estaba: con un solo especialista ya se había apagado. La
herramienta no es el costo. Lo que sobra es la pasada que elige la
herramienta y no escribe nada.

### La ficha corta

En cada turno entra una línea por solución activa, leída de
`bot_solucionintouch`. Con eso se puede nombrar qué hace InTouch sin llamar
a `listar_soluciones`. La herramienta queda para el detalle, para filtrar
por canal o categoría, y para el fondo (cómo funciona, datos, seguridad).
Acusar recibo de lo que la persona acaba de contar —el Excel, el rubro, el
volumen— se responde en la misma pasada.

Si eso se cumple, la media de la primera prueba bajaría a unos 4,5 s. La
segunda prueba fue corta y la velocidad se sintió bien; no se volvió a
correr `medir_latencia`, así que el 4,5 s sigue sin medir.

### Medido después, la misma tarde

`medir_latencia` partido a las 18:40 UTC, entorno `development`. Antes: 7
turnos con tool, media 6,32 s, y la tool en 0,02 s. Desde las 18:40: 3
turnos, ninguno con tool, una generación, media 2,90 s (p50 2,81 s, máx
3,13 s). La generación que escribe midió 2,14 s. El 4,5 s restaba la pasada
de selección y dejaba la redacción; sin tool el grafo no corre esa
redacción. Quedó en la biblia §III.1.

Esos tres turnos salieron con el ancho en 36. El 78 quedó cargado a las
18:57, después. n=3, sin control de llamada mínima y sin juez.

Prompt republicado y gunicorn recargado con HUP. El doctor sigue en FALLA
mientras `TEXTO_CONSENTIMIENTO` esté vacío y `LEAD_SINK` no sea `none`.

### Lead y panel

El lead de la primera prueba quedó en `intouch.bot_leadintouch` (fila 6,
Tomas, WARM). El CRM lo rechazó tres veces con 400: `despachado_en` vacío.
El bot no lo había derivado: había preguntado si le acomodaba un especialista
y la conversación se cortó sin un sí.

En el dashboard, «Respuesta» estaba en minutos, así que 6 segundos se veían
como 0. Ahora dice «Promedio por mensaje» y el valor va en segundos. Cuenta
cada pregunta hasta la primera burbuja. El bundle está en
`staticfiles/mf/wsp_intouch/`.

### Commits

`c8e637d` y `2a6a3d0`, autor Tomas Valenzuela. El push a
`Waryxxful/wsp-intouch` respondió 403. Lo de después —el largo, la ficha
corta y la tarjeta del panel— no está commiteado.

### Cierre al apagar — 2026-09-22

Quedó listo por ahora. No hay tarea abierta con la latencia ni con el largo.
Los tres límites que se conversaron son condiciones, no trabajo pendiente:

1. **Razonamiento apagado en el turno corto.** El contacto lee la primera
   llamada. En la prueba se leyó bien. Se mira de nuevo solo si en una
   conversación real aparece voseo, una falta grave o un dato que no está en
   la ficha. No se agrega una segunda generación para cuidar la prosa: eso
   devuelve los ~6 s.
2. **Turno de detalle, filtro o RAG.** Sigue en unas dos generaciones, ~6,3 s,
   y cerca de 7 s si la consulta es el RAG. Es el camino correcto de esa
   pregunta. No se diseña otra cosa mientras nombrar y preguntar sea lo
   frecuente.
3. **Ancho 78 y Cavem.** Falta, si se quiere, un vistazo al teléfono: un acuse
   más una pregunta, de unas tres líneas, tiene que llegar en una sola
   burbuja. Cavem no se toca. La ficha no se le copia: allá la herramienta
   manda el pin, el PDF o la foto. El corte de seis líneas solo se porta si
   en Cavem las respuestas también se van largas.

Dónde quedó escrito:

- Biblia, `/home/admincrm/docs-repo/biblia_bots.md` §II.1, §III.1, ley 4 y
  §III.6. Ahí están las cifras y el contrato del corte.
- `PENDIENTES.md` §4.3 punto 1, marcado como cerrado por ahora.
- `auditoria latencia/estado-actual-wsp-intouch.md`, el corte y la ficha.

Nada de esta sesión está commiteado. En disco sí: sobrevive al apagado. El
código que corre es el del working tree (ficha en `bot/flow/contexto_turno.py`,
corte en `bot/whatsapp/handlers.py`, workers recargados a las 18:57 UTC).

## 2026-09-23 — Chat 9 del gerente comercial: el sitio entra, los contactos van a la ficha, la regla de clientes

Felipe, gerente comercial, probó el bot en el chat 9: 38 turnos, con una
campaña de renovación (2.000) y fidelización (3.000) para «AutoCar». El bot
respondió «no tengo ese dato confirmado» nueve veces. Le preguntaron el
teléfono, el correo, la dirección, los clientes y las cifras. Tiempo por turno,
de mensaje guardado a respuesta guardada: media 3,7 s, p50 2,8 s, p90 6,8 s,
máximo 11,8 s (los lentos fueron los de RAG).

### Por qué no sabía

in-touch.cl nunca se había scrapeado: la única fuente eran los siete `.md`.
Cuando el usuario lo scrapeó desde el panel, el run quedó en «error» aunque ya
había indexado 21 fragmentos: `extract_catalog` levantaba NotImplementedError
DESPUÉS de indexar. Y el crawler perdía justo lo que se preguntó:

- Solo leía `p/li/table/h1-3`. La dirección, las cifras y los KPIs estaban en
  `<div>`. El resto de la página superaba el piso de cobertura del 50 %, así
  que el fallback a texto plano no se activaba: se perdían sin aviso.
- Borraba el `<footer>` entero, que es donde están el teléfono y el correo.
- El correo estaba ofuscado por Cloudflare («[email protected]»).

Con eso arreglado, los datos quedaron en el RAG, **pero la búsqueda no los
encontraba**. «¿Cuál es el teléfono?» no traía el fragmento entre los 20
candidatos: no dice «teléfono». «¿Dónde están?» lo tenía en el puesto 2 y el
rerank lo descartaba, porque la dirección iba dentro del fragmento de la
consultoría. Un dato fijo y corto no puede depender del ranking.

### Qué se hizo

| Commit | Qué |
|---|---|
| `0232ac4` | Crawler: bloques hoja (salvo dentro de `<form>`), rescate del pie con `tel:`/`mailto:`, correos de Cloudflare, dedup por párrafo. El run termina en «ok» |
| `ded886e` | El trabajo sin commitear del 22-09 (ficha corta, corte a 6 líneas, tarjeta del panel), commiteado con autorización del usuario. Se actualizó la prueba heredada que esperaba una burbuja por párrafo |
| `51600fd` | `ContactoInstitucional` (migración 0043, aplicada en producción): teléfonos y correos salen de los enlaces sin LLM; las direcciones las lee un LLM y el código descarta cualquiera que no esté escrita en la página. Van a la ficha de cada turno con su rótulo. `doctor` falla si no hay |
| `c246b61` | Regla de clientes del gerente comercial, publicada (PromptVersion global 7, comercial 8) |
| docs-repo `a9a2d76` | Biblia §VI.5: el paso hecho y las dos lecciones del crawler |

La regla de clientes, en palabras de Felipe: el bot no da nombres; si el
contacto es automotriz, InTouch «es líder en la industria automotriz»; si es de
otra industria, «tiene presencia y experiencia en la industria automotriz, en
empresas privadas y corporativas, y en entidades públicas». Se quitó «todavía
no hay una ficha firmada», que impedía decir hasta los 18 años del sitio. Las
cifras del sitio se citan si las trae la base de conocimiento; las métricas,
siempre con su aclaración (promedios en clientes automotrices e industriales,
2022-2024).

### Verificación

- Suite: 1996 tests en la última corrida completa, en verde tras actualizar la
  prueba heredada del corte.
- Direcciones contra el LLM real con el texto del sitio: 3 de 3 exactas, sin
  inventar.
- Prompt validado ANTES de publicar, con `override_prompts`, en un contenedor
  aparte (`LEAD_SINK=none`, WhatsApp y notify mockeados, Langfuse apagado). Nueve
  conversaciones TEST, borradas después junto con su Incident. El bot da el
  teléfono «como el de Recursos Humanos», las dos direcciones, las cifras con
  su aclaración y los clientes en términos generales.
- Scrapeo real (run 4): 4 contactos en la tabla. `doctor`: prompts activos =
  git.

### Tres cosas que esta sesión aprendió

1. **Que el dato esté en el RAG no significa que el bot lo encuentre.** Se mide
   la búsqueda con la pregunta real del contacto, no con `SELECT` a los chunks.
2. **«No nombres clientes» salió como «no nombre clientes».** El modelo imitó el
   imperativo. Con «Nunca des nombres de clientes» respondió «No compartimos
   nombres de clientes». Solo se vio corriendo el LLM real.
3. **Un `grep -v "dios"` para limpiar logs escondió «promedios».** Pareció un
   turno sin respuesta y era un filtro. Antes de diagnosticar un silencio, mirar
   la salida sin filtrar.

### Lo que queda

En `PENDIENTES.md`, sección «RETOMAR ACÁ»: la derivación y el CRM (el lead no
llegó, el notify dio 404, el score se degrada), el extractor, el comportamiento
del prompt (consultoría gratuita, implementación ≠ plazo, consentimiento,
saludo, nombre), las listas corridas del corte y el contacto comercial que
falta en el sitio.
