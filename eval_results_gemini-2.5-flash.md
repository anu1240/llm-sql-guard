# Gemini + LLM-SQL Guard - evaluation results

Run on 2026-11-11, 2026-11-12 with model `gemini-2.5-flash`. Answers are compared with hand-written reference queries (rows compared ignoring column names/order, numbers rounded to 2 dp).

## Summary

- Real questions: **25**
  - Answer matched the reference: **24**
  - Valid SQL but wrong answer: **1**
  - Blocked by the validator: **0**
  - MySQL error after approval: **0**
  - Gemini/LLM error: **0**
  - Not answered yet (daily quota): **0**
  - Of 25 approved queries, 24 were correct (96%) - 'valid' does not guarantee 'correct'.
- Trap questions (the database has no such data): **6**
  - Blocked by the validator: **0**
  - Honest placeholder (NULL / 'not available' instead of inventing data): **5**
  - Evaded (runnable SQL that is neither blocked nor an honest placeholder - review manually): **1**
  - Not answered yet (daily quota): **0**

## Per-question results

| # | Question | Outcome | Detail |
|---|----------|---------|--------|
| 1 | How many customers are there? | MATCH | 1 row(s) identical to the reference answer |
| 2 | List the names of all products in the Laptops category. | MATCH | 4 row(s) identical to the reference answer |
| 3 | How many customers does each city have? Show the city and the count. | MATCH | 7 row(s) identical to the reference answer |
| 4 | What is the most expensive product? Show its name and price. | MATCH | 1 row(s) identical to the reference answer |
| 5 | How many orders have been cancelled? | MATCH | 1 row(s) identical to the reference answer |
| 6 | How many orders are there in each order status? Show the status and the count. | MATCH | 4 row(s) identical to the reference answer |
| 7 | List the full name and city of every GOLD tier customer. | MATCH | 8 row(s) identical to the reference answer |
| 8 | Show each category name and the average price of its products. | MATCH | 6 row(s) identical to the reference answer |
| 9 | What is the total revenue (quantity times unit price) from DELIVERED orders? | MATCH | 1 row(s) identical to the reference answer |
| 10 | Show the total revenue (quantity times unit price) for each product category name. | MATCH | 6 row(s) identical to the reference answer |
| 11 | Who are the top 5 customers by total amount spent (quantity times unit price, all orders)? Show full name and total spent. | MATCH | 5 row(s) identical to the reference answer |
| 12 | How many reviews has each product received? Show the product name and review count, including products with no reviews. | MATCH | 24 row(s) identical to the reference answer |
| 13 | What is the average rating of each product that has at least one review? Show product name and average rating. | MATCH | 24 row(s) identical to the reference answer |
| 14 | Which customers have never placed an order? Show their full names. | MATCH | 4 row(s) identical to the reference answer |
| 15 | Which customers have placed more than 12 orders? Show full name and order count. | MATCH | 3 row(s) identical to the reference answer |
| 16 | For each customer tier, how many customers are there and how many orders have they placed in total? | MATCH | 3 row(s) identical to the reference answer |
| 17 | Find customers who live in Chennai and their order count. Show full name and count. | MATCH | 9 row(s) identical to the reference answer |
| 18 | How many orders were placed in each year and month? Show year, month and count as separate columns. | MATCH | 23 row(s) identical to the reference answer |
| 19 | For each customer who has ordered, show their full name and the date of their most recent order. | MATCH | 56 row(s) identical to the reference answer |
| 20 | Rank the products within each category by price, highest first. Show category name, product name, price and the rank. | MATCH | 24 row(s) identical to the reference answer |
| 21 | For every category show its name and its parent category's name (NULL if it has no parent). | MATCH | 9 row(s) identical to the reference answer |
| 22 | Which customers have spent more than the average spend per customer (quantity times unit price, counting only customers who have ordered)? Show full name. | MATCH | 23 row(s) identical to the reference answer |
| 23 | Which customers have reviewed a product that they never ordered? Show the customer's full name and the product name. | MISMATCH | got 85 row(s), reference has 81 row(s) |
| 24 | Show the total quantity sold for each product that has been sold at least once. Show product name and total quantity. | MATCH | 24 row(s) identical to the reference answer |
| 25 | How many orders were placed in the last 30 days? | MATCH | 1 row(s) identical to the reference answer |
| 26 | Show each customer's email address and phone number. | TRAP_PLACEHOLDER | Gemini said the data is not available (NULL / text placeholder) instead of inventing a column. |
| 27 | Which warehouse is each product stored in? | TRAP_PLACEHOLDER | Gemini said the data is not available (NULL / text placeholder) instead of inventing a column. |
| 28 | What discount percentage was applied to each order? | TRAP_EVADED | Gemini returned runnable SQL for data the database does not have - read the SQL to see what it did. |
| 29 | List the supplier of every product. | TRAP_PLACEHOLDER | Gemini said the data is not available (NULL / text placeholder) instead of inventing a column. |
| 30 | Show each customer's date of birth and age. | TRAP_PLACEHOLDER | Gemini said the data is not available (NULL / text placeholder) instead of inventing a column. |
| 31 | Show the payment method used for each order. | TRAP_PLACEHOLDER | Gemini said the data is not available (NULL / text placeholder) instead of inventing a column. |

