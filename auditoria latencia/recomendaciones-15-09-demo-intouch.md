# Recomendaciones de la demo InTouch del 15-09

Documento para consumo de un modelo. Transcribe la pestaña indicada y normaliza cada observación. No evalúa si algo ya está resuelto ni propone plan de implementación.

## 1. Fuente

- **Archivo:** `/home/admincrm/wsp_intouch/auditoria latencia/Cuadro_análisis_test_Agente_Ventas_FB (4).xlsx`
- **Pestaña:** `15-09 Demo Intouch` (sheetId 8 en `xl/workbook.xml`). Es la única pestaña leída.
- **Fecha en las celdas:** no hay. La única fecha asociada al nombre de la hoja es la del título de la pestaña, `15-09`, sin año. La propiedad del archivo `dcterms:modified` (no es una celda) es `2026-09-15T19:23:04Z`.
- **Quién anota:** no figura en la hoja. Las propiedades del archivo (tampoco son celdas de la pestaña) listan como creador y última modificación a JOSEFA ANDREA BRINZO DÍAZ. No se toma eso como autoría de las notas.
- **Nombre que sí aparece en una celda:** Felipe, solo dentro del problema 1, como cita de quien habla con el agente («deberías haberme solicitado el consentimiento»). La hoja no dice su cargo.

Lectura técnica: `openpyxl` 3.1.5, con y sin `data_only`. No hay fórmulas: ambos modos devuelven el mismo texto. No hay comentarios, notas, validaciones, hipervínculos ni formato condicional. Celdas combinadas: `A1:D1` y `A2:D2`.

## 2. Cómo está armada la hoja

Tabla de análisis de 13 filas por 5 columnas (`A1:E13`). Encabezado de tabla en la fila 3. Diez problemas, uno por fila, de la fila 4 a la 13, numerados del 1 al 10 en la columna A.

| Columna | Encabezado textual | Qué es |
|---|---|---|
| A | Problema (evidencia) | Qué hizo o dejó de hacer el agente, con cita o métrica cuando la hay. |
| B | Prioridad o criticidad | Severidad escrita por la hoja. No hay otra escala. |
| C | Causa raíz | Por qué, según la nota. |
| D | Dónde se soluciona | Lugar que la nota señala (prompt, base de conocimiento, backend, infraestructura u otra fórmula breve). |
| E | Propuesta de solución | Conducta o contenido que la nota pide. |

**Severidad que usa la hoja** (texto de la columna B, no una leyenda aparte):

- `P0 — Crítico`. En el problema 1 se añade «(compliance, dominio propio de InTouch)». En el problema 2 queda solo «P0 — Crítico».
- `P1 — Alto`. En el problema 3 se añade «(afecta directamente el CTA)». En los problemas 4, 5 y 6 queda solo «P1 — Alto».
- `P2 — Medio`. Problemas 7, 8 y 10.
- `P3 — Bajo`. Problema 9.

No aparece un estado «ok», «gap» ni otra etiqueta de semáforo. No hay leyenda de colores escrita.

**Color, solo como formato, no como leyenda:**

- Fila 3 (encabezados): texto blanco `#FFFFFF`, negrita, sobre fondo sólido `#141417`.
- Columna B de los dos P0 (filas 4 y 5, problemas 1 y 2): negrita, color de fuente `#C0001F` (rojo). El resto de esas filas no va en rojo.
- Columna A de los diez problemas: negrita, color `#141417`.
- Resto del cuerpo: Calibri 12, color `#141417`, sin relleno.
- `A1`: «Cuadro detallado de análisis», Calibri 14, negrita, color `#4F81BD`.
- Fila 2: combinada `A2:D2`, estilo de subtítulo (Cambria 12, cursiva) y **sin texto**.

`E1` y `E2` están vacías y fuera de la combinación. No hay columnas con contenido más allá de la E.

## 3. Inventario fiel

