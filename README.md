# YottaFlex Data Pipeline

A small end-to-end data platform: it ingests **customers (CSV)** and **orders (JSON)**, validates and
transforms them, loads a **SQLite star schema**, and exposes analytics and run monitoring through a
**CLI**, **SQL**, and a **REST API with Swagger docs**.

Stack: Python 3.10+, SQLite (stdlib), FastAPI, pytest. No external services or credentials needed.

> **New here? Read [INTERVIEWER_GUIDE.md](INTERVIEWER_GUIDE.md) first.** It is a 15-minute
> walkthrough: start the server, open http://127.0.0.1:8000, create customer/order data in the
> browser, run the pipeline and see every API call and Python function involved.

## What it does

1. Reads customers from CSV and orders from JSON (from disk or via API upload).
2. Checks file-level schema (missing required columns fail the run; extra columns are reported).
3. Validates required fields, nulls, types, dates, positive 2-decimal amounts, future dates and customer references.
4. Detects duplicate order IDs and duplicate customer IDs within a batch.
5. Loads valid customers, dates and orders into a dimensional model inside one transaction.
6. Quarantines rejected records with reason, source file and source row number.
7. Records every run (including failed ones) with counts, amounts, duration and failure reason.
8. Keeps customer attribute history instead of silently overwriting it.
9. Reconciles source-to-target counts **and** amounts per run.

## Project structure

```
src/
  pipeline/
    readers.py      # read CSV/JSON, header schema check
    validation.py   # schema + business rules -> list of reason codes
    transform.py    # cleaning, rupees -> integer paise
    database.py     # DDL, customer upsert with change log, date dimension
    runner.py       # orchestration, transaction, run metrics, logging, CLI
    metrics.py      # reconciliation counts
    queries.py      # read-only analytics/monitoring queries (shared by API + report)
  api/
    main.py         # FastAPI routes (each route documents which functions it calls)
    schemas.py      # request/response models (drive Swagger)
    source_files.py # demo steps 1-2: writes customers.csv / orders.json like a source system
    static/index.html # step-by-step demo page served at /
  logging_config.py # log format: request id + module.function:line
scripts/
  generate_data.py  # seeded synthetic data + separate expected labels
  report.py         # print analytics + reconciliation
  export_openapi.py # write docs/openapi.json
sql/analytics.sql   # same analytics as plain SQL
data/input/         # small sample with deliberate edge cases
data/expected/      # expected results for the sample (kept apart from input)
docs/               # architecture, data dictionary, design notes, API spec, Postman, sample output
tests/              # unit, pipeline, edge-case and API tests
```

## Setup

### Windows PowerShell

```powershell
cd yotta-data-pipeline
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS / Linux

```bash
cd yotta-data-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the pipeline (CLI)

```bash
python -m src.pipeline.runner --customers data/input/customers.csv --orders data/input/orders.json --db data/generated/pipeline.db
```

Prints a JSON run summary (see [docs/sample_run_output.json](docs/sample_run_output.json)) and logs
start/finish lines. Exit code is non-zero if the run fails; the failure is still stored in `pipeline_runs`.

## Run the API

```bash
uvicorn src.api.main:app --reload
```

- Demo page: http://127.0.0.1:8000 (see [INTERVIEWER_GUIDE.md](INTERVIEWER_GUIDE.md))
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- OpenAPI spec: http://127.0.0.1:8000/openapi.json (also committed at [docs/openapi.json](docs/openapi.json); regenerate with `python scripts/export_openapi.py`)
- Postman: import [docs/postman_collection.json](docs/postman_collection.json)

