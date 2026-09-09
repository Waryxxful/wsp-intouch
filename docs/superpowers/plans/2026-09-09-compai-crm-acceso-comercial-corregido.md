# CompAI CRM — acceso comercial desde GranCRM

Fecha de revisión: 2026-09-09. Estado: **PLAN CORREGIDO; IMPLEMENTACIÓN NO VALIDADA EN EL SERVIDOR**.

Este documento reemplaza íntegramente el plan de acceso anterior. Está preparado para la IA que implementa en la máquina. No ejecutar los ejemplos de código del documento anterior junto con estas instrucciones.

## 1. Objetivo y límites

Un comercial autorizado abre el CRM desde GranCRM sin un segundo login. GranCRM determina identidad, cuenta, aplicación y permisos vigentes. CompAI conserva sus entidades comerciales y su sesión local con Better Auth.

**Better Auth debe ser self-hosted:** su código se ejecuta en la infraestructura propia del CRM y sus usuarios y sesiones se almacenan en el PostgreSQL propio. No depender de Better Auth Cloud, Google, Microsoft ni otro proveedor externo para este acceso. Better Auth es una biblioteca; no hace falta desplegar otro servicio de identidad si la integración existente puede alojarla.

El bot ya está implementado. Este plan no modifica su código, modelos, extractor, frontend, cron ni migraciones. El plan hermano corregido define la recepción de sus leads en el CRM.

La instancia es de una sola cuenta GranCRM mientras el esquema no demuestre aislamiento por cuenta. Restringir el login no convierte una base compartida en multitenant. Si se requieren varias cuentas, usar instancias y bases separadas o aprobar antes un proyecto específico de aislamiento.

## 2. Instrucciones para la IA implementadora

- Trabajar por tareas; inspeccionar, implementar, probar y registrar evidencia antes de marcar cada casilla.
- Leer la ficha de CompAI en `/home/admincrm/docs-repo`, las instrucciones locales y los cambios pendientes antes de tocar el repositorio. No sobrescribir trabajo ajeno.
- Usar el código y las dependencias instaladas como referencia de interfaces. Los nombres de archivos de este plan son orientativos hasta verificarlos.
- Registrar commit de CompAI, lockfile, versiones reales y contrato GranCRM inspeccionado. Las versiones del plan anterior son antecedentes, no pruebas de lo instalado.
- Resolver decisiones técnicas rutinarias con evidencia. Si falta un dato que define quién accede —cuenta, aplicación o roles—, buscarlo primero en la configuración y la ficha; si no existe una decisión autorizada, preguntar sólo ese dato y continuar con las pruebas independientes.
- No inventar métodos de Better Auth, claims, URLs ni credenciales. No distribuir `GRANCRM_JWT_SECRET` al CRM.
- La solicitud de corregir este documento no autoriza por sí sola desplegar ni ampliar permisos. En una ejecución posterior, usar la autorización vigente de esa sesión; preparar los cambios y resultados antes de solicitar cualquier aprobación que todavía haga falta.
- No declarar PASS con resultados esperados ni inventar autores, versiones de modelos o commits.

## 3. Dependencias y orden compartido

1. Ejecutar R0–R1 del plan `2026-09-09-compai-crm-ruta-del-lead-corregido.md`: inventario y entorno CRM aislado y reproducible.
2. Ejecutar A0–A4 de este documento en ese entorno. No hace falta una API key del bot para implementar el acceso.
3. Completar la ingesta R2–R7 del plan hermano.
4. Publicar una sola configuración de gateway revisada y realizar A5 y R8 con la autorización correspondiente.

Una sola tarea debe integrar la configuración final del gateway. Evitar que dos planes agreguen rutas o zonas de rate limit duplicadas.

## A0. Verificar los contratos reales

**Entregable:** `docs/integracion-crm/contrato-acceso.md`, sin secretos.

