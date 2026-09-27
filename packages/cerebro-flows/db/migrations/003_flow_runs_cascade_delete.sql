-- Fixes DELETE /flows/{code} crashing with an unhandled 500
-- (asyncpg.exceptions.ForeignKeyViolationError) whenever the flow being deleted
-- had at least one run recorded in `flow_runs` -- found live in production
-- (2026-09-27): the API's `delete_flow`, the CLI's `cerebro flow delete`
-- confirmation prompt, and the MCP `flow_delete` tool's docstring all already
-- promised that deleting a flow cascades to "su historial de
-- versiones/ejecuciones" / "its version history and its recorded runs", but
-- `flow_runs.definition_id`'s FK (001_init.sql) was never given `ON DELETE
-- CASCADE` to begin with -- only `flow_definition_versions.definition_id` was.
-- This migration makes the schema match what was already documented and
-- expected, instead of rewriting that documented behavior to match the bug.

ALTER TABLE flow_runs DROP CONSTRAINT flow_runs_definition_id_fkey;
ALTER TABLE flow_runs ADD CONSTRAINT flow_runs_definition_id_fkey
    FOREIGN KEY (definition_id) REFERENCES flow_definitions(id) ON DELETE CASCADE;
