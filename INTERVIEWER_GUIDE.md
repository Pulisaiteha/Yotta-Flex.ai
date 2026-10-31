# Interviewer guide: start here

This guide takes you from a fresh clone to a full demo in about **15 minutes**. You'll create the
input data yourself in a browser page, run the pipeline, and see the results. Each step names the
API that is called and the Python function that does the work, so you can open the code alongside.

---

## 1. What the project does (the business story)

An online store has **two source systems**:

| Source system | Exports | Example |
|---|---|---|
| CRM | `customers.csv` | `C101,Ravi,Hyderabad` |
| Order system | `orders.json` | `{"order_id": "O500", "customer_id": "C101", "order_date": "2026-10-28", "amount": 1200}` |

Real exports contain mistakes: unknown customers, negative amounts, impossible dates, duplicates,
missing values. The **pipeline**:

1. **reads** both files,
2. **validates** every row against schema and business rules,
3. **transforms** good rows (cleans text, converts ₹ to integer paise),
4. **loads** them into a small reporting **warehouse** (star schema in SQLite),
5. **quarantines** bad rows with the exact reason and source row number,
6. **records** every run (counts, duration, status, failure reason) and **reconciles** input against output,
7. **serves** revenue reports and monitoring through a REST API.

```
 customers.csv ─┐                            ┌─► dim_customer ┐
                ├─► read ─► validate ─► transform ─► dim_date ├─► reports (/analytics)
 orders.json  ──┘              │                └─► fact_order┘
                               └─► rejected_records ─► monitoring (/pipeline/runs/...)
                                    pipeline_runs  ─┘
```

---

## 2. Setup (about 3 minutes)

Requires **Python 3.10+**.

**Windows (PowerShell)**
```powershell
cd yotta-data-pipeline
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux**
```bash
cd yotta-data-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional check that everything works: `pytest -q` should report **34 passed**.

---

## 3. Start the app

```bash
uvicorn src.api.main:app --reload
```

Then open **http://127.0.0.1:8000** in a browser. That's the demo page this guide follows.

| URL | What it is |
|---|---|
| http://127.0.0.1:8000 | Step-by-step demo page |
| http://127.0.0.1:8000/docs | Swagger: every endpoint, try-it-out |
| `logs/pipeline.log` | Server log: every function call with file, function and line |

Optional: to watch the server log live, open a second terminal.
- Windows: `Get-Content logs\pipeline.log -Wait -Tail 30`
- macOS/Linux: `tail -f logs/pipeline.log`

For one log line per order, start the server with `LOG_LEVEL=DEBUG`. On Windows that's
`$env:LOG_LEVEL="DEBUG"` before `uvicorn`.

The page has a panel on the right called **"API calls made by this page"**. Every button click
appears there with the HTTP call and the server functions it ran.

---

## 4. Walkthrough: follow the page from top to bottom

### Step 0: Start clean

**Do:** click **Reset everything**, then **Load sample data**.

**What happens in the code:**
```
POST /demo/reset          → main.reset_demo()   → source_files.clear()  + database.clear_all_tables()
POST /source/load-sample  → main.load_sample()  → source_files.load_sample()
                                                    copies data/input/customers.csv + orders.json
                                                    into data/workspace/
```

**Result:** Steps 1 and 2 now show 3 customers and 11 orders. You can skip the sample and type your
own data in Steps 1–2 instead.

### Step 1: Customer data → `customers.csv`

**Do:** add a customer, for example `C104 / Meena / Pune`, and click **Save to customers.csv**.

**What happens:**
```
POST /source/customers {"customer_id":"C104","name":"Meena","city":"Pune"}
  → main.add_source_customer()  → source_files.save_customer()  → writes data/workspace/customers.csv
```
Open **Show raw file content** to see the real CSV text.

**Why there's no validation here:** this step plays the *source system*, and source systems send bad
data. Validation belongs to the pipeline (Step 3), so it can be traced and reported.

### Step 2: Order data → `orders.json`

**Do:** click a quick-fill button (✓ green = valid, ✗ red = deliberately bad), then **Save to orders.json**.
Add one good order for C104 and one or two bad ones.

