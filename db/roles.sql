-- ============================================================================
-- Data platform: application roles (least privilege)
-- Run this LAST, after schema.sql + seed.sql.
--
-- Each app connects as the role it needs. Table grants are the security spine:
-- the public query role has no write grant on any application table, the public
-- contact form cannot read other people's submissions, and one app's role has no
-- grant on another app's tables. Grants are not everything a login can do; see
-- the pizza_ro note below.
--
-- >>> Replace every REPLACE_ME_* below with a strong password before running. <<<
-- ============================================================================

-- pizza, READ-ONLY: used by the public NL->SQL /ask endpoint.
-- Can read the pizza schema and has no write grant on any application table.
-- Like any login it can still read the Postgres system catalogs and, outside a
-- read-only transaction, create temp tables and large objects. On the public
-- path, /ask runs every query in a read-only transaction and is_safe_select
-- refuses catalog access.
CREATE ROLE pizza_ro LOGIN PASSWORD 'REPLACE_ME_pizza_ro';
GRANT USAGE ON SCHEMA pizza TO pizza_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA pizza TO pizza_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA pizza GRANT SELECT ON TABLES TO pizza_ro;
ALTER ROLE pizza_ro SET search_path = pizza;

-- pizza, READ-WRITE: used by /order (place order) and the nightly restock.
CREATE ROLE pizza_rw LOGIN PASSWORD 'REPLACE_ME_pizza_rw';
GRANT USAGE ON SCHEMA pizza TO pizza_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA pizza TO pizza_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA pizza TO pizza_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA pizza GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pizza_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA pizza GRANT USAGE ON SEQUENCES TO pizza_rw;
ALTER ROLE pizza_rw SET search_path = pizza;

-- leads, WRITER: used by the public contact form. INSERT only: it can drop a
-- submission in but cannot read anyone's data back.
CREATE ROLE leads_writer LOGIN PASSWORD 'REPLACE_ME_leads_writer';
GRANT USAGE ON SCHEMA leads TO leads_writer;
GRANT INSERT ON leads.submissions TO leads_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA leads TO leads_writer;
ALTER ROLE leads_writer SET search_path = leads;

-- leads, READER: used only by the authenticated admin view.
CREATE ROLE leads_reader LOGIN PASSWORD 'REPLACE_ME_leads_reader';
GRANT USAGE ON SCHEMA leads TO leads_reader;
GRANT SELECT ON leads.submissions TO leads_reader;
ALTER ROLE leads_reader SET search_path = leads;
