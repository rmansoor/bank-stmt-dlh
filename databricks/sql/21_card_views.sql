-- USD credit card reporting. Never interpret a statement balance as cash available.
CREATE OR REPLACE VIEW __CATALOG__.gold.card_activity AS
SELECT *, CASE
    WHEN activity_type IN ('purchase', 'fee', 'interest') THEN amount
    WHEN activity_type = 'refund' THEN -amount ELSE 0 END AS spending_amount,
    CASE WHEN direction = 'debit' THEN amount ELSE -amount END AS debt_change
FROM __CATALOG__.gold.fact_transactions
WHERE account_type = 'credit-card' AND currency = 'USD';

CREATE OR REPLACE VIEW __CATALOG__.gold.card_monthly AS
SELECT date_format(txn_date, 'yyyy-MM') AS month, currency,
    ROUND(SUM(CASE WHEN activity_type = 'purchase' THEN amount ELSE 0 END), 2) AS purchases,
    ROUND(SUM(CASE WHEN activity_type = 'refund' THEN amount ELSE 0 END), 2) AS refunds,
    ROUND(SUM(CASE WHEN activity_type = 'payment' THEN amount ELSE 0 END), 2) AS payments,
    ROUND(SUM(CASE WHEN activity_type = 'fee' THEN amount ELSE 0 END), 2) AS fees,
    ROUND(SUM(CASE WHEN activity_type = 'interest' THEN amount ELSE 0 END), 2) AS interest,
    ROUND(SUM(CASE WHEN activity_type = 'cash-advance' THEN amount ELSE 0 END), 2) AS cash_advances,
    ROUND(SUM(CASE WHEN activity_type = 'balance-transfer' THEN amount ELSE 0 END), 2) AS balance_transfers,
    ROUND(SUM(spending_amount), 2) AS net_spending,
    ROUND(SUM(debt_change), 2) AS debt_change
FROM __CATALOG__.gold.card_activity GROUP BY 1, 2;

CREATE OR REPLACE VIEW __CATALOG__.gold.card_statement_balances AS
SELECT account_id, currency, period_from, period_to, opening AS opening_balance, closing AS balance_owed,
    debits AS charges, credits AS payments_and_credits, txn_count, file_sha256, parsed_at
FROM __CATALOG__.silver.statements WHERE account_type = 'credit-card' AND currency = 'USD';

CREATE OR REPLACE VIEW __CATALOG__.gold.card_kpis AS
WITH latest AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY period_to DESC, period_from DESC, parsed_at DESC, file_sha256 DESC) AS rn
    FROM __CATALOG__.gold.card_statement_balances),
debt AS (SELECT COALESCE(SUM(balance_owed), 0) AS balance_owed,
    MIN(period_to) AS oldest_account_as_of, MAX(period_to) AS latest_account_as_of FROM latest WHERE rn = 1),
a AS (SELECT COALESCE(ROUND(SUM(spending_amount), 2), 0) AS net_spending,
    COALESCE(ROUND(SUM(CASE WHEN activity_type = 'payment' THEN amount ELSE 0 END), 2), 0) AS payments,
    COALESCE(ROUND(SUM(CASE WHEN activity_type IN ('fee', 'interest') THEN amount ELSE 0 END), 2), 0) AS fees_and_interest
    FROM __CATALOG__.gold.card_activity)
SELECT debt.*, a.* FROM debt CROSS JOIN a;

CREATE OR REPLACE VIEW __CATALOG__.gold.card_spend_by_category AS
SELECT category, category_type, currency, COUNT(*) AS txns, ROUND(SUM(spending_amount), 2) AS amount
FROM __CATALOG__.gold.card_activity
WHERE activity_type IN ('purchase', 'refund', 'fee', 'interest') GROUP BY 1, 2, 3;
