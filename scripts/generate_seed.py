"""
Generates seed data for llm_sql_guard DB.
Run: python3 generate_seed.py > ../db/02_seed_data.sql
"""
import random


random.seed(7)

def esc(s):
    return str(s).replace("'", "''")

lines = ["USE llm_sql_guard;", ""]

# ---------------- Categories (hierarchy, for recursive CTE demo) ----------------
categories = [
    (1, "Electronics", None),
    (2, "Clothing", None),
    (3, "Home & Kitchen", None),
    (4, "Mobiles", 1),
    (5, "Laptops", 1),
    (6, "Men's Wear", 2),
    (7, "Women's Wear", 2),
    (8, "Cookware", 3),
    (9, "Furniture", 3),
]
lines.append("INSERT INTO categories (category_id, category_name, parent_category_id) VALUES")
lines.append(",\n".join(
    f"({cid}, '{esc(name)}', {pid if pid else 'NULL'})" for cid, name, pid in categories
) + ";")
lines.append("")

# ---------------- Products ----------------
product_pool = {
    4: ["Galaxy M14", "Pixel 8a", "OnePlus Nord 4", "iQOO Z9"],
    5: ["ThinkPad E14", "MacBook Air M2", "HP Pavilion 15", "Dell Inspiron 14"],
    6: ["Cotton Shirt", "Denim Jacket", "Formal Trousers", "Sports Track Pants"],
    7: ["Cotton Kurti", "Silk Saree", "Denim Jeans", "Casual Top"],
    8: ["Non-stick Pan", "Pressure Cooker", "Steel Kadai", "Induction Tawa"],
    9: ["Study Table", "Bookshelf", "Office Chair", "Bean Bag"],
}
# Realistic price band (in rupees) per category, so the demo answers look believable.
price_range = {4: (8000, 60000), 5: (30000, 120000), 6: (500, 3000),
               7: (500, 6000), 8: (400, 3500), 9: (2000, 25000)}
products = []
pid = 1
for cat_id, names in product_pool.items():
    for name in names:
        lo, hi = price_range[cat_id]
        price = round(random.uniform(lo, hi), 2)
        stock = random.randint(0, 300)
        products.append((pid, name, cat_id, price, stock))
        pid += 1

lines.append("INSERT INTO products (product_id, product_name, category_id, price, stock_quantity) VALUES")
lines.append(",\n".join(
    f"({p[0]}, '{esc(p[1])}', {p[2]}, {p[3]}, {p[4]})" for p in products
) + ";")
lines.append("")

# ---------------- Customers ----------------
cities = ["Chennai", "Bengaluru", "Hyderabad", "Mumbai", "Delhi", "Pune", "Kolkata"]
first_names = ["Arun", "Priya", "Karthik", "Divya", "Ravi", "Sneha", "Vijay", "Anitha",
               "Suresh", "Meena", "Rahul", "Kavya", "Manoj", "Deepa", "Sathish", "Lakshmi"]
last_names = ["Kumar", "Raj", "Iyer", "Nair", "Sharma", "Reddy", "Pillai", "Menon"]
tiers = ["BRONZE", "SILVER", "GOLD"]

# All dates are written as DATE_SUB(CURDATE(), INTERVAL n DAY), i.e. "n days ago",
# so the data is always fresh and questions like "orders in the last 30 days" return rows
# no matter when the seed file is loaded.
def ago(n):
    return f"DATE_SUB(CURDATE(), INTERVAL {n} DAY)"


customers = []  # (id, name, city, signup_days_ago, tier)
for cid in range(1, 61):
    name = f"{random.choice(first_names)} {random.choice(last_names)}"
    city = random.choice(cities)
    signup_ago = random.randint(150, 700)
    tier = random.choices(tiers, weights=[0.5, 0.35, 0.15])[0]
    customers.append((cid, name, city, signup_ago, tier))

lines.append("INSERT INTO customers (customer_id, full_name, city, signup_date, customer_tier) VALUES")
lines.append(",\n".join(
    f"({c[0]}, '{esc(c[1])}', '{esc(c[2])}', {ago(c[3])}, '{c[4]}')" for c in customers
) + ";")
lines.append("")

# ---------------- Orders + Order Items ----------------
orders, order_items = [], []
oid = oiid = 1
statuses = ["PLACED", "SHIPPED", "DELIVERED", "CANCELLED"]
status_weights = [0.05, 0.1, 0.78, 0.07]

for cid, _, _, signup_ago, tier in customers:
    n_orders = random.randint(8, 20) if tier == "GOLD" else random.randint(3, 12) if tier == "SILVER" else random.randint(0, 6)
    for _ in range(n_orders):
        order_ago = random.randint(0, signup_ago - 1)   # any day between signup and today
        # recent orders are less likely to be delivered yet
        if order_ago < 5:
            status = random.choices(statuses, weights=[0.5, 0.35, 0.1, 0.05])[0]
        else:
            status = random.choices(statuses, weights=status_weights)[0]
        orders.append((oid, cid, order_ago, status))
        for _ in range(random.randint(1, 4)):
            prod = random.choice(products)
            qty = random.randint(1, 3)
            order_items.append((oiid, oid, prod[0], qty, prod[3]))
            oiid += 1
        oid += 1

lines.append("INSERT INTO orders (order_id, customer_id, order_date, order_status) VALUES")
lines.append(",\n".join(f"({o[0]}, {o[1]}, {ago(o[2])}, '{o[3]}')" for o in orders) + ";")
lines.append("")

lines.append("INSERT INTO order_items (order_item_id, order_id, product_id, quantity, unit_price) VALUES")
lines.append(",\n".join(f"({oi[0]}, {oi[1]}, {oi[2]}, {oi[3]}, {oi[4]})" for oi in order_items) + ";")
lines.append("")

# ---------------- Reviews ----------------
reviews = []
rid = 1
for cid, _, _, _, _ in customers:
    for _ in range(random.randint(0, 5)):
        prod = random.choice(products)
        rating = random.choices([1, 2, 3, 4, 5], weights=[0.05, 0.05, 0.15, 0.35, 0.4])[0]
        reviews.append((rid, prod[0], cid, rating, random.randint(0, 600)))
        rid += 1

lines.append("INSERT INTO reviews (review_id, product_id, customer_id, rating, review_date) VALUES")
lines.append(",\n".join(f"({r[0]}, {r[1]}, {r[2]}, {r[3]}, {ago(r[4])})" for r in reviews) + ";")
lines.append("")

print("\n".join(lines))
