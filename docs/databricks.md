# Databricks lakehouse

The same parser runs inside a scheduled Databricks job. Raw PDFs stay in your bucket; each one is downloaded to
temporary disk, parsed, reconciled and dropped. Nothing is kept in Databricks but the tables.

## Wells Fargo credit card mode

This checkout sets `lakehouse_account_type: credit-card` in `config/settings.yaml`.
Use a dedicated catalog for USD cards. For HDFC, choose `bank` and a separate INR catalog.
A catalog containing transactions of the other type/currency is rejected. Existing legacy
columns are migrated as HDFC/INR; do not switch a populated bank catalog to card mode.

Credit card mode preserves account identity, activity type, currency, nullable transaction balances,
and occurrence identity in bronze/silver/gold. Every activity section and statement balance must reconcile.
The USD dashboard has Overview, Spending, Transactions (date filter) and Data quality. Balances are
amounts owed as of each account's latest statement, not live cash balances. Payments are transfers,
refunds offset spending, and advances and explicitly labeled balance transfers are excluded from spending.
There is no card runway, founder ledger or invented daily running balance. Unclassified merchants
remain in the review queue. Card rows do not call Jev. Account identity uses the printed last four digits;
process different cards sharing those digits in separate catalogs.

Weekly **bank cash email is unavailable in credit card mode**: its scheduled job is paused, and manual
job/notebook execution is blocked before mail or FX access. Card email is a separate future feature. Job failure emails are opt-in through `ALERT_EMAIL`; blank disables them.

## What you need

- A Databricks workspace. Free Edition with serverless compute is enough.
- An S3-compatible bucket: MinIO, AWS S3, Cloudflare R2. Use a key limited to that bucket, not a root account.
- Optional: a Microsoft 365 tenant admin, once, if you want the SharePoint source or the weekly email.

## One-time setup

1. Copy `.env.sample` to `.env` if needed (never overwrite configured credentials). Fill the `DATABRICKS_*` and `MINIO_*` values in `.env`. In `config/settings.yaml` set `statement_source: minio`,
   `minio_bucket` and `catalog`.
2. Create the catalog by hand in the SQL editor: `CREATE CATALOG bank_stmt_dlh;` (or your name).
3. `make db-init` creates the `bronze`, `silver` and `gold` schemas.
4. `make secrets` copies the PDF password, the Jev key and the bucket keys from `.env` into the secret scope.
   Groups: `core`, `minio`, `sp`, `mail`. An empty group that the configured source does not need is skipped.
   Card mode skips mail; an empty PDF password and AI key are optional.
5. `make deploy` uploads `src/`, `config/`, `databricks/sql/`, `assets/` and the notebooks to
   `/Users/<DATABRICKS_USER>/bank-stmt-dlh`. The repo is the source of truth; do not edit notebooks online.
6. `make etl` creates or updates the two jobs and starts the ETL once.
7. `make dashboard` generates the AI/BI dashboard definition from `databricks/dashboards/build_dashboard.py`
   and publishes it.

## Verify setup before deployment

Fill these local `.env` fields; do not paste keys into chat:

- `DATABRICKS_HOST`: your workspace URL.
- `DATABRICKS_TOKEN`: a workspace token permitted to deploy code, manage jobs and access the catalog.
- `DATABRICKS_HTTP_PATH`: the SQL warehouse connection path.
- `DATABRICKS_USER`: your workspace login.
- `DATABRICKS_CATALOG`: must match `catalog` in `config/settings.yaml`.
- `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`: your S3-compatible endpoint and bucket-scoped key.
- `MINIO_BUCKET`: must match `minio_bucket` in `config/settings.yaml`.
- Set `minio_region` in `config/settings.yaml` to the AWS bucket region or your provider’s signing region.
  The `MINIO_*` names are shared by AWS S3, MinIO and R2; a separate MinIO server is not required.
- `STATEMENTS_DIR`: local PDF folder (`samples/mystatement` works; `data/raw/wells-fargo` is preferred).

`make cloud-check` checks that required values are present without contacting services or printing secrets.
It does not verify permissions or network access. Create the chosen catalog in the workspace SQL editor,
then use the one-time setup commands. The first ingestion run adds metadata columns to existing Delta
tables. On a fresh catalog there are no historical rows to migrate.

