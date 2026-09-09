# CompAI CRM — recepción y gestión de leads del bot implementado

Fecha de revisión: 2026-09-09. Estado: **PLAN CORREGIDO; IMPLEMENTACIÓN NO VALIDADA EN EL SERVIDOR**.

Este documento reemplaza íntegramente el plan anterior de 14 tareas. Está preparado para la IA implementadora. No ejecutar en paralelo los bloques de código antiguos: varios contradicen sus propios criterios de aceptación.

## 1. Objetivo y alcance

Recibir en CompAI los leads emitidos por el bot comercial ya implementado; conservar sus 22 campos; mostrar información accionable al comercial autorizado por GranCRM; tolerar reintentos y entregas desordenadas sin duplicar efectos ni declarar éxitos falsos.

**Sólo se implementa el lado CRM.** El bot es un productor existente cuyo contrato se inspecciona en modo lectura. No rehacer extractor, modelos, transporte, panel, cron o migraciones del bot. Las antiguas Tasks 10–13 quedan retiradas y se reemplazan por comprobaciones de compatibilidad. Si aparece una carencia en el emisor que impide garantías, documentar exactamente la brecha; no cambiar el bot bajo este plan ni afirmar que el CRM puede resolver por sí solo la pérdida de eventos que nunca recibió.

El CRM, su PostgreSQL y Better Auth serán self-hosted. GranCRM gobierna el acceso humano conforme al plan `2026-09-09-compai-crm-acceso-comercial-corregido.md`. La credencial técnica de ingesta no concede acceso humano ni acceso general al CRM. No habilitar agentes autónomos, correo, enriquecimiento externo o proveedores de identidad externos para completar esta integración.

## 2. Instrucciones de ejecución para la IA

- Inspeccionar la ficha CRM en `/home/admincrm/docs-repo`, instrucciones locales, estado Git y versiones antes de editar. Respetar cambios ajenos.
- Resolver las tareas R0–R8 en orden. Registrar evidencia; no sustituir ejecución por texto “Expected: PASS”.
- Reutilizar módulos, servicios, validadores y migraciones del checkout real cuando satisfagan los invariantes. No imponer APIs, enums, rutas ni versiones del documento antiguo.
- Mantener una implementación pequeña: receptor autenticado, contrato, persistencia transaccional, proyección comercial y diagnóstico. No agregar otra cola, ORM o framework si no se necesita.
- Los ejemplos del plan anterior son antecedentes no verificados. Ningún `findFirst`, decorador de sesión o nombre de helper demuestra por sí solo una garantía.
- Buscar primero los datos faltantes en configuración y documentación. Preguntar sólo decisiones de negocio o accesos imprescindibles que no estén ya autorizados; seguir con trabajo independiente.
- Preparar y probar los cambios antes de cualquier aprobación de despliegue aún necesaria. No repetir aprobaciones ya otorgadas para el mismo alcance. La corrección de este documento no equivale a desplegarlo.
- Mantener separados los estados IMPLEMENTADO, VALIDADO EN ENTORNO AISLADO, DESPLEGADO y VALIDADO EN DESTINO.

## R0. Fijar versión y contrato con el bot existente

**Entregables:** `docs/integracion-crm/inventario.md` y `contrato-ingesta.md`.

- [ ] Registrar remoto, commit, lockfile y versiones efectivas de CRM, Prisma, Better Auth, runtime y Compose. Si se elige una release, verificar el tag y fijar su SHA; clonar la rama `release` no fija la versión `v1.15.3`.
- [ ] Revisar la especificación referenciada por los planes si está disponible. Si falta, anotarlo; estos documentos no demuestran su contenido.
- [ ] Leer el serializador y las pruebas actuales del emisor, sin modificarlo. Capturar fixtures anonimizados de primer envío, actualización y reintento.
- [ ] Confirmar ruta, método, cabecera de autenticación, timeout, política de redirects, formato exacto de IDs/revisión, respuesta aceptada y persistencia de pendientes. No inferirlos del plan del bot ya superado.
- [ ] Verificar que un reintento conserva identificador, revisión y contenido; que una actualización genera otro evento; que una respuesta de un envío anterior no marca como entregada una revisión más nueva.
- [ ] Distinguir snapshot completo de patch. El algoritmo de orden de este plan presupone snapshots acumulados; con patches no se puede descartar una revisión anterior sin comprobar que sus datos estén incluidos o procesarla en orden.
- [ ] Documentar si los tres booleanos admiten ausencia/null/false. El contrato anterior decía tres triestados, pero exigía dos booleanos obligatorios: resolverlo con el emisor real, sin convertir desconocido en false.
- [ ] Verificar que el bot puede funcionar con el contrato seguro. Adaptar el receptor donde sea compatible. Si no existen IDs/revisiones durables, no fabricarlos por petición ni deducir orden de timestamps de recepción: eso no garantiza idempotencia ni orden.
- [ ] Si hay incompatibilidad bloqueante, terminar el CRM y sus pruebas de contrato aisladas; dejar la activación pendiente con la mínima brecha del bot descrita. No convertirla automáticamente en una tarea de reimplementación.

