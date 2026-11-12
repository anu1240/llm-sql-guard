"""
run_validator_tests.py
-----------------------
Evaluates the validator itself. A strong LLM (like Gemini 2.5 Flash) makes very
few schema mistakes, so on its own it gives the validator little to catch.
This script feeds in hand-written queries where we KNOW the right answer:
  - queries that are correct against the schema            -> expect VALID
  - queries with a made-up table                           -> expect UNKNOWN_TABLE
  - queries with a made-up column                          -> expect UNKNOWN_COLUMN
  - queries joining real tables that have no direct FK     -> expect INVALID_JOIN_PATH
  - directly-linked tables joined on the wrong columns     -> expect INVALID_JOIN_PATH
  - made-up columns hiding in WHERE / GROUP BY / ORDER BY / HAVING
  - realistic valid queries (window functions, subqueries, CTEs, CASE, EXTRACT)
    that a naive checker would wrongly reject
and reports how many the validator got right (accuracy), per failure type.

Results are also logged to hallucination_log under the provider name
'benchmark-suite', so the analytics (window-function) queries have varied data
and the benchmark rows stay separate from the real Gemini rows.

Usage:
    python run_validator_tests.py            # run and log
    python run_validator_tests.py --no-log   # run without writing to the database
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from validator import validate_sql, log_result

# (label, sql, expected_verdict, expected_failure_type)
CASES = [
    # ---------------- should be VALID ----------------
    ("valid: simple select", "SELECT * FROM orders", "VALID", "NONE"),
    ("valid: two columns", "SELECT full_name, city FROM customers", "VALID", "NONE"),
    ("valid: SELECT DISTINCT", "SELECT DISTINCT city FROM customers", "VALID", "NONE"),
    ("valid: NULL placeholder column",
     "SELECT c.full_name, NULL AS email FROM customers AS c", "VALID", "NONE"),
    ("valid: customers-orders join",
     "SELECT c.full_name, o.order_date FROM customers c JOIN orders o ON c.customer_id = o.customer_id",
     "VALID", "NONE"),
    ("valid: 3-table join chain",
     "SELECT o.order_id, p.product_name FROM orders o JOIN order_items oi ON o.order_id = oi.order_id "
     "JOIN products p ON oi.product_id = p.product_id", "VALID", "NONE"),
    ("valid: self-join on category parent",
     "SELECT c.category_name, p.category_name FROM categories c JOIN categories p "
     "ON c.parent_category_id = p.category_id", "VALID", "NONE"),
    ("valid: recursive CTE (query-defined table)",
     "WITH RECURSIVE tree AS (SELECT category_id, category_name, parent_category_id FROM categories "
     "WHERE parent_category_id IS NULL UNION ALL SELECT c.category_id, c.category_name, c.parent_category_id "
     "FROM categories c JOIN tree t ON c.parent_category_id = t.category_id) "
     "SELECT p.product_name, t.category_name FROM products p JOIN tree t ON p.category_id = t.category_id",
     "VALID", "NONE"),

    ("valid: SQL comments containing 'Join to' are ignored",
     "SELECT p.product_name -- Join to find the name\nFROM products p /* join to nothing */", "VALID", "NONE"),

    # ---------------- made-up TABLE ----------------
    ("bad table: vendors", "SELECT * FROM vendors", "HALLUCINATED", "UNKNOWN_TABLE"),
    ("bad table: payments", "SELECT p.amount FROM payments p", "HALLUCINATED", "UNKNOWN_TABLE"),
    ("bad table: warehouses joined to products",
     "SELECT * FROM products p JOIN warehouses w ON p.product_id = w.product_id",
     "HALLUCINATED", "UNKNOWN_TABLE"),
    ("bad table hidden inside a CTE",
     "WITH x AS (SELECT * FROM suppliers) SELECT * FROM x", "HALLUCINATED", "UNKNOWN_TABLE"),

    # ---------------- made-up COLUMN ----------------
    ("bad column: customers.email (unqualified)", "SELECT email FROM customers",
     "HALLUCINATED", "UNKNOWN_COLUMN"),
    ("bad column: c.phone (qualified)", "SELECT c.phone FROM customers c",
     "HALLUCINATED", "UNKNOWN_COLUMN"),
    ("bad column: products.discount_percent", "SELECT p.discount_percent FROM products p",
     "HALLUCINATED", "UNKNOWN_COLUMN"),
    ("bad column: orders.total_amount", "SELECT o.total_amount FROM orders o",
     "HALLUCINATED", "UNKNOWN_COLUMN"),
    ("bad column: products.customer_id (wrong table)",
     "SELECT p.product_name FROM products p JOIN categories c ON p.customer_id = c.category_id",
     "HALLUCINATED", "UNKNOWN_COLUMN"),

    # ---------------- real tables, but NOT directly joinable ----------------
    ("bad join: customers -> products (skips orders/order_items)",
     "SELECT * FROM customers c JOIN products p ON c.customer_id = p.category_id",
     "HALLUCINATED", "INVALID_JOIN_PATH"),
    ("bad join: reviews -> categories",
     "SELECT * FROM reviews r JOIN categories c ON r.review_id = c.category_id",
     "HALLUCINATED", "INVALID_JOIN_PATH"),
    ("bad join: orders -> products (skips order_items)",
     "SELECT * FROM orders o JOIN products p ON o.order_id = p.product_id",
     "HALLUCINATED", "INVALID_JOIN_PATH"),
    ("bad join: customers -> order_items",
     "SELECT * FROM customers c JOIN order_items oi ON c.customer_id = oi.order_id",
     "HALLUCINATED", "INVALID_JOIN_PATH"),

    # ======== HARDER CASES (added after code review of the first version) ========
    # --- valid queries that a naive checker would wrongly reject ---
    ("valid: EXTRACT(YEAR FROM col) has its own FROM",
     "SELECT EXTRACT(YEAR FROM order_date) AS yr, COUNT(*) FROM orders GROUP BY yr", "VALID", "NONE"),
    ("valid: CASE WHEN expression in SELECT",
     "SELECT CASE WHEN price > 10000 THEN 'high' ELSE 'low' END AS band FROM products", "VALID", "NONE"),
    ("valid: window function",
     "SELECT customer_id, order_date, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC) AS rn FROM orders",
     "VALID", "NONE"),
    ("valid: LEFT JOIN + GROUP BY + HAVING + ORDER BY alias",
     "SELECT c.full_name, COUNT(o.order_id) AS n FROM customers c LEFT JOIN orders o "
     "ON c.customer_id = o.customer_id GROUP BY c.customer_id, c.full_name HAVING COUNT(o.order_id) > 2 "
     "ORDER BY n DESC LIMIT 5", "VALID", "NONE"),
    ("valid: IN subquery", "SELECT full_name FROM customers WHERE customer_id IN "
     "(SELECT customer_id FROM orders WHERE order_status = 'DELIVERED')", "VALID", "NONE"),
    ("valid: correlated EXISTS subquery",
     "SELECT full_name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)",
     "VALID", "NONE"),
    ("valid: extra condition inside ON clause",
     "SELECT o.order_id FROM orders o JOIN order_items oi ON o.order_id = oi.order_id AND oi.quantity > 1",
     "VALID", "NONE"),
    ("valid: backticks and a dotted string literal",
     "SELECT `full_name` FROM `customers` WHERE full_name LIKE 'A%.%'", "VALID", "NONE"),
    ("valid: CTE joined to a real table",
     "WITH totals AS (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) "
     "SELECT c.full_name, t.n FROM customers c JOIN totals t ON c.customer_id = t.customer_id", "VALID", "NONE"),

    # --- joins written WITHOUT table aliases ---
    ("valid: unaliased JOIN ... ON",
     "SELECT orders.order_id, order_items.quantity FROM orders JOIN order_items "
     "ON orders.order_id = order_items.order_id", "VALID", "NONE"),
    ("valid: JOIN ... USING",
     "SELECT * FROM orders JOIN order_items USING (order_id)", "VALID", "NONE"),
    ("bad table hidden in an unaliased JOIN",
     "SELECT * FROM orders JOIN payments ON orders.order_id = payments.order_id",
     "HALLUCINATED", "UNKNOWN_TABLE"),
    ("bad join between unaliased tables",
     "SELECT * FROM customers JOIN products ON customers.customer_id = products.category_id",
     "HALLUCINATED", "INVALID_JOIN_PATH"),

    # --- made-up columns hiding OUTSIDE the SELECT list ---
    ("bad column in WHERE (unqualified)", "SELECT full_name FROM customers WHERE email = 'a@b.com'",
     "HALLUCINATED", "UNKNOWN_COLUMN"),
    ("bad column in ORDER BY", "SELECT city FROM customers ORDER BY signup_year",
     "HALLUCINATED", "UNKNOWN_COLUMN"),
    ("bad column in GROUP BY", "SELECT COUNT(*) FROM orders GROUP BY region",
     "HALLUCINATED", "UNKNOWN_COLUMN"),
    ("bad column inside aggregate", "SELECT SUM(total_amount) FROM orders",
     "HALLUCINATED", "UNKNOWN_COLUMN"),
    ("bad column in HAVING", "SELECT city, COUNT(*) FROM customers GROUP BY city HAVING AVG(age) > 30",
     "HALLUCINATED", "UNKNOWN_COLUMN"),

    # --- directly-linked tables joined on the WRONG columns ---
    ("bad join columns: orders.order_id = customers.customer_id",
     "SELECT * FROM orders o JOIN customers c ON o.order_id = c.customer_id",
     "HALLUCINATED", "INVALID_JOIN_PATH"),
    ("bad join columns: products.product_id = order_items.order_id",
     "SELECT * FROM products p JOIN order_items oi ON p.product_id = oi.order_id",
     "HALLUCINATED", "INVALID_JOIN_PATH"),
    ("bad join columns: reviews.rating = products.price",
     "SELECT * FROM reviews r JOIN products p ON r.rating = p.price",
     "HALLUCINATED", "INVALID_JOIN_PATH"),
]


def main():
    do_log = "--no-log" not in sys.argv
    print(f"Running {len(CASES)} test queries through the validator "
          f"({'logging to hallucination_log' if do_log else 'not logging'})\n")

    correct = 0
    by_type = {}  # expected_type -> [passed, total]
    failures = []
    for label, sql, exp_verdict, exp_type in CASES:
        result = validate_sql(sql)
        ok = result["verdict"] == exp_verdict and result["failure_type"] == exp_type
        correct += ok
        stats = by_type.setdefault(exp_type, [0, 0])
        stats[0] += ok
        stats[1] += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)
            print(f"        expected {exp_verdict}/{exp_type}, got {result['verdict']}/{result['failure_type']}"
                  f" ({result['failure_detail']})")
        if do_log:
            log_result(f"[benchmark] {label}", sql, "benchmark-suite", result)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    names = {"NONE": "Valid queries accepted", "UNKNOWN_TABLE": "Made-up tables caught",
             "UNKNOWN_COLUMN": "Made-up columns caught", "INVALID_JOIN_PATH": "Impossible joins caught"}
    for key in ("NONE", "UNKNOWN_TABLE", "UNKNOWN_COLUMN", "INVALID_JOIN_PATH"):
        if key in by_type:
            p, t = by_type[key]
            print(f"  {names[key]:<28} {p}/{t}")
    print(f"\n  Overall accuracy: {correct}/{len(CASES)} = {100.0 * correct / len(CASES):.1f}%")
    if failures:
        print("\n  Failed cases:", "; ".join(failures))
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()