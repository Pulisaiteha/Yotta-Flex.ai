-- Total revenue (INR)
SELECT ROUND(COALESCE(SUM(amount_paise), 0) / 100.0, 2) AS total_revenue_inr
FROM fact_order;

-- Revenue by customer and city
SELECT c.name, c.city,
       ROUND(SUM(f.amount_paise) / 100.0, 2) AS revenue_inr,
       COUNT(*) AS order_count
FROM fact_order f
JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.customer_key, c.name, c.city
ORDER BY revenue_inr DESC;

-- Revenue by day
SELECT d.full_date,
       ROUND(SUM(f.amount_paise) / 100.0, 2) AS revenue_inr,
       COUNT(*) AS order_count
FROM fact_order f
JOIN dim_date d ON d.date_key = f.date_key
GROUP BY d.full_date
ORDER BY d.full_date;

-- Rejection reasons
SELECT reason, COUNT(*) AS records
FROM rejected_records
GROUP BY reason
ORDER BY records DESC;

-- Pipeline history
SELECT run_id, status, order_rows_read, accepted_orders,
       rejected_orders, skipped_orders, duration_seconds
FROM pipeline_runs
ORDER BY started_at DESC;

-- Revenue by city
SELECT c.city, ROUND(SUM(f.amount_paise) / 100.0, 2) AS revenue_inr, COUNT(*) AS order_count
FROM fact_order f
JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.city
ORDER BY revenue_inr DESC;

-- Reconciliation per run: run metrics vs what is actually stored (all *_ok columns should be 1)
SELECT r.run_id, r.status,
       r.order_rows_read = r.accepted_orders + r.rejected_orders + r.skipped_orders AS counts_ok,
       r.accepted_orders = (SELECT COUNT(*) FROM fact_order f WHERE f.source_run_id = r.run_id) AS fact_count_ok,
       r.accepted_amount_paise = (SELECT COALESCE(SUM(amount_paise), 0) FROM fact_order f
                                  WHERE f.source_run_id = r.run_id) AS fact_amount_ok,
       r.rejected_orders = (SELECT COUNT(*) FROM rejected_records x
                            WHERE x.run_id = r.run_id AND x.entity = 'order') AS reject_count_ok
FROM pipeline_runs r
ORDER BY r.started_at DESC;

-- Customer attribute history
SELECT customer_id, old_city, new_city, old_name, new_name, run_id, changed_at
FROM customer_change_log
ORDER BY changed_at;