**What happens:**
```
POST /source/orders {"order_id":"O611","customer_id":"C104","order_date":"...","amount":"499.99"}
  → main.add_source_order()  → source_files.save_order()  → appends to data/workspace/orders.json
```
Blank fields are saved as `null`, the way a broken export would contain them.

**What the sample contains** (orders 1–11, before you add your own):

| Row | Order | Problem | Pipeline result |
|---|---|---|---|
| 1 | O500 | none | ✅ accepted, ₹1200 |
| 2 | O501 | none | ✅ accepted, ₹800 |
| 3 | O502 | customer C999 doesn't exist | ❌ `unknown_customer_id` |
| 4 | O503 | amount sent as text "349.50" | ✅ accepted, ₹349.50 |
| 5 | O500 | same ID as row 1 | ❌ `duplicate_order_id_in_batch` |
| 6 | O504 | amount is null | ❌ `missing_amount` |
| 7 | O505 | date 2026-02-30 doesn't exist | ❌ `invalid_order_date` |
| 8 | O506 | amount −50 | ❌ `amount_must_be_positive` |
| 9 | O507 | amount 0.001 (less than 1 paisa) | ❌ `amount_more_than_2_decimals` |
| 10 | O508 | amount field missing | ❌ `missing_order_fields:amount` |
| 11 | O509 | ₹0.01 (smallest valid amount) | ✅ accepted |

The expected answers are also stored separately in `data/expected/sample_expected.json`, and the test
`test_bundled_sample_matches_expected_file` checks the pipeline against them.

### Step 3: Run the pipeline

**Do:** click **Run pipeline**.

**What happens.** This is the core of the project: `src/pipeline/runner.py → run_pipeline()`.
```
POST /pipeline/runs {"customers_path": "data/workspace/customers.csv", "orders_path": "data/workspace/orders.json"}
  → main.create_run() → main.execute_run() → runner.run_pipeline()
```
| # | What | Function (file) | Writes to |
|---|---|---|---|
| 1 | Open DB, create tables if missing | `connect()`, `initialize()` (database.py) | |
| 2 | Read files; a missing required column fails the whole run | `read_customers()`, `read_orders()` (readers.py), `check_customer_header()` (validation.py) | |
| — | **BEGIN TRANSACTION** | `with conn:` in runner.py | |
| 3 | Validate and clean each customer | `validate_customer()` (validation.py), `clean_customer()` (transform.py) | |
| 4 | Insert or update customers; log changed attributes | `upsert_customer()` (database.py) | `dim_customer`, `customer_change_log` |
| 5 | For each order: duplicate check → `validate_order()` → `clean_order()` (₹ to paise) → skip if already loaded → `ensure_date()` → insert | validation.py, transform.py, database.py | `fact_order`, `dim_date`, `rejected_records` |
| 6 | Store customer rejections | `record_rejection()` (runner.py) | `rejected_records` |
| 7 | Reconcile: input = accepted + rejected + skipped; record the run | `summarize_counts()` (metrics.py) | `pipeline_runs` |
| — | **COMMIT**: on any error, everything rolls back and `record_failed_run()` stores a FAILED run | runner.py | `pipeline_runs` |

> **Seeing `accepted_orders: 0` and `skipped_orders_already_loaded: 4`?** The warehouse already holds
> those orders from an earlier run, and *Load sample data* only replaces the input files. That's the
> rerun protection working. Click **Reset everything** → **Load sample data** → **Run pipeline** for a fresh first load.

**Result with the sample only, on a fresh warehouse:** 11 read = **4 accepted + 7 rejected + 0 skipped**, reconciliation
PASSED, revenue **₹2349.51**. Status is `COMPLETED_WITH_REJECTS`: good rows are loaded, and bad
rows don't block them.

### Step 4: Monitoring

The page loads three things automatically after the run:

| Panel | API → function | What to look at |
|---|---|---|
| Rejected rows | `GET /pipeline/runs/{id}/rejections` → `main.get_rejections()` → `queries.list_rejections()` | Every bad row with file, **row number**, ID and reason. Fully traceable to the source. |
| Reconciliation | `GET /pipeline/runs/{id}/reconciliation` → `main.get_reconciliation()` → `queries.reconcile_run()` | 4 checks: counts add up; accepted count = rows in `fact_order`; rejected count = rows in `rejected_records`; **amount** loaded = sum in `fact_order` |
| Run history | `GET /pipeline/runs` → `main.list_runs()` → `queries.list_runs()` | One row per run, including failed runs |

### Step 5: Business reports

| Report | API → function | SQL idea |
|---|---|---|
| Total revenue, orders, customers, AOV | `/analytics/revenue/summary` → `queries.revenue_summary()` | SUM/COUNT on `fact_order` |
| Revenue by customer | `/analytics/revenue/by-customer` → `queries.revenue_by_customer()` | `fact_order JOIN dim_customer` |
| Revenue by day | `/analytics/revenue/by-day` → `queries.revenue_by_day()` | `fact_order JOIN dim_date` |
| Data-quality issues | `/analytics/rejections/reasons` → `queries.rejection_reasons()` | GROUP BY reason |

Money is stored as integer **paise** and only converted to ₹ for display, so totals have no
floating-point rounding errors.

### Step 6: Look up a customer

**Do:** type `C103` and click **Look up**.
`GET /customers/C103` → `main.get_customer()` → `queries.get_customer()`: 2 orders, ₹349.51, change history empty.
An unknown ID returns **404**.

### Step 7: Edge cases to try

| Try | Expected | Proves |
|---|---|---|
| Click **Run pipeline** again with no changes | accepted **0**, skipped **4**, revenue still ₹2349.51 | **Idempotency**: reruns never double-count |
| Add one new valid order, run again | accepted **1**, skipped **4**, the 7 bad rows rejected again | Incremental load |
| Step 1: save `C101 / Ravi / Pune`, run, look up `C101` | city = Pune, history row Hyderabad → Pune | **History preserved**, not silently overwritten |
| Add a valid order dated earlier than loaded ones (e.g. `2026-09-01`), run | `late_arriving_orders: 1` in the run result | Late or out-of-order data handled and flagged |
| Swagger: `POST /pipeline/runs` with `{"customers_path":"nope.csv","orders_path":"nope.json"}` | 404 with a `run_id`; run history shows **FAILED** with the message | Failures are recorded, nothing is half-written |

---

## 5. Automated tests

```bash
pytest -q          # 34 tests
```

| File | Covers |
|---|---|
| `tests/test_validation.py` | Unit tests of the business rules |
| `tests/test_pipeline.py` | Happy path; rerun doesn't duplicate |
| `tests/test_failures.py` | Malformed JSON; JSON not an array |
| `tests/test_edge_cases.py` | Empty batch, nulls, duplicates (orders and customers), invalid reference, missing or extra columns (schema change), malformed file recorded as FAILED, **crash mid-load → rollback → retry succeeds**, late-arriving data, sub-paise amount, future date, customer history, reconciliation, generator labels = pipeline output, sample = expected file |
| `tests/test_api.py` | Every endpoint, including the demo flow from this guide |

## 6. Larger dataset and performance

```bash
python scripts/generate_data.py --customers 1000 --orders 100000 --seed 42
python -m src.pipeline.runner --customers data/generated/input/customers.csv --orders data/generated/input/orders.json --db data/generated/perf.db
```
The generator uses a fixed seed and writes labelled bad rows. The expected answers go to
`data/generated/expected/expected_labels.json`, separate from the input. Measured: about **5.4 s** for
100k orders (~18k/s); a rerun takes ~1.8 s. See `docs/design_notes.md` for the optimization identified.

---

## 7. Code tour (reading order)

```
Browser / Swagger
   │
src/api/main.py           routes; each route makes ONE call into the layer below
src/api/schemas.py        request/response models (become the Swagger docs)
src/api/source_files.py   steps 1–2: writes customers.csv / orders.json (simulated source systems)
src/api/static/index.html the demo page
   │
src/pipeline/runner.py     run_pipeline(): orchestration, transaction, run metrics   ← start here
src/pipeline/readers.py    read CSV/JSON, header schema check
src/pipeline/validation.py rules → list of reason codes (pure functions, easy to unit test)
src/pipeline/transform.py  cleaning, ₹ → paise
src/pipeline/database.py   table definitions (SCHEMA), upsert_customer, ensure_date
src/pipeline/metrics.py    reconciliation counts
src/pipeline/queries.py    every read-only SQL query used by the API and scripts/report.py
src/logging_config.py      log format: request ID + module.function:line
```