**Aceptación:** contrato real y ejemplos disponibles; diferencias explícitas respecto a este diseño. Las referencias `origen`, `clave_contacto`, `evento_id` y `revision` de las tareas siguientes se mapean a ese contrato sin pérdida de significado.

## R1. Preparar CRM, base de pruebas y despliegue reproducible

**Ámbito:** repo CompAI, Dockerfiles si hacen falta, Compose propio y herramientas de prueba.

- [ ] Usar un archivo Compose autónomo para este despliegue y referenciarlo siempre con `-f`; así no se heredan servicios/puertos del Compose upstream por accidente. Si se decide combinar archivos, inspeccionar el modelo fusionado real.
- [ ] No usar `ports: []` en un override para suponer que elimina puertos heredados: las listas pueden combinarse. Comprobar servicios y puertos resultantes, con salida redactada para no mostrar secretos.
- [ ] PostgreSQL sin puertos publicados, con volumen persistente y credenciales propias. No reutilizar `postgres:postgres`. Dar a la aplicación y a las pruebas usuarios de privilegio mínimo y bases separadas.
- [ ] La base de prueba debe estar aislada también por credenciales/permisos, no sólo por nombre `_test`. El runner fija `DATABASE_URL` de prueba antes de importar el cliente y falla si no coincide con el destino autorizado. No usar `docker compose exec api bun test` en el servicio de producción.
- [ ] Generar Prisma Client y compilar con los scripts y versiones del repo. Verificar que la imagen ejecuta realmente el código construido y soporta las dependencias del monorepo; no asumir que ejecutar `main.ts` directamente bajo Bun equivale al build Nest.
- [ ] Incluir los workspaces requeridos por el lockfile y separar build/runtime. Añadir `.dockerignore` para secretos, `.git`, bases, dumps y artefactos locales. Nunca copiar `.env` con `COPY . .` al contexto sin exclusión.
- [ ] Proporcionar sólo variables públicas al build del frontend y secretos exclusivamente a los procesos de servidor que los requieren. Registrar por variable si se consume al compilar o al ejecutar.
- [ ] Separar imagen de pruebas/herramientas de imagen inmutable de despliegue. Un script agregado en el host después del build no aparece mágicamente en el contenedor; reconstruir la imagen correspondiente o usar un montaje de desarrollo explícito en el entorno aislado.
- [ ] Configurar health/readiness según rutas reales. Readiness comprueba base y configuración necesaria del módulo; no activar tráfico de ingesta con campos/migraciones incompletos.
- [ ] Para host gateway, publicar sólo frontend/API en loopback. Desde contenedores usar DNS y puerto internos. En red compartida de ingesta conectar únicamente los servicios necesarios; PostgreSQL permanece en red privada CRM.
- [ ] No hacer migraciones en cada arranque de la API. Usar un paso de release explícito con migraciones revisadas, respaldo y coordinación del despliegue.
- [ ] Crear migraciones con `migrate dev` exclusivamente en base de desarrollo y shadow database aisladas. Versionar SQL revisado y aplicar con `migrate deploy` en el destino autorizado. No usar `db push` ni generar migraciones contra producción.
- [ ] Comprobar historial de migraciones y esquema real. `migrate status` compara historiales; no certifica por sí solo ausencia de drift. Si hay una base preexistente, inspeccionar diferencias con las herramientas compatibles con la versión instalada.
- [ ] Guardar referencia de imagen y configuración anteriores; probar restauración de backup en aislamiento. No improvisar rollback destructivo de esquema.

**Aceptación:** build reproducible, pruebas incapaces de escribir en producción, ningún puerto de base publicado y secretos ausentes del build. R1 habilita A0–A4 del plan de acceso.

## R2. Autenticar y limitar exclusivamente la ingesta

**Entregables:** guard de integración, configuración, gestión de credencial y pruebas HTTP reales.

