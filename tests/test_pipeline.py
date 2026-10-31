import sqlite3
from src.pipeline.runner import run_pipeline


def test_pipeline_accepts_two_rejects_one(sample_files):
    customers, orders, db = sample_files
    result = run_pipeline(customers, orders, db)
    assert result["accepted_orders"] == 2
    assert result["rejected_records_including_customer_errors"] == 1
    assert result["order_reconciliation_passed"] is True
    assert result["warehouse_total_revenue_inr"] == 2000.0

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM fact_order").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM rejected_records").fetchone()[0] == 1
    conn.close()


def test_rerun_does_not_duplicate_orders(sample_files):
    customers, orders, db = sample_files
    first = run_pipeline(customers, orders, db)
    second = run_pipeline(customers, orders, db)
    assert first["warehouse_total_revenue_inr"] == second["warehouse_total_revenue_inr"] == 2000.0
    assert second["accepted_orders"] == 0
    assert second["skipped_orders_already_loaded"] == 2

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM fact_order").fetchone()[0] == 2
    conn.close()