- [ ] Identificar en el CRM las entradas HTTP, REST, tRPC, server actions, SSR con datos, descargas, streams y endpoints de Better Auth. Señalar qué capa autoriza cada una.
- [ ] Inspeccionar el endpoint real del orquestador que valida `grancrm_session`, incluyendo consulta de sesión activa, expiración, cuenta suspendida, app retirada y roles. `GET /api/session/` es la ruta propuesta; verificarla antes de usarla.
- [ ] Documentar un ejemplo anonimizado de respuesta: identificador estable del usuario, identificador de sesión o `jti`, cuenta efectiva, `rol_real`, aplicaciones autorizadas, expiración y estado de impersonación. No asumir que un 200 implica autorización al CRM.
- [ ] Verificar que los permisos devueltos reflejan el estado actual. Si sólo reproducen claims antiguos del JWT, documentar la brecha y usar una consulta autoritativa existente; no prometer revocación de roles inmediata sin ella.
- [ ] Documentar el alcance Path/Domain/SameSite real de la cookie GranCRM. El navegador debe enviarla a las rutas protegidas del CRM, incluidas `/crm-api/`; una cookie limitada a otra ruta no sirve.
- [ ] Confirmar el origen HTTPS público, certificado y modo de red del gateway. No copiar una IP privada ni usar `curl -k` como validación TLS.
- [ ] Registrar cuenta, aplicación y roles autorizados desde evidencia existente. `CRM_PERMITE_VER_COMO=false` por defecto; no autorizar soporte impersonado por conveniencia.

Si falta un identificador estable o una forma de comprobar vigencia, mantener el acceso sin publicar y completar el resto con fixtures del contrato explícitamente propuesto. No unir usuarios sólo por email.

## A1. Registrar la aplicación y preparar configuración

**Ámbito:** manifiesto de aplicación, configuración de servidor y documentación. No conceder permisos adicionales al registrar.

- [ ] Reutilizar el registro GranCRM existente por identificador/slug, si existe; evitar aplicaciones duplicadas.
- [ ] Verificar el esquema real de registro y su autenticación. El modo iframe no demuestra que el registro pueda hacerse sin credencial.
- [ ] Publicar el menú hacia `/crm/`, con arranque controlado hacia el puente cuando falte sesión local. Usar iframe sólo si el shell realmente soporta ese modo.
- [ ] Resolver la URL interna desde el proceso que la consume. Si el CRM escucha en `127.0.0.1:3005`, la URL `http://IP_DEL_HOST:3005` no es equivalente. En una red Docker usar el nombre de servicio y puerto internos; con gateway en host usar loopback cuando corresponda.
- [ ] Configurar en servidor: origen público canónico; URL fija del orquestador; aplicación; cuenta; lista de roles; política de impersonación; secreto propio de Better Auth; conexión propia a PostgreSQL. Usar nombres de variables compatibles con el código real.
- [ ] Mantener secretos fuera del bundle Next, argumentos de build, Git, logs y documentación. No compartir un archivo con todos los secretos entre frontend y API indiscriminadamente.
- [ ] Validar al arrancar tipos y valores obligatorios del módulo habilitado. Una lista de roles vacía no significa permitir todos.

**Aceptación:** registro único, configuración coherente con la red y ningún acceso concedido por valores de ejemplo.

## A2. Implementar el puente y la vinculación de sesión

**Ámbito sugerido:** módulo GranCRM de `apps/api`, integración en `packages/auth`, persistencia mínima de vínculos y pruebas. Confirmar primero sus ubicaciones reales.

### Validación remota

- [ ] Extraer sólo la cookie de sesión esperada, con tamaño acotado; no reenviar todas las cookies del navegador.
- [ ] Consultar exclusivamente la URL interna configurada y confiable. Usar timeout acotado, `redirect: error`, sin caché de respuesta y sin registrar cookies ni el body completo.
- [ ] Validar el JSON con un esquema estricto en los campos de seguridad. No usar `Boolean("false")`, `String(undefined)` ni coerciones numéricas que acepten valores ambiguos.
- [ ] Exigir sesión vigente, usuario activo, cuenta exacta, app habilitada para esa cuenta y usuario, y `rol_real` permitido. No usar fallback a `rol`.
- [ ] Cuando se admita impersonación expresamente, validar actor y sujeto, cuenta efectiva y alcance de soporte; registrar ambos. No convertir al actor en dueño comercial por defecto.
- [ ] Ante sesión inválida responder 401; ante cuenta/app/rol fuera de alcance, 403. Ante caída o respuesta inválida del orquestador, 503 y cero acceso a datos; no fallback a sesión local sin validación.

