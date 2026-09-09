-- Login SQL de wsp_intouch — crear ANTES del primer arranque del bot.
--
-- POR QUE ES BLOQUEANTE, y no un detalle de configuracion:
-- `DB_SCHEMA` del .env.docker es DECORATIVO. `OPTIONS["database_schema"]` no es
-- una opcion real de mssql-django 1.8.0 y se ignora en silencio (ver el
-- comentario de config/settings.py:66-81). El schema donde el bot escribe DE
-- VERDAD lo fija el DEFAULT_SCHEMA de ESTE login.
--
-- Reusar el login de otro bot hace que este escriba en la produccion del otro, y
-- el dano lo hace el `migrate` del arranque, antes del primer mensaje. Ya paso:
-- wsp_cavem arranco con el login de wsp_demo (DEFAULT_SCHEMA=botdemo) y aplico 5
-- migraciones en el schema de produccion de Renault/Astara (PENDIENTES.md #12).
--
-- Y en este bot seria peor que "dos datasets en una tabla": `Conversation` no
-- tiene columna `cliente` y su `wa_id` es UNIQUE, asi que serian LAS MISMAS
-- FILAS. Un contacto que ya hablo con el otro bot reanudaria esa conversacion,
-- con su flow_state, su flow_data y su historial de mensajes.
--
-- Este script replica exactamente lo que hace el provisionador del orquestador
-- (orquestador/core/provisioning.py::AppUserProvisioner, lineas 103-116 y
-- 206-245), con una sola diferencia deliberada: EL NOMBRE. Ver la nota al final.
--
-- Es idempotente: se puede correr dos veces sin dano.

-- ---------------------------------------------------------------------------
-- PASO 0 — generar la contrasena (NO la escribas a mano)
-- ---------------------------------------------------------------------------
-- Corre esto en el host y guarda el resultado; va en DB_PASSWORD del
-- .env.docker. 32 caracteres alfanumericos, sin simbolos a proposito: los
-- simbolos ($ # ! % ^) rompen el paso del password por .env y docker-compose
-- (interpolacion de $, comentario inline de #). Misma convencion que el
-- provisionador (provisioning.py:63-66).
--
--   python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))"
--
-- Reemplaza <PASSWORD_GENERADO> abajo por ese valor.

-- ---------------------------------------------------------------------------
-- PASO 1 — el login, a nivel servidor (base `master`)
-- ---------------------------------------------------------------------------
USE [master];
GO

IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'intouch_login_qa')
BEGIN
    -- CHECK_POLICY = OFF es la convencion del provisionador (provisioning.py:113):
    -- la entropia la da el largo (62^32), no la politica de complejidad.
    CREATE LOGIN [intouch_login_qa]
        WITH PASSWORD = '<PASSWORD_GENERADO>', CHECK_POLICY = OFF;
    PRINT 'login intouch_login_qa creado';
END
ELSE
    PRINT 'login intouch_login_qa ya existia -- no se toca';
GO

-- ---------------------------------------------------------------------------
-- PASO 2 — el schema y el user mapping (base `QAIntouch`)
-- ---------------------------------------------------------------------------
-- Un solo user mapping alcanza para las DOS conexiones del bot: `default` y
-- `qaintouch` apuntan a la misma base fisica (QAIntouch) y toman
-- USER/PASSWORD de las mismas env vars (config/settings.py:130-146).
USE [QAIntouch];
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'intouch')
BEGIN
    EXEC('CREATE SCHEMA [intouch]');
    PRINT 'schema intouch creado';
END
ELSE
    PRINT 'schema intouch ya existia';
GO

-- OJO: si el user ya existe con OTRO default_schema, este script NO lo corrige.
-- Cambiar el default sin mover las tablas ya creadas rompe la app en caliente
-- (autentica pero no ve datos -- incidentes reales: Call Reviews 2026-07-02,
-- InciTrack 2026-07-09). En ese caso hay que decidirlo a mano:
--   ALTER SCHEMA [intouch] TRANSFER [<schema_viejo>].<tabla>;
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'intouch_login_qa')
BEGIN
    CREATE USER [intouch_login_qa] FOR LOGIN [intouch_login_qa]
        WITH DEFAULT_SCHEMA = [intouch];
    PRINT 'user intouch_login_qa creado con DEFAULT_SCHEMA=intouch';
END
ELSE
BEGIN
    DECLARE @actual sysname = (
        SELECT default_schema_name FROM sys.database_principals
        WHERE name = 'intouch_login_qa'
    );
    IF @actual <> 'intouch'
        RAISERROR('El user intouch_login_qa ya existe con DEFAULT_SCHEMA=%s, se esperaba intouch. NO se corrige automaticamente: si ya hay tablas bajo ese schema, cambiar el default rompe la app. Mover las tablas a mano primero.', 16, 1, @actual);
    ELSE
        PRINT 'user intouch_login_qa ya existia con el schema correcto';
END
GO

-- ---------------------------------------------------------------------------
-- PASO 3 — permisos (exactamente los del provisionador, lineas 241-245)
-- ---------------------------------------------------------------------------
-- GRANT es idempotente: no falla si el permiso ya existe.
-- ALTER y CREATE TABLE son necesarios porque el bot corre `migrate` al arrancar.
GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::[intouch] TO [intouch_login_qa];
GRANT ALTER      ON SCHEMA::[intouch] TO [intouch_login_qa];
GRANT REFERENCES ON SCHEMA::[intouch] TO [intouch_login_qa];
GRANT CREATE TABLE TO [intouch_login_qa];
GO

-- ---------------------------------------------------------------------------
-- PASO 4 — VERIFICACION. No es opcional.
-- ---------------------------------------------------------------------------
-- Reconectate a QAIntouch CON EL LOGIN NUEVO (no con sa) y corre:
--
--   SELECT DB_NAME() AS base, SCHEMA_NAME() AS schema_efectivo, CURRENT_USER AS usuario;
--
-- Tiene que decir:  QAIntouch | intouch | intouch_login_qa
--
-- Si `schema_efectivo` dice otra cosa, NO arranques el bot: el `migrate` del
-- arranque escribiria ahi. El bot tiene un system check propio (`bot.E003`,
-- bot/apps.py:72-146) que compara SCHEMA_NAME() contra DB_SCHEMA y aborta el
-- migrate, pero no valida si DB_SCHEMA quedo vacia ni si la conexion falla en
-- ese momento -- asi que esta verificacion a mano igual vale.

-- ---------------------------------------------------------------------------
-- NOTA SOBRE EL NOMBRE — un desalineamiento del ecosistema, no un error de aca
-- ---------------------------------------------------------------------------
-- Este script usa `intouch_login_qa`, que es el nombre que esperan el
-- .env.docker, docs/PENDIENTE_CREDENCIALES.md y docs/DEPLOY_INTOUCH.md.
--
-- El provisionador del orquestador generaria OTRO nombre: deriva el slug del
-- CAMPO `nombre` de la Aplicacion (provisioning.py:73-78), no del slug del
-- dios.json. Para "Asesor Comercial IA — InTouch" daria
-- `asesor_comercial_ia___intouch_login_qa`.
--
-- Si en vez de este script se usa el panel SA / `AppUserProvisioner`, hay que
-- poner ESE nombre en DB_USER y corregir los dos documentos. Las dos vias
-- funcionan; lo que no funciona es mezclarlas.
