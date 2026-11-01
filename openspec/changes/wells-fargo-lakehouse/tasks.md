# Tasks
- [x] Shared metadata, schemas and typed Spark writes.
- [x] Migrate cloud ingestion/replay and gold handling.
- [x] Card SQL views and dashboard selection.
- [x] Guard weekly cash report and scheduling.
- [x] Automated tests, docs and setup template.
- [x] Live deployment and reconciliation.

Validation: 70 tests passed with PySpark 4.0.2 and Java 17, including explicit nullable/empty
Spark writes, card SQL totals, latest balance per account, every card and bank dashboard query,
legacy HDFC gold transformations, duplicate/replay identity, and card email/schedule guards.
Local private validation: 6 PDFs, 5 distinct contents, 430 unique transactions with identical
local/cloud IDs; no reconciliation failures and no AI calls. Private data remains ignored.
Live deployment succeeded: S3 upload, catalog/schema creation, secret provisioning, all ETL tasks,
and all nine cloud dashboard dataset queries. Cloud silver/gold match local transaction identities,
amounts, account/activity/currency fields, statement totals and card KPIs. Dashboard published.
The weekly cash email job is paused; failure notifications require explicit ALERT_EMAIL.
A private .env template was prepared; bucket typo and deployment username were corrected locally.

Live incremental rerun succeeded with zero new files. Seed/gold were excluded by the condition;
bronze, silver, appearances and gold counts remained unchanged. Daily ETL schedule enabled;
weekly bank cash email schedule verified paused.