### Creación de la sesión local

- [ ] Buscar la extensión soportada por la versión instalada de Better Auth para emitir una sesión después de una autenticación externa verificada. Implementar una prueba de integración con esa versión antes de depender de ella.
- [ ] No asumir que existe `auth.api.signInWithoutPassword`. No simular contraseñas ni firmar cookies a mano; tampoco insertar filas de sesión fuera del mecanismo soportado.
- [ ] Si se requiere un plugin propio, mantenerlo pequeño y limitarlo a la identidad ya validada. Revisar sus usos de adaptador, hooks, cookies y controles de origen contra el código instalado.
- [ ] Vincular el usuario CRM mediante la combinación proveedor GranCRM + cuenta + identificador estable del usuario. El email es un atributo, no la clave de confianza para fusionar cuentas existentes.
- [ ] Para un usuario local preexistente con el mismo correo, exigir un vínculo previo verificable o resolver el conflicto explícitamente. No hacer account linking silencioso.
- [ ] No asignar `emailVerified=true` salvo que el contrato del emisor lo garantice. Aplicar la política de acceso antes de crear o actualizar al usuario.
- [ ] Asociar cada sesión local al usuario, cuenta, aplicación y sesión GranCRM de origen usando su identificador verificable. No persistir el token GranCRM completo si basta el identificador de vínculo.
- [ ] Crear una sesión local nueva después de autenticar; impedir fijación de sesión y evitar que una sesión CRM de otro usuario sobreviva a un cambio de usuario en GranCRM.
- [ ] El vencimiento local no debe superar el de la sesión GranCRM vinculada. Una renovación válida del origen debe actualizar/recrear el vínculo mediante el flujo definido y probado.
- [ ] Usar las utilidades de cookies y cabeceras del framework. Preservar múltiples `Set-Cookie`; no concatenarlas ni perderlas al iterar un objeto de headers.

### Navegación y CSRF

- [ ] Definir un inicio de sesión protegido contra login CSRF y cambio de cuenta inducido. Usar el mecanismo soportado de estado/nonce y comprobación de origen; el POST que establece sesión debe estar protegido. Un GET de entrada puede iniciar el flujo, sin aceptar identidades o destinos arbitrarios.
- [ ] Fijar el destino de éxito en una ruta local autorizada bajo `/crm/`. Si se admite retorno a una ficha, validarlo contra ese prefijo y el mismo origen; rechazar URLs externas y variantes `//host`.
- [ ] Mantener las protecciones CSRF en todas las mutaciones por cookie. No aplicar excepciones globales para hacer funcionar el puente.
- [ ] Evitar bucles cuando GranCRM está caído o deniega acceso; mostrar un estado claro con reintento explícito.

**Aceptación:** una sesión GranCRM válida produce un vínculo y una sesión Better Auth reales; entradas inválidas no crean usuarios ni sesiones y no redirigen fuera del CRM.

## A3. Mantener la autorización durante toda la sesión

Ésta es la corrección central: validar sólo al entrar deja viva la cookie Better Auth después del logout o de retirar permisos en GranCRM.

