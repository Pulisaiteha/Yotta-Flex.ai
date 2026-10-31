"""REST API over the pipeline.

Run:   uvicorn src.api.main:app --reload
Open:  http://127.0.0.1:8000        -> step-by-step demo page (static/index.html)
       http://127.0.0.1:8000/docs   -> Swagger UI

How a request flows (every route follows this):
    browser -> log_requests() middleware -> get_db() opens SQLite -> route function
            -> ONE call into the pipeline package (runner / queries / source_files) -> JSON response

Route groups (same order as the demo page and INTERVIEWER_GUIDE.md):
    Step 0-2  Source data  /demo/reset, /source/...        -> source_files.py   (writes CSV/JSON inputs)
    Step 3    Pipeline     POST /pipeline/runs             -> runner.run_pipeline()  (the ONLY write path)
    Step 4    Monitoring   GET /pipeline/runs/...          -> queries.py
    Step 5    Analytics    GET /analytics/...              -> queries.py
    Step 6    Warehouse    GET /customers, /orders         -> queries.py
"""
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from src.logging_config import request_id_var, setup_logging
from src.pipeline import queries
from src.pipeline.database import clear_all_tables, connect, initialize
from src.pipeline.runner import run_pipeline
from . import schemas, source_files


logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/generated/pipeline.db"
DEFAULT_SOURCE_DIR = "data/workspace"
UPLOAD_DIR = Path("data/generated/uploads")
DEMO_PAGE = Path(__file__).parent / "static" / "index.html"

