# Evaluador de Langfuse para wsp_intouch — diseño

**Fecha:** 2026-09-23 · **Estado:** diseño aprobado por secciones en conversación, spec pendiente de revisión.
**Anexo:** [`2026-09-23-tacticas-del-gerente.md`](2026-09-23-tacticas-del-gerente.md) — catálogo de las 38 tácticas y 12 escenarios destilados de las pruebas reales del gerente comercial.

---

## 1. Qué se quiere lograr

Dos usos, con los mismos criterios:

1. **Vigilar producción.** Cada turno real de `wsp_intouch` queda puntuado en Langfuse contra las reglas que más duelen comercialmente (inventar un dato, ignorar el contexto, afirmar un registro o una agenda que no existe, forma de WhatsApp). Sirve para enterarse de lo que pasa y ver la tendencia por versión de prompt.
2. **Filtrar antes de publicar.** Un **gerente simulado** —un LLM que prueba como prueba el gerente comercial— corre conversaciones completas contra el grafo real con el prompt o el modelo candidato, y un juez de conversación entrega el mismo cuadro de hallazgos que entrega el gerente (problema, evidencia, severidad P0–P3). Si el candidato introduce P0 que el activo no tiene, no pasa.

**El gerente simulado no reemplaza la prueba del gerente: la antecede.** Atrapa las regresiones conocidas —todo lo que ya encontró en sus 8 sesiones— para que él use su tiempo en buscar problemas nuevos. Cada hallazgo nuevo suyo se agrega como táctica o escenario.

**Éxito se ve así:**
- Todo turno de producción tiene sus scores en Langfuse, con el razonamiento del juez cuando marca una falla.
- El juez de conversación, corrido sobre las sesiones reales del gerente en InTouch (15-09 y 23-09), encuentra los P0/P1 que él anotó en sus cuadros (métrica: recall contra el humano, §6.6).
- `correr_gerente_simulado --candidato …` responde "pasa / no pasa" con el diff de hallazgos contra el activo.

### Decisiones ya tomadas

| Decisión | Elección | Por qué |
|---|---|---|
| Uso | Producción **y** experimentos | Los mismos criterios sirven para las dos cosas |
| Contexto del juez | **A**: historial, lead y fuentes escritos en la observación raíz `whatsapp-turn`; evaluadores nativos de Langfuse | Único camino donde producción y experimentos usan literalmente el mismo evaluador; lo recomienda Langfuse para v4 |
| Criterios por turno | `inventa_dato`, `ignora_contexto`, `afirma_registro` (LLM) + forma de WhatsApp (código) | Las reglas del prompt que más se rompen |
| Modelo del juez | `google/gemini-3.8-flash` vía OpenRouter, 100 % de los turnos | Elegido por el usuario; ~US$0,012/turno con 3 jueces |
| Dataset de experimentos | Escenarios del gerente + turnos reales marcados | Crece con los errores reales |
| Gerente simulado | Filtro previo a su prueba; compuerta "0 P0 nuevos"; 3 repeticiones por escenario | Aprobado |

### Hechos del entorno que condicionan el diseño

- **Langfuse self-hosted 4.36.0 en modo `events_only`** (`langfuse.in-touchcrm.cl`, proyecto `bot atencion pagina intouch`). En ese modo **los evaluadores a nivel de traza no producen resultados**: sólo sirven los de **observación**. `GET /api/public/traces` responde que no está disponible; se usa `/api/public/v2/observations`.
- **Los evaluadores de código de Langfuse** en self-hosted exigen un dispatcher (`LANGFUSE_CODE_EVAL_DISPATCHER`: AWS Lambda o `insecure-local`). No lo hay, y no se agrega: los scores deterministas se calculan en el bot y se suben con el SDK.
- **El proyecto no tiene LLM Connection.** Se crea con OpenRouter como adaptador OpenAI (`base URL https://openrouter.ai/api/v1`); Langfuse exige que el gateway soporte tool calling, y `gemini-3.8-flash` lo soporta (verificado contra el catálogo de OpenRouter).
- **Hoy la raíz `whatsapp-turn` sólo trae** `input={wa_id, text}` y `output={response_text, active_agent}` (`bot/whatsapp/handlers.py`), **sin historial**: un juez sobre eso no puede ver repreguntas ni despedidas duplicadas. Y el camino de error sale sin `output`.
- **No existe ningún score en Langfuse** hoy (`create_score` no aparece en `bot/`). El simulador y `rag_eval` guardan resultados en la BD y el comentario del runner lo dice: "Ya NO reporta scores a Langfuse".

---