- [ ] Verificar el plugin/API key instalado, sus permisos y la interacción con los guards globales. Un header presente no demuestra que esa key sea la identidad autenticada: probar cookie válida + key inválida y rechazarla.
- [ ] Preferir la verificación soportada de una credencial limitada al recurso/acción `intouch:ingest`, con principal y origen fijos del servidor. La cuenta de destino también es configuración del servidor.
- [ ] Si las keys existentes se convierten en sesiones generales, impedir ese comportamiento para la credencial de integración y probarlo en todas las superficies. Si la versión no permite separar ese alcance, usar un mecanismo de credencial de integración aislado del login, con biblioteca/verificación probada; documentar la elección antes de implementarla.
- [ ] No crear una key de usuario con acceso total y llamarla “limitada” por comprobar su usuario en un único endpoint. La credencial del bot debe fallar en contactos, exportaciones, usuarios, ajustes, auth y tRPC fuera de su ruta.
- [ ] No aceptar cookies de navegador como autenticación de ingesta, aunque exista una sesión comercial. No aceptar keys de otros usuarios o integraciones.
- [ ] Provisionar la credencial sin imprimirla en terminal ni incluirla en argumentos, commits o informes. Guardarla en el almacén/configuración privada existente con permisos mínimos; mostrar sólo ID o huella no reutilizable.
- [ ] Hacer idempotente el aprovisionamiento del principal. No regenerar una key cada vez que se ejecuta una semilla. Si una key no es recuperable, indicar que ya existe y gestionar rotación explícita, no emitir otra en silencio.
- [ ] Definir expiración, rotación y revocación; probar el rechazo de una key revocada y el relevo sin perder pendientes. Aplicar límites en el receptor también al tráfico interno directo.
- [ ] Limitar tamaño del body antes de cargarlo íntegro, tipos MIME, tiempo y frecuencia. Si el framework usa bodyParser deshabilitado, registrar el parser sólo para esta ruta sin romper Better Auth/tRPC. Responder 400 a JSON inválido, 413 a exceso y 415 a tipo no admitido.
- [ ] El gateway público no expone la ruta de ingesta. No confiar sólo en esa restricción de red: el guard se ejecuta igualmente por la red interna.

**Aceptación:** key válida sólo ingiere; una key inválida acompañada de cookie válida no pasa; key de ingesta no lee el CRM. Rate limit efectivo en la ruta por la que realmente envía el bot.

## R3. Definir datos, proyección y migraciones

**Entregables:** contrato validado, modelo persistente, migraciones y configuración de campos visible en la UI.

### Contrato de entrada

- [ ] Validar sólo las claves acordadas; rechazar campos internos enviados por el body, como dueño, etapa, IDs CRM y cuenta de destino. No asignar el body directamente al ORM.
- [ ] Distinguir metadata de transporte de los **22 campos de negocio** siguientes. `origen`, `clave_contacto`, `evento_id` y `revision` no cuentan entre esos 22.
- [ ] Usar tipos JSON reales, enteros positivos dentro del rango seguro compatible con almacenamiento, límites de longitud/cantidad y teléfono normalizado según el contrato real. No exigir un UUID o hash de 32 caracteres si el emisor implementado usa otro identificador válido.
- [ ] Preservar el payload de negocio recibido antes de transformaciones de presentación, como objeto JSON. Separar original, valor normalizado y proyección; un objeto validado después de `.trim()` ya no es una copia exacta de lo recibido.
- [ ] No registrar cuerpos completos de entradas inválidas en logs. Conservar los aceptados y conflictos de negocio en almacenamiento protegido con la política de retención de la instancia; no replicarlos indiscriminadamente a observabilidad.

### Matriz de los 22 campos

Todos deben poder consultarse desde el contacto aunque no exista Deal. Los nativos ayudan a presentar, pero no reemplazan la conservación del valor recibido y su procedencia. Usar un bloque de calificación/contacto y el mecanismo existente de campos antes de crear una pantalla nueva.