The database path defaults to `data/generated/pipeline.db` (override with `PIPELINE_DB_PATH`); the demo
page's input files live in `data/workspace/` (override with `PIPELINE_SOURCE_DIR`).

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/demo/reset` | Demo only: empty input files and warehouse |
| POST | `/source/load-sample` | Copy the bundled sample into `data/workspace/` |
| GET/POST | `/source/customers` | Show / add a row in `customers.csv` |
| GET/POST | `/source/orders` | Show / append an order in `orders.json` |
| GET | `/health` | Liveness + DB check |
| POST | `/pipeline/runs` | Run on server files (JSON body: `customers_path`, `orders_path`; empty body = sample data) |
| POST | `/pipeline/runs/upload` | Upload `customers_file` + `orders_file` (multipart) and run |
| GET | `/pipeline/runs?status=&limit=` | Run history (newest first) |
| GET | `/pipeline/runs/{run_id}` | One run: status, counts, duration, failure message |
| GET | `/pipeline/runs/{run_id}/rejections?entity=&limit=&offset=` | Quarantined rows with source row number and raw record |
| GET | `/pipeline/runs/{run_id}/reconciliation` | Count + amount reconciliation checks |
| GET | `/analytics/revenue/summary` | Total revenue, orders, customers, AOV |
| GET | `/analytics/revenue/by-customer?limit=` | Revenue per customer |
| GET | `/analytics/revenue/by-city` | Revenue per city |
| GET | `/analytics/revenue/by-day?start_date=&end_date=` | Daily revenue |
| GET | `/analytics/rejections/reasons?run_id=` | Data-quality issues by reason |
| GET | `/customers?city=&limit=&offset=` | Customer dimension |
| GET | `/customers/{customer_id}` | Customer totals + change history |
| GET | `/orders?customer_id=&start_date=&end_date=` | Loaded orders |

Error codes: `404` missing file / run / customer, `422` malformed or schema-invalid input. Failed runs
return their `run_id` in the error body so they can be looked up.

## Logs

Logs go to the console and to `logs/pipeline.log` (rotated at 5 MB). Each line shows the request ID
and the exact `module.function:line` that wrote it:

```
2026-10-31 15:35:16,087 | DEBUG | req=be7849 | src.pipeline.runner.run_pipeline:171 | order row 1 O500 -> validate_order() OK -> ...
```

- `LOG_LEVEL=INFO` (default): requests, pipeline steps 1–7, query row counts, failures.
- `LOG_LEVEL=DEBUG`: also one line per order (accepted / rejected / skipped) and DB connection details.
- CLI: `python -m src.pipeline.runner --log-level DEBUG`.

A full annotated example of every API call is in [docs/sample_api_logs.log](docs/sample_api_logs.log).

## Run tests

```bash
pytest -q
```

34 tests cover: happy path, empty batch, malformed JSON, nulls/blanks, duplicate orders and customers,
missing/extra columns (schema change), partial failure + rollback + retry, late-arriving data, invalid
reference key, rerun idempotency, sub-paise amounts, future dates, customer history, reconciliation,
generator labels vs pipeline output, the bundled sample vs its expected file, every API endpoint and the demo-page flow.

## Run analytics

```bash
python scripts/report.py --db data/generated/pipeline.db
```

Sample output: [docs/sample_report.txt](docs/sample_report.txt). Plain SQL: [sql/analytics.sql](sql/analytics.sql).

## Generate a larger dataset and measure performance

```bash
python scripts/generate_data.py --customers 1000 --orders 100000 --invalid-rate 0.05 --seed 42
python -m src.pipeline.runner --customers data/generated/input/customers.csv --orders data/generated/input/orders.json --db data/generated/perf.db
```

Input goes to `data/generated/input/`; expected labels (counts per reject reason, rejected row numbers)
go to `data/generated/expected/expected_labels.json`. The generator injects labelled invalid rows
(unknown customer, non-positive / non-numeric / null / 3-decimal amount, bad date, missing field,
in-batch duplicate), boundary amounts (0.01, 999999.99), out-of-order dates and two bad customer rows.

Measured on a Windows laptop (Python 3.14):

| Run | Orders | Result | Time | Throughput |
|---|---|---|---|---|
| First load | 100,000 | 95,028 accepted / 4,972 rejected (matches labels) | 5.4 s | ~18.5k orders/s |
| Rerun same input | 100,000 | 0 accepted / 95,028 skipped | 1.8 s | ~57k orders/s |

See [docs/design_notes.md](docs/design_notes.md) for the optimization identified.

## Naming conventions

- `snake_case` for all tables and columns; `dim_` / `fact_` prefixes for the star schema.
- Surrogate keys end in `_key`; source business identifiers end in `_id`.
- `date_key` is a deterministic `YYYYMMDD` integer.
- `run_id` = `run_` + 12 hex chars; stored on facts (`source_run_id`), rejects and run rows.
- Timestamps are ISO-8601 UTC (`started_at`, `finished_at`, `changed_at`).
- Rejected rows store `source_name` (file name) and `source_row_number` (CSV rows count the header as row 1; JSON rows start at 1).
- Reason codes are `snake_case`; multiple reasons are joined with `;`.

## Retry, restart and backfill

- **Retry / restart:** a run is one transaction. If it crashes, nothing is half-written; a `FAILED` row with
  the error message is recorded. Simply rerun the same command.
- **Rerun same input:** orders already in `fact_order` are counted as `skipped`, never inserted twice
  (`order_id` is also `UNIQUE` as a safety net).
- **Backfill / late data:** run the pipeline on older files at any time. Older dates are inserted into
  `dim_date` as needed and the orders are flagged as `late_arriving_orders` in the run metrics.
- **Corrections:** a changed order for an existing `order_id` is skipped, not updated (documented limitation).

## Documentation

- [docs/architecture.md](docs/architecture.md) — flow diagram, user flow and processing contract
- [docs/data_dictionary.md](docs/data_dictionary.md) — source and target schemas, keys, nullability
- [docs/design_notes.md](docs/design_notes.md) — decisions, trade-offs, limitations, next steps
