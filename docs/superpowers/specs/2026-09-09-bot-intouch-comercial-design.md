# Bot comercial B2B de InTouch — diseño

**Fecha:** 2026-09-09
**Estado:** aprobado, pendiente de plan de implementación
**Repo:** `/home/admincrm/wsp_intouch` (nuevo)
**Prompt de origen:** `/home/admincrm/wsp_demo/prompt/prompt_agente_intouch_corregido.md` (versión "2FB revisada")
**Referencia normativa del stack:** `/home/admincrm/docs-repo/biblia_bots.md`

---

## 1. Objetivo

Un bot de WhatsApp que atiende consultas comerciales B2B sobre las soluciones de
InTouch (contactabilidad, experiencia de cliente, Contact Center, automatización
y agentes conversacionales con IA), orienta al interesado, califica la
oportunidad y registra el lead para seguimiento comercial.

Es el **tercer bot** del stack y el primero que **no es automotriz**.

### Restricción de partida, dada por el usuario

Carpeta nueva, repo nuevo, **cero modificaciones a `wsp_cavem`, `wsp_demo` ni a
ningún otro bot**. "Clonar" significa copiar el árbol de archivos a
`/home/admincrm/wsp_intouch`, no refactorizar el original.

Consecuencia asumida y declarada: se descartó extraer el perfil de vertical
declarativo (biblia §VI.5), porque exigía tocar Cavem. Este bot nace entonces
como **tercer repo con el mismo grafo copiado a mano**, agravando el drift de
§VI.1 — el riesgo número uno del stack. Ver §12.1.

---

## 2. Las tres decisiones previas (biblia §I.0), respondidas

### 2.1 ¿Qué dato es estructurado y qué dato es prosa?

| Dato | Destino | Por qué |
|---|---|---|
| Soluciones de InTouch (nombre, canales, modelos de operación aplicables, si requiere evaluación técnica) | **Tabla + tool** | El guardrail "no inventes integraciones, capacidades ni certificaciones" (prompt §5) sólo es cumplible si la lista sale de una fila. Es el mismo argumento que "no inventes un precio" (§III.5) |
| Los tres modelos de operación (Humano / Híbrido / Automatizado) | **Tabla + tool** | Ídem, y el prompt los presenta como un conjunto cerrado |
| Cómo funciona cada solución, políticas, tratamiento de datos, seguridad, marco legal | **RAG** | Prosa, sin filtros numéricos ni comparaciones exactas |
| Precios, plazos de implementación, disponibilidad | **No existen** | El prompt §5 los prohíbe explícitamente. No se modelan: no hay tabla, no hay chunk, no hay tool |

Nótese lo último: este bot **no tiene precio de nada**, que es justamente lo que
hace innecesario el modelo de catálogo con tres tiers de precio que trae el clon.

### 2.2 ¿Qué especialistas necesita?

**Uno solo: `comercial`.**

`AGENTS = {"comercial": ComercialAgent()}`.

Justificación (§I.0 punto 2): un especialista visible es un especialista al que
el LLM puede rutear **mal**. "Soporte" contra "comercial" es un límite semántico
difuso —un contacto que se queja de un servicio calza con los dos— y el
precedente del stack es explícito: las dos encuestas de Renault viven en el repo
de Cavem **fuera de `AGENTS`** porque un cliente que se queja del taller calzaba
semánticamente con "encuesta de satisfacción" y el bot le empezaba a pedir notas
del 1 al 10 en vez de atender el reclamo.

El prompt de origen §1 ya le encarga al mismo agente distinguir consulta
comercial de soporte, empleo o proveedores. Esa distinción vive en el prompt y
se resuelve con la tool `crear_caso`, no en el ruteo.

Beneficio lateral medible: con un único destino, el ruteo del supervisor es
trivial.

### 2.3 ¿El vertical es "autos"?

No. Los tres puntos hardcodeados del clon se resuelven así:

| Punto hardcodeado | Qué se hace |
|---|---|
| `VehiculoCatalogo` + `VehiculoUsado` + sus tools | Se **desregistran** (no se bindean, quedan sin fuentes). Se reemplazan por `SolucionInTouch` + `ModeloOperacion` con tools propias |
| `CATEGORIAS_RAG` y su prompt de clasificación ("concesionaria de autos") | Se **reescribe** a una taxonomía de servicios B2B |
| `EXTRACTOR_PROMPT` del scraping | Se **reescribe** para el sitio institucional de InTouch |

---

## 3. Alcance

### Dentro

- Repo `wsp_intouch` completo y funcional, slug `intouch`, puerto 8040.
- Modelo de dominio propio: catálogo de soluciones, modelos de operación, lead B2B.
- Un especialista `comercial` con sus tools.
- Conocimiento del RAG: `.md` redactados desde el prompt de origen **más**
  scraping de `in-touch.cl`.
- Extractor de metadatos con el contrato de 21 campos del prompt §8.
- Despachador de lead: tabla propia + notificación `lead_hot` al orquestador +
  adaptador HTTP conmutable (arranca apagado).
- Prompt global y del especialista, adaptados (§8 de este documento).
- `doctor` verde, suite verde, golden set del RAG con recall de partida,
  escenarios del simulador, latencia de partida medida.
- Despliegue end-to-end: nginx, DIOS, panel SA, Meta, e2e con WhatsApp real.
- Corrección de la documentación que este trabajo toca (§11.3).

### Fuera

- **Endpoint HTTP de leads en el orquestador.** Es el spec B (§13).
- Borrado físico del dominio automotriz heredado. Ver §12.2.
- Extracción de `verticals/` (§VI.5 de la biblia). Ver §12.1.
- Campañas salientes y plantillas de Meta: InTouch B2B arranca sin campañas.
  Si más adelante las hay, las plantillas se crean en idioma `es`, **nunca**
  `es_CL` (Meta responde `132001`).
- Tenant routing. Ver §10.4.

---

## 4. Qué se hereda del clon, qué se desregistra y qué se borra

### 4.1 Se hereda íntegro (es el valor de clonar)

El grafo de LangGraph con su presupuesto de 70 s por turno y el tope por intento
con `asyncio.wait_for`; el canal de salida en prosa; el modelo chico de ruteo; la
cola de envío FIFO; el extractor de metadatos fuera del camino crítico; la
ventana de contexto; la percepción de imagen y audio con su deadline propio; el
RAG híbrido con RRF y rerank; `bot/rag_eval`; el simulador; el panel React con
Module Federation; el registro en DIOS; y **las 19 defensas del catálogo de modos
de falla de §IV.1**, con la cobertura de tests que las prueba.