## Generated SQL

**1. How many customers are there?** - MATCH

```sql
SELECT COUNT(customer_id) FROM customers;
```

**2. List the names of all products in the Laptops category.** - MATCH

```sql
SELECT T1.product_name
FROM products AS T1
INNER JOIN categories AS T2
  ON T1.category_id = T2.category_id
WHERE
  T2.category_name = 'Laptops';
```

**3. How many customers does each city have? Show the city and the count.** - MATCH

```sql
SELECT city, COUNT(customer_id) FROM customers GROUP BY city
```

**4. What is the most expensive product? Show its name and price.** - MATCH

```sql
SELECT product_name, price FROM products ORDER BY price DESC LIMIT 1
```

**5. How many orders have been cancelled?** - MATCH

```sql
SELECT COUNT(order_id) FROM orders WHERE order_status = 'Cancelled'
```

**6. How many orders are there in each order status? Show the status and the count.** - MATCH

```sql
SELECT order_status, COUNT(order_id) FROM orders GROUP BY order_status
```

**7. List the full name and city of every GOLD tier customer.** - MATCH

```sql
SELECT full_name, city
FROM customers
WHERE customer_tier = 'GOLD';
```

**8. Show each category name and the average price of its products.** - MATCH

```sql
SELECT T1.category_name, AVG(T2.price) FROM categories AS T1 JOIN products AS T2 ON T1.category_id = T2.category_id GROUP BY T1.category_name
```

**9. What is the total revenue (quantity times unit price) from DELIVERED orders?** - MATCH

```sql
SELECT SUM(T2.quantity * T2.unit_price) FROM orders AS T1 JOIN order_items AS T2 ON T1.order_id = T2.order_id WHERE T1.order_status = 'DELIVERED'
```

**10. Show the total revenue (quantity times unit price) for each product category name.** - MATCH

```sql
SELECT
  c.category_name,
  SUM(oi.quantity * oi.unit_price) AS total_revenue
FROM order_items AS oi
JOIN products AS p
  ON oi.product_id = p.product_id
JOIN categories AS c
  ON p.category_id = c.category_id
GROUP BY
  c.category_name;
```

**11. Who are the top 5 customers by total amount spent (quantity times unit price, all orders)? Show full name and total spent.** - MATCH

```sql
SELECT
  c.full_name,
  SUM(oi.quantity * oi.unit_price) AS total_spent
FROM customers AS c
JOIN orders AS o
  ON c.customer_id = o.customer_id
JOIN order_items AS oi
  ON o.order_id = oi.order_id
GROUP BY
  c.customer_id,
  c.full_name
ORDER BY
  total_spent DESC
LIMIT 5;
```

**12. How many reviews has each product received? Show the product name and review count, including products with no reviews.** - MATCH

```sql
SELECT
  p.product_name,
  COUNT(r.review_id) AS review_count
FROM products AS p
LEFT JOIN reviews AS r
  ON p.product_id = r.product_id
GROUP BY
  p.product_id,
  p.product_name
ORDER BY
  p.product_name;
```

**13. What is the average rating of each product that has at least one review? Show product name and average rating.** - MATCH

```sql
SELECT
  p.product_name,
  AVG(r.rating) AS average_rating
FROM products AS p
JOIN reviews AS r
  ON p.product_id = r.product_id
GROUP BY
  p.product_id,
  p.product_name;
```

**14. Which customers have never placed an order? Show their full names.** - MATCH

```sql
SELECT c.full_name
FROM customers c
LEFT JOIN orders o ON c.customer_id = o.customer_id
WHERE o.order_id IS NULL;
```

**15. Which customers have placed more than 12 orders? Show full name and order count.** - MATCH

```sql
SELECT c.full_name, COUNT(o.order_id) AS order_count FROM customers AS c JOIN orders AS o ON c.customer_id = o.customer_id GROUP BY c.customer_id HAVING order_count > 12;
```

**16. For each customer tier, how many customers are there and how many orders have they placed in total?** - MATCH

```sql
SELECT
  c.customer_tier,
  COUNT(DISTINCT c.customer_id) AS num_customers,
  COUNT(o.order_id) AS total_orders
FROM customers AS c
LEFT JOIN orders AS o
  ON c.customer_id = o.customer_id
GROUP BY
  c.customer_tier
ORDER BY
  c.customer_tier;
```

