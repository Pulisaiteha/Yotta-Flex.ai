# Design notes and trade-offs

## Key decisions

- **Schema vs business validation are separate.** File-level problems (unreadable JSON, missing
  columns) stop the run because every row would be wrong. Row-level problems quarantine only that row.
- **Quarantine, don't drop.** Every rejected row keeps its file, row number, reason and raw content, so
  it can be traced and fixed at the source.
- **Idempotency by business key.** `order_id` is checked before insert and protected by a `UNIQUE`
  constraint. Reruns skip loaded orders; revenue never double counts.
- **One transaction per run.** A crash mid-load leaves the warehouse unchanged; the failure is written
  afterwards in a separate transaction so operators always see it.
- **Integer paise.** Avoids float rounding; amounts with more than 2 decimals are rejected rather than
  silently rounded (0.001 would otherwise become 0 and break the `CHECK`).
- **Customer history.** `dim_customer` holds current state; `customer_change_log` keeps old values.
  This is lighter than full SCD Type 2 while still not losing history.
- **Shared query layer.** `queries.py` is used by both the API and the report so numbers always match.
- **SQLite + FastAPI.** Zero-setup, runs from a clean clone; FastAPI gives typed validation and Swagger for free.

## Performance

100,000 orders load in ~5.4 s (~18.5k orders/s); a rerun takes ~1.8 s.

**Identified optimization:** the order loop runs two lookups per row (`SELECT 1 FROM fact_order` for
idempotency and `SELECT customer_key FROM dim_customer`). Preloading existing `order_id`s into a set and
customer IDs into a `{customer_id: customer_key}` dict, then inserting with `executemany` in chunks,
removes ~200k round trips per 100k rows. For much larger inputs, also stream the JSON (e.g. `ijson`)
instead of loading the whole array into memory.

## Known limitations

- Re-sent orders with corrected values are skipped, not updated (no upsert of facts).
- `late_arriving_orders` compares against the newest date already in the warehouse, not against
  ordering within the same batch.
- Whole files are read into memory.
- SQLite allows one writer at a time; concurrent API-triggered runs are serialized by SQLite locks.
- `POST /pipeline/runs` reads server-side paths; fine locally, would need path restrictions or auth if exposed.

## Next steps

- PostgreSQL + Alembic migrations; object storage for raw files with checksums to skip identical files.
- Schedule with Airflow/Prefect; alert on `FAILED` or reconciliation failure.
- Structured JSON logs and metrics export; API authentication.
- Full SCD Type 2 for customers if point-in-time reporting is needed.
