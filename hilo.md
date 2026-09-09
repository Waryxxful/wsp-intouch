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

Important: `admin_panel/proyeccion.py` (dataset de demo, 959 líneas) tenía
diálogos, una sucursal y 3 campañas de WhatsApp con la identidad de
Cavem/"Auto IA" vendiendo autos. Se reescribieron 11 de las 12
conversaciones de `_TRANSCRIPCIONES` como diálogos B2B de InTouch, la
sucursal de `reservas()` y las 3 `campanas()`.

**Lo que ese fix rompió y no se verificó en el momento** (se corrió sólo
`admin_panel.tests`, no `admin_panel.tests_proyeccion`): el dataset quedó
incoherente consigo mismo (antes era consistentemente automotriz -mal
identidad, pero consistente-, después las transcripciones son B2B y
`leads()`/`conversaciones()` siguen siendo compradores de auto), lo que hizo
caer **3 tests reales** de `admin_panel/tests_proyeccion.py`
(`test_la_simulacion_de_la_tucson_sale_de_la_tool_real`,
`test_tres_conversaciones_tienen_dialogo_completo`,
`test_los_vehiculos_del_lead_aparecen_en_su_conversacion`). Se arreglaron
aparte 2 tests con string viejo hardcodeado (`test_son_las_tres_campanas_del_seed`,
`test_usa_servicios_y_sucursal_que_existen_en_el_seed`, mismo patrón que el
fix de `admin_panel/tests.py:140` del round de `/cavem/api`).

### Round 2 (gate por cliente)

La salida a la incoherencia de Round 1 no es completar el rewrite de las
959 líneas (84 apariciones de dato automotriz, proyecto propio con su
spec) ni revertirlo: es que este bot no sirva la demo de otro cliente.

**Se gateó `admin_panel/proyeccion.py` completo por `CLIENTE_ACTIVO`**: las
7 funciones públicas (`leads`, `reservas`, `campanas`, `dashboard`,
`conversaciones`, `mensajes`, `es_id_proyectado`) devuelven vacío para
cualquier cliente fuera de `{renault, astara, cavem}` (constante
`_CLIENTES_CON_DEMO_AUTOMOTRIZ`), vía el decorador
`_gatear_demo_automotriz`. Con `CLIENTE_ACTIVO=intouch`: listas vacías,
`dashboard()`/`conversaciones()`/`mensajes()` con `es_proyeccion: False`, y
`es_id_proyectado()` siempre `False`. Test propio agregado
(`DemoAutomotrizGateadaPorClienteTest` en `tests_proyeccion.py`, 5 tests con
`override_settings(CLIENTE_ACTIVO="intouch")` + 1 que fija que
renault/astara/cavem siguen viendo el dataset completo).

**Deuda anotada, explícita, para que no se pierda:**

1. **El dataset de demo queda gateado y sin narrativa propia para
   InTouch.** Detrás del gate sigue existiendo el dataset automotriz de
   Cavem (ahora invisible para `intouch`), y las 11 conversaciones B2B
   reescritas en el Round 1 quedan ahí sin uso real, desacopladas de
   `leads()`/`conversaciones()` (que siguen siendo compradores de auto). Si
   algún día se activa un dataset de demo propio para este bot, conviene
   partir de esas 11 conversaciones B2B (ya escritas, ya en tono correcto)
   en vez de los compradores de auto — pero es trabajo propio, con su
   propia narrativa coherente de punta a punta (`leads()`, `reservas()`,
   `campanas()`, `conversaciones()`, no sólo las transcripciones), y un spec
   propio, no un fix round de una review.
2. **Deuda del `apiBase` (Round 1, Critical).** Los ~26 archivos de
   `frontend/src/` usan rutas absolutas `/intouch/api/...` en vez de leer el
   `apiBase` que el shell pasa por el contract (ver comentario en
   `App.tsx`). Refactor de fondo pendiente, sin tests de frontend que lo
   cubran hoy.

### Línea base de tests — ACTUALIZADA (creció de 8 a 11 fallas)

Corrida completa después de ambos rounds: **1603 tests (1598 + 5 nuevos del
gate), 11 fallas (8 `ERROR` + 3 `FAIL`), 10 skipped.**

Los 8 `ERROR` originales siguen siendo los 4 grupos de arriba, sin cambios.
Grupo nuevo:

5. **Contenido de `_TRANSCRIPCIONES` vs. `leads()`/`conversaciones()` — 3
   FAIL, en `admin_panel/tests_proyeccion.py`.** Consecuencia directa de la
   reescritura del Round 1 (aprobada explícitamente, no un bug introducido
   sin querer), y **no se arreglan en este fix round** porque arreglarlos
   exigiría una de dos cosas fuera de alcance: revertir contenido de las
   transcripciones (que el coordinador pidió dejar como están) o decidir
   unilateralmente debilitar/borrar tests que protegen defensas reales de
   la biblia §IV.1 (anti-alucinación de montos, cobertura de diálogo
   completo) — decisión que no me corresponde tomar sola:
   - `ChatsProyeccionTest.test_la_simulacion_de_la_tucson_sale_de_la_tool_real`:
     verifica que la conversación `-1` mencione una simulación de
     financiamiento calculada por la tool real
     (`bot.business.ventas._simular_financiamiento_impl`), con cifras
     exactas de una Tucson. La `-1` reescrita ya no habla de autos.
   - `ChatsProyeccionTest.test_tres_conversaciones_tienen_dialogo_completo`:
     exige al menos 3 conversaciones con 12+ mensajes. Las 11 diálogos B2B
     nuevos son más cortos (6-11 turnos); sólo la `-10` (sin tocar, taller)
     llega a 12+.
   - `CoherenciaProyeccionTest.test_los_vehiculos_del_lead_aparecen_en_su_conversacion`:
     para cada lead con conversación, exige que el modelo de auto de
     `vehiculo_interes` aparezca literal en el texto de esa conversación.
     Falla en la primera (`Camila Fuentes` / `Creta`) porque el test corta
     al primer mismatch; probablemente fallaría en más de una si siguiera.

**Pendiente de decisión del coordinador**: qué hacer con estos 3 tests
(adaptarlos al nuevo contenido, aceptarlos como línea base ampliada a 11, u
otra opción). No se tocaron sin ese visto bueno.