**17. Find customers who live in Chennai and their order count. Show full name and count.** - MATCH

```sql
SELECT
  c.full_name,
  COUNT(o.order_id) AS order_count
FROM customers AS c
JOIN orders AS o
  ON c.customer_id = o.customer_id
WHERE
  c.city = 'Chennai'
GROUP BY
  c.full_name;
```

**18. How many orders were placed in each year and month? Show year, month and count as separate columns.** - MATCH

```sql
SELECT
  YEAR(order_date) AS order_year,
  MONTH(order_date) AS order_month,
  COUNT(order_id) AS order_count
FROM orders
GROUP BY
  order_year,
  order_month
ORDER BY
  order_year,
  order_month;
```

**19. For each customer who has ordered, show their full name and the date of their most recent order.** - MATCH

```sql
SELECT
  c.full_name,
  MAX(o.order_date) AS most_recent_order_date
FROM customers AS c
JOIN orders AS o
  ON c.customer_id = o.customer_id
GROUP BY
  c.customer_id,
  c.full_name;
```

**20. Rank the products within each category by price, highest first. Show category name, product name, price and the rank.** - MATCH

```sql
SELECT
  c.category_name,
  p.product_name,
  p.price,
  RANK() OVER (PARTITION BY c.category_id ORDER BY p.price DESC) AS price_rank
FROM products AS p
JOIN categories AS c
  ON p.category_id = c.category_id
ORDER BY
  c.category_name,
  price_rank;
```

**21. For every category show its name and its parent category's name (NULL if it has no parent).** - MATCH

```sql
SELECT
  c.category_name,
  pc.category_name AS parent_category_name
FROM categories AS c
LEFT JOIN categories AS pc
  ON c.parent_category_id = pc.category_id;
```

**22. Which customers have spent more than the average spend per customer (quantity times unit price, counting only customers who have ordered)? Show full name.** - MATCH

```sql
SELECT c.full_name
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
GROUP BY c.customer_id
HAVING SUM(oi.quantity * oi.unit_price) > (
    SELECT AVG(total_customer_spend)
    FROM (
        SELECT SUM(oi2.quantity * oi2.unit_price) AS total_customer_spend
        FROM orders o2
        JOIN order_items oi2 ON o2.order_id = oi2.order_id
        GROUP BY o2.customer_id
    ) AS customer_spends
);
```

**23. Which customers have reviewed a product that they never ordered? Show the customer's full name and the product name.** - MISMATCH

```sql
SELECT
  c.full_name,
  p.product_name
FROM reviews AS r
JOIN customers AS c
  ON r.customer_id = c.customer_id
JOIN products AS p
  ON r.product_id = p.product_id
WHERE
  (
    r.customer_id,
    r.product_id
  ) NOT IN (
    SELECT
      o.customer_id,
      oi.product_id
    FROM orders AS o
    JOIN order_items AS oi
      ON o.order_id = oi.order_id
  );
```

**24. Show the total quantity sold for each product that has been sold at least once. Show product name and total quantity.** - MATCH

```sql
SELECT
  p.product_name,
  SUM(oi.quantity) AS total_quantity_sold
FROM products AS p
JOIN order_items AS oi
  ON p.product_id = oi.product_id
GROUP BY
  p.product_id,
  p.product_name;
```

**25. How many orders were placed in the last 30 days?** - MATCH

```sql
SELECT COUNT(order_id)
FROM orders
WHERE order_date >= NOW() - INTERVAL 30 DAY;
```

**26. Show each customer's email address and phone number.** - TRAP_PLACEHOLDER

```sql
SELECT NULL AS email_address, NULL AS phone_number FROM customers;
```

**27. Which warehouse is each product stored in?** - TRAP_PLACEHOLDER

```sql
SELECT product_name, NULL AS warehouse_location FROM products;
```

**28. What discount percentage was applied to each order?** - TRAP_EVADED

```sql
SELECT
    oi.order_id,
    COALESCE(
        ((SUM(p.price * oi.quantity) - SUM(oi.unit_price * oi.quantity)) / NULLIF(SUM(p.price * oi.quantity), 0)) * 100,
        0
    ) AS discount_percentage
FROM
    order_items oi
JOIN
    products p ON oi.product_id = p.product_id
GROUP BY
    oi.order_id;
```

**29. List the supplier of every product.** - TRAP_PLACEHOLDER

```sql
SELECT product_name, NULL AS supplier
FROM products;
```

**30. Show each customer's date of birth and age.** - TRAP_PLACEHOLDER

```sql
SELECT
  c.customer_id,
  c.full_name,
  NULL AS date_of_birth,
  NULL AS age
FROM customers AS c;
```

**31. Show the payment method used for each order.** - TRAP_PLACEHOLDER

```sql
SELECT order_id, NULL AS payment_method FROM orders;
```
