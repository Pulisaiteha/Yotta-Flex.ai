from src.pipeline.validation import validate_order, validate_customer


def test_valid_customer():
    assert validate_customer({"customer_id":"C1","name":"Ravi","city":"Hyderabad"}) == []


def test_customer_missing_name():
    assert "missing_name" in validate_customer(
        {"customer_id":"C1","name":"","city":"Hyderabad"}
    )


def test_order_unknown_customer():
    row = {"order_id":"O1","customer_id":"C999","order_date":"2026-10-28","amount":100}
    assert "unknown_customer_id" in validate_order(row, {"C1"})


def test_order_negative_amount():
    row = {"order_id":"O1","customer_id":"C1","order_date":"2026-10-28","amount":-1}
    assert "amount_must_be_positive" in validate_order(row, {"C1"})


def test_order_invalid_date():
    row = {"order_id":"O1","customer_id":"C1","order_date":"2026-99-40","amount":100}
    assert "invalid_order_date" in validate_order(row, {"C1"})
