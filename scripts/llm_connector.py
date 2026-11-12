"""
llm_connector.py
-----------------
One function — generate_sql(question, schema_context) — that calls
Google Gemini (key and model come from .env) and returns the SQL it generated.
"""
import os
import re
import time
import requests
from dotenv import load_dotenv

load_dotenv()

# This project uses Google Gemini only. PROVIDER is the label stored in hallucination_log:
# the exact model name, so results from different Gemini models can be compared.
PROVIDER = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

PROMPT_TEMPLATE = """You are a SQL generator for a MySQL 8 database. Given a database
schema and a natural language question, output ONLY the SQL query that answers it.
Use MySQL syntax (for dates use CURDATE(), NOW() and INTERVAL; do NOT use SQLite
functions like date('now')). Do not write THOUGHT, reasoning, explanations,
markdown or code fences. Your whole reply must be one SQL statement starting
with SELECT or WITH.

Schema:
{schema}

Question: {question}

SQL:"""


def _extract_sql(raw_text: str) -> str:
    """Pull the SQL statement out of the model's reply. Raises RuntimeError if the
    reply contains no SQL at all (e.g. only reasoning text), so that case is
    reported as 'no SQL returned' instead of being mistaken for a hallucination."""
    text = raw_text.strip()
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = re.search(r"^\s*(SELECT|WITH)\b", text, flags=re.IGNORECASE | re.MULTILINE)
    if not start:
        raise RuntimeError(
            "The model did not return a SQL query (no SELECT/WITH found). "
            f"Reply started with: {text[:80]!r}"
        )
    text = text[start.start():].strip()
    if ";" in text:
        text = text[: text.index(";") + 1]
    return text.strip()


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Model names change over time. Override in .env with GEMINI_MODEL=... if this one stops working.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def list_gemini_models(api_key: str) -> list:
    """Return the model names this API key can call with generateContent."""
    resp = requests.get(f"{GEMINI_BASE}/models", headers={"x-goog-api-key": api_key}, timeout=30)
    resp.raise_for_status()
    names = []
    for m in resp.json().get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            names.append(m["name"].replace("models/", ""))
    return names


RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


def _call_gemini(prompt: str) -> str:
    """Calls Gemini. The key is sent in a header (never in the URL) so it can't
    leak into error messages. Busy/rate-limit errors are retried automatically."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is empty. Open your .env file and paste your key after GEMINI_API_KEY=")
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key}
    body = {"contents": [{"parts": [{"text": prompt}]}]}

    def scrub(text: str) -> str:
        return str(text).replace(api_key, "***")

    last_problem = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = 8 * attempt
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=60)
        except requests.exceptions.RequestException as e:
            last_problem = f"network problem ({type(e).__name__})"
        else:
            if resp.status_code == 404:
                try:
                    available = ", ".join(list_gemini_models(api_key)[:15])
                except Exception:
                    available = "(could not fetch the list)"
                raise RuntimeError(
                    f"Gemini model '{model}' was not found. Models your key can use: {available}. "
                    f"Put one of them in .env as GEMINI_MODEL=<name>."
                )
            if resp.status_code in (400, 401, 403):
                raise RuntimeError(
                    f"Gemini rejected the request (HTTP {resp.status_code}). "
                    f"Check the API key in .env. Details: {scrub(resp.text[:300])}"
                )
            if resp.status_code == 429:
                if "PerDay" in resp.text:
                    raise RuntimeError(
                        "Gemini DAILY free quota is used up. It resets once a day - try again "
                        "tomorrow, or use a different API key in .env."
                    )
                last_problem = "per-minute rate limit (HTTP 429)"
                wait = 25 * attempt
            elif resp.status_code in RETRY_STATUS:
                last_problem = f"Gemini server busy (HTTP {resp.status_code})"
            elif resp.ok:
                data = resp.json()
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    raise RuntimeError(f"Gemini returned no text (possibly blocked). Raw response: {scrub(str(data)[:300])}")
            else:
                raise RuntimeError(f"Gemini error HTTP {resp.status_code}: {scrub(resp.text[:200])}")

        if attempt < MAX_ATTEMPTS:
            print(f"   ... {last_problem}. Retrying in {wait}s (attempt {attempt}/{MAX_ATTEMPTS})")
            time.sleep(wait)

    raise RuntimeError(
        f"Gemini still unavailable after {MAX_ATTEMPTS} attempts ({last_problem}). "
        f"Wait a few minutes and run again."
    )


def generate_sql(question: str, schema_context: str) -> str:
    """Send a natural-language question + schema description to the
    Gemini, return the SQL it produced."""
    prompt = PROMPT_TEMPLATE.format(schema=schema_context, question=question)
    last_error = None
    for _ in range(2):  # if the reply has no SQL, ask once more, more strictly
        raw = _call_gemini(prompt)
        try:
            return _extract_sql(raw)
        except RuntimeError as err:
            last_error = err
            prompt += "\n\nIMPORTANT: reply with ONLY the SQL query, starting with SELECT or WITH. No other text."
    raise last_error


# A plain-English description of the schema, handed to the LLM as context.
# It is built from the LIVE database (INFORMATION_SCHEMA), so it can never drift from the
# real tables. The hand-written text below is only a fallback if the database is unreachable.
_FALLBACK_SCHEMA_CONTEXT = """
Tables:
- customers(customer_id, full_name, city, signup_date, customer_tier)
- categories(category_id, category_name, parent_category_id)
- products(product_id, product_name, category_id, price, stock_quantity)
- orders(order_id, customer_id, order_date, order_status)
- order_items(order_item_id, order_id, product_id, quantity, unit_price)
- reviews(review_id, product_id, customer_id, rating, review_date)

Relationships:
- products.category_id -> categories.category_id
- orders.customer_id -> customers.customer_id
- order_items.order_id -> orders.order_id
- order_items.product_id -> products.product_id
- reviews.product_id -> products.product_id
- reviews.customer_id -> customers.customer_id
"""

def _build_schema_context() -> str:
    try:
        from validator import get_schema_context
        return get_schema_context()
    except Exception:
        return _FALLBACK_SCHEMA_CONTEXT


SCHEMA_CONTEXT = _build_schema_context()
