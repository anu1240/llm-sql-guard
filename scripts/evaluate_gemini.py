"""
evaluate_gemini.py
-------------------
Measures how well Gemini + the validator actually do on real questions.

For each question it:
  1. asks Gemini for SQL                                   (llm_connector.py)
  2. validates the SQL against the live schema             (validator.py)
  3. if VALID: runs it, and compares its result rows with a hand-written
     REFERENCE query (the known-correct answer)
  4. classifies the outcome:

       MATCH       valid SQL, and its answer equals the reference answer
       MISMATCH    valid SQL, but the answer differs  <- "valid but wrong"
       BLOCKED     validator caught a hallucination (query never run)
       SQL_ERROR   validator approved it but MySQL could not run it
       LLM_ERROR   Gemini failed / returned no SQL
     and, for the deliberate TRAP questions (data the database does not have):
       TRAP_BLOCKED      hallucination caught by the validator          (good)
       TRAP_PLACEHOLDER  Gemini honestly said the data is not available, using a
                         NULL or a text placeholder instead of inventing a column (good)
       TRAP_EVADED       Gemini returned runnable SQL that is neither blocked nor an
                         honest placeholder - read the SQL to see what it did

Results are printed, and saved to eval_results_<model>.md (and eval_state_<model>.json) in the project folder.
Rows are compared ignoring column names and row order; numbers are rounded to 2 dp.

Usage:
    python evaluate_gemini.py              # all questions, logged to hallucination_log
    python evaluate_gemini.py --no-log     # don't write to hallucination_log
    python evaluate_gemini.py --limit 5    # only the first 5 questions (quick test)
    python evaluate_gemini.py --resume     # keep earlier answers (eval_state.json); only run the questions
                                           # that still have no answer (e.g. after the daily Gemini quota
                                           # ran out). The script stops by itself when the quota is hit.
"""
import datetime
import decimal
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv

load_dotenv()
from llm_connector import generate_sql, SCHEMA_CONTEXT, PROVIDER
from validator import validate_sql, log_result, execute_select

