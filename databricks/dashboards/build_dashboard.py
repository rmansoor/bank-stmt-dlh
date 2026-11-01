"""Generate bank-stmt-dlh.lvdash.json (AI/BI dashboard definition) from Python helpers.

The JSON is committed. Datasets are plain SQL on bank_stmt_dlh.gold views: no model calls.
Run:  python databricks/dashboards/build_dashboard.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("bank-stmt-dlh.lvdash.json")
CAT = "__CATALOG__"


def ds(name, title, sql):
    return {"name": name, "displayName": title, "queryLines": [sql]}


def pos(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def text(name, md, p):
    return {"widget": {"name": name, "textbox_spec": md}, "position": p}


def counter(name, dataset, field, title, p, fmt):
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": dataset, "fields": [{"name": field, "expression": f"SUM(`{field}`)"}], "disaggregated": False}}],
            "spec": {"version": 2, "widgetType": "counter", "frame": {"showTitle": True, "title": title},
                     "encodings": {"value": {"fieldName": field, "format": fmt, "displayName": title}}}}, "position": p}


INR = {"type": "number-currency", "currencyCode": "INR", "abbreviation": "compact", "decimalPlaces": {"type": "max", "places": 2}}
NUM1 = {"type": "number", "abbreviation": "none", "decimalPlaces": {"type": "max", "places": 1}}


def chart(name, kind, dataset, x, y, color, title, p, xtype="categorical", xlabel=None, ylabel="Amount (INR)", stacked=False):
    fields = [{"name": x, "expression": f"`{x}`"}, {"name": f"sum({y})", "expression": f"SUM(`{y}`)"}]
    enc = {"x": {"fieldName": x, "scale": {"type": xtype}, "displayName": xlabel or x},
           "y": {"fieldName": f"sum({y})", "scale": {"type": "quantitative"}, "displayName": ylabel}}
    if color:
        fields.append({"name": color, "expression": f"`{color}`"})
        enc["color"] = {"fieldName": color, "scale": {"type": "categorical"}, "displayName": color}
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": dataset, "fields": fields, "disaggregated": False}}],
            "spec": {"version": 3, "widgetType": kind, "frame": {"showTitle": True, "title": title}, "encodings": enc}}, "position": p}


def table(name, dataset, cols, title, p):
    """cols: [(field, header, type, displayAs)] type in string|integer|float, displayAs string|number.
    Dates and booleans are cast to text in the dataset SQL, so they are plain string columns here."""
    encs = []
    for i, (f, head, typ, disp) in enumerate(cols):
        e = {"fieldName": f, "booleanValues": ["false", "true"], "imageUrlTemplate": "{{ @ }}", "imageTitleTemplate": "{{ @ }}",
             "imageWidth": "", "imageHeight": "", "linkUrlTemplate": "{{ @ }}", "linkTextTemplate": "{{ @ }}", "linkTitleTemplate": "{{ @ }}",
             "linkOpenInNewTab": True, "type": typ, "displayAs": disp, "visible": True, "order": i, "title": head, "allowSearch": False,
             "alignContent": "right" if disp == "number" else "left", "allowHTML": False, "highlightLinks": False,
             "useMonospaceFont": False, "preserveWhitespace": False, "displayName": head}
        if typ == "float":
            e["numberFormat"] = "0.00"
        encs.append(e)
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": dataset, "fields": [{"name": f, "expression": f"`{f}`"} for f, *_ in cols], "disaggregated": True}}],
            "spec": {"version": 1, "widgetType": "table", "allowHTMLByDefault": False, "itemsPerPage": 25, "condensed": True, "withRowNumber": False,
                     "invisibleColumns": [], "paginationSize": "default", "frame": {"showTitle": True, "title": title}, "encodings": {"columns": encs}}}, "position": p}


datasets = [
    ds("ds_kpis", "Headline figures", f"select * from {CAT}.gold.kpis"),
    ds("ds_in", "Money in by month", f"select month, measure, amount from {CAT}.gold.monthly_flow_long where measure in ('Client receipts', 'Funding', 'Other credits')"),
    ds("ds_out", "Money out by month", f"select month, measure, amount from {CAT}.gold.monthly_flow_long where measure in ('Operating spend', 'Founder payout')"),
    ds("ds_monthly", "Monthly cash flow", f"select month, client_receipts, funding_in, other_in, operating_spend, founder_payout, total_outflow, closing_balance, case when partial then 'partial' else 'full' end as month_type from {CAT}.gold.monthly_flow order by month"),
    ds("ds_cat", "Spend by category", f"select category, category_type, amount, txns from {CAT}.gold.category_totals order by amount desc"),
    ds("ds_cat_month", "Spend by category and month", f"select month, category, category_type, amount, txns from {CAT}.gold.spend_by_category_month order by month, amount desc"),
    ds("ds_recurring", "Recurring and yearly payments", f"select payee, category, cadence, typical_amount, date_format(last_seen, 'yyyy-MM-dd') as last_seen, date_format(next_expected, 'yyyy-MM-dd') as next_expected, status, source from {CAT}.gold.upcoming_payments order by case status when 'upcoming' then 0 when 'lapsed' then 1 else 2 end, next_expected"),
    ds("ds_funding", "Funding received", f"select month, category, amount, txns from {CAT}.gold.funding_received order by month, category"),
    ds("ds_founder", "Founder payments", f"select month, category, paid_to_founder, received_from_founder from {CAT}.gold.founder_monthly order by month"),
    ds("ds_review", "Unlabelled payments", f"select category, direction, txns, amount from {CAT}.gold.review_summary"),
    ds("ds_status", "Statement status", f"select file, statement_kind, date_format(period_from, 'yyyy-MM-dd') as period_from, date_format(period_to, 'yyyy-MM-dd') as period_to, txn_count, coalesce(duplicate_lines, 0) as duplicate_lines, cast(reconciled as string) as reconciled, date_format(ingested_at, 'yyyy-MM-dd HH:mm') as ingested_at from {CAT}.gold.statement_status order by statement_kind, file"),
    ds("ds_dups", "Duplicate line items", f"select file, statement_kind, duplicate_of, lines from {CAT}.gold.duplicate_lines order by file"),
    ds("ds_dr_flow", "Cash flow by day and category", f"select txn_date, category, category_type, money_in, money_out, net, txns from {CAT}.gold.cashflow_daily"),
    ds("ds_dr_long", "Money in and out by day", f"select txn_date, measure, amount from {CAT}.gold.cashflow_daily LATERAL VIEW STACK(2, 'Money in', money_in, 'Money out', money_out) t as measure, amount"),
    ds("ds_dr_bal", "Closing balance by day", f"select txn_date, closing_balance from {CAT}.gold.balance_daily"),
    ds("ds_dr_tx", "Payments in the range", f"select txn_date, date_format(txn_date, 'yyyy-MM-dd') as day, counterparty_clean, category, direction, amount from {CAT}.gold.fact_transactions order by txn_date desc, seq desc"),
    ds("ds_source", "How payments were labelled", f"select category_source, txns, amount from {CAT}.gold.category_source_mix order by txns desc"),
    ds("ds_queue", "Review queue", f"select category, counterparty_clean, direction, txns, total, reason from {CAT}.gold.review_queue order by case when category = 'human-intervention-required' then 0 else 1 end, total desc"),
]

MONEY = lambda f, h: (f, h, "float", "number")
page_over = {"name": "page_overview", "displayName": "Overview", "layout": [
    text("txt_over", "# __COMPANY__ Finance\n**Cash, outflow and runway** from the bank statements loaded so far. Every figure is a SQL view on `gold`; nothing here is generated by a model.", pos(0, 0, 6, 2)),
    counter("k_cash", "ds_kpis", "cash", "Cash in bank", pos(0, 2, 2, 3), INR),
    counter("k_out", "ds_kpis", "monthly_outflow", "Monthly outflow (last 3 full months)", pos(2, 2, 2, 3), INR),
    counter("k_run", "ds_kpis", "runway_months", "Runway (months, no new money)", pos(4, 2, 2, 3), NUM1),
    counter("k_fund", "ds_kpis", "funding_received", "Funding received, 2026", pos(0, 5, 3, 3), INR),
    counter("k_rec", "ds_kpis", "client_receipts", "Client receipts, 2026", pos(3, 5, 3, 3), INR),
    chart("c_in", "bar", "ds_in", "month", "amount", "measure", "Money in by month", pos(0, 8, 3, 6), xlabel="Month"),
    chart("c_out", "bar", "ds_out", "month", "amount", "measure", "Money out by month", pos(3, 8, 3, 6), xlabel="Month"),
    chart("c_bal", "line", "ds_monthly", "month", "closing_balance", None, "Closing balance by month (bank figure)", pos(0, 14, 6, 5), xlabel="Month", ylabel="Balance (INR)"),
    table("t_monthly", "ds_monthly", [("month", "Month", "string", "string"), MONEY("client_receipts", "Client receipts"), MONEY("funding_in", "Funding"), MONEY("other_in", "Other credits"),
                                      MONEY("operating_spend", "Operating spend"), MONEY("founder_payout", "Founder payout"), MONEY("total_outflow", "Total out"),
                                      MONEY("closing_balance", "Closing balance"), ("month_type", "Month", "string", "string")], "Monthly cash flow (the data behind the three charts)", pos(0, 19, 6, 7)),
    text("txt_def", "**Definitions.** Cash is the bank's closing balance on the latest statement. Monthly outflow is the mean of all debits, founder payouts included, over the last three full months; the current partial month is left out. Runway is cash divided by that outflow. Funding is money from founders and the investor; it is kept apart from client income.", pos(0, 26, 6, 2)),
]}
page_spend = {"name": "page_spend", "displayName": "Spend", "layout": [
    text("txt_spend", "# Where the money went\nAll debits, January to October 2026, by category. `unclassified` and `human-intervention-required` are not labelled yet.", pos(0, 0, 6, 2)),
    chart("c_cat", "bar", "ds_cat", "category", "amount", None, "Spend by category", pos(0, 2, 6, 7), xlabel="Category"),
    table("t_cat", "ds_cat", [("category", "Category", "string", "string"), ("category_type", "Type", "string", "string"), ("txns", "Payments", "integer", "number"), MONEY("amount", "Amount (INR)")], "Spend by category (the data behind the chart)", pos(0, 9, 6, 8)),
    chart("c_catm", "bar", "ds_cat_month", "month", "amount", "category", "Spend by month and category", pos(0, 17, 6, 7), xlabel="Month"),
    table("t_catm", "ds_cat_month", [("month", "Month", "string", "string"), ("category", "Category", "string", "string"), ("category_type", "Type", "string", "string"), ("txns", "Payments", "integer", "number"), MONEY("amount", "Amount (INR)")], "Spend by month and category (the data behind the chart)", pos(0, 24, 6, 8)),
]}
page_rec = {"name": "page_rec", "displayName": "Recurring and yearly", "layout": [
    text("txt_rec", "# Recurring and yearly payments\nFound from the statements by a fixed gap and amount test, plus rent and the auditor fee which fall once a year (declared in config). *Lapsed* means it was expected and no payment came. *Ended* is an engagement you told us has stopped.", pos(0, 0, 6, 3)),
    table("t_rec", "ds_recurring", [("payee", "Payee", "string", "string"), ("category", "Category", "string", "string"), ("cadence", "Repeats", "string", "string"),
                                    MONEY("typical_amount", "Amount (INR)"), ("last_seen", "Last paid", "string", "string"), ("next_expected", "Next due", "string", "string"),
                                    ("status", "Status", "string", "string"), ("source", "Found by", "string", "string")], "Recurring and yearly payments", pos(0, 3, 6, 8)),
]}
page_fund = {"name": "page_fund", "displayName": "Funding and founder", "layout": [
    text("txt_fund", "# Funding and founder\nMoney put in by founders and the investor, and payments to the founder (shown as operations, no further split).", pos(0, 0, 6, 2)),
    chart("c_fund", "bar", "ds_funding", "month", "amount", "category", "Funding received by month", pos(0, 2, 6, 6), xlabel="Month"),
    table("t_fund", "ds_funding", [("month", "Month", "string", "string"), ("category", "Source", "string", "string"), ("txns", "Payments", "integer", "number"), MONEY("amount", "Amount (INR)")], "Funding received (the data behind the chart)", pos(0, 8, 6, 6)),
    table("t_founder", "ds_founder", [("month", "Month", "string", "string"), ("category", "Category", "string", "string"), MONEY("paid_to_founder", "Paid to founder (INR)"), MONEY("received_from_founder", "Received from founder (INR)")], "Founder payments and deposits", pos(0, 14, 6, 6)),
]}
def date_filter(name, datasets, p, title="Date range"):
    """Date-range filter on field txn_date of every listed dataset (the dashboard widgets on those datasets follow it)."""
    queries = [{"name": f"q_{d}", "query": {"datasetName": d, "fields": [{"name": "txn_date", "expression": "`txn_date`"}], "disaggregated": False}} for d in datasets]
    fields = [{"fieldName": "txn_date", "displayName": "Date", "queryName": f"q_{d}"} for d in datasets]
    return {"widget": {"name": name, "queries": queries, "spec": {"version": 2, "widgetType": "filter-date-range-picker",
            "encodings": {"fields": fields}, "frame": {"showTitle": True, "title": title}}}, "position": p}


def time_chart(name, kind, dataset, y, color, title, p, trunc, ylabel="Amount (INR)"):
    xname = f"{trunc.lower()}ly(txn_date)" if trunc != "DAY" else "daily(txn_date)"
    fields = [{"name": xname, "expression": f'DATE_TRUNC("{trunc}", `txn_date`)'}, {"name": f"sum({y})", "expression": f"SUM(`{y}`)"}]
    enc = {"x": {"fieldName": xname, "scale": {"type": "temporal"}, "displayName": "Date"},
           "y": {"fieldName": f"sum({y})", "scale": {"type": "quantitative"}, "displayName": ylabel}}
    if color:
        fields.append({"name": color, "expression": f"`{color}`"})
        enc["color"] = {"fieldName": color, "scale": {"type": "categorical"}, "displayName": color}
    return {"widget": {"name": name, "queries": [{"name": "main_query", "query": {"datasetName": dataset, "fields": fields, "disaggregated": False}}],
            "spec": {"version": 3, "widgetType": kind, "frame": {"showTitle": True, "title": title}, "encodings": enc}}, "position": p}


page_range = {"name": "page_range", "displayName": "Cash flow by date", "layout": [
    text("txt_range", "# Cash flow by date\nPick a date range. Every figure, chart and the payment list on this page follows it. Money in and out are the bank's own credits and debits; the balance is the bank's running balance after the last payment of each day.", pos(0, 0, 6, 2)),
    date_filter("f_range", ["ds_dr_flow", "ds_dr_long", "ds_dr_bal", "ds_dr_tx"], pos(0, 2, 6, 2)),
    counter("k_in", "ds_dr_flow", "money_in", "Money in", pos(0, 4, 2, 3), INR),
    counter("k_out", "ds_dr_flow", "money_out", "Money out", pos(2, 4, 2, 3), INR),
    counter("k_net", "ds_dr_flow", "net", "Net change", pos(4, 4, 2, 3), INR),
    time_chart("c_weekly", "bar", "ds_dr_long", "amount", "measure", "Money in and out by week", pos(0, 7, 3, 6), "WEEK"),
    time_chart("c_bal", "line", "ds_dr_bal", "closing_balance", None, "Closing balance by day", pos(3, 7, 3, 6), "DAY", ylabel="Balance (INR)"),
    chart("c_cat_out", "bar", "ds_dr_flow", "category", "money_out", None, "Money out by category", pos(0, 13, 3, 6), xlabel="Category", ylabel="Money out (INR)"),
    table("t_dr_flow", "ds_dr_flow", [("category", "Category", "string", "string"), ("category_type", "Type", "string", "string"), ("txns", "Payments", "integer", "number"),
                                      MONEY("money_in", "Money in (INR)"), MONEY("money_out", "Money out (INR)")], "Money in and out by category (the data behind the chart)", pos(3, 13, 3, 6)),
    table("t_dr_tx", "ds_dr_tx", [("day", "Date", "string", "string"), ("counterparty_clean", "Payee", "string", "string"), ("category", "Category", "string", "string"),
                                  ("direction", "Direction", "string", "string"), MONEY("amount", "Amount (INR)")], "Payments in the range", pos(0, 19, 6, 9)),
]}
page_dq = {"name": "page_dq", "displayName": "Data quality", "layout": [
    text("txt_dq", "# Data quality\nEvery statement is checked against the bank's own totals before it is loaded. Payments that no rule, payee list or Jev label could place with confidence are listed here for a person.", pos(0, 0, 6, 2)),
    table("t_status", "ds_status", [("file", "Statement", "string", "string"), ("statement_kind", "Kind", "string", "string"), ("period_from", "From", "string", "string"), ("period_to", "To", "string", "string"), ("txn_count", "Payments", "integer", "number"), ("duplicate_lines", "Already loaded", "integer", "number"),
                                    ("reconciled", "Reconciled", "string", "string"), ("ingested_at", "Loaded at", "string", "string")], "Statement reconciliation (weekly and monthly)", pos(0, 2, 6, 7)),
    table("t_dups", "ds_dups", [("file", "Statement", "string", "string"), ("statement_kind", "Kind", "string", "string"), ("duplicate_of", "Line items already loaded from", "string", "string"), ("lines", "Line items", "integer", "number")], "Duplicate line items (weekly and monthly overlap, counted once)", pos(0, 9, 6, 4)),
    chart("c_src", "bar", "ds_source", "category_source", "txns", None, "How payments were labelled", pos(0, 13, 3, 5), xlabel="Labelled by", ylabel="Payments"),
    table("t_src", "ds_source", [("category_source", "Labelled by", "string", "string"), ("txns", "Payments", "integer", "number"), MONEY("amount", "Amount (INR)")], "How payments were labelled (the data behind the chart)", pos(3, 13, 3, 5)),
    table("t_review", "ds_review", [("category", "Status", "string", "string"), ("direction", "Direction", "string", "string"), ("txns", "Payments", "integer", "number"), MONEY("amount", "Amount (INR)")], "Unlabelled payments", pos(0, 18, 6, 4)),
    table("t_queue", "ds_queue", [("category", "Status", "string", "string"), ("counterparty_clean", "Payee", "string", "string"), ("direction", "Direction", "string", "string"),
                                  ("txns", "Payments", "integer", "number"), MONEY("total", "Total (INR)"), ("reason", "Why", "string", "string")], "Review queue", pos(0, 22, 6, 8)),
]}

def build(mode="bank"):
    if mode == "credit-card":
        from build_card_dashboard import build as card_build
        return card_build()
    if mode != "bank":
        raise ValueError("unknown lakehouse_account_type")
    return {"datasets": datasets, "pages": [page_over, page_range, page_spend, page_rec, page_fund, page_dq]}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import settings
    mode = settings.load()["lakehouse_account_type"]
    OUT.write_text(json.dumps(build(mode), indent=1))
    print("wrote", OUT.name, OUT.stat().st_size, "bytes", "mode:", mode)