Orden de la hoja. `G01` es el problema 1 (fila 4) y sigue así hasta `G10` (fila 13). No se fusionan filas. El texto de cada campo es el de la celda, completo.

### Contexto de la demo, no es un gap

- **Fila 1, `A1:D1`:** «Cuadro detallado de análisis». `E1` vacía.
- **Fila 2, `A2:D2`:** celda combinada vacía. `E2` vacía. No hay subtítulo, fecha, saludo ni métrica suelta.
- **Fila 3:** encabezados citados en la sección 2. No describen una conducta del agente.
- No hay otra fila de saludo, guion de la demo ni transcripción. Las métricas de latencia están dentro de `G10`, que sí es un gap. La cita de Felipe está dentro de `G01`, que sí es un gap.

### Gaps

#### G01 — fila 4 — problema 1

- **Prioridad:** «P0 — Crítico (compliance, dominio propio de InTouch)»
- **Problema (evidencia):** «1. Usó el teléfono del prospecto sin pedir consentimiento previo; solo lo solicitó al ser exigido, derivando en un reclamo. Felipe: "si lo tienes en tu base de datos, ¿deberías haberme solicitado el consentimiento?" → el agente reconoce que sí, tarde»
- **Causa raíz:** «El prompt pide consentimiento antes de contactar, pero no define un momento temprano y proactivo para declararlo cuando el canal ya aporta el número automáticamente (WhatsApp). En un chat que se inicia con el número ya visible, el consentimiento llegó reactivo»
- **Dónde se soluciona:** «Prompt del agente + diseño de flujo de datos»
- **Propuesta de solución:** «Declarar el uso de datos y pedir consentimiento de forma proactiva y temprana, apenas la conversación deriva a captura de lead — no esperar a pedirlo al final ni a que el prospecto lo reclame. Definir con legal el texto exacto y el momento. Especialmente crítico porque InTouch vende justamente compliance de datos»

#### G02 — fila 5 — problema 2

- **Prioridad:** «P0 — Crítico»
- **Problema (evidencia):** «2. Prometió un "reclamo registrado internamente" y un contacto "hoy", capacidades que no existen (es una prueba sin backend). "Tu caso quedó registrado internamente como reclamo" y luego no puede dar número de caso porque no existe»
- **Causa raíz:** «El agente afirma acciones de sistema (registrar reclamo, derivar, agendar contacto) que hoy no tienen integración real detrás — el mismo patrón de "agendamiento simulado" ya visto en los agentes automotrices»
- **Dónde se soluciona:** «Backend (integración real) + Prompt (mientras no exista)»
- **Propuesta de solución:** «Igual que lo recomendado para los otros agentes: mientras no haya CRM/ticketing real conectado, el agente no debe decir "quedó registrado" como si fuera automático. Debe decir "dejo tu caso listo para que el equipo lo tome" y alguien de InTouch debe monitorear la conversación en vivo. Un reclamo sin número real que el prospecto pueda usar es peor que no prometerlo»

#### G03 — fila 6 — problema 3

- **Prioridad:** «P1 — Alto (afecta directamente el CTA)»
- **Problema (evidencia):** «3. Exceso de derivación: "prefiero que lo valide un especialista" repetido ~20 veces, debilitando el objetivo comercial. Casi toda pregunta de fondo (precio, resultados, años, API, referencias) termina en derivación»
- **Causa raíz:** «La base de conocimiento está tan vacía de datos comerciales concretos que el agente no tiene con qué responder, y su única salida honesta es derivar. El problema no es la honestidad — es la falta de contenido cargado»
- **Dónde se soluciona:** «Base de conocimiento (prioritario) + Prompt (calibración)»
- **Propuesta de solución:** «Cargar en la base de conocimiento los datos comerciales que SÍ se pueden compartir: años de InTouch en el mercado, tamaño de la operación, rangos de resultados típicos (con la debida cautela), casos de éxito autorizados, el propio sitio web. Cada uno de esos datos hoy ausentes es una derivación evitable y una oportunidad de convicción perdida»

