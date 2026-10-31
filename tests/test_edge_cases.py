"""Required edge cases from the challenge: empty batch, nulls, duplicates, schema change,
partial failure + retry, late-arriving data, invalid reference, rerun, domain rules."""
import json
import sqlite3
from datetime import date

import pytest

from src.pipeline import runner
from src.pipeline.runner import run_pipeline
from src.pipeline.validation import SchemaError
from src.pipeline import queries
from src.pipeline.database import connect


def order(order_id, customer_id="C101", order_date="2026-10-28", amount=100):
    return {"order_id": order_id, "customer_id": customer_id,
            "order_date": order_date, "amount": amount}


def fetch(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_empty_batch_succeeds_with_zero_rows(make_inputs):
    result = run_pipeline(*make_inputs([]))
    assert result["status"] == "SUCCESS"
    assert result["order_rows_read"] == 0
    assert result["accepted_orders"] == 0
    assert result["order_reconciliation_passed"] is True


def test_null_and_blank_fields_are_rejected(make_inputs):
    customers, orders, db = make_inputs([
        order(None),
        order("O2", customer_id=None),
        order("O3", order_date="  "),
        order("O4", amount=None),
    ])
    result = run_pipeline(customers, orders, db)
    assert result["accepted_orders"] == 0
    assert result["rejected_orders"] == 4
    reasons = [r[0] for r in fetch(db, "SELECT reason FROM rejected_records ORDER BY rejection_key")]
    assert reasons == ["missing_order_id", "missing_customer_id",
                       "missing_order_date", "missing_amount"]
    # Missing ID is stored as NULL, not the text "None".
    assert fetch(db, "SELECT record_id FROM rejected_records WHERE source_row_number=1")[0][0] is None


def test_duplicate_order_in_batch_keeps_first(make_inputs):
    customers, orders, db = make_inputs([order("O1", amount=100), order("O1", amount=999)])
    result = run_pipeline(customers, orders, db)
    assert result["accepted_orders"] == 1
    assert result["accepted_amount_inr"] == 100.0
    assert fetch(db, "SELECT reason, source_row_number FROM rejected_records") == [
        ("duplicate_order_id_in_batch", 2)]


def test_duplicate_and_invalid_customers_are_rejected(make_inputs):
    csv_text = "customer_id,name,city\nC101,Ravi,Hyderabad\nC101,Ravi K,Pune\nC103,,Chennai\n"
    result = run_pipeline(*make_inputs([order("O1")], customers_csv=csv_text))
    assert result["rejected_customers"] == 2
    assert result["status"] == "COMPLETED_WITH_REJECTS"


def test_invalid_reference_key_is_rejected(make_inputs):
    customers, orders, db = make_inputs([order("O1", customer_id="C404")])
    run_pipeline(customers, orders, db)
    assert fetch(db, "SELECT reason, record_id FROM rejected_records") == [
        ("unknown_customer_id", "O1")]


def test_missing_required_column_fails_run_and_is_recorded(make_inputs):
    """Unexpected schema change: a required CSV column disappears."""
    customers, orders, db = make_inputs([order("O1")], customers_csv="customer_id,full_name,city\nC101,Ravi,Hyd\n")
    with pytest.raises(SchemaError, match="name"):
        run_pipeline(customers, orders, db)
    runs = fetch(db, "SELECT status, message FROM pipeline_runs")
    assert runs[0][0] == "FAILED"
    assert "missing required columns" in runs[0][1]
    assert fetch(db, "SELECT COUNT(*) FROM fact_order")[0][0] == 0


def test_extra_columns_are_reported_not_fatal(make_inputs):
    """Unexpected schema change: new columns appear upstream."""
    csv_text = "customer_id,name,city,email\nC101,Ravi,Hyderabad,r@x.com\n"
    customers, orders, db = make_inputs([{**order("O1"), "channel": "web"}], customers_csv=csv_text)
    result = run_pipeline(customers, orders, db)
    assert result["accepted_orders"] == 1
    assert result["unexpected_columns"] == {"customers": ["email"], "orders": ["channel"]}


def test_malformed_json_run_is_recorded_as_failed(make_inputs):
    customers, orders, db = make_inputs("[{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        run_pipeline(customers, orders, db)
    assert fetch(db, "SELECT status FROM pipeline_runs") == [("FAILED",)]


def test_partial_failure_rolls_back_then_retry_succeeds(make_inputs, monkeypatch):
    customers, orders, db = make_inputs([order("O1"), order("O2"), order("O3")])
    real_ensure_date = runner.ensure_date
    calls = {"n": 0}

    def crash_on_second_order(conn, iso_date):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash mid-load")
        return real_ensure_date(conn, iso_date)

    monkeypatch.setattr(runner, "ensure_date", crash_on_second_order)
    with pytest.raises(RuntimeError):
        run_pipeline(customers, orders, db)
    # Transaction rolled back: O1 was inserted before the crash but is not persisted.
    assert fetch(db, "SELECT COUNT(*) FROM fact_order")[0][0] == 0
    assert fetch(db, "SELECT status FROM pipeline_runs") == [("FAILED",)]

    monkeypatch.setattr(runner, "ensure_date", real_ensure_date)
    retry = run_pipeline(customers, orders, db)
    assert retry["status"] == "SUCCESS"
    assert retry["accepted_orders"] == 3


def test_late_arriving_orders_load_and_are_flagged(make_inputs):
    customers, orders, db = make_inputs([order("O1", order_date="2026-10-28")])
    run_pipeline(customers, orders, db)
    late = run_pipeline(*make_inputs([order("O0", order_date="2026-10-01"),
                                      order("O9", order_date="2026-10-29")]))
    assert late["accepted_orders"] == 2
    assert late["late_arriving_orders"] == 1
    conn = connect(db)
    days = [r["full_date"] for r in queries.revenue_by_day(conn)]
    conn.close()
    assert days == ["2026-10-01", "2026-10-28", "2026-10-29"]


def test_domain_rule_sub_paise_amount_rejected_not_crash(make_inputs):
    """0.001 INR would round to 0 paise and break the CHECK constraint."""
    customers, orders, db = make_inputs([order("O1", amount="0.001"), order("O2", amount=0.01)])
    result = run_pipeline(customers, orders, db)
    assert result["accepted_orders"] == 1
    assert fetch(db, "SELECT reason FROM rejected_records") == [("amount_more_than_2_decimals",)]


def test_domain_rule_future_order_date_rejected(make_inputs):
    customers, orders, db = make_inputs([order("O1", order_date="2026-11-01")])
    result = run_pipeline(customers, orders, db, today=date(2026, 10, 31))
    assert result["accepted_orders"] == 0
    assert fetch(db, "SELECT reason FROM rejected_records") == [("order_date_in_future",)]


def test_customer_changes_keep_history(make_inputs):
    customers, orders, db = make_inputs([order("O1")])
    run_pipeline(customers, orders, db)
    moved = "customer_id,name,city\nC101,Ravi,Pune\nC102,Priya,Bengaluru\n"
    run_pipeline(*make_inputs([], customers_csv=moved))
    assert fetch(db, "SELECT city FROM dim_customer WHERE customer_id='C101'") == [("Pune",)]
    assert fetch(db, "SELECT old_city, new_city FROM customer_change_log") == [("Hyderabad", "Pune")]


def test_reconciliation_counts_and_amounts_match(sample_files):
    result = run_pipeline(*sample_files)
    conn = connect(sample_files[2])
    report = queries.reconcile_run(conn, result["run_id"])
    conn.close()
    assert report["passed"] is True
    assert report["fact_amount_loaded_inr"] == 2000.0


def test_generated_dataset_matches_expected_labels(tmp_path):
    """The generator labels every invalid row; the pipeline must agree exactly."""
    from scripts.generate_data import generate
    expected = generate(customers=20, orders=2000, invalid_rate=0.1, seed=7, out=tmp_path)
    result = run_pipeline(tmp_path / "input" / "customers.csv",
                          tmp_path / "input" / "orders.json", tmp_path / "pipeline.db")
    assert result["accepted_orders"] == expected["expected_accepted_orders"]
    assert result["rejected_orders"] == expected["expected_rejected_orders"]
    assert result["rejected_customers"] == expected["expected_rejected_customers"]
    actual = dict(fetch(tmp_path / "pipeline.db",
                        "SELECT reason, COUNT(*) FROM rejected_records WHERE entity='order' GROUP BY reason"))
    assert actual == expected["expected_rejections_by_reason"]


def test_bundled_sample_matches_expected_file(tmp_path):
    expected = json.loads(open("data/expected/sample_expected.json", encoding="utf-8").read())
    db = tmp_path / "sample.db"
    result = run_pipeline("data/input/customers.csv", "data/input/orders.json", db,
                          today=date(2026, 10, 31))
    assert result["accepted_orders"] == expected["accepted_orders"]
    assert result["rejected_orders"] == expected["rejected_orders"]
    assert result["accepted_amount_inr"] == expected["accepted_amount_inr"]
    actual = {str(row): reason for row, reason in fetch(
        db, "SELECT source_row_number, reason FROM rejected_records WHERE entity='order'")}
    assert actual == expected["expected_rejections"]
