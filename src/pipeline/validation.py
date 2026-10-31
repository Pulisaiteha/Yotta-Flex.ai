import logging
from datetime import date
from decimal import Decimal, InvalidOperation


logger = logging.getLogger(__name__)


CUSTOMER_FIELDS = {"customer_id", "name", "city"}
ORDER_FIELDS = {"order_id", "customer_id", "order_date", "amount"}


class SchemaError(ValueError):
    """Raised when a whole source file does not match the expected schema."""


def check_customer_header(fieldnames):
    """Header-level schema check. Missing columns fail the run; extra columns are returned."""
    missing = CUSTOMER_FIELDS - set(fieldnames)
    if missing:
        raise SchemaError("Customer CSV missing required columns: " + ",".join(sorted(missing)))
    logger.debug("customer header OK: required columns %s present", sorted(CUSTOMER_FIELDS))
    return sorted(set(fieldnames) - CUSTOMER_FIELDS)


def validate_customer(row):
    errors = []
    missing = CUSTOMER_FIELDS - set(row.keys())
    if missing:
        errors.append("missing_customer_columns:" + ",".join(sorted(missing)))
        return errors
    for field in ("customer_id", "name", "city"):
        if row.get(field) is None or not str(row.get(field)).strip():
            errors.append(f"missing_{field}")
    return errors


def validate_order(row, known_customer_ids, today=None):
    today = today or date.today()
    errors = []
    missing = ORDER_FIELDS - set(row.keys())
    if missing:
        errors.append("missing_order_fields:" + ",".join(sorted(missing)))
        return errors

    for field in ("order_id", "customer_id", "order_date", "amount"):
        if row.get(field) is None or str(row.get(field)).strip() == "":
            errors.append(f"missing_{field}")

    if errors:
        return errors

    try:
        order_date = date.fromisoformat(str(row["order_date"]).strip())
        # Domain rule: an order cannot be placed in the future.
        if order_date > today:
            errors.append("order_date_in_future")
    except (ValueError, TypeError):
        errors.append("invalid_order_date")

    # bool is a subclass of int in Python, so reject it explicitly.
    if isinstance(row["amount"], bool):
        errors.append("invalid_amount")
    else:
        try:
            amount = Decimal(str(row["amount"]).strip())
            if not amount.is_finite():
                errors.append("amount_not_finite")
            elif amount <= 0:
                errors.append("amount_must_be_positive")
            # Domain rule: money has at most 2 decimals (paise). 0.001 would round to 0.
            elif amount.as_tuple().exponent < -2:
                errors.append("amount_more_than_2_decimals")
        except (InvalidOperation, ValueError, TypeError):
            errors.append("invalid_amount")

    if str(row["customer_id"]).strip() not in known_customer_ids:
        errors.append("unknown_customer_id")

    return errors
