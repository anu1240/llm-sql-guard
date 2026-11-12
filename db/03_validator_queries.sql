-- ============================================================
-- LLM-SQL GUARD — VALIDATOR SQL
-- These are the queries the Python validator calls to check
-- whether an LLM-generated query references real schema objects
-- and real join relationships.
-- ============================================================
USE llm_sql_guard;

-- ------------------------------------------------------------
-- A) TABLE EXISTENCE CHECK
-- Given a table name, does it exist in this database?
-- (Python passes the table name as a bound parameter; shown here
--  as a literal for demonstration.)
-- ------------------------------------------------------------
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'llm_sql_guard'
  AND TABLE_NAME = 'orders';           -- <- parameterized in Python

-- ------------------------------------------------------------
-- B) COLUMN EXISTENCE CHECK
-- Given a table + column name, does that column exist on that table?
-- ------------------------------------------------------------
SELECT COLUMN_NAME, DATA_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'llm_sql_guard'
  AND TABLE_NAME = 'orders'
  AND COLUMN_NAME = 'order_date';       -- <- parameterized in Python

-- ------------------------------------------------------------
-- C) ALL FOREIGN-KEY EDGES (the "join graph")
-- This is the edge list the recursive CTE below traverses.
-- ------------------------------------------------------------
SELECT
    TABLE_NAME        AS child_table,
    COLUMN_NAME        AS child_column,
    REFERENCED_TABLE_NAME  AS parent_table,
    REFERENCED_COLUMN_NAME AS parent_column
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = 'llm_sql_guard'
  AND REFERENCED_TABLE_NAME IS NOT NULL;

-- ------------------------------------------------------------
-- D) *** CORE CONTRIBUTION ***
-- RECURSIVE CTE — JOIN-PATH REACHABILITY CHECK
--
-- Problem this solves: an LLM can write
--     SELECT * FROM customers c JOIN products p ON c.customer_id = p.category_id
-- Both `customers` and `products` are real tables and both columns exist, so the
-- flat existence checks (A/B above) pass it. But there is NO foreign key between
-- those two tables: customers connect to products only through other tables
-- (customers -> orders -> order_items -> products, or customers -> reviews -> products).
--
-- This recursive CTE treats the FK graph as undirected edges and finds every table
-- reachable from a given start table within N hops, together with the readable path
-- taken. The validator uses it to (1) confirm a JOIN is a direct 1-hop FK edge and
-- (2) when it is not, tell the user the real path to join through.
-- ------------------------------------------------------------

-- Example: find every table reachable from 'customers' within the FK graph,
-- how many hops away each one is, and the shortest join path to it.
--
-- Two MariaDB/MySQL recursive-CTE quirks handled here:
--  1. You cannot reference the recursive CTE in a subquery inside its
--     own recursive branch (e.g. to dedupe mid-recursion) — the engine
--     rejects it. Instead we bound recursion depth (hops < 4) and
--     dedupe afterwards with GROUP BY / MIN(hops) in the outer SELECT.
--  2. The engine infers the recursive column's type from the anchor
--     row, so an untyped literal like 'orders' gets sized to fit just
--     that string — later rows with longer table names then overflow
--     it. Casting the anchor to CHAR(64) avoids that.
WITH RECURSIVE fk_edges AS (
    SELECT TABLE_NAME AS a, REFERENCED_TABLE_NAME AS b
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = 'llm_sql_guard' AND REFERENCED_TABLE_NAME IS NOT NULL
    UNION ALL
    SELECT REFERENCED_TABLE_NAME AS a, TABLE_NAME AS b    -- undirected: traverse both ways
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = 'llm_sql_guard' AND REFERENCED_TABLE_NAME IS NOT NULL
),
reachable (table_name, hops, path) AS (
    SELECT CAST('customers' AS CHAR(64)), 0, CAST('customers' AS CHAR(255))  -- <- start table, parameterized in Python
    UNION ALL
    SELECT fk_edges.b, reachable.hops + 1, CONCAT(reachable.path, ' -> ', fk_edges.b)
    FROM reachable
    JOIN fk_edges ON fk_edges.a = reachable.table_name
    WHERE reachable.hops < 4                -- safety bound; also naturally halts cycles
)
SELECT table_name, MIN(hops) AS min_hops, SUBSTRING_INDEX(GROUP_CONCAT(path ORDER BY hops SEPARATOR '|'), '|', 1) AS shortest_path
FROM reachable
GROUP BY table_name
ORDER BY min_hops;

-- The Python validator runs the parameterized version of the query above for
-- every JOIN ... ON clause whose columns are not a real foreign-key pair, to explain
-- the failure: either the tables are directly linked but joined on the wrong columns,
-- or they are several hops apart (and the shortest path is suggested).
-- A JOIN ... ON that uses a real FK column pair (see query C) is accepted outright.

-- ------------------------------------------------------------
-- E) ANALYTICS LAYER (window functions) — hallucination_log reporting
-- These run AFTER the validator has logged results from many
-- generated queries, to power the project's dashboard/report.
-- ------------------------------------------------------------

-- E1. Daily hallucination rate, with a 7-day moving average of flagged counts
-- (ROWS frame over the daily rows, via window functions)
SELECT
    DATE(checked_at)                                   AS log_date,
    COUNT(*)                                            AS total_checked,
    SUM(CASE WHEN verdict = 'HALLUCINATED' THEN 1 ELSE 0 END) AS hallucinated_count,
    ROUND(100.0 * SUM(CASE WHEN verdict = 'HALLUCINATED' THEN 1 ELSE 0 END) / COUNT(*), 2) AS hallucination_pct,
    ROUND(AVG(
        SUM(CASE WHEN verdict = 'HALLUCINATED' THEN 1 ELSE 0 END)
    ) OVER (ORDER BY DATE(checked_at) ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS moving_avg_7
FROM hallucination_log
GROUP BY DATE(checked_at)
ORDER BY log_date;

-- E2. Rank failure types by frequency (RANK + running total)
SELECT
    failure_type,
    COUNT(*) AS occurrences,
    RANK() OVER (ORDER BY COUNT(*) DESC) AS failure_rank,
    SUM(COUNT(*)) OVER (ORDER BY COUNT(*) DESC) AS running_total
FROM hallucination_log
WHERE verdict = 'HALLUCINATED'
GROUP BY failure_type
ORDER BY failure_rank;

-- E3. Per-provider hallucination rate (which LLM hallucinates most)
SELECT
    llm_provider,
    COUNT(*) AS total_queries,
    SUM(CASE WHEN verdict = 'HALLUCINATED' THEN 1 ELSE 0 END) AS hallucinated,
    ROUND(100.0 * SUM(CASE WHEN verdict = 'HALLUCINATED' THEN 1 ELSE 0 END) / COUNT(*), 2) AS hallucination_pct,
    RANK() OVER (ORDER BY SUM(CASE WHEN verdict = 'HALLUCINATED' THEN 1 ELSE 0 END) DESC) AS worst_offender_rank
FROM hallucination_log
GROUP BY llm_provider
ORDER BY worst_offender_rank;