## 2. Arquitectura

```
                    PRODUCCIÓN                                   EXPERIMENTOS
                                                                                     
 WhatsApp ─▶ handlers._run_graph                     correr_gerente_simulado --candidato X
               │                                        │  (escenarios de EscenarioDePrueba
               ▼                                        │   → dataset Langfuse "gerente-simulado")
       armar_registro_turno()  ◀── un solo dueño ──▶    ▼
       (bot/evaluacion/registro.py)              simulador (openevals) × grafo real
               │                                        │  activo y candidato, 3 repeticiones
               ├─▶ observación raíz whatsapp-turn       ├─▶ cada turno: el mismo registro
               │   input: mensaje, historial,           │   (entorno "experimento")
               │          lead, fuentes                 │
               │   output: respuesta                    ▼
               │   metadata: version_prompt, modelo  juez de conversación (SDK, nuestro código)
               │                                        │  → cuadro de hallazgos P0–P3
               ├─▶ scores de forma (código, SDK)        │  → scores de sesión + reporte .md
               ▼                                        ▼
   Regla de evaluación Langfuse ──▶ 3 jueces LLM   diff activo vs candidato ─▶ pasa / no pasa
   (gemini-3.8-flash vía OpenRouter)
```

La pieza que une los dos mundos es `armar_registro_turno`: un turno de producción y un turno simulado producen **la misma observación raíz**, así que los mismos jueces por turno puntúan los dos, y un turno real marcado se convierte en item de dataset sin transformación.

**El rubric vive en git.** Los jueces son archivos del repo; un comando los publica en Langfuse por API, y `doctor` falla si lo publicado difiere de git.

---

## 3. Sección 1 — El contrato de la observación raíz `whatsapp-turn`

```python
input = {
    "mensaje":   "<texto del usuario en este turno>",
    "historial": [{"rol": "usuario" | "bot", "texto": "..."}, ...],  # la ventana que ve el LLM (build_context_window)
    "lead": {
        "conocidos": {...},   # flow_data: nombre, empresa, correo, industria, necesidad, preferencias
        "faltantes": [...],   # antecedentes_que_faltan(conv)
    },
    "fuentes": [              # result["tool_messages"] del turno: RAG, catálogo, tools
        {"tipo": "rag" | "tool", "nombre": "...", "contenido": "<truncado>"},
    ],
}
output   = {"respuesta": "<lo que se envió>", "agente": "comercial"}
metadata = {"version_prompt": <id de PromptVersion activo>, "modelo": "<OPENROUTER_MODEL>", "error": False}
```

- **Todo sale de datos que `_run_graph` ya tiene**: `messages`, `conv.get_flow()`, `lead_faltante`, `result["tool_messages"]`. No se calcula nada nuevo ni se agrega una consulta.
- **`fuentes`** es lo que hace justo al juez de `inventa_dato`: sin ella, cada precio del catálogo parecería inventado. Cada `contenido` se trunca a un tope fijo (constante en `registro.py`, del orden de 1.500 caracteres) para que la traza no crezca sin techo.
- **`version_prompt` y `modelo`** permiten cortar cada score por versión: "el bot está mejor" pasa a ser un número.
- **Camino de error** (`GraphRecursionError` o excepción): escribe `output={"respuesta": <fallback>}` y `metadata.error = True`. Hoy esos turnos quedan sin output; así se ven, y la regla de evaluación los excluye por filtro.
- **Un solo dueño del contrato:** `bot/evaluacion/registro.py::armar_registro_turno(...)`, función pura con tests. `handlers.py` sólo la llama. Un test de contrato falla si cambia la forma, y otro verifica que `medir_latencia` sigue leyendo la jerarquía (cambiar un registro central rompe en silencio: memoria del proyecto, 5 ocurrencias medidas).
- **Nombres fijos, sin variables** (§V.3 de la biblia): la raíz sigue siendo `whatsapp-turn`.

---

## 4. Sección 2 — Jueces por turno (producción y experimentos)

### 4.1 Tres jueces LLM, booleanos, `true` = incumplimiento

| Score | Marca `true` cuando… | **No** cuenta como falla | Variables |
|---|---|---|---|
| `inventa_dato` | afirma un precio, plazo, cliente, cifra, certificación, integración, disponibilidad o capacidad de InTouch que no está en `fuentes` ni en `historial` | la frase de escape; algo presentado "sujeto a evaluación"; hablar en general sin datos concretos | `mensaje`, `historial`, `fuentes`, `respuesta` |
| `ignora_contexto` | pide un dato que ya está en `lead.conocidos` o en el historial; repite saludo o despedida; contradice lo que dijo antes; inventa un antecedente que no está en el historial ("tu campaña anterior", 23-09) | confirmar una vez un dato ambiguo; pedir el subtipo si la industria es automotriz | `historial`, `lead`, `mensaje`, `respuesta` |
| `afirma_registro` | dice que algo "quedó registrado / agendado / notificado", o trata una preferencia horaria como cita | "un especialista te va a contactar"; la frase fija de `crear_caso` | `mensaje`, `respuesta` |

