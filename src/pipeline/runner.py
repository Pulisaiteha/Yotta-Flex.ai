import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .database import connect, initialize, upsert_customer, ensure_date
from .readers import read_customers, read_orders
from .transform import clean_customer, clean_order
from .validation import ORDER_FIELDS, validate_customer, validate_order
from .metrics import summarize_counts
from ..logging_config import setup_logging


logger = logging.getLogger(__name__)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def clean_id(value):
    """Return a stripped ID string, or None when missing/blank (avoids storing the text 'None')."""
    if value is None:
        return None
    return str(value).strip() or None


def record_rejection(conn, run_id, entity, source, row_number, record_id, reason, raw):
    conn.execute(
        """INSERT INTO rejected_records
           (run_id, entity, source_name, source_row_number, record_id, reason, raw_record)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, entity, source, row_number, record_id, reason, json.dumps(raw, default=str)),
    )


def record_failed_run(conn, run_id, started_at, started_clock, customers_file, orders_file, message):
    # Runs in its own transaction, after the main load transaction was rolled back.
    with conn:
        conn.execute(
            """INSERT INTO pipeline_runs
               (run_id, started_at, finished_at, status, customers_file, orders_file,
                duration_seconds, message)
               VALUES (?, ?, ?, 'FAILED', ?, ?, ?, ?)""",
            (run_id, started_at, now_utc(), customers_file, orders_file,
             time.perf_counter() - started_clock, message),
        )
    logger.info("FAILED run %s recorded in pipeline_runs (separate transaction)", run_id)


def run_pipeline(customers_path, orders_path, db_path, today=None):
    started_clock = time.perf_counter()
    started_at = now_utc()
    run_id = "run_" + uuid.uuid4().hex[:12]
    customers_file = Path(customers_path).name
    orders_file = Path(orders_path).name
    logger.info("run %s started customers=%s orders=%s", run_id, customers_path, orders_path)

    conn = connect(db_path)
    initialize(conn)
    logger.info("step 1/7 database ready: %s", db_path)

    accepted = rejected_orders = skipped = late_arriving = 0
    accepted_amount_paise = 0
    seen_order_ids = set()
    customer_errors = []

    try:
        customers, extra_customer_columns = read_customers(customers_path)
        orders = read_orders(orders_path)
        logger.info("step 2/7 files read: %d customer rows, %d order rows", len(customers), len(orders))

        # Unexpected schema change: extra columns are ignored but reported, not silently dropped.
        unexpected_order_fields = sorted({k for row in orders for k in row} - ORDER_FIELDS)
        if extra_customer_columns:
            logger.warning("run %s customer file has unexpected columns: %s",
                           run_id, extra_customer_columns)
        if unexpected_order_fields:
            logger.warning("run %s order file has unexpected fields: %s",
                           run_id, unexpected_order_fields)

        with conn:  # BEGIN transaction: commits on success, rolls back on any exception
            logger.info("transaction started")
            valid_customers = {}
            for idx, raw in enumerate(customers, start=2):
                errors = validate_customer(raw)
                if errors:
                    customer_errors.append((idx, raw, errors))
                    continue
                customer = clean_customer(raw)
                cid = customer["customer_id"]
                if cid in valid_customers:
                    customer_errors.append((idx, raw, ["duplicate_customer_id"]))
                    continue
                valid_customers[cid] = customer
            logger.info("step 3/7 customers validated (validate_customer + clean_customer): "
                        "%d valid, %d rejected", len(valid_customers), len(customer_errors))

            # Existing customer records are retained; changed attributes are logged before update.
            for customer in valid_customers.values():
                upsert_customer(conn, customer, run_id, started_at)
            logger.info("step 4/7 upserted %d customers into dim_customer", len(valid_customers))

            known_ids = set(valid_customers)
            # Also allow references to customers already present in the warehouse.
            known_ids.update(
                row["customer_id"]
                for row in conn.execute("SELECT customer_id FROM dim_customer").fetchall()
            )

            # Orders dated before the newest already-loaded order are late-arriving.
            max_loaded_date_key = conn.execute(
                "SELECT MAX(date_key) AS d FROM fact_order"
            ).fetchone()["d"]

            logger.info("step 5/7 processing %d orders (newest date_key already loaded=%s)",
                        len(orders), max_loaded_date_key)
            for idx, raw in enumerate(orders, start=1):
                order_id = clean_id(raw.get("order_id"))

                if order_id and order_id in seen_order_ids:
                    record_rejection(conn, run_id, "order", orders_file, idx, order_id,
                                     "duplicate_order_id_in_batch", raw)
                    logger.debug("order row %d %s -> REJECTED duplicate_order_id_in_batch", idx, order_id)
                    rejected_orders += 1
                    continue
                if order_id:
                    seen_order_ids.add(order_id)

                errors = validate_order(raw, known_ids, today)
                if errors:
                    record_rejection(conn, run_id, "order", orders_file, idx, order_id,
                                     ";".join(errors), raw)
                    logger.debug("order row %d %s -> validate_order() -> REJECTED %s", idx, order_id, errors)
                    rejected_orders += 1
                    continue

                order = clean_order(raw)
                exists = conn.execute(
                    "SELECT 1 FROM fact_order WHERE order_id=?",
                    (order["order_id"],),
                ).fetchone()
                if exists:
                    # An order already loaded by an earlier run is not counted twice.
                    logger.debug("order row %d %s -> SKIPPED already in fact_order (rerun)", idx, order_id)
                    skipped += 1
                    continue

                customer_row = conn.execute(
                    "SELECT customer_key FROM dim_customer WHERE customer_id=?",
                    (order["customer_id"],),
                ).fetchone()
                customer_key = customer_row["customer_key"]
                date_key = ensure_date(conn, order["order_date"])

                conn.execute(
                    """INSERT INTO fact_order
                       (order_id, customer_key, date_key, amount_paise, source_run_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (order["order_id"], customer_key, date_key,
                     order["amount_paise"], run_id),
                )
                accepted += 1
                accepted_amount_paise += order["amount_paise"]
                is_late = bool(max_loaded_date_key and date_key < max_loaded_date_key)
                if is_late:
                    late_arriving += 1
                logger.debug("order row %d %s -> validate_order() OK -> clean_order() amount=%s -> "
                             "%d paise -> INSERT fact_order (customer_key=%s, date_key=%s)%s",
                             idx, order_id, raw["amount"], order["amount_paise"], customer_key,
                             date_key, " LATE-ARRIVING" if is_late else "")

            for idx, raw, errors in customer_errors:
                record_rejection(conn, run_id, "customer", customers_file, idx,
                                 clean_id(raw.get("customer_id")),
                                 ";".join(errors), raw)

            logger.info("step 6/7 orders done: accepted=%d rejected=%d skipped=%d late=%d; "
                        "%d customer rejects written to rejected_records",
                        accepted, rejected_orders, skipped, late_arriving, len(customer_errors))

            # Customer rejects are separately recorded; order reconciliation concerns order rows only.
            counts = summarize_counts(len(orders), accepted, rejected_orders, skipped)
            order_reconciliation = counts["reconciliation_passed"]
            duration = time.perf_counter() - started_clock
            status = "COMPLETED_WITH_REJECTS" if rejected_orders or customer_errors else "SUCCESS"
            if not order_reconciliation:
                status = "FAILED_RECONCILIATION"
            logger.info("step 7/7 summarize_counts(): input=%d accounted=%d passed=%s -> status=%s",
                        counts["input_orders"], counts["accounted_for_orders"],
                        order_reconciliation, status)

            conn.execute(
                """INSERT INTO pipeline_runs
                   (run_id, started_at, finished_at, status, customers_file, orders_file,
                    customer_rows_read, order_rows_read, accepted_orders, rejected_orders,
                    rejected_customers, skipped_orders, late_arriving_orders,
                    accepted_amount_paise, duration_seconds, message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, started_at, now_utc(), status, customers_file, orders_file,
                 len(customers), len(orders), accepted, rejected_orders,
                 len(customer_errors), skipped, late_arriving, accepted_amount_paise, duration,
                 "Order reconciliation passed" if order_reconciliation else "Order reconciliation failed"),
            )
            logger.info("pipeline_runs row written; committing transaction")

        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) AS total FROM fact_order"
        ).fetchone()["total"]
    except Exception as exc:
        # The load transaction has rolled back; record the failure so operators can see why.
        logger.exception("run %s FAILED, transaction rolled back: %s", run_id, exc)
        record_failed_run(conn, run_id, started_at, started_clock,
                          customers_file, orders_file, f"{type(exc).__name__}: {exc}")
        exc.run_id = run_id  # lets callers (e.g. the API) point to the FAILED run row
        raise
    finally:
        conn.close()

    total_duration = time.perf_counter() - started_clock
    logger.info("run %s finished status=%s accepted=%d rejected=%d skipped=%d in %.3fs",
                run_id, status, accepted, rejected_orders, skipped, total_duration)
    return {
        "run_id": run_id,
        "status": status,
        "customer_rows_read": len(customers),
        "order_rows_read": len(orders),
        "accepted_orders": accepted,
        "rejected_orders": rejected_orders,
        "rejected_customers": len(customer_errors),
        "rejected_records_including_customer_errors": rejected_orders + len(customer_errors),
        "skipped_orders_already_loaded": skipped,
        "late_arriving_orders": late_arriving,
        "unexpected_columns": {
            "customers": extra_customer_columns,
            "orders": unexpected_order_fields,
        },
        "order_reconciliation_passed": order_reconciliation,
        "accepted_amount_inr": round(accepted_amount_paise / 100, 2),
        "warehouse_total_revenue_inr": round(total_revenue / 100, 2),
        "duration_seconds": round(total_duration, 4),
        "orders_per_second": round(len(orders) / total_duration) if total_duration else None,
        "database": str(db_path),
    }


def main():
    parser = argparse.ArgumentParser(description="Run the customer/order data pipeline")
    parser.add_argument("--customers", default="data/input/customers.csv")
    parser.add_argument("--orders", default="data/input/orders.json")
    parser.add_argument("--db", default="data/generated/pipeline.db")
    parser.add_argument("--log-level", default=None, help="DEBUG shows one line per order")
    args = parser.parse_args()
    setup_logging(args.log_level)
    try:
        result = run_pipeline(args.customers, args.orders, args.db)
    except Exception as exc:
        raise SystemExit(f"Pipeline failed: {exc}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
