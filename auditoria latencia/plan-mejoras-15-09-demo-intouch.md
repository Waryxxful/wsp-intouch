# Plan de mejoras — demo InTouch 15-09

Fecha del plan: 2026-09-22. Contrasta la pestaña `15-09 Demo Intouch` con el
código de `wsp_intouch` en esta fecha. No implementa nada.

Fuentes, en este orden si hay que volver a leerlas:

- `auditoria latencia/recomendaciones-15-09-demo-intouch.md` — los 10 gaps, fieles a la hoja, sin opinar si ya están resueltos.
- `auditoria latencia/estado-actual-wsp-intouch.md` — qué hace el bot hoy, con archivo y test.
- `auditoria latencia/2026-09-21-medicion-y-correccion.md` — la cifra de latencia de la misma demo, medida en Langfuse.

La hoja no firma un cargo. En este plan se las llama notas del gerente porque
así las identificó quien pidió el trabajo. Felipe aparece solo como cita dentro
del problema 1. El creador del archivo (propiedades, no celdas) es Josefa
Andrea Brinzo Díaz; eso no se toma como autoría de las notas.

## Lectura en una página

La demo falló en tres sitios distintos, y conviene no mezclarlos.

1. **El modelo afirmó cosas que el sistema no hace** (reclamo con número, contacto hoy a las 22:00, tutorial ante el SERNAC). Parte de eso ya está prohibido en el prompt. Igual ocurrió, y hay una tool que le ordena al modelo decir lo contrario.
2. **El bot es más ignorante que el sitio de la empresa.** No tiene la URL, no tiene la palabra «copiloto», y tiene prohibido citar cifras que [in-touch.cl](https://in-touch.cl/index.html) publica. Cuando no sabe, repite «prefiero que lo valide un especialista». Esa frase es la salida honesta que el propio prompt le exige, y en la demo se usó unas veinte veces.
3. **La latencia de la hoja (promedio 7,9 s) no es el turno que vio el prospecto.** La medición del 21-09, sobre las mismas trazas del 15-09, deja el turno en 5,94 s de media y 3,99 s de mediana. Los 7,72 s son el extractor, que corre después de que el primer mensaje ya salió.

Ningún frente se cierra agregando una línea más al prompt y esperando que el modelo la cumpla. Donde la demo ya desobedeció una regla escrita, el arreglo es que el entorno la haga cumplir: un dato que tiene que estar siempre va inyectado en el turno, un teléfono que no debe salir sin consentimiento no entra al payload, un horario que no es laboral no lo propone el texto libre.

## Contraste

| Id | Qué pasó en la demo | Qué hay hoy | Lectura |
|---|---|---|---|
| G01 P0 | Usó el teléfono sin consentimiento. Lo pidió cuando Felipe lo exigió. | El teléfono llega de WhatsApp. El prompt dice «no lo pidas: ya lo tienes» y, si el contacto entrega datos sin pedir contacto, explicar para qué se registran. `registrar_consentimiento` solo se usa cuando el contacto se pronuncia; el docstring prohíbe inferirlo. `payload_del_lead` manda `telefono` al CRM sin mirar si hubo consentimiento. | La causa que escribe la hoja es la que está en el código. El consentimiento es reactivo y el envío del número no depende de él. |
| G02 P0 | Dijo «tu caso quedó registrado internamente como reclamo» y un contacto «hoy». No pudo dar número. | El prompt global y el del especialista prohíben decir que algo quedó registrado, agendado o notificado. El escenario `no-afirma-registro` exige eso. `crear_caso` sí crea un `Incident` local y devuelve `caso_id`. Su docstring ordena: «dile al contacto que su consulta queda registrada para el área correspondiente». Ese id no es un folio que el prospecto pueda usar. El handoff no apaga el bot ni pone modo humano. | Hay dos instrucciones en el mismo turno. La tool gana la que la demo obedeció. El CRM de leads existe (`LEAD_SINK=http`); un reclamo no es un lead y no tiene ticket público. |
| G03 P1 | «Prefiero que lo valide un especialista» unas veinte veces. Precio, resultados, años, API y referencias terminaron ahí. | Esa frase es la salida obligatoria de los ocho «Nunca inventes». El prompt de cotización pide otra cosa (explicar que depende de modelo, canales, volumen y alcance, y ofrecer evaluación). Las dos reglas conviven. Los `.md` del RAG tienen tests que les prohíben montos, plazos, clientes, casos y certificaciones. `sobre-intouch.md` no trae años ni tamaño. | La derivación repetida es el síntoma. La causa de la hoja (base vacía) es cierta para años, tamaño, resultados y URL. Precio, plazos e integración concreta siguen sin dato a propósito. |
| G04 P1 | No conocía el «IA copiloto» del sitio. El modelo híbrido se preguntó dos veces. | El catálogo tiene tres modelos: Humano, Híbrido, Automatizado, con ficha y con `bot/fixtures/rag/modelos-de-operacion.md`. No existe una solución llamada copiloto. En el HTML de la home, leído el 2026-09-22, está «Contact Center Híbrido» y no aparece la palabra copiloto. | El híbrido ya se puede responder si el modelo llama a `listar_modelos_operacion`. El copiloto no está en la base. Indexar el sitio entero no alcanza: hay que nombrar el producto y atarlo a una ficha. |
| G05 P1 | «El enlace directo del sitio web no lo tengo a la mano.» | Ningún `.md` de `bot/fixtures/rag/` ni el prompt contienen `in-touch.cl`. Las URLs de ese dominio que aparecen en tests son fixtures de prueba del RAG, no conocimiento del agente. | Arreglo de contenido, con una condición: si la URL vive solo en el RAG, una búsqueda fallida la vuelve a esconder. Tiene que estar en el turno, siempre. |
| G06 P1 | Dio una guía para reclamar contra InTouch ante el SERNAC. | No hay regla para un reclamo contra la propia empresa. `crear_caso` acepta `reclamo` y `datos_personales`. El prompt pide usarla ante reclamo grave, amenaza legal o datos personales, y enseguida decir que quedó registrada (el mismo choque de G02). | Falta el equilibrio que pide la nota: reconocer el derecho, ofrecer el canal interno, no tutelar el reclamo de consumo. |
| G07 P2 | Ofreció hoy a las 22:00 como mejor horario para el ejecutivo. | El prompt permite preguntar día u horario como preferencia y prohíbe comprometer agenda. No dice qué franja es laboral. `BusinessHours` (lun–vie 09:00–18:00 si no hay filas) existe en el panel y ningún código de `bot/` lo lee. El bot contesta de noche igual. | La regla de «no comprometas» no impide proponer las 22:00. El horario laboral ya está modelado y el agente no lo ve. |
| G08 P2 | Confirmó el contacto de las 22:00 como preferencia registrada, sin poder cumplirlo. | `preferencia_horaria` se guarda en `LeadInTouch`. El `help_text` dice que no es una hora reservada. No está en `_CAMPOS_DEL_PAYLOAD` ni en el panel de leads. Aunque el modelo anote bien, el ejecutivo no la ve en el CRM. | Registrar sin comprometer ya es la regla escrita. Falta que la preferencia viaje, y que el texto no la presente como coordinación. |
| G09 P3 | Diez turnos repitieron «hoy a las 22:00, viernes 18 de respaldo». | El prompt pide no repetir saludos, datos ya conocidos, preguntas ya respondidas ni invitaciones a reunión. No dice qué hacer con un acuerdo ya cerrado. El saludo tuvo que recortarse en código porque la instrucción sola falló. | Misma familia: una regla de estilo que el modelo no sostiene turno a turno. El acuerdo tiene que volver en el contexto como hecho cerrado. |
| G10 P2 | 39 % de turnos bajo 5 s, promedio 7,9 s. Estándar de la nota: 5 s en todos. Remite a «sección 5», que no está en la pestaña. | Turno del 15-09: media 5,94 s, mediana 3,99 s, n=56. Con tool, 10,08 s; sin tool, 3,82 s. El ruteo de un solo especialista (1,01 s en todos los turnos de esa muestra) ya no se llama. La consulta RAG bajó a ~0,9 s el 22-09. Siguen abiertos el sándwich de dos generaciones alrededor de la tool, el arranque en frío y el extractor en el único hilo de la cola. | La hoja pide infraestructura con una cifra que mezcla el turno y el extractor. El tramo que todavía decide si se cumple o no el estándar de 5 s es la ronda de tool. |

## Lo que el sitio público ya dice y el bot no puede repetir

Leído el 2026-09-22 en `https://in-touch.cl/index.html`, además del modelo
híbrido:

- 18 años de experiencia en Contact Center.
- +300 colaboradores.
- +30 clientes.
- 1M+ llamadas al mes.
- Tasa de resolución IA 83 %, CSAT 91 %, reducción de costos 67 %, NPS +72, con la nota del propio sitio: métricas promedio de clientes activos del sector automotriz e industrial, 2022–2024.
- «Hacemos el trabajo de 100 con 30.»

Eso es, casi textual, la lista de G03 (años, tamaño, rangos de resultados). El
prompt y `test_conocimiento_intouch` los tratan como invención. El bot queda
más ciego que la página que dice representar. Cargarlos no es un cambio de
prompt: es una decisión comercial de autorizar esas cifras, con la cautela que
el sitio ya escribe.

En el mismo dominio hay sitios de clientes (agendamiento de talleres de Opel,
Peugeot, Piamonte, Indumotora). Un RAG del dominio completo se los tragaría.
Por eso la vía 2 del spec (scraping de `in-touch.cl`) no es el primer paso, y
el extractor estructurado de este vertical sigue en `NotImplementedError`.

La palabra «copiloto» no salió en ese HTML. Puede estar en otra URL, en una
imagen o en un bloque que el fetch no vio. Antes de escribirla en la base,
alguien de InTouch tiene que indicar la página y a cuál de las ocho soluciones
corresponde. Si no corresponde a ninguna, es una novena ficha, no un párrafo
suelto.

## Decisiones que el plan no puede tomar solo

Cada una tiene una opción completa, que es la recomendada, y una barata. La
barata cierra la demo de hoy y deja el mismo fallo disponible para la próxima.

### D1. Cuándo sale el teléfono al CRM (G01)

Recomendada. El lead local se puede abrir igual que hoy. El `telefono` no
entra al payload, ni a la notificación, hasta que exista un
`Consentimiento` con `otorgado=true` para ese `wa_id`. El texto y el momento
los define legal. El momento técnico ya está dicho por la hoja: apenas la
conversación deriva a captura, en un mensaje fijo (el mismo mecanismo del
saludo instantáneo), no cuando el modelo se acuerde. Si el contacto dice que
no, se llama a `registrar_consentimiento(otorgado=false)` o a
`registrar_no_contactar`, y el número no se despacha.

Costo permanente de la barata (una frase más en el prompt, y el payload sigue
llevando el teléfono): la demo del 15-09 ya tenía una regla de consentimiento
y llegó tarde. InTouch vende compliance de datos. Un número que viaja sin sí
explícito revive el reclamo de Felipe en cuanto el modelo se apura.

Bloquea la implementación el texto de legal. Sin ese texto no se redacta la
burbuja.

### D2. Qué cifras del sitio puede decir el bot (G03, G04)

Recomendada. Una ficha corta, firmada por comercial, con solo lo que el sitio
ya publica y con la nota al pie de las métricas copiada. Vive en el
repositorio, la revisa una persona, y un test falla si el `.md` trae un monto
en pesos, un plazo o un cliente con nombre que la ficha no liste. Precios,
plazos, certificaciones e integraciones con un sistema puntual siguen
prohibidos: la hoja no entrega esas cifras y el sitio, en la home, tampoco.

Costo permanente de la barata (pedirle al modelo que «use el sitio» o indexar
el dominio): el bot empieza a citar 83 % y 67 % sin la cautela, o cita el
agendamiento de un concesionario como si fuera producto de InTouch. Y cada
página nueva del sitio vuelve a desalinear al agente, que es el G04 de la
próxima demo.

### D3. Quién mira la conversación cuando el bot dice que el equipo la toma (G02)

Recomendada. `crear_caso` y el handoff disparan la misma campanita que el lead
HOT, con el resumen y el enlace a la conversación. El modo humano sigue
siendo un acto del operador: el bot no se calla solo. Alguien de InTouch tiene
que tener esa cola en la jornada. Sin esa persona, el texto «dejo tu caso
listo para que el equipo lo tome» es la misma promesa vacía con otras palabras.

Costo permanente de la barata (solo cambiar la frase del prompt): el prospecto
oye una fórmula correcta y nadie abre el chat. `GRANCRM_TENANT_SLUG=CHANGEME`
en `.env.docker` ya deja la campanita HOT en silencio. Hay que confirmar el
valor del contenedor que corre, no solo el del archivo.

### D4. Horario nocturno si el prospecto insiste (G07 contra G08)

La hoja dice las dos cosas: aceptar la preferencia si insiste, y no confirmar
un contacto que no se puede cumplir. Se resuelve así, y es la lectura que este
plan usa mientras nadie la corrija.

- El bot no propone una hora fuera de `BusinessHours`.
- Si el contacto insiste, se anota tal cual en `preferencia_horaria`.
- El texto es «anoto que te acomoda hoy a las 22:00; no queda agendado, el equipo te confirma».
- Esa preferencia viaja en el payload y se ve en el panel. Hoy no hace ninguna de las dos.

## Plan por frentes

El orden es el de abajo. A y B se pueden hacer en paralelo. C espera el texto
de legal. D espera la ficha firmada de D2. E y F no se adelantan a A: repetir
un acuerdo falso, o acelerar una respuesta que promete un reclamo, empeora la
demo.

### Frente A — Dejar de afirmar acciones que el sistema no hizo

Gaps: G02, G06, G08. Dueño: prompt, docstring de `crear_caso`, escenario del
simulador.

Hoy `crear_caso` le pide al modelo que diga que la consulta «queda registrada»
y le devuelve un `caso_id` interno. Eso es la frase de la demo. El cambio:

- El docstring pasa a la fórmula de la hoja: «dejo tu caso listo para que el equipo lo tome». No menciona registro, número de caso ni «hoy».
- El resultado de la tool no incluye un id pensado para leer en voz alta. El id sigue en la base para el panel.
- Regla de reclamo contra la propia empresa: reconoce el derecho, ofrece el canal interno (`crear_caso` con `reclamo` o `datos_personales`), nombra a la Agencia de Protección de Datos cuando el tema es datos personales, y no da pasos para presentar un reclamo de consumo contra InTouch. Puede decir que ese derecho existe. No arma el tutorial.
- Un escenario nuevo del simulador cubre las tres conductas: reclamo interno, pedido de tutorial ante el SERNAC, y «¿quedó agendado hoy?». El juez mira el texto. El test de código mira que el docstring ya no contenga «queda registrada».

La opción barata es borrar una oración del prompt. El costo permanente es el
docstring de la tool, que entra al contexto en el mismo turno y ya le ganó al
prompt global una vez.

Verificación: el escenario en el simulador, más un test que falle si el
docstring de `crear_caso` vuelve a ordenar decir que quedó registrada.

### Frente B — Lo que el agente tiene que saber aunque el RAG falle

Gaps: G05 entero, G04 en la parte de híbrido y de URL, el ítem «sitio web» de
G03.

La URL, los enlaces públicos de soluciones, casos y contacto, y una línea por
modelo de operación (Humano, Híbrido, Automatizado, con la frase del sitio)
van en un bloque que el grafo inyecta en todos los turnos, al lado de la fecha.
No dependen de `consultar_base_conocimiento`. El RAG sigue para el fondo
(cómo funciona, datos, seguridad).

Un test de texto falla si el bloque no contiene `https://in-touch.cl` y los
tres modelos. Otro falla si el prompt le dice al modelo que no tiene el enlace
de su propio sitio.

El copiloto entra a ese bloque solo con la URL y la ficha que salgan de D2.
Hasta entonces el agente dice que no tiene ese nombre en el catálogo y ofrece
lo más cercano que sí esté (`agentes-conversacionales` o el modelo híbrido),
en vez de «no lo tengo en mi base» a secas.

Poner la URL solo en un markdown y reindexar es la opción barata. La próxima
búsqueda vacía repite «no lo tengo a la mano», que es el G05 textual.

Este frente además alivia G10: una URL respondida desde el bloque no paga la
ronda de tool (10,08 s en la muestra del 15-09).

### Frente C — Consentimiento antes de que el número salga

Gap: G01. Depende de D1 y del texto de legal.

Cuando el extractor va a abrir lead, o cuando el especialista pide el primer
antecedente de contacto, sale la burbuja fija con el texto de legal. El
despacho al CRM (`payload_del_lead` y la campanita) omite el teléfono mientras
no haya consentimiento otorgado. El panel muestra el lead igual, con el estado
del consentimiento, para que operaciones no pierda la conversación.

`registrar_consentimiento` se queda como está en un punto: no se infiere. Lo
que cambia es el momento en que se pregunta, y la compuerta del payload.

Sin el texto de legal este frente no se escribe. Se puede dejar preparado el
hueco (setting `texto_consentimiento`, vacío, y `doctor` en rojo si el
despacho del teléfono está prendido y el texto falta). Así la regresión no
queda escondida detrás de una variable olvidada.

### Frente D — Responder lo comercial y derivar una sola vez lo que no tiene precio

Gap: G03. Depende de D2 para las cifras. El cambio de frase no depende.

La frase única de escape se parte:

- Precio, plazo o integración concreta: el texto que el prompt de cotización ya tiene (depende del modelo, los canales, el volumen y el alcance; el siguiente paso es una evaluación comercial). Una vez por tema. No se usa «no tengo ese dato confirmado».
- Dato que la ficha firmada sí trae: se responde con la cautela escrita en la ficha.
- Dato que nadie autorizó (un cliente con nombre, una API de un vendor, un porcentaje que no está en la ficha): una derivación, y en los turnos siguientes se refiere a ella en vez de repetirla.

El prompt ya dice «responde primero la consulta» y «no repitas invitaciones».
La demo mostró que la frase de escape le gana. Por eso el bloque de cifras
autorizadas va inyectado, y la frase de escape deja de ser la única salida
para los ocho puntos.

Cargar «casos de éxito» genéricos sin la ficha firmada reabre la prohibición
3 de «Nunca inventes» y el test de los `.md`. No es un atajo disponible.

### Frente E — Horario laboral, preferencia visible, cierre que no se reescribe

Gaps: G07, G08, G09. Usa la lectura de D4.

- Cada turno recibe la fecha actual (ya existe) y la franja de `BusinessHours` que el panel ya guarda. El modelo propone dentro de esa franja.
- `preferencia_horaria` entra a `_CAMPOS_DEL_PAYLOAD` y al JSON del panel de leads. El `help_text` actual se mantiene: no es una hora reservada.
- Cuando el lead ya tiene preferencia, el contexto del turno dice «preferencia anotada: …; no la repitas completa; no digas que quedó agendada». Es el mismo remedio que `_quitar_saludo_inicial`: la instrucción de no repetir ya falló sola.

La opción barata es una línea «no ofrezcas horarios nocturnos». El panel sigue
sin la preferencia, el modelo sigue sin ver el horario real, y el cierre largo
vuelve en la próxima conversación que acuerde un dato.

### Frente F — Latencia, con la cifra corregida

Gap: G10. No se reabre la auditoría. Se continúa
`2026-09-21-medicion-y-correccion.md`.

Lo que ya está hecho desde esa demo: el ruteo de un solo especialista no llama
al LLM, y la consulta RAG se midió más rápida el 22-09. Lo que la hoja pide
(5 s en todos los turnos) no se cumple en la muestra: la media del turno es
5,94 s y el tramo con tool es 10,08 s.

El trabajo que queda, en el orden en que esa medición lo dejó:

1. La ronda de tool, que es dos generaciones alrededor de una herramienta. Cualquier diseño se mide contra el LLM real. El prefetch y bajar el razonamiento de la redacción final ya se midieron y se descartaron (biblia §III.2): el primero no daba los segundos prometidos, el segundo derivó a voseo y erratas.
2. Un ejecutor de cola por conversación, para que el extractor (7,72 s, fuera del turno) no retenga las burbujas 2..N de los demás.
3. Sacar lead, CRM y notificación del camino de `send_text` en el camino `responder`. Es fiabilidad. En la muestra la cola pesó 0,74 s de mediana.
4. Tratar el `False` de `send_text` como fallo. Hoy un rechazo de Meta puede guardarse como mensaje enviado.

Meter más conocimiento por RAG, que es la tentación de G03 y G04, alarga justo
el tramo lento. Por eso los hechos estables van en el Frente B.

El arranque en frío (unos 5,9 s en el primer turno tras más de 10 minutos de
silencio) quedó medido y sin causa reproducida. No se parcha a ciegas. Los
spans `preparar-contexto`, `antecedentes-faltantes` y `resolver-especialista`
ya están para la próxima vez que ocurra.

## Orden recomendado

| Orden | Frente | Qué desbloquea | Qué lo frena |
|---|---|---|---|
| 1 | A. Promesas | G02, G06 y la mitad de G08. No espera a legal ni a comercial. | Publicar el prompt. Si hay `PromptVersion` activa en la base, el fixture no llega solo. El primer paso es `manage.py doctor` y ver qué texto está publicado. |
| 2 | B. Bloque fijo | G05, el híbrido de G04, y evita pagar RAG por la URL. | La ficha del copiloto (D2). La URL y los tres modelos no esperan. |
| 3 | E. Horario y preferencia | G07, G09 y la otra mitad de G08. | Confirmar que el horario del panel (09:00–18:00, lun–vie) es el que comercial quiere ofrecer. |
| 4 | D. Una sola derivación | G03, en cuanto exista la ficha firmada. | D2. |
| 5 | C. Consentimiento | G01. | Texto de legal y la decisión D1. |
| 6 | F. Ronda de tool | Acercar G10 al estándar de 5 s en los turnos que consultan conocimiento. | Un experimento medido. No un cambio de prompt. |

En paralelo, y fuera del código: confirmar `GRANCRM_TENANT_SLUG` del contenedor
(D3) y nombrar quién mira la cola de casos durante una demo.

## Qué queda fuera de este plan a propósito

- Construir un ticketing con folio público. La hoja dice que un número que no existe es peor que no prometerlo.
- Conectar la agenda de talleres heredada (`agendar_hora` sigue en el repo y no está bindeada). Una hora reservada de autos no cumple G08.
- Indexar `in-touch.cl` entero, ni los subdominios de concesionarios.
- Reabrir streaming, prefetch de tools o bajar el razonamiento de la redacción final.
- Borrar el dominio automotriz que sigue desregistrado. Es deuda del repo y no cambia estas diez notas.