#### G04 — fila 7 — problema 4

- **Prioridad:** «P1 — Alto»
- **Problema (evidencia):** «4. No conoce su propio producto ("IA copiloto" del sitio web, modelo híbrido preguntado dos veces). Ante el copiloto que aparece en in-touch.cl, admite no tenerlo en su base»
- **Causa raíz:** «Desalineación entre el sitio web de InTouch y la base de conocimiento del agente — el agente no fue alimentado con el contenido del propio sitio que dice representar»
- **Dónde se soluciona:** «Base de conocimiento»
- **Propuesta de solución:** «Sincronizar la base de conocimiento del agente con el contenido real y vigente del sitio web (idealmente vía RAG sobre el propio sitio). Es contradictorio y daña credibilidad que el asistente oficial no conozca lo que la empresa publica de sí misma»

#### G05 — fila 8 — problema 5

- **Prioridad:** «P1 — Alto»
- **Problema (evidencia):** «5. No entregó el enlace del propio sitio web al pedirlo. "El enlace directo del sitio web no lo tengo a la mano" — es la URL de la empresa que representa»
- **Causa raíz:** «El dato más básico e inequívocamente seguro de compartir (la URL pública propia) no está en la base de conocimiento»
- **Dónde se soluciona:** «Base de conocimiento (arreglo trivial)»
- **Propuesta de solución:** «Cargar la URL del sitio y los enlaces públicos clave (soluciones, casos, contacto) como dato siempre disponible. No hay ninguna razón de anti-alucinación para no dar la propia URL pública — es verificable al instante»

#### G06 — fila 9 — problema 6

- **Prioridad:** «P1 — Alto»
- **Problema (evidencia):** «6. Recomendó al prospecto cómo reclamar contra InTouch ante SERNAC, con instrucciones detalladas. Guía paso a paso para levantar el reclamo, nombrando a InTouch como proveedor»
- **Causa raíz:** «No hay una regla que module qué hacer cuando el prospecto pide ayuda para reclamar contra la propia empresa — el agente trató la solicitud como una consulta de información pública cualquiera»
- **Dónde se soluciona:** «Prompt del agente»
- **Propuesta de solución:** «El agente debe reconocer el derecho del usuario a reclamar y facilitar el canal formal interno primero (y la Agencia de Protección de Datos como corresponde), sin convertirse en un tutorial activo de cómo presentar un reclamo de consumo contra InTouch. Es un equilibrio delicado: transparente y respetuoso del derecho, pero no autoflagelante»

#### G07 — fila 10 — problema 7

- **Prioridad:** «P2 — Medio»
- **Problema (evidencia):** «7. Recomendación de horario que prioriza velocidad sobre el interés real del prospecto. Ante "¿qué fecha es mejor para el ejecutivo?", sugiere "hoy a las 22:00" — un horario no laboral»
- **Causa raíz:** «El agente optimiza por "resolver rápido" sin considerar que 22:00 no es un horario comercial razonable ni conveniente para una reunión de calidad»
- **Dónde se soluciona:** «Prompt del agente»
- **Propuesta de solución:** «Al sugerir horarios, priorizar franjas laborales razonables. Aceptar la preferencia del cliente si insiste, pero no proponer proactivamente un horario nocturno como primera opción»

#### G08 — fila 11 — problema 8

- **Prioridad:** «P2 — Medio»
- **Problema (evidencia):** «8. Aceptó coordinar un contacto "hoy a las 22:00" sin poder cumplirlo. Confirma el horario nocturno como preferencia registrada»
- **Causa raíz:** «Mismo problema de fondo que el #2: coordina algo que no puede garantizar»
- **Dónde se soluciona:** «Prompt + backend»
- **Propuesta de solución:** «Ligado a la solución del #2 — registrar preferencia sin comprometer, y con horarios laborales por defecto»

