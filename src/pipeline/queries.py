"""Read-only analytical queries used by the API and the report script."""
import logging


logger = logging.getLogger(__name__)


# stacklevel=2 makes the log line show the calling query function (e.g. revenue_by_day), not _rows.
def _rows(conn, sql, params=()):
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    logger.info("SELECT params=%s -> %d rows", list(params), len(rows), stacklevel=2)
    return rows


def _one(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    logger.info("SELECT params=%s -> %s", list(params), "1 row" if row else "no row", stacklevel=2)
    return dict(row) if row else None


# ---------- pipeline runs ----------

def list_runs(conn, limit=20, status=None):
    sql = "SELECT * FROM pipeline_runs"
    params = []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    return _rows(conn, sql, params)


def get_run(conn, run_id):
    return _one(conn, "SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,))


def list_rejections(conn, run_id, entity=None, limit=100, offset=0):
    sql = """SELECT rejection_key, run_id, entity, source_name, source_row_number,
                    record_id, reason, raw_record
             FROM rejected_records WHERE run_id=?"""
    params = [run_id]
    if entity:
        sql += " AND entity=?"
        params.append(entity)
    sql += " ORDER BY rejection_key LIMIT ? OFFSET ?"
    params += [limit, offset]
    return _rows(conn, sql, params)


def reconcile_run(conn, run_id):
    """Compare what the run says it did with what is actually stored in the tables."""
    run = get_run(conn, run_id)
    if run is None:
        return None
    loaded = _one(conn, """SELECT COUNT(*) AS orders, COALESCE(SUM(amount_paise), 0) AS amount
                           FROM fact_order WHERE source_run_id=?""", (run_id,))
    rejected = _one(conn, """SELECT COUNT(*) AS n FROM rejected_records
                             WHERE run_id=? AND entity='order'""", (run_id,))["n"]
    accounted = run["accepted_orders"] + run["rejected_orders"] + run["skipped_orders"]
    checks = {
        "input_equals_accepted_plus_rejected_plus_skipped": run["order_rows_read"] == accounted,
        "accepted_count_matches_fact_table": run["accepted_orders"] == loaded["orders"],
        "rejected_count_matches_reject_table": run["rejected_orders"] == rejected,
        "accepted_amount_matches_fact_table": run["accepted_amount_paise"] == loaded["amount"],
    }
    logger.info("reconciliation checks for %s: %s", run_id, checks)
    return {
        "run_id": run_id,
        "status": run["status"],
        "order_rows_read": run["order_rows_read"],
        "accepted_orders": run["accepted_orders"],
        "rejected_orders": run["rejected_orders"],
        "skipped_orders": run["skipped_orders"],
        "fact_rows_loaded_by_run": loaded["orders"],
        "reject_rows_recorded": rejected,
        "accepted_amount_inr": round(run["accepted_amount_paise"] / 100, 2),
        "fact_amount_loaded_inr": round(loaded["amount"] / 100, 2),
        "checks": checks,
        "passed": all(checks.values()),
    }


# ---------- analytics ----------

def revenue_summary(conn):
    return _one(conn, """
        SELECT ROUND(COALESCE(SUM(amount_paise), 0) / 100.0, 2) AS total_revenue_inr,
               COUNT(*) AS order_count,
               COUNT(DISTINCT customer_key) AS paying_customers,
               ROUND(COALESCE(AVG(amount_paise), 0) / 100.0, 2) AS avg_order_value_inr
        FROM fact_order""")


def revenue_by_customer(conn, limit=50):
    return _rows(conn, """
        SELECT c.customer_id, c.name, c.city,
               ROUND(SUM(f.amount_paise) / 100.0, 2) AS revenue_inr,
               COUNT(*) AS order_count
        FROM fact_order f
        JOIN dim_customer c ON c.customer_key = f.customer_key
        GROUP BY c.customer_key
        ORDER BY SUM(f.amount_paise) DESC
        LIMIT ?""", (limit,))


def revenue_by_city(conn):
    return _rows(conn, """
        SELECT c.city,
               ROUND(SUM(f.amount_paise) / 100.0, 2) AS revenue_inr,
               COUNT(*) AS order_count
        FROM fact_order f
        JOIN dim_customer c ON c.customer_key = f.customer_key
        GROUP BY c.city
        ORDER BY SUM(f.amount_paise) DESC""")


def revenue_by_day(conn, start_date=None, end_date=None):
    sql = """SELECT d.full_date,
                    ROUND(SUM(f.amount_paise) / 100.0, 2) AS revenue_inr,
                    COUNT(*) AS order_count
             FROM fact_order f
             JOIN dim_date d ON d.date_key = f.date_key
             WHERE 1=1"""
    params = []
    if start_date:
        sql += " AND d.full_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND d.full_date <= ?"
        params.append(end_date)
    sql += " GROUP BY d.full_date ORDER BY d.full_date"
    return _rows(conn, sql, params)


def rejection_reasons(conn, run_id=None):
    sql = "SELECT entity, reason, COUNT(*) AS records FROM rejected_records"
    params = []
    if run_id:
        sql += " WHERE run_id=?"
        params.append(run_id)
    sql += " GROUP BY entity, reason ORDER BY records DESC"
    return _rows(conn, sql, params)


# ---------- dimension / fact lookups ----------

def list_customers(conn, city=None, limit=100, offset=0):
    sql = "SELECT customer_id, name, city FROM dim_customer"
    params = []
    if city:
        sql += " WHERE city=?"
        params.append(city)
    sql += " ORDER BY customer_id LIMIT ? OFFSET ?"
    params += [limit, offset]
    return _rows(conn, sql, params)


def get_customer(conn, customer_id):
    customer = _one(conn, """
        SELECT c.customer_id, c.name, c.city,
               COUNT(f.order_key) AS order_count,
               ROUND(COALESCE(SUM(f.amount_paise), 0) / 100.0, 2) AS revenue_inr
        FROM dim_customer c
        LEFT JOIN fact_order f ON f.customer_key = c.customer_key
        WHERE c.customer_id=?
        GROUP BY c.customer_key""", (customer_id,))
    if customer:
        customer["change_history"] = _rows(conn, """
            SELECT run_id, old_name, old_city, new_name, new_city, changed_at
            FROM customer_change_log WHERE customer_id=? ORDER BY change_key""", (customer_id,))
    return customer


def list_orders(conn, customer_id=None, start_date=None, end_date=None, limit=100, offset=0):
    sql = """SELECT f.order_id, c.customer_id, d.full_date AS order_date,
                    ROUND(f.amount_paise / 100.0, 2) AS amount_inr, f.source_run_id
             FROM fact_order f
             JOIN dim_customer c ON c.customer_key = f.customer_key
             JOIN dim_date d ON d.date_key = f.date_key
             WHERE 1=1"""
    params = []
    if customer_id:
        sql += " AND c.customer_id=?"
        params.append(customer_id)
    if start_date:
        sql += " AND d.full_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND d.full_date <= ?"
        params.append(end_date)
    sql += " ORDER BY d.full_date, f.order_id LIMIT ? OFFSET ?"
    params += [limit, offset]
    return _rows(conn, sql, params)