- [ ] Toda operación protegida debe comprobar sesión local, vínculo y autoridad GranCRM vigente, incluidos los accesos directos a la API. El menú y el middleware del frontend no bastan.
- [ ] Para esta primera versión, revalidar contra la autoridad en cada petición protegida. No usar una caché positiva entre peticiones que permita continuar después de revocar. Puede deduplicarse la consulta dentro de una misma petición.
- [ ] Comparar la identidad y la sesión GranCRM presentadas con el vínculo local. No permitir que una cookie CRM antigua opere con otra identidad GranCRM válida.
- [ ] Auditar procedimientos tRPC, handlers REST, server actions, SSR, exports, archivos y streams. Si una ruta obtiene datos sin pasar por la capa común, añadir el control allí o centralizarla.
- [ ] Cerrar o revalidar suscripciones/streams antes de nuevas entregas protegidas; una autorización al abrir la conexión no habilita lecturas indefinidas. Para trabajos largos, revalidar antes de una nueva acción privilegiada.
- [ ] Impedir entradas alternativas: registro abierto, OAuth externo, magic link, usuario/contraseña, sesiones sin vínculo y uso de API keys ordinarias para saltarse la política GranCRM. Inventariar las opciones realmente instaladas y desactivar o proteger las que correspondan.
- [ ] La credencial técnica del bot tiene su propia autenticación y sólo habilita la ingesta del plan hermano. No se transforma en sesión de comercial ni autoriza otras rutas.
- [ ] Definir logout local con el mecanismo de Better Auth: revocar sesión y limpiar cookies con los mismos atributos. Al cerrar GranCRM, las peticiones futuras del CRM deben denegarse aunque el navegador conserve su cookie local.
- [ ] Registrar denegaciones y cambios de vínculo con identificadores y códigos; sin tokens, correo completo ni datos de leads en logs de autenticación.

**Límite preciso:** revocación efectiva en la siguiente comprobación autoritativa. No se pueden retirar datos ya enviados al navegador ni prometer cancelar una transacción ya autorizada y terminada.

## A4. Montar `/crm/` y `/crm-api/` sin desactivar controles

- [ ] Configurar `basePath: "/crm"` antes del build Next. No añadir `assetPrefix` sólo para desplegar en una subruta: está destinado a otro uso y no sustituye `basePath`.
- [ ] Verificar rutas de recursos públicos, enlaces, imágenes, fuentes, redirecciones y navegación directa a fichas. Confirmar qué variables consume el cliente en build y cuáles el servidor en runtime.
- [ ] Definir una única tabla de rutas. Propuesta si coincide con la aplicación instalada:

| Ruta pública | Destino | Transformación |
|---|---|---|
| `/crm` | `/crm/` | Redirección canónica local |
| `/crm/…` | Next en loopback/red interna | Conservar `/crm/…` por `basePath` |
| `/crm-api/api/…` | Nest `/api/…` | Quitar sólo `/crm-api/` |
| Ruta pública equivalente a `/api/ingest/…` | Denegada | La ingesta usa red interna |

- [ ] Verificar el baseURL/basePath efectivo de Better Auth y sus clientes; evitar perder o duplicar `/api` y comprobar cookies enviadas por el navegador tanto a Next como a la API. Path de cookie no es un control de autorización.
- [ ] Configurar cabeceras proxy de Host y protocolo para un gateway confiable; no confiar en `X-Forwarded-*` arbitrarios de clientes directos.
- [ ] Mantener TLS válido, cookies Secure/HttpOnly y SameSite compatible con el iframe del mismo origen; `trustedOrigins` explícitos sin comodines innecesarios.
- [ ] Permitir el iframe sólo desde el shell autorizado mediante `frame-ancestors`; revisar coherencia con X-Frame-Options y con todas las CSP que llegan al navegador. No eliminar la CSP completa ni agregar `unsafe-inline` global para arreglar errores.
- [ ] Conservar la política de scripts que soporte el build instalado (nonce/hash cuando aplique), sin inventar una plantilla CSP que rompa hidratación o reduzca los controles existentes.
- [ ] No aplicar el rate limit de ingesta a toda la API interactiva. La ingesta interna necesita su límite en el receptor, porque no pasa por nginx.
- [ ] Validar configuración candidata y cabeceras efectivas antes de publicar. Confirmar qué contenido ve nginx dentro del contenedor: un reload suele bastar si ve el archivo actualizado; recrear sólo cuando los montajes o cambios lo requieran.
- [ ] Verificar que la sintaxis validada corresponde al archivo que se va a cargar. Conservar configuración e imagen anteriores para rollback; no interrumpir otras aplicaciones del gateway para hacer pruebas.

## A5. Pruebas y publicación controlada

Ejecutar primero con cuentas y base aisladas. La revisión de autenticación debe quedar resuelta antes de habilitar acceso general. Puede hacerse con revisión documentada y pruebas; este plan no exige herramientas o agentes inexistentes.