# (question, reference_sql)   reference_sql = None  ->  TRAP question (no correct answer exists)
QUESTIONS = [
    # ---------------- simple ----------------
    ("How many customers are there?",
     "SELECT COUNT(*) FROM customers"),
    ("List the names of all products in the Laptops category.",
     "SELECT p.product_name FROM products p JOIN categories c ON p.category_id = c.category_id "
     "WHERE c.category_name = 'Laptops'"),
    ("How many customers does each city have? Show the city and the count.",
     "SELECT city, COUNT(*) FROM customers GROUP BY city"),
    ("What is the most expensive product? Show its name and price.",
     "SELECT product_name, price FROM products ORDER BY price DESC LIMIT 1"),
    ("How many orders have been cancelled?",
     "SELECT COUNT(*) FROM orders WHERE order_status = 'CANCELLED'"),
    ("How many orders are there in each order status? Show the status and the count.",
     "SELECT order_status, COUNT(*) FROM orders GROUP BY order_status"),
    ("List the full name and city of every GOLD tier customer.",
     "SELECT full_name, city FROM customers WHERE customer_tier = 'GOLD'"),
    ("Show each category name and the average price of its products.",
     "SELECT c.category_name, AVG(p.price) FROM categories c JOIN products p ON p.category_id = c.category_id "
     "GROUP BY c.category_id, c.category_name"),

    # ---------------- joins and aggregates ----------------
    ("What is the total revenue (quantity times unit price) from DELIVERED orders?",
     "SELECT SUM(oi.quantity * oi.unit_price) FROM order_items oi JOIN orders o ON oi.order_id = o.order_id "
     "WHERE o.order_status = 'DELIVERED'"),
    ("Show the total revenue (quantity times unit price) for each product category name.",
     "SELECT c.category_name, SUM(oi.quantity * oi.unit_price) FROM order_items oi "
     "JOIN products p ON oi.product_id = p.product_id JOIN categories c ON p.category_id = c.category_id "
     "GROUP BY c.category_id, c.category_name"),
    ("Who are the top 5 customers by total amount spent (quantity times unit price, all orders)? "
     "Show full name and total spent.",
     "SELECT c.full_name, SUM(oi.quantity * oi.unit_price) AS s FROM customers c "
     "JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id "
     "GROUP BY c.customer_id, c.full_name ORDER BY s DESC LIMIT 5"),
    ("How many reviews has each product received? Show the product name and review count, "
     "including products with no reviews.",
     "SELECT p.product_name, COUNT(r.review_id) FROM products p LEFT JOIN reviews r ON r.product_id = p.product_id "
     "GROUP BY p.product_id, p.product_name"),
    ("What is the average rating of each product that has at least one review? "
     "Show product name and average rating.",
     "SELECT p.product_name, AVG(r.rating) FROM products p JOIN reviews r ON r.product_id = p.product_id "
     "GROUP BY p.product_id, p.product_name"),
    ("Which customers have never placed an order? Show their full names.",
     "SELECT c.full_name FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id "
     "WHERE o.order_id IS NULL"),
    ("Which customers have placed more than 12 orders? Show full name and order count.",
     "SELECT c.full_name, COUNT(o.order_id) FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
     "GROUP BY c.customer_id, c.full_name HAVING COUNT(o.order_id) > 12"),
    ("For each customer tier, how many customers are there and how many orders have they placed in total?",
     "SELECT c.customer_tier, COUNT(DISTINCT c.customer_id), COUNT(o.order_id) FROM customers c "
     "LEFT JOIN orders o ON o.customer_id = c.customer_id GROUP BY c.customer_tier"),
    ("Find customers who live in Chennai and their order count. Show full name and count.",
     "SELECT c.full_name, COUNT(o.order_id) FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id "
     "WHERE c.city = 'Chennai' GROUP BY c.customer_id, c.full_name"),
    ("How many orders were placed in each year and month? Show year, month and count as separate columns.",
     "SELECT YEAR(order_date), MONTH(order_date), COUNT(*) FROM orders GROUP BY YEAR(order_date), MONTH(order_date)"),

    # ---------------- advanced ----------------
    ("For each customer who has ordered, show their full name and the date of their most recent order.",
     "SELECT c.full_name, MAX(o.order_date) FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
     "GROUP BY c.customer_id, c.full_name"),
    ("Rank the products within each category by price, highest first. "
     "Show category name, product name, price and the rank.",
     "SELECT c.category_name, p.product_name, p.price, "
     "RANK() OVER (PARTITION BY p.category_id ORDER BY p.price DESC) FROM products p "
     "JOIN categories c ON p.category_id = c.category_id"),
    ("For every category show its name and its parent category's name (NULL if it has no parent).",
     "SELECT c.category_name, p.category_name FROM categories c "
     "LEFT JOIN categories p ON c.parent_category_id = p.category_id"),
    ("Which customers have spent more than the average spend per customer (quantity times unit price, "
     "counting only customers who have ordered)? Show full name.",
     "WITH spend AS (SELECT o.customer_id, SUM(oi.quantity * oi.unit_price) AS s FROM orders o "
     "JOIN order_items oi ON oi.order_id = o.order_id GROUP BY o.customer_id) "
     "SELECT c.full_name FROM customers c JOIN spend ON spend.customer_id = c.customer_id "
     "WHERE spend.s > (SELECT AVG(s) FROM spend)"),
    ("Which customers have reviewed a product that they never ordered? Show the customer's full name "
     "and the product name.",
     "SELECT DISTINCT c.full_name, p.product_name FROM reviews r JOIN customers c ON c.customer_id = r.customer_id "
     "JOIN products p ON p.product_id = r.product_id WHERE NOT EXISTS ("
     "SELECT 1 FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "WHERE o.customer_id = r.customer_id AND oi.product_id = r.product_id)"),
    ("Show the total quantity sold for each product that has been sold at least once. "
     "Show product name and total quantity.",
     "SELECT p.product_name, SUM(oi.quantity) FROM products p JOIN order_items oi ON oi.product_id = p.product_id "
     "GROUP BY p.product_id, p.product_name"),
    ("How many orders were placed in the last 30 days?",
     "SELECT COUNT(*) FROM orders WHERE order_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"),

    # ---------------- TRAPS: the database has no such data ----------------
    ("Show each customer's email address and phone number.", None),
    ("Which warehouse is each product stored in?", None),
    ("What discount percentage was applied to each order?", None),
    ("List the supplier of every product.", None),
    ("Show each customer's date of birth and age.", None),
    ("Show the payment method used for each order.", None),
]