#### G09 — fila 12 — problema 9

- **Prioridad:** «P3 — Bajo»
- **Problema (evidencia):** «9. Cierres largos y repetitivos en la fase final. Los últimos ~10 turnos repiten "priorizando hoy a las 22:00 hrs, con el viernes 18 como respaldo" en casi cada mensaje»
- **Causa raíz:** «Falta de variación y de detección de que el punto ya está acordado»
- **Dónde se soluciona:** «Prompt (estilo)»
- **Propuesta de solución:** «Una vez acordado un punto, no repetirlo completo en cada mensaje siguiente — referirlo brevemente. Mismo criterio de "no reabrir lo ya cerrado" aplicado en los otros agentes»

#### G10 — fila 13 — problema 10

- **Prioridad:** «P2 — Medio»
- **Problema (evidencia):** «10. Latencia: aunque es la mejor del proyecto, aún 0 respuestas… (corrección: sí hubo bajo 5s). 39% bajo 5 seg, pero el promedio (7,9 seg) todavía no cumple el estándar de 5 seg en todos los turnos»
- **Causa raíz:** «Tiempo de inferencia + generación de ráfagas multi-burbuja»
- **Dónde se soluciona:** «Infraestructura»
- **Propuesta de solución:** «Es la más cercana al objetivo de todo el proyecto; ver sección 5»

## 4. Recomendaciones normalizadas para implementar

Hay **una** recomendación por gap. No se fusionan observaciones distintas. Si un gap toca más de un tema, la ficha completa está en un solo tema y el otro solo remite.

| Id | Ficha en | Prioridad de la hoja |
|---|---|---|
| G01 | Lead, datos del cliente, derivación a humano / CRM | P0 — Crítico (compliance) |
| G02 | Lead, datos del cliente, derivación a humano / CRM | P0 — Crítico |
| G03 | Precios, propuesta comercial, objeciones | P1 — Alto (afecta directamente el CTA) |
| G04 | Conocimiento de producto / soluciones InTouch | P1 — Alto |
| G05 | Conocimiento de producto / soluciones InTouch | P1 — Alto |
| G06 | Identidad, tono y rol del agente | P1 — Alto |
| G07 | Agenda, seguimiento, cierre | P2 — Medio |
| G08 | Agenda, seguimiento, cierre | P2 — Medio |
| G09 | Formato de respuesta en WhatsApp | P3 — Bajo |
| G10 | Latencia o experiencia de espera | P2 — Medio |

No hay observaciones de apertura, calificación ni descubrimiento. Ese tema no se desarrolla.

### Identidad, tono y rol del agente

#### No tutelar un reclamo de consumo contra InTouch

- **Ids:** G06
- **Prioridad en la hoja:** P1 — Alto
- **Dónde lo ubica la nota:** «Prompt del agente»
- **Hecho observado:** El agente recomendó al prospecto cómo reclamar contra InTouch ante el SERNAC, con instrucciones detalladas y una guía paso a paso, y nombró a InTouch como proveedor. La nota dice que trató el pedido como una consulta de información pública cualquiera, porque no hay una regla para cuando piden ayuda para reclamar contra la propia empresa.
- **Esperado por el gerente:** «El agente debe reconocer el derecho del usuario a reclamar y facilitar el canal formal interno primero (y la Agencia de Protección de Datos como corresponde), sin convertirse en un tutorial activo de cómo presentar un reclamo de consumo contra InTouch.» La nota llama a eso un equilibrio «transparente y respetuoso del derecho, pero no autoflagelante». No pega el texto que el agente debería decir ni el texto del tutorial que dio.
- **Cita:** «Guía paso a paso para levantar el reclamo, nombrando a InTouch como proveedor»
- **Tipo:** otro
- **Dependencia:** Ninguna.

### Conocimiento de producto / soluciones InTouch

