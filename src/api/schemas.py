"""Request/response models. FastAPI uses these to validate input and build the Swagger docs."""
from typing import Optional

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    customers_path: str = Field("data/input/customers.csv", description="Path to customers CSV on the server")
    orders_path: str = Field("data/input/orders.json", description="Path to orders JSON on the server")


class UnexpectedColumns(BaseModel):
    customers: list[str]
    orders: list[str]


class RunSummary(BaseModel):
    run_id: str
    status: str
    customer_rows_read: int
    order_rows_read: int
    accepted_orders: int
    rejected_orders: int
    rejected_customers: int
    skipped_orders_already_loaded: int
    late_arriving_orders: int
    unexpected_columns: UnexpectedColumns
    order_reconciliation_passed: bool
    accepted_amount_inr: float
    warehouse_total_revenue_inr: float
    duration_seconds: float
    orders_per_second: Optional[int]


class Run(BaseModel):
    run_id: str
    started_at: str
    finished_at: str
    status: str
    customers_file: Optional[str]
    orders_file: Optional[str]
    customer_rows_read: int
    order_rows_read: int
    accepted_orders: int
    rejected_orders: int
    rejected_customers: int
    skipped_orders: int
    late_arriving_orders: int
    accepted_amount_paise: int
    duration_seconds: float
    message: Optional[str]


class Rejection(BaseModel):
    rejection_key: int
    run_id: str
    entity: str
    source_name: str
    source_row_number: int
    record_id: Optional[str]
    reason: str
    raw_record: str


class Reconciliation(BaseModel):
    run_id: str
    status: str
    order_rows_read: int
    accepted_orders: int
    rejected_orders: int
    skipped_orders: int
    fact_rows_loaded_by_run: int
    reject_rows_recorded: int
    accepted_amount_inr: float
    fact_amount_loaded_inr: float
    checks: dict[str, bool]
    passed: bool


class RevenueSummary(BaseModel):
    total_revenue_inr: float
    order_count: int
    paying_customers: int
    avg_order_value_inr: float


class CustomerRevenue(BaseModel):
    customer_id: str
    name: str
    city: str
    revenue_inr: float
    order_count: int


class CityRevenue(BaseModel):
    city: str
    revenue_inr: float
    order_count: int


class DailyRevenue(BaseModel):
    full_date: str
    revenue_inr: float
    order_count: int


class RejectionReason(BaseModel):
    entity: str
    reason: str
    records: int


class Customer(BaseModel):
    customer_id: str
    name: str
    city: str


class CustomerChange(BaseModel):
    run_id: str
    old_name: str
    old_city: str
    new_name: str
    new_city: str
    changed_at: str


class CustomerDetail(Customer):
    order_count: int
    revenue_inr: float
    change_history: list[CustomerChange]


class Order(BaseModel):
    order_id: str
    customer_id: str
    order_date: str
    amount_inr: float
    source_run_id: str


# ---------- source data entry (demo page steps 1-2) ----------
# Fields are plain text and may be blank on purpose: bad input must reach the pipeline.

class SourceCustomer(BaseModel):
    customer_id: str = Field("", examples=["C104"])
    name: str = Field("", examples=["Meena"])
    city: str = Field("", examples=["Pune"])


class SourceOrder(BaseModel):
    order_id: Optional[str] = Field(None, examples=["O600"])
    customer_id: Optional[str] = Field(None, examples=["C104"])
    order_date: Optional[str] = Field(None, examples=["2026-10-30"], description="YYYY-MM-DD")
    amount: Optional[str] = Field(None, examples=["499.99"], description="INR; blank = null")


class SourceFile(BaseModel):
    file: str
    rows: list[dict]


class SaveResult(BaseModel):
    file: str
    action: str
    total_rows: int


class Health(BaseModel):
    status: str
    database: str
