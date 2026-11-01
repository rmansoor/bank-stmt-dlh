-- Gold reporting views for the __CATALOG__ dashboard. Plain SQL on gold and silver tables, no model calls.
-- Run by notebook 03 after fact_transactions and recurring_series are rebuilt. Safe to run again.

-- Month by month cash flow. Closing balance is the bank's own closing figure. If a month has more than one
-- statement, a monthly one is preferred over a weekly one (a weekly statement can end in the next month), then the latest date wins.
CREATE OR REPLACE VIEW __CATALOG__.gold.monthly_flow AS
WITH f AS (
  SELECT date_trunc('month', txn_date) AS m,
    SUM(CASE WHEN direction = 'credit' AND category = 'client-receipts' THEN amount ELSE 0 END) AS client_receipts,
    SUM(CASE WHEN direction = 'credit' AND category IN ('founder-investment', 'investor-funding', 'founder-funding') THEN amount ELSE 0 END) AS funding_in,
    SUM(CASE WHEN direction = 'credit' AND category NOT IN ('client-receipts', 'founder-investment', 'investor-funding', 'founder-funding') THEN amount ELSE 0 END) AS other_in,
    SUM(CASE WHEN direction = 'debit' AND category = 'operations' THEN amount ELSE 0 END) AS founder_payout,
    SUM(CASE WHEN direction = 'debit' AND category <> 'operations' THEN amount ELSE 0 END) AS operating_spend
  FROM __CATALOG__.gold.fact_transactions WHERE account_type = 'bank' AND currency = 'INR' GROUP BY 1),
s AS (SELECT m, closing_balance, partial FROM (
        SELECT date_trunc('month', period_from) AS m, closing AS closing_balance, period_to < last_day(period_to) AS partial,
               ROW_NUMBER() OVER (PARTITION BY date_trunc('month', period_from) ORDER BY (statement_kind = 'monthly') DESC, period_to DESC) AS rn
        FROM __CATALOG__.silver.statements WHERE account_type = 'bank' AND currency = 'INR') WHERE rn = 1)
SELECT date_format(f.m, 'yyyy-MM') AS month, f.m AS month_start, ROUND(f.client_receipts, 2) AS client_receipts, ROUND(f.funding_in, 2) AS funding_in,
  ROUND(f.other_in, 2) AS other_in, ROUND(f.founder_payout, 2) AS founder_payout, ROUND(f.operating_spend, 2) AS operating_spend,
  ROUND(f.founder_payout + f.operating_spend, 2) AS total_outflow, s.closing_balance, s.partial
FROM f JOIN s ON f.m = s.m;

-- The same flow, one row per measure, for stacked charts.
CREATE OR REPLACE VIEW __CATALOG__.gold.monthly_flow_long AS
SELECT month, month_start, measure, amount FROM __CATALOG__.gold.monthly_flow
LATERAL VIEW STACK(5, 'Client receipts', client_receipts, 'Funding', funding_in, 'Other credits', other_in,
                   'Operating spend', operating_spend, 'Founder payout', founder_payout) t AS measure, amount;

-- Debits by category and month.
CREATE OR REPLACE VIEW __CATALOG__.gold.spend_by_category_month AS
SELECT date_format(txn_date, 'yyyy-MM') AS month, category, category_type, ROUND(SUM(amount), 2) AS amount, COUNT(*) AS txns
FROM __CATALOG__.gold.fact_transactions WHERE account_type = 'bank' AND currency = 'INR' AND direction = 'debit' GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW __CATALOG__.gold.category_totals AS
SELECT category, category_type, ROUND(SUM(amount), 2) AS amount, COUNT(*) AS txns
FROM __CATALOG__.gold.fact_transactions WHERE account_type = 'bank' AND currency = 'INR' AND direction = 'debit' GROUP BY 1, 2;

-- Recurring and yearly payments with a status against the latest transaction date.
CREATE OR REPLACE VIEW __CATALOG__.gold.upcoming_payments AS
SELECT counterparty_clean AS payee, category, cadence, typical_amount, occurrences, last_seen, next_expected, source,
  CASE WHEN ended THEN 'ended'
       WHEN next_expected < (SELECT MAX(txn_date) FROM __CATALOG__.gold.fact_transactions) THEN 'lapsed'
       ELSE 'upcoming' END AS status
FROM __CATALOG__.gold.recurring_series WHERE direction = 'debit' AND typical_amount >= 100;

CREATE OR REPLACE VIEW __CATALOG__.gold.founder_monthly AS
SELECT date_format(txn_date, 'yyyy-MM') AS month, category,
  ROUND(SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END), 2) AS paid_to_founder,
  ROUND(SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END), 2) AS received_from_founder
FROM __CATALOG__.gold.fact_transactions WHERE account_type = 'bank' AND currency = 'INR' AND is_founder GROUP BY 1, 2;

