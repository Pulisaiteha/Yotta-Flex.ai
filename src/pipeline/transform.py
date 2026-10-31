from datetime import date
from decimal import Decimal


def clean_customer(row):
    return {
        "customer_id": str(row["customer_id"]).strip(),
        "name": " ".join(str(row["name"]).split()),
        "city": " ".join(str(row["city"]).split()).title(),
    }


def clean_order(row):
    amount = Decimal(str(row["amount"]).strip()).quantize(Decimal("0.01"))
    order_date = date.fromisoformat(str(row["order_date"]).strip()).isoformat()
    return {
        "order_id": str(row["order_id"]).strip(),
        "customer_id": str(row["customer_id"]).strip(),
        "order_date": order_date,
        # Store money as integer paise to avoid floating-point rounding errors.
        "amount_paise": int(amount * 100),
    }