Cada juez devuelve también su razonamiento, que Langfuse guarda con el score. Los prompts se escriben **en español correcto, con tildes** —los lee un LLM y el modelo imita su corpus.

### 4.2 Scores de forma, calculados en el bot (costo cero)

Cinco booleanos, `true` = falla, sobre la misma observación raíz vía `create_score`: `largo_excedido` (más de 6 líneas), `preguntas_multiples`, `pregunta_no_al_final`, `voseo`, `sin_tildes` (heurística sobre palabras frecuentes que deben llevar tilde, no un corrector). Viven en `bot/evaluacion/forma.py`, con tests hechos de respuestas reales del corpus. Son cálculo local; el SDK los envía en segundo plano, así que no suman latencia al turno.

### 4.3 Cuándo corren

Una regla de evaluación sobre observaciones con `name = whatsapp-turn`, `isRootObservation = true`, `metadata.error != true`, muestreo 100 %, en el entorno de producción del bot **y** en `experimento`. La suite y el simulador en modo test no mandan trazas al proyecto (biblia §VI.3, trampa 2: `docker exec` contamina).

### 4.4 El rubric en git y el drift detectado por el entorno

- `bot/evaluacion/jueces/<score>.md`: el prompt de cada juez, con su definición de score.
- `manage.py sincronizar_evaluadores`: crea o actualiza por API pública, idempotente y por nombre, la **LLM Connection** (OpenRouter, `gemini-3.8-flash`), los **evaluadores**, la **regla** y el **dataset** `gerente-simulado`.
- `doctor` suma `chequear_evaluadores_langfuse`: compara lo publicado con git y **falla** si alguien editó un juez en la UI. Es un chequeo con red (entra en `CHEQUEOS_CON_RED`).
- **La API key de OpenRouter del juez es propia, con tope de gasto**, distinta de la del bot: un juez desbocado no deja sin saldo a producción y su costo se ve aparte. Va en `.env.docker` como `LANGFUSE_JUEZ_OPENROUTER_API_KEY` y sólo la lee `sincronizar_evaluadores`.

### 4.5 Calibración antes de encender la regla

Cada juez corre sobre ~20 turnos reales que el usuario etiqueta a mano (un rato), sacados de las sesiones 15-09 y 23-09 del gerente y del tráfico real. La regla se enciende cuando el juez coincide con el humano en la gran mayoría; si no, se ajusta el rubric. Un juez sin calibrar cría lobos y deja de leerse.

---

## 5. Sección 3 — El gerente simulado

### 5.1 El corpus del que sale

Las pruebas manuales del gerente comercial, rescatadas el 2026-09-23 (lectura de producción autorizada) en `~/backup_conversaciones/felipe_2026-09-23/`, **fuera de git porque tienen datos personales**:

| Sesión | Bot | Fuente | Cuadro |
|---|---|---|---|
| 24-08 | demo (Renault) | ninguna — perdida | ✅ 9 hallazgos |
| 26-08 (dos pruebas) | demo (Renault) | BD `botdemo` + Langfuse US | ✅ 26-08 y 26-08V2 |
| 31-08 | demo (Astara) | BD `botdemo` + Langfuse US | ✅ |
| 02-09 | cavem | Langfuse US + respaldo del 03-09 | ✅ |
| 03-09 | cavem (demo en vivo) | **sólo Langfuse US** | ✅ |
| 15-09 | intouch | **sólo Langfuse US** (borrada de la BD) | ✅ 10 hallazgos |
| 23-09 | intouch | BD `intouch` | ✅ 6 hallazgos (planilla versión 5) |

Sus cuadros están en `auditoria latencia/Cuadro_análisis_test_Agente_Ventas_FB (5).xlsx` (fuera de git: trae datos personales). Lo que quedó en git es el **anexo sanitizado**: 38 tácticas (T01–T38) con ejemplos literales, falla buscada, evidencia y frecuencia; la persona por vertical; el mapa táctica → hallazgo; y 12 escenarios para InTouch (E01–E12).

### 5.2 La persona

