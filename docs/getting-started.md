# Getting started

## Try it in two minutes

You need Python 3.12 or newer, [`uv`](https://docs.astral.sh/uv/) and `make`.

Synthetic statements for a fictional company (ConceptLabs) are in `samples/statements`: four monthly statements
(July to October 2026) and two weekly ones that overlap October, no password. The payee map shipped in `config/`
matches them.

```bash
git clone https://github.com/senthilsweb/bank-stmt-dlh.git
cd bank-stmt-dlh
make setup                                      # uv sync
make test                                       # unit tests
STATEMENTS_DIR=samples/statements make extract  # parse and reconcile -> data/silver/*.parquet
make gold                                       # categorize, recurring -> data/bank.duckdb
make report                                     # print the reports
```

`make extract` prints one line per statement, reconciled or failed. `make report` shows the statements, the monthly
summary, spend by category, the categorization source mix, upcoming payments (detected and declared, including one
ended engagement) and the founder ledger.

```
6 statements, 80 transactions, 0 failed reconciliation
```

Regenerate the samples with `python scripts/make_sample_statements.py`. Make a text logo for your own company with
`python scripts/make_text_logo.py "Your Company"` (used by the weekly report and its PDFs).

## Your own statements

1. `cp .env.sample .env`. Set `PDF_PASSWORD` (HDFC protects each statement with one) and `STATEMENTS_DIR`.
   Never commit `.env`.
2. Edit `config/merchant_map.yaml` (known payees) and `config/recurring_declared.yaml` (yearly payments). The shipped
   files hold fictional names. `config/rules.yaml` and `config/categories.yaml` are generic and a good start.
3. `make extract`, `make gold`, `make report`.

A statement for a running month is partial; drop the full one in after month end. Weekly statements may overlap
monthly ones. Each line item is identified by date, reference, amount and running balance, so a line already loaded
is counted once; the monthly text wins.

## What the parser expects

One text line per transaction, as `pdfplumber` extracts it from an HDFC current account statement:

```
DD/MM/YY  narration  <reference, 10 to 20 digits>  DD/MM/YY  amount  closing balance
```

Direction comes from the running balance, never from a column. The period comes from the `From : ... To : ...`
line and the totals from the `STATEMENT SUMMARY` block. Another bank is one parser module that returns the same
columns as `src/extract.py`.

## Wells Fargo credit cards

The local parser also supports text-based Wells Fargo credit card statements with a
`Statement Period` and `Account Summary`. Scans and other layouts are rejected.
Keep personal PDFs in `data/raw/wells-fargo/` (ignored by Git), not in the fictional sample folder.
Existing PDFs in `samples/mystatement/` are also ignored.

```bash
STATEMENTS_DIR=data/raw/wells-fargo make extract
make gold
make report
```

Automatic format detection selects the parser. Every activity section must match the summary,
and previous balance + charges + fees + interest - payments - credits must equal new balance.
An unsupported or empty PDF produces an explanatory error. If no valid transactions remain,
previous output is left unchanged; do not run gold/report after failed extraction.

Card reports use USD and distinguish purchases, refunds, payments, fees, interest, cash advances,
and explicitly labeled balance transfers. Payments are transfers, not income. Refunds reduce
spending. Purchases grouped by the bank with other charges remain in that section unless the
narration explicitly identifies a balance transfer; review classifications before relying on totals.
Reports show transaction-month activity separately from statement closing balances owed.
Duplicate copies load once; repeated purchases with different references remain separate.
The review queue is `data/gold/review_queue.csv`. Card transactions never use Jev or founder logic.
Card reports do not generate cash runway, recurring obligations or weekly emails.

Use separate input folders for bank accounts and cards and for different currencies. Each extraction
replaces the local ledger; it does not append to previous runs. Account identity uses a hash of the
issuer, account type and printed last four digits; cards sharing those digits must be processed separately.
For cloud ingestion and the USD dashboard, see [Databricks setup](databricks.md). Weekly cash email remains bank-only.
