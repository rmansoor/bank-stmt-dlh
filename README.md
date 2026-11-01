# bank-stmt-dlh

Turns statement PDFs (HDFC Bank current accounts or Wells Fargo credit cards) into a reconciled ledger with category,
recurring-payment, cash and runway reports. Runs on a laptop with DuckDB, or as a scheduled Databricks job
that reads the PDFs from an S3-compatible bucket (MinIO, AWS S3, R2) and serves an AI/BI dashboard and a
weekly cash report by email.

**Deterministic first.** Parsing, reconciliation, rules, recurring detection, reports and the dashboard
are plain code and SQL. The one optional AI step is Jev (TypeSafe AI), used only for payments the rules
cannot place, behind a confidence gate (see [Deterministic or AI?](#deterministic-or-ai)).

## Try it in two minutes

Synthetic statements for a fictional company (ConceptLabs) are in `samples/statements`: four monthly
statements and two overlapping weekly ones, no password. The sample payee map in `config/` matches them.

```bash
make setup                                   # uv sync
make test                                    # unit tests
STATEMENTS_DIR=samples/statements make extract   # parse and reconcile -> data/silver/*.parquet
make gold                                    # categorize, recurring -> data/bank.duckdb
make report                                  # print the reports
```

`make extract` reports every statement as reconciled or failed (counts, totals and closing balance
against the bank's STATEMENT SUMMARY). Nothing unreconciled is loaded. Lines that appear in both a
weekly and a monthly statement count once.

Regenerate the samples with `python scripts/make_sample_statements.py`, and a text logo for your own
company with `python scripts/make_text_logo.py "Your Company"`.

## How it works

```
PDF statements  (local folder | S3-compatible bucket)
   -> bronze   file list + raw parsed lines
   -> silver   reconciled statements + transactions (weekly/monthly duplicates tagged once)
   -> gold     categorized facts, recurring series, views  -> reports, dashboard, weekly email
```

| Step | Method |
|---|---|
| Parse and reconcile | `pdfplumber` + balance arithmetic (`src/extract.py`) |
| Channel, counterparty, `txn_id` | regex and hashing (`src/normalize.py`) |
| Categorize | payee map, then rules, then founder logic (`config/*.yaml`, `src/categorize.py`) |
| Recurring and yearly payments | gap and amount test, plus a declared list (`src/recurring.py`) |
| Reports, dashboard, weekly email | SQL views and plain rendering (`src/reports.py`, `src/weekly_report.py`) |
| Leftovers | Jev, optional, confidence 0.9 or more |

## Your own statements

Wells Fargo credit card statements are supported locally and in Databricks, with USD card reports.
See [the walkthrough](docs/getting-started.md#wells-fargo-credit-cards).
Keep personal statements under `data/raw/`; the tracked samples are fictional.


1. `cp .env.sample .env`. Set `PDF_PASSWORD` (HDFC protects each statement) and `STATEMENTS_DIR`.
   Never commit `.env`.
2. Edit `config/merchant_map.yaml` (known payees) and `config/recurring_declared.yaml` (yearly payments).
   The shipped files hold fictional names. `config/rules.yaml` and `config/categories.yaml` are generic
   and a good start. Names that are not secrets (company name, catalog, bucket) are in `config/settings.yaml`.
3. `make extract`, `make gold`, `make report`.

A statement for a running month is partial; drop the full one in after month end. Weekly statements may
overlap monthly ones; each line item is identified by date, reference, amount and running balance.

## Databricks with an S3-compatible bucket

Choose `lakehouse_account_type: credit-card` for Wells Fargo (this checkout) or `bank` for HDFC.
Use a separate catalog for each type/currency. Card mode uses USD views/dashboard and blocks the bank cash email.
See [the cloud setup guide](docs/databricks.md).

You need a Databricks workspace (Free Edition works) and a bucket on MinIO, S3 or R2 with a key limited
to that bucket.

1. Fill the `DATABRICKS_*` and `MINIO_*` values in `.env`, and set `statement_source: minio`,
   `minio_bucket` and `catalog` in `config/settings.yaml`.
2. Create the catalog by hand (`CREATE CATALOG bank_stmt_dlh;` or your name), then `make db-init` (schemas).
3. `make secrets` copies the PDF password, Jev key and bucket keys into the secret scope.
4. `make deploy` uploads code, config, SQL and notebooks to `/Users/<DATABRICKS_USER>/bank-stmt-dlh`.
5. `make upload` copies the PDFs from `STATEMENTS_DIR` to the bucket (`WEEKLY=1` for weekly statements,
   which go under `statements/weekly/`) and starts the job. `make etl` creates or updates the jobs.
6. `make dashboard` generates and publishes the dashboard.

Job `bank-stmt-dlh` runs daily at midnight IST: `02` (ingest) -> new data? -> `01` (seed config) -> `03` (gold).
No PDF is kept in Databricks; each is downloaded to temporary disk, parsed and dropped.

| Notebook | What it does |
|---|---|
| `01_reference_seed` | Copies categories, payee map, rules and declared payments from `config/*.yaml` into gold |
| `02_ingest_bronze_silver` | Lists the bucket, skips content already loaded (SHA-256), parses, reconciles, tags duplicates, merges to silver |
| `03_gold_build` | Categorizes, finds recurring payments, rebuilds gold tables and the dashboard views. The only AI call (Jev), if a key is set |
| `04_weekly_cash_report` | Snapshots the week, fetches one USD/INR rate, renders and emails the report |
| `jobs/b0_*` | One-off checks that the bucket is reachable from serverless compute |

**Optional, Microsoft 365.** The pipeline can also read the PDFs from a SharePoint folder through Microsoft
Graph (`statement_source: sharepoint`) and send the weekly report from a shared mailbox. Both need a tenant
admin once: `docs/cookbook/sharepoint-source-setup.md` and `docs/cookbook/graph-mail-setup.md`.

## Deterministic or AI?

Jev is a decision model, not a text-generating LLM: it answers a typed `choice` question with a probability
and a confidence. It sees only `Counterparty: X. Channel: upi. Direction: debit.`, never amounts or account
numbers. A label is used only at confidence 0.9 or more; otherwise the row is `unclassified`, or
`human-intervention-required` if it is large or a credit. Every answer is stored, so the same request is
never sent twice. No `TYPESAFE_API_KEY` means no AI step at all. The label source is always visible in
`category_source`.

## `.env` reference

| Variable | Used for |
|---|---|
| `PDF_PASSWORD` | Opens the statement PDFs. Empty for the samples |
| `STATEMENTS_DIR` | Folder with the PDFs (local runs, `make upload`) |
| `TYPESAFE_API_KEY` | Jev, optional |
| `DATABRICKS_HOST`, `_TOKEN`, `_HTTP_PATH`, `_USER`, `_CATALOG`, `ALERT_EMAIL` | Deploy scripts, SQL runner, job alerts |
| `MINIO_ENDPOINT`, `_ACCESS_KEY`, `_SECRET_KEY`, `_BUCKET` | S3-compatible statement source |
| `SP_*`, `MAIL_*` | Optional SharePoint source and weekly email (cookbooks) |

## Layout

```
src/         parser, normalizer, categorizer, recurring, readers (S3, SharePoint), report, mail, settings
databricks/  notebooks/, sql/ (init + gold views), dashboards/, jobs/ (health checks), run_sql.py
scripts/     deploy, jobs, secrets, upload, sample statements, text logo, publish
config/      categories, rules, settings, payee map and declared payments (fictional samples)
samples/     synthetic statement PDFs
openspec/    change specs (proposal, specs, design, tasks) for every non-trivial change
tests/       unit tests, fictional data only
data/        local parquet and DuckDB (gitignored)
```

## Process and contributing

Every non-trivial change starts as `openspec/changes/<name>/` (proposal, specs, design, tasks) before any
code. See `CLAUDE.md`. Add a new bank by adding one parser module that returns the same columns as
`src/extract.py`. No real names, addresses, hosts or amounts in code, tests or docs.

MIT licence, see `LICENSE`.
