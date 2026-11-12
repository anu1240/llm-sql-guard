"""
validator.py
-------------
Takes a piece of LLM-generated SQL and checks it against the LIVE
database schema before it's allowed to run:

  1. Does every referenced table actually exist?               (flat check)
  2. Does every referenced column actually exist on its table?  (flat check)
  3. Is every JOIN between two tables actually supported by a
     real foreign-key path between them?                        (recursive CTE check — the core contribution)

Returns a verdict dict; does NOT execute the query itself unless
asked to (keeps the "don't run hallucinated SQL" guarantee).
"""
import os
import re
import sqlparse
from sqlparse.sql import IdentifierList, Identifier
from sqlparse import tokens as T
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "llm_sql_guard"),
}


def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


# ------------------------------------------------------------------
# 1. PARSE SQL -> extract table names, alias map, column references
# ------------------------------------------------------------------
# Functions that contain their own "FROM" keyword, e.g. EXTRACT(YEAR FROM order_date).
# Without this, the word after that FROM would be mistaken for a table name.
_FROM_INSIDE_FUNCTION = re.compile(r"(\b(?:EXTRACT|TRIM|SUBSTRING)\s*\([^()]*?)\bFROM\b", re.IGNORECASE)

# Words that can follow a table name but are NOT an alias for it.
_NOT_AN_ALIAS = {
    "ON", "USING", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "UNION",
    "INTERSECT", "EXCEPT", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER",
    "NATURAL", "STRAIGHT_JOIN", "WINDOW", "FOR", "LOCK", "INTO", "SELECT",
}


def extract_tables_and_aliases(sql: str):
    """Returns {alias_or_name: real_table_name} for every table/alias
    referenced in FROM / JOIN clauses."""
    text = _FROM_INSIDE_FUNCTION.sub(r"\1,", str(sqlparse.parse(sql)[0]))
    tables = {}

    # Simple, robust regex approach: catches "FROM x [AS] a" and
    # "JOIN y [AS] b" patterns, which covers the vast majority of
    # LLM-generated SQL for this project's scope.
    # The (?!keyword) lookahead stops a following keyword (JOIN, ON, WHERE...) from
    # being swallowed as an "alias", which would hide the next table in
    # "FROM orders JOIN order_items ON ...".
    pattern = re.compile(
        r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:AS\s+)?"
        r"(?!(?:" + "|".join(sorted(_NOT_AN_ALIAS)) + r")\b)([a-zA-Z_][a-zA-Z0-9_]*)?",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        table_name = match.group(1)
        alias = match.group(2)
        tables[alias or table_name] = table_name
    return tables


def extract_column_refs(sql: str, alias_map: dict):
    """Returns a list of (alias_or_table, column) tuples for every
    qualified column reference like alias.column found in the query."""
    pattern = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b")
    refs = []
    for match in pattern.finditer(sql):
        prefix, col = match.group(1), match.group(2)
        if prefix in alias_map:
            refs.append((alias_map[prefix], col))
    return refs


def extract_cte_names(sql: str) -> set:
    """Names the query defines for itself via WITH [RECURSIVE] name AS (...),
    name2 AS (...). These are temporary tables, not real schema tables."""
    pattern = re.compile(
        r"(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\([^)]*\)\s*)?AS\s*\(",
        re.IGNORECASE,
    )
    return {m.group(1).lower() for m in pattern.finditer(sql)}


