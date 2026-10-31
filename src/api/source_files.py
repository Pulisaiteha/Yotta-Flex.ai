"""Simulates the two SOURCE SYSTEMS that produce the pipeline's input files.

The demo page (and the /source API) writes here:
    data/workspace/customers.csv   <- "CRM system" export
    data/workspace/orders.json     <- "order system" export
The pipeline then reads these files exactly as it would read real exports.

Nothing here validates the data on purpose: bad rows must reach the pipeline so
you can watch the pipeline reject them.
"""
import csv
import json
import logging
import shutil
from pathlib import Path


logger = logging.getLogger(__name__)

CUSTOMER_COLUMNS = ["customer_id", "name", "city"]
SAMPLE_DIR = Path("data/input")


def customers_file(folder):
    return Path(folder) / "customers.csv"


def orders_file(folder):
    return Path(folder) / "orders.json"


# ---------- customers.csv ----------

def read_customers_file(folder):
    path = customers_file(folder)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_customers_file(folder, rows):
    path = customers_file(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CUSTOMER_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def save_customer(folder, customer):
    """Add a customer row, or replace the row with the same customer_id
    (a source system always exports the CURRENT state of a customer)."""
    rows = read_customers_file(folder)
    replaced = False
    for i, row in enumerate(rows):
        if customer["customer_id"] and row["customer_id"] == customer["customer_id"]:
            rows[i] = customer
            replaced = True
    if not replaced:
        rows.append(customer)
    write_customers_file(folder, rows)
    logger.info("save_customer(): %s %s in %s", "replaced" if replaced else "appended",
                customer, customers_file(folder))
    return {"action": "replaced" if replaced else "appended", "total_rows": len(rows)}


# ---------- orders.json ----------

def read_orders_file(folder):
    path = orders_file(folder)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_orders_file(folder, orders):
    path = orders_file(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(orders, f, indent=2)


def save_order(folder, order):
    """Always append, even if the order_id already exists, so in-batch duplicates can be demoed."""
    orders = read_orders_file(folder)
    orders.append(order)
    write_orders_file(folder, orders)
    logger.info("save_order(): appended %s to %s", order, orders_file(folder))
    return {"action": "appended", "total_rows": len(orders)}


# ---------- whole workspace ----------

def load_sample(folder):
    """Copy the bundled sample (3 customers, 11 orders incl. 7 deliberately bad) into the workspace."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SAMPLE_DIR / "customers.csv", customers_file(folder))
    shutil.copyfile(SAMPLE_DIR / "orders.json", orders_file(folder))
    logger.info("load_sample(): copied %s/* into %s", SAMPLE_DIR, folder)


def clear(folder):
    write_customers_file(folder, [])
    write_orders_file(folder, [])
    logger.info("clear(): emptied source files in %s", folder)