def is_honest_placeholder(sql):
    """True if the SQL answers an unanswerable question with a NULL or a text literal that
    says the data is missing, instead of inventing a column or table."""
    import re
    if re.search(r"\bNULL\s+AS\b", sql, re.IGNORECASE):
        return True
    phrases = r"not available|unavailable|not exist|does not|no such|not in (?:the )?schema|not found|not stored|not recorded|\bn/?a\b|unknown|not tracked|not applicable"
    for literal in re.findall(r"'([^']*)'", sql):
        if re.search(phrases, literal, re.IGNORECASE):
            return True
    return False


def norm_value(v):
    if v is None:
        return "NULL"
    if isinstance(v, (decimal.Decimal, float, int)) and not isinstance(v, bool):
        return f"{round(float(v), 2):.2f}"
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


def norm_rows(rows):
    return sorted(tuple(norm_value(v) for v in r) for r in rows)


def run_all(sql):
    cols, rows, _ = execute_select(sql, max_rows=100000)
    return cols, rows


STATE_PATH = os.path.join(os.path.dirname(__file__), "..", f"eval_state_{PROVIDER}.json")


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except Exception:
        return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in sorted(state.items())}, f, indent=1, ensure_ascii=False)


def main():
    do_log = "--no-log" not in sys.argv
    resume = "--resume" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    questions = QUESTIONS[:limit] if limit else QUESTIONS
    delay = float(os.getenv("REQUEST_DELAY_SECONDS", "8"))

    print(f"Evaluating {len(questions)} questions with {PROVIDER} "
          f"({'logging to hallucination_log' if do_log else 'not logging'}"
          f"{', resuming from eval_state.json' if resume else ''})\n")
    state = load_state() if resume else {}
    quota_hit = False
    made_call = False
    for i, (question, ref_sql) in enumerate(questions, 1):
        old = state.get(i)
        if resume and old and old["outcome"] not in ("LLM_ERROR", "NOT_RUN"):
            print(f"[{i}/{len(questions)}] {question}\n    (kept from an earlier run: {old['outcome']})\n")
            continue
        is_trap = ref_sql is None
        rec = {"n": i, "question": question, "trap": is_trap, "sql": "", "verdict": "", "detail": "", "outcome": "",
               "date": str(datetime.date.today())}
        print(f"[{i}/{len(questions)}] {question}")
        if quota_hit:
            rec.update(outcome="NOT_RUN", detail="Not run: the Gemini daily free quota was used up earlier in this run.")
            state[i] = rec
            save_state(state)
            print(f"    NOT_RUN: {rec['detail']}\n")
            continue
        if made_call and delay > 0:
            time.sleep(delay)
        made_call = True
        try:
            sql = generate_sql(question, SCHEMA_CONTEXT)
        except Exception as e:
            msg = str(e)
            if "DAILY" in msg:
                quota_hit = True
                rec.update(outcome="NOT_RUN", detail="Not run: the Gemini daily free quota is used up (it resets once a day).")
            else:
                rec.update(outcome="LLM_ERROR", detail=msg[:200])
            state[i] = rec
            save_state(state)
            print(f"    {rec['outcome']}: {rec['detail']}\n")
            continue
        rec["sql"] = sql
        result = validate_sql(sql)
        rec["verdict"] = result["verdict"]
        if do_log:
            log_result(question, sql, PROVIDER, result)

        if result["verdict"] == "HALLUCINATED":
            rec["outcome"] = "TRAP_BLOCKED" if is_trap else "BLOCKED"
            rec["detail"] = f"{result['failure_type']}: {result['failure_detail']}"
        elif is_trap and is_honest_placeholder(sql):
            rec["outcome"] = "TRAP_PLACEHOLDER"
            rec["detail"] = "Gemini said the data is not available (NULL / text placeholder) instead of inventing a column."
        elif is_trap:
            rec["outcome"] = "TRAP_EVADED"
            rec["detail"] = "Gemini returned runnable SQL for data the database does not have - read the SQL to see what it did."
        else:
            try:
                _, got = run_all(sql)
            except Exception as e:
                rec.update(outcome="SQL_ERROR", detail=str(e)[:200])
            else:
                _, want = run_all(ref_sql)
                if norm_rows(got) == norm_rows(want):
                    rec["outcome"] = "MATCH"
                    rec["detail"] = f"{len(got)} row(s) identical to the reference answer"
                else:
                    rec["outcome"] = "MISMATCH"
                    rec["detail"] = f"got {len(got)} row(s), reference has {len(want)} row(s)"
        state[i] = rec
        save_state(state)
        print(f"    {rec['outcome']}: {rec['detail']}\n")

    results = [state[i] for i in sorted(state)]
    counts = Counter(r["outcome"] for r in results)
    real = [r for r in results if not r["trap"]]
    traps = [r for r in results if r["trap"]]
    valid_real = [r for r in real if r["verdict"] == "VALID"]
    print("=" * 66)
    print("SUMMARY")
    print("=" * 66)
    print(f"Real questions: {len(real)}")
    print(f"  Answer matched the reference:           {counts['MATCH']}")
    print(f"  Valid SQL but WRONG answer (mismatch):  {counts['MISMATCH']}")
    print(f"  Blocked by the validator:               {counts['BLOCKED']}")
    print(f"  MySQL error after approval:             {counts['SQL_ERROR']}")
    print(f"  Gemini/LLM error:                       {sum(1 for r in real if r['outcome'] == 'LLM_ERROR')}")
    print(f"  Not answered yet (daily quota):         {sum(1 for r in real if r['outcome'] == 'NOT_RUN')}")
    if valid_real:
        print(f"  -> of {len(valid_real)} queries the validator approved, "
              f"{counts['MATCH']} were also correct ({100.0 * counts['MATCH'] / len(valid_real):.0f}%)")
    print(f"Trap questions (no such data exists): {len(traps)}")
    print(f"  Blocked by the validator:  {counts['TRAP_BLOCKED']}")
    print(f"  Honest placeholder (good): {counts['TRAP_PLACEHOLDER']}")
    print(f"  Evaded (check the SQL):    {counts['TRAP_EVADED']}")
    print(f"  Not answered yet (quota):  {sum(1 for r in traps if r['outcome'] == 'NOT_RUN')}")

    write_report(results, counts, real, traps, valid_real)