| # | Campo de negocio | Proyección CRM y regla |
|---|---|---|
| 1 | `nombre_completo` | Nombre nativo cuando corresponda; preservar nombre completo original, sin inventar apellidos |
| 2 | `correo` | Email normalizado sólo bajo resolución de identidad; conservar recibido |
| 3 | `telefono` | Teléfono WhatsApp de origen; no borrar otro teléfono verificado |
| 4 | `empresa` | Declaración del contacto; Company sólo después de resolución segura |
| 5 | `industria` | Calificación de contacto; Company si procede sin pisar edición humana |
| 6 | `subtipo_automotriz` | Igual criterio; no exigirlo a otros rubros |
| 7 | `cargo` | Nativo cuando esté vacío o sea propiedad de la integración |
| 8 | `pais_ciudad` | Calificación visible en contacto |
| 9 | `situacion_contact_center` | Enum conforme al emisor, desconocido explícito |
| 10 | `tipo_contact_center` | Enum conforme al emisor; comprobar coherencia con situación |
| 11 | `usa_ia_actualmente` | Sí / No / Sin información; false no es ausencia |
| 12 | `canales_actuales` | Array estructurado; presentación derivada sin reconstruir por comas |
| 13 | `volumen_interacciones` | Valor declarado, sin inventar una unidad o cifra |
| 14 | `necesidad_principal` | Calificación visible en contacto y descripción derivada si procede |
| 15 | `soluciones_interes` | Array estructurado visible incluso sin Deal |
| 16 | `intencion` | Calificación visible incluso sin Deal |
| 17 | `plazo_proyecto` | Calificación visible incluso sin Deal; no equivale a cita agendada |
| 18 | `lead_score` | HOT / WARM / COLD / NO_CALIFICADO o desconocido según contrato; no es etapa |
| 19 | `solicita_consultoria` | Estado explícito visible; genera seguimiento cuando sea true |
| 20 | `solicita_contacto_humano` | Estado explícito visible; genera seguimiento cuando sea true |
| 21 | `resumen_conversacion` | Texto visible y nota con procedencia de evento; contenido no confiable |
| 22 | `siguiente_accion_recomendada` | Sugerencia visible; no afirma una cita ni ejecuta instrucciones |

### Semántica de actualización

- Ausente significa no aportado; no borra un valor previo.
- Null significa desconocido según contrato; no convertirlo a false ni usarlo para borrar un dato verificado sin una operación explícita.
- False es un dato y se persiste. True repetido en snapshots no crea una tarea nueva en cada revisión.
- String vacío no sustituye un dato conocido salvo que el contrato acuerde expresamente una operación de borrado.
- Array vacío presente es distinto de array ausente: en campos propiedad del bot representa una lista explícitamente vacía según contrato. Preservar ambas situaciones y probarlas; no usar `join()` + “ignorar vacío” como mecanismo de actualización.
- Las ediciones humanas de nativos, dueño, empresa vinculada y etapa prevalecen. Guardar la información nueva del bot como observación/sugerencia si entra en conflicto; no reemplazarla silenciosamente.
- Mantener la procedencia y última revisión aplicada de campos propiedad del bot. Para nativos, actualizar sólo si estaban vacíos o se puede demostrar que conservan el último valor escrito por la integración.

### Persistencia mínima

- [ ] Ledger de eventos con origen confiable, ID de evento, clave externa, revisión, hash, payload original, estado, código de conflicto, resultado y fechas. Usar estados restringidos por esquema; nunca marcar aplicado sin efectos confirmados.
- [ ] Vínculo estable por origen + clave externa hacia el contacto, empresa/oportunidad de integración cuando existan y última revisión **aplicada**. Puede residir en un modelo existente si garantiza unicidad y actualización transaccional; no depender de buscar siempre por teléfono.
- [ ] Restricción única origen + evento, y unicidad de la revisión por clave de origen según contrato. Una colisión se resuelve por estado y contenido, no como 500 genérico.
- [ ] Registrar intentos o resoluciones de conflicto sin borrar el evento original. Un evento en conflicto no avanza la revisión aplicada.
- [ ] Garantizar una sola relación de oportunidad de esta integración por contexto comercial acordado; no capturar arbitrariamente un Deal manual del mismo contacto.
- [ ] Definir integridad de referencias y política ante archivo/borrado de un contacto: no recrear entidades silenciosamente porque desapareció un ID. No convertir un replay histórico en prueba de que una entidad borrada sigue accesible.
- [ ] Crear/upsert de campos con namespace de integración, validación de tipo y compatibilidad de valores. No sobrescribir personalizaciones de presentación ajenas en cada semilla.
- [ ] Si falta una definición requerida, fallar readiness o revertir la ingesta con error reintentable. No continuar con un warning y responder éxito incompleto.
- [ ] Probar visualización real: una fila de FieldDefinition o `showOnSheet=true` no prueba que el comercial vea la información. Un checkbox solo puede ocultar la diferencia entre false y desconocido.

## R4. Resolver identidad antes de escribir

**Entregable:** servicio de resolución sin efectos parciales, con pruebas transaccionales.

