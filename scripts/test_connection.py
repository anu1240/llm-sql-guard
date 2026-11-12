"""
test_connection.py
-------------------
Run this FIRST. It checks the three things that usually go wrong, one at a
time, and tells you exactly which one failed:

  1. Is the .env file found and filled in?
  2. Can Python connect to your MySQL database (the one in Workbench)?
  3. Can Python reach Gemini with your API key?

Usage:   python test_connection.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import find_dotenv, load_dotenv

print("=" * 60)
print("STEP 1: .env file")
print("=" * 60)
env_path = find_dotenv()
if not env_path:
    print("FAIL: no .env file found.")
    print("  - It must be named exactly  .env  (not .env.txt) and sit in the")
    print("    project folder (next to README.md) or in the scripts folder.")
    print("  - On Windows, turn on 'File name extensions' in File Explorer to check.")
    sys.exit(1)
print(f"OK: found {env_path}")
load_dotenv(env_path)

provider = os.getenv("LLM_PROVIDER", "gemini")
key = os.getenv("GEMINI_API_KEY", "").strip()
print(f"   LLM_PROVIDER = {provider}")
print(f"   GEMINI_API_KEY = {'set (' + str(len(key)) + ' chars)' if key else 'EMPTY'}")
print(f"   DB_USER = {os.getenv('DB_USER', 'root')}, DB_PASSWORD = {'set' if os.getenv('DB_PASSWORD') else 'EMPTY'}")

print()
print("=" * 60)
print("STEP 2: MySQL connection")
print("=" * 60)
try:
    from validator import get_connection

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM customers")
    n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL")
    fks = cur.fetchone()[0]
    print(f"OK: connected. customers table has {n} rows, {fks} foreign-key links found.")
    cur.close()
    conn.close()
except Exception as e:
    print(f"FAIL: {e}")
    print("  Common causes:")
    print("  - DB_PASSWORD in .env must be the same password you type in MySQL Workbench.")
    print("  - You have not run 01_schema.sql and 02_seed_data.sql in Workbench yet.")
    print("  - MySQL server is not running.")
    sys.exit(1)

print()
print("=" * 60)
print("STEP 3: Gemini API")
print("=" * 60)
try:
    from llm_connector import generate_sql, SCHEMA_CONTEXT

    sql = generate_sql("Show all customers", SCHEMA_CONTEXT)
    print("OK: Gemini replied with:")
    print("   " + sql)
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)

print()
print("All three checks passed. Now run:  python run_demo.py")