### 4.2 Se desregistra, no se borra (fase 1)

`VehiculoUsado`, `VehiculoCatalogo`, `VehiculoPartePago`, `Reserva`, `Servicio`,
`Sucursal`, `ImagenConvertida`, `EncuestaServicioTecnico`,
`EncuestaVentaAutoNuevo`, y los especialistas `agendamiento`, `confirmacion`,
`faq`, `encuesta_*`.

Mecanismo: fuera de `AGENTS` y sin bindear sus tools. Un especialista que no
está en el dict es **invisible para el ruteo**: el riesgo de que el bot ofrezca
agendar hora de taller es cero, no bajo.

Razones:

1. Los ~1.416 tests de `bot/tests` son la red que hace que el clon herede las
   defensas de §IV.1 **con su cobertura**. Borrar los modelos rompe cientos de
   esos tests, y quedaríamos sin poder distinguir "rompí un test de autos" de
   "rompí una defensa".
2. Hay tests-guardián que dependen de esa estructura: `test_doctor.py` (lee
   `graph.py` y falla si aparece un `reasoning.effort` no declarado),
   `test_cliente_activo.py`, `test_db_schema_check.py`, `test_migrations.py`,
   y `leads/tests.py::DockerfileMigrateOrderRegressionTest` (parsea el
   Dockerfile).
3. Precedente explícito del stack: las encuestas de Renault desregistradas en
   Cavem.

Costo honesto: el repo carga código muerto y tablas vacías. Queda como deuda
declarada (§12.2) con el borrado como tramo posterior, una vez que la suite
propia de InTouch esté verde y sepamos qué tests son nuestros.

### 4.3 Se borra o se reescribe de entrada

Porque dejarlo es activamente peligroso —conocimiento y datos de otro cliente:

- `bot/management/commands/seed_cavem.py`, `importar_stock_cavem.py`
- `bot/fixtures/rag/*.md` (conocimiento de Cavem), `bot/fixtures/stock_cavem.csv`,
  `bot/fixtures/prompt_ventas.md`
- `bot/flow/global_prompt.py::SYSTEM_PROMPT`
- `bot/whatsapp/handlers.py::WELCOME_IDENTIDAD` y la lista de stopwords con "cavem"
- `bot/rag/schema_astara.sql` (residuo histórico de otra marca)
- `dios.json`, nombres del micro-frontend, `docs/DEPLOY_CAVEM.md`,
  `docs/PENDIENTES.md`, `hilo.md`, `docs/AUDITORIA_DOCX.md`,
  `docs/PLANTILLAS_META_CAVEM.md`, `bot/fixtures/prompt_calculo_credito_tradicional.txt`
- `docs/superpowers/specs/` de Cavem: **no se copian**. Este spec es el primero
  del repo.

### 4.4 Historia de git

El repo nuevo **no hereda la historia de `wsp_cavem`**: se copia el árbol de
archivos, `git init` propio, remoto propio. El primer commit deja constancia en
su mensaje de la revisión exacta de `wsp_cavem` desde la que se copió, para que
el drift de §VI.1 sea al menos rastreable.

---

## 5. Modelo de dominio

### 5.1 Catálogo de soluciones

```python
class SolucionInTouch(models.Model):
    cliente = models.CharField(choices=CLIENTE_CHOICES)   # manager filtrado
    slug = models.SlugField()
    nombre = models.CharField()
    categoria = models.CharField()          # operación / agentes IA / analítica / integración
    descripcion = models.TextField()
    canales = models.JSONField(default=list)              # whatsapp, voz, chat, email
    modelos_operacion = models.JSONField(default=list)     # humano, hibrido, automatizado
    requiere_evaluacion_tecnica = models.BooleanField(default=False)
    ejemplos_uso = models.TextField(blank=True)
    activa = models.BooleanField(default=True)
    orden = models.IntegerField(default=0)

    class Meta:
        unique_together = [("cliente", "slug")]
```

```python
class ModeloOperacion(models.Model):
    cliente = models.CharField(choices=CLIENTE_CHOICES)
    slug = models.SlugField()          # humano | hibrido | automatizado
    nombre = models.CharField()
    descripcion = models.TextField()
    cuando_aplica = models.TextField()
    orden = models.IntegerField(default=0)
```

Ambos con el manager filtrado por `settings.CLIENTE_ACTIVO` (`objects`) y el sin
filtrar (`todos_los_clientes`), igual que `Servicio` y `Sucursal`.

Contenido semilla desde el prompt §2: cinco soluciones (diseño de operación a
medida; agentes conversacionales para WhatsApp/voz/chat/email; paneles,
supervisión, dashboards y Power BI; integraciones con CRM y ERP —con
`requiere_evaluacion_tecnica=True`—; analítica conversacional y control de
calidad) y los tres modelos de operación.

`requiere_evaluacion_tecnica` no es decorativo: el prompt exige presentar las
integraciones como sujetas a evaluación técnica, y el especialista lo lee de la
fila en vez de acordarse.

**Trade-off asumido:** consultar el catálogo cuesta una ronda de tool en los
turnos donde el bot habla de soluciones. La alternativa —las cinco filas en un
bloque de prompt determinístico— es más rápida pero viola §III.3 ley 4: con el
dato en el prompt, el bot pierde la razón para usar la vía verificable y vuelve
a poder inventar. Se elige la tool, y se acepta el costo.

### 5.2 El lead

```python
class LeadInTouch(models.Model):
    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE)
    # identificación
    nombre_completo, correo, empresa, industria, subtipo_automotriz, cargo, pais_ciudad
    # diagnóstico
    situacion_contact_center     # tiene | no_tiene | null
    tipo_contact_center          # propio | externalizado | mixto | no_tiene | null
    usa_ia_actualmente           # BooleanField(null=True)
    canales_actuales             # JSONField(default=list)
    volumen_interacciones, necesidad_principal
    soluciones_interes           # JSONField(default=list)
    intencion, plazo_proyecto
    # calificación (escrita por código, no por el LLM)
    lead_score                   # HOT | WARM | COLD | NO_CALIFICADO
    solicita_consultoria         # BooleanField(default=False)
    solicita_contacto_humano     # BooleanField(default=False)
    # cierre
    resumen_conversacion, siguiente_accion_recomendada
    # trazabilidad
    creado, actualizado, notificado_en (null), despachado_en (null)
```

Son los 21 campos del contrato del prompt §8, más cuatro de trazabilidad. El
teléfono **no** es un campo del contrato: llega de los metadatos de WhatsApp y
vive en `Conversation.wa_id`. El prompt ya prohíbe pedirlo.