- [ ] Buscar primero el vínculo de origen + clave externa. Verificar consistencia de identificadores nuevos con ese vínculo antes de cambiar nativos.
- [ ] Para un origen nuevo, buscar candidatos por teléfono normalizado y email normalizado. Si hay varios, o teléfono/email apuntan a contactos distintos, registrar conflicto y no fusionar.
- [ ] No asumir que un correo escrito en una conversación prueba posesión o empleo. Un match por email puede ser candidato; un teléfono incompatible requiere revisión o una regla de identidad ya autorizada, no vinculación automática a una tercera persona.
- [ ] No fusionar empresas sólo por nombre, aunque ambas tengan `domain=null` o las haya creado la misma integración. La búsqueda del documento anterior no comprobaba origen y seguía fusionando por nombre.
- [ ] Un dominio de email no demuestra identidad legal de empresa. Excluir proveedores personales es necesario, pero no suficiente: agencias y grupos comparten dominios. Reutilizar una Company sólo con vínculo confiable o evidencia y regla explícitas; ante ambigüedad, conservar la empresa declarada en el contacto y dejar resolución pendiente.
- [ ] No crear `website=https://dominio` como si el sitio hubiera sido verificado. No inventar Company para cumplir una FK obligatoria de Deal.
- [ ] Realizar toda resolución previa sin crear empresas. Cuando un conflicto de contacto aparece después de crear Company dentro de una transacción que se confirma, quedan efectos parciales: evitar esa secuencia.
- [ ] Si se necesita una Company nueva, crearla únicamente después de que la resolución completa sea aceptable y dentro de la transacción de aplicación.
- [ ] No sobrescribir `contact.companyId` ni un teléfono humano por la observación del bot. Conservar discrepancia y fuente para resolución.
- [ ] Cubrir concurrencia entre claves externas diferentes que comparten teléfono/email/empresa. Un lock por clave de contacto no cubre esas carreras: combinar restricciones existentes, locks transaccionales sobre identidades normalizadas en orden estable y reintentos acotados de transacción, o mecanismo equivalente probado.

**Aceptación:** identidad inequívoca o conflicto visible; nunca `findFirst` para elegir entre identidades ambiguas; un conflicto deja cero cambios comerciales.

## R5. Aplicar información comercial, orden e idempotencia

### Política comercial

- [ ] Mantener como política propuesta la apertura para HOT/WARM o solicitud explícita de contacto/consultoría; COLD/NO_CALIFICADO sin solicitud se conserva como contacto. Comprobar que coincide con la política vigente antes de activar.
- [ ] Elegir la etapa inicial real que signifique lead recibido/por calificar. No fijar `QUALIFIED_TO_BUY` sólo porque sea menos incorrecta que `DEMO_BOOKED`: también puede afirmar una calificación no realizada. Si no existe etapa adecuada, preparar una configuración/migración mínima y revisar su significado comercial.
- [ ] Nunca inventar demo, compromiso de compra, monto, probabilidad o fecha de cierre a partir de score o texto del bot.
- [ ] Si el esquema exige Company para Deal y no hay una resuelta, guardar contacto y calificación, dejar `dealId:null` y hacer visible la falta de empresa. No perder una solicitud de seguimiento por ese requisito.
- [ ] Conservar dueño, etapa, cierre y archivo de oportunidades existentes. Una revisión COLD posterior no borra el vínculo a un Deal ya creado ni devuelve null como si nunca hubiera existido.
- [ ] Asignar dueño desde configuración/identidad CRM autorizada y activa, no desde body ni por correo de ejemplo. Distinguir autor técnico de asignatario humano; `createdById` no demuestra asignación de tarea.
- [ ] Crear seguimiento accionable para contacto humano **o consultoría**, con responsable real, plazo configurable y estado pendiente. No crear otra tarea por cada snapshot con el mismo true; usar una clave de efecto persistente y conservar la tarea completada. Una nueva solicitud independiente requiere evidencia identificable; si el emisor no la distingue, no inventarla.
- [ ] Preservar resumen/recomendación con procedencia de evento; no repetir notas idénticas sin información nueva. El texto es dato no confiable, nunca instrucciones ni HTML ejecutable.

### Algoritmo transaccional requerido

1. Autenticar y validar contrato. Determinar origen, cuenta y políticas desde servidor.
2. Establecer serialización por evento y clave externa, con adquisición de locks en orden consistente. Las restricciones de base son el respaldo, no un sustituto del manejo de colisiones.
3. Consultar evento existente. Comparar clave externa, revisión y contenido; el mismo ID con otra revisión también es conflicto aunque el hash de negocio coincida.
4. Si ya se aplicó exactamente ese evento, devolver replay del resultado persistido sin repetir efectos. Si está en conflicto, devolver conflicto; **nunca replay exitoso con `contactId:""`**. Si está en proceso, devolver la condición transitoria definida, sin afirmar aplicación.
5. Comparar con última revisión aplicada, no con la máxima revisión recibida en conflicto. Para snapshot completo, revisión inferior no se aplica; igual revisión con otro evento es colisión explícita; revisión mayor puede avanzar. Documentar qué hacer con saltos y probarlo según contrato.
6. Resolver identidad sin escrituras comerciales. Si hay conflicto, persistir sólo diagnóstico/evento con estado de conflicto y confirmar cero efectos comerciales. No avanzar el cursor aplicado.
7. Si es aplicable, escribir contacto/vínculos, campos, oportunidad cuando corresponda y efectos de actividad. Actualizar ledger, resultado y cursor aplicado **en la misma transacción**.
8. Si cualquier escritura falla, rollback completo. Un registro de fallo operativo, si se guarda aparte, nunca puede parecer un evento aplicado. Aplicar reintentos acotados sólo a errores transitorios de transacción.
9. Responder únicamente después del commit. Una caída después del commit permite repetir el mismo evento y recuperar sus IDs.