def extract_bare_columns(sql: str, alias_map: dict, cte_names: set):
    """Returns every plain column name used WITHOUT a table prefix anywhere in
    the query - SELECT list, WHERE, GROUP BY, HAVING, ORDER BY, ON, function
    arguments. Uses sqlparse's tokenizer so keywords, functions, string
    literals and numbers are never mistaken for columns.

    Skipped on purpose: names followed by '(' (functions), parts of
    prefix.column pairs (handled by extract_column_refs), table names/aliases,
    CTE names, and names the query defines itself via 'AS alias' or a CTE
    column list."""
    defined = {m.lower() for m in re.findall(r"\bAS\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)}
    for m in re.finditer(r"[a-zA-Z_][a-zA-Z0-9_]*\s*\(([^()]*)\)\s*AS\s*\(", sql, re.IGNORECASE):
        defined.update(x.strip().lower() for x in m.group(1).split(","))
    skip = defined | cte_names | {a.lower() for a in alias_map} | {t.lower() for t in alias_map.values()}

    tokens = [t for t in sqlparse.parse(sql)[0].flatten() if not t.is_whitespace]
    columns = []
    for i, tok in enumerate(tokens):
        if tok.ttype is not T.Name:
            continue
        prev_val = tokens[i - 1].value if i > 0 else ""
        next_val = tokens[i + 1].value if i + 1 < len(tokens) else ""
        if prev_val == "." or next_val in (".", "("):
            continue
        if tok.value.lower() in skip:
            continue
        columns.append(tok.value)
    return columns


_ON_CLAUSE = re.compile(
    r"\bON\b(.*?)(?=\b(?:JOIN|INNER|LEFT|RIGHT|CROSS|FULL|NATURAL|WHERE|GROUP|HAVING|ORDER|LIMIT|UNION)\b|\)|;|$)",
    re.IGNORECASE | re.DOTALL,
)
_EQUALITY = re.compile(
    r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)"
)


def extract_on_clauses(sql: str, alias_map: dict):
    """One entry per JOIN ... ON clause. Each entry is the list of column
    equalities found inside it, as (left_table, left_col, right_table, right_col)
    with aliases resolved to real table names, e.g. ON o.customer_id = c.customer_id
    -> ('orders', 'customer_id', 'customers', 'customer_id')."""
    clauses = []
    for on_match in _ON_CLAUSE.finditer(sql):
        pairs = []
        for m in _EQUALITY.finditer(on_match.group(1)):
            la, lc, ra, rc = m.groups()
            if la in alias_map and ra in alias_map:
                pairs.append((alias_map[la], lc, alias_map[ra], rc))
        if pairs:
            clauses.append(pairs)
    return clauses


# ------------------------------------------------------------------
# 2. SCHEMA CHECKS — flat existence (INFORMATION_SCHEMA)
# ------------------------------------------------------------------
def table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """SELECT 1 FROM INFORMATION_SCHEMA.TABLES
           WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s""",
        (DB_CONFIG["database"], table_name),
    )
    return cursor.fetchone() is not None


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s""",
        (DB_CONFIG["database"], table_name, column_name),
    )
    return cursor.fetchone() is not None


# ------------------------------------------------------------------
# 3. *** CORE CONTRIBUTION *** recursive CTE join-path reachability
# ------------------------------------------------------------------
RECURSIVE_REACHABILITY_SQL = """
WITH RECURSIVE fk_edges AS (
    SELECT TABLE_NAME AS a, REFERENCED_TABLE_NAME AS b
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = %(schema)s AND REFERENCED_TABLE_NAME IS NOT NULL
    UNION ALL
    SELECT REFERENCED_TABLE_NAME AS a, TABLE_NAME AS b
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = %(schema)s AND REFERENCED_TABLE_NAME IS NOT NULL
),
reachable (table_name, hops, path) AS (
    SELECT CAST(%(start_table)s AS CHAR(64)), 0, CAST(%(start_table)s AS CHAR(255))
    UNION ALL
    SELECT fk_edges.b, reachable.hops + 1, CONCAT(reachable.path, ' -> ', fk_edges.b)
    FROM reachable
    JOIN fk_edges ON fk_edges.a = reachable.table_name
    WHERE reachable.hops < 4
)
SELECT table_name, hops, path
FROM reachable
ORDER BY hops;
"""


def reachable_tables(cursor, start_table: str) -> dict:
    """Returns {table_name: (min_hops, shortest_path)} for every table reachable
    from start_table in the (undirected) foreign-key graph, within 4 hops.
    The path is a readable chain such as 'customers -> orders -> order_items'."""
    cursor.execute(
        RECURSIVE_REACHABILITY_SQL,
        {"schema": DB_CONFIG["database"], "start_table": start_table},
    )
    best = {}
    for table_name, hops, path in cursor.fetchall():  # already sorted by hops
        best.setdefault(table_name, (hops, path))
    return best


def fk_column_pairs(cursor) -> set:
    """Every real foreign-key column pair as (child_table, child_col, parent_table, parent_col)."""
    cursor.execute(
        """SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
           FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
           WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL""",
        (DB_CONFIG["database"],),
    )
    return {tuple(row) for row in cursor.fetchall()}


def _is_fk_match(fks: set, lt, lc, rt, rc) -> bool:
    """True if (lt.lc = rt.rc) is a real foreign-key relationship, in either direction."""
    return (lt, lc, rt, rc) in fks or (rt, rc, lt, lc) in fks


def _fk_hint(fks: set, a, b) -> str:
    """Human-readable correct join columns for two directly-linked tables."""
    for (ct, cc, pt, pc) in fks:
        if {ct, pt} == {a, b}:
            return f"{ct}.{cc} = {pt}.{pc}"
    return ""


# ------------------------------------------------------------------
# 4. MAIN VALIDATION ENTRY POINT
# ------------------------------------------------------------------
def validate_sql(generated_sql: str) -> dict:
    """Returns:
    {
        "verdict": "VALID" | "HALLUCINATED",
        "failure_type": "NONE" | "UNKNOWN_TABLE" | "UNKNOWN_COLUMN" | "INVALID_JOIN_PATH",
        "failure_detail": str,
    }
    """
    if not generated_sql or not generated_sql.strip():
        return {"verdict": "HALLUCINATED", "failure_type": "UNKNOWN_TABLE",
                "failure_detail": "Empty query - there is no SQL to check."}
    # Remove SQL comments first, so words inside comments (e.g. "-- Join to find...")
    # are never mistaken for tables or columns.
    generated_sql = re.sub(r"/\*.*?\*/", " ", generated_sql, flags=re.DOTALL)
    generated_sql = re.sub(r"--[^\n]*", " ", generated_sql)
    generated_sql = re.sub(r"(?m)^\s*#[^\n]*", " ", generated_sql)
    # Blank out string literals and backticks so text inside quotes (e.g. 'a.b',
    # 'Join us') is never mistaken for a table, alias or column.
    generated_sql = re.sub(r"'(?:[^'\\]|\\.|'')*'", "''", generated_sql)
    generated_sql = re.sub(r'"(?:[^"\\]|\\.)*"', "''", generated_sql)
    generated_sql = generated_sql.replace("`", "")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        alias_map = extract_tables_and_aliases(generated_sql)
        cte_names = extract_cte_names(generated_sql)
        is_cte = lambda t: t.lower() in cte_names
        if not alias_map:
            return {"verdict": "HALLUCINATED", "failure_type": "UNKNOWN_TABLE",
                     "failure_detail": "Could not identify any FROM/JOIN table in the query."}

        # --- Check A: every referenced table exists ---
        for alias, real_table in alias_map.items():
            if is_cte(real_table):
                continue  # defined by the query itself (WITH ... AS), not a schema table
            if not table_exists(cursor, real_table):
                return {
                    "verdict": "HALLUCINATED",
                    "failure_type": "UNKNOWN_TABLE",
                    "failure_detail": f"Table '{real_table}' does not exist in schema '{DB_CONFIG['database']}'.",
                }

        # --- Check B1: every qualified column (alias.column) exists on its table ---
        for real_table, column in extract_column_refs(generated_sql, alias_map):
            if column == "*" or is_cte(real_table):
                continue
            if not column_exists(cursor, real_table, column):
                return {
                    "verdict": "HALLUCINATED",
                    "failure_type": "UNKNOWN_COLUMN",
                    "failure_detail": f"Column '{column}' does not exist on table '{real_table}'.",
                }

        # --- Check B2: unqualified columns ANYWHERE in the query (SELECT, WHERE,
        #     GROUP BY, HAVING, ORDER BY, ON...) exist on AT LEAST ONE of the
        #     tables in play (covers single-table queries with no alias) ---
        real_tables = {t for t in alias_map.values() if not is_cte(t)}
        for column in extract_bare_columns(generated_sql, alias_map, cte_names):
            if real_tables and not any(column_exists(cursor, t, column) for t in real_tables):
                return {
                    "verdict": "HALLUCINATED",
                    "failure_type": "UNKNOWN_COLUMN",
                    "failure_detail": f"Column '{column}' does not exist on any of {sorted(real_tables)}.",
                }

        # --- Check C: every single JOIN...ON edge must be a DIRECT (1-hop)
        #     foreign-key relationship. A query chaining several real joins
        #     (A JOIN B ON ... JOIN C ON ...) is fine — each ON clause is its
        #     own 1-hop edge. What's NOT fine is a single ON clause jumping
        #     straight between two tables that are only connected indirectly,
        #     several hops apart (e.g. customers directly to products,
        #     skipping orders/order_items) — that's exactly the kind of
        #     "plausible but structurally impossible" join an LLM can
        #     hallucinate, and a flat existence-check would miss it since
        #     both tables and both columns are individually real.
        #     Each ON clause must also use the REAL foreign-key columns, not
        #     just join two linked tables on arbitrary columns.
        fks = None
        for equalities in extract_on_clauses(generated_sql, alias_map):
            if all(is_cte(t) for eq in equalities for t in (eq[0], eq[2])):
                continue  # joins against a query-defined CTE have no FK to check
            if fks is None:
                fks = fk_column_pairs(cursor)
            # An ON clause is fine if at least one equality is a real FK match
            # (extra conditions such as "AND o.order_status = ..." are allowed).
            if any(_is_fk_match(fks, *eq) for eq in equalities):
                continue
            lt, lc, rt, rc = equalities[0]
            if is_cte(lt) or is_cte(rt):
                continue
            hops = reachable_tables(cursor, lt)
            dist, path = hops.get(rt, (None, None))
            if dist == 1:
                detail = (f"'{lt}' and '{rt}' are linked, but not on {lt}.{lc} = {rt}.{rc}. "
                          f"The real foreign-key join is {_fk_hint(fks, lt, rt)}.")
            elif dist is not None:
                detail = (f"'{lt}' and '{rt}' are not directly connected by a foreign key "
                          f"(graph distance: {dist}). Valid join path: {path}.")
            else:
                detail = (f"'{lt}' and '{rt}' are not connected by any foreign-key path "
                          f"within 4 hops.")
            return {
                "verdict": "HALLUCINATED",
                "failure_type": "INVALID_JOIN_PATH",
                "failure_detail": detail,
            }

        return {"verdict": "VALID", "failure_type": "NONE", "failure_detail": None}
    finally:
        cursor.close()
        conn.close()


def log_result(nl_question: str, generated_sql: str, provider: str, result: dict):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO hallucination_log
               (nl_question, generated_sql, llm_provider, verdict, failure_type, failure_detail)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (nl_question, generated_sql, provider, result["verdict"],
             result["failure_type"], result["failure_detail"]),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


# ----------------------------------------------------------------------
# Run an APPROVED query and show the real answer
# ----------------------------------------------------------------------
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|RENAME|CALL|LOAD|OUTFILE|DUMPFILE)\b",
    re.IGNORECASE)


def execute_select(sql: str, max_rows: int = 15):
    """Runs a validated read-only query. Only SELECT / WITH queries are allowed.
    Returns (column_names, rows, total_rows_fetched_capped)."""
    clean = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    clean = re.sub(r"--[^\n]*", " ", clean).strip().rstrip(";").strip()
    if ";" in clean:
        raise ValueError("Only a single statement can be run.")
    if not re.match(r"^(SELECT|WITH)\b", clean, re.IGNORECASE) or _FORBIDDEN.search(clean):
        raise ValueError("Only read-only SELECT queries are run.")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        try:
            cursor.execute("SET SESSION max_execution_time = 5000")  # 5s cap on SELECTs (MySQL 8)
        except Exception:
            pass  # MariaDB / older servers don't support it; carry on without the cap
        cursor.execute(clean)
        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
        return cols, rows[:max_rows], len(rows) > max_rows
    finally:
        cursor.close()
        conn.close()


def format_table(cols, rows, more=False) -> str:
    """Plain-text table for the terminal."""
    data = [[("NULL" if x is None else str(x)) for x in r] for r in rows]
    widths = [min(40, max([len(c)] + [len(r[i]) for r in data])) for i, c in enumerate(cols)]
    cut = lambda s, w: s if len(s) <= w else s[: w - 1] + "\u2026"
    line = "   " + "-+-".join("-" * w for w in widths)
    out = ["   " + " | ".join(cut(c, w).ljust(w) for c, w in zip(cols, widths)), line]
    for r in data:
        out.append("   " + " | ".join(cut(x, w).ljust(w) for x, w in zip(r, widths)))
    if not data:
        out.append("   (no rows returned)")
    elif more:
        out.append("   ... (more rows not shown)")
    return "\n".join(out)


def show_answer(sql: str):
    """Prints the real answer for an approved query (never raises)."""
    try:
        cols, rows, more = execute_select(sql)
        print("   Answer from the database:")
        print(format_table(cols, rows, more))
    except Exception as e:
        print(f"   (Validator approved it, but MySQL could not run it: {e})")

# ----------------------------------------------------------------------
# Schema description for the LLM, built from the live database
# ----------------------------------------------------------------------
_PROJECT_TABLES = {"hallucination_log"}   # the validator's own bookkeeping table, hidden from the LLM


def get_schema_context() -> str:
    """Plain-English schema description (tables, columns, foreign keys) read from
    INFORMATION_SCHEMA, in the format handed to the LLM. Always matches the real database."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
               WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
               ORDER BY CREATE_TIME, TABLE_NAME""",
            (DB_CONFIG["database"],),
        )
        tables = [r[0] for r in cursor.fetchall() if r[0] not in _PROJECT_TABLES]
        lines = ["", "Tables:"]
        for t in tables:
            cursor.execute(
                """SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION""",
                (DB_CONFIG["database"], t),
            )
            lines.append(f"- {t}({', '.join(r[0] for r in cursor.fetchall())})")
        lines += ["", "Relationships:"]
        for ct, cc, pt, pc in sorted(fk_column_pairs(cursor)):
            if ct not in _PROJECT_TABLES and pt not in _PROJECT_TABLES:
                lines.append(f"- {ct}.{cc} -> {pt}.{pc}")
        return "\n".join(lines) + "\n"
    finally:
        cursor.close()
        conn.close()
