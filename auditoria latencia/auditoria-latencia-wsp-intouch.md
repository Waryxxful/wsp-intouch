> **CORREGIDA el 2026-09-21.** Este documento se conserva por procedencia,
> pero su premisa es incorrecta: los 7,6 s no son la latencia del turno (es
> 5,94 s de media / 3,99 s de mediana, medido) sino la media de
> `extract-metadata`, que corre después del envío. Varias de sus
> recomendaciones reabren experimentos que la biblia §III.2 ya descartó con
> evidencia. Leer antes: `2026-09-21-medicion-y-correccion.md`.

# Auditoría de latencia — wsp-intouch
Commit: 8364a62316392668c43c447195aad910ff61d28f  
Fecha: 15 de septiembre de 2026

## Conclusión
Hay trabajo evitable en la ruta de respuesta. La primera intervención debería eliminar la clasificación por IA cuando el registro efectivo tenga un solo especialista y sacar las integraciones del lead del camino previo al envío. Después, medir y reducir las rondas de herramientas/RAG y ajustar la generación con una evaluación de calidad.

Bajar de 7,6 a 5 segundos exige ahorrar 2,6 segundos, un 34,2%. Es un objetivo plausible; no está demostrado con esta auditoría. Apuntar a 4–4,5 segundos de promedio deja margen, pero es una meta de ingeniería, no una predicción.

## Alcance y límites
Inspección del commit solicitado mediante GitHub: grafo, agentes, RAG, entrada/salida WhatsApp, extracción, leads, notificaciones, configuración, contexto y simulador. Se descargaron los archivos relevantes a una copia de trabajo, sin modificar el repositorio remoto ni producción.

Se ejecutaron funciones originales aisladas mediante AST, con dependencias simuladas: el supervisor llama al clasificador aun con solo comercial; la especulación de ventas no se activa; construir mensajes con el mensaje actual en el historial lo duplica. Esto comprueba comportamiento, no rendimiento de proveedores.

No se ejecutó la suite completa ni pruebas de carga o llamadas reales al modelo. No hay acceso en esta auditoría a trazas actuales, BD de configuración, métricas del host ni entrega de Meta. Los 7,6 segundos son el dato aportado por el usuario. Los tiempos antiguos escritos en comentarios no son mediciones nuevas ni necesariamente corresponden a InTouch. La documentación central /home/admincrm/docs-repo no está disponible en este entorno Windows. No hay graphify-out en el árbol remoto; el flujo se reconstruyó desde el código.

## Ruta relevante
Mensaje → webhook sincrónico → persistencia/contexto → supervisor LLM → especialista LLM → herramientas si son necesarias → especialista LLM → persistencia/lead en algunas salidas → primer POST a Meta → cola de partes restantes y extractor.

Sin herramientas puede haber supervisor + una generación. Con una ronda de herramientas hay supervisor + generación que decide herramientas + ejecución + generación final. Las rondas adicionales vuelven a sumar ejecución y generación. El saludo inmediato es un camino distinto y no debe confundirse con resolver la consulta.

## Hallazgos priorizados

