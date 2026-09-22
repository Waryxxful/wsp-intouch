# Latencia de wsp_intouch — medición y corrección de la auditoría del 15-09

Fecha: 21 de septiembre de 2026
Commit base: `44b9831`
Corrige: `auditoria-latencia-wsp-intouch.md` (15-09, commit `8364a62`)

Toda cifra de acá sale de las trazas reales de Langfuse y es reproducible con:

```
manage.py medir_latencia --desde 2026-09-15
```

Cada afirmación va marcada **(obs.)** si la observé yo en esta sesión o
**(rep.)** si la reporta otra fuente y no la verifiqué.

---

## 1. La conclusión corta

**La premisa de la auditoría del 15-09 es incorrecta.** Los 7,6 s que motivaron
el trabajo no son la latencia del turno. El turno percibido es **5,94 s de
media y 3,99 s de mediana** (obs., n=56, 2026-09-15). Los 7,6 s coinciden con la
media de `extract-metadata` (**7,72 s**, obs.), que corre en la cola de envío
**después** de que el primer mensaje ya salió a Meta.

Con la medición en la mano, el orden de las palancas cambia: lo que la
auditoría puso como "Alta" por el lado del CRM cuesta hoy 0,74 s, y lo que
importa es el ruteo (arreglado, ver §4) y la ronda de tool (abierto, ver §6).

## 2. La foto

| | media | p50 | p95 | máx |
|---|---|---|---|---|
| **`whatsapp-turn`** (el turno completo) | **5,94 s** | **3,99 s** | 13,80 s | 19,32 s |
| `classify-intent` (ruteo) | 1,01 s | 0,70 s | 2,46 s | 9,06 s |
| `generate-response` | 2,10 s | 1,92 s | 3,46 s | 4,57 s |
| `run-business-action` | 2,08 s | 2,07 s | 4,64 s | 6,20 s |
| `extract-metadata` *(fuera del turno)* | 7,72 s | 7,72 s | 12,08 s | 16,50 s |

n=56 turnos, 2026-09-15, entorno `development` (obs.).

### Los tres tramos

```
CABEZA (ORM + ventana de contexto + registro)  p50 0,05 s   p95 0,07 s
LLM    (ruteo + generaciones + tools)          media 4,75 s
COLA   (lead + CRM + notificación + POST Meta) p50 0,74 s   p95 1,07 s   máx 1,85 s
```

**El turno es LLM.** Nuestro código pesa ~0,8 s, consistente con la ley de la
biblia §III.2 ("831 ms de piso son del proveedor; nuestro código < 2 ms").

### La bimodal

El corte que ordena toda la priorización es *¿llamó una tool?*:

| | n | media | p50 | máx |
|---|---|---|---|---|
| sin tool | 37 | 3,82 s | 3,53 s | 11,77 s |
| **con tool** | **19** | **10,08 s** | **9,46 s** | **19,32 s** |

Distribución de tools por turno (obs., contrastada contra el detalle de cada
traza): 37 turnos con 0, 17 con 1, 1 con 2, 1 con 3.

La tool dominante es `consultar_base_conocimiento` (RAG): 11 de 22 llamadas,
~3,3 s de media, 6,20 s de máximo (obs.). Va en medio de un sándwich de dos
generaciones completas.

## 3. Qué de la auditoría del 15-09 no se sostiene

| Hallazgo | Veredicto |
|---|---|
| #2 CRM y notificaciones antes de WhatsApp, prioridad **Alta** | **Real en el código, irrelevante para la media hoy.** `_despachar_si_corresponde` (`timeout=15`) y `notify` (`timeout=5`) sí corren antes de `send_text`. Pero la COLA completa mide 0,74 s p50 y 1,85 s de máximo (obs.). Es riesgo de cola —hasta ~20 s si el CRM se cuelga—, no una palanca de latencia. Se reclasifica a fiabilidad. |
| #5 cola serial, #7 contexto duplicado y trabajo repetido | **Ciertos en el código, coste medido ≈ 0.** La CABEZA es 0,05 s p50 (obs.). El mensaje duplicado y las consultas repetidas de settings existen, pero no se pagan en segundos. |
| #6 A/B de *streaming* y bajar `reasoning` en la llamada final | **Ya medido y descartado** (biblia §III.2, rep.). Streaming: WhatsApp no muestra tokens parciales. Bajar el razonamiento final: derivó a voseo y erratas. |
| #3 (parte) recuperación anticipada / prefetch de tool | **Ya medido y descartado** (biblia §III.2, rep.): "la hipótesis prometía 6 s; el desglose fino la desmintió". |

La causa de fondo es que esa auditoría corrió desde GitHub en otra máquina, sin
acceso a `docs-repo` ni a Langfuse — lo dice ella misma en su sección de
límites. Por eso reabre experimentos ya pagados y prioriza sin poder medir.

## 4. Qué sí acertó, y qué se hizo

### El ruteo con un solo destino — **arreglado**

`AGENTS` tiene un solo especialista (`comercial`) a propósito, y
`supervisor_node` no tenía retorno directo para un registro de uno. Verificado
(obs.) que la llamada **sólo podía devolver `"comercial"`**: su fallback de
output inválido cae en `next(iter(registry))`, y el override de
`intencion_compra_real` exige `"ventas"` en el registro, que no está.

