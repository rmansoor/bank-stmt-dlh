"""USD credit card dashboard, built using the existing Lakeview widget helpers."""
from build_dashboard import ds, pos, text, counter, chart, table, date_filter, time_chart, MONEY, CAT

USD = {'type': 'number-currency', 'currencyCode': 'USD', 'abbreviation': 'compact',
       'decimalPlaces': {'type': 'max', 'places': 2}}
STR = lambda field, title: (field, title, 'string', 'string')
INT = lambda field, title: (field, title, 'integer', 'number')


def build():
    datasets = [
        ds('card_kpis', 'Credit card totals (USD)', f'select * from {CAT}.gold.card_kpis'),
        ds('card_monthly', 'Activity by transaction month (USD)', f'select * from {CAT}.gold.card_monthly order by month'),
        ds('card_balances', 'Statement balances owed (USD)', f"select account_id, cast(period_from as string) as period_from, cast(period_to as string) as period_to, opening_balance, balance_owed, charges, payments_and_credits from {CAT}.gold.card_statement_balances order by period_to desc"),
        ds('card_spend', 'Net spending by category (USD)', f'select * from {CAT}.gold.card_spend_by_category order by amount desc'),
        ds('card_activity', 'Credit card transactions (USD)', f"select txn_date, cast(txn_date as string) as day, account_id, counterparty_clean, activity_type, category, amount, spending_amount, debt_change from {CAT}.gold.card_activity order by txn_date desc, seq desc"),
        ds('card_status', 'Statement reconciliation', f"select file, cast(period_to as string) as period_to, cast(reconciled as string) as reconciled, txn_count, errors from {CAT}.gold.statement_status order by file"),
        ds('card_dups', 'Duplicate transaction appearances', f'select file, duplicate_of, lines from {CAT}.gold.duplicate_lines'),
        ds('card_copies', 'Repeated PDF content', f'select file_sha256, count(distinct object_key) as copies from {CAT}.bronze.statement_files group by file_sha256 having count(distinct object_key) > 1'),
        ds('card_review', 'Transactions needing review (USD)', f'select account_id, currency, category, counterparty_clean, direction, txns, total, reason from {CAT}.gold.review_queue order by total desc'),
    ]
    overview = {'name': 'card_overview', 'displayName': 'Overview', 'layout': [
        text('card_intro', '# __COMPANY__ Credit Cards\nUSD statement balances owed and transaction activity. Payments reduce debt; refunds offset spending. Figures cover the loaded statements, not a live card balance.', pos(0, 0, 6, 2)),
        counter('card_debt', 'card_kpis', 'balance_owed', 'Latest statement balance owed (USD)', pos(0, 2, 3, 3), USD),
        counter('card_net', 'card_kpis', 'net_spending', 'Net spending across loaded activity (USD)', pos(3, 2, 3, 3), USD),
        counter('card_paid', 'card_kpis', 'payments', 'Payments across loaded activity (USD)', pos(0, 5, 3, 3), USD),
        counter('card_cost', 'card_kpis', 'fees_and_interest', 'Fees and interest (USD)', pos(3, 5, 3, 3), USD),
        table('card_asof', 'card_kpis', [STR('oldest_account_as_of', 'Oldest account statement date'), STR('latest_account_as_of', 'Latest account statement date')], 'Balance dates across accounts', pos(0, 8, 6, 3)),
        chart('card_trend', 'bar', 'card_monthly', 'month', 'net_spending', None, 'Net spending by transaction month', pos(0, 11, 6, 6), ylabel='Amount (USD)'),
        table('card_month_table', 'card_monthly', [STR('month', 'Transaction month'), MONEY('purchases', 'Purchases / other charges (USD)'), MONEY('refunds', 'Refunds (USD)'), MONEY('payments', 'Payments (USD)'), MONEY('fees', 'Fees (USD)'), MONEY('interest', 'Interest (USD)'), MONEY('cash_advances', 'Advances (USD)'), MONEY('balance_transfers', 'Balance transfers (USD)'), MONEY('net_spending', 'Net spending (USD)')], 'Monthly activity', pos(0, 17, 6, 7)),
        table('card_statement_table', 'card_balances', [STR('account_id', 'Account key'), STR('period_from', 'Period start'), STR('period_to', 'Period end'), MONEY('opening_balance', 'Previous balance (USD)'), MONEY('balance_owed', 'New balance owed (USD)'), MONEY('charges', 'Charges (USD)'), MONEY('payments_and_credits', 'Payments / credits (USD)')], 'Reconciled statement balances', pos(0, 24, 6, 7)),
    ]}
    spend = {'name': 'card_spending', 'displayName': 'Spending', 'layout': [
        text('card_spend_intro', '# Spending in USD\nPurchases, fees and interest less refunds. Payments, advances and explicitly identified balance transfers are excluded. Review other charges and unclassified merchants.', pos(0, 0, 6, 2)),
        chart('card_category_chart', 'bar', 'card_spend', 'category', 'amount', None, 'Net spending by category', pos(0, 2, 6, 6), ylabel='Amount (USD)'),
        table('card_category_table', 'card_spend', [STR('category', 'Category'), INT('txns', 'Transactions'), MONEY('amount', 'Net spending (USD)')], 'Spending detail', pos(0, 8, 6, 7)),
    ]}
    activity = {'name': 'card_transactions', 'displayName': 'Transactions', 'layout': [
        text('card_activity_intro', '# Transactions\nFilter by transaction date. A positive debt change increases the amount owed. Statement dates and posting dates can differ from transaction dates.', pos(0, 0, 6, 2)),
        date_filter('card_date_filter', ['card_activity'], pos(0, 2, 6, 2)),
        counter('card_range_spend', 'card_activity', 'spending_amount', 'Net spending in selected range (USD)', pos(0, 4, 3, 3), USD),
        counter('card_range_debt', 'card_activity', 'debt_change', 'Debt change in selected range (USD)', pos(3, 4, 3, 3), USD),
        time_chart('card_week_chart', 'bar', 'card_activity', 'spending_amount', None, 'Net spending by week', pos(0, 7, 6, 6), 'WEEK', ylabel='Amount (USD)'),
        table('card_transaction_table', 'card_activity', [STR('day', 'Date'), STR('account_id', 'Account key'), STR('counterparty_clean', 'Merchant'), STR('activity_type', 'Activity'), STR('category', 'Category'), MONEY('amount', 'Amount (USD)')], 'Transactions in selected range', pos(0, 13, 6, 9)),
    ]}
    quality = {'name': 'card_quality', 'displayName': 'Data quality', 'layout': [
        text('card_quality_intro', '# Data quality\nEach loaded statement passed section totals and balance reconciliation. Failed statements remain quarantined. Duplicate PDFs and duplicate transaction appearances are shown separately.', pos(0, 0, 6, 2)),
        table('card_status_table', 'card_status', [STR('file', 'Statement'), STR('period_to', 'Period end'), STR('reconciled', 'Reconciled'), INT('txn_count', 'Transactions'), STR('errors', 'Validation errors')], 'Statement status and quarantine', pos(0, 2, 6, 7)),
        table('card_dups_table', 'card_dups', [STR('file', 'Statement'), STR('duplicate_of', 'First appearance'), INT('lines', 'Duplicate lines')], 'Duplicate transaction appearances', pos(0, 9, 6, 5)),
        table('card_copies_table', 'card_copies', [STR('file_sha256', 'Content hash'), INT('copies', 'Object copies')], 'Repeated PDF content', pos(0, 14, 6, 5)),
        table('card_review_table', 'card_review', [STR('account_id', 'Account key'), STR('category', 'Status'), STR('counterparty_clean', 'Merchant'), INT('txns', 'Transactions'), MONEY('total', 'Total (USD)'), STR('reason', 'Reason')], 'Review queue', pos(0, 19, 6, 8)),
    ]}
    # Dates in the as-of table are strings, matching the established table widget contract.
    datasets[0]['queryLines'] = [f"select balance_owed, net_spending, payments, fees_and_interest, cast(oldest_account_as_of as string) as oldest_account_as_of, cast(latest_account_as_of as string) as latest_account_as_of from {CAT}.gold.card_kpis"]
    return {'datasets': datasets, 'pages': [overview, spend, activity, quality]}
