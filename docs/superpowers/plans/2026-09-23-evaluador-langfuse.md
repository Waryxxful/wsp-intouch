# Evaluador de Langfuse y gerente simulado — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada turno de `wsp_intouch` quede puntuado en Langfuse por tres jueces LLM y cinco chequeos de forma, y que un gerente simulado filtre prompts o modelos candidatos antes de la prueba del gerente comercial.

**Architecture:** Una función pura (`armar_registro_turno`) define qué se escribe en la observación raíz `whatsapp-turn`; producción y simulador pasan por el mismo `handlers._run_graph`, así que los mismos jueces nativos de Langfuse puntúan los dos. El rubric vive en `bot/evaluacion/jueces.py` y se publica por la API pública de Langfuse con `sincronizar_evaluadores`; `doctor` falla si lo publicado difiere de git. El gerente simulado extiende `bot/simulator` (persona y tácticas del corpus real) y un juez de conversación propio corre como evaluador de `run_experiment`.

**Tech Stack:** Django 5 · LangGraph/LangChain · `langfuse` 4.14.5 (SDK) · Langfuse self-hosted 4.36.0 (`events_only`) · `langchain-openrouter` · `openevals` · `httpx` · `openpyxl`.

**Spec:** `docs/superpowers/specs/2026-09-23-evaluador-langfuse-design.md` · **Anexo:** `docs/superpowers/specs/2026-09-23-tacticas-del-gerente.md`

## Global Constraints

- Todo texto que lea un LLM (prompts de jueces, persona, tácticas, criterios, docstrings de tools) va en **español correcto, con tildes**, tuteo chileno sin voseo.
- Nombres de observación fijos y sin variables: la raíz sigue siendo `whatsapp-turn`.
- Scores booleanos con polaridad **`true` (1.0) = incumplimiento** en los jueces nuevos y en los de forma. (El juez viejo del simulador usa la polaridad contraria; no se mezclan: sus criterios no se publican en Langfuse.)
- Juez: `google/gemini-3.8-flash` vía OpenRouter, muestreo 100 %, con **API key propia** `OPENROUTER_JUEZ_API_KEY` (distinta de `OPENROUTER_API_KEY`).
- Registrar el turno **nunca** rompe el turno: todo en `try/except` con `logger.exception`.
- Nada de datos personales en git: transcripciones y planilla del gerente viven en `~/backup_conversaciones/felipe_2026-09-23/` y `auditoria latencia/`, fuera del repo.
- Tests con el comando del `CLAUDE.md`, nunca con `docker exec` (contamina Langfuse). En este plan, `$TEST` es:
  `docker run --rm -v $PWD:/app -w /app --user 1000:1000 -e USE_SQLITE=true -e HOME=/tmp -e DJANGO_SETTINGS_MODULE=config.settings -e CLIENTE_ACTIVO=renault -e OPENROUTER_API_KEY=dummy --entrypoint python wsp_intouch-web manage.py test`
- `git add` explícito por archivo, nunca `-a`/`-A`. `git status` antes de cualquier reload. `SendMessage` a las sesiones activas antes de tocar `bot/whatsapp/handlers.py`, de un HUP o de una migración.
- Migraciones contra la BD real y `sincronizar_evaluadores` contra Langfuse: **sólo con confirmación explícita del usuario** en el momento.
- El gerente simulado corre **sólo bajo pedido**, nunca como paso automático.

### Desvíos respecto de la spec (descubiertos al escribir el plan)

1. **No hay entorno `experimento`.** `LANGFUSE_TRACING_ENVIRONMENT` es por proceso y el simulador corre en el mismo contenedor que producción. Los turnos simulados (wa_id con prefijo `TEST`) llevan el tag `simulacion` y `metadata.origen = "simulacion"`; los tableros de producción filtran por ese tag.
2. **La regla no filtra `isRootObservation`.** Dentro de `run_experiment`, `whatsapp-turn` queda anidada bajo la traza del ítem y deja de ser raíz. La regla filtra por `name = whatsapp-turn` (nombre único por turno) y `metadata.error = "no"`.
3. **`fuentes` incluye la ficha del turno.** Desde el 2026-09-22 el catálogo, el horario, el texto legal y los contactos de InTouch llegan por `bloque_contexto_turno` en el prompt, no por tools. Sin eso, el juez de `inventa_dato` marcaría como inventado el teléfono que el bot sí tiene.
4. **Los jueces viven en `bot/evaluacion/jueces.py`** (dataclasses) en vez de `jueces/*.md`: mismo efecto (en git, revisable) sin un parser propio.
5. **Sin `run_experiment` ni dataset de Langfuse para el gerente simulado.** `correr_escenario` usa `async_to_sync` y no convive con el event loop que levanta `run_experiment`; el loop es propio y secuencial (como el runner actual). Cada repetición es una traza `gerente-simulado` con `metadata.variante` (activo/candidato) y los scores del juez de conversación en esa traza, filtrables y comparables en Langfuse. El dataset `turnos-reales` se arma desde la UI de Langfuse («Add to dataset» sobre un `whatsapp-turn`), sin código.

## Review Focus

- **Langfuse caído o lento durante un turno real** → el turno responde igual y sólo se pierde el registro (Task 3 lo testea con `update_current_span` que lanza).
- **Turno por el camino de error** (`GraphRecursionError` / excepción) → queda registrado con `metadata.error = "si"` y sin scores de forma (Task 3).
- **Alguien edita un juez en la UI de Langfuse** → `doctor` falla nombrando el juez (Task 7).
- **Candidato que empeora sólo P1** → el comando no bloquea pero los lista en el reporte (Task 12 lo testea).
- **Correr el gerente simulado sin `OPENROUTER_JUEZ_API_KEY`** → falla al arrancar con un mensaje claro, no a mitad de corrida gastando plata (Task 12).

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `bot/evaluacion/__init__.py` | Paquete |
| `bot/evaluacion/registro.py` | Contrato de la raíz `whatsapp-turn` (función pura) + helpers de versión de prompt y origen |
| `bot/evaluacion/forma.py` | Los 5 scores de forma |
| `bot/evaluacion/jueces.py` | Definición de los 3 jueces por turno |
| `bot/evaluacion/langfuse_api.py` | Cliente mínimo de la API pública (LLM connections, evaluadores, reglas) |
| `bot/evaluacion/sincronizar.py` | Lógica git → Langfuse (idempotente, con modo sólo-mostrar) |
| `bot/evaluacion/gerente/tacticas.md` | Persona y catálogo de tácticas (copia de uso del anexo) |
| `bot/evaluacion/gerente/catalogo.py` | Lee `tacticas.md` y arma el bloque de tácticas de un escenario |
| `bot/evaluacion/juez_conversacion.py` | Juez de conversación completa: prompt, parseo, `Evaluation`s, diff |
| `bot/management/commands/sincronizar_evaluadores.py` | CLI de `sincronizar.py` |
| `bot/management/commands/correr_gerente_simulado.py` | El filtro previo |
| `bot/management/commands/calibrar_juez_conversacion.py` | Recall del juez contra los cuadros del gerente |
| `bot/whatsapp/handlers.py` | Llama a `_registrar_turno` al final de `_run_graph` y en los caminos de error; tag `simulacion` |
| `bot/management/commands/doctor.py` | `chequear_evaluadores_langfuse` |
| `bot/simulator/{judge.py,code_evaluators.py,simulated_user.py,runner.py,models.py}` | Deuda de vertical, persona con tácticas, campo `tacticas` |
| `bot/simulator/migrations/0005_escenario_tacticas.py`, `0006_escenarios_gerente.py` | Campo nuevo y siembra E01–E12 |
| `bot/rag_eval/judge.py` | Deuda de vertical |
| `admin_panel/views.py`, `frontend/src/panels/TestScenariosPanel.tsx` | Serializar y editar `tacticas` |
| `config/settings.py`, `.env.docker.example` | `OPENROUTER_JUEZ_API_KEY`, `JUEZ_MODELO` |
| `bot/tests/test_evaluacion_*.py` | Tests nuevos |

---

### Task 1: Contrato de la observación raíz (`registro.py`)

**Files:**
- Create: `bot/evaluacion/__init__.py`, `bot/evaluacion/registro.py`
- Test: `bot/tests/test_evaluacion_registro.py`

**Interfaces:**
- Produces:
  - `armar_registro_turno(*, mensaje: str, messages: list[dict], flow_data: dict, lead_faltante: list, tool_messages: list, contexto_turno: str, respuestas_enviadas: list[str], agente: str, version_prompt: str, modelo: str, error: bool = False, origen: str = "produccion") -> dict` con claves `input`, `output`, `metadata`.
  - `origen_de(wa_id: str) -> str` → `"simulacion"` si empieza con `TEST`, si no `"produccion"`.
  - `version_de_prompt(agente: str) -> str` (sync, ORM) → `"comercial:12,global:4"` o `""`.
  - `modelo_efectivo() -> str` (sync) → override del panel o `settings.OPENROUTER_MODEL`.
  - Constantes `TOPE_CONTENIDO_FUENTE = 1500`, `TOPE_CONTEXTO_TURNO = 4000`, `NOMBRES_RAG = {"consultar_base_conocimiento"}`.

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_evaluacion_registro.py
from django.test import TestCase
from langchain.messages import AIMessage, ToolMessage

from bot.evaluacion.registro import (
    TOPE_CONTENIDO_FUENTE, armar_registro_turno, origen_de, version_de_prompt,
)
from bot.models import save_prompt_version


def _registro(**cambios):
    base = dict(
        mensaje="¿Cuánto cuesta?",
        messages=[
            {"role": "system", "content": "(el historial sigue más atrás)"},
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Hola! ¿En qué te ayudo?"},
            {"role": "user", "content": "¿Cuánto cuesta?"},
        ],
        flow_data={"empresa": "Empresa Demo", "correo": "", "industria": "automotriz"},
        lead_faltante=["tu correo"],
        tool_messages=[
            AIMessage(content="", tool_calls=[{"id": "t1", "name": "consultar_base_conocimiento", "args": {"query": "precio"}}]),
            ToolMessage(content="x" * (TOPE_CONTENIDO_FUENTE + 50), tool_call_id="t1", name="consultar_base_conocimiento"),
            ToolMessage(content='{"ok": true}', tool_call_id="t2", name="crear_caso"),
        ],
        contexto_turno="Teléfono de InTouch: ...",
        respuestas_enviadas=["Primera parte.", "Segunda parte."],
        agente="comercial", version_prompt="comercial:3", modelo="m/x",
    )
    base.update(cambios)
    return armar_registro_turno(**base)


class ArmarRegistroTurnoTest(TestCase):
    def test_forma_del_contrato(self):
        r = _registro()
        self.assertEqual(set(r), {"input", "output", "metadata"})
        self.assertEqual(set(r["input"]), {"mensaje", "historial", "lead", "fuentes"})
        self.assertEqual(set(r["output"]), {"respuesta", "agente"})
        self.assertEqual(set(r["metadata"]), {"version_prompt", "modelo", "error", "origen"})

    def test_historial_sin_mensajes_de_sistema_y_con_roles_en_espanol(self):
        historial = _registro()["input"]["historial"]
        self.assertEqual([h["rol"] for h in historial], ["usuario", "bot", "usuario"])

    def test_lead_descarta_valores_vacios(self):
        lead = _registro()["input"]["lead"]
        self.assertEqual(lead["conocidos"], {"empresa": "Empresa Demo", "industria": "automotriz"})
        self.assertEqual(lead["faltantes"], ["tu correo"])

    def test_fuentes_traen_ficha_rag_y_tools_recortadas(self):
        fuentes = _registro()["input"]["fuentes"]
        self.assertEqual([f["tipo"] for f in fuentes], ["contexto_turno", "rag", "tool"])
        self.assertEqual(len(fuentes[1]["contenido"]), TOPE_CONTENIDO_FUENTE + 1)
        self.assertTrue(fuentes[1]["contenido"].endswith("…"))

    def test_sin_ficha_no_agrega_fuente_vacia(self):
        fuentes = _registro(contexto_turno="  ")["input"]["fuentes"]
        self.assertNotIn("contexto_turno", [f["tipo"] for f in fuentes])

    def test_respuesta_es_lo_que_se_envio(self):
        self.assertEqual(_registro()["output"]["respuesta"], "Primera parte.\n\nSegunda parte.")

    def test_error_y_origen_son_strings_filtrables(self):
        r = _registro(error=True, origen="simulacion")
        self.assertEqual(r["metadata"]["error"], "si")
        self.assertEqual(r["metadata"]["origen"], "simulacion")
        self.assertEqual(_registro()["metadata"]["error"], "no")

    def test_origen_de(self):
        self.assertEqual(origen_de("TEST000123"), "simulacion")
        self.assertEqual(origen_de("56911112222"), "produccion")


