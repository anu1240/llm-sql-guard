-- ============================================================
-- OPTIONAL BUT RECOMMENDED: a read-only MySQL user for the app.
-- The validator only ever needs to READ the shop tables and WRITE to
-- hallucination_log. Using root means a bug (or a clever prompt) could
-- in theory change real data; this user physically cannot.
--
-- Run once in Workbench as root, then put these in .env:
--     DB_USER=guard_app
--     DB_PASSWORD=<the password you choose below>
-- ============================================================
CREATE USER IF NOT EXISTS 'guard_app'@'localhost' IDENTIFIED BY 'CHANGE_ME_before_running';
GRANT SELECT ON llm_sql_guard.* TO 'guard_app'@'localhost';
GRANT INSERT ON llm_sql_guard.hallucination_log TO 'guard_app'@'localhost';
FLUSH PRIVILEGES;
