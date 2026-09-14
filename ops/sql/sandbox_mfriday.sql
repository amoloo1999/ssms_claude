/*
  Sandbox for mfriday@williamwarren.com — the server side of SANDBOX_USERS.

  What this creates, on MSSQL01 (the instance at 13.57.123.119,1433):
    login   ssms_mfriday
    user    ssms_mfriday in Sites, default schema dbo
    schema  Sites.sandbox_mfriday, OWNED by ssms_mfriday
    grants  CREATE TABLE and SHOWPLAN in Sites. Nothing else.

  Why that is enough, and why it is safe:
    - CREATE TABLE needs BOTH the database permission AND ALTER on the target
      schema. ssms_mfriday has ALTER only on the schema it owns, so
      CREATE TABLE dbo.anything fails.
    - A schema owner can insert, update, delete, alter and drop everything in
      it. That is the "only tables he creates" rule, enforced by the engine.
    - Read on Sites (db_datareader, step 5). Mason keeps his prior all-of-Sites
      read; his normal reads use the app's shared login, and this lets a write
      that reads (INSERT ... SELECT FROM dbo.X) work under this login too. If he
      should instead be limited to specific tables, use per-table GRANT SELECT.
    - The schema is owned by ssms_mfriday, not dbo. With dbo as owner, objects
      in it would ownership-chain into dbo tables.

  One trap is closed explicitly (step 4): a table owner can put a DML trigger
  on their own table, and a trigger runs as whoever fires it. If a RevMan, an
  Airflow job, or anything else using a privileged login ever writes to one of
  these tables, the trigger body would run with THAT login's rights. A
  database DDL trigger refuses CREATE/ALTER TRIGGER from this login.

  Run in SSMS as a sysadmin (it needs securityadmin for the login and db_owner
  on Sites). Replace the password first:
  32+ characters, letters and digits only — it goes into an ODBC connection
  string, where ; { } would break it. Then put the same value in
  C:\ssms_claude\backend\.env on MSSQL01:

      SANDBOX_PASSWORDS={"ssms_mfriday": "<the password>"}

  and restart the service (nssm restart ssms-claude).

  Rollback is at the bottom.
*/

-- 1. Login ------------------------------------------------------------------
USE master;
GO
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'ssms_mfriday')
    CREATE LOGIN ssms_mfriday
        WITH PASSWORD = N'<REPLACE_WITH_32_CHAR_ALPHANUMERIC>',
             DEFAULT_DATABASE = Sites,
             CHECK_POLICY = ON,
             CHECK_EXPIRATION = OFF;
GO

-- 2. User and schema ----------------------------------------------------------
USE Sites;
GO
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'ssms_mfriday')
    CREATE USER ssms_mfriday FOR LOGIN ssms_mfriday WITH DEFAULT_SCHEMA = dbo;
GO
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'sandbox_mfriday')
    EXEC (N'CREATE SCHEMA sandbox_mfriday AUTHORIZATION ssms_mfriday');
GO

-- 3. Permissions --------------------------------------------------------------
GRANT CREATE TABLE TO ssms_mfriday;
-- The query editor's "estimated plan" for a write runs under this login.
GRANT SHOWPLAN TO ssms_mfriday;
GO

-- 4. No triggers from the sandbox login ----------------------------------------
CREATE OR ALTER TRIGGER trg_sandbox_no_triggers
ON DATABASE
FOR CREATE_TRIGGER, ALTER_TRIGGER
AS
BEGIN
    SET NOCOUNT ON;
    -- SUSER_SNAME() is the effective login, which is also what the smoke test
    -- below sees under EXECUTE AS. ORIGINAL_LOGIN() alone would miss that case.
    IF SUSER_SNAME() IN (N'ssms_mfriday') OR ORIGINAL_LOGIN() IN (N'ssms_mfriday')
    BEGIN
        ROLLBACK;
        THROW 50001, N'Triggers are not allowed in sandbox schemas.', 1;
    END
END;
GO

-- 5. Read access to Sites -----------------------------------------------------
-- Mason keeps read access to all of Sites (his access before the sandbox). His
-- READS run under the app's shared login, not this one — but a write that reads,
--   INSERT INTO sandbox_mfriday.x SELECT ... FROM dbo.Units
-- runs entirely under THIS login, so it needs read on Sites too. db_datareader
-- adds no exposure beyond the all-of-Sites read he already has in the app.
-- If his access should instead be a specific list of tables, drop the role add
-- below and GRANT SELECT ON dbo.<table> TO ssms_mfriday per table.
ALTER ROLE db_datareader ADD MEMBER ssms_mfriday;
GO

-- 6. Verify -------------------------------------------------------------------
-- Expect: CREATE TABLE and SHOWPLAN (database), and the schema owner.
SELECT p.class_desc, p.permission_name, p.state_desc,
       OBJECT_SCHEMA_NAME(p.major_id) AS obj_schema, OBJECT_NAME(p.major_id) AS obj
FROM sys.database_permissions p
WHERE p.grantee_principal_id = DATABASE_PRINCIPAL_ID(N'ssms_mfriday');

SELECT s.name AS schema_name, USER_NAME(s.principal_id) AS owner
FROM sys.schemas s WHERE s.name = N'sandbox_mfriday';

-- Behaviour, as the login itself. Each "expect error" line should fail.
EXECUTE AS LOGIN = N'ssms_mfriday';
    CREATE TABLE sandbox_mfriday.smoke_test (id int PRIMARY KEY, note nvarchar(50));
    INSERT INTO sandbox_mfriday.smoke_test VALUES (1, N'ok');
    UPDATE sandbox_mfriday.smoke_test SET note = N'updated' WHERE id = 1;
    SELECT * FROM sandbox_mfriday.smoke_test;
    DROP TABLE sandbox_mfriday.smoke_test;
REVERT;
GO
EXECUTE AS LOGIN = N'ssms_mfriday';
    BEGIN TRY CREATE TABLE dbo.sandbox_smoke_test (id int); PRINT N'FAIL: created a dbo table'; END TRY
    BEGIN CATCH PRINT N'ok, refused: ' + ERROR_MESSAGE(); END CATCH
REVERT;
GO
EXECUTE AS LOGIN = N'ssms_mfriday';
    CREATE TABLE sandbox_mfriday.trigger_test (id int);
    BEGIN TRY
        EXEC (N'CREATE TRIGGER sandbox_mfriday.t_test ON sandbox_mfriday.trigger_test AFTER INSERT AS SELECT 1');
        PRINT N'FAIL: created a trigger';
    END TRY
    BEGIN CATCH PRINT N'ok, refused: ' + ERROR_MESSAGE(); END CATCH
    DROP TABLE sandbox_mfriday.trigger_test;
REVERT;
GO

/* Rollback ---------------------------------------------------------------------
USE Sites;
-- The schema must be empty first. See what's in it:
--   SELECT name FROM sys.tables WHERE schema_id = SCHEMA_ID(N'sandbox_mfriday');
DROP TRIGGER IF EXISTS trg_sandbox_no_triggers ON DATABASE;
DROP SCHEMA IF EXISTS sandbox_mfriday;
DROP USER IF EXISTS ssms_mfriday;
USE master;
DROP LOGIN ssms_mfriday;
-- And remove the entry from SANDBOX_USERS and SANDBOX_PASSWORDS.
*/
