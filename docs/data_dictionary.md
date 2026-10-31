# Data dictionary

## Source schemas

### customers.csv (grain: one row per customer)
| Column | Type | Required | Rule |
|---|---|---|---|
| customer_id | text | yes | business key, unique within file |
| name | text | yes | whitespace collapsed |
| city | text | yes | whitespace collapsed, Title Case |

Extra columns are ignored and reported; a missing required column fails the run.

### orders.json (grain: one object per order)
| Field | Type | Required | Rule |
|---|---|---|---|
| order_id | string/number | yes | business key, unique within batch and warehouse |
| customer_id | string | yes | must exist in file or in `dim_customer` |
| order_date | ISO date `YYYY-MM-DD` | yes | valid calendar date, not in the future |
| amount | number or numeric string (INR) | yes | > 0, at most 2 decimals |

## Target schema

### dim_customer (one row per customer, current state)
- `customer_key` INTEGER PK: warehouse surrogate key.
- `customer_id` TEXT NOT NULL UNIQUE: source business identifier.
- `name`, `city` TEXT NOT NULL: cleaned descriptive attributes.

### customer_change_log (one row per attribute change)
- `change_key` INTEGER PK.
- `customer_id`, `run_id` TEXT NOT NULL.
- `old_name`, `old_city`, `new_name`, `new_city` TEXT NOT NULL.
- `changed_at` TEXT NOT NULL: ISO-8601 UTC run start.

### dim_date (one row per calendar date)
- `date_key` INTEGER PK: deterministic `YYYYMMDD`.
- `full_date` TEXT NOT NULL UNIQUE: ISO date.
- `year`, `month`, `day` INTEGER NOT NULL.

### fact_order (one row per accepted unique order)
- `order_key` INTEGER PK: warehouse surrogate key.
- `order_id` TEXT NOT NULL UNIQUE: source business identifier.
- `customer_key` INTEGER NOT NULL FK → dim_customer.
- `date_key` INTEGER NOT NULL FK → dim_date.
- `amount_paise` INTEGER NOT NULL, CHECK > 0: derived = amount × 100.
- `source_run_id` TEXT NOT NULL: run that first loaded the order.

### rejected_records (one row per rejected source row)
- `rejection_key` INTEGER PK.
- `run_id` TEXT NOT NULL.
- `entity` TEXT NOT NULL: `customer` or `order`.
- `source_name` TEXT NOT NULL: source file name.
- `source_row_number` INTEGER NOT NULL: CSV line (header = 1) or JSON array position (from 1).
- `record_id` TEXT NULL: business ID if present.
- `reason` TEXT NOT NULL: `;`-separated reason codes.
- `raw_record` TEXT NOT NULL: original record as JSON.

### pipeline_runs (one row per execution, including failures)
- `run_id` TEXT PK; `started_at`, `finished_at` ISO-8601 UTC.
- `status`: `SUCCESS`, `COMPLETED_WITH_REJECTS`, `FAILED_RECONCILIATION`, `FAILED`.
- `customers_file`, `orders_file`: source file names.
- `customer_rows_read`, `order_rows_read`, `accepted_orders`, `rejected_orders`, `rejected_customers`,
  `skipped_orders` (already loaded), `late_arriving_orders` (dated before newest loaded order).
- `accepted_amount_paise`: sum of amounts loaded by this run (used for amount reconciliation).
- `duration_seconds` REAL; `message`: reconciliation result or failure reason.

## Reason codes

`missing_customer_columns:<cols>`, `missing_<field>`, `duplicate_customer_id`,
`missing_order_fields:<fields>`, `duplicate_order_id_in_batch`, `invalid_order_date`,
`order_date_in_future`, `invalid_amount`, `amount_not_finite`, `amount_must_be_positive`,
`amount_more_than_2_decimals`, `unknown_customer_id`.
