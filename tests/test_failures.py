import pytest
from src.pipeline.readers import read_orders


def test_malformed_json_fails_clearly(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception):
        read_orders(path)


def test_json_must_be_array(tmp_path):
    path = tmp_path / "object.json"
    path.write_text('{"order_id":"O1"}', encoding="utf-8")
    with pytest.raises(ValueError, match="top-level array"):
        read_orders(path)