Costo medido: **1,01 s de media, 0,70 s de mediana, en el 100 % de los turnos**
(obs.) — el 17 % de un turno, para elegir entre una opción. Y no era sólo
tiempo: es una llamada de red que puede fallar, con máximo observado de 9,06 s,
y es la misma que produjo el 403 de *prompt injection* del 02-09 (rep.,
`docs/PENDIENTES.md` #16).

Agravante que la auditoría no vio: `_AGENTE_ESPECULADO = "ventas"` tampoco está
en el registro, así que `_lanzar_especulacion` devolvía `None` siempre. **InTouch
pagaba el ruteo y además perdía la mitigación que sí tiene el linaje de cavem.**

**Hecho:** retorno directo en `supervisor_node` cuando el registro tiene un solo
especialista. El atajo se apaga solo — `build_agent_registry` se reconstruye en
cada turno, así que el primer `CustomSpecialist` creado desde el panel devuelve
la clasificación sin que nadie tenga que acordarse. Eso lo anclan, en par:

- `GraphSmokeTest::test_un_solo_especialista_registrado_no_llama_al_ruteo`
- `GraphCustomSpecialistTest::test_supervisor_rutea_a_un_especialista_personalizado`

### El banco de medición — **hecho**

`manage.py medir_latencia` (deuda §VI.3 de la biblia, prioridad ALTA, abierta
desde que tres auditorías reconstruyeron el mismo banco a mano). Congela el
método: separación por entorno, los tres tramos, la bimodal, rondas y
reintentos por turno, arranque en frío y control del proveedor.

## 5. Dos hallazgos nuevos

### 5.1 Arranque en frío: 5,9 s en el primer turno — **medido, sin causa probada**

Los dos únicos turnos con CABEZA alta (5,82 s y 5,92 s) son los dos únicos
precedidos por más de `CONN_MAX_AGE` (600 s) de silencio: 652 s y 4,8 días
(obs.). Los otros 59 midieron 0,05 s. **2 de 2, sin contraejemplo en 61 turnos.**

Con el tráfico esporádico de un bot de lead gen, esto golpea el **primer turno
de casi toda conversación** — el que decide si el contacto sigue.

La sospecha natural es el handshake a SQL Server, que este repo ya midió lento e
intermitente (`config/settings.py`, 02-09: 0,26–0,57 s normal, hasta 35,93 s
intermitente; rep.). **Pero no se pudo reproducir:** una sonda contra el
servidor actual (172.20.21.50) dio 0,01–0,04 s para abrir la conexión, seis
veces seguidas, con las queries en 0,001 s (obs., 21-09).

**Por eso no se arregló: no se arregla a ciegas lo que no se pudo reproducir.**
En su lugar se instrumentó el tramo, que era una caja negra:
`preparar-contexto`, `antecedentes-faltantes` y `resolver-especialista` son
ahora spans propios. La próxima vez que ocurra, la traza dice cuál de las tres
llamadas fue, en vez de obligar a otra auditoría.

### 5.2 `extract-metadata` a 7,72 s reteniendo el único hilo de la cola

`cola_envio::_EJECUTOR` es un `ThreadPoolExecutor(max_workers=1)`. El extractor
corre ahí con un presupuesto de 35 s y una media observada de 7,72 s. Hoy no
duele con una conversación; con varias en paralelo, las partes 2..N de todos los
demás contactos esperan detrás. Es exactamente la deuda §VI.7 de la biblia.

## 6. Lo que queda abierto, por tamaño

1. **La ronda de tool (la masa real).** 19 de 56 turnos, 10,08 s de media contra
   3,82 s. Una tool cuesta su propia ejecución **más una generación entera**.
   Necesita diseño y experimento propio; ojo con §III.2, que ya descartó el
   prefetch. No proponer nada acá sin medir contra el LLM real.
2. **Ejecutor de cola por conversación** (§5.2 / biblia §VI.7).
3. **El lead y el CRM fuera del camino de envío** — por fiabilidad y cola, no
   por la media (§3).
4. **`send_text` devuelve `False` y los llamadores lo ignoran**: un rechazo de
   Meta se guarda igual como mensaje `assistant`. Hallazgo #8 de la auditoría
   del 15-09, confirmado en el código (obs.), no tocado acá.

## 7. Una trampa de medición que costó una hora

El proyecto de Langfuse **está compartido entre bots**. De 6.423 observaciones,
6.125 eran de `wsp_cavem` (entorno `qa`) y 298 de este bot (`development`)
(obs.). Medir sin filtrar por entorno da los números de otro bot.

Peor: correr la suite con `docker exec` **dentro del contenedor** hereda las
credenciales de Langfuse y deja trazas de test en el proyecto de producción —
48 `run-business-action` de 0,01 s el 21-09, que al mezclarse duplicaban el
conteo de tools por turno (obs., autoinfligido). El comando documentado en el
`CLAUDE.md` de este repo usa `docker run` sin esas credenciales y no contamina.
`medir_latencia` ahora excluye de toda estadística las observaciones que no
cuelgan de un turno, y las informa aparte con una advertencia.