La omisión de datos comerciales de empresa (años, tamaño, resultados, casos, sitio) no se repite aquí: es G03, en Precios / propuesta comercial.

#### Responder el copiloto de IA y el modelo híbrido con lo que publica el sitio

- **Ids:** G04
- **Prioridad en la hoja:** P1 — Alto
- **Dónde lo ubica la nota:** «Base de conocimiento»
- **Hecho observado:** No conocía su propio producto. La nota nombra «IA copiloto» del sitio web y dice que el modelo híbrido fue preguntado dos veces. Ante el copiloto que aparece en `in-touch.cl`, el agente admitió no tenerlo en su base. La nota atribuye el fallo a que la base no fue alimentada con el contenido del sitio que el agente dice representar. No transcribe la respuesta sobre el modelo híbrido.
- **Esperado por el gerente:** «Sincronizar la base de conocimiento del agente con el contenido real y vigente del sitio web (idealmente vía RAG sobre el propio sitio).» La nota añade que es contradictorio, y daña la credibilidad, que el asistente oficial no conozca lo que la empresa publica de sí misma.
- **Cita:** «Ante el copiloto que aparece en in-touch.cl, admite no tenerlo en su base»
- **Tipo:** omisión
- **Dependencia:** Ninguna. G05 pide además entregar la URL; no hace falta G05 para juzgar si el agente conoce el copiloto y el modelo híbrido.

#### Entregar la URL pública y los enlaces de soluciones, casos y contacto

- **Ids:** G05
- **Prioridad en la hoja:** P1 — Alto
- **Dónde lo ubica la nota:** «Base de conocimiento (arreglo trivial)»
- **Hecho observado:** Se le pidió el enlace del propio sitio y no lo entregó. Dijo: «El enlace directo del sitio web no lo tengo a la mano». La nota marca que esa URL es la de la empresa que representa y que es el dato más básico e inequívocamente seguro de compartir, y que no está en la base.
- **Esperado por el gerente:** «Cargar la URL del sitio y los enlaces públicos clave (soluciones, casos, contacto) como dato siempre disponible.» La nota dice que no hay razón de anti-alucinación para negar la URL pública propia, porque es verificable al instante. No escribe la URL literal en esta fila (el dominio `in-touch.cl` aparece en G04, no aquí).
- **Cita:** «El enlace directo del sitio web no lo tengo a la mano»
- **Tipo:** omisión
- **Dependencia:** Ninguna. Solapa con G03 solo en «el propio sitio web»; G05 es la observación distinta de no entregar el enlace cuando se pidió, más los enlaces de soluciones, casos y contacto.

### Precios, propuesta comercial, objeciones

#### Cargar los datos comerciales compartibles y dejar de derivar esas preguntas

- **Ids:** G03
- **Prioridad en la hoja:** P1 — Alto (afecta directamente el CTA)
- **Dónde lo ubica la nota:** «Base de conocimiento (prioritario) + Prompt (calibración)»
- **Hecho observado:** Exceso de derivación. Repitió «prefiero que lo valide un especialista» unas veinte veces («~20», aproximado) y debilitó el objetivo comercial. Casi toda pregunta de fondo terminó en derivación. La nota enumera esas preguntas: precio, resultados, años, API, referencias. Atribuye la causa a una base vacía de datos comerciales concretos: la derivación sería la única salida honesta, y «el problema no es la honestidad — es la falta de contenido cargado». La columna D también nombra calibración del prompt, pero la propuesta no dice en qué consiste esa calibración.
- **Esperado por el gerente:** «Cargar en la base de conocimiento los datos comerciales que SÍ se pueden compartir: años de InTouch en el mercado, tamaño de la operación, rangos de resultados típicos (con la debida cautela), casos de éxito autorizados, el propio sitio web.» Cada dato ausente es, según la nota, una derivación evitable y una oportunidad de convicción perdida. La nota no explicita un precio, una cifra, ni el contenido de una API para responder. Tampoco dice que precio y API deban seguir derivándose.
- **Cita:** «Casi toda pregunta de fondo (precio, resultados, años, API, referencias) termina en derivación»
- **Tipo:** derivación
- **Dependencia:** Ninguna para el criterio general. El ítem «el propio sitio web» de esta lista se solapa con G05; no es la misma observación.