TAGS = [
    {"name": "Source data", "description": "Steps 0-2: create the input files (customers.csv, orders.json) "
                                           "the way a source system would."},
    {"name": "Pipeline", "description": "Step 3-4: run the pipeline and monitor runs, rejects and reconciliation."},
    {"name": "Analytics", "description": "Step 5: revenue and data-quality reports."},
    {"name": "Warehouse", "description": "Step 6: look up loaded customers and orders."},
    {"name": "Health", "description": "Service liveness."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when uvicorn starts the server (not in unit tests).
    setup_logging()
    logger.info("API started, database=%s, open http://127.0.0.1:8000 for the demo", app.state.db_path)
    yield
    logger.info("API shutting down")


def create_app(db_path=None, source_dir=None):
    app = FastAPI(
        lifespan=lifespan,
        title="YottaFlex Data Pipeline API",
        version="1.0.0",
        description="Ingest customers (CSV) and orders (JSON), validate, load a SQLite "
                    "star schema, and expose monitoring and analytics. "
                    "Demo page: [/](/) · Guide: INTERVIEWER_GUIDE.md",
        openapi_tags=TAGS,
    )
    app.state.db_path = db_path or os.getenv("PIPELINE_DB_PATH", DEFAULT_DB_PATH)
    app.state.source_dir = source_dir or os.getenv("PIPELINE_SOURCE_DIR", DEFAULT_SOURCE_DIR)
    register_middleware(app)
    register_routes(app)
    return app


def register_middleware(app: FastAPI):
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Give each request an ID, log it in and out, and name the route function that handled it."""
        token = request_id_var.set(uuid.uuid4().hex[:6])
        started = time.perf_counter()
        query = f"?{request.url.query}" if request.url.query else ""
        logger.info("--> %s %s%s", request.method, request.url.path, query)
        try:
            response = await call_next(request)
            endpoint = request.scope.get("endpoint")
            handler = f"main.{endpoint.__name__}()" if endpoint else "-"
            logger.info("<-- %s %s %d handled by %s in %.1f ms", request.method, request.url.path,
                        response.status_code, handler, (time.perf_counter() - started) * 1000)
            return response
        finally:
            request_id_var.reset(token)


def get_db(request: Request):
    logger.debug("get_db(): opening connection for this request")
    conn = connect(request.app.state.db_path)
    initialize(conn)
    try:
        yield conn
    finally:
        conn.close()


def execute_run(db_path, customers_path, orders_path):
    """Run the pipeline and translate failures into HTTP errors (the run is still recorded as FAILED)."""
    logger.info("execute_run() -> calling runner.run_pipeline(customers=%s, orders=%s)",
                customers_path, orders_path)
    try:
        return run_pipeline(customers_path, orders_path, db_path)
    except FileNotFoundError as exc:
        logger.warning("run failed, returning 404: %s", exc)
        raise HTTPException(404, {"error": str(exc), "run_id": getattr(exc, "run_id", None)})
    except ValueError as exc:  # malformed JSON, schema errors, wrong file shape
        logger.warning("run failed, returning 422: %s", exc)
        raise HTTPException(422, {"error": str(exc), "run_id": getattr(exc, "run_id", None)})


def save_upload(upload: UploadFile, folder: Path, default_name: str) -> Path:
    path = folder / (Path(upload.filename or default_name).name or default_name)
    with path.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    logger.info("save_upload(): stored uploaded %s at %s", upload.filename, path)
    return path


def register_routes(app: FastAPI):

    # ---------- demo page ----------

    @app.get("/", include_in_schema=False)
    def demo_page():
        """Serves the step-by-step demo page. All its buttons call the API routes below."""
        return FileResponse(DEMO_PAGE)

    # ---------- steps 0-2: source data ----------

    @app.post("/demo/reset", tags=["Source data"], summary="Step 0: empty the input files AND the warehouse")
    def reset_demo(request: Request, conn=Depends(get_db)):
        """Demo only. Calls `source_files.clear()` and `database.clear_all_tables()`."""
        source_files.clear(request.app.state.source_dir)
        clear_all_tables(conn)
        return {"status": "reset", "source_dir": request.app.state.source_dir}

    @app.post("/source/load-sample", tags=["Source data"],
              summary="Step 0: copy the bundled sample (3 customers, 11 orders) into the input files")
    def load_sample(request: Request):
        """Calls `source_files.load_sample()`: copies data/input/* to data/workspace/."""
        source_files.load_sample(request.app.state.source_dir)
        return {"status": "loaded", "source_dir": request.app.state.source_dir}

    @app.get("/source/customers", response_model=schemas.SourceFile, tags=["Source data"],
             summary="Step 1: show the rows currently in customers.csv")
    def get_source_customers(request: Request):
        """Calls `source_files.read_customers_file()`."""
        folder = request.app.state.source_dir
        return {"file": str(source_files.customers_file(folder)),
                "rows": source_files.read_customers_file(folder)}

    @app.post("/source/customers", response_model=schemas.SaveResult, status_code=201,
              tags=["Source data"], summary="Step 1: add/replace one customer row in customers.csv")
    def add_source_customer(request: Request, customer: schemas.SourceCustomer):
        """Calls `source_files.save_customer()`. No validation here on purpose - the pipeline validates."""
        folder = request.app.state.source_dir
        result = source_files.save_customer(folder, customer.model_dump())
        return {"file": str(source_files.customers_file(folder)), **result}

    @app.get("/source/orders", response_model=schemas.SourceFile, tags=["Source data"],
             summary="Step 2: show the objects currently in orders.json")
    def get_source_orders(request: Request):
        """Calls `source_files.read_orders_file()`."""
        folder = request.app.state.source_dir
        return {"file": str(source_files.orders_file(folder)),
                "rows": source_files.read_orders_file(folder)}

    @app.post("/source/orders", response_model=schemas.SaveResult, status_code=201,
              tags=["Source data"], summary="Step 2: append one order to orders.json")
    def add_source_order(request: Request, order: schemas.SourceOrder):
        """Calls `source_files.save_order()`. Blank fields are stored as null so the pipeline can reject them."""
        folder = request.app.state.source_dir
        row = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in order.model_dump().items()}
        result = source_files.save_order(folder, row)
        return {"file": str(source_files.orders_file(folder)), **result}

    # ---------- step 3-4: pipeline run + monitoring ----------

    @app.post("/pipeline/runs", response_model=schemas.RunSummary, status_code=201, tags=["Pipeline"],
              summary="Step 3: run the pipeline on files already on the server")
    def create_run(request: Request, body: Optional[schemas.RunRequest] = None):
        """Calls `execute_run()` -> `runner.run_pipeline()`:
        read files -> validate -> transform -> load in ONE transaction -> record run metrics."""
        body = body or schemas.RunRequest()  # empty body -> default sample files
        return execute_run(request.app.state.db_path, body.customers_path, body.orders_path)

    @app.post("/pipeline/runs/upload", response_model=schemas.RunSummary, status_code=201,
              tags=["Pipeline"], summary="Step 3 (alternative): upload customers CSV + orders JSON and run")
    def upload_run(request: Request,
                   customers_file: UploadFile = File(..., description="customers.csv"),
                   orders_file: UploadFile = File(..., description="orders.json")):
        """Calls `save_upload()` twice, then the same `runner.run_pipeline()`."""
        folder = UPLOAD_DIR / uuid.uuid4().hex[:12]
        folder.mkdir(parents=True, exist_ok=True)
        customers_path = save_upload(customers_file, folder, "customers.csv")
        orders_path = save_upload(orders_file, folder, "orders.json")
        return execute_run(request.app.state.db_path, customers_path, orders_path)

    @app.get("/pipeline/runs", response_model=list[schemas.Run], tags=["Pipeline"],
             summary="Step 4: list recent runs (newest first)")
    def list_runs(status: Optional[str] = Query(None, examples=["SUCCESS"]),
                  limit: int = Query(20, ge=1, le=500), conn=Depends(get_db)):
        """Calls `queries.list_runs()` -> SELECT FROM pipeline_runs."""
        return queries.list_runs(conn, limit, status)

    @app.get("/pipeline/runs/{run_id}", response_model=schemas.Run, tags=["Pipeline"],
             summary="Step 4: one run's status, counts and failure message")
    def get_run(run_id: str, conn=Depends(get_db)):
        """Calls `queries.get_run()`."""
        run = queries.get_run(conn, run_id)
        if run is None:
            raise HTTPException(404, f"Run not found: {run_id}")
        return run

    @app.get("/pipeline/runs/{run_id}/rejections", response_model=list[schemas.Rejection],
             tags=["Pipeline"], summary="Step 4: quarantined records of a run, traceable to source row")
    def get_rejections(run_id: str, entity: Optional[Literal["customer", "order"]] = None,
                       limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                       conn=Depends(get_db)):
        """Calls `queries.get_run()` (404 check) then `queries.list_rejections()`."""
        if queries.get_run(conn, run_id) is None:
            raise HTTPException(404, f"Run not found: {run_id}")
        return queries.list_rejections(conn, run_id, entity, limit, offset)

    @app.get("/pipeline/runs/{run_id}/reconciliation", response_model=schemas.Reconciliation,
             tags=["Pipeline"], summary="Step 4: source-to-target count and amount reconciliation")
    def get_reconciliation(run_id: str, conn=Depends(get_db)):
        """Calls `queries.reconcile_run()`: compares run metrics with real table contents."""
        result = queries.reconcile_run(conn, run_id)
        if result is None:
            raise HTTPException(404, f"Run not found: {run_id}")
        return result

    # ---------- step 5: analytics ----------

    @app.get("/analytics/revenue/summary", response_model=schemas.RevenueSummary, tags=["Analytics"])
    def revenue_summary(conn=Depends(get_db)):
        """Calls `queries.revenue_summary()` -> SUM/COUNT over fact_order."""
        return queries.revenue_summary(conn)

    @app.get("/analytics/revenue/by-customer", response_model=list[schemas.CustomerRevenue],
             tags=["Analytics"])
    def revenue_by_customer(limit: int = Query(50, ge=1, le=1000), conn=Depends(get_db)):
        """Calls `queries.revenue_by_customer()` -> fact_order JOIN dim_customer."""
        return queries.revenue_by_customer(conn, limit)

    @app.get("/analytics/revenue/by-city", response_model=list[schemas.CityRevenue], tags=["Analytics"])
    def revenue_by_city(conn=Depends(get_db)):
        """Calls `queries.revenue_by_city()` -> fact_order JOIN dim_customer GROUP BY city."""
        return queries.revenue_by_city(conn)

    @app.get("/analytics/revenue/by-day", response_model=list[schemas.DailyRevenue], tags=["Analytics"])
    def revenue_by_day(start_date: Optional[date] = None, end_date: Optional[date] = None,
                       conn=Depends(get_db)):
        """Calls `queries.revenue_by_day()` -> fact_order JOIN dim_date."""
        return queries.revenue_by_day(conn, start_date and start_date.isoformat(),
                                      end_date and end_date.isoformat())

    @app.get("/analytics/rejections/reasons", response_model=list[schemas.RejectionReason],
             tags=["Analytics"], summary="Data-quality issues grouped by reason")
    def rejection_reasons(run_id: Optional[str] = None, conn=Depends(get_db)):
        """Calls `queries.rejection_reasons()` -> GROUP BY reason over rejected_records."""
        return queries.rejection_reasons(conn, run_id)

    # ---------- step 6: warehouse lookups ----------

    @app.get("/customers", response_model=list[schemas.Customer], tags=["Warehouse"])
    def list_customers(city: Optional[str] = None, limit: int = Query(100, ge=1, le=1000),
                       offset: int = Query(0, ge=0), conn=Depends(get_db)):
        """Calls `queries.list_customers()` -> dim_customer."""
        return queries.list_customers(conn, city, limit, offset)

    @app.get("/customers/{customer_id}", response_model=schemas.CustomerDetail, tags=["Warehouse"],
             summary="Customer with order totals and attribute change history")
    def get_customer(customer_id: str, conn=Depends(get_db)):
        """Calls `queries.get_customer()` -> dim_customer + fact_order totals + customer_change_log."""
        customer = queries.get_customer(conn, customer_id)
        if customer is None:
            raise HTTPException(404, f"Customer not found: {customer_id}")
        return customer

    @app.get("/orders", response_model=list[schemas.Order], tags=["Warehouse"])
    def list_orders(customer_id: Optional[str] = None, start_date: Optional[date] = None,
                    end_date: Optional[date] = None, limit: int = Query(100, ge=1, le=1000),
                    offset: int = Query(0, ge=0), conn=Depends(get_db)):
        """Calls `queries.list_orders()` -> fact_order JOIN dim_customer JOIN dim_date."""
        return queries.list_orders(conn, customer_id, start_date and start_date.isoformat(),
                                   end_date and end_date.isoformat(), limit, offset)

    # ---------- health ----------

    @app.get("/health", response_model=schemas.Health, tags=["Health"])
    def health(request: Request, conn=Depends(get_db)):
        """Runs SELECT 1 to prove the database is reachable."""
        conn.execute("SELECT 1")
        return {"status": "ok", "database": str(request.app.state.db_path)}


app = create_app()
