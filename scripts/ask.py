"""
ask.py
-------
Type your OWN questions and see the validator's verdict live.

  python ask.py                      -> interactive: type a question, press Enter
  python ask.py "your question"      -> one question, then exit

Optional: to test the validator with SQL you write yourself (no Gemini needed):
  python ask.py --sql "SELECT email FROM customers"

Every result is saved to hallucination_log, like run_demo.py does.
Type  quit  (or press Ctrl+C) to stop.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv

load_dotenv()
from validator import validate_sql, log_result, show_answer


def show(result):
    if result["verdict"] == "VALID":
        print("   Verdict: VALID  (tables, columns and joins all exist in the schema)")
    else:
        print(f"   Verdict: HALLUCINATED  ->  {result['failure_type']}: {result['failure_detail']}")


def check_sql(sql, label, provider):
    result = validate_sql(sql)
    show(result)
    log_result(label, sql, provider, result)
    if result["verdict"] == "VALID":
        show_answer(sql)
    else:
        print("   Blocked: this query was NOT run because it does not match the database schema.")


def ask(question):
    from llm_connector import generate_sql, SCHEMA_CONTEXT, PROVIDER
    print(f"\nQ: {question}")
    try:
        sql = generate_sql(question, SCHEMA_CONTEXT)
    except Exception as e:
        print(f"   [LLM ERROR] {e}")
        return
    print(f"   Generated SQL: {sql}")
    check_sql(sql, question, PROVIDER)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--sql":
        sql = " ".join(args[1:])
        print(f"\nSQL: {sql}")
        check_sql(sql, "[manual SQL] " + sql[:150], "manual")
        return
    if args:
        ask(" ".join(args))
        return
    print("Type a question about the shop database (customers, orders, products,")
    print("categories, order_items, reviews). Type 'quit' to stop.")
    while True:
        try:
            q = input("\nYour question> ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if q.lower() in ("quit", "exit", "q"):
            break
        if q:
            ask(q)


if __name__ == "__main__":
    main()