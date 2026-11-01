# Requirements
- Ingestion SHALL preserve card metadata and nullable balances in all layers and during replay.
- Migration SHALL add columns without deleting existing rows and backfill legacy bank metadata.
- Incompatible source types or currencies SHALL be quarantined; incompatible stored silver SHALL block gold.
- Typed writes SHALL support all-null balance columns and empty review and recurring tables.
- Card totals SHALL exclude payments, advances and identified balance transfers from net spending.
- Latest debt SHALL use the latest statement per account, never sum historical balances.
- Dashboard card mode SHALL use USD without bank cash, runway, founder or currency-conversion widgets.
- Bank cash report SHALL refuse credit card mode before secrets, FX requests or sends.
- Tests SHALL use fictional data, cover incremental duplicate resolution and HDFC regression.