def write_report(results, counts, real, traps, valid_real):
    path = os.path.join(os.path.dirname(__file__), "..", f"eval_results_{PROVIDER}.md")
    lines = [
        "# Gemini + LLM-SQL Guard - evaluation results",
        "",
        f"Run on {', '.join(sorted({r.get('date', str(datetime.date.today())) for r in results}))} with model `{PROVIDER}`. "
        "Answers are compared with hand-written reference queries (rows compared ignoring column names/order, numbers rounded to 2 dp).",
        "",
        "## Summary",
        "",
        f"- Real questions: **{len(real)}**",
        f"  - Answer matched the reference: **{counts['MATCH']}**",
        f"  - Valid SQL but wrong answer: **{counts['MISMATCH']}**",
        f"  - Blocked by the validator: **{counts['BLOCKED']}**",
        f"  - MySQL error after approval: **{counts['SQL_ERROR']}**",
        f"  - Gemini/LLM error: **{sum(1 for r in real if r['outcome'] == 'LLM_ERROR')}**",
        f"  - Not answered yet (daily quota): **{sum(1 for r in real if r['outcome'] == 'NOT_RUN')}**",
    ]
    if valid_real:
        lines.append(f"  - Of {len(valid_real)} approved queries, {counts['MATCH']} were correct "
                     f"({100.0 * counts['MATCH'] / len(valid_real):.0f}%) - 'valid' does not guarantee 'correct'.")
    lines += [
        f"- Trap questions (the database has no such data): **{len(traps)}**",
        f"  - Blocked by the validator: **{counts['TRAP_BLOCKED']}**",
        f"  - Honest placeholder (NULL / 'not available' instead of inventing data): **{counts['TRAP_PLACEHOLDER']}**",
        f"  - Evaded (runnable SQL that is neither blocked nor an honest placeholder - review manually): **{counts['TRAP_EVADED']}**",
        f"  - Not answered yet (daily quota): **{sum(1 for r in traps if r['outcome'] == 'NOT_RUN')}**",
        "",
        "## Per-question results",
        "",
        "| # | Question | Outcome | Detail |",
        "|---|----------|---------|--------|",
    ]
    for r in results:
        q = r["question"].replace("|", "/")
        d = (r["detail"] or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {r['n']} | {q} | {r['outcome']} | {d} |")
    lines += ["", "## Generated SQL", ""]
    for r in results:
        lines += [f"**{r['n']}. {r['question']}** - {r['outcome']}", "", "```sql", r["sql"] or "(none)", "```", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nSaved: {os.path.normpath(path)}")


if __name__ == "__main__":
    main()
