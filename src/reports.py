"""Gold layer: categorized facts, DuckDB file and the standard reports."""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv

from categorize import categorize
from recurring import declared, detect, ended_patterns

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
SILVER = ROOT / "data" / "silver"
GOLD = ROOT / "data" / "gold"
DB = ROOT / "data" / "bank.duckdb"


def build_gold(use_jev: bool = True) -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    txns = pd.read_parquet(SILVER / "transactions.parquet")
    fact = categorize(txns, use_jev=use_jev)
    fact.to_parquet(GOLD / "fact_transactions.parquet", index=False)
    if "account_type" in fact and (fact["account_type"] == "credit-card").all():
        build_card_gold(fact)
        return
    auto = detect(fact).assign(source="detected")
    rec = pd.concat([auto, declared(fact).assign(source="declared")], ignore_index=True)
    ended = "|".join(ended_patterns())
    rec["ended"] = rec["counterparty_clean"].str.contains(ended, case=False, regex=True) if ended else False
    rec.to_parquet(GOLD / "recurring_series.parquet", index=False)
    fact[fact["category"].isin(["human-intervention-required", "unclassified"])].groupby(
        ["category", "counterparty_clean", "direction"], as_index=False
    ).agg(count=("amount", "size"), total=("amount", "sum"), reason=("review_reason", "first"),
          example=("narration", "first")).sort_values(
        ["category", "total"], ascending=[False, False]
    ).to_csv(GOLD / "review_queue.csv", index=False)

    con = duckdb.connect(str(DB))
    con.execute(f"create or replace table fact_transactions as select * from read_parquet('{GOLD}/fact_transactions.parquet')")
    con.execute(f"create or replace table recurring_series as select * from read_parquet('{GOLD}/recurring_series.parquet')")
    con.execute(f"create or replace table statements as select * from read_parquet('{SILVER}/statements.parquet')")
    con.execute(
        """create or replace view monthly_by_category as
           select year, month, category_type, category, direction,
                  count(*) as txns, round(sum(amount), 2) as total
           from fact_transactions group by all order by year, month, total desc"""
    )
    con.execute(
        """create or replace view monthly_summary as
           select year, month,
                  round(sum(case when direction='credit' then amount end), 2) as credits,
                  round(sum(case when direction='debit'  then amount end), 2) as debits,
                  round(sum(signed_amount), 2) as net,
                  round(avg(sum(case when direction='debit' then amount end)) over
                        (order by year, month rows between 2 preceding and current row), 2) as debit_3m_avg
           from fact_transactions group by year, month order by year, month"""
    )
    con.execute(
        """create or replace view upcoming_payments as
           select counterparty_clean as payee, category, cadence, typical_amount, next_expected, source,
                  case when ended then 'ended'
                       when next_expected < (select max(txn_date) from fact_transactions)
                       then 'lapsed: no payment since' else 'upcoming' end as status
           from recurring_series where direction='debit' order by status desc, next_expected"""
    )
    con.execute(
        """create or replace view founder_monthly as
           select year, month,
                  round(sum(case when direction='debit'  then amount end), 2) as paid_to_founder,
                  round(sum(case when direction='credit' then amount end), 2) as received_from_founder
           from fact_transactions where is_founder group by all order by year, month"""
    )
    con.close()