**Hash:** usar representación canónica determinista del contenido validado para comparar reintentos; conservar por separado el original. Mantener el orden de arrays cuando tenga significado y distinguir ausencia/null/false. Un hash de negocio puede excluir metadata, pero las invariantes de evento, origen, clave y revisión se comprueban aparte. El emisor y receptor no necesitan compartir algoritmo de hash si no intercambian hashes; si los intercambian, probar fixtures comunes, Unicode y números. No considerar anónimo el hash de un teléfono.

**Resolución de conflictos:** una operación administrativa autenticada en CRM debe permitir revisar y asociar una identidad, registrar actor y motivo, y reejecutar el mismo evento sin editar su payload. Revalidar unicidad y orden en transacción. Si una revisión posterior ya fue aplicada, no pisarla. Una corrección del contenido necesita un evento nuevo del productor, no alterar silenciosamente el ledger. Repetir un conflicto por cron no lo resuelve.

## R6. Publicar un contrato HTTP preciso

Ruta interna propuesta: `POST /api/ingest/intouch-lead`, sólo si coincide con el emisor real. Ajustar el receptor con un adaptador compatible cuando sea necesario; nunca cambiar el contrato del bot sin registrar la incompatibilidad.

| HTTP | Estado | Significado |
|---|---|---|
| 201 | `created` | Primera aplicación para el vínculo de origen; contacto puede haber existido previamente |
| 200 | `updated` | Nueva revisión aplicada al vínculo existente |
| 200 | `replayed` | Evento idéntico ya aplicado; devuelve su resultado sin efectos nuevos |
| 400 | `invalid` | JSON/contrato inválido; errores por campo, sin eco de valores sensibles |
| 401 | `unauthorized` | Falta credencial o es inválida/revocada |
| 403 | `forbidden` | Credencial válida fuera de alcance |
| 409 | `conflict` | Identidad, ID/revisión o contenido incompatible; requiere resolución |
| 409 | `stale` | Snapshot anterior al último aplicado; no se aplicó este evento |
| 413 / 415 | `invalid` | Body excesivo / tipo no admitido |
| 429 | `rate_limited` | Límite efectivo del receptor; `Retry-After` |
| 503 | `unavailable` | Configuración, dueño requerido, base o dependencia temporal no disponible |

El plan anterior prometía 503 sin dueño pero lanzaba `ConflictException` (409). Usar el status real acordado y probarlo por HTTP. Clasificar stale/conflict como intervención o conciliación, no reintento infinito sin cambios. Si el emisor aún trata todos los errores igual, documentar esa limitación en R0.

Toda respuesta de éxito contiene `status`, `eventoId`, `revision`, `contactId` no vacío, `companyId` nullable y `dealId` nullable. Los IDs son los del resultado confirmado; `dealId:null` es éxito válido. Mantener los nombres y tipos exactos que admite el emisor. Incluir código diagnóstico estable en errores y correlación sin exponer datos de terceros.

- [ ] Probar matching de evento y revisión de la respuesta con el envío. Un 2xx con HTML, JSON incompleto o IDs de otro evento no demuestra entrega.
- [ ] No devolver redirects a login para errores de esta API; responder JSON.
- [ ] En replay no exigir que la configuración comercial actual vuelva a producir el mismo resultado: recuperar el resultado confirmado tras autenticar y validar el evento. No crear actividades ni volver a resolver identidad.
- [ ] Mantener accesible el diagnóstico de eventos recibidos y conflictos en CRM, protegido por GranCRM y permisos de operación. No exponer payloads/secretos al público.
- [ ] Para fallos que nunca llegaron al receptor, usar la evidencia del mecanismo existente del emisor. El CRM no puede listar eventos que desconoce.

## R7. Validación con efectos observables

Pruebas unitarias para reglas puras, integración con PostgreSQL aislado para carreras/rollback y HTTP con autenticación real. No usar SQLite para afirmar que locks o restricciones PostgreSQL funcionan. Cada fixture usa IDs únicos por prueba y cleanup por esos IDs; no borrar por prefijos de teléfono o nombre.