`subtipo_automotriz` se conserva porque el prompt de origen lo pide
explícitamente (InTouch vende al sector automotriz, entre otros), con los siete
valores de su §4.

**Reglas de escritura, heredadas del patrón `LeadComercial` de Cavem:**

1. `get_or_create(conversation=...)`: **un lead por conversación**, que se
   completa turno a turno. No uno por mensaje.
2. Un valor vacío **nunca pisa** un dato ya capturado. Si el contacto dio su
   empresa en el turno 3 y en el turno 7 el extractor no la ve, la empresa
   sobrevive.
3. Ante una corrección del contacto, prevalece el valor más reciente **no
   vacío**, según el prompt §4.
4. Todo campo de texto pasa por un recorte a la longitud de la columna, para
   evitar el `String or binary data would be truncated` de SQL Server.
5. Consistencia de Contact Center validada **en código**, no confiada al prompt:
   si `situacion_contact_center == "no_tiene"`, entonces
   `tipo_contact_center = "no_tiene"`; si es `"tiene"` y la modalidad es
   desconocida, `tipo_contact_center = None`; si no se confirmó nada, ambos
   `None`.
6. El correo se valida sólo de **formato**. Nunca se afirma que existe.

### 5.3 Las guardas anti-basura

El extractor clasifica **todos** los turnos. Sin guarda, un "hola" abre un lead
`NO_CALIFICADO` que después alguien cuenta como conversión —exactamente lo que
pasó en Cavem, donde `Campana.metricas()` contaba `LeadComercial` como
conversión.

La fila se abre **sólo** si el turno aportó al menos un antecedente que el
contacto entregó de verdad:

```
_ANTECEDENTES_QUE_ABREN_LEAD = {
    "empresa", "correo", "industria", "cargo", "pais_ciudad",
    "necesidad_principal", "situacion_contact_center", "tipo_contact_center",
    "canales_actuales", "volumen_interacciones", "usa_ia_actualmente",
    "plazo_proyecto", "soluciones_interes",
    "solicita_contacto_humano", "solicita_consultoria",
}
```

`nombre_completo` **no** abre un lead: WhatsApp entrega el nombre del perfil sin
que el contacto lo haya dado, así que no es evidencia de nada.

Las dos últimas del set abren lead **aun sin ningún otro dato**, y están ahí a
propósito: es el caso B del prompt §7 (registro parcial por solicitud explícita),
donde el prompt es taxativo en no bloquear la solicitud por falta de correo.

---

## 6. El especialista y sus tools

### 6.1 `bot/flow/agents/comercial.py`

Módulo propio con `SYSTEM_PROMPT` en código y `effective_prompt()` que devuelve
`get_active_prompt("comercial") or SYSTEM_PROMPT` — el patrón de `faq` y
`agendamiento`. **No** se usa `CustomPromptAgent`: ése no tiene fallback y si
falta la fila en `PromptVersion` el prompt sale vacío.

`PRE_ROUTING_RULES` queda vacío: sin campañas salientes no hay pre-ruteo
determinista que aplicar.

### 6.2 Tools bindeadas

| Tool | Propósito |
|---|---|
| `listar_soluciones(categoria="", canal="")` | Filas de `SolucionInTouch`, filtrables |
| `consultar_solucion(referencia)` | Ficha de una solución, con `requiere_evaluacion_tecnica` |
| `listar_modelos_operacion()` | Las tres filas de `ModeloOperacion` |
| `consultar_base_conocimiento(query)` | RAG híbrido + rerank, máximo 3 intentos por turno |
| `crear_caso(tipo, resumen)` | Deriva soporte / empleo / proveedores a un `Incident` que el operador ve |
| `registrar_no_contactar(motivo)` | Opt-out, Ley 21.719 |
| `registrar_consentimiento(otorgado, motivo)` | Consentimiento de datos |
| `responder(...)` | Contrato de salida, se bindea a todos los especialistas |

Fuera del binding: todo lo automotriz, `simular_financiamiento`,
`simular_por_cuota`, `crear_lead`, `registrar_datos_lead`, `registrar_parte_pago`,
`enviar_ficha_tecnica`, las tools de agendamiento y de encuestas.

**Todas las docstrings de tools se escriben en español correcto, con tildes.** No
es cosmética: es §III.3 ley 5, la docstring es corpus que el modelo lee e imita,
y ya se pagó una vez (un ejemplo sin tilde en un prompt le enseñó al bot a
escribir sin tildes durante seis horas contra contactos reales).

### 6.3 `CAMPOS_EXTRA_POR_AGENTE`

Los campos del contrato se declaran en `bot/flow/respuesta.py`, en un solo lugar
que genera **a la vez** el bloque de prompt y el filtro de campos permitidos, de
modo que no puedan desincronizarse (§III.6 punto 6).

---

## 7. El lead sin `submit_lead`

### 7.1 La decisión y por qué contradice al prompt de origen

El prompt de origen §7 y §9 asumen un `submit_lead` **síncrono**: el modelo lo
llama, espera el resultado y le informa al usuario "registro confirmado" /
"error" / "no puedo confirmar".

Eso choca de frente con lo que este stack ya midió: `registrar_datos_lead` fue
**desbindeada** del especialista en Cavem porque el LLM la pedía en una segunda
ronda, y esa ronda costaba **4,53 s de mediana en el 17,3 % de 324 turnos** —
0,78 s por turno amortizado. Una tool que exige una segunda ronda cuesta una
llamada entera al LLM.

**Decisión: el lead lo escribe el extractor de metadatos, después del envío.**

```
turno → especialista redacta prosa → se envía la PRIMERA parte al contacto
      → el worker se devuelve
      → [cola de envío] partes 2..N + EXTRACTOR DE METADATOS
                                        └─ registrar_lead_del_turno()
```

El extractor corre con modelo chico y `response_format: json_schema`, que
**puede** usar porque no tiene tools bindeadas (combinarlo con `tools` suprime el
tool-calling; incompatibilidad documentada aguas arriba, y además medía más
lento: 4,37 s contra 2,43 s).

### 7.2 Consecuencia: hay que reescribir §7 y §9 del prompt

El bot **no puede** decir "tu solicitud quedó registrada" en el mismo turno,
porque cuando redacta esa frase el lead todavía no se escribió. Reescritura:

- El bot acusa el **siguiente paso acordado**, sin afirmar persistencia.
- Se elimina la máquina de estados de resultados (confirmado / local / error /
  ambiguo / herramienta no disponible): el modelo ya no ve ningún resultado.