El cliente simulado recibe la persona B2B del anexo (§1): gerente o dueño de concesionario o empresa automotriz, que arranca con saludo corto y necesidad difusa, entrega datos por goteo y con typos, empieza la mitad de los mensajes con "Ok", casi sin tildes, hace due diligence de proveedor, explota el dominio propio de InTouch (Ley 21.719, consentimiento), cierra en cascada y al final deriva a meta-pruebas (identidad, arquitectura). **Imita el estilo, no sólo el objetivo**: sin eso las pruebas miden a un cliente educado que el bot nunca ve.

### 5.3 Dónde vive cada cosa

- **Catálogo de tácticas y persona → git**, `bot/evaluacion/gerente/tacticas.md` (el anexo, en su forma de uso). Lo lee el prompt del cliente simulado; va en git porque es código de comportamiento.
- **Escenarios → `EscenarioDePrueba`** (BD), que es el patrón que ya existe: el panel (`TestScenariosPanel`) los edita y los corre, y las migraciones los siembran (0003/0004 ya traen los de InTouch). Los 12 escenarios del gerente entran por una migración nueva que referencia tácticas por id. El runner los copia al dataset `gerente-simulado` de Langfuse al correr.
- **Resultados → Langfuse y BD.** El experimento queda en Langfuse (traces, scores de sesión, comparación de corridas). `ResultadoDeEscenario` sigue alimentando el panel con un resumen y el link a la corrida en Langfuse, así el panel no se rompe y no hay dos verdades: el detalle vive en Langfuse.

### 5.4 El motor: extender el simulador, no escribir otro

`bot/simulator` ya corre conversaciones multi-turno con cliente simulado (`openevals.run_multiturn_simulation`) contra el grafo real (`app_wrapper`) y prueba candidatos sin publicar (`prompt_override`). Se extiende:

- `simulated_user.py` recibe persona + tácticas del escenario y el estilo del catálogo.
- **Medios:** las imágenes y audios le llegan al bot como texto de percepción. El simulador los emite en ese formato (las tácticas T12/T13), con una nota en el reporte de que no prueban la percepción en sí.
- **Deuda del vertical que se paga acá:** los jueces de `simulator/judge.py` y `rag_eval/judge.py` todavía dicen "concesionaria", y los evaluadores de código son automotrices (`simular_financiamiento`, `buscar_reserva`, imagen). Se reescriben para InTouch; los automotrices se sacan del camino de InTouch.
- **Cada turno simulado pasa por `armar_registro_turno`** en el entorno `experimento`: los jueces por turno de §4 lo puntúan igual que a producción.

### 5.5 El juez de conversación completa

Corre en nuestro código como evaluador de ítem de `langfuse.run_experiment` (Langfuse no evalúa sesiones de forma nativa). Recibe la transcripción, las fuentes de cada turno y el estado final del lead. Devuelve **el cuadro del gerente**: lista de `{problema, evidencia (cita textual), severidad P0–P3, categoría}`. Se publica como scores de sesión:

- `hallazgos_p0`, `hallazgos_p1` (conteos).
- Un booleano por categoría, derivadas de sus cuadros: `dato_inconsistente`, `cierre_duplicado`, `acepta_premisa_falsa`, `fuga_de_arquitectura`, `promete_accion_o_horario`, `identidad_inventada` (el "Matías" del 23-09), `frase_de_escape_repetida`, `no_sigue_pivote`, `consentimiento_tardio`, `ayuda_a_reclamar_contra_intouch`.
- `lead_completo`: capturó nombre, empresa, correo y necesidad sin insistir.

Las columnas "causa raíz" y "dónde se soluciona" del gerente quedan fuera: exigen conocer el sistema.

### 5.6 El comando del filtro previo

`manage.py correr_gerente_simulado --candidato <prompt|modelo> [--escenario E05] [--repeticiones 3]`

1. Corre el dataset con el **activo** y con el **candidato**, cada escenario **3 veces** (el cliente simulado varía; un solo resultado es ruido).
2. Imprime el diff en el formato del gerente: "el candidato introduce estos P0 que el activo no tiene", y deja un `.md` con el cuadro para que el gerente lo lea antes de su prueba.
3. **Compuerta:** P0 nuevos respecto del activo → no pasa (código de salida ≠ 0).
4. **Sólo bajo pedido** (memoria del proyecto: el simulador nunca corre como paso automático). Costo estimado del orden de US$1–2 por corrida completa con los modelos actuales. Engancharlo al runner de GitHub Actions queda para después (biblia §VI.4).

### 5.7 Calibración contra el gerente real

