-- ============================================================
-- LLM-SQL GUARD — PRACTICE DATABASE SCHEMA
-- Domain: E-commerce (customers, orders, products, categories)
-- MySQL 8.0+ / MariaDB 10.5+
-- This is the schema the LLM will be asked to write SQL against.
-- Its INFORMATION_SCHEMA entries are what the validator checks against.
-- ============================================================

DROP DATABASE IF EXISTS llm_sql_guard;
CREATE DATABASE llm_sql_guard;
USE llm_sql_guard;

CREATE TABLE customers (
    customer_id     INT AUTO_INCREMENT PRIMARY KEY,
    full_name       VARCHAR(100) NOT NULL,
    city            VARCHAR(50),
    signup_date     DATE NOT NULL,
    customer_tier   ENUM('BRONZE','SILVER','GOLD') DEFAULT 'BRONZE'
);

-- Self-referencing hierarchy: gives the recursive-CTE join-path
-- check something real to traverse (Electronics -> Mobiles -> ...).
CREATE TABLE categories (
    category_id        INT AUTO_INCREMENT PRIMARY KEY,
    category_name      VARCHAR(60) NOT NULL,
    parent_category_id INT NULL,
    CONSTRAINT fk_cat_parent FOREIGN KEY (parent_category_id)
        REFERENCES categories(category_id) ON DELETE SET NULL
);

CREATE TABLE products (
    product_id      INT AUTO_INCREMENT PRIMARY KEY,
    product_name    VARCHAR(100) NOT NULL,
    category_id     INT NOT NULL,
    price           DECIMAL(10,2) NOT NULL,
    stock_quantity  INT NOT NULL DEFAULT 0,
    CONSTRAINT fk_prod_cat FOREIGN KEY (category_id)
        REFERENCES categories(category_id) ON DELETE CASCADE
);

CREATE TABLE orders (
    order_id        INT AUTO_INCREMENT PRIMARY KEY,
    customer_id     INT NOT NULL,
    order_date      DATE NOT NULL,
    order_status    ENUM('PLACED','SHIPPED','DELIVERED','CANCELLED') DEFAULT 'PLACED',
    CONSTRAINT fk_order_cust FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id) ON DELETE CASCADE
);

CREATE TABLE order_items (
    order_item_id   INT AUTO_INCREMENT PRIMARY KEY,
    order_id        INT NOT NULL,
    product_id      INT NOT NULL,
    quantity        INT NOT NULL,
    unit_price      DECIMAL(10,2) NOT NULL,
    CONSTRAINT fk_oi_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    CONSTRAINT fk_oi_product FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
);

CREATE TABLE reviews (
    review_id       INT AUTO_INCREMENT PRIMARY KEY,
    product_id      INT NOT NULL,
    customer_id     INT NOT NULL,
    rating          TINYINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_date     DATE NOT NULL,
    CONSTRAINT fk_rev_product FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE,
    CONSTRAINT fk_rev_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- VALIDATOR SUPPORT TABLES (these belong to the project itself,
-- not the "target" database being protected)
-- ------------------------------------------------------------

-- Every NL question + generated SQL + verdict gets logged here.
CREATE TABLE hallucination_log (
    log_id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    nl_question     TEXT NOT NULL,
    generated_sql   TEXT NOT NULL,
    llm_provider    VARCHAR(50) NOT NULL,
    verdict         ENUM('VALID','HALLUCINATED') NOT NULL,
    failure_type    ENUM('UNKNOWN_TABLE','UNKNOWN_COLUMN','INVALID_JOIN_PATH','NONE') DEFAULT 'NONE',
    failure_detail  VARCHAR(255),
    checked_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_halluc_checked_at ON hallucination_log(checked_at);
CREATE INDEX idx_halluc_verdict ON hallucination_log(verdict);