-- Funding received by who, for the funding page.
CREATE OR REPLACE VIEW __CATALOG__.gold.funding_received AS
SELECT date_format(txn_date, 'yyyy-MM') AS month, category, ROUND(SUM(amount), 2) AS amount, COUNT(*) AS txns
FROM __CATALOG__.gold.fact_transactions
WHERE account_type = 'bank' AND currency = 'INR' AND direction = 'credit' AND category IN ('founder-investment', 'investor-funding', 'founder-funding') GROUP BY 1, 2;

-- Data quality: unlabelled payments and the reconciliation status of every statement file.
CREATE OR REPLACE VIEW __CATALOG__.gold.review_summary AS
SELECT category, direction, COUNT(*) AS txns, ROUND(SUM(amount), 2) AS amount
FROM __CATALOG__.gold.fact_transactions WHERE category IN ('human-intervention-required', 'unclassified') GROUP BY 1, 2;

CREATE OR REPLACE VIEW __CATALOG__.gold.statement_status AS
WITH b AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY file_sha256 ORDER BY ingested_at DESC) AS rn FROM __CATALOG__.bronze.statement_files),
d AS (SELECT file_sha256, COUNT(*) AS lines, SUM(CASE WHEN is_duplicate THEN 1 ELSE 0 END) AS duplicate_lines
      FROM __CATALOG__.silver.transaction_sources GROUP BY file_sha256)
SELECT regexp_extract(b.object_key, '[^/]+$', 0) AS file, b.statement_kind, b.size_bytes, b.ingested_at, b.reconciled,
  s.period_from, s.period_to, s.txn_count, d.duplicate_lines, q.errors
FROM b
LEFT JOIN __CATALOG__.silver.statements s ON s.file_sha256 = b.file_sha256
LEFT JOIN __CATALOG__.silver.quarantine q ON q.file_sha256 = b.file_sha256
LEFT JOIN d ON d.file_sha256 = b.file_sha256
WHERE b.rn = 1;

-- Line items that were already loaded from another file (weekly and monthly overlap), one row per file and original.
CREATE OR REPLACE VIEW __CATALOG__.gold.duplicate_lines AS
SELECT source_file AS file, statement_kind, duplicate_of_file AS duplicate_of, COUNT(*) AS lines, MIN(first_seen_at) AS tagged_at
FROM __CATALOG__.silver.transaction_sources WHERE is_duplicate GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW __CATALOG__.gold.category_source_mix AS
SELECT category_source, COUNT(*) AS txns, ROUND(SUM(amount), 2) AS amount FROM __CATALOG__.gold.fact_transactions WHERE account_type = 'bank' AND currency = 'INR' GROUP BY 1;

-- Headline figures. Outflow = mean total debits (founder payouts included) of the last three full months.
CREATE OR REPLACE VIEW __CATALOG__.gold.kpis AS
WITH full AS (SELECT total_outflow FROM __CATALOG__.gold.monthly_flow WHERE NOT partial ORDER BY month_start DESC LIMIT 3),
burn AS (SELECT ROUND(AVG(total_outflow), 0) AS monthly_outflow FROM full),
cash AS (SELECT closing AS cash, period_to AS as_of FROM __CATALOG__.silver.statements WHERE account_type = 'bank' AND currency = 'INR' ORDER BY period_to DESC LIMIT 1)
SELECT cash.cash, cash.as_of, burn.monthly_outflow, ROUND(cash.cash / burn.monthly_outflow, 1) AS runway_months,
  (SELECT ROUND(SUM(funding_in), 2) FROM __CATALOG__.gold.monthly_flow) AS funding_received,
  (SELECT ROUND(SUM(client_receipts), 2) FROM __CATALOG__.gold.monthly_flow) AS client_receipts
FROM cash CROSS JOIN burn;

-- Cash flow by day, for the date-range page of the dashboard. One row per day and category; the dashboard's date filter
-- (field txn_date) narrows every widget built on these views. Plain SQL, no model calls.
CREATE OR REPLACE VIEW __CATALOG__.gold.cashflow_daily AS
SELECT txn_date, category, category_type,
  ROUND(SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END), 2) AS money_in,
  ROUND(SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END), 2) AS money_out,
  ROUND(SUM(signed_amount), 2) AS net, COUNT(*) AS txns
FROM __CATALOG__.gold.fact_transactions WHERE account_type = 'bank' AND currency = 'INR' GROUP BY txn_date, category, category_type;

-- The bank's running balance after the last payment of each day.
CREATE OR REPLACE VIEW __CATALOG__.gold.balance_daily AS
SELECT txn_date, max_by(balance, seq) AS closing_balance
FROM __CATALOG__.gold.fact_transactions WHERE account_type = 'bank' AND currency = 'INR' GROUP BY txn_date;