| ID | Prueba | Evidencia necesaria |
|---|---|---|
| R01 | Payload real del bot, 22 campos | Comparación de entrada, almacenamiento y detalle visible del contacto |
| R02 | COLD/NO_CALIFICADO sin Deal | Los 22 campos aportados siguen visibles; `dealId:null` aceptado |
| R03 | Ausente/null/false y strings vacíos | No coerción ni borrado involuntario; false visible |
| R04 | Arrays con comas y array vacío presente | Estructura exacta preservada; actualización conforme a contrato |
| R05 | Key inválida, ajena, revocada y cookie sola | Rechazo por HTTP; cero efectos |
| R06 | Key de ingesta contra resto de API/auth/tRPC | No obtiene datos ni sesión general |
| R07 | Cookie válida + key inventada | No confunde identidad de cookie con credencial de integración |
| R08 | JSON inválido, campos internos y body enorme | 400/413/415 según caso; cero escrituras |
| R09 | Repetir evento aplicado | Mismos IDs; conteos y valores comerciales sin cambios |
| R10 | Mismo evento en N peticiones simultáneas | Una aplicación y ningún duplicado de contacto/Deal/actividad |
| R11 | Mismo ID con payload, clave o revisión diferentes | 409 incluso con contenido de negocio idéntico y revisión alterada |
| R12 | Repetir evento previamente en conflicto | Sigue siendo conflicto; nunca 2xx ni contactId vacío |
| R13 | Conflicto de revisión alta y revisión válida menor | Cursor depende sólo de aplicada; comportamiento coherente con snapshots |
| R14 | Revisión vieja después de una nueva | No pisa valores; no deja falsa confirmación de aplicación |
| R15 | Dos claves externas con misma identidad concurrente | Sin duplicados ni fusión arbitraria; resultado determinista/diagnosticable |
| R16 | Nombre de empresa igual, con/sin dominio | No fusiona sólo por nombre ni por correo autodeclarado |
| R17 | Conflicto de teléfono/email | Cero Company, Contact, Deal o Activity nuevos; sólo diagnóstico |
| R18 | Falla después de escribir contacto/campos | Rollback de todos los efectos y cursor/ledger no aplicado |
| R19 | Campo requerido ausente de configuración | Error visible/reintentable; no éxito parcial |
| R20 | Edición humana + nueva revisión | Conserva corrección humana, dueño, empresa y etapa |
| R21 | HOT/WARM luego COLD, o Deal cerrado/archivado | No borra vínculo, reabre ni cambia etapa silenciosamente |
| R22 | Contacto humano y consultoría repetidos | Seguimiento asignado con vencimiento, sin duplicar por snapshots |
| R23 | Lead sin Company resoluble que pide ayuda | Contacto completo y seguimiento accionable sin empresa inventada |
| R24 | Pérdida de respuesta después del commit | Proxy/harness entrega al receptor, descarta su respuesta y repite exactamente; una aplicación |
| R25 | Reinicio del CRM entre recepción y reintento | Ledger y resultado sobreviven; no se duplican efectos |
| R26 | Dueño/configuración no disponible y rate limit interno | HTTP 503/429 efectivos; no 409 accidental ni éxito falso |
| R27 | Resolución manual de conflicto repetida/concurrente | Auditada, idempotente y respeta revisión posterior |
| R28 | Resumen con HTML/instrucciones maliciosas | Texto seguro; no ejecución ni acciones del agente |
| R29 | Acceso al diagnóstico desde otra cuenta | Denegado conforme al plan de acceso |
| R30 | Build, tipos y migraciones con versión fijada | Comandos reales, exit codes y entorno registrado |

No sustituir R24 por un mock que evita llamar al receptor: eso no demuestra commit con respuesta perdida. No detener servicios de producción para simular fallos. Las pruebas del productor real se separan de las del receptor: un simulador valida el contrato CRM, no demuestra por sí solo el outbox/reintento del bot existente.

## R8. Integrar red, activar y dejar evidencia