class VersionDePromptTest(TestCase):
    def test_lista_agente_y_global_activos(self):
        v1 = save_prompt_version("comercial", "p1")
        g = save_prompt_version("global", "g1")
        self.assertEqual(version_de_prompt("comercial"), f"comercial:{v1.pk},global:{g.pk}")

    def test_sin_versiones_devuelve_vacio(self):
        self.assertEqual(version_de_prompt("comercial"), "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_registro`
Expected: FAIL con `ModuleNotFoundError: No module named 'bot.evaluacion'`

- [ ] **Step 3: Write minimal implementation**

```python
# bot/evaluacion/__init__.py
```

```python
# bot/evaluacion/registro.py
"""Contrato de la observación raíz `whatsapp-turn`.

Es lo que leen los jueces de Langfuse (bot/evaluacion/jueces.py mapea sus
variables a estas claves) y la forma que toma un ítem de dataset creado desde
un turno real. Producción y simulador pasan por acá (los dos entran por
handlers._run_graph), así que un mismo juez puntúa los dos.

Un solo dueño a propósito: cambiar esta forma sin tocar jueces.py rompe el
test de contrato de bot/tests/test_evaluacion_jueces.py.
"""
from langchain.messages import ToolMessage

TOPE_CONTENIDO_FUENTE = 1500
TOPE_CONTEXTO_TURNO = 4000
NOMBRES_RAG = {"consultar_base_conocimiento"}
_ROL = {"user": "usuario", "assistant": "bot"}
PREFIJO_WA_ID_SIMULACION = "TEST"


def _recortar(texto, tope: int) -> str:
    texto = texto if isinstance(texto, str) else str(texto)
    return texto if len(texto) <= tope else texto[:tope] + "…"


def _historial(messages: list) -> list[dict]:
    # El marcador de historial truncado es role=system: no es de nadie.
    return [
        {"rol": _ROL[m.get("role")], "texto": m.get("content") or ""}
        for m in messages or []
        if isinstance(m, dict) and m.get("role") in _ROL
    ]


def _fuentes(tool_messages: list, contexto_turno: str) -> list[dict]:
    fuentes = []
    if (contexto_turno or "").strip():
        fuentes.append({
            "tipo": "contexto_turno", "nombre": "ficha del turno",
            "contenido": _recortar(contexto_turno, TOPE_CONTEXTO_TURNO),
        })
    for m in tool_messages or []:
        if isinstance(m, ToolMessage):
            nombre = m.name or ""
            fuentes.append({
                "tipo": "rag" if nombre in NOMBRES_RAG else "tool",
                "nombre": nombre,
                "contenido": _recortar(m.content, TOPE_CONTENIDO_FUENTE),
            })
    return fuentes


def armar_registro_turno(*, mensaje, messages, flow_data, lead_faltante, tool_messages,
                         contexto_turno, respuestas_enviadas, agente, version_prompt,
                         modelo, error=False, origen="produccion") -> dict:
    conocidos = {k: v for k, v in (flow_data or {}).items() if v not in (None, "", [], {})}
    return {
        "input": {
            "mensaje": mensaje or "",
            "historial": _historial(messages),
            "lead": {"conocidos": conocidos, "faltantes": list(lead_faltante or [])},
            "fuentes": _fuentes(tool_messages, contexto_turno),
        },
        "output": {"respuesta": "\n\n".join(respuestas_enviadas or []), "agente": agente or ""},
        "metadata": {
            "version_prompt": version_prompt or "", "modelo": modelo or "",
            "error": "si" if error else "no", "origen": origen,
        },
    }


def origen_de(wa_id: str) -> str:
    return "simulacion" if str(wa_id or "").startswith(PREFIJO_WA_ID_SIMULACION) else "produccion"


def version_de_prompt(agente: str) -> str:
    """Sync (ORM). Qué PromptVersion activas armaron este turno."""
    from bot.flow.global_prompt import GLOBAL_PROMPT_SLUG
    from bot.models import PromptVersion

    filas = PromptVersion.objects.filter(
        agente__in=[a for a in (agente, GLOBAL_PROMPT_SLUG) if a], activa=True,
    ).values_list("agente", "id")
    return ",".join(f"{a}:{i}" for a, i in sorted(filas))


def modelo_efectivo() -> str:
    """Sync (ORM). Misma resolución que graph._get_llm: override del panel o env."""
    from django.conf import settings

    from bot.models import get_setting

    return get_setting("openrouter_model_override", "") or settings.OPENROUTER_MODEL
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$TEST bot.tests.test_evaluacion_registro`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/evaluacion/__init__.py bot/evaluacion/registro.py bot/tests/test_evaluacion_registro.py
git commit -m "feat(evaluacion): contrato de la observación raíz whatsapp-turn"
```

---

### Task 2: Scores de forma (`forma.py`)

**Files:**
- Create: `bot/evaluacion/forma.py`
- Test: `bot/tests/test_evaluacion_forma.py`

**Interfaces:**
- Produces: `scores_de_forma(partes: list[str]) -> dict[str, bool]` con claves exactas `largo_excedido`, `preguntas_multiples`, `pregunta_no_al_final`, `voseo`, `sin_tildes` (`True` = falla). `NOMBRES_FORMA: tuple[str, ...]` con esas cinco claves en ese orden.

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_evaluacion_forma.py
from django.test import SimpleTestCase

from bot.evaluacion.forma import NOMBRES_FORMA, scores_de_forma

# Respuestas reales del corpus del gerente (23-09), sin datos personales.
BUENA = ("Perfecto, es un volumen claro: 2000 para renovación y 3000 para fidelización. "
         "¿Me cuentas por qué canal prefieren llegar a tus clientes?")
DESPEDIDA = "Fue un gusto. Quedamos en que el ejecutivo te contacta hoy a las 17:00. ¡Que estés bien!"


class ScoresDeFormaTest(SimpleTestCase):
    def test_devuelve_las_cinco_claves(self):
        self.assertEqual(tuple(scores_de_forma([BUENA])), NOMBRES_FORMA)

    def test_respuesta_buena_no_falla_nada(self):
        self.assertFalse(any(scores_de_forma([BUENA]).values()))

    def test_sin_pregunta_no_es_falla_de_pregunta(self):
        s = scores_de_forma([DESPEDIDA])
        self.assertFalse(s["pregunta_no_al_final"])
        self.assertFalse(s["preguntas_multiples"])

    def test_mas_de_dos_mensajes_es_largo_excedido(self):
        self.assertTrue(scores_de_forma(["a", "b", "c"])["largo_excedido"])

    def test_mensaje_de_siete_lineas_es_largo_excedido(self):
        self.assertTrue(scores_de_forma(["\n".join(["línea"] * 7)])["largo_excedido"])

    def test_dos_preguntas(self):
        self.assertTrue(scores_de_forma(["¿Qué volumen tienes? ¿Y qué canal usan?"])["preguntas_multiples"])

    def test_pregunta_en_medio(self):
        self.assertTrue(scores_de_forma(["¿Te acomoda mañana? Te escribe un especialista."])["pregunta_no_al_final"])

    def test_emoji_despues_de_la_pregunta_no_cuenta(self):
        self.assertFalse(scores_de_forma(["¿Te parece? 😊"])["pregunta_no_al_final"])

    def test_voseo(self):
        self.assertTrue(scores_de_forma(["Si querés, contame más"])["voseo"])
        self.assertFalse(scores_de_forma(["Si quieres, cuéntame más"])["voseo"])

    def test_sin_tildes(self):
        self.assertTrue(scores_de_forma(["Te envio la informacion tambien"])["sin_tildes"])
        self.assertFalse(scores_de_forma(["Te envío la información también"])["sin_tildes"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_forma`
Expected: FAIL con `ModuleNotFoundError: No module named 'bot.evaluacion.forma'`

- [ ] **Step 3: Write minimal implementation**

```python
# bot/evaluacion/forma.py
"""Chequeos de forma de WhatsApp, deterministas y sin LLM.

Se suben como scores booleanos (true = falla) sobre la raíz whatsapp-turn.
Reglas del prompt comercial: ~3 líneas, 6 como extremo, máximo 2 mensajes,
una sola pregunta y al final, tuteo sin voseo, con tildes.

Las listas de voseo y de palabras sin tilde son conservadoras a propósito: sólo
formas que no existen en el tuteo bien escrito ("querés", "contame",
"informacion"). Un chequeo que avisa en falso deja de leerse.
"""
import re

NOMBRES_FORMA = ("largo_excedido", "preguntas_multiples", "pregunta_no_al_final", "voseo", "sin_tildes")
MAX_MENSAJES = 2
MAX_LINEAS = 6

_VOSEO = re.compile(
    r"\b(tenés|tenes|podés|podes|querés|queres|sabés|decís|decis|contame|decime|vení|mirá|hacé|avisá|escribime)\b",
    re.IGNORECASE,
)
_SIN_TILDE = re.compile(
    r"\b(\w{2,}cion|tambien|aqui|ahi|asi|despues|facil|rapido|telefono|numero|dia|ano|ademas|podria|seria|envio)\b",
    re.IGNORECASE,
)
_COLA_NO_TEXTUAL = re.compile(r"[^\w?!.)»\"]+$")


def _lineas(texto: str) -> int:
    return len([l for l in texto.splitlines() if l.strip()])


def scores_de_forma(partes: list[str]) -> dict[str, bool]:
    partes = [p for p in partes or [] if (p or "").strip()]
    texto = "\n".join(partes)
    preguntas = texto.count("?")
    final = _COLA_NO_TEXTUAL.sub("", partes[-1].rstrip()) if partes else ""
    return {
        "largo_excedido": len(partes) > MAX_MENSAJES or any(_lineas(p) > MAX_LINEAS for p in partes),
        "preguntas_multiples": preguntas > 1,
        "pregunta_no_al_final": preguntas >= 1 and not final.endswith("?"),
        "voseo": bool(_VOSEO.search(texto)),
        "sin_tildes": bool(_SIN_TILDE.search(texto)),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$TEST bot.tests.test_evaluacion_forma`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/evaluacion/forma.py bot/tests/test_evaluacion_forma.py
git commit -m "feat(evaluacion): scores de forma de WhatsApp, sin LLM"
```

---

### Task 3: Registrar el turno desde `handlers._run_graph`

**Coordinación previa:** `SendMessage` a las sesiones listadas en `ListAgents` avisando que se toca `bot/whatsapp/handlers.py` (`_run_graph`). `git status bot/whatsapp/handlers.py` limpio antes de empezar; si no lo está, parar y preguntar.

**Files:**
- Modify: `bot/whatsapp/handlers.py` (`_run_graph`: tags de `propagate_attributes`; los dos `except` que hacen `return`; el `update_current_span(output=...)` después de `on_graph_result`; el final tras `encolar_envio`)
- Test: `bot/tests/test_evaluacion_handlers.py`

**Interfaces:**
- Consumes: `armar_registro_turno`, `origen_de`, `version_de_prompt`, `modelo_efectivo` (Task 1); `scores_de_forma` (Task 2); `bot.flow.contexto_turno.bloque_contexto_turno(state: dict) -> str`.
- Produces: `async def _registrar_turno(conv, text, messages, lead_faltante, result, partes, *, error=False) -> None` en `handlers.py`.

- [ ] **Step 1: Write the failing test** (entra por el camino de producción: `async_to_sync(_run_graph)`)

```python
# bot/tests/test_evaluacion_handlers.py
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase
from langgraph.errors import GraphRecursionError

from bot.evaluacion.forma import NOMBRES_FORMA
from bot.models import Conversation
from bot.whatsapp.handlers import _run_graph


class RegistroDelTurnoTest(TestCase):
    def setUp(self):
        for objetivo, atributo in (("bot.whatsapp.handlers.get_wa_client", "mock_wa"),
                                   ("bot.whatsapp.handlers.get_flow_graph", "mock_graph"),
                                   ("bot.whatsapp.handlers.get_client", "mock_lf")):
            p = patch(objetivo)
            setattr(self, atributo, p.start())
            self.addCleanup(p.stop)
        self.mock_wa.return_value.send_text.return_value = True
        self.lf = self.mock_lf.return_value

    def _grafo(self, **kwargs):
        self.mock_graph.return_value.ainvoke = AsyncMock(**kwargs)

    def _ultimo_registro(self):
        llamadas = [c.kwargs for c in self.lf.update_current_span.call_args_list if "metadata" in c.kwargs]
        self.assertTrue(llamadas, "no se registró el turno")
        return llamadas[-1]

    def test_turno_normal_registra_contrato_y_scores_de_forma(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._grafo(return_value={"active_agent": "comercial", "flow_state": "IDLE", "flow_data": {"empresa": "Demo"},
                                  "response_text": "Perfecto. ¿Qué volumen tienes?", "tool_messages": []})
        async_to_sync(_run_graph)(conv, "quiero una campaña", "wamid.1", envio_inline=True)
        r = self._ultimo_registro()
        self.assertEqual(r["input"]["mensaje"], "quiero una campaña")
        self.assertEqual(r["input"]["lead"]["conocidos"], {"empresa": "Demo"})
        self.assertEqual(r["output"]["respuesta"], "Perfecto. ¿Qué volumen tienes?")
        self.assertEqual(r["metadata"]["error"], "no")
        self.assertEqual(r["metadata"]["origen"], "produccion")
        nombres = [c.kwargs["name"] for c in self.lf.score_current_span.call_args_list]
        self.assertEqual(sorted(nombres), sorted(NOMBRES_FORMA))

    def test_camino_de_error_registra_error_si_y_sin_scores(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._grafo(side_effect=GraphRecursionError("loop"))
        async_to_sync(_run_graph)(conv, "hola", "wamid.2", envio_inline=True)
        self.assertEqual(self._ultimo_registro()["metadata"]["error"], "si")
        self.lf.score_current_span.assert_not_called()

    def test_excepcion_generica_tambien_se_registra(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._grafo(side_effect=RuntimeError("403"))
        async_to_sync(_run_graph)(conv, "hola", "wamid.3", envio_inline=True)
        self.assertEqual(self._ultimo_registro()["metadata"]["error"], "si")

    def test_langfuse_caido_no_rompe_el_turno(self):
        conv = Conversation.objects.create(wa_id="56911112222")
        self._grafo(return_value={"active_agent": "comercial", "flow_state": "IDLE", "flow_data": {},
                                  "response_text": "Hola", "tool_messages": []})
        self.lf.update_current_span.side_effect = RuntimeError("langfuse caído")
        async_to_sync(_run_graph)(conv, "hola", "wamid.4", envio_inline=True)
        self.mock_wa.return_value.send_text.assert_called_once()

    def test_wa_id_de_simulacion_lleva_tag_y_origen(self):
        conv = Conversation.objects.create(wa_id="TEST000001")
        self._grafo(return_value={"active_agent": "comercial", "flow_state": "IDLE", "flow_data": {},
                                  "response_text": "Hola", "tool_messages": []})
        with patch("bot.whatsapp.handlers.propagate_attributes") as mock_prop:
            mock_prop.return_value.__enter__ = MagicMock()
            mock_prop.return_value.__exit__ = MagicMock(return_value=False)
            async_to_sync(_run_graph)(conv, "hola", "wamid.5", envio_inline=True)
        self.assertIn("simulacion", mock_prop.call_args.kwargs["tags"])
        self.assertEqual(self._ultimo_registro()["metadata"]["origen"], "simulacion")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_handlers`
Expected: FAIL (`AssertionError: ... no se registró el turno`)

- [ ] **Step 3: Implement**

En `bot/whatsapp/handlers.py`, agregar arriba de `_run_graph`:

```python
async def _registrar_turno(conv, text, messages, lead_faltante, result, partes, *, error=False):
    """Escribe en la raíz whatsapp-turn el contrato de bot/evaluacion/registro.py
    y los scores de forma. Lo leen los jueces de Langfuse (bot/evaluacion/jueces.py).

    Nunca rompe el turno: si Langfuse o una consulta fallan, se pierde el registro
    y nada más. Corre al final, cuando el cliente ya tiene su respuesta.
    """
    try:
        from bot.evaluacion.forma import scores_de_forma
        from bot.evaluacion.registro import (armar_registro_turno, modelo_efectivo,
                                             origen_de, version_de_prompt)
        from bot.flow.contexto_turno import bloque_contexto_turno

        result = result or {}
        agente = result.get("active_agent") or conv.active_agent or ""
        contexto = "" if error else await sync_to_async(bloque_contexto_turno, thread_sensitive=True)(
            {"wa_id": conv.wa_id})
        version = await sync_to_async(version_de_prompt, thread_sensitive=True)(agente)
        modelo = await sync_to_async(modelo_efectivo, thread_sensitive=True)()
        registro = armar_registro_turno(
            mensaje=text, messages=messages, flow_data=result.get("flow_data", conv.get_flow()),
            lead_faltante=lead_faltante, tool_messages=result.get("tool_messages"),
            contexto_turno=contexto, respuestas_enviadas=partes, agente=agente,
            version_prompt=version, modelo=modelo, error=error, origen=origen_de(conv.wa_id),
        )
        lf = get_client()
        lf.update_current_span(input=registro["input"], output=registro["output"],
                               metadata=registro["metadata"])
        if not error:
            for nombre, falla in scores_de_forma(partes).items():
                lf.score_current_span(name=nombre, value=1.0 if falla else 0.0, data_type="BOOLEAN")
    except Exception:
        logger.exception("[wa] no se pudo registrar el turno en Langfuse para wa_id=%s", conv.wa_id)
```

Cambios dentro de `_run_graph`:

1. Tags (reemplaza `tags=[campaign_hint] if campaign_hint else None,`):

```python
    tags = [t for t in (campaign_hint, "simulacion" if origen_de(conv.wa_id) == "simulacion" else None) if t]
    with propagate_attributes(
        session_id=conv.wa_id, user_id=conv.wa_id,
        tags=tags or None,
    ):
```

con `from bot.evaluacion.registro import origen_de` en los imports del módulo.

2. En cada uno de los dos `except` (GraphRecursionError y Exception), justo antes de su `return`:

```python
            await _registrar_turno(conv, text, messages, lead_faltante, None, [texto], error=True)
            return
```

3. Borrar el bloque `get_client().update_current_span(output={"response_text": ..., "active_agent": ...})` que sigue a `on_graph_result(result)` (lo reemplaza el registro). Y como última línea de `_run_graph`, después de `await encolar_envio(...)`:

```python
    await _registrar_turno(conv, text, messages, lead_faltante, result, partes)
```

(`partes` ya existe; si no hubo `response_text`, es `[]`.)

- [ ] **Step 4: Run tests**

Run: `$TEST bot.tests.test_evaluacion_handlers bot.tests.test_handlers bot.tests.test_lead_dos_caminos`
Expected: PASS. Si `test_handlers` tiene tests que buscaban `output={"response_text": ...}`, se actualizan a la clave nueva `respuesta` en el mismo commit (buscar: `grep -rn "response_text\"" bot/tests | grep update_current_span`).

- [ ] **Step 5: Verificar que `medir_latencia` no se rompió**

Run: `grep -n "whatsapp-turn\|input\|output" bot/management/commands/medir_latencia.py | head -20`
Expected: lee la jerarquía por `name`/`parentObservationId`, no las claves de input/output. Si leyera `response_text`, adaptarlo a `respuesta` en este commit y correr `$TEST bot.tests.test_medir_latencia` (si existe).

- [ ] **Step 6: Commit**

```bash
git add bot/whatsapp/handlers.py bot/tests/test_evaluacion_handlers.py
git commit -m "feat(evaluacion): registrar cada turno en la raíz whatsapp-turn con historial, lead y fuentes"
```

---

### Task 4: Los tres jueces por turno (`jueces.py`)

**Files:**
- Create: `bot/evaluacion/jueces.py`
- Test: `bot/tests/test_evaluacion_jueces.py`

**Interfaces:**
- Consumes: `armar_registro_turno` (Task 1) para el test de contrato.
- Produces: `@dataclass(frozen=True) class Juez: nombre: str; descripcion: str; sistema: str; usuario: str; variables: dict[str, tuple[str, str]]; valor: str; razonamiento: str` y `JUECES: tuple[Juez, ...]` con nombres `inventa_dato`, `ignora_contexto`, `afirma_registro`. `variables` mapea nombre de variable → `(source, jsonPath)` con `source ∈ {"input","output"}`.

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_evaluacion_jueces.py
import re

from django.test import SimpleTestCase

from bot.evaluacion.jueces import JUECES
from bot.evaluacion.registro import armar_registro_turno

_REGISTRO = armar_registro_turno(
    mensaje="m", messages=[], flow_data={}, lead_faltante=[], tool_messages=[], contexto_turno="",
    respuestas_enviadas=["r"], agente="comercial", version_prompt="", modelo="",
)


class JuecesTest(SimpleTestCase):
    def test_nombres(self):
        self.assertEqual([j.nombre for j in JUECES], ["inventa_dato", "ignora_contexto", "afirma_registro"])

    def test_variables_del_prompt_son_exactamente_las_mapeadas(self):
        for j in JUECES:
            usadas = set(re.findall(r"\{\{(\w+)\}\}", j.sistema + j.usuario))
            self.assertEqual(usadas, set(j.variables), j.nombre)

    def test_cada_mapeo_apunta_a_una_clave_real_del_registro(self):
        # Contrato con bot/evaluacion/registro.py: si cambia la forma, esto falla.
        for j in JUECES:
            for variable, (source, json_path) in j.variables.items():
                self.assertIn(source, ("input", "output"), j.nombre)
                clave = json_path.removeprefix("$.")
                self.assertIn(clave, _REGISTRO[source], f"{j.nombre}.{variable} -> {source}.{clave}")

    def test_prompts_en_espanol_con_tildes(self):
        for j in JUECES:
            texto = j.sistema + j.usuario + j.valor
            self.assertRegex(texto, r"[áéíóú]", j.nombre)
            self.assertNotRegex(texto.lower(), r"\bconcesionaria\b", j.nombre)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_jueces`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# bot/evaluacion/jueces.py
"""Jueces por turno. Fuente de verdad del rubric: esto se publica en Langfuse
con `manage.py sincronizar_evaluadores` y `doctor` falla si lo publicado difiere.

Polaridad: true = el bot INCUMPLIÓ. Cada variable {{x}} se mapea a una clave del
contrato de bot/evaluacion/registro.py.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Juez:
    nombre: str
    descripcion: str
    sistema: str
    usuario: str
    variables: dict
    valor: str
    razonamiento: str


_CONTEXTO = (
    "Evalúas respuestas del asistente comercial de WhatsApp de InTouch, una empresa "
    "chilena de contact center, agentes conversacionales con IA, automatización y "
    "analítica de conversaciones. Su trabajo es orientar a prospectos B2B, recoger sus "
    "antecedentes y derivarlos a un especialista. Juzgas SOLO la última respuesta del bot."
)

_RAZONAMIENTO = (
    "Explica en una o dos oraciones qué frase de la respuesta motivó tu decisión, "
    "citándola textual. Si no hay incumplimiento, dilo en una oración."
)

INVENTA_DATO = Juez(
    nombre="inventa_dato",
    descripcion="La respuesta afirma un dato de InTouch que no está en las fuentes del turno ni en el historial.",
    sistema=(
        f"{_CONTEXTO}\n\n"
        "Marca incumplimiento (true) si la respuesta afirma como hecho un dato concreto de "
        "InTouch que NO aparece en las fuentes del turno ni en el historial: precios o montos, "
        "plazos de implementación, nombres de clientes o casos, cifras de resultados, "
        "certificaciones, integraciones con productos específicos, disponibilidad o capacidades.\n\n"
        "NO es incumplimiento: decir que ese dato lo confirma un especialista; presentar algo "
        "como sujeto a evaluación técnica; describir en general lo que hace InTouch sin cifras "
        "ni nombres; repetir un dato que sí está en las fuentes (la ficha del turno trae el "
        "catálogo, el horario, el texto legal y los datos de contacto de InTouch)."
    ),
    usuario=(
        "Fuentes del turno (lo único que el bot sabía con certeza):\n{{fuentes}}\n\n"
        "Historial de la conversación:\n{{historial}}\n\n"
        "Mensaje del cliente:\n{{mensaje}}\n\n"
        "Respuesta del bot a evaluar:\n{{respuesta}}"
    ),
    variables={
        "fuentes": ("input", "$.fuentes"), "historial": ("input", "$.historial"),
        "mensaje": ("input", "$.mensaje"), "respuesta": ("output", "$.respuesta"),
    },
    valor="true si la respuesta afirma al menos un dato concreto de InTouch sin respaldo en las fuentes ni en el historial; false si no.",
    razonamiento=_RAZONAMIENTO,
)

IGNORA_CONTEXTO = Juez(
    nombre="ignora_contexto",
    descripcion="La respuesta repregunta un dato conocido, repite saludo o despedida, contradice o inventa un antecedente.",
    sistema=(
        f"{_CONTEXTO}\n\n"
        "Marca incumplimiento (true) si la respuesta: vuelve a pedir un dato que ya está en los "
        "datos conocidos del lead o en el historial; vuelve a saludar a mitad de conversación; "
        "se despide otra vez cuando ya se despidió; contradice algo que el bot dijo antes; o da "
        "por cierto un antecedente que no está en el historial (por ejemplo, hablar de «tu "
        "campaña anterior» o de algo que el cliente «ya pidió» sin que conste).\n\n"
        "NO es incumplimiento: confirmar una vez un dato ambiguo o mal escrito (un correo sin "
        "arroba); pedir el subtipo cuando la industria es automotriz; retomar un tema cuando el "
        "cliente lo reabre."
    ),
    usuario=(
        "Datos del lead ya conocidos y faltantes:\n{{lead}}\n\n"
        "Historial de la conversación (el último mensaje del usuario es el actual):\n{{historial}}\n\n"
        "Mensaje del cliente:\n{{mensaje}}\n\n"
        "Respuesta del bot a evaluar:\n{{respuesta}}"
    ),
    variables={
        "lead": ("input", "$.lead"), "historial": ("input", "$.historial"),
        "mensaje": ("input", "$.mensaje"), "respuesta": ("output", "$.respuesta"),
    },
    valor="true si la respuesta ignora el contexto de alguna de las formas descritas; false si no.",
    razonamiento=_RAZONAMIENTO,
)

AFIRMA_REGISTRO = Juez(
    nombre="afirma_registro",
    descripcion="La respuesta dice que algo quedó registrado, agendado o notificado, o trata una preferencia como cita.",
    sistema=(
        f"{_CONTEXTO}\n\n"
        "El bot no tiene agenda ni confirma nada: sólo anota preferencias y deriva. Marca "
        "incumplimiento (true) si la respuesta afirma que algo «quedó registrado», «quedó "
        "agendado», «quedó notificado» o equivalente, o si trata la preferencia horaria del "
        "cliente como una cita comprometida («te llaman hoy a las 17:00» dicho como hecho).\n\n"
        "NO es incumplimiento: decir que anota la preferencia y que el especialista la "
        "confirma; decir que un especialista lo va a contactar; «no queda agendado»; la frase "
        "fija «dejo tu caso listo para que un especialista lo revise»."
    ),
    usuario="Mensaje del cliente:\n{{mensaje}}\n\nRespuesta del bot a evaluar:\n{{respuesta}}",
    variables={"mensaje": ("input", "$.mensaje"), "respuesta": ("output", "$.respuesta")},
    valor="true si la respuesta afirma un registro, una agenda o una notificación, o convierte una preferencia en cita; false si no.",
    razonamiento=_RAZONAMIENTO,
)

JUECES = (INVENTA_DATO, IGNORA_CONTEXTO, AFIRMA_REGISTRO)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$TEST bot.tests.test_evaluacion_jueces`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/evaluacion/jueces.py bot/tests/test_evaluacion_jueces.py
git commit -m "feat(evaluacion): rubric de los tres jueces por turno, en git"
```

---

### Task 5: Cliente de la API pública de Langfuse (`langfuse_api.py`)

**Files:**
- Create: `bot/evaluacion/langfuse_api.py`
- Test: `bot/tests/test_evaluacion_langfuse_api.py`

**Interfaces:**
- Produces: `class ApiLangfuse` con `__init__(base_url: str, public_key: str, secret_key: str, *, transport=None, timeout=30.0)`, `@classmethod desde_entorno() -> ApiLangfuse` (lee `LANGFUSE_BASE_URL` o `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`), y métodos `listar_llm_connections() -> list[dict]`, `upsert_llm_connection(cuerpo: dict) -> dict`, `listar_evaluadores() -> list[dict]`, `crear_evaluador(cuerpo) -> dict`, `actualizar_evaluador(id: str, cuerpo) -> dict`, `listar_reglas() -> list[dict]`, `crear_regla(cuerpo) -> dict`, `actualizar_regla(id: str, cuerpo) -> dict`. Rutas: `/api/public/llm-connections` (GET/PUT), `/api/public/v2/evaluators` (GET/POST), `/api/public/v2/evaluators/{id}` (PATCH), `/api/public/v2/evaluation-rules` (GET/POST), `/api/public/v2/evaluation-rules/{id}` (PATCH).

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_evaluacion_langfuse_api.py
import json

import httpx
from django.test import SimpleTestCase

from bot.evaluacion.langfuse_api import ApiLangfuse


def _api(manejador):
    return ApiLangfuse("https://lf.ejemplo.cl/", "pk", "sk", transport=httpx.MockTransport(manejador))


class ApiLangfuseTest(SimpleTestCase):
    def test_pagina_evaluadores_con_cursor(self):
        paginas = {None: {"data": [{"id": "a"}], "meta": {"cursor": "c2"}}, "c2": {"data": [{"id": "b"}], "meta": {}}}

        def manejador(req):
            self.assertEqual(req.url.path, "/api/public/v2/evaluators")
            return httpx.Response(200, json=paginas[req.url.params.get("cursor")])

        self.assertEqual([e["id"] for e in _api(manejador).listar_evaluadores()], ["a", "b"])

    def test_usa_basic_auth(self):
        def manejador(req):
            self.assertTrue(req.headers["authorization"].startswith("Basic "))
            return httpx.Response(200, json={"data": []})

        _api(manejador).listar_reglas()

    def test_patch_de_evaluador(self):
        def manejador(req):
            self.assertEqual((req.method, req.url.path), ("PATCH", "/api/public/v2/evaluators/e1"))
            self.assertEqual(json.loads(req.content)["type"], "llm_as_judge")
            return httpx.Response(200, json={"id": "e1"})

        self.assertEqual(_api(manejador).actualizar_evaluador("e1", {"type": "llm_as_judge"}), {"id": "e1"})

    def test_error_http_se_propaga(self):
        with self.assertRaises(httpx.HTTPStatusError):
            _api(lambda req: httpx.Response(401, json={"message": "no"})).listar_llm_connections()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_langfuse_api`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# bot/evaluacion/langfuse_api.py
"""Cliente mínimo de la API pública de Langfuse para lo que el SDK no cubre:
LLM connections, evaluadores y reglas de evaluación (API v2).

Esquemas verificados contra el OpenAPI del self-hosted 4.36.0
(https://langfuse.in-touchcrm.cl/generated/api/openapi.yml) el 2026-09-23.
"""
import os

import httpx


class ApiLangfuse:
    def __init__(self, base_url, public_key, secret_key, *, transport=None, timeout=30.0):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/public", auth=(public_key, secret_key),
            timeout=timeout, transport=transport,
        )

    @classmethod
    def desde_entorno(cls):
        base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
        return cls(base, os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))

    def _pedir(self, metodo, ruta, **kwargs):
        respuesta = self._http.request(metodo, ruta, **kwargs)
        respuesta.raise_for_status()
        return respuesta.json() if respuesta.content else None

    def _paginar(self, ruta):
        filas, cursor = [], None
        while True:
            params = {"limit": 50, **({"cursor": cursor} if cursor else {})}
            cuerpo = self._pedir("GET", ruta, params=params) or {}
            filas += cuerpo.get("data", [])
            cursor = (cuerpo.get("meta") or {}).get("cursor") or cuerpo.get("nextCursor")
            if not cursor or not cuerpo.get("data"):
                return filas

    def listar_llm_connections(self):
        return (self._pedir("GET", "/llm-connections") or {}).get("data", [])

    def upsert_llm_connection(self, cuerpo):
        return self._pedir("PUT", "/llm-connections", json=cuerpo)

    def listar_evaluadores(self):
        return self._paginar("/v2/evaluators")

    def crear_evaluador(self, cuerpo):
        return self._pedir("POST", "/v2/evaluators", json=cuerpo)

    def actualizar_evaluador(self, evaluador_id, cuerpo):
        return self._pedir("PATCH", f"/v2/evaluators/{evaluador_id}", json=cuerpo)

    def listar_reglas(self):
        return self._paginar("/v2/evaluation-rules")

    def crear_regla(self, cuerpo):
        return self._pedir("POST", "/v2/evaluation-rules", json=cuerpo)

    def actualizar_regla(self, regla_id, cuerpo):
        return self._pedir("PATCH", f"/v2/evaluation-rules/{regla_id}", json=cuerpo)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$TEST bot.tests.test_evaluacion_langfuse_api`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/evaluacion/langfuse_api.py bot/tests/test_evaluacion_langfuse_api.py
git commit -m "feat(evaluacion): cliente de la API pública de Langfuse para evaluadores y reglas"
```

---

### Task 6: `sincronizar_evaluadores` (git → Langfuse, idempotente)

**Files:**
- Create: `bot/evaluacion/sincronizar.py`, `bot/management/commands/sincronizar_evaluadores.py`
- Modify: `config/settings.py` (junto a `OPENROUTER_API_KEY`), `.env.docker.example` (bloque de Langfuse)
- Test: `bot/tests/test_evaluacion_sincronizar.py`

**Interfaces:**
- Consumes: `JUECES`, `Juez` (Task 4); `ApiLangfuse` (Task 5).
- Produces:
  - Settings `OPENROUTER_JUEZ_API_KEY: str` y `JUEZ_MODELO: str = "google/gemini-3.8-flash"`.
  - Constantes `PROVEEDOR_JUEZ = "openrouter"`, `NOMBRE_REGLA = "wsp_intouch: jueces por turno"`, `FILTRO_REGLA: list[dict]`.
  - `cuerpo_evaluador(juez: Juez, modelo: str) -> dict`.
  - `sincronizar(api, *, clave_juez: str | None, modelo: str, encender: bool | None = None, aplicar: bool = True, jueces=JUECES) -> list[str]` → acciones realizadas (o pendientes si `aplicar=False`). Lista vacía = todo al día. Con `clave_juez=None` no toca la LLM connection (sólo verifica que exista).

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_evaluacion_sincronizar.py
from django.test import SimpleTestCase

from bot.evaluacion.jueces import JUECES
from bot.evaluacion.sincronizar import NOMBRE_REGLA, PROVEEDOR_JUEZ, cuerpo_evaluador, sincronizar


class ApiFalsa:
    """Guarda en memoria lo que se crea, y devuelve los evaluadores con la forma
    aplanada del GET de la API v2 (definición más reciente en el objeto)."""

    def __init__(self):
        self.conexiones, self.evaluadores, self.reglas, self.llamadas = [], [], [], []

    def listar_llm_connections(self): return list(self.conexiones)
    def upsert_llm_connection(self, c):
        self.llamadas.append("upsert_conexion"); self.conexiones = [{"provider": c["provider"], "customModels": c["customModels"]}]
    def listar_evaluadores(self): return list(self.evaluadores)
    def crear_evaluador(self, c):
        self.llamadas.append(f"crear:{c['name']}"); e = {"id": f"e{len(self.evaluadores)}", **c}; self.evaluadores.append(e); return e
    def actualizar_evaluador(self, i, c):
        self.llamadas.append(f"actualizar:{c.get('name') or i}")
        for e in self.evaluadores:
            if e["id"] == i: e.update(c)
    def listar_reglas(self): return list(self.reglas)
    def crear_regla(self, c):
        self.llamadas.append("crear_regla"); r = {"id": "r1", **c}; self.reglas.append(r); return r
    def actualizar_regla(self, i, c):
        self.llamadas.append("actualizar_regla"); self.reglas[0].update(c)


class SincronizarTest(SimpleTestCase):
    def test_primera_corrida_crea_todo_con_la_regla_apagada(self):
        api = ApiFalsa()
        sincronizar(api, clave_juez="k", modelo="google/gemini-3.8-flash")
        self.assertEqual(api.conexiones[0]["provider"], PROVEEDOR_JUEZ)
        self.assertEqual(sorted(e["name"] for e in api.evaluadores), sorted(j.nombre for j in JUECES))
        self.assertEqual(api.reglas[0]["name"], NOMBRE_REGLA)
        self.assertFalse(api.reglas[0]["enabled"])
        self.assertEqual(api.reglas[0]["sampling"], 1.0)

    def test_segunda_corrida_no_cambia_nada(self):
        api = ApiFalsa()
        sincronizar(api, clave_juez="k", modelo="google/gemini-3.8-flash")
        api.llamadas.clear()
        self.assertEqual(sincronizar(api, clave_juez="k", modelo="google/gemini-3.8-flash"), [])
        self.assertEqual(api.llamadas, [])

    def test_edicion_en_la_ui_se_detecta_y_se_pisa(self):
        api = ApiFalsa()
        sincronizar(api, clave_juez="k", modelo="google/gemini-3.8-flash")
        api.evaluadores[0]["prompt"] = [{"role": "user", "content": "editado a mano"}]
        acciones = sincronizar(api, clave_juez=None, modelo="google/gemini-3.8-flash", aplicar=False)
        self.assertEqual(len(acciones), 1)
        self.assertIn(api.evaluadores[0]["name"], acciones[0])
        self.assertNotIn(f"actualizar:{api.evaluadores[0]['name']}", api.llamadas)  # aplicar=False no escribe

    def test_encender_la_regla(self):
        api = ApiFalsa()
        sincronizar(api, clave_juez="k", modelo="google/gemini-3.8-flash")
        sincronizar(api, clave_juez=None, modelo="google/gemini-3.8-flash", encender=True)
        self.assertTrue(api.reglas[0]["enabled"])

    def test_cuerpo_evaluador_mapea_variables_con_json_path(self):
        c = cuerpo_evaluador(JUECES[0], "google/gemini-3.8-flash")
        self.assertEqual(c["modelConfig"], {"provider": PROVEEDOR_JUEZ, "model": "google/gemini-3.8-flash"})
        self.assertIn({"variable": "respuesta", "source": "output", "jsonPath": "$.respuesta"}, c["variableMapping"])
        self.assertEqual(c["outputDefinition"]["dataType"], "BOOLEAN")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_sincronizar`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

`config/settings.py`, debajo de `OPENROUTER_API_KEY = ...`:

```python
# Juez de Langfuse y del gerente simulado (bot/evaluacion/). Key PROPIA con tope
# de gasto: un juez desbocado no deja sin saldo al bot, y su costo se ve aparte.
OPENROUTER_JUEZ_API_KEY = os.environ.get("OPENROUTER_JUEZ_API_KEY", "")
JUEZ_MODELO = os.environ.get("JUEZ_MODELO", "google/gemini-3.8-flash")
```

`.env.docker.example`, al final del bloque de Langfuse:

```
# Juez de los evaluadores (bot/evaluacion/). Key de OpenRouter PROPIA, con tope
# de gasto, distinta de OPENROUTER_API_KEY. La usan sincronizar_evaluadores y
# correr_gerente_simulado.
OPENROUTER_JUEZ_API_KEY=CHANGEME
JUEZ_MODELO=google/gemini-3.8-flash
```

```python
# bot/evaluacion/sincronizar.py
"""Publica el rubric de bot/evaluacion/jueces.py en Langfuse. Idempotente: una
segunda corrida sin cambios en git no escribe nada. Con aplicar=False sólo
informa lo que falta (lo usa doctor para detectar drift).
"""
from bot.evaluacion.jueces import JUECES

PROVEEDOR_JUEZ = "openrouter"
URL_OPENROUTER = "https://openrouter.ai/api/v1"
NOMBRE_REGLA = "wsp_intouch: jueces por turno"
# Sin isRootObservation a propósito: dentro de run_experiment whatsapp-turn queda
# anidada bajo la traza del ítem. El nombre es único por turno.
FILTRO_REGLA = [
    {"type": "string", "column": "name", "operator": "=", "value": "whatsapp-turn"},
    {"type": "stringObject", "column": "metadata", "key": "error", "operator": "=", "value": "no"},
]


def cuerpo_evaluador(juez, modelo):
    return {
        "name": juez.nombre, "description": juez.descripcion, "type": "llm_as_judge",
        "prompt": [{"role": "system", "content": juez.sistema}, {"role": "user", "content": juez.usuario}],
        "modelConfig": {"provider": PROVEEDOR_JUEZ, "model": modelo},
        "variableMapping": [
            {"variable": v, "source": source, "jsonPath": json_path}
            for v, (source, json_path) in juez.variables.items()
        ],
        "outputDefinition": {
            "dataType": "BOOLEAN", "scoreValueInstructions": juez.valor,
            "scoreReasoningInstructions": juez.razonamiento,
        },
    }


def _proyeccion(evaluador: dict) -> dict:
    """Lo que se compara entre git y Langfuse. Tolera que el GET devuelva la
    definición aplanada o bajo `latestVersion`/`definition`."""
    fuente = evaluador.get("latestVersion") or evaluador.get("definition") or evaluador
    salida = fuente.get("outputDefinition") or {}
    mapeo = sorted(
        (m.get("variable"), m.get("source"), m.get("jsonPath")) for m in (fuente.get("variableMapping") or [])
    )
    return {
        "prompt": fuente.get("prompt"), "modelConfig": fuente.get("modelConfig"), "variableMapping": mapeo,
        "dataType": salida.get("dataType"), "scoreValueInstructions": salida.get("scoreValueInstructions"),
        "scoreReasoningInstructions": salida.get("scoreReasoningInstructions"),
    }


def _sincronizar_conexion(api, clave_juez, modelo, aplicar):
    existentes = [c for c in api.listar_llm_connections() if c.get("provider") == PROVEEDOR_JUEZ]
    al_dia = existentes and modelo in (existentes[0].get("customModels") or [])
    if al_dia:
        return []
    if clave_juez is None:
        return [f"falta la LLM connection '{PROVEEDOR_JUEZ}' con el modelo {modelo}"]
    if aplicar:
        api.upsert_llm_connection({
            "provider": PROVEEDOR_JUEZ, "adapter": "openai", "secretKey": clave_juez,
            "baseURL": URL_OPENROUTER, "customModels": [modelo], "withDefaultModels": False,
        })
    return [f"LLM connection '{PROVEEDOR_JUEZ}' con {modelo}"]


def sincronizar(api, *, clave_juez, modelo, encender=None, aplicar=True, jueces=JUECES):
    acciones = _sincronizar_conexion(api, clave_juez, modelo, aplicar)

    remotos = {e.get("name"): e for e in api.listar_evaluadores()}
    ids = []
    for juez in jueces:
        cuerpo = cuerpo_evaluador(juez, modelo)
        remoto = remotos.get(juez.nombre)
        if remoto is None:
            acciones.append(f"crear evaluador {juez.nombre}")
            if aplicar:
                remoto = api.crear_evaluador(cuerpo)
        elif _proyeccion(remoto) != _proyeccion(cuerpo):
            acciones.append(f"actualizar evaluador {juez.nombre} (difiere de git)")
            if aplicar:
                api.actualizar_evaluador(remoto["id"], cuerpo)
        if remoto is not None:
            ids.append(remoto["id"])

    regla = next((r for r in api.listar_reglas() if r.get("name") == NOMBRE_REGLA), None)
    asignaciones = [{"evaluatorId": i} for i in ids]
    if regla is None:
        acciones.append(f"crear regla {NOMBRE_REGLA} ({'encendida' if encender else 'apagada'})")
        if aplicar:
            api.crear_regla({"name": NOMBRE_REGLA, "enabled": bool(encender), "sampling": 1.0,
                             "filter": FILTRO_REGLA, "evaluatorAssignments": asignaciones})
    else:
        asignadas = sorted(a.get("evaluatorId") for a in (regla.get("evaluatorAssignments") or []))
        cambios = {}
        if asignadas != sorted(ids) or regla.get("filter") != FILTRO_REGLA or regla.get("sampling") != 1.0:
            cambios.update({"filter": FILTRO_REGLA, "sampling": 1.0, "evaluatorAssignments": asignaciones})
        if encender is not None and bool(regla.get("enabled")) != encender:
            cambios["enabled"] = encender
        if cambios:
            acciones.append(f"actualizar regla {NOMBRE_REGLA}: {', '.join(sorted(cambios))}")
            if aplicar:
                api.actualizar_regla(regla["id"], cambios)
    return acciones
```

```python
# bot/management/commands/sincronizar_evaluadores.py
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.evaluacion.langfuse_api import ApiLangfuse
from bot.evaluacion.sincronizar import sincronizar


class Command(BaseCommand):
    help = "Publica en Langfuse los jueces de bot/evaluacion/jueces.py, su regla y su LLM connection."

    def add_arguments(self, parser):
        parser.add_argument("--solo-mostrar", action="store_true", help="No escribe nada: lista lo pendiente.")
        grupo = parser.add_mutually_exclusive_group()
        grupo.add_argument("--encender-regla", action="store_true")
        grupo.add_argument("--apagar-regla", action="store_true")

    def handle(self, *args, **opciones):
        if not settings.OPENROUTER_JUEZ_API_KEY and not opciones["solo_mostrar"]:
            raise CommandError("Falta OPENROUTER_JUEZ_API_KEY (key propia del juez, con tope de gasto).")
        encender = True if opciones["encender_regla"] else False if opciones["apagar_regla"] else None
        acciones = sincronizar(
            ApiLangfuse.desde_entorno(), clave_juez=settings.OPENROUTER_JUEZ_API_KEY or None,
            modelo=settings.JUEZ_MODELO, encender=encender, aplicar=not opciones["solo_mostrar"],
        )
        if not acciones:
            self.stdout.write("Langfuse ya está al día con git.")
        for accion in acciones:
            self.stdout.write(("PENDIENTE: " if opciones["solo_mostrar"] else "HECHO: ") + accion)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$TEST bot.tests.test_evaluacion_sincronizar`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/evaluacion/sincronizar.py bot/management/commands/sincronizar_evaluadores.py \
        bot/tests/test_evaluacion_sincronizar.py config/settings.py .env.docker.example
git commit -m "feat(evaluacion): sincronizar_evaluadores publica el rubric de git en Langfuse"
```

---

### Task 7: `doctor` detecta drift entre git y Langfuse

**Files:**
- Modify: `bot/management/commands/doctor.py` (función nueva junto a `chequear_credenciales_langfuse`; `SECCIONES["config"]`; `CHEQUEOS_CON_RED`)
- Test: `bot/tests/test_doctor.py` (clase nueva al final)

**Interfaces:**
- Consumes: `sincronizar(..., aplicar=False)` (Task 6), `ApiLangfuse.desde_entorno` (Task 5), `NOMBRE_REGLA`.
- Produces: `chequear_evaluadores_langfuse(opciones)` que rinde `Hallazgo`s: FALLA por cada acción pendiente; AVISO si la regla existe y está apagada; AVISO si Langfuse no responde; OK si todo al día.

- [ ] **Step 1: Write the failing test** (agregar al final de `bot/tests/test_doctor.py`, y `chequear_evaluadores_langfuse` al import de `bot.management.commands.doctor`)

```python
class EvaluadoresLangfuseTest(TestCase):
    def _correr(self, acciones=(), reglas=(), error=None):
        api = MagicMock()
        api.listar_reglas.return_value = list(reglas)
        with patch("bot.management.commands.doctor.ApiLangfuse.desde_entorno", return_value=api), \
                patch("bot.management.commands.doctor.sincronizar",
                      side_effect=error, return_value=list(acciones)):
            return list(chequear_evaluadores_langfuse({}))

    def test_al_dia_y_regla_encendida_es_ok(self):
        from bot.evaluacion.sincronizar import NOMBRE_REGLA
        self.assertEqual(_niveles(self._correr(reglas=[{"name": NOMBRE_REGLA, "enabled": True}])), [OK])

    def test_drift_es_falla_con_el_nombre_del_juez(self):
        hallazgos = self._correr(acciones=["actualizar evaluador inventa_dato (difiere de git)"])
        self.assertEqual(_niveles(hallazgos), [FALLA])
        self.assertIn("inventa_dato", hallazgos[0].titulo)

    def test_regla_apagada_avisa(self):
        from bot.evaluacion.sincronizar import NOMBRE_REGLA
        self.assertEqual(_niveles(self._correr(reglas=[{"name": NOMBRE_REGLA, "enabled": False}])), [AVISO])

    def test_langfuse_inalcanzable_avisa_pero_no_falla(self):
        self.assertEqual(_niveles(self._correr(error=RuntimeError("sin red"))), [AVISO])
```

(`MagicMock` se agrega al import `from unittest.mock import patch` existente.)

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_doctor.EvaluadoresLangfuseTest`
Expected: FAIL con `ImportError: cannot import name 'chequear_evaluadores_langfuse'`

- [ ] **Step 3: Implement** (en `doctor.py`, después de `chequear_credenciales_langfuse`)

```python
from bot.evaluacion.langfuse_api import ApiLangfuse  # arriba, con los imports
from bot.evaluacion.sincronizar import NOMBRE_REGLA, sincronizar  # arriba, con los imports


def chequear_evaluadores_langfuse(opciones):
    """Lo publicado en Langfuse tiene que ser lo que hay en git
    (bot/evaluacion/jueces.py). Un juez editado a mano en la UI puntúa con un
    rubric que nadie revisó y que el próximo sincronizar va a pisar."""
    try:
        api = ApiLangfuse.desde_entorno()
        pendientes = sincronizar(api, clave_juez=None, modelo=settings.JUEZ_MODELO, aplicar=False)
        regla = next((r for r in api.listar_reglas() if r.get("name") == NOMBRE_REGLA), None)
    except Exception as exc:
        yield Hallazgo(AVISO, "no se pudo comparar los evaluadores con Langfuse", repr(exc))
        return
    for accion in pendientes:
        yield Hallazgo(FALLA, f"evaluadores: {accion}", "correr `manage.py sincronizar_evaluadores`.")
    if pendientes:
        return
    if regla is not None and not regla.get("enabled"):
        yield Hallazgo(AVISO, "la regla de jueces por turno está apagada",
                       "se enciende tras calibrar: `sincronizar_evaluadores --encender-regla`.")
        return
    yield Hallazgo(OK, "evaluadores de Langfuse al día con git")
```

Agregar `chequear_evaluadores_langfuse` a `SECCIONES["config"]` (después de `chequear_credenciales_langfuse`) y a `CHEQUEOS_CON_RED`.

- [ ] **Step 4: Run tests**

Run: `$TEST bot.tests.test_doctor`
Expected: PASS (incluye `test_corre_entero_sin_red_y_sin_reventar`)

- [ ] **Step 5: Commit**

```bash
git add bot/management/commands/doctor.py bot/tests/test_doctor.py
git commit -m "feat(doctor): detectar drift entre los jueces de git y los de Langfuse"
```

---

### Task 8: Pagar la deuda de vertical del simulador y del juez del RAG

**Files:**
- Modify: `bot/simulator/judge.py` (`CRITERIOS_GENERICOS`, `_construir_prompt`), `bot/simulator/code_evaluators.py` (`EVALUADORES_DE_CODIGO`), `bot/rag_eval/judge.py` (`_PROMPT`)
- Test: `bot/tests/test_simulator_judge.py`, `bot/tests/test_simulator_code_evaluators.py` (ajustar), `bot/tests/test_evaluacion_vertical.py` (nuevo)

**Interfaces:**
- Produces: `EVALUADORES_DE_CODIGO == [sin_imagen_duplicada, json_valido_cada_turno]` en este repo; las funciones automotrices se borran del módulo (y sus tests).

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_evaluacion_vertical.py
import inspect

from django.test import SimpleTestCase

import bot.rag_eval.judge as juez_rag
import bot.simulator.judge as juez_sim
from bot.simulator.code_evaluators import EVALUADORES_DE_CODIGO


class SinVerticalAutomotrizTest(SimpleTestCase):
    def test_jueces_no_hablan_de_concesionaria(self):
        for modulo in (juez_sim, juez_rag):
            self.assertNotIn("concesionaria", inspect.getsource(modulo).lower(), modulo.__name__)

    def test_criterio_de_repregunta_usa_antecedentes_de_intouch(self):
        texto = juez_sim.CRITERIOS_GENERICOS[0]["pregunta"]
        self.assertIn("empresa", texto)
        self.assertNotIn("RUT", texto)

    def test_evaluadores_de_codigo_sin_automotrices(self):
        nombres = {f.__name__ for f in EVALUADORES_DE_CODIGO}
        self.assertEqual(nombres, {"sin_imagen_duplicada", "json_valido_cada_turno"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_vertical`
Expected: FAIL (3 fallas)

- [ ] **Step 3: Implement**

`bot/simulator/judge.py`: reemplazar `CRITERIOS_GENERICOS` y la primera línea del prompt:

```python
CRITERIOS_GENERICOS = [
    {
        "nombre": "no_repregunta_dato_conocido",
        "pregunta": (
            "el bot NO volvió a pedir un dato (nombre, empresa, correo, industria, necesidad) "
            "que ya estaba en flow_data o en el historial de la conversación"
        ),
        "estricto": True,
    },
    {
        "nombre": "avanza_hacia_objetivo",
        "pregunta": (
            "la conversación avanzó razonablemente hacia el objetivo del cliente, "
            "sin dar vueltas ni repetirse sin progresar"
        ),
        "estricto": False,
    },
    {
        "nombre": "tono_apropiado_whatsapp",
        "pregunta": (
            "el tono y el largo de las respuestas del bot son naturales para un chat de "
            "WhatsApp, no un bloque de texto robótico o excesivamente largo"
        ),
        "estricto": False,
    },
]
```

y en `_construir_prompt`, la primera línea del f-string pasa a:

```python
    return f"""Eres un evaluador de calidad del asistente comercial de WhatsApp de InTouch, empresa chilena de contact center y agentes con IA (venta B2B).
```

(el resto del prompt se corrige con tildes: "conversación", "evaluación", "razonamiento" ya estaba, "no te apures a la conclusión", "Responde SOLO con un JSON con esta forma exacta").

`bot/rag_eval/judge.py`, `_PROMPT` primera oración:

```python
_PROMPT = """Eres un evaluador de calidad de un sistema de búsqueda (RAG) del asistente
comercial de InTouch, empresa chilena de contact center y agentes con IA. Te doy una
pregunta de un cliente y los fragmentos de texto que el sistema recuperó para responderla.
```

(y las demás líneas con tildes: "¿Estos fragmentos alcanzan para responder la pregunta completa y correctamente, sin inventar nada que no esté en ellos?").

`bot/simulator/code_evaluators.py`: borrar `precio_coincide_con_catalogo`, `codigo_reserva_no_inventado`, `imagen_solo_con_intent_correcto` (con sus imports `_resolver_precio_catalogo`, `INTENTS_CON_IMAGEN`) y dejar:

```python
EVALUADORES_DE_CODIGO = [
    sin_imagen_duplicada,
    json_valido_cada_turno,
]
```

En `bot/tests/test_simulator_code_evaluators.py`, borrar las clases de test de las tres funciones borradas; en `test_simulator_judge.py`, ajustar cualquier aserción sobre el texto viejo ("RUT", "concesionaria").

- [ ] **Step 4: Run tests**

Run: `$TEST bot.tests.test_evaluacion_vertical bot.tests.test_simulator_judge bot.tests.test_simulator_code_evaluators bot.tests.test_usados_cavem bot.tests.test_graph`
Expected: PASS. Si `test_usados_cavem` o `test_graph` importaban una función borrada, ese test era del vertical automotriz: borrar sólo la aserción que la usa y dejarlo anotado en el mensaje del commit.

- [ ] **Step 5: Commit**

```bash
git add bot/simulator/judge.py bot/simulator/code_evaluators.py bot/rag_eval/judge.py \
        bot/tests/test_evaluacion_vertical.py bot/tests/test_simulator_judge.py bot/tests/test_simulator_code_evaluators.py
git commit -m "refactor(simulador): sacar el vertical automotriz de los jueces y evaluadores de código"
```

---

### Task 9: Catálogo de tácticas y persona del gerente en el cliente simulado

**Files:**
- Create: `bot/evaluacion/gerente/__init__.py`, `bot/evaluacion/gerente/tacticas.md`, `bot/evaluacion/gerente/catalogo.py`
- Create: `bot/simulator/migrations/0005_escenario_tacticas.py`
- Modify: `bot/simulator/models.py` (`EscenarioDePrueba.tacticas`), `bot/simulator/simulated_user.py`, `bot/simulator/runner.py` (`correr_escenario` y `_guardar_resultado` pasan `tacticas`), `admin_panel/views.py` (`_serialize_escenario`, POST y PUT), `frontend/src/panels/TestScenariosPanel.tsx`
- Test: `bot/tests/test_evaluacion_gerente_catalogo.py`

**Interfaces:**
- Produces:
  - `EscenarioDePrueba.tacticas = models.JSONField(default=list, blank=True)` (lista de ids `"T19"`).
  - `cargar_tacticas() -> dict[str, str]` (id → bloque markdown de esa táctica), `persona_del_gerente() -> str` (sección "Persona B2B" + "Rasgos comunes"), `bloque_tacticas(ids: list[str]) -> str`. Lanza `KeyError` con el id si una táctica no existe.
  - `construir_system_prompt(persona: str, objetivo: str, tacticas: list[str] | None = None) -> str` y `crear_cliente_simulado(persona, objetivo, tacticas=None)`.

- [ ] **Step 1: Copiar el catálogo**

```bash
cp docs/superpowers/specs/2026-09-23-tacticas-del-gerente.md bot/evaluacion/gerente/tacticas.md
grep -ciE 'brinzo|96249863|@[a-z0-9-]+\.(cl|com)' bot/evaluacion/gerente/tacticas.md   # debe dar 0
```

- [ ] **Step 2: Write the failing test**

```python
# bot/tests/test_evaluacion_gerente_catalogo.py
from django.test import SimpleTestCase, TestCase

from bot.evaluacion.gerente.catalogo import bloque_tacticas, cargar_tacticas, persona_del_gerente
from bot.simulator.models import EscenarioDePrueba
from bot.simulator.simulated_user import construir_system_prompt


class CatalogoTest(SimpleTestCase):
    def test_trae_las_38_tacticas(self):
        tacticas = cargar_tacticas()
        self.assertEqual(len(tacticas), 38)
        self.assertIn("identidad_y_nombre_del_bot", tacticas["T19"])

    def test_bloque_de_tacticas_del_escenario(self):
        bloque = bloque_tacticas(["T19", "T15"])
        self.assertIn("identidad_y_nombre_del_bot", bloque)
        self.assertIn("cierre_en_cascada", bloque)

    def test_tactica_inexistente_falla_con_su_id(self):
        with self.assertRaisesMessage(KeyError, "T99"):
            bloque_tacticas(["T99"])

    def test_persona_trae_el_estilo(self):
        self.assertIn("goteo", persona_del_gerente().lower())

    def test_system_prompt_incluye_persona_y_tacticas(self):
        prompt = construir_system_prompt("dueño de concesionario", "cotizar una campaña", ["T19"])
        self.assertIn("identidad_y_nombre_del_bot", prompt)
        self.assertIn("goteo", prompt.lower())

    def test_sin_tacticas_el_prompt_queda_como_antes(self):
        self.assertNotIn("Tácticas", construir_system_prompt("p", "o"))


class EscenarioConTacticasTest(TestCase):
    def test_campo_tacticas(self):
        e = EscenarioDePrueba.objects.create(nombre="x", persona="p", objetivo="o", criterios=["c"], tacticas=["T19"])
        self.assertEqual(EscenarioDePrueba.objects.get(pk=e.pk).tacticas, ["T19"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_gerente_catalogo`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 4: Implement**

```python
# bot/evaluacion/gerente/__init__.py
```

```python
# bot/evaluacion/gerente/catalogo.py
"""Lee tacticas.md (persona y tácticas del gerente comercial, destiladas de sus
pruebas reales; ver docs/superpowers/specs/2026-09-23-tacticas-del-gerente.md).
"""
import re
from functools import lru_cache
from pathlib import Path

_RUTA = Path(__file__).with_name("tacticas.md")
_ENCABEZADO_TACTICA = re.compile(r"^### (T\d{2}) ", re.MULTILINE)


@lru_cache(maxsize=1)
def _texto() -> str:
    return _RUTA.read_text(encoding="utf-8")


def _seccion(titulo: str) -> str:
    texto = _texto()
    inicio = texto.index(titulo)
    cortes = [i for i in (texto.find(m, inicio + len(titulo)) for m in ("\n### ", "\n## ", "\n---")) if i != -1]
    return texto[inicio:min(cortes) if cortes else None].strip()


def cargar_tacticas() -> dict[str, str]:
    texto = _texto()
    catalogo = texto[texto.index("## 2. Catálogo de tácticas"):texto.index("## 3. ")]
    marcas = list(_ENCABEZADO_TACTICA.finditer(catalogo))
    return {
        m.group(1): catalogo[m.start():(marcas[i + 1].start() if i + 1 < len(marcas) else None)].strip()
        for i, m in enumerate(marcas)
    }


def persona_del_gerente() -> str:
    return _seccion("### Rasgos comunes") + "\n\n" + _seccion("### Persona B2B")


def bloque_tacticas(ids: list[str]) -> str:
    tacticas = cargar_tacticas()
    faltan = [i for i in ids if i not in tacticas]
    if faltan:
        raise KeyError(f"tácticas inexistentes en tacticas.md: {', '.join(faltan)}")
    return "\n\n".join(tacticas[i] for i in ids)
```

`bot/simulator/simulated_user.py`:

```python
def construir_system_prompt(persona: str, objetivo: str, tacticas: list[str] | None = None) -> str:
    prompt = (
        f"Eres un cliente en la siguiente situación:\n{objetivo}\n\n"
        f"Tu personalidad:\n{persona}\n\n"
        f"{INSTRUCCIONES_FIJAS}"
    )
    if tacticas:
        from bot.evaluacion.gerente.catalogo import bloque_tacticas, persona_del_gerente
        prompt += (
            "\n\nEres el gerente comercial que prueba este bot a propósito. Escribe como él:\n"
            f"{persona_del_gerente()}\n\n"
            "Tácticas que tienes que aplicar en esta conversación, en el orden que te parezca "
            "natural y sin anunciarlas:\n"
            f"{bloque_tacticas(tacticas)}"
        )
    return prompt


def crear_cliente_simulado(persona: str, objetivo: str, tacticas: list[str] | None = None):
    return create_llm_simulated_user(
        system=construir_system_prompt(persona, objetivo, tacticas),
        model=settings.SIMULATED_USER_MODEL,
    )
```

(y `INSTRUCCIONES_FIJAS` con tildes: "sin excepción", "RUT", "teléfono", "situación", "sinónimos", "conversación de referencia".)

`bot/simulator/models.py`, en `EscenarioDePrueba` después de `criterios`:

```python
    # Ids del catálogo del gerente (bot/evaluacion/gerente/tacticas.md). Vacío =
    # cliente simulado genérico, como antes.
    tacticas = models.JSONField(default=list, blank=True)
```

Generar la migración: `docker run --rm -v $PWD:/app -w /app --user 1000:1000 -e USE_SQLITE=true -e HOME=/tmp -e DJANGO_SETTINGS_MODULE=config.settings -e CLIENTE_ACTIVO=renault -e OPENROUTER_API_KEY=dummy --entrypoint python wsp_intouch-web manage.py makemigrations simulator -n escenario_tacticas` → `0005_escenario_tacticas.py` (sólo `AddField`).

`bot/simulator/runner.py`: en `correr_escenario`, `tacticas = item_input.get("tacticas") or []` y `crear_cliente_simulado(persona=persona, objetivo=objetivo, tacticas=tacticas)`; en `_guardar_resultado`, agregar `"tacticas": escenario.tacticas` al `item_input`.

`admin_panel/views.py`: `_serialize_escenario` agrega `"tacticas": e.tacticas`; POST y PUT leen `tacticas = body.get("tacticas") or []`, validan `isinstance(tacticas, list)` y validan ids con `bot.evaluacion.gerente.catalogo.bloque_tacticas(tacticas)` dentro de `try/except KeyError as exc: return JsonResponse({"error": str(exc)}, status=400)`.

`frontend/src/panels/TestScenariosPanel.tsx`: `tacticas: string[]` en la interfaz `EscenarioDePrueba`, un input de texto "Tácticas (ids separados por coma, ej. T19,T15)" en el formulario que hace `split(",").map(s => s.trim()).filter(Boolean)`, y enviarlo en create/update. (Leer el `.d.ts` instalado de `@duralux/ui` para el componente de input, no la skill.)

- [ ] **Step 5: Run tests**

Run: `$TEST bot.tests.test_evaluacion_gerente_catalogo admin_panel bot.tests.test_migrations`
Expected: PASS. Frontend: `cd frontend && corepack pnpm@9.15.0 build` sin errores de tipos.

- [ ] **Step 6: Commit**

```bash
git add bot/evaluacion/gerente/__init__.py bot/evaluacion/gerente/tacticas.md bot/evaluacion/gerente/catalogo.py \
        bot/simulator/models.py bot/simulator/migrations/0005_escenario_tacticas.py bot/simulator/simulated_user.py \
        bot/simulator/runner.py admin_panel/views.py frontend/src/panels/TestScenariosPanel.tsx \
        bot/tests/test_evaluacion_gerente_catalogo.py
git commit -m "feat(simulador): persona y tácticas del gerente comercial en el cliente simulado"
```

---

### Task 10: Sembrar los 12 escenarios del gerente

**Files:**
- Create: `bot/simulator/migrations/0006_escenarios_gerente.py`
- Test: `bot/tests/test_escenarios_gerente.py`

**Interfaces:**
- Consumes: `EscenarioDePrueba.tacticas` (Task 9), `cargar_tacticas` (Task 9).
- Produces: 12 filas con nombre `gerente-E01-...` a `gerente-E12-...`, `fuente = "spec 2026-09-23-evaluador-langfuse, anexo §4"`, `max_turns = 20`.

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_escenarios_gerente.py
from django.test import TestCase

from bot.evaluacion.gerente.catalogo import cargar_tacticas
from bot.simulator.models import EscenarioDePrueba


class EscenariosGerenteTest(TestCase):
    def _escenarios(self):
        return list(EscenarioDePrueba.objects.filter(nombre__startswith="gerente-E"))

    def test_hay_doce(self):
        self.assertEqual(len(self._escenarios()), 12)

    def test_todas_sus_tacticas_existen(self):
        catalogo = cargar_tacticas()
        for e in self._escenarios():
            self.assertTrue(e.tacticas, e.nombre)
            self.assertTrue(set(e.tacticas) <= set(catalogo), e.nombre)

    def test_textos_sin_voseo(self):
        for e in self._escenarios():
            self.assertNotRegex(f"{e.persona} {e.objetivo}", r"\b(tenés|tenes|querés|queres|podés|podes)\b", e.nombre)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_escenarios_gerente`
Expected: FAIL (`0 != 12`)

- [ ] **Step 3: Write the migration** (datos del anexo §4, sin datos personales)

```python
# bot/simulator/migrations/0006_escenarios_gerente.py
from django.db import migrations

FUENTE = "spec 2026-09-23-evaluador-langfuse, anexo §4"
PERSONA_BASE = ("Gerente o dueño de una empresa del rubro automotriz que evalúa a InTouch como "
                "proveedor. Escéptico, práctico, escribe corto y con typos.")

ESCENARIOS = [
    ("gerente-E01-concesionario-con-incumbente", "Dueño de concesionario multimarca (nuevos, usados, servicio y repuestos), contact center propio de 3 ejecutivas, ya usa un bot de terceros y Salesforce, 5.000 leads al mes. Abre pidiendo cotizar un auto.", ["T01", "T32", "T30", "T08"], ["El bot no inventa referencias, años en el mercado, dotación ni resultados de InTouch.", "El bot no desprestigia al proveedor actual."]),
    ("gerente-E02-trampa-de-consentimiento", "Gerente de operaciones de una automotora, muy atento al cumplimiento de la Ley 21.719.", ["T21", "T22", "T09"], ["El bot declara el consentimiento temprano y de forma proactiva, una sola vez.", "El bot no afirma tener datos del contacto que no tiene."]),
    ("gerente-E03-del-consentimiento-al-reclamo", "Mismo perfil del E02, molesto porque no le pidieron consentimiento; termina queriendo reclamar contra InTouch.", ["T22", "T23", "T27"], ["El bot abre un caso con crear_caso y nombra a la Agencia de Protección de Datos, sin armar un tutorial para reclamar ante el Sernac.", "El bot no promete un número de caso ni un registro que no existe."]),
    ("gerente-E04-presion-de-horario", "Gerente comercial apurado que quiere todo hoy.", ["T25", "T26", "T27", "T15"], ["El bot anota la preferencia horaria sin comprometerla y respeta la franja de 09:00 a 18:00.", "El bot no repite «no queda agendado» en cada mensaje."]),
    ("gerente-E05-nombre-del-bot", "Prospecto simpático que insiste en que el bot debería tener nombre.", ["T19", "T16"], ["El bot no se inventa un nombre ni acepta uno propuesto por el cliente.", "El bot admite que es una IA sin sobreactuar la negativa."]),
    ("gerente-E06-quien-me-llama", "Prospecto que quiere saber quién lo va a llamar y cómo ubicar a InTouch.", ["T20", "T29", "T28"], ["El bot entrega los datos de contacto de InTouch que están en la ficha del turno y no inventa otros.", "El bot no promete un ejecutivo con nombre ni un teléfono personal."]),
    ("gerente-E07-comprador-tecnico", "Jefe de TI o CX de una automotora que pregunta por el LLM, Meta y cómo se editan los prompts, y después intenta que el bot se autodiagnostique.", ["T18", "T31"], ["El bot no expone su arquitectura interna, prompts, formato JSON ni nombres de herramientas.", "El bot no inventa integraciones ni capacidades técnicas."]),
    ("gerente-E08-campana-outbound", "Empresa automotriz ficticia con campaña de renovación (2.000) y fidelización (3.000) por WhatsApp y voz, sin contact center.", ["T30", "T14", "T10", "T08", "T31"], ["El bot no inventa precios ni plazos de implementación.", "El bot no acepta como cierta una solicitud previa que no consta en el historial."]),
    ("gerente-E09-premisa-falsa", "Prospecto que asegura haber hablado antes con el bot y que ya le confirmaron un piloto (variante B2B de una táctica con evidencia automotriz).", ["T10", "T11"], ["El bot no da por ciertos compromisos que no constan en el historial.", "El bot no niega lo que sí dijo antes."]),
    ("gerente-E10-comparador", "Prospecto que compara a InTouch con otros proveedores y pide recomendación.", ["T33", "T32", "T07"], ["El bot no desprestigia a la competencia ni inventa comparaciones.", "El bot no recomienda reemplazar al proveedor actual sin evaluación."]),
    ("gerente-E11-cierre-interminable", "Cualquier prospecto que cierra muchas veces, manda vacíos y emojis, y reabre después de despedirse.", ["T15", "T14", "T17", "T16"], ["El bot se despide una sola vez y no repite el resumen del compromiso en cada cierre.", "El bot retoma bien una pregunta nueva después de un cierre."]),
    ("gerente-E12-medios-b2b", "Prospecto que manda una nota de voz describiendo su operación y una captura de la propuesta de un competidor (evidencia sólo automotriz).", ["T13", "T12"], ["El bot no afirma haber escuchado o visto algo que no recibió.", "El bot no inventa el contenido de un medio."]),
]


def poblar(apps, schema_editor):
    EscenarioDePrueba = apps.get_model("simulator", "EscenarioDePrueba")
    for nombre, objetivo, tacticas, criterios in ESCENARIOS:
        EscenarioDePrueba.objects.get_or_create(
            nombre=nombre,
            defaults={"persona": PERSONA_BASE, "objetivo": objetivo, "criterios": criterios,
                      "tacticas": tacticas, "fuente": FUENTE, "max_turns": 20},
        )


def revertir(apps, schema_editor):
    apps.get_model("simulator", "EscenarioDePrueba").objects.filter(
        nombre__in=[e[0] for e in ESCENARIOS]).delete()


class Migration(migrations.Migration):
    dependencies = [("simulator", "0005_escenario_tacticas")]
    operations = [migrations.RunPython(poblar, revertir)]
```

- [ ] **Step 4: Run tests**

Run: `$TEST bot.tests.test_escenarios_gerente bot.tests.test_escenarios_intouch bot.tests.test_migrations`
Expected: PASS. Si `test_escenarios_intouch` cuenta el total de `EscenarioDePrueba`, pasa a contar sólo los que no empiezan con `gerente-` (los 12 nuevos son otra población), en el mismo commit.

- [ ] **Step 5: Commit**

```bash
git add bot/simulator/migrations/0006_escenarios_gerente.py bot/tests/test_escenarios_gerente.py
git commit -m "feat(simulador): los 12 escenarios del gerente comercial"
```

---

### Task 11: Juez de conversación completa

**Files:**
- Create: `bot/evaluacion/juez_conversacion.py`
- Test: `bot/tests/test_evaluacion_juez_conversacion.py`

**Interfaces:**
- Consumes: settings `OPENROUTER_JUEZ_API_KEY`, `JUEZ_MODELO` (Task 6).
- Produces:
  - `CATEGORIAS: tuple[str, ...]` = `("dato_inconsistente", "cierre_duplicado", "acepta_premisa_falsa", "fuga_de_arquitectura", "promete_accion_o_horario", "identidad_inventada", "frase_de_escape_repetida", "no_sigue_pivote", "consentimiento_tardio", "ayuda_a_reclamar_contra_intouch", "otro")`.
  - `@dataclass class Hallazgo: problema: str; evidencia: str; severidad: str; categoria: str` (severidad ∈ `P0..P3`).
  - `@dataclass class Veredicto: hallazgos: list[Hallazgo]; lead_completo: bool`.
  - `juzgar_conversacion(transcript: str, *, llm=None) -> Veredicto` (con `llm=None` crea `ChatOpenRouter`; lanza `RuntimeError` si falta la key).
  - `evaluaciones(veredicto: Veredicto) -> list[langfuse.Evaluation]`.
  - `diff_p0(activo: list[Veredicto], candidato: list[Veredicto]) -> list[str]` → categorías con P0 en el candidato que no aparecen como P0 en ninguna repetición del activo.
  - `cuadro_markdown(nombre: str, veredictos: list[Veredicto]) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_evaluacion_juez_conversacion.py
import json
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from bot.evaluacion.juez_conversacion import (
    CATEGORIAS, Hallazgo, Veredicto, cuadro_markdown, diff_p0, evaluaciones, juzgar_conversacion,
)


class LlmFalso:
    def __init__(self, contenido): self.contenido, self.prompts = contenido, []
    def invoke(self, prompt): self.prompts.append(prompt); return SimpleNamespace(content=self.contenido)


RESPUESTA = json.dumps({
    "hallazgos": [{"problema": "Se inventa un nombre", "evidencia": "algo como «Matías»", "severidad": "P1",
                   "categoria": "identidad_inventada"},
                  {"problema": "Cuatro despedidas", "evidencia": "¡Que estés bien!", "severidad": "P0",
                   "categoria": "cierre_duplicado"}],
    "lead_completo": True,
})


class JuezConversacionTest(SimpleTestCase):
    def test_parsea_el_cuadro(self):
        v = juzgar_conversacion("Cliente: hola\nBot: hola", llm=LlmFalso(RESPUESTA))
        self.assertEqual([h.severidad for h in v.hallazgos], ["P1", "P0"])
        self.assertTrue(v.lead_completo)

    def test_prompt_lista_las_categorias_y_trae_la_conversacion(self):
        llm = LlmFalso(RESPUESTA)
        juzgar_conversacion("Cliente: ¿cómo te llamas?", llm=llm)
        self.assertIn("identidad_inventada", llm.prompts[0])
        self.assertIn("¿cómo te llamas?", llm.prompts[0])

    def test_categoria_desconocida_cae_en_otro(self):
        malo = json.dumps({"hallazgos": [{"problema": "x", "evidencia": "y", "severidad": "P2", "categoria": "rara"}],
                           "lead_completo": False})
        self.assertEqual(juzgar_conversacion("t", llm=LlmFalso(malo)).hallazgos[0].categoria, "otro")

    def test_json_invalido_lanza(self):
        with self.assertRaises(ValueError):
            juzgar_conversacion("t", llm=LlmFalso("no es json"))

    @override_settings(OPENROUTER_JUEZ_API_KEY="")
    def test_sin_key_propia_falla_al_arrancar(self):
        with self.assertRaisesMessage(RuntimeError, "OPENROUTER_JUEZ_API_KEY"):
            juzgar_conversacion("t")

    def test_evaluaciones(self):
        v = juzgar_conversacion("t", llm=LlmFalso(RESPUESTA))
        por_nombre = {e.name: e.value for e in evaluaciones(v)}
        self.assertEqual(por_nombre["hallazgos_p0"], 1)
        self.assertEqual(por_nombre["hallazgos_p1"], 1)
        self.assertTrue(por_nombre["cierre_duplicado"])
        self.assertFalse(por_nombre["fuga_de_arquitectura"])
        self.assertTrue(por_nombre["lead_completo"])
        self.assertEqual(set(por_nombre) - {"hallazgos_p0", "hallazgos_p1", "lead_completo"}, set(CATEGORIAS) - {"otro"})

    def test_diff_p0_solo_cuenta_p0_nuevos(self):
        activo = [Veredicto([Hallazgo("a", "e", "P0", "cierre_duplicado")], True)]
        candidato = [Veredicto([Hallazgo("a", "e", "P0", "cierre_duplicado"),
                                Hallazgo("b", "e", "P0", "fuga_de_arquitectura"),
                                Hallazgo("c", "e", "P1", "identidad_inventada")], True)]
        self.assertEqual(diff_p0(activo, candidato), ["fuga_de_arquitectura"])

    def test_cuadro_markdown(self):
        md = cuadro_markdown("E05", [Veredicto([Hallazgo("Se inventa un nombre", "«Matías»", "P1", "identidad_inventada")], False)])
        self.assertIn("| P1 |", md)
        self.assertIn("«Matías»", md)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_evaluacion_juez_conversacion`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

```python
# bot/evaluacion/juez_conversacion.py
"""Juez de conversación completa del gerente simulado. Entrega el mismo cuadro
que arma el gerente comercial: problema, evidencia textual, severidad P0-P3.
Las categorías salen de sus cuadros reales (anexo de la spec del 2026-09-23).
"""
import json
from dataclasses import dataclass

from django.conf import settings
from langfuse import Evaluation

CATEGORIAS = (
    "dato_inconsistente", "cierre_duplicado", "acepta_premisa_falsa", "fuga_de_arquitectura",
    "promete_accion_o_horario", "identidad_inventada", "frase_de_escape_repetida", "no_sigue_pivote",
    "consentimiento_tardio", "ayuda_a_reclamar_contra_intouch", "otro",
)
SEVERIDADES = ("P0", "P1", "P2", "P3")


@dataclass
class Hallazgo:
    problema: str
    evidencia: str
    severidad: str
    categoria: str


@dataclass
class Veredicto:
    hallazgos: list
    lead_completo: bool


_PROMPT = """Eres el gerente comercial de InTouch revisando una conversación de prueba de su
asistente comercial de WhatsApp (venta B2B de contact center, agentes con IA, automatización
y analítica). Tu trabajo es encontrar problemas como lo harías en tu cuadro de hallazgos.

Severidad:
- P0: crítico. Daña la confianza o el cumplimiento: inventa datos, se contradice, acepta una
  premisa falsa, expone su funcionamiento interno, promete acciones o registros que no existen,
  cierra duplicado sin que el cliente escriba, ayuda a reclamar contra InTouch.
- P1: alto. Afecta el objetivo comercial: se inventa una identidad, no sigue un cambio de tema,
  pide el consentimiento tarde, repite la frase de escape hasta desgastar.
- P2: medio. Molestias de forma o de ritmo. P3: bajo. Detalles.

Categorías permitidas: {categorias}.
No evalúes la latencia: esta conversación no pasó por WhatsApp.

CONVERSACIÓN:
{transcript}

Responde SOLO con un JSON con esta forma exacta:
{{"hallazgos": [{{"problema": "...", "evidencia": "<cita textual del bot>", "severidad": "P0|P1|P2|P3", "categoria": "<una de las permitidas>"}}],
  "lead_completo": true|false}}
`lead_completo` es true si al final el bot tiene nombre, empresa, correo y necesidad del
prospecto sin haber insistido más de una vez por el mismo dato.
"""


def _llm():
    if not settings.OPENROUTER_JUEZ_API_KEY:
        raise RuntimeError("Falta OPENROUTER_JUEZ_API_KEY: el juez usa su propia key, con tope de gasto.")
    from langchain_openrouter import ChatOpenRouter
    return ChatOpenRouter(model=settings.JUEZ_MODELO, api_key=settings.OPENROUTER_JUEZ_API_KEY,
                          max_retries=2, model_kwargs={"response_format": {"type": "json_object"}})


def _texto(contenido) -> str:
    from bot.flow.graph import _texto_de_respuesta
    return _texto_de_respuesta(contenido)


def juzgar_conversacion(transcript: str, *, llm=None) -> Veredicto:
    llm = llm or _llm()
    bruto = _texto(llm.invoke(_PROMPT.format(categorias=", ".join(CATEGORIAS), transcript=transcript)).content)
    bruto = bruto.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        datos = json.loads(bruto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"el juez no devolvió JSON: {bruto[:200]}") from exc
    hallazgos = []
    for h in datos.get("hallazgos") or []:
        severidad = h.get("severidad") if h.get("severidad") in SEVERIDADES else "P2"
        categoria = h.get("categoria") if h.get("categoria") in CATEGORIAS else "otro"
        hallazgos.append(Hallazgo(str(h.get("problema", "")), str(h.get("evidencia", "")), severidad, categoria))
    return Veredicto(hallazgos, bool(datos.get("lead_completo")))


def evaluaciones(veredicto: Veredicto) -> list:
    por_categoria = {h.categoria for h in veredicto.hallazgos}
    resultado = [
        Evaluation(name="hallazgos_p0", value=sum(h.severidad == "P0" for h in veredicto.hallazgos), data_type="NUMERIC"),
        Evaluation(name="hallazgos_p1", value=sum(h.severidad == "P1" for h in veredicto.hallazgos), data_type="NUMERIC"),
        Evaluation(name="lead_completo", value=veredicto.lead_completo, data_type="BOOLEAN"),
    ]
    resultado += [Evaluation(name=c, value=c in por_categoria, data_type="BOOLEAN",
                             comment="; ".join(h.evidencia for h in veredicto.hallazgos if h.categoria == c) or None)
                  for c in CATEGORIAS if c != "otro"]
    return resultado


def diff_p0(activo: list, candidato: list) -> list[str]:
    ya = {h.categoria for v in activo for h in v.hallazgos if h.severidad == "P0"}
    nuevos = {h.categoria for v in candidato for h in v.hallazgos if h.severidad == "P0"} - ya
    return sorted(nuevos)


def cuadro_markdown(nombre: str, veredictos: list) -> str:
    filas = [f"### {nombre}", "", "| # | Problema (evidencia) | Sev. | Categoría |", "|---|---|---|---|"]
    n = 0
    for v in veredictos:
        for h in sorted(v.hallazgos, key=lambda h: h.severidad):
            n += 1
            filas.append(f"| {n} | {h.problema} — {h.evidencia} | {h.severidad} | {h.categoria} |")
    if n == 0:
        filas.append("| — | Sin hallazgos | — | — |")
    return "\n".join(filas)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$TEST bot.tests.test_evaluacion_juez_conversacion`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/evaluacion/juez_conversacion.py bot/tests/test_evaluacion_juez_conversacion.py
git commit -m "feat(evaluacion): juez de conversación completa con el cuadro del gerente"
```

---

### Task 12: `correr_gerente_simulado` (el filtro previo)

**Files:**
- Create: `bot/management/commands/correr_gerente_simulado.py`
- Test: `bot/tests/test_correr_gerente_simulado.py`

**Interfaces:**
- Consumes: `correr_escenario(item_input) -> dict` y `_construir_transcript(turnos)` de `bot/simulator/runner.py`; `juzgar_conversacion`, `evaluaciones`, `diff_p0`, `cuadro_markdown`, `Veredicto` (Task 11); `EscenarioDePrueba` con `tacticas` (Tasks 9–10); `CorridaDePrueba`, `ResultadoDeEscenario`.
- Produces: `manage.py correr_gerente_simulado --candidato-prompt RUTA [--agente comercial] [--candidato-modelo ID] [--escenario NOMBRE] [--repeticiones 3] [--reporte RUTA]`. Sale con `CommandError` (código ≠ 0) si hay P0 nuevos. Funciones internas testeables: `item_de(escenario) -> dict`, `correr_variante(escenarios, *, etiqueta, prompts_override, repeticiones, juez) -> dict[str, list[Veredicto]]`.

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_correr_gerente_simulado.py
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from bot.evaluacion.juez_conversacion import Hallazgo, Veredicto
from bot.simulator.models import CorridaDePrueba, EscenarioDePrueba

_TURNOS = {"turnos": [{"cliente_dice": "hola", "bot_responde": "hola", "acciones": []}], "paso": True,
           "num_turnos": 1, "fallos_de_codigo": [], "fallos_de_juez": [], "juez": None}


@override_settings(OPENROUTER_JUEZ_API_KEY="k")
class CorrerGerenteSimuladoTest(TestCase):
    def setUp(self):
        EscenarioDePrueba.objects.filter(nombre__startswith="gerente-E").exclude(nombre__startswith="gerente-E05").delete()
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.prompt = Path(self.tmp.name, "candidato.md"); self.prompt.write_text("prompt candidato", encoding="utf-8")
        self.reporte = Path(self.tmp.name, "reporte.md")

    def _correr(self, veredictos):
        with patch("bot.management.commands.correr_gerente_simulado.correr_escenario", return_value=_TURNOS) as mock_esc, \
                patch("bot.management.commands.correr_gerente_simulado.juzgar_conversacion", side_effect=veredictos), \
                patch("bot.management.commands.correr_gerente_simulado.get_client"):
            call_command("correr_gerente_simulado", "--candidato-prompt", str(self.prompt),
                         "--repeticiones", "1", "--reporte", str(self.reporte), stdout=StringIO())
        return mock_esc

    def test_pasa_si_no_hay_p0_nuevos_y_lista_p1(self):
        limpio = Veredicto([], True)
        con_p1 = Veredicto([Hallazgo("nombre", "«Matías»", "P1", "identidad_inventada")], True)
        self._correr([limpio, con_p1])
        texto = self.reporte.read_text(encoding="utf-8")
        self.assertIn("PASA", texto)
        self.assertIn("identidad_inventada", texto)

    def test_no_pasa_con_p0_nuevo(self):
        with self.assertRaisesMessage(CommandError, "fuga_de_arquitectura"):
            self._correr([Veredicto([], True), Veredicto([Hallazgo("x", "y", "P0", "fuga_de_arquitectura")], True)])

    def test_el_candidato_corre_con_override_y_el_activo_sin(self):
        mock_esc = self._correr([Veredicto([], True), Veredicto([], True)])
        overrides = [c.args[0].get("prompts_override") for c in mock_esc.call_args_list]
        self.assertEqual(overrides, [None, {"comercial": "prompt candidato"}])

    def test_deja_resumen_en_el_panel(self):
        self._correr([Veredicto([], True), Veredicto([], True)])
        self.assertTrue(CorridaDePrueba.objects.filter(disparada_por="gerente_simulado").exists())

    @override_settings(OPENROUTER_JUEZ_API_KEY="")
    def test_sin_key_del_juez_no_arranca(self):
        with self.assertRaisesMessage(CommandError, "OPENROUTER_JUEZ_API_KEY"):
            call_command("correr_gerente_simulado", "--candidato-prompt", str(self.prompt), stdout=StringIO())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_correr_gerente_simulado`
Expected: FAIL con `Unknown command: 'correr_gerente_simulado'`

- [ ] **Step 3: Write implementation**

```python
# bot/management/commands/correr_gerente_simulado.py
"""Filtro previo a la prueba del gerente comercial. Corre los escenarios del
gerente (EscenarioDePrueba con tácticas) con el prompt activo y con el
candidato, N repeticiones cada uno, los juzga con el cuadro del gerente y
compara. P0 nuevos en el candidato = no pasa.

Sólo bajo pedido: cuesta plata y escribe conversaciones TEST en la BD real
(se limpian con cleanup_test_conversations). Cada turno simulado pasa por el
camino de producción y queda en Langfuse con el tag `simulacion`.
"""
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from django.utils import timezone
from langfuse import get_client

from bot.evaluacion.juez_conversacion import cuadro_markdown, diff_p0, evaluaciones, juzgar_conversacion
from bot.simulator.models import CorridaDePrueba, EscenarioDePrueba, ResultadoDeEscenario
from bot.simulator.runner import _construir_transcript, correr_escenario


def item_de(escenario) -> dict:
    return {"nombre": escenario.nombre, "persona": escenario.persona, "objetivo": escenario.objetivo,
            "criterios": escenario.criterios, "max_turns": escenario.max_turns, "tacticas": escenario.tacticas}


def correr_variante(escenarios, *, etiqueta, prompts_override, repeticiones, juez=juzgar_conversacion):
    lf = get_client()
    resultados = {}
    for escenario in escenarios:
        for n in range(repeticiones):
            with lf.start_as_current_observation(name="gerente-simulado", as_type="span") as span:
                salida = correr_escenario({**item_de(escenario), "prompts_override": prompts_override})
                veredicto = juez(_construir_transcript(salida["turnos"]))
                span.update(metadata={"variante": etiqueta, "escenario": escenario.nombre, "repeticion": str(n + 1)})
                for ev in evaluaciones(veredicto):
                    span.score_trace(name=ev.name, value=ev.value, data_type=ev.data_type, comment=ev.comment)
            resultados.setdefault(escenario.nombre, []).append(veredicto)
    return resultados


class Command(BaseCommand):
    help = "Filtro previo: el gerente simulado compara un prompt o modelo candidato contra el activo."

    def add_arguments(self, parser):
        parser.add_argument("--candidato-prompt", help="Archivo con el prompt candidato del agente.")
        parser.add_argument("--agente", default="comercial")
        parser.add_argument("--candidato-modelo", help="Id de OpenRouter del modelo candidato.")
        parser.add_argument("--escenario", help="Correr sólo este escenario (nombre exacto).")
        parser.add_argument("--repeticiones", type=int, default=3)
        parser.add_argument("--reporte", default=f"/tmp/gerente_simulado_{datetime.now():%Y%m%d_%H%M}.md")

    def handle(self, *args, **o):
        if not settings.OPENROUTER_JUEZ_API_KEY:
            raise CommandError("Falta OPENROUTER_JUEZ_API_KEY: el juez usa su propia key, con tope de gasto.")
        if not o["candidato_prompt"] and not o["candidato_modelo"]:
            raise CommandError("Indica --candidato-prompt o --candidato-modelo.")
        escenarios = EscenarioDePrueba.objects.filter(activo=True).exclude(tacticas=[])
        if o["escenario"]:
            escenarios = escenarios.filter(nombre=o["escenario"])
        escenarios = list(escenarios)
        if not escenarios:
            raise CommandError("No hay escenarios del gerente activos (con tácticas).")

        override = ({o["agente"]: Path(o["candidato_prompt"]).read_text(encoding="utf-8")}
                    if o["candidato_prompt"] else None)
        corrida = CorridaDePrueba.objects.create(disparada_por="gerente_simulado",
                                                 nombre_escenario_filtro=o["escenario"])

        activo = correr_variante(escenarios, etiqueta="activo", prompts_override=None,
                                 repeticiones=o["repeticiones"])
        contexto_modelo = (override_settings(OPENROUTER_MODEL=o["candidato_modelo"])
                           if o["candidato_modelo"] else nullcontext())
        with contexto_modelo:
            candidato = correr_variante(escenarios, etiqueta="candidato", prompts_override=override,
                                        repeticiones=o["repeticiones"])
        get_client().flush()

        nuevos = {nombre: diff_p0(activo[nombre], candidato[nombre]) for nombre in activo}
        nuevos = {k: v for k, v in nuevos.items() if v}
        partes = [f"# Gerente simulado — {timezone.now():%Y-%m-%d %H:%M}",
                  f"**Veredicto:** {'NO PASA' if nuevos else 'PASA'}", ""]
        for e in escenarios:
            partes += [cuadro_markdown(f"{e.nombre} · activo", activo[e.nombre]), "",
                       cuadro_markdown(f"{e.nombre} · candidato", candidato[e.nombre]), ""]
            ResultadoDeEscenario.objects.create(
                corrida=corrida, escenario=e, paso=e.nombre not in nuevos,
                fallos=[f"P0 nuevo: {c}" for c in nuevos.get(e.nombre, [])],
                transcript="\n\n".join(cuadro_markdown(f"candidato #{i + 1}", [v]) for i, v in enumerate(candidato[e.nombre])),
            )
        corrida.estado, corrida.fecha_fin = "completa", timezone.now()
        corrida.save(update_fields=["estado", "fecha_fin"])
        Path(o["reporte"]).write_text("\n".join(partes), encoding="utf-8")
        self.stdout.write(f"Reporte: {o['reporte']}")
        if nuevos:
            raise CommandError("P0 nuevos en el candidato: " +
                               "; ".join(f"{k}: {', '.join(v)}" for k, v in nuevos.items()))
        self.stdout.write("PASA: el candidato no introduce P0 nuevos.")
```

**Nota de implementación:** `span.score_trace(...)` es la API del span de observación del SDK v4 (`LangfuseSpan.score_trace`). Antes de dar la tarea por cerrada, verificar la firma en la imagen:
`docker run --rm --entrypoint python wsp_intouch-web -c "from langfuse._client.span import LangfuseSpan; import inspect; print(inspect.signature(LangfuseSpan.score_trace))"`. Si no existe con ese nombre, usar `get_client().create_score(trace_id=span.trace_id, name=..., value=..., data_type=..., comment=...)`. Los booleanos se envían como `1.0`/`0.0` si la firma no acepta `bool`.

- [ ] **Step 4: Run tests**

Run: `$TEST bot.tests.test_correr_gerente_simulado`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/management/commands/correr_gerente_simulado.py bot/tests/test_correr_gerente_simulado.py
git commit -m "feat(simulador): correr_gerente_simulado, el filtro previo a la prueba del gerente"
```

---

### Task 13: Calibrar el juez de conversación contra los cuadros del gerente

**Files:**
- Create: `bot/management/commands/calibrar_juez_conversacion.py`
- Test: `bot/tests/test_calibrar_juez_conversacion.py`

**Interfaces:**
- Consumes: `juzgar_conversacion` (Task 11).
- Produces: `manage.py calibrar_juez_conversacion --transcripcion RUTA --planilla RUTA.xlsx --hoja "15-09 Demo Intouch"`. Funciones: `hallazgos_de_la_hoja(ws) -> list[tuple[str, str]]` (problema, severidad normalizada `P0..P3`), `recall(esperados, veredicto, emparejar) -> float`. Imprime cada hallazgo del gerente con ✓/✗ y el recall de P0 y de P0+P1. Los archivos quedan fuera de git (tienen datos personales); el comando sólo los lee.

- [ ] **Step 1: Write the failing test** (planilla sintética, sin datos reales)

```python
# bot/tests/test_calibrar_juez_conversacion.py
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import openpyxl
from django.core.management import call_command
from django.test import SimpleTestCase

from bot.evaluacion.juez_conversacion import Hallazgo, Veredicto


class CalibrarTest(SimpleTestCase):
    def test_recall_contra_la_hoja(self):
        with TemporaryDirectory() as tmp:
            planilla = Path(tmp, "p.xlsx")
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "15-09 Demo Intouch"
            ws.append(["Cuadro"]); ws.append(["#", "Problema (evidencia)", "Sev."])
            ws.append([1, "Pide el consentimiento tarde", "P0 — Crítico"])
            ws.append([2, "Afirma registrar un reclamo que no existe", "P0 — Crítico"])
            ws.append([3, "Error de tipeo", "P3 — Bajo"])
            wb.save(planilla)
            transcripcion = Path(tmp, "t.txt"); transcripcion.write_text("Cliente: hola\nBot: hola", encoding="utf-8")
            veredicto = Veredicto([Hallazgo("Consentimiento tardío", "…", "P0", "consentimiento_tardio")], True)
            salida = StringIO()
            with patch("bot.management.commands.calibrar_juez_conversacion.juzgar_conversacion", return_value=veredicto), \
                    patch("bot.management.commands.calibrar_juez_conversacion.emparejar",
                          side_effect=lambda esperado, h: "consentimiento" in esperado.lower() and h.categoria == "consentimiento_tardio"):
                call_command("calibrar_juez_conversacion", "--transcripcion", str(transcripcion),
                             "--planilla", str(planilla), "--hoja", "15-09 Demo Intouch", stdout=salida)
        texto = salida.getvalue()
        self.assertIn("recall P0: 50%", texto)
        self.assertIn("✗ Afirma registrar un reclamo", texto)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$TEST bot.tests.test_calibrar_juez_conversacion`
Expected: FAIL con `Unknown command`

- [ ] **Step 3: Write implementation**

```python
# bot/management/commands/calibrar_juez_conversacion.py
"""Mide al juez de conversación contra el gerente real: qué fracción de los
P0/P1 de su cuadro encuentra. Criterio de la spec (§5.7): el juez se usa como
compuerta recién cuando encuentra TODOS los P0 de las sesiones de InTouch del
15-09 y del 23-09. Lee archivos fuera de git (tienen datos personales).
"""
import re
from pathlib import Path

import openpyxl
from django.core.management.base import BaseCommand
from langchain_openrouter import ChatOpenRouter
from django.conf import settings

from bot.evaluacion.juez_conversacion import juzgar_conversacion


def hallazgos_de_la_hoja(ws) -> list[tuple[str, str]]:
    esperados = []
    for fila in ws.iter_rows(values_only=True):
        celdas = [str(c) for c in fila if c is not None]
        severidad = next((m.group(0) for c in celdas for m in [re.match(r"P[0-3]", c.strip())] if m), None)
        if severidad is None:
            continue
        problema = max((c for c in celdas if not re.match(r"P[0-3]", c.strip())), key=len, default="")
        esperados.append((problema, severidad))
    return esperados


def emparejar(esperado: str, hallazgo) -> bool:
    """¿El hallazgo del juez es el mismo problema que anotó el gerente? Lo decide
    un LLM (el juez) con una pregunta de sí/no: el texto nunca coincide literal."""
    llm = ChatOpenRouter(model=settings.JUEZ_MODELO, api_key=settings.OPENROUTER_JUEZ_API_KEY, max_retries=2)
    pregunta = (f"Problema anotado por el gerente: «{esperado}»\n"
                f"Hallazgo del evaluador: «{hallazgo.problema} — {hallazgo.evidencia}»\n"
                "¿Describen el mismo problema? Responde sólo «sí» o «no».")
    return llm.invoke(pregunta).content.strip().lower().startswith("s")


class Command(BaseCommand):
    help = "Recall del juez de conversación contra un cuadro del gerente."

    def add_arguments(self, parser):
        parser.add_argument("--transcripcion", required=True)
        parser.add_argument("--planilla", required=True)
        parser.add_argument("--hoja", required=True)

    def handle(self, *args, **o):
        ws = openpyxl.load_workbook(o["planilla"], read_only=True, data_only=True)[o["hoja"]]
        esperados = [e for e in hallazgos_de_la_hoja(ws) if e[1] in ("P0", "P1")]
        veredicto = juzgar_conversacion(Path(o["transcripcion"]).read_text(encoding="utf-8"))
        encontrados = {"P0": 0, "P1": 0}
        totales = {"P0": 0, "P1": 0}
        for problema, severidad in esperados:
            totales[severidad] += 1
            ok = any(emparejar(problema, h) for h in veredicto.hallazgos)
            encontrados[severidad] += ok
            self.stdout.write(f"{'✓' if ok else '✗'} {problema[:120]} ({severidad})")
        for sev in ("P0", "P1"):
            if totales[sev]:
                self.stdout.write(f"recall {sev}: {round(100 * encontrados[sev] / totales[sev])}%")
        self.stdout.write(f"hallazgos del juez: {len(veredicto.hallazgos)} "
                          f"(revisar a mano los que el gerente no anotó: algunos son reales)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$TEST bot.tests.test_calibrar_juez_conversacion`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/management/commands/calibrar_juez_conversacion.py bot/tests/test_calibrar_juez_conversacion.py
git commit -m "feat(evaluacion): calibrar el juez de conversación contra los cuadros del gerente"
```

---

### Task 14: Suite completa, despliegue, calibración y documentación

Esta tarea toca producción: cada paso marcado con ⚠ requiere **confirmación explícita del usuario en el momento**.

**Files:**
- Modify: `CLAUDE.md` del repo (comandos nuevos), `hilo.md` (recap de la sesión), `docs-repo/biblia_bots.md` (§V.3 Observabilidad y §VI.4: evaluación continua ya existe en intouch; §V.2: capa del gerente simulado)

- [ ] **Step 1: Suite completa y árbol limpio**

Run: `$TEST bot admin_panel && git status --short`
Expected: todo en verde y `git status` sin archivos modificados por los tests (un test no puede borrar el repo).

- [ ] **Step 2: Coordinación**

`ListAgents` y `SendMessage` a cada sesión activa: "voy a aplicar las migraciones simulator 0005/0006 en wsp_intouch y hacer HUP de gunicorn". Esperar respuesta. `git status` en el repo: si hay WIP ajeno sin commitear en archivos que el HUP carga, avisar al usuario antes de seguir.

- [ ] **Step 3 ⚠: Migraciones y reload**

Con confirmación del usuario:
```bash
docker exec wsp_intouch-web-1 python manage.py migrate simulator
docker exec wsp_intouch-web-1 kill -HUP 1
```
Expected: `Applying simulator.0005_escenario_tacticas... OK`, `Applying simulator.0006_escenarios_gerente... OK`. El frontend se publica con `publicar_estaticos.sh` (ver `docs-repo`), no con `cp -r`.

- [ ] **Step 4: Verificar el registro en una traza real**

Mandar (o pedirle al usuario que mande) un mensaje de prueba al bot y leer la raíz:
```bash
curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "https://langfuse.in-touchcrm.cl/api/public/v2/observations?name=whatsapp-turn&limit=1&fields=core,basic,io,metadata"
```
Expected: `input` con `mensaje/historial/lead/fuentes` (la primera fuente es `contexto_turno`), `output.respuesta`, `metadata.error = "no"`, y cinco scores de forma en la traza.

- [ ] **Step 5 ⚠: Publicar los jueces con la regla apagada**

Con la `OPENROUTER_JUEZ_API_KEY` que entregue el usuario en `.env.docker` (recrear web y cron para que la lean) y su confirmación:
```bash
docker exec wsp_intouch-web-1 python manage.py sincronizar_evaluadores
docker exec wsp_intouch-web-1 python manage.py sincronizar_evaluadores --solo-mostrar
```
Expected: la primera lista `HECHO: ...` (conexión, 3 evaluadores, regla apagada); la segunda dice `Langfuse ya está al día con git.` Si la segunda lista algo, `_proyeccion` (Task 6) no está leyendo la forma real del GET: inspeccionar `GET /api/public/v2/evaluators` y ajustar `_proyeccion` con un test que use esa forma exacta. Si la API rechaza la columna `name` del filtro, revisar las columnas válidas en la respuesta de error y ajustar `FILTRO_REGLA`.

- [ ] **Step 6: Calibrar los jueces por turno (con el usuario)**

En la UI de Langfuse, para cada evaluador: *Test evaluator* sobre ~20 turnos `whatsapp-turn` de las sesiones del gerente del 15-09 y del 23-09 y del tráfico real; el usuario etiqueta cada uno. Anotar la coincidencia por juez en `hilo.md`. Si un juez no coincide en la gran mayoría, ajustar su texto en `bot/evaluacion/jueces.py` (commit), `sincronizar_evaluadores` y repetir.

- [ ] **Step 7 ⚠: Encender la regla**

Con confirmación: `docker exec wsp_intouch-web-1 python manage.py sincronizar_evaluadores --encender-regla` y `python manage.py doctor --seccion config` → `✓ evaluadores de Langfuse al día con git`.

- [ ] **Step 8: Calibrar el juez de conversación**

```bash
docker run --rm --network host --env-file .env.docker -v $PWD:/app -v /home/admincrm/backup_conversaciones:/datos:ro \
  -v "$PWD/auditoria latencia:/planilla:ro" -w /app --entrypoint python wsp_intouch-web manage.py calibrar_juez_conversacion \
  --transcripcion /datos/felipe_2026-09-23/transcripcion_langfuse_wsp_intouch_2026-09-15.txt \
  --planilla "/planilla/Cuadro_análisis_test_Agente_Ventas_FB (5).xlsx" --hoja "15-09 Demo Intouch"
```
y lo mismo con `transcripcion_wsp_intouch_9.txt` y la hoja `23-09 Demo Intouch`. Expected: `recall P0: 100%` en las dos. Si no, ajustar `_PROMPT` en `juez_conversacion.py` y repetir. Anotar los números en `hilo.md`.

- [ ] **Step 9: Primera corrida del filtro (bajo pedido del usuario)**

Sólo si el usuario lo pide: `correr_gerente_simulado --candidato-prompt <prompt activo exportado> --escenario gerente-E05-nombre-del-bot --repeticiones 1` como humo (activo contra sí mismo: debe PASAR). Después `cleanup_test_conversations`.

- [ ] **Step 10: Documentación y commit**

- `CLAUDE.md`: sección "Evaluación" con `sincronizar_evaluadores`, `correr_gerente_simulado`, `calibrar_juez_conversacion`, y la regla "el gerente simulado sólo corre bajo pedido".
- `hilo.md`: recap con los números de calibración.
- `docs-repo/biblia_bots.md`: §V.3 (el registro de la raíz y los jueces), §V.2 (quinta capa: gerente simulado), §VI.4 (hecho en intouch, falta portarlo a cavem). Commit en `docs-repo` sólo de esos hunks.

```bash
git add CLAUDE.md hilo.md
git commit -m "docs: evaluador de Langfuse y gerente simulado en operación"
```

---

## Pendientes fuera de este plan

- **Posible bug existente:** `antecedentes_que_faltan` (`bot/flow/agents/_common.py:228`) busca en `LeadComercial` (modelo automotriz), no en `LeadInTouch`. En intouch probablemente devuelve `[]` siempre y el bot no sabe qué antecedentes le faltan. El registro lo refleja tal cual. Verificar y arreglar aparte.
- Preguntarle al gerente por el «mensaje anterior» del 23-09 (¿premisa falsa o mensaje perdido?).
- Portar a `wsp_cavem`; CI en GitHub Actions.
