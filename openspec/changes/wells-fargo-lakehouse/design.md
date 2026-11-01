# Design
Use lakehouse_account_type = bank (INR) or credit-card (USD), one mode per catalog.
Backfill legacy tables as bank/INR. Reject incompatible statements into quarantine before
silver writes and fail gold if stored metadata conflicts with configured mode. Retain account,
currency, activity and occurrence fields in bronze, silver and gold. Explicit target schemas
support all-null running balances and empty review/recurring tables. Preserve metadata during
bronze replay. Card views use USD and statement balances owed, not bank cash or runway.
Card dashboard has overview, monthly activity, spending, transaction date filter and data quality.
Use existing bank views filtered to bank accounts and add separate card views. Pause scheduled
weekly cash email and reject manual requests/card notebook execution before mail/FX access.
Pure transformation tests plus Spark/SQL execution where available; live Databricks requires
workspace/bucket credentials. Existing local outputs are not overwritten by cloud validation.