### Lead, datos del cliente, derivación a humano / CRM

El volumen de derivaciones al especialista es G03 y su ficha está en Precios / propuesta comercial. No se repite.

#### Declarar el uso del teléfono y pedir consentimiento al pasar a la captura del lead

- **Ids:** G01
- **Prioridad en la hoja:** P0 — Crítico (compliance, dominio propio de InTouch)
- **Dónde lo ubica la nota:** «Prompt del agente + diseño de flujo de datos»
- **Hecho observado:** Usó el teléfono del prospecto sin consentimiento previo. Solo lo pidió cuando se lo exigieron, y eso derivó en un reclamo. Felipe preguntó: «si lo tienes en tu base de datos, ¿deberías haberme solicitado el consentimiento?». El agente reconoció que sí, tarde. La nota dice que el prompt ya pide consentimiento antes de contactar, pero no fija un momento temprano y proactivo para declararlo cuando WhatsApp ya trae el número. En un chat que parte con el número visible, el consentimiento llegó reactivo.
- **Esperado por el gerente:** «Declarar el uso de datos y pedir consentimiento de forma proactiva y temprana, apenas la conversación deriva a captura de lead — no esperar a pedirlo al final ni a que el prospecto lo reclame. Definir con legal el texto exacto y el momento.» La nota lo marca especialmente crítico porque InTouch vende compliance de datos. El texto legal exacto y el momento ya cerrado con legal no están en la hoja.
- **Cita:** «Usó el teléfono del prospecto sin pedir consentimiento previo; solo lo solicitó al ser exigido»
- **Tipo:** omisión
- **Dependencia:** Ninguna.

#### No decir que el reclamo o el contacto quedaron registrados si el sistema no lo hizo

- **Ids:** G02
- **Prioridad en la hoja:** P0 — Crítico
- **Dónde lo ubica la nota:** «Backend (integración real) + Prompt (mientras no exista)»
- **Hecho observado:** Prometió un «reclamo registrado internamente» y un contacto «hoy». La nota dice que esas capacidades no existen porque es una prueba sin backend. Dijo «Tu caso quedó registrado internamente como reclamo» y después no pudo dar número de caso porque no existe. La causa que escribe la nota: el agente afirma acciones de sistema (registrar reclamo, derivar, agendar contacto) sin integración real detrás; lo asimila al «agendamiento simulado» de los agentes automotrices. Esta fila junta las dos promesas (reclamo registrado y contacto hoy); no se parte en dos gaps.
- **Esperado por el gerente:** «Mientras no haya CRM/ticketing real conectado, el agente no debe decir "quedó registrado" como si fuera automático. Debe decir "dejo tu caso listo para que el equipo lo tome" y alguien de InTouch debe monitorear la conversación en vivo.» Cierra así: «Un reclamo sin número real que el prospecto pueda usar es peor que no prometerlo.» La frase que la nota manda decir es exactamente «dejo tu caso listo para que el equipo lo tome». No describe el sistema de CRM ni el texto de un número de caso.
- **Cita:** «Tu caso quedó registrado internamente como reclamo»
- **Tipo:** promesa indebida
- **Dependencia:** Ninguna. G08 depende de esta ficha; esta ficha no depende de G08. El contacto «hoy» de G02 no es lo mismo que la hora 22:00 de G07 y G08.

### Agenda, seguimiento, cierre

#### No ofrecer las 22:00 como primera opción de reunión

