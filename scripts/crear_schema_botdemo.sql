-- ============================================================================
-- Crear schema 'botdemo' en DevIntouch
-- ============================================================================
-- Este script SOLO crea el schema vacio. Las tablas las crea Django con
-- 'python manage.py migrate' (una vez configurado .env.docker con
-- USE_SQLITE=false y las credenciales del login dedicado).
--
-- Como ejecutar:
--   A) SSMS: conectarse a 172.20.21.50, seleccionar DevIntouch, New Query, F5.
--   B) sqlcmd -S 172.20.21.50 -U <usuario> -P <pass> -d DevIntouch -i scripts\crear_schema_botdemo.sql
--
-- Permisos requeridos: CREATE SCHEMA en DevIntouch.
-- ============================================================================

USE DevIntouch;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'botdemo')
BEGIN
    EXEC('CREATE SCHEMA botdemo');
    PRINT 'Schema botdemo creado.';
END
ELSE
BEGIN
    PRINT 'Schema botdemo ya existia. Sin cambios.';
END
GO

SELECT
    s.name           AS schema_name,
    p.name           AS owner_name,
    s.schema_id      AS schema_id
FROM sys.schemas s
LEFT JOIN sys.database_principals p ON s.principal_id = p.principal_id
WHERE s.name = 'botdemo';
GO

SELECT COUNT(*) AS tablas_en_schema
FROM sys.tables t
INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE s.name = 'botdemo';
GO

PRINT '';
PRINT 'OK. Schema listo. Siguiente paso: python manage.py migrate desde la raiz del proyecto.';
GO