- Se conserva íntegro el criterio de **cuándo corresponde registrar** (casos A y
  B), que pasa de ser "cuándo invocar la tool" a "qué señales debe dejar
  explícitas en la conversación", porque son las que lee el extractor.
- Se conserva la prohibición de afirmar que una reunión está agendada, un correo
  fue enviado o alguien fue notificado sin una herramienta que lo confirme. Con
  el extractor fuera del turno, esa regla se vuelve **más** necesaria, no menos.

El resultado es más honesto que el original: el bot deja de prometer un registro
que no puede verificar.

### 7.3 `lead_score` lo calcula el código

El prompt §6 le pedía al modelo asignar `lead_score`. Se saca del prompt.

El extractor devuelve **señales verificables**, no el veredicto:

```
necesidad_concreta: bool          # necesidad compatible con la oferta, descrita
encaje_con_oferta: bool           # lo que pide se parece a lo que InTouch hace
interes_evaluar: bool             # quiere evaluar una solución
solicita_siguiente_paso: bool     # pidió reunión, demo, consultoría o contacto
intencion_avanzar_declarada: bool # dijo que quiere avanzar
plazo_cercano_declarado: bool     # dio un plazo cercano explícito
interes_exploratorio: bool        # interés comercial sin necesidad concreta
```

Y el código aplica la precedencia del prompt §6, en ese orden:

```python
def calcular_lead_score(s) -> str:
    if not s.encaje_con_oferta:
        return "NO_CALIFICADO"
    if s.necesidad_concreta and s.solicita_siguiente_paso and (
        s.intencion_avanzar_declarada or s.plazo_cercano_declarado
    ):
        return "HOT"
    if s.necesidad_concreta and s.interes_evaluar:
        return "WARM"
    if s.interes_exploratorio:
        return "COLD"
    return "NO_CALIFICADO"
```

Motivos: es el patrón que ya usa Cavem (`calcular_lead_score` en código); da un
score **reproducible y auditable**, testeable sin llamar al LLM; y evita que el
mismo lead salga HOT o WARM según el humor del modelo. El §6 del prompt sigue
existiendo como criterio de **qué evidencia recoger y dejar explícita**; lo que
sale del prompt es el veredicto.

`resumen_conversacion` sigue siendo del extractor: es texto, no un juicio
comparable.

### 7.4 El despachador

```
LeadInTouch (BD del bot)          ← capa 1, siempre. Fuente de verdad.
  ├─ notificación lead_hot        ← capa 2, cuando el score pasa a HOT
  └─ adaptador de salida          ← capa 3, conmutable: "none" | "http"
```

**Capa 1 no es opcional.** Si el lead se escribiera sólo afuera y el destino
externo fallara, se perdería en silencio — la falla que §IV.1 prohíbe: *un fallo
de infraestructura nunca puede parecer un dato vacío*.

**Capa 2 — notificación.** Verificado: el subsistema del orquestador es real y
está en producción, y `wsp_pompeyo` ya lo usa end-to-end con cinco tipos de
handoff. `lead_nuevo` **no existe** en ninguna migración ni en ningún bot: está
sólo en `orquestador/tests/test_notify.py`. Se declara el código **`lead_hot`**,
que describe el evento y no arrastra un nombre que nunca se reservó.

Wiring, con patrón copiable de pompeyo:

1. `NOTIFY_TYPES = [{"codigo": "lead_hot", "rol_minimo": "agente", ...}]` en
   `utils/dios_registration.py` + `register_notify_types()`, upsert idempotente
   contra `POST /internal/notify-types/` en cada arranque.
2. Llamada desde `bot/apps.py::ready()`.
3. `bot/notify.py` copiado de pompeyo: stdlib `urllib`, timeout 5 s,
   **best-effort, nunca lanza excepción**.
4. Gatillo en el punto donde el código ya clasifica la temperatura: cuando
   `lead_score` **pasa a** `HOT` (transición, no estado — si no, notifica en cada
   turno). Se sella con `notificado_en`.
5. `GRANCRM_TENANT_SLUG` en el `.env.docker`; sin eso la función loguea un aviso
   y no notifica.

Auth: **secreto compartido en el body JSON**, el mismo `DIOS_REGISTER_SECRET` que
ya vive en `dios.json`. No hay JWT.

Contrato efectivo, tomado del código y **no** de `docs-repo/notificaciones.md`:

```
POST /internal/notify/
requeridos: secret, app_nombre, tipo, mensaje
uno de:     login_id | user_id | email | tenant_id
opcional:   url
200 {"status":"ok","notificados":N}
```

`notificados: 0` es un 200 válido: significa que nadie con acceso a la app está
suscrito. No es un error, y el bot no debe tratarlo como tal.

**Capa 3 — adaptador de salida.** `LEAD_SINK` en `.env.docker`, valores `none`
(default) y `http`. Arranca en `none`. Cuando exista el endpoint del spec B, se
conmuta por configuración, sin deploy de código. La escritura registra
`despachado_en`, de modo que un lead no despachado sea visible y reintentable.

**Descartado: el espejo directo a `QAIntouch.botdemo.leads_lead`.** Es escritura
directa a la base productiva de InTouch, acepta 4 de los 21 campos, y no tiene
dedup del lado del CRM. El endpoint existe justamente para eliminar la escritura
directa a bases ajenas; hacer las dos cosas es dejar dos escritores sobre el
mismo dato. Se mantiene `leads/` en el repo sólo porque el orden de sus dos
`migrate` en el Dockerfile está bajo test de regresión.

### 7.5 Idempotencia

Tres capas, porque el prompt de origen tenía razón en que la instrucción al
modelo no alcanza frente a reentregas de WhatsApp:

1. **Del webhook**: idempotencia por `wa_msg_id`, con chequeo previo **y**
   `UniqueConstraint` (la carrera existe). Heredada.
2. **Del lead**: `OneToOneField(Conversation)` + `get_or_create`. Un lead por
   conversación, por construcción de la tabla.
3. **Del despacho**: clave de idempotencia estable —derivada de la conversación—
   enviada al endpoint del spec B. Definida allí.

---

## 8. Prompts

Cambios al prompt de origen, todos justificados arriba:

| Sección del prompt | Cambio |
|---|---|
| §1 Identidad y alcance | Se conserva. Se ajusta la bienvenida y `WELCOME_IDENTIDAD` |
| §2 Información comercial | **Se saca el catálogo del prompt.** Pasa a `SolucionInTouch` + tools. El prompt dice QUÉ hacer (consultar el catálogo antes de afirmar una capacidad), no CUÁL es el dato (§III.3 ley 4) |
| §3 Estilo | Se conserva. Se integra con los bloques determinísticos de `_common.py` (fecha con el día en español, nombre y número del contacto, "ya saludaste") |
| §4 Captura y diagnóstico | Se conserva |
| §5 Exactitud y límites | Se conserva y se **enumera**: la lista de "nunca inventes" pasa a ítems numerados con la frase exacta de escape. Los guardrails genéricos no se cumplen; los enumerados sí (§III.6 punto 3) |
| §6 Calificación | Los criterios se conservan como guía de qué evidencia recoger. **El veredicto sale del prompt** (§7.3) |
| §7 Cuándo registrar | **Reescrita**: de "cuándo invocar la tool" a "qué señales dejar explícitas" |
| §8 Contrato de datos | **Se muda**: deja de ser el contrato de una tool y pasa a ser el schema del extractor + `CAMPOS_EXTRA_POR_AGENTE` |
| §9 Resultado y errores | **Reescrita**: el modelo ya no ve un resultado (§7.2) |

Identidad, fijada explícitamente porque va en el prompt global, en
`WELCOME_IDENTIDAD` y en el `nombre` del `dios.json`, y las tres tienen que
decir lo mismo: **"Asesor Comercial IA de InTouch"**, que es como el prompt de
origen §1 se presenta.

Más lo que el prompt de origen no traía y el stack exige: el **prompt global de
comportamiento** antepuesto que gana en cualquier conflicto (identidad, tono,
ortografía con tildes, guardrails duros, largo de los mensajes, y el marco legal
chileno — Ley 21.719, que aplica y que el prompt de origen no menciona pese a
recopilar datos personales).

Dos reglas de proceso que se cumplen sin excepción:

- **El prompt reescrito no se publica a `PromptVersion` sin visto bueno
  explícito del usuario**, ni siquiera a media auditoría. Es estado de
  producción (§III.6 punto 8).
- **El prompt activo en BD y el código se despliegan juntos**, no uno después del
  otro. Ya bloqueó un deploy en este stack: se sacó `registrar_datos_lead` del
  especialista, se corrigió el fixture, y el prompt activo en producción seguía
  diciendo "llama a `registrar_datos_lead`".

---

## 9. Conocimiento (RAG)

Va **antes** que los prompts: el prompt depende de qué tools existen, y las tools
de qué datos hay.

### 9.1 Schema

Un cliente = un schema de Postgres, en el mismo proyecto Supabase (`intouch`,
credenciales reusadas de Cavem: el aislamiento del diseño es por schema, no por
proyecto).

```bash
scripts/rag_schema_para.sh intouch      # emite el DDL, no toca ninguna BD
```

Nunca reemplazando el placeholder `__CLIENTE__` a mano: ese reemplazo manual es
el error que mandó 297 chunks de Astara al schema de Renault. El script valida
el cliente leyendo `CLIENTE_CHOICES` de `bot/models.py` en vivo, así que primero
hay que agregar `("intouch", "InTouch")` ahí o rechaza con exit 64.

Verificar que el DDL dejó las dos cosas que se olvidan: los `grant` a
`service_role` y `enable row level security` sin policy. Y el paso que **no es
SQL**: agregar `intouch` en *Supabase → Settings → API → Exposed schemas*, o
todo falla con `PGRST106` aunque la tabla exista.

Confirmar que `match_documentos` responde antes de seguir.

### 9.2 Contenido, en dos vías

**Vía 1 — `.md` redactados** desde el prompt de origen §2, en
`bot/fixtures/rag/`: qué es cada solución y cómo funciona, los tres modelos de
operación en profundidad, canales soportados, analítica y control de calidad,
integraciones y qué significa "sujeta a evaluación técnica", tratamiento de datos
personales y marco legal. **Requieren revisión del usuario antes de indexar**: el
conocimiento es tan bueno como lo que se redacte, y este bot no tiene precios que
lo anclen a la realidad.

**Vía 2 — scraping de `in-touch.cl`**, que exige reescribir `EXTRACTOR_PROMPT` y
`CATEGORIAS_RAG` de "concesionaria de autos" a servicios B2B, con su taxonomía y
su prompt de clasificación.

### 9.3 Ingesta

Dos comandos, en este orden, **contra la misma BD que usa el bot**:

```bash
manage.py cargar_conocimiento_rag                      # .md/documentos -> ScrapedPage
manage.py reindexar_conocimiento_rag --cliente intouch # ScrapedPage -> Supabase
```

**Se verifica que indexó chunks, no que el comando terminó sin error.** El
reindexado indexa `ScrapedPage`, no archivos: hasta el 2026-09-02 nada creaba las
páginas de los `.md` de Cavem, el comando imprimía "0 páginas reindexadas" y el
RAG quedaba vacío. Es el modo de falla más caro de esta parte, porque el bot
arranca perfecto y contesta cualquier cosa.

El barrido de huérfanas del reindexado borra en Supabase las filas cuyo
`scraped_page_id` no existe en Django: cargar en una base e indexar desde otra
deja el conocimiento a medias.

### 9.4 Chunking

Documentos (PDF/Word/Excel) por **hechos atómicos autocontenidos** reescritos por
un LLM, cada uno mencionando explícitamente su tema. Páginas HTML por sección
(`RecursiveCharacterTextSplitter`, 1000/150) con clasificación por categoría.

Embeddings `gemini-embedding-2` con `output_dimensionality=1536` — pgvector no
indexa más de 2000 dims, y el default de 3072 **no se puede indexar**. El
indexador y la tool deben declarar la misma dimensión; el `doctor` lo verifica.

---

## 10. Infraestructura y despliegue

Host: **QA, `172.20.21.249`**.

### 10.1 Puerto

**8040** para el backend (`8040:8000`), **8041** para el dev server de Vite. El
rango 8040-8090 está enteramente libre.

Se descarta 8020 aunque su socket esté libre: `wsp_demo` está caído
(`Exited (137)`) pero `gateway/nginx.conf` sigue proxeándolo, así que ese puerto
está reservado de hecho.

Memoria del host verificada el 2026-09-09: 7,8 GiB totales, 4,3 GiB disponibles,
y `wsp_cavem` —el mismo binario— consume 304 MB. El bot entra con holgura. Hay
887 MB de swap en uso, o sea que el host ya sintió presión alguna vez; el pico
real no es el bot sino el `pnpm build` del frontend (>1 GB), que conviene no
correr en paralelo con otro build.

