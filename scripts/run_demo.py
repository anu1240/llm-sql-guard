"""
run_demo.py
------------
The script you actually run. For each natural-language question:
  1. Sends it + the schema to the configured LLM          (llm_connector.py)
  2. Validates the SQL that comes back                    (validator.py)
  3. Logs the question, SQL, and verdict to hallucination_log
  4. Prints a readable line per question

At the end, prints the analytics report (window-function queries)
straight from the database.

Usage:
    python3 run_demo.py
"""
import os
import sys
import time
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from llm_connector import generate_sql, SCHEMA_CONTEXT, PROVIDER
from validator import validate_sql, log_result, get_connection, show_answer

load_dotenv()

# A mix of questions: some should produce clean, valid SQL; a couple are
# deliberately worded to tempt an LLM into hallucinating (asking for a
# column/relationship that sounds plausible but isn't in the schema).
QUESTIONS = [
    "Show me all orders placed in the last 30 days.",
    "List every customer's full name and their city.",
    "What is the total revenue per product category?",
    "Show each customer's email address and phone number.",          # tempts hallucination: no email/phone column
    "List all products along with their average review rating.",
    "Show which warehouse each product is stored in.",               # tempts hallucination: no warehouse table
    "Find customers who live in Chennai and their order count.",
    "Show the category hierarchy for every product.",
]


def main():
    print(f"Using LLM provider: {PROVIDER}\n")
    # Free tiers allow only a few requests per minute, so pause between questions.
    delay = float(os.getenv("REQUEST_DELAY_SECONDS", "8"))
    for i, question in enumerate(QUESTIONS):
        if i > 0 and delay > 0:
            time.sleep(delay)
        print(f"Q: {question}")
        try:
            sql = generate_sql(question, SCHEMA_CONTEXT)
        except Exception as e:
            print(f"   [LLM ERROR] {e}\n")
            continue

        print(f"   Generated SQL: {sql}")

        try:
            result = validate_sql(sql)
        except Exception as e:
            print(f"   [VALIDATOR ERROR] {e}\n")
            continue

        print(f"   Verdict: {result['verdict']}"
              + (f" ({result['failure_type']}: {result['failure_detail']})"
                 if result["verdict"] == "HALLUCINATED" else ""))
        log_result(question, sql, PROVIDER, result)
        if result["verdict"] == "VALID":
            show_answer(sql)
        print()

    print_report()


def print_report():
    print("=" * 70)
    print("ANALYTICS REPORT (window functions over hallucination_log)")
    print("=" * 70)
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT failure_type, COUNT(*) AS occurrences,
               RANK() OVER (ORDER BY COUNT(*) DESC) AS failure_rank,
               SUM(COUNT(*)) OVER (ORDER BY COUNT(*) DESC) AS running_total
        FROM hallucination_log
        WHERE verdict = 'HALLUCINATED'
        GROUP BY failure_type
        ORDER BY failure_rank;
    """)
    rows = cursor.fetchall()
    print("\nFailure types, ranked by frequency:")
    for row in rows:
        print(f"  {row[0]:<20} count={row[1]:<4} rank={row[2]:<4} running_total={row[3]}")

    cursor.execute("""
        SELECT llm_provider, COUNT(*) AS total,
               SUM(CASE WHEN verdict='HALLUCINATED' THEN 1 ELSE 0 END) AS flagged,
               ROUND(100.0 * SUM(CASE WHEN verdict='HALLUCINATED' THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct
        FROM hallucination_log
        GROUP BY llm_provider
        ORDER BY (llm_provider = 'benchmark-suite'), llm_provider;
    """)
    print("\nFlag rate per source:")
    for provider, total, flagged, pct in cursor.fetchall():
        note = "  (hand-written test queries, bad ones are deliberate)" if provider == "benchmark-suite" else ""
        print(f"  {provider:<18} {int(flagged)}/{int(total)} flagged ({pct}%){note}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()