```bash
make cloud-check
make db-init
make secrets
make deploy
make upload
make etl
make dashboard
```

Inspect the ETL run status before publishing the dashboard. A successful HTTP response starting a job
is not proof that ingestion completed. Check that silver has the expected unique transaction count,
quarantine is empty, and gold card totals match the statement summaries. `make upload` starts a run
only if a job ID already exists; otherwise `make etl` creates the jobs and starts the first run.
`make dashboard-build` generates the JSON locally without publishing it. Dashboard publication also
rebuilds the definition from the configured account mode, so it cannot publish stale bank widgets.

Local validation can run without credentials. The regular `make test` suite skips Spark integration tests
if PySpark is unavailable. To execute those tests, install PySpark 4.0.x in the test environment, use Java
17+, and run `make test` with `SPARK_LOCAL_IP=127.0.0.1`.

## Daily loop

```bash
make upload            # STATEMENTS_DIR -> bucket, then start the job (WEEKLY=1 for weekly statements)
make run               # start the ETL job now
make weekly-report     # bank mode only; run the weekly cash report now (WEEK_END=2026-10-18 FORCE=1 MODE=dry)
make deploy            # after a code or config change, then make run
```

Weekly statements go under `statements/weekly/` in the bucket; everything else under the prefix is monthly.

## Jobs and notebooks

Job `bank-stmt-dlh` runs once a day at midnight IST. Task order: `02` (ingest) -> did it find new data? ->
`01` (seed) -> `03` (gold). The last two run only when there is something new, or when the job is started with
`force=true` (what `make run` does). Job `bank-stmt-dlh-weekly-cash-report` runs the same chain and then `04`
every Monday 00:00 America/New_York.

| Notebook | What it does | Deterministic? |
|---|---|---|
| `01_reference_seed` | Copies categories, payee map, rules, declared payments and settings from `config/*.yaml` into gold tables | Yes |
| `02_ingest_bronze_silver` | Lists the bucket, skips content already loaded (SHA-256), parses, reconciles, tags weekly/monthly duplicates, merges to silver. Unreconciled files go to quarantine | Yes |
| `03_gold_build` | Categorizes, finds recurring payments, rebuilds gold tables and the views in `databricks/sql/20_gold_views.sql` | Mostly. Holds the only AI call (Jev), if a key is set |
| `04_weekly_cash_report` | Snapshots the week, fetches one USD/INR rate (ECB via Frankfurter), renders the email (INR or USD body, PDFs in both) and sends it through Microsoft Graph | Yes |
| `jobs/b0_feasibility`, `jobs/b0_diagnose` | One-off checks that serverless compute can reach the bucket and parse a statement | Yes |
| `jobs/s0_sharepoint_check`, `jobs/m0_mail_rate_check` | Health checks for the optional SharePoint source and mailer | Yes |

Notebooks find the deployed repo root from their own path and read catalog, secret scope and bucket from
`config/settings.yaml`, so no user name or workspace id is in the code. SQL files use the token `__CATALOG__`,
replaced at run time.

## Dashboard

Pages: Overview (cash, monthly outflow, runway), Spend, Recurring and yearly (next due, lapsed), Funding and
founder, Cash flow by date (date-range filter), Data quality (statements loaded, quarantined files, duplicate
lines, rows for review, reports sent). Every tile is SQL on the gold views; no model writes anything.

## Weekly cash report

One email per week with opening and closing balance, money in and out, spend by category, the largest movements,
upcoming payments and the review queue. The body is in INR by default; two PDFs (INR and USD) are attached, at
one stated exchange rate. Append-only snapshot tables keep every report as sent, so a resend reproduces what went
out and past weeks do not change when rules are fixed later. `gold.report_log` records every send; a dry-run mode
sends to one address only.

Sending needs a shared mailbox and a send-only Entra app limited to it: see the
[mail cookbook](cookbook/graph-mail-setup.md).

## Troubleshooting

- A 403 from the bucket behind a proxy: rerun `jobs/b0_diagnose`, which probes list, HEAD and ranged GET.
- A quarantined file is listed on the dashboard's Data quality tab with the reconciliation errors.
- Secrets expire (SharePoint and mailer client secrets): renew, then `make secrets`.
- Graph throttling (429) and 5xx answers are retried with backoff.