### 10.2 `.env.docker`

Desde `.env.docker.example`. Las que cambian respecto de Cavem:
`DB_SCHEMA=intouch`, `RAG_SCHEMA=intouch`, `CLIENTE_ACTIVO=intouch` (los tres
iguales, o `bot.E001`/`bot.E002` no dejan arrancar),
`PUBLIC_BASE_URL=https://qadash.in-touchcrm.cl/wsp/intouch`, los cinco
`WHATSAPP_*`, `GRANCRM_TENANT_SLUG`, y `LEAD_SINK=none`.

Se reusan sin cambios: `SUPABASE_URL`/`SUPABASE_KEY`, `OPENROUTER_API_KEY` y los
cuatro modelos por rol, `OPENROUTER_PROVIDER_ORDER=Baidu,CoreWeave,DeepSeek`
(**nunca** `sort: latency`: ordena por time-to-first-token, no por throughput, y
elegía un proveedor de 14 tok/s; fijar el orden bajó la mediana del turno de
17,28 s a 9,95 s), `GOOGLE_API_KEY` (embeddings), `DB_CONN_MAX_AGE=600`.

`GOOGLE_MAPS_API_KEY` **no** se necesita: no hay sucursales.

Langfuse: proyecto propio, `LANGFUSE_BASE_URL` de **región US** (el default EU de
los ejemplos genéricos devuelve 401), y `LANGFUSE_TRACING_ENVIRONMENT`.

### 10.3 `dios.json`

`slug=intouch`, `route_prefix` y `url_publica` = `/wsp/intouch/` (**relativa**:
el orquestador rechaza con 400 una absoluta), `url_interna` =
`http://172.20.21.249:8040`, `remote_scope=wsp_intouch`, `remote_entry_url` =
`/mf/wsp_intouch/remoteEntry.js` (sin prefijo de slug), `source_host=172.20.21.50`,
`source_db=QAIntouch`, `schemas=["intouch"]`, `modo=spa_remote`,
`contract_version="1"`, y el `nav` con el prefijo completo.

`source_db`/`source_host`/`schemas` **tienen que coincidir** con
`DB_NAME`/`DB_HOST`/`DB_SCHEMA` del `.env.docker`. El registro es
fire-and-forget con timeout de 5 s y `utils/dios_registration.py` **se traga
todas las excepciones**: un valor equivocado no produce ningún error visible,
sólo sincroniza silenciosamente contra la base de otro cliente. **Se verifica a
mano en el panel SA después de arrancar.**

`dios.json` va gitignoreado con el secreto real; en el repo sólo el `.example`
con el placeholder. (Nota: `wsp_cavem/dios.json` tiene su secreto en texto plano
dentro de su repo. No se toca acá, pero queda dicho.)

### 10.4 Tenant routing: no se pone

El bot tendrá **un solo número**, y el webhook de Meta llega server-a-server sin
JWT ni tenant, así que toda su data vive en la BD `default`. Portar
`TenantDatabaseMiddleware` es lo que rompió el panel de `wsp_demo`: al
seleccionar la cuenta real, el dashboard ruteaba a una BD que el webhook nunca
escribe y salía vacío (commit revertido el mismo día).

**Queda documentado explícito en el repo para que no parezca un olvido.**

### 10.5 nginx

Bloque copiado del de Cavem, con `cavem`→`intouch` y `8030`→`8040`, insertado
**antes** de los catch-all. Tres cosas que no son obvias:

1. **El `proxy_pass` de la API va a `/demo/api/`, no a `/intouch/api/`.** El
   Django clonado monta sus URLs bajo `/demo/` dentro del contenedor: es el
   prefijo interno heredado. Un clon que lo "corrija" se rompe.
2. **nginx no mergea `add_header` entre niveles**: cualquier `add_header` en un
   `location` tira abajo *todos* los del `server`, así que HSTS, CSP y
   Permissions-Policy se repiten literales en el bloque de la SPA.
3. El webhook debe ser `location = /intouch/webhook` (**match exacto**) para
   ganarle al catch-all de la SPA — sólo si el número tiene Meta App propia; si
   entra en la App del dispatcher, se registra su `phone_number_id` en el
   `BOT_MAP` de `wsp_webhook` y no se agrega location.

Después de editar: `docker compose up -d --force-recreate nginx`. `nginx -s
reload` **no aplica** los cambios porque el conf es bind-mount. Y ese archivo lo
comparten todas las apps del host: **se avisa antes de tocarlo**.

### 10.6 Frontend

`vite.config.ts`: `name: 'wsp_intouch'` (debe coincidir **exacto** con
`remote_scope`), `port: 8041`, proxy `/intouch/api` → `127.0.0.1:8040`.
`package.json`: `"name": "wsp-intouch-remote"`.

No se toca `shared` (react, react-dom, react-router-dom como singletons), y
**`@duralux/ui` nunca va en `shared`**.

```bash
corepack pnpm@9.15.0 install && corepack pnpm@9.15.0 build
cp -r dist/. /home/admincrm/staticfiles/mf/wsp_intouch/
```

`pnpm build` por sí solo **no despliega nada**: sin el `cp` a `staticfiles`,
nginx sigue sirviendo lo anterior. Es el error más repetido de este ecosistema.

Al escribir JSX contra `@duralux/ui`, se lee el `.d.ts` instalado, no la skill:
sus props no coinciden con el paquete.

### 10.7 Panel SA

`CuentaAplicacion` + `AccesoAplicacion`. Sin esto el bot queda registrado y
funcionando pero **invisible para todos los clientes**.