def build_card_gold(fact: pd.DataFrame) -> None:
    """Card activity is not a bank cash-flow or runway report."""
    fact[fact["category"].isin(["human-intervention-required", "unclassified"])].to_csv(
        GOLD / "review_queue.csv", index=False)
    # Remove artifacts from an earlier bank run; card reports do not infer business recurring obligations.
    recurring_path = GOLD / "recurring_series.parquet"
    if recurring_path.exists():
        recurring_path.unlink()
    with duckdb.connect(str(DB)) as con:
        for view in ("monthly_summary", "monthly_by_category", "upcoming_payments", "founder_monthly"):
            con.execute(f"drop view if exists {view}")
        con.execute("drop table if exists recurring_series")
        con.execute("create or replace table fact_transactions as select * from fact")
        con.execute(f"create or replace table statements as select * from read_parquet('{SILVER}/statements.parquet')")
        con.execute("""create view monthly_summary as
            select year, month, currency,
                round(sum(case when activity_type = 'purchase' then amount else 0 end),2) as purchases,
                round(sum(case when activity_type = 'refund' then amount else 0 end),2) as refunds,
                round(sum(case when activity_type = 'fee' then amount else 0 end),2) as fees,
                round(sum(case when activity_type = 'interest' then amount else 0 end),2) as interest,
                round(sum(case when activity_type = 'payment' then amount else 0 end),2) as payments,
                round(sum(case when activity_type = 'cash-advance' then amount else 0 end),2) as cash_advances,
                round(sum(case when activity_type = 'balance-transfer' then amount else 0 end),2) as balance_transfers,
                round(sum(case when activity_type in ('purchase','fee','interest') then amount
                               when activity_type = 'refund' then -amount else 0 end),2) as net_spending
            from fact_transactions group by all order by year, month""")
        con.execute("""create view monthly_by_category as
            select year, month, currency, category, category_type, direction,
                count(*) as txns, round(sum(amount),2) as total
            from fact_transactions group by all""")


def show() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    columns = {r[0] for r in con.execute("describe fact_transactions").fetchall()}
    if "account_type" in columns and con.execute(
        "select bool_and(account_type = 'credit-card') from fact_transactions").fetchone()[0]:
        print("\n== Credit card activity (USD; transaction month, not bank cash flow) ==")
        print(con.sql("select * from monthly_summary").df().to_string(index=False))
        print("\n== Statement balances (USD owed; duplicate file contents shown once) ==")
        print(con.sql("select account_id, period_from::date as period_from, period_to::date as period_to, opening, closing, reconciled "
                      "from statements qualify row_number() over (partition by file_sha256 order by file) = 1 "
                      "order by period_to").df().to_string(index=False))
        print("\n== Spending by category (USD; refunds offset expenses) ==")
        print(con.sql("select category, round(sum(case when activity_type = 'refund' then -amount else amount end),2) as total_usd "
                      "from fact_transactions where activity_type not in ('payment','cash-advance','balance-transfer') "
                      "group by category order by total_usd desc").df().to_string(index=False))
        print("\n== Categorization source ==")
        print(con.sql("select category_source, count(*) as transactions from fact_transactions group by 1").df().to_string(index=False))
        print("Payments are transfers; refunds reduce spending. Cash advances and identified balance transfers are excluded from net spending.")
        con.close()
        return
    pd.set_option("display.width", 200, "display.max_rows", 200, "display.max_colwidth", 50)
    print("\n== Statements reconciled ==")
    print(con.sql("select file, txn_count, reconciled from statements order by file").df().to_string(index=False))
    print("\n== Monthly summary ==")
    print(con.sql("select * from monthly_summary").df().to_string(index=False))
    print("\n== Spend by category (debits) ==")
    print(con.sql("select category, count(*) txns, round(sum(amount),2) total from fact_transactions "
                  "where direction='debit' group by 1 order by total desc").df().to_string(index=False))
    print("\n== Categorization source ==")
    print(con.sql("select category_source, count(*) n from fact_transactions group by 1 order by n desc").df().to_string(index=False))
    print("\n== Upcoming payments ==")
    print(con.sql("select * from upcoming_payments").df().to_string(index=False))
    print("\n== Founder (payouts and credits) ==")
    print(con.sql("select txn_date::date as date, direction, amount, category from fact_transactions "
                  "where is_founder order by txn_date").df().to_string(index=False))
    print("\n== Recurring payments (detected + declared) ==")
    print(con.sql("select * from recurring_series").df().to_string(index=False))


if __name__ == "__main__":
    if "--no-jev" in sys.argv:
        build_gold(use_jev=False)
    elif "--show" not in sys.argv:
        build_gold()
    show()
