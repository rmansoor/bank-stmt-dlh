# Data model

Three schemas in one catalog (`catalog` in `config/settings.yaml`). Locally the same shape lands in
`data/silver/*.parquet` and `data/bank.duckdb`.

## Bronze: what arrived

| Table | One row per | Columns of note |
|---|---|---|
| `bronze.statement_files` | file seen in the source | `object_key`, `etag`, `file_sha256`, `size_bytes`, `source`, `statement_kind`, `reconciled`, `parser_version` |
| `bronze.raw_lines` | parsed transaction line, before any cleaning | `file_sha256`, `seq`, `txn_date`, `narration`, `ref_no`, `value_date`, `amount`, `balance`, `direction` |

A file is new when its content hash is new. A moved or renamed copy of a loaded file is only re-registered.

## Silver: reconciled

| Table | One row per | Columns of note |
|---|---|---|
| `silver.statements` | reconciled statement | `period_from`, `period_to`, `opening`, `closing`, `debits`, `credits`, `txn_count`, `statement_kind` |
| `silver.transactions` | transaction, once | `txn_id`, `txn_date`, `narration`, `ref_no`, `amount`, `balance`, `direction`, `signed_amount`, `channel`, `counterparty_clean`, `statement_kind` |
| `silver.transaction_sources` | appearance of a line in a file | `txn_id`, `file_sha256`, `statement_kind`, `is_duplicate`, `duplicate_of_file` |
| `silver.quarantine` | file that failed reconciliation | `file`, `errors`, `quarantined_at` |

`txn_id` is a hash of date, reference, amount and running balance. Narration is left out because weekly and
monthly statements print it differently for some lines; the balance keeps look-alike lines apart. `channel` is
`upi`, `upi-autopay`, `card`, `imps`, `neft`, `rtgs`, `cheque`, `bank-interest` or `other`.

## Gold: facts, reference, reports

| Table | Written by | Purpose |
|---|---|---|
| `gold.fact_transactions` | `03` | Every transaction with `category`, `category_source`, `category_confidence`, `is_founder` |
| `gold.recurring_series` | `03` | Detected monthly and yearly series: payee, cadence, typical amount, last seen, next expected, status |
| `gold.review_queue` | `03` | Rows that need a person |
| `gold.jev_labels` | `03` | Every Jev answer, keyed by the request text, so it is never asked twice |
| `gold.dim_category`, `gold.payee_map`, `gold.category_rules`, `gold.recurring_declared`, `gold.settings` | `01` | The YAML configuration as tables, so SQL users can see what each label is based on |
| `gold.fx_rates` | `04` | One ECB USD/INR reference rate per date used |
| `gold.weekly_cash_snapshot`, `gold.weekly_cash_snapshot_lines` | `04` | Append-only: each weekly report as sent |
| `gold.report_log` | `04` | Every send: week, mode, version, recipients, status |

Views in `databricks/sql/20_gold_views.sql`, all plain SQL: `monthly_flow`, `monthly_flow_long`,
`spend_by_category_month`, `category_totals`, `upcoming_payments`, `founder_monthly`, `funding_received`,
`review_summary`, `statement_status`, `duplicate_lines`, `category_source_mix`, `kpis`, `cashflow_daily`,
`balance_daily`.

## Reconciliation rule

For each statement, the parser recomputes from the rows: debit count, credit count, debit total, credit total and
the last running balance, and compares them with the bank's STATEMENT SUMMARY. Direction of each row comes from
the change in running balance. Any mismatch, including a balance break between two rows, quarantines the file.

## Local Wells Fargo credit cards

Local and cloud statements and transactions include `account_type`, `currency` and an opaque `account_id`.
Card transactions add `activity_type`, `identity_occurrence`, and a nullable `balance` (the PDF has no
running transaction balance). `value_date` is the posting date; `txn_date` is the purchase/activity date.
Card identity uses account, currency, both dates, reference, amount, direction, activity type and
occurrence number. This preserves repeated transactions and deduplicates repeated statement copies.
Card `credit` rows reduce debt; `debit` rows increase it. Statement closing balances are amounts owed,
not cash available. Local card views replace bank cash-flow views. Cloud bronze/silver/gold retain card metadata through replay;
legacy rows are backfilled as bank/INR. Cloud bank cash views filter to bank/INR, and separate `card_activity`,
`card_monthly`, `card_statement_balances`, `card_kpis`, and `card_spend_by_category` views serve USD cards.
`card_kpis` sums only the latest statement per account; it also exposes oldest/latest account statement dates.
Cloud review rows retain account and currency. Cloud recurring series are empty for cards.
