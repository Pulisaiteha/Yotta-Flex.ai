import json
import pytest  # type: ignore[import-not-found]
from pathlib import Path


CUSTOMERS_CSV = "customer_id,name,city\nC101,Ravi,Hyderabad\nC102,Priya,Bengaluru\n"


@pytest.fixture
def sample_files(tmp_path):
    customers = tmp_path / "customers.csv"
    customers.write_text(CUSTOMERS_CSV, encoding="utf-8")
    orders = tmp_path / "orders.json"
    orders.write_text(json.dumps([
        {"order_id":"O500","customer_id":"C101","order_date":"2026-10-28","amount":1200},
        {"order_id":"O501","customer_id":"C102","order_date":"2026-10-28","amount":800},
        {"order_id":"O502","customer_id":"C999","order_date":"2026-10-28","amount":500},
    ]), encoding="utf-8")
    return customers, orders, tmp_path / "pipeline.db"


@pytest.fixture
def make_inputs(tmp_path):
    """Factory: make_inputs(orders_list, customers_csv=...) -> (customers, orders, db) paths."""
    counter = {"n": 0}

    def _make(orders, customers_csv=CUSTOMERS_CSV):
        counter["n"] += 1
        folder = tmp_path / f"batch{counter['n']}"
        folder.mkdir()
        customers = folder / "customers.csv"
        customers.write_text(customers_csv, encoding="utf-8")
        orders_path = folder / "orders.json"
        orders_path.write_text(orders if isinstance(orders, str) else json.dumps(orders),
                               encoding="utf-8")
        return customers, orders_path, tmp_path / "pipeline.db"

    return _make