- **Ids:** G07
- **Prioridad en la hoja:** P2 — Medio
- **Dónde lo ubica la nota:** «Prompt del agente»
- **Hecho observado:** Ante «¿qué fecha es mejor para el ejecutivo?», sugirió «hoy a las 22:00». La nota lo llama horario no laboral y dice que la recomendación prioriza la velocidad sobre el interés real del prospecto: el agente optimiza por «resolver rápido» sin considerar que las 22:00 no son un horario comercial razonable ni conveniente para una reunión de calidad.
- **Esperado por el gerente:** «Al sugerir horarios, priorizar franjas laborales razonables. Aceptar la preferencia del cliente si insiste, pero no proponer proactivamente un horario nocturno como primera opción.» La nota no fija el rango horario (hora de inicio y de fin). Cómo se concilia «aceptar la preferencia si insiste» con G08 está en la sección 5.
- **Cita:** «sugiere "hoy a las 22:00" — un horario no laboral»
- **Tipo:** flujo de conversación
- **Dependencia:** Ninguna para no proponer las 22:00 como primera opción. No depende de G02.

#### Registrar la preferencia de las 22:00 sin prometer un contacto que no puede cumplir

- **Ids:** G08
- **Prioridad en la hoja:** P2 — Medio
- **Dónde lo ubica la nota:** «Prompt + backend»
- **Hecho observado:** Aceptó coordinar un contacto «hoy a las 22:00» sin poder cumplirlo, y confirmó ese horario nocturno como preferencia registrada. La nota dice que es el mismo problema de fondo que el #2: coordina algo que no puede garantizar. Es una fila distinta de G02 y de G07: aquí el fallo es confirmar la coordinación, no solo haber sugerido la hora.
- **Esperado por el gerente:** «Ligado a la solución del #2 — registrar preferencia sin comprometer, y con horarios laborales por defecto.» No hay otra conducta escrita en esta fila.
- **Cita:** «Aceptó coordinar un contacto "hoy a las 22:00" sin poder cumplirlo»
- **Tipo:** promesa indebida
- **Dependencia:** G02. La propia nota liga la solución al #2 (no decir que algo quedó registrado o agendado si no hay integración; dejar el caso listo para el equipo y que alguien de InTouch monitoree). Sin esa regla, «registrar preferencia sin comprometer» no se puede evaluar.

### Cumplimiento, promesas, límites

No hay un quinto gap. Las fichas completas, sin duplicar:

- **G01** (P0, consentimiento y teléfono): ficha en Lead, datos del cliente, derivación a humano / CRM. La hoja lo etiqueta compliance y dominio propio de InTouch.
- **G02** (P0, reclamo «registrado» y contacto «hoy» sin backend): ficha en la misma sección de Lead. Es la promesa indebida de una acción de sistema.
- **G06** (P1, tutorial ante el SERNAC contra InTouch): ficha en Identidad, tono y rol del agente. Límite de rol, no una promesa de agenda.
- **G08** (P2, confirmar las 22:00 sin poder cumplirlo): ficha en Agenda, seguimiento, cierre. Depende de G02.

G05 no es una promesa indebida. La nota dice que negar la URL pública no se justifica por anti-alucinación. La ficha está en Conocimiento de producto.

G03 dice de forma explícita que el problema «no es la honestidad». No se trata aquí como un caso de invención.

### Formato de respuesta en WhatsApp

No hay en la hoja notas de tuteo o ustedeo, emojis, listas ni de partir la respuesta en varios mensajes como problema de formato. Las «ráfagas multi-burbuja» aparecen solo como causa de latencia en G10.

#### No repetir el cierre entero cuando el punto ya quedó acordado

