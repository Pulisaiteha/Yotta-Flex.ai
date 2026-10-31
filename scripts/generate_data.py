"""Generate a reproducible (fixed seed) synthetic dataset of any size.

Input files go to <out>/input/, expected-answer labels go to <out>/expected/ so they
are kept separate from the data the pipeline reads.
"""
import argparse
import csv
import json
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


CITIES = ["Hyderabad", "Bengaluru", "Chennai", "Pune", "Mumbai"]

# Each invalid kind -> function that corrupts a valid order, and the reason the pipeline should give.
INVALID_KINDS = {
    "unknown_customer_id": lambda o: o.update(customer_id="C999999"),
    "amount_must_be_positive": lambda o: o.update(amount=random.choice([0, -100, -0.01])),
    "invalid_order_date": lambda o: o.update(order_date="2026-13-45"),
    "missing_amount": lambda o: o.update(amount=None),
    "missing_order_fields:order_date": lambda o: o.pop("order_date"),
    "invalid_amount": lambda o: o.update(amount="abc"),
    "amount_more_than_2_decimals": lambda o: o.update(amount=10.005),
}


def generate(customers=100, orders=1000, invalid_rate=0.05, seed=42, out="data/generated"):
    random.seed(seed)
    out = Path(out)
    (out / "input").mkdir(parents=True, exist_ok=True)
    (out / "expected").mkdir(parents=True, exist_ok=True)

    customer_rows = [
        {"customer_id": f"C{i:04d}", "name": f"Customer {i}", "city": random.choice(CITIES)}
        for i in range(1, customers + 1)
    ]
    # Two labelled bad customer rows: a duplicate ID and a missing name.
    bad_customers = [
        dict(customer_rows[0]),
        {"customer_id": "C9998", "name": "", "city": "Pune"},
    ]
    with (out / "input" / "customers.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["customer_id", "name", "city"])
        writer.writeheader()
        writer.writerows(customer_rows + bad_customers)

    order_rows, labels = [], []
    valid_ids = []
    start = date(2026, 1, 1)
    for i in range(1, orders + 1):
        order = {
            "order_id": f"O{i:07d}",
            "customer_id": random.choice(customer_rows)["customer_id"],
            # Random dates => orders arrive out of order, like real event data.
            "order_date": (start + timedelta(days=random.randrange(270))).isoformat(),
            "amount": round(random.uniform(100, 5000), 2),
        }
        expected = "accepted"
        if random.random() < invalid_rate:
            kinds = list(INVALID_KINDS) + ["duplicate_order_id_in_batch"]
            kind = random.choice(kinds)
            if kind == "duplicate_order_id_in_batch" and valid_ids:
                order["order_id"] = random.choice(valid_ids)
                expected = kind
            elif kind != "duplicate_order_id_in_batch":
                INVALID_KINDS[kind](order)
                expected = kind
        elif i % 500 == 0:
            # Boundary values that must still be accepted.
            order["amount"] = random.choice([0.01, 999999.99])
        if expected == "accepted":
            valid_ids.append(order["order_id"])
        else:
            labels.append({"row": i, "order_id": order["order_id"], "expected_reason": expected})
        order_rows.append(order)

    with (out / "input" / "orders.json").open("w", encoding="utf-8") as f:
        json.dump(order_rows, f, indent=1)

    expected = {
        "seed": seed,
        "customer_rows": len(customer_rows) + len(bad_customers),
        "expected_rejected_customers": len(bad_customers),
        "order_rows": len(order_rows),
        "expected_accepted_orders": len(valid_ids),
        "expected_rejected_orders": len(labels),
        "expected_rejections_by_reason": dict(Counter(l["expected_reason"] for l in labels)),
        "rejected_rows": labels,
    }
    with (out / "expected" / "expected_labels.json").open("w", encoding="utf-8") as f:
        json.dump(expected, f, indent=1)
    return expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--customers", type=int, default=100)
    parser.add_argument("--orders", type=int, default=1000)
    parser.add_argument("--invalid-rate", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data/generated")
    args = parser.parse_args()
    if args.customers < 1 or args.orders < 0 or not 0 <= args.invalid_rate <= 1:
        raise SystemExit("Use customers >= 1, orders >= 0 and invalid-rate between 0 and 1")

    expected = generate(args.customers, args.orders, args.invalid_rate, args.seed, args.out)
    print(f"Wrote {expected['customer_rows']} customers and {expected['order_rows']} orders "
          f"to {args.out}/input; labels in {args.out}/expected/expected_labels.json")
    print(f"Expected accepted={expected['expected_accepted_orders']} "
          f"rejected={expected['expected_rejected_orders']}")


if __name__ == "__main__":
    main()
