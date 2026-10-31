import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipeline import queries  # noqa: E402
from src.pipeline.database import connect  # noqa: E402


def print_rows(title, rows):
    print(f"\n=== {title} ===")
    for row in rows:
        print(row)
    if not rows:
        print("(no rows)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/generated/pipeline.db")
    args = parser.parse_args()
    if not Path(args.db).exists():
        raise SystemExit(f"Database not found: {args.db}. Run the pipeline first.")
    conn = connect(args.db)
    print_rows("Revenue summary", [queries.revenue_summary(conn)])
    print_rows("Revenue by customer", queries.revenue_by_customer(conn, limit=10))
    print_rows("Revenue by city", queries.revenue_by_city(conn))
    print_rows("Recent pipeline runs", [
        {k: r[k] for k in ("run_id", "status", "order_rows_read", "accepted_orders",
                           "rejected_orders", "skipped_orders", "duration_seconds")}
        for r in queries.list_runs(conn, limit=10)
    ])
    print_rows("Rejection reasons", queries.rejection_reasons(conn))
    latest = queries.list_runs(conn, limit=1)
    if latest:
        print(f"\n=== Reconciliation for latest run {latest[0]['run_id']} ===")
        print(json.dumps(queries.reconcile_run(conn, latest[0]["run_id"]), indent=2))
    conn.close()


if __name__ == "__main__":
    main()