Las sesiones de InTouch del 15-09 (10 hallazgos) y del 23-09 (6 hallazgos) se pasan al juez de conversación. **Métrica:** qué fracción de los P0/P1 de su cuadro encuentra el juez (recall contra el humano), y cuántos hallazgos nuevos inventa (se revisan a mano: algunos serán reales, como las 5 respuestas con "no queda agendado" del 23-09 que la planilla no anotó). El juez se usa como compuerta recién cuando encuentra todos los P0 de esas dos sesiones.

---

## 6. Componentes y archivos

| Archivo | Qué hace |
|---|---|
| `bot/evaluacion/registro.py` | `armar_registro_turno`: el contrato de la raíz (función pura) |
| `bot/evaluacion/forma.py` | Los 5 scores de forma |
| `bot/evaluacion/jueces/*.md` | Prompts de los 3 jueces por turno y del juez de conversación |
| `bot/evaluacion/gerente/tacticas.md` | Persona y catálogo de tácticas |
| `bot/evaluacion/langfuse_api.py` | Cliente de la API pública: LLM Connection, evaluadores, reglas, datasets |
| `bot/management/commands/sincronizar_evaluadores.py` | Publica git → Langfuse, idempotente |
| `bot/management/commands/correr_gerente_simulado.py` | El filtro previo |
| `bot/simulator/*` | Extendido (persona, tácticas, registro por turno, juez de conversación, deuda del vertical) |
| `bot/migrations/00xx_escenarios_gerente.py` | Siembra E01–E12 en `EscenarioDePrueba` |
| `bot/whatsapp/handlers.py` | Llama a `armar_registro_turno` y a los scores de forma; output en el camino de error |
| `bot/management/commands/doctor.py` | `chequear_evaluadores_langfuse` (drift git ↔ Langfuse) |

## 7. Testing

- `registro.py` y `forma.py`: unitarios con casos reales del corpus sanitizados; un test de contrato de la forma del registro.
- `handlers.py`: el test entra **por el camino de producción** (async), no por un atajo sync (memoria: `_flag_incident` estuvo muerta meses con su test en verde).
- `sincronizar_evaluadores`: contra un cliente de API falso; idempotencia (dos corridas = cero cambios).
- `doctor`: drift detectado, drift ausente, Langfuse inalcanzable (aviso, no falla).
- Juez de conversación: la calibración de §5.7 es su test de aceptación; se corre bajo pedido, no en la suite.
- `git status` limpio después de la suite (un test no puede borrar el repo).

## 8. Datos personales

- Las transcripciones crudas y la planilla **nunca van a git**. Lo que va a git (anexo, catálogo, escenarios, casos de test) está sanitizado: sin nombres, teléfonos, correos, RUT ni patentes.
- La traza de producción pasa a llevar el historial: son datos que el proyecto de Langfuse ya recibe turno a turno (input/output de cada `whatsapp-turn`), ahora agrupados. El proyecto es propio del bot y está en el self-hosted de la empresa.

## 9. Coordinación y riesgos

- **`handlers.py` y los prompts se tocan en paralelo** por otra sesión (hoy commiteó cambios de prompt a partir del chat del 23-09). La implementación coordina por `SendMessage` antes de tocar `handlers.py` y antes de cualquier HUP o migración; `git add` explícito siempre.
- **La migración de escenarios toca la BD de producción**: sólo con confirmación explícita del usuario, como toda migración en este contenedor.
- **Costo del juez en producción**: ~US$0,012/turno al 100 %. Si el tráfico crece, se baja el muestreo en la regla (un cambio en git + `sincronizar_evaluadores`).
- **Riesgo de un juez mal calibrado**: mitigado por §4.5 y §5.7 antes de encender la regla y la compuerta.

## 10. Fuera de alcance y pendientes

- Latencia: la sigue midiendo `medir_latencia` sobre trazas reales; el simulado no pasa por WhatsApp.
- Portar el evaluador a `wsp_cavem` (el catálogo ya trae las tácticas automotrices): después, como parte del drift entre repos (biblia §VI.1).
- CI en GitHub Actions.
- **Preguntarle al gerente**: el "indícame el proceso de implementación que solicité en mensaje anterior" del 23-09 no tiene ese mensaje previo en la BD. ¿Premisa falsa deliberada o mensaje perdido? Define si va como táctica T10 o como incidente de entrega.
- La prueba del 24-08 y la del 13–19 de agosto no están en ninguna fuente; sólo sus cuadros.
- Regla operativa nueva: antes de borrar conversaciones para "medir como número nuevo", exportar las del gerente (así se perdieron sesiones).