### 1. Alta: clasificador LLM para un único destino
Evidencia: [bot/flow/agents/__init__.py:19](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/agents/__init__.py#L19), [bot/flow/graph.py:706](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/graph.py#L706).

AGENTS contiene solamente comercial. build_agent_registry añade especialistas personalizados de BD, pero supervisor_node no tiene un retorno directo para len(registry)==1: sin regla de campaña ejecuta _rutear_con_el_llm. Su detección de intención de compra solo tiene efecto especial si ventas está registrado.

Recomendación: después de resolver reglas válidas de campaña y construir el registro efectivo, devolver el único agente directamente si hay uno. Conservar clasificación para registros de varios agentes. Validar que reglas heredadas no apunten a agentes inexistentes.

Impacto: elimina una llamada externa por turno afectado y sus fallos/reintentos. El ahorro real es la duración actual del span classify-intent de esos turnos. El comentario heredado menciona una mediana de 0,71 segundos, pero no permite atribuir ese valor a la media actual.

Relacionado: _AGENTE_ESPECULADO sigue siendo ventas ([bot/flow/graph.py:179](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/graph.py#L179)), y _lanzar_especulacion retorna None si no está en el registro (línea 662). No recomendar cambiarlo a comercial como primera solución: con un solo destino, eliminar el ruteo es más sencillo.

### 2. Alta, condicional: CRM y notificaciones antes de WhatsApp
Evidencia: [bot/whatsapp/handlers.py:587](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/whatsapp/handlers.py#L587), [bot/business/lead_intouch.py:197](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/business/lead_intouch.py#L197), [bot/business/lead_intouch.py:331](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/business/lead_intouch.py#L331), [bot/notify.py:59](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/notify.py#L59).

En la salida con result.lead, el handler espera registrar_lead_del_turno antes de send_text (línea 606). El registro puede notificar la transición HOT y despachar al sink. Esas integraciones usan llamadas sincrónicas con timeout de 5 y 10 segundos respectivamente. No implican que siempre se consuman esos tiempos; dependen de los datos y configuración.

La salida normal en prosa ya usa extracción posterior al envío. La afirmación general de que todo el lead ocurre después del envío no se cumple en ambos caminos.

Recomendación: persistir de manera durable el trabajo pendiente del lead y realizar notificación/despacho fuera de la respuesta. Mantener idempotencia y estados de entrega; no depender exclusivamente de un hilo volátil para conservar leads. Comprobar que la respuesta no confirma una operación que aún no se realizó.

Impacto: ahorro igual a lo que esas integraciones estén consumiendo antes del POST, solo en turnos afectados. Prioridad especialmente alta si hay picos al completar datos o generar HOT.

### 3. Alta en consultas documentales: demasiadas etapas secuenciales
Evidencia: [bot/rag/tool.py:61](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/rag/tool.py#L61), [bot/rag/tool.py:79](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/rag/tool.py#L79), [bot/rag/tool.py:143](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/rag/tool.py#L143), [bot/flow/graph.py:1260](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/graph.py#L1260).

El RAG hace embedding remoto, RPC a Supabase y rerank remoto. En el flujo habitual todo queda entre una llamada LLM que pide la herramienta y otra que redacta. Puede reformular hasta tres búsquedas por turno; el reranker tiene hasta tres intentos con backoff de 1,5 segundos.

Recomendaciones, en orden:
- Medir embedding, RPC y rerank por separado, incluyendo intentos.
- Reutilizar resultados dentro del turno cuando la consulta se repite; evaluar caché de consultas frecuentes aislada por cliente/schema y versión del corpus, sin mezclar contexto privado.
- Para consultas inequívocas, evaluar recuperación anticipada por reglas o contexto relevante precargado; evita que una llamada generativa solo decida buscar. No aplicar RAG a todos los mensajes.
- Evaluar rerank condicional con recall y respuestas fundamentadas, conservándolo en búsquedas ambiguas. No eliminarlo sin comparar calidad.
- Buscar una sola ronda útil en preguntas habituales. No paralelizar pasos que dependen del resultado anterior ni escrituras comerciales especulativas.

No se puede asignar un ahorro a esta sección sin conocer frecuencia de RAG, tiempos y calidad. Medir por tipo de consulta.

### 4. Media/alta: falta un plazo global efectivo para herramientas
Evidencia: [bot/flow/graph.py:1292](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/graph.py#L1292), [bot/flow/graph.py:273](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/graph.py#L273), [bot/rag/tool.py:88](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/rag/tool.py#L88).

El wrapper fija una fecha límite de 70 segundos, consultada al invocar LLM. No envuelve toda la ejecución en una cancelación global. Las herramientas y el reranker tienen su propia espera; la llamada RPC es sincrónica dentro de una función async.

Un httpx timeout de 10 segundos no equivale por sí solo a un presupuesto total para tres intentos. Cambiar el timeout del modelo no acota todas las etapas.

Recomendación: propagar un presupuesto restante a herramientas, red y reintentos, y reservar tiempo para una salida útil. Sacar la RPC bloqueante del event loop mediante el mecanismo apropiado o usar cliente async compatible. Priorizar rutas de fallback útiles. Bajar límites sin resolver la causa puede bajar el promedio aumentando respuestas fallidas.

### 5. Media: una cola serial comparte envíos y extracción
Evidencia: [bot/whatsapp/cola_envio.py:102](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/whatsapp/cola_envio.py#L102), [bot/whatsapp/cola_envio.py:203](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/whatsapp/cola_envio.py#L203), [bot/flow/extractor_metadatos.py:68](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/extractor_metadatos.py#L68).

Un ThreadPoolExecutor de un hilo por proceso ejecuta partes restantes, extractor y persistencia del lead. El extractor tiene presupuesto de 35 segundos. Un trabajo lento puede bloquear las partes pendientes de otras conversaciones que comparten ese proceso. El primer mensaje ordinario sale por el handler y no espera esta cola: separar la cola no debe venderse como ahorro directo de su latencia.

Los dos procesos tienen colas distintas. Esa FIFO no garantiza orden global por contacto; tampoco controla primeras partes enviadas desde handlers concurrentes.

Recomendación: separar envíos de extracción/CRM, preservar orden por conversación y durabilidad si se requieren reintentos tras reinicios. Revisar además actualizaciones de estado concurrentes: una extracción tardía puede persistir un estado anterior mientras otro turno avanza. Medir espera en cola, edad del trabajo y retraso de metadatos.

### 6. Media: medir modelo y proveedor con la respuesta completa
Evidencia: [bot/flow/graph.py:39](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/graph.py#L39), [bot/flow/graph.py:856](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/graph.py#L856), [config/settings.py:202](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/config/settings.py#L202).

La primera generación usa reasoning none; las posteriores vuelven al default medium. El cliente conversacional no fija max_tokens explícito. Modelo y clave pueden tener overrides en BD; el valor del archivo no confirma producción. Los comentarios históricos advierten degradación de calidad al bajar razonamiento en otro contexto.

Recomendación: A/B con consultas de InTouch, comparando proveedor/modelo, esfuerzo admitido, tokens generados, duración completa y calidad. Pedir respuestas concisas y poner límites de salida solo después de validar que no truncan herramientas o datos necesarios. No suponer que los modelos/proveedores de comentarios antiguos conservan hoy su rendimiento.

OpenRouter permite orden y clasificación de proveedores por latencia o throughput; son controles distintos. Fuente: [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection). Para este flujo que espera una respuesta completa, decidir por el tiempo completo medido, no únicamente por el primer token.

Streaming hacia la aplicación no adelanta por sí solo el envío de WhatsApp: el handler espera graph.ainvoke y luego manda el texto. Transmitir fragmentos requiere un diseño de mensajes, orden y calidad; no es una mejora automática al activar stream=true.

### 7. Media/baja: contexto duplicado y trabajo repetido
Evidencia: [bot/whatsapp/handlers.py:637](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/whatsapp/handlers.py#L637), [bot/flow/context_window.py:142](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/context_window.py#L142), [bot/flow/graph.py:480](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/flow/graph.py#L480).

Se guarda el mensaje del usuario antes de armar el historial, el historial lo incluye y _construir_mensajes añade state.text otra vez. Comprobado en aislamiento. El supervisor también recibe historial y texto por separado.

Recomendación: definir un contrato único: messages contiene solo turnos anteriores y text es el turno actual, o no volver a añadirlo. Excluir por identidad del mensaje, no deduplicar por texto: el usuario puede repetir legítimamente una consulta.

La ventana corta en Python tras 20 mensajes, pero la consulta no tiene LIMIT. Evaluar traer como máximo 21 mensajes recientes para conservar la detección de truncado y las fronteras de sesión/campaña.

Se consultan settings y prompts y se reconstruye el registro varias veces en cada ronda. Priorizar un snapshot coherente por turno; solo añadir caché entre turnos con invalidación/TTL y aislamiento por cliente. No atribuir segundos a estas consultas sin medir.

También se construyen clientes de embeddings, Supabase y rerank en el camino de cada búsqueda. Evaluar reutilización segura. Cuidado con clientes async globales: async_to_sync y asyncio.run crean fronteras de event loop que hay que respetar.

### 8. Alta para fiabilidad: puede guardarse como respuesta un envío rechazado
Evidencia: [bot/whatsapp/client.py:53](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/whatsapp/client.py#L53), [bot/whatsapp/handlers.py:606](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/whatsapp/handlers.py#L606), [bot/whatsapp/cola_envio.py:222](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/whatsapp/cola_envio.py#L222).

send_text devuelve False si Meta no devuelve 200, pero los llamadores mostrados ignoran el booleano y guardan el mensaje como assistant igualmente. El HTTP 4xx/5xx puede parecer respuesta procesada aunque el cliente no la reciba.

Recomendación: registrar aceptación, identificador y estado de entrega; manejar rechazo explícitamente y reintentar solo fallos apropiados con protección frente a duplicados. Los fallos no deben desaparecer del denominador al calcular el objetivo.

## Qué ya está hecho
No contar como mejoras nuevas:
- Acuse y escribiendo en executor separado.
- Primer mensaje enviado antes de partes secundarias.
- Extractor posterior al envío en prosa.
- Cliente HTTP persistente para Meta.
- Conexiones SQL persistentes configuradas a 600 segundos.
- docker-compose usa gthread con 2 procesos y 8 hilos: 16 slots nominales, no solo dos peticiones concurrentes. Varios comentarios del grafo siguen hablando de dos workers como si eso fuera toda la capacidad.
- Saludos puros tienen camino sin LLM.
- Grafo compilado reutilizado, reintentos y defensas ya existentes.

El webhook sigue esperando procesamiento antes de devolver 200. Una recepción durable seguida de procesamiento en cola puede mejorar resistencia y absorción de ráfagas, pero no reduce automáticamente el tiempo de servicio del modelo. Medir saturación antes de añadir infraestructura.

## Cómo verificar la meta
Acordar tres relojes separados:
1. Entrada al webhook → primer contenido útil aceptado por Meta.
2. Entrada al webhook → respuesta útil completa aceptada por Meta.
3. Timestamp de entrada del canal → confirmación delivered, cuando sea observable.

Registrar también cola previa al handler, ORM/contexto, clasificación, cada generación, cada herramienta, reintentos, primer/último POST, cola secundaria y extractor. Usar reloj monotónico para duraciones internas y correlación por message_id/turn_id. Un 200 de Meta confirma aceptación, no entrega al dispositivo.

El simulador usa envio_inline=True y mock de WhatsApp ([bot/simulator/app_wrapper.py:93](https://github.com/Waryxxful/wsp-intouch/blob/8364a62316392668c43c447195aad910ff61d28f/bot/simulator/app_wrapper.py#L93)): incluye trabajo posterior y excluye el transporte real. Su duración no es equivalente a la latencia percibida.

Comparar baseline/candidato con los mismos casos, carga y configuración efectiva:
- Textos simples, catálogo, RAG, captura de lead, HOT/CRM, negativa de contacto, handoff y mensajes consecutivos.
- Separar audio/imagen de texto y separar saludos de consultas útiles.
- Ejecutar varias repeticiones; primera aproximación: 100–200 turnos representativos, ampliando si la variabilidad no permite concluir.
- Concurrencia 1, 5, 10 y 20 para localizar saturación, con números de prueba y transporte controlado.
- Informar media, p50, p95, porcentaje <=5 s, errores y calidad. Propuesta de aceptación: media <=5 s en mezcla acordada, sin regresión de calidad/éxito; objetivo interno 4–4,5 s. Acordar p95 aparte.

No sumar medianas de componentes para afirmar una media total. No contar saludos vacíos ni fallbacks genéricos como consultas resueltas.

## Secuencia recomendada
1. Instrumentación y confirmación de los 7,6 s.
2. Bypass del supervisor con un único agente y corrección de contexto duplicado.
3. Separar integraciones de lead del envío; corregir estados de envío y orden.
4. Optimizar RAG/rondas donde las trazas demuestren mayor contribución.
5. A/B de modelo/proveedor/razonamiento y longitud.
6. Validar bajo carga y desplegar gradualmente con comparación de calidad y reversión.

Si la clasificación ahorra a segundos de media sobre toda la mezcla, todavía hacen falta 2,6-a segundos. El ahorro de una ruta condicional se calcula con su frecuencia: frecuencia × ahorro por turno afectado. Esta cuenta debe guiar la priorización, sin prometer segundos que aún no se midieron.