**Endpoint → function map**

| Endpoint | Route function (main.py) | Calls |
|---|---|---|
| `GET /` | `demo_page` | serves `static/index.html` |
| `POST /demo/reset` | `reset_demo` | `source_files.clear`, `database.clear_all_tables` |
| `POST /source/load-sample` | `load_sample` | `source_files.load_sample` |
| `GET/POST /source/customers` | `get_source_customers` / `add_source_customer` | `source_files.read_customers_file` / `save_customer` |
| `GET/POST /source/orders` | `get_source_orders` / `add_source_order` | `source_files.read_orders_file` / `save_order` |
| `POST /pipeline/runs` | `create_run` | `execute_run` → `runner.run_pipeline` |
| `POST /pipeline/runs/upload` | `upload_run` | `save_upload` ×2 → `runner.run_pipeline` |
| `GET /pipeline/runs` | `list_runs` | `queries.list_runs` |
| `GET /pipeline/runs/{id}` | `get_run` | `queries.get_run` |
| `GET /pipeline/runs/{id}/rejections` | `get_rejections` | `queries.get_run`, `queries.list_rejections` |
| `GET /pipeline/runs/{id}/reconciliation` | `get_reconciliation` | `queries.reconcile_run` |
| `GET /analytics/revenue/summary` | `revenue_summary` | `queries.revenue_summary` |
| `GET /analytics/revenue/by-customer` | `revenue_by_customer` | `queries.revenue_by_customer` |
| `GET /analytics/revenue/by-city` | `revenue_by_city` | `queries.revenue_by_city` |
| `GET /analytics/revenue/by-day` | `revenue_by_day` | `queries.revenue_by_day` |
| `GET /analytics/rejections/reasons` | `rejection_reasons` | `queries.rejection_reasons` |
| `GET /customers`, `/customers/{id}`, `/orders` | `list_customers`, `get_customer`, `list_orders` | `queries.*` with the same names |
| `GET /health` | `health` | `SELECT 1` |

The same pipeline can also be run without the API: `python -m src.pipeline.runner`. Results can be
printed with `python scripts/report.py`.

---

## 8. Challenge requirements → where they are met

| Requirement | Where |
|---|---|
| Two sources, schemas, keys, assumptions | `docs/data_dictionary.md` |
| Sample data + scalable generator, fixed seed, labels kept separate | `data/input/`, `data/expected/`, `scripts/generate_data.py` |
| Ingestion, validation, transformation, publishing | `readers.py`, `validation.py`, `transform.py`, `runner.py` |
| Duplicate handling, quarantine path | `runner.py` (in-batch duplicates, already-loaded skip), `rejected_records` |
| Run ID, start/end time, counts, rejects, failure reason, logging | `pipeline_runs`, `record_failed_run()`, `logging_config.py`, `logs/pipeline.log` |
| Idempotent load, retry/restart/backfill | `UNIQUE(order_id)` + skip check; single transaction; README "Retry, restart and backfill" |
| History instead of silent overwrite | `customer_change_log` |
| Tests: happy path, empty, malformed, duplicates, nulls, domain edge cases | `tests/` (section 5) |
| Reconciliation | `queries.reconcile_run()`, `sql/analytics.sql`, Step 4 |
| Performance measurement + optimization | Section 6, `docs/design_notes.md` |
| Design and trade-off notes, limitations | `docs/design_notes.md` |

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope Process Bypass` first |
| Run pipeline → 404 "file not found" | Load the sample (Step 0) or save at least one customer and one order first |
| Port 8000 busy | `uvicorn src.api.main:app --port 8001` |
| Want a completely fresh start | Click **Reset everything**, or stop the server and delete `data/generated/pipeline.db` and `data/workspace/` |
