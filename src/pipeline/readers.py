import csv
import json
import logging
from pathlib import Path

from .validation import check_customer_header


logger = logging.getLogger(__name__)


def read_customers(path):
    """Return (rows, extra_columns). Fails fast if required columns are missing."""
    path = Path(path)
    logger.info("reading customers CSV %s", path)
    if not path.exists():
        raise FileNotFoundError(f"Customer file not found: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Customer CSV is empty or has no header")
        extra_columns = check_customer_header(reader.fieldnames)
        rows = list(reader)
    logger.info("customers CSV header=%s -> %d data rows", reader.fieldnames, len(rows))
    return rows, extra_columns


def read_orders(path):
    path = Path(path)
    logger.info("reading orders JSON %s", path)
    if not path.exists():
        raise FileNotFoundError(f"Order file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Orders file is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("Orders JSON must contain a top-level array")
    if not all(isinstance(row, dict) for row in data):
        raise ValueError("Every order must be a JSON object")
    logger.info("orders JSON parsed -> %d order objects", len(data))
    return data
