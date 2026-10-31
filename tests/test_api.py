import pytest
from fastapi.testclient import TestClient  # type: ignore[reportMissingImports]

from src.api.main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(db_path=tmp_path / "api.db"))


@pytest.fixture
def run_id(client, sample_files):
    customers, orders, _ = sample_files
    response = client.post("/pipeline/runs", json={"customers_path": str(customers),
                                                   "orders_path": str(orders)})
    assert response.status_code == 201
    return response.json()["run_id"]


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_run_and_monitoring_endpoints(client, run_id):
    run = client.get(f"/pipeline/runs/{run_id}").json()
    assert run["status"] == "COMPLETED_WITH_REJECTS"
    assert run["accepted_orders"] == 2

    assert [r["run_id"] for r in client.get("/pipeline/runs").json()] == [run_id]

    rejections = client.get(f"/pipeline/runs/{run_id}/rejections").json()
    assert rejections[0]["reason"] == "unknown_customer_id"
    assert rejections[0]["source_row_number"] == 3

    recon = client.get(f"/pipeline/runs/{run_id}/reconciliation").json()
    assert recon["passed"] is True


def test_upload_run(client, sample_files):
    customers, orders, _ = sample_files
    with customers.open("rb") as c, orders.open("rb") as o:
        response = client.post("/pipeline/runs/upload",
                               files={"customers_file": ("customers.csv", c, "text/csv"),
                                      "orders_file": ("orders.json", o, "application/json")})
    assert response.status_code == 201
    assert response.json()["accepted_orders"] == 2


def test_analytics_endpoints(client, run_id):
    assert client.get("/analytics/revenue/summary").json()["total_revenue_inr"] == 2000.0
    by_customer = client.get("/analytics/revenue/by-customer").json()
    assert by_customer[0] == {"customer_id": "C101", "name": "Ravi", "city": "Hyderabad",
                              "revenue_inr": 1200.0, "order_count": 1}
    assert client.get("/analytics/revenue/by-day").json() == [
        {"full_date": "2026-10-28", "revenue_inr": 2000.0, "order_count": 2}]
    assert len(client.get("/analytics/revenue/by-city").json()) == 2
    assert client.get("/analytics/rejections/reasons").json()[0]["reason"] == "unknown_customer_id"


def test_warehouse_endpoints(client, run_id):
    assert len(client.get("/customers").json()) == 2
    detail = client.get("/customers/C101").json()
    assert detail["order_count"] == 1 and detail["change_history"] == []
    assert len(client.get("/orders", params={"customer_id": "C102"}).json()) == 1


def test_not_found_and_bad_input(client, tmp_path):
    assert client.get("/pipeline/runs/run_missing").status_code == 404
    assert client.get("/customers/NOPE").status_code == 404

    missing = client.post("/pipeline/runs", json={"customers_path": str(tmp_path / "x.csv"),
                                                  "orders_path": str(tmp_path / "y.json")})
    assert missing.status_code == 404
    failed_run_id = missing.json()["detail"]["run_id"]
    assert client.get(f"/pipeline/runs/{failed_run_id}").json()["status"] == "FAILED"


# ---------- demo page + source-data endpoints (INTERVIEWER_GUIDE.md steps 0-7) ----------

@pytest.fixture
def demo_client(tmp_path):
    return TestClient(create_app(db_path=tmp_path / "demo.db", source_dir=tmp_path / "workspace"))


def test_demo_page_is_served(demo_client):
    response = demo_client.get("/")
    assert response.status_code == 200
    assert "Run pipeline" in response.text


def test_guide_flow_create_files_then_run(demo_client):
    assert demo_client.post("/demo/reset").status_code == 200

    # Step 1: customers.csv
    demo_client.post("/source/customers", json={"customer_id": "C1", "name": "Ravi", "city": "Hyderabad"})
    customers = demo_client.get("/source/customers").json()
    assert customers["file"].endswith("customers.csv")
    assert customers["rows"] == [{"customer_id": "C1", "name": "Ravi", "city": "Hyderabad"}]

    # Step 2: orders.json, one good and one bad (blank amount -> null)
    demo_client.post("/source/orders", json={"order_id": "O1", "customer_id": "C1",
                                             "order_date": "2026-10-01", "amount": "100.50"})
    demo_client.post("/source/orders", json={"order_id": "O2", "customer_id": "C1",
                                             "order_date": "2026-10-01", "amount": ""})
    orders = demo_client.get("/source/orders").json()
    assert orders["rows"][1]["amount"] is None

    # Step 3: run on exactly those files
    run = demo_client.post("/pipeline/runs", json={"customers_path": customers["file"],
                                                   "orders_path": orders["file"]}).json()
    assert (run["accepted_orders"], run["rejected_orders"]) == (1, 1)
    reasons = [r["reason"] for r in demo_client.get(f"/pipeline/runs/{run['run_id']}/rejections").json()]
    assert reasons == ["missing_amount"]

    # Step 7: customer moves city -> source row replaced -> history after next run
    saved = demo_client.post("/source/customers", json={"customer_id": "C1", "name": "Ravi", "city": "Pune"})
    assert saved.json()["action"] == "replaced"
    demo_client.post("/pipeline/runs", json={"customers_path": customers["file"],
                                             "orders_path": orders["file"]})
    history = demo_client.get("/customers/C1").json()["change_history"]
    assert [(h["old_city"], h["new_city"]) for h in history] == [("Hyderabad", "Pune")]


def test_load_sample_then_reset(demo_client):
    demo_client.post("/source/load-sample")
    assert len(demo_client.get("/source/orders").json()["rows"]) == 11
    files = demo_client.get("/source/customers").json()["file"], demo_client.get("/source/orders").json()["file"]
    demo_client.post("/pipeline/runs", json={"customers_path": files[0], "orders_path": files[1]})
    assert demo_client.get("/analytics/revenue/summary").json()["order_count"] == 4

    demo_client.post("/demo/reset")
    assert demo_client.get("/source/orders").json()["rows"] == []
    assert demo_client.get("/pipeline/runs").json() == []