| ID | Caso | Resultado exigido |
|---|---|---|
| A01 | Usuario autorizado entra desde GranCRM | Abre CRM sin segundo login; vínculo correcto |
| A02 | Falta cookie, expiró o fue revocada | 401 y cero datos protegidos |
| A03 | Otra cuenta, app retirada o rol no permitido | 403 aunque Better Auth siga vigente |
| A04 | Respuesta sin `rol_real` o con tipos ambiguos | Denegación; sin fallback/coerción |
| A05 | Logout GranCRM conservando cookie CRM | Siguiente petición directa denegada, sin volver al puente |
| A06 | Retirar rol/app o suspender cuenta con CRM abierto | Lecturas y mutaciones posteriores denegadas |
| A07 | Orquestador caído o JSON inválido | 503, sin datos ni acceso por cookie local sola |
| A08 | Cambiar usuario/cuenta GranCRM en el mismo navegador | No reutiliza sesión CRM de la identidad anterior |
| A09 | Email coincidente de otro usuario/proveedor | No vincula automáticamente ni toma la cuenta |
| A10 | Login CSRF, retorno externo y mutación desde otro origen | Rechazados; no cambia identidad ni datos |
| A11 | Rutas alternativas y API key fuera de alcance | No eluden GranCRM ni abren sesión de comercial |
| A12 | Impersonación deshabilitada; habilitada explícitamente en fixture | Deniega por defecto; valida actor/sujeto cuando procede |
| A13 | Iframe, deep link, assets y cookies reales | Funciona con TLS y CSP; sin errores relevantes de consola |
| A14 | SSR, REST, tRPC, server actions, exports y streams | Ninguna vía evita la comprobación de autoridad |
| A15 | Logout local y renovación del origen | Cookies/vínculo coherentes; no bucles ni sesión fijada |
| A16 | Acceso con egreso a identidad externa bloqueado | Login GranCRM + Better Auth local funciona |

- [ ] Guardar evidencia en `docs/integracion-crm/evidencia-acceso.md`: ID, commit, entorno, pasos/comando, esperado, observado y PASS/FAIL/NO EJECUTADO.
- [ ] No borrar sesiones reales por SQL para probar: usar usuarios de prueba y operaciones soportadas de logout/revocación.
- [ ] Preparar diff, impacto, respaldo y rollback antes de cualquier aprobación de despliegue aún necesaria. No solicitar otra aprobación si la sesión ya autoriza exactamente esa acción.
- [ ] Publicar únicamente la versión probada y repetir smoke tests de acceso y denegación en el destino autorizado.
- [ ] Actualizar la ficha del CRM en docs-repo con estado real; no editar documentación del bot por esta tarea.

## 4. Criterio de cierre

Separar estados: IMPLEMENTADO, VALIDADO EN ENTORNO AISLADO, DESPLEGADO, VALIDADO EN DESTINO. Marcar cada uno sólo con evidencia. Si falla revocación, aislamiento de cuenta, vinculación o una ruta protegida, el acceso no está listo para habilitarse.

## 5. Fuentes y alcance de esta revisión

Se revisó estáticamente el plan recibido y se consultaron fuentes oficiales. Esto no verifica el checkout privado, la versión instalada ni el contrato del orquestador; A0 debe hacerlo en la máquina.

- [Repositorio oficial CompAI CRM](https://github.com/trycompai/crm): punto de entrada para localizar código y fijar revisión, no prueba del despliegue local.
- [Better Auth: plugins](https://better-auth.com/docs/concepts/plugins) y [API del servidor](https://better-auth.com/docs/concepts/api): las extensiones exponen sus endpoints; se debe comprobar el método disponible en la versión instalada.
- [Better Auth: sesiones mediante API keys](https://better-auth.com/docs/plugins/api-key/advanced): una key puede representar una sesión; su alcance debe comprobarse explícitamente en la aplicación.
- [Next.js: assetPrefix](https://nextjs.org/docs/app/api-reference/config/next-config-js/assetPrefix): para alojar bajo una subruta corresponde `basePath`.

Las reglas de autorización continua, vinculación estable y cierre ante fallos son decisiones de esta revisión para satisfacer el acceso solicitado; no se atribuyen a una implementación upstream ya comprobada.