- [ ] Consolidar con A4 del plan de acceso una única configuración gateway. Publicar API interactiva con su auth GranCRM; denegar la ruta pública de ingesta.
- [ ] Verificar conectividad desde el runtime existente del bot, en modo lectura/smoke controlado, usando la red actual. Si falta una modificación de red o configuración del bot, preparar la necesidad exacta sin aplicarla bajo el alcance “sólo CRM”.
- [ ] Validar límites en el receptor por la conexión interna; un `limit_req` del gateway no limita al bot que lo evita. No duplicar zonas de nginx ni limitar toda la UI con una cuota pensada para ingestión.
- [ ] Preparar imagen identificada, SQL revisado, configuración redactada, resultados R01–R30, respaldo y rollback antes de la publicación autorizada.
- [ ] Aplicar migraciones del CRM y desplegar la imagen correspondiente en el orden probado. No ejecutar las antiguas migraciones 0038/0039 del bot ni reconstruir su frontend/cron.
- [ ] Si la conexión ya está configurada en el emisor, comprobar la ruta completa con un fixture autorizado y aislado. Si aún falta habilitar el sink, dejar instrucción concreta de configuración compatible y estado PENDIENTE DE ACTIVACIÓN; no afirmar integración completa.
- [ ] Verificar desde el CRM abierto en GranCRM: contacto, calificación, Company cuando proceda, Deal cuando proceda y seguimiento asignado. La comprobación por SQL sola no prueba calidad de uso.
- [ ] Usar cuentas/datos de prueba identificados individualmente y conservar sus IDs. Limpiar sólo esos registros con orden de dependencias revisado; no usar `startsWith` ni borrar conversaciones del bot en este plan.
- [ ] Revisar logs mediante comprobaciones que informen conteos/ubicaciones redactadas, sin imprimir coincidencias secretas. Ausencia de coincidencias en una regex no prueba ausencia absoluta de secretos; cubrir fixtures con valores canario y salidas de error.
- [ ] Guardar `docs/integracion-crm/evidencia-ingesta.md` con ID, entorno, commit, comando/pasos, esperado, observado, PASS/FAIL/NO EJECUTADO y bloqueo exacto cuando aplique.
- [ ] Actualizar la ficha central del CRM con rutas, versión y estado real. No marcar commits del bot ni inventar coautoría/modelo. Añadir a Git sólo los archivos de esta implementación.

## 3. Criterio de cierre y límites

El CRM está validado para recibir cuando contrato, autenticación, persistencia, idempotencia, UI y pruebas de fallos pasan en el entorno registrado. La integración completa exige además evidencia del emisor existente enviando y conciliando sus respuestas, y del acceso comercial del plan hermano. Si esas pruebas no se ejecutaron, declararlo por separado.

No se necesitan Meta ni RAG para probar el receptor con fixtures. Eso no equivale a validar una conversación real. No asumir que existe un login SQL concreto o que hay que reiniciar el bot para avanzar con el CRM.

## 4. Correspondencia con las tareas retiradas

| Tarea del original | Sustitución |
|---|---|
| 1: levantar CRM | R0–R1, versión fijada, Compose y pruebas aisladas |
| 2: principal API key | R2, alcance en ambas direcciones, sin sesión general |
| 3: LeadIngestEvent | R3/R5, ledger, vínculo estable y cursor aplicado |
| 4: campos dinámicos | R3, 22 campos consultables sin Deal |
| 5: contrato | R0/R3, contrato del emisor existente y semántica explícita |
| 6: identidad | R4, sin fusión por nombre ni escritura previa a conflicto |
| 7: escritura comercial | R5, etapas veraces, precedencia humana, seguimiento deduplicado |
| 8: idempotencia | R5/R7, replay por estado y carreras entre identidades |
| 9: endpoint | R2/R6, autenticación real, 503 correcto y límites internos |
| 10–13: modelos/transporte/cron/panel del bot | Retiradas; R0 audita compatibilidad sin modificar el bot |
| 14: conectar/desplegar | R8, alcance CRM y validación controlada |

## 5. Fuentes oficiales y alcance

La revisión se basa en los documentos entregados y en referencias oficiales. No se ejecutó el código propuesto ni se auditó en este trabajo el servidor o el bot ya desplegado. Verificar interfaces concretas contra el commit y lockfile de R0.

- [CompAI CRM oficial](https://github.com/trycompai/crm): base para identificar versión y estructura; no certifica que la copia del servidor coincida.
- [Docker Compose: reglas de merge](https://docs.docker.com/reference/compose-file/merge/): los puertos tienen reglas de combinación; no confiar en una lista vacía para quitarlos de un override.
- [Better Auth: API keys y sesiones](https://better-auth.com/docs/plugins/api-key/advanced): convertir una key en sesión requiere atender su alcance efectivo en la aplicación.
- [Prisma 6: desarrollo y producción](https://www.prisma.io/docs/orm/v6/prisma-migrate/workflows/development-and-production): referencia de migraciones versionadas para la versión mencionada en el plan original; comprobar opciones en la versión instalada.

El ledger, las reglas de resolución y la matriz de aceptación son correcciones de diseño de esta revisión. No se presentan como funcionalidades upstream ya existentes.