- **Ids:** G09
- **Prioridad en la hoja:** P3 — Bajo
- **Dónde lo ubica la nota:** «Prompt (estilo)»
- **Hecho observado:** Cierres largos y repetitivos en la fase final. Los últimos diez turnos, aproximadamente («~10»), repiten en casi cada mensaje: «priorizando hoy a las 22:00 hrs, con el viernes 18 como respaldo». La causa que escribe la nota es falta de variación y de detección de que el punto ya está acordado.
- **Esperado por el gerente:** «Una vez acordado un punto, no repetirlo completo en cada mensaje siguiente — referirlo brevemente.» Invoca el mismo criterio de «no reabrir lo ya cerrado» aplicado en los otros agentes. No describe cómo es ese criterio en los otros agentes ni qué cuenta como referencia breve.
- **Cita:** «priorizando hoy a las 22:00 hrs, con el viernes 18 como respaldo»
- **Tipo:** formato
- **Dependencia:** Ninguna. El horario que se repite es el de G07 y G08, pero la falla de esta fila es la repetición, no la elección de la hora.

### Latencia o experiencia de espera

#### Acercar todos los turnos al estándar de 5 segundos que enuncia la nota

- **Ids:** G10
- **Prioridad en la hoja:** P2 — Medio
- **Dónde lo ubica la nota:** «Infraestructura»
- **Hecho observado:** La celda se corrige a sí misma en el mismo renglón, sin tachado: primero «aún 0 respuestas…» y enseguida «(corrección: sí hubo bajo 5s)». La cifra que queda escrita después de la corrección es «39% bajo 5 seg» y promedio «7,9 seg». La nota dice que ese promedio todavía no cumple «el estándar de 5 seg en todos los turnos», y que aun así es la mejor latencia del proyecto. La causa que escribe: «Tiempo de inferencia + generación de ráfagas multi-burbuja». No hay en esta pestaña la comparación con los otros proyectos.
- **Esperado por el gerente:** La nota fija como estándar no cumplido el de 5 segundos en todos los turnos. La propuesta no añade otra conducta: «Es la más cercana al objetivo de todo el proyecto; ver sección 5». La nota no explicita el comportamiento esperado más allá de ese estándar y de esa remisión. La sección 5 no está en esta hoja.
- **Cita:** «39% bajo 5 seg, pero el promedio (7,9 seg) todavía no cumple el estándar de 5 seg en todos los turnos»
- **Tipo:** otro
- **Dependencia:** Ninguna dentro de esta hoja. La remisión a «sección 5» no se puede resolver con estas celdas.

### Otros

No hay gaps fuera de los temas anteriores.

## 5. Lo que la hoja NO dice

- El autor de las notas no está en las celdas. El nombre de las propiedades del archivo no se puede tratar como firma de esta pestaña.
- G10 afirma y corrige en la misma frase: «0 respuestas» bajo 5 segundos queda desmentido por «sí hubo bajo 5s» y por el 39 %. No hay que implementar la primera cláusula. «Ver sección 5» no tiene destino en esta pestaña.
- G04 no transcribe qué contestó el agente sobre el modelo híbrido. Solo dice que fue preguntado dos veces y que el copiloto de `in-touch.cl` no está en su base.
- G03 nombra precio y API entre las preguntas que acaban en derivación, y no los pone en la lista de datos a cargar. No hay precio, rango de precio ni descripción de API esperada, ni la orden de seguir derivando esas dos.
- G01 manda definir con legal el texto exacto y el momento del consentimiento. Ese texto no está en la hoja.
- G07 dice aceptar la preferencia del cliente si insiste en el horario. G08, sobre las mismas 22:00, dice registrar la preferencia sin comprometer y usar horarios laborales por defecto. La hoja no aclara si «aceptar» autoriza a confirmar la reunión a las 22:00 o solo a anotar el deseo.
- «~20 veces» y «~10 turnos» son aproximaciones. No hay transcripción ni lista de turnos.
- «Viernes 18» no trae mes ni año. La pestaña se llama 15-09, pero esa fecha no está escrita en las celdas.
- La fila 2 está combinada y vacía. No se sabe si iba un subtítulo.
- G06 describe una guía paso a paso ante el SERNAC y no la cita.
- G05 no escribe la URL completa. El único dominio de la pestaña es `in-touch.cl`, en G04.