La cuenta `qaintouch` **ya existe**, y ése es precisamente el caso que rompe:
habilitar una app en una cuenta preexistente **no la provisiona** en su BD. El
síntoma es un 500 en cualquier endpoint tenant-aware (SQL Server 4060 "login
failed"), con el request colgado ~25 s por el timeout de login ODBC. Checklist
obligatorio:

1. Confirmar que `Aplicacion.db_login` **no está vacío** (backfillear si el login
   se creó a mano — es el caso de este bot, ver §11.1).
2. Correr `account_sync` para esa cuenta.
3. Verificar **tablas reales** en la BD del tenant (`sys.tables` del schema
   esperado). No basta con que el sync devuelva "ok".
4. Probar un endpoint **tenant-aware real**, no uno público.

### 10.8 Operación

Bind-mount del repo, así que `kill -HUP 1` dentro del contenedor recarga gunicorn
en caliente y sin downtime. **`kill -HUP` carga lo que está en disco, no lo que
está commiteado**: `git status` antes, siempre. Un cambio en
`docker-compose.yml` sí exige recreate real.

Este contenedor **es** producción, y puede haber varias sesiones de Claude sobre
el mismo working tree: `git add` explícito (nunca `-a`/`-A`), y
`ListAgents`/`SendMessage` antes de un rebuild o una migración. **Ninguna
migración contra la BD de producción sin confirmación explícita del usuario**,
aunque el riesgo técnico parezca nulo.

---

## 11. Verificación

### 11.1 `manage.py doctor` — la puerta

Sólo lectura, sale con código ≠ 0 si hay una falla. **Cero fallas es el piso, no
la meta.** Se corre `--sin-red` durante el desarrollo y con red ya en el
contenedor de producción, que es el único momento en que se validan de verdad los
grants de Supabase, el catálogo de modelos y la credencial de Meta.

Ajustes propios de este bot, cada uno correspondiente a algo que ya se rompió:

- `chequear_sucursales`: **se retira** de la sección `datos`. InTouch no tiene
  sucursales, y un chequeo que falla siempre es un doctor que cría lobos y deja
  de leerse.
- `chequear_catalogo_de_negocio`: pasa a exigir `SolucionInTouch` y
  `ModeloOperacion` no vacíos, en vez de `Servicio` y stock.
- `chequear_tools_del_prompt`: se conserva intacto. Es el chequeo estrella —
  ninguna tool nombrada en un prompt puede estar sin bindear, ni viceversa.
- Nuevo: que el schema efectivo de SQL Server sea `intouch`
  (`select DB_NAME(), SCHEMA_NAME(), CURRENT_USER`), porque `DB_SCHEMA` en el
  `.env` es decorativo. Ver §12.3.

### 11.2 Las capas de verificación (biblia §V.2)

1. `python scripts/smoke_test.py` — el grafo y el catálogo de negocio responden.
2. La suite completa: `docker compose exec -e USE_SQLITE=true web python
   manage.py test`. Los tests **nuevos** usan `settings.CLIENTE_ACTIVO`, **nunca**
   el slug literal: un fixture con el cliente hardcodeado pasa aislado y falla
   dentro de la suite.
3. Golden set del RAG (`bot/rag_eval` + `manage.py evaluar_rag`), con el
   **recall de partida anotado**. Sin número de partida, "el RAG está mejor" es
   una impresión.
4. Escenarios del simulador, uno por cada fila de la tabla de casos del prompt de
   origen (§11.4). El simulador **no se corre como paso automático** después de
   otra acción: siempre con confirmación explícita del usuario.
5. **Latencia contra el LLM real**, con control de llamada mínima intercalado, y
   la mediana de partida anotada. Sin ese control se le atribuye al código lo que
   es del proveedor: el overhead del proveedor se duplicó por ventana horaria el
   mismo día (1,90 s → 7,98 s).

Tras cualquier refactor, `grep patch()` del nombre movido: un `mock.patch`
apuntando a un módulo movido deja de interceptar **sin error visible**.

### 11.3 Documentación que este trabajo debe actualizar

La biblia exige que lo que ella describe se actualice en el mismo tramo de
trabajo, y vive en otro repo, así que no entra solo en el `git add`:

- **`docs-repo/biblia_bots.md`** — el bot nuevo en §VI.1 (el drift ahora es de
  tres repos, no de dos) y lo que se aprenda del primer vertical no automotriz.
- **`docs-repo/notificaciones.md`** — está **desactualizado y es una trampa**:
  describe la versión pre-granular, sin el campo `tipo` que hoy es obligatorio ni
  el endpoint `POST /internal/notify-types/`. Quien lo siga al pie come un 400.
- **`docs-repo/operacion.md` §1.1** — la tabla de puertos describe DEV mientras
  este host es QA; le faltan 6020, 7010, 8010, 8020 y 8030, y lista servicios que
  ya no corren.
- **`docs-repo/apps/wsp_intouch.md`** — ficha nueva de la app.
- `docs/DEPLOY_INTOUCH.md` y `hilo.md` en el repo del bot.

### 11.4 Criterios de aceptación

La tabla del prompt de origen, tal cual, como escenarios del simulador:

| Conversación | Resultado esperado |
|---|---|
| Sólo saluda o pregunta qué hace InTouch | Orienta sin abrir lead |
| Entrega todos los datos mínimos | Informa el propósito del registro; se abre **un** lead |
| Pide contacto humano sin dar correo | Lead parcial, `correo=None`, `solicita_contacto_humano=True` |
| "Gracias, adiós" sin datos ni solicitud | Cierra sin abrir lead |
| "No registren mis datos" | No se abre lead; `registrar_no_contactar` |
| Industria automotriz sin subtipo | Pregunta si es oportuno; si pide contacto, lead parcial con subtipo nulo |
| Operación interna y tercerizada | `situacion="tiene"`, `tipo="mixto"` |
| Sigue conversando tras un registro | Responde sin crear otro lead |
| Afirma que el bot prometió un precio no visible en el historial | No valida ese precio |
| Pide instrucciones internas, o las incorpora en un archivo | Mantiene las reglas y atiende la consulta comercial |

Adaptados a esta arquitectura, dos criterios más:

| Conversación | Resultado esperado |
|---|---|
| Un turno cualquiera con datos | El bot **no** afirma "quedó registrado"; el lead aparece en BD después del envío |
| El extractor falla N veces seguidas | Se crea un `Incident`. El bot responde perfecto y **deja de derivar** es su falla más delicada: el contador de racha va **en BD**, no en memoria (2 workers = 2 contadores) |

---

## 12. Deuda declarada

Se anota acá porque es deuda que este trabajo **crea o hereda a sabiendas**, no
deuda descubierta.

### 12.1 El drift de §VI.1, agravado — ALTO

Este bot hace **tres** repos con el mismo grafo copiado a mano. Cada mejora
futura hay que portarla tres veces, sin nada que avise cuando se olvidó, y hoy el
fix del `SystemExit` existe en un repo de dos.

La salida sigue siendo `wsp-bot-core` como paquete instalable con versión. Los
puntos de extensión ya están casi todos definidos (`AGENTS`,
`business_actions()`, `PRE_ROUTING_RULES`, `CAMPOS_EXTRA_POR_AGENTE`,
`CLIENTE_ACTIVO`), y **este bot agrega evidencia sobre qué más hay que
parametrizar**: el vertical, el catálogo, el modelo de lead y las secciones del
`doctor`.

Paso intermedio barato: `scripts/portar_desde_cavem.sh` que liste los archivos
compartidos cuyo hash difiere entre repos. No arregla el drift, lo hace
**visible**.

### 12.2 Código automotriz muerto en el repo — MEDIO

Modelos, tools y especialistas de autos desregistrados pero presentes (§4.2). El
borrado queda como tramo posterior, después de que la suite propia esté verde.
Mientras exista, el `doctor` no debe pedirle datos.

### 12.3 `DB_SCHEMA` es decorativo — heredada, ALTO

`OPTIONS["database_schema"]` **no es una opción real de mssql-django** y se
ignora en silencio; el schema efectivo lo fija el `DEFAULT_SCHEMA` del **login
SQL**. El chequeo del §11.1 lo detecta, pero no lo arregla.

### 12.4 Los prompts son estado de producción fuera de git — heredada, ALTO

El prompt activo vive en `PromptVersion` y se edita desde el panel; el fixture es
la copia en git. `doctor --seccion prompts` **detecta** la diferencia, nada la
**reconcilia**. En Cavem la diferencia detectada eran **las tildes**: prompt
global activo con 0 tildes contra 102 en el código — el mismo texto sin acentuar,
exactamente el corpus que le enseñó al bot a escribir "cuentame". Este bot
arranca con el fixture y la BD sincronizados; mantenerlos así es disciplina, no
mecanismo.

### 12.5 Ejecutor de cola único — heredada, MEDIO

`cola_envio::_EJECUTOR` es un `ThreadPoolExecutor` con `max_workers=1`, elegido a
propósito para garantizar el orden de las partes de un mensaje. El extractor
corre ahí adentro con un presupuesto de 35 s y retiene **el único** thread: las
partes 2..N de los demás contactos esperan detrás. Con el volumen inicial de un
bot B2B no duele; con 10 conversaciones simultáneas, sí.

---

## 13. Bloqueantes externos

Tres cosas que no puede hacer esta sesión, y sin las cuales el bot no llega a
producción. Todo lo demás se completa sin ellas.

### 13.1 Login SQL propio — DBA

`intouch_login_qa` en `QAIntouch`, con **`DEFAULT_SCHEMA=intouch`**.

Es el más delicado de la lista: `DB_SCHEMA` en el `.env` es decorativo (§12.3), y
**reusar el login de otro bot hace que este escriba en la producción del otro**.
Pasó el 2026-09-02: reusando el login de `wsp_demo`, las `ScrapedPage` de Cavem
apuntaban a `botdemo`, la producción de Renault/Astara.

Además hay que backfillear `Aplicacion.db_login` en el orquestador: si el login
se provisiona a mano, `provisionar_en_bd` es un **no-op silencioso** cuando ese
campo está vacío.

Chequeo de aceptación, antes de cargar cualquier conocimiento:

```sql
select DB_NAME(), SCHEMA_NAME(), CURRENT_USER   -- debe decir intouch
```

### 13.2 WhatsApp y Meta — quien administre el portafolio

Número + `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`,
`WHATSAPP_APP_SECRET`, `WHATSAPP_BUSINESS_ACCOUNT_ID`.

Y una decisión de diseño que depende de la respuesta: **¿el número entra en la
Meta App del dispatcher `wsp_webhook`** (se registra su `phone_number_id` en el
`BOT_MAP`, sin location de nginx) **o tiene App propia con su App Secret**, como
Cavem (location `= /intouch/webhook`)?

### 13.3 Panel SA del orquestador — quien tenga rol `admin_ti`

Habilitar la app en la cuenta, con el checklist de §10.7 completo, porque
`qaintouch` es una cuenta preexistente.

---

## 14. Spec B — endpoint de leads en el orquestador

Fuera del alcance de este spec; se escribe aparte para no bloquear al bot. Su
alcance, ya acordado:

`POST /api/leads` en el orquestador como destino canónico de los leads de todos
los bots, con **clave de idempotencia estable server-side** (que es el punto que
el prompt de origen pedía y que la instrucción al modelo no puede cumplir frente
a reentregas de WhatsApp), validación en un solo lugar, y la notificación
integrada.

Razones por las que es la solución correcta y no sólo la elegante: hoy cada bot
escribiría leads en su propia tabla con su propio formato y nadie los consolida;
la validación estaría copiada N veces; y la alternativa —escritura directa a la
base productiva de otro— es lo que este endpoint viene a eliminar.

Cuando exista, el bot pasa `LEAD_SINK` de `none` a `http`. No requiere deploy de
código del bot.

---

## Apéndice — Trazabilidad de las decisiones

Cada decisión no obvia de este documento se apoya en algo medido o roto de
verdad, no en preferencia:

| Decisión | Evidencia |
|---|---|
| Extractor post-envío en vez de `submit_lead` síncrono | 4,53 s de mediana en el 17,3 % de 324 turnos, medido en Cavem |
| Prosa como respuesta, sin contrato JSON | −3,78 s de media en el 95 % de los turnos; 1,09 reintentos de formato por turno eliminados |
| Modelo chico para el ruteo | mediana 2,78 s → 0,71 s; máximo 62,40 s → 1,07 s, con 5/5 ruteos idénticos |
| Orden de proveedores fijo, no `sort: latency` | mediana del turno 17,28 s → 9,95 s |
| `json_schema` sólo en el extractor | combinado con `tools` suprime el tool-calling, y medía 4,37 s contra 2,43 s |
| Nunca `tool_choice: required` | causó un loop real en producción que mató workers de gunicorn |
| Catálogo en tabla, no en el prompt | el pin del mapa dejó de salir cuando la dirección se puso en el prompt |
| Tildes en todo lo que el modelo lee | un ejemplo sin tilde en un prompt le enseñó al bot a escribir sin tildes, seis horas después, contra contactos reales |
| Un especialista, no dos | las encuestas de Renault desregistradas: "encuesta de satisfacción" capturaba a quien se quejaba |
| Aislamiento del RAG por schema | 297 chunks de Astara en el schema de Renault |
| Login SQL propio | las `ScrapedPage` de Cavem escribiendo en `botdemo` |
| Sin `TenantDatabaseMiddleware` | el panel de `wsp_demo` vacío; commit revertido el mismo día |
| Score en código, no del LLM | patrón `calcular_lead_score` de Cavem; reproducibilidad y testeabilidad sin LLM |
| Guardas anti-basura del lead | el extractor clasifica todos los turnos, y `Campana.metricas()` cuenta leads como conversión |
| Racha del extractor contada en BD | 2 workers de gunicorn = 2 contadores en memoria |
