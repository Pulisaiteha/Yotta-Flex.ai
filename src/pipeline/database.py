import logging
import sqlite3
from pathlib import Path


logger = logging.getLogger(__name__)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_key INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key INTEGER PRIMARY KEY,
    full_date TEXT NOT NULL UNIQUE,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    day INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_order (
    order_key INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL UNIQUE,
    customer_key INTEGER NOT NULL,
    date_key INTEGER NOT NULL,
    amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
    source_run_id TEXT NOT NULL,
    FOREIGN KEY(customer_key) REFERENCES dim_customer(customer_key),
    FOREIGN KEY(date_key) REFERENCES dim_date(date_key)
);

-- History of customer attribute changes, so upserts never silently lose old values.
CREATE TABLE IF NOT EXISTS customer_change_log (
    change_key INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    old_name TEXT NOT NULL,
    old_city TEXT NOT NULL,
    new_name TEXT NOT NULL,
    new_city TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rejected_records (
    rejection_key INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    entity TEXT NOT NULL CHECK(entity IN ('customer', 'order')),
    source_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    record_id TEXT,
    reason TEXT NOT NULL,
    raw_record TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    customers_file TEXT,
    orders_file TEXT,
    customer_rows_read INTEGER NOT NULL DEFAULT 0,
    order_rows_read INTEGER NOT NULL DEFAULT 0,
    accepted_orders INTEGER NOT NULL DEFAULT 0,
    rejected_orders INTEGER NOT NULL DEFAULT 0,
    rejected_customers INTEGER NOT NULL DEFAULT 0,
    skipped_orders INTEGER NOT NULL DEFAULT 0,
    late_arriving_orders INTEGER NOT NULL DEFAULT 0,
    accepted_amount_paise INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL,
    message TEXT
);

CREATE INDEX IF NOT EXISTS idx_fact_order_customer ON fact_order(customer_key);
CREATE INDEX IF NOT EXISTS idx_fact_order_date ON fact_order(date_key);
CREATE INDEX IF NOT EXISTS idx_fact_order_run ON fact_order(source_run_id);
CREATE INDEX IF NOT EXISTS idx_rejections_run ON rejected_records(run_id);
"""


def connect(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    logger.debug("opened SQLite connection to %s", path)
    return conn


def initialize(conn):
    conn.executescript(SCHEMA)
    logger.debug("schema ensured (CREATE TABLE IF NOT EXISTS for all tables)")


def clear_all_tables(conn):
    """Demo helper: empty every warehouse table (used by POST /demo/reset)."""
    with conn:
        for table in ("fact_order", "dim_date", "customer_change_log", "dim_customer",
                      "rejected_records", "pipeline_runs"):
            conn.execute(f"DELETE FROM {table}")
    logger.info("all warehouse tables emptied")


def upsert_customer(conn, customer, run_id, changed_at):
    existing = conn.execute(
        "SELECT name, city FROM dim_customer WHERE customer_id=?",
        (customer["customer_id"],),
    ).fetchone()
    if existing and (existing["name"], existing["city"]) != (customer["name"], customer["city"]):
        conn.execute(
            """INSERT INTO customer_change_log
               (customer_id, run_id, old_name, old_city, new_name, new_city, changed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (customer["customer_id"], run_id, existing["name"], existing["city"],
             customer["name"], customer["city"], changed_at),
        )
        logger.info("customer %s changed (%s, %s) -> (%s, %s); old values saved to customer_change_log",
                    customer["customer_id"], existing["name"], existing["city"],
                    customer["name"], customer["city"])
    conn.execute(
        """INSERT INTO dim_customer(customer_id, name, city)
           VALUES (?, ?, ?)
           ON CONFLICT(customer_id) DO UPDATE SET
             name=excluded.name, city=excluded.city""",
        (customer["customer_id"], customer["name"], customer["city"]),
    )
    row = conn.execute(
        "SELECT customer_key FROM dim_customer WHERE customer_id=?",
        (customer["customer_id"],),
    ).fetchone()
    if not existing:
        action = "inserted"
    elif (existing["name"], existing["city"]) != (customer["name"], customer["city"]):
        action = "updated"
    else:
        action = "unchanged"
    logger.debug("customer %s %s in dim_customer (customer_key=%s)",
                 customer["customer_id"], action, row["customer_key"])
    return row["customer_key"]


def ensure_date(conn, iso_date):
    year, month, day = map(int, iso_date.split("-"))
    date_key = int(iso_date.replace("-", ""))
    conn.execute(
        """INSERT OR IGNORE INTO dim_date(date_key, full_date, year, month, day)
           VALUES (?, ?, ?, ?, ?)""",
        (date_key, iso_date, year, month, day),
    )
    return date_key
