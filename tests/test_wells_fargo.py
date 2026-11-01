"""Fictional statements only; never use personal PDF fixtures."""
import sys
from pathlib import Path
from decimal import Decimal

import pandas as pd
import pytest
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from extract import parse
from extract_wells_fargo import parse_lines
from pipeline import build_silver
from normalize import enrich
from categorize import categorize


def fixture_lines():
    return [
        'WELLS FARGO EXAMPLE CREDIT CARD', 'Account ending in 1234',
        'Statement Period 12/28/2025 to 01/27/2026',
        'Previous Balance $100.00', '- Payments $50.00', '- Other Credits $10.00',
        '+ Cash Advances $0.00', '+ Purchases, Balance Transfers & $40.00',
        'Other Charges', '+ Fees Charged $5.00', '+ Interest Charged $2.00',
        '= New Balance $87.00', 'Transactions',
        'Payments', '01/05 01/06 PAYMENTREF EXAMPLE PAYMENT 50.00',
        'Other Credits', '1234 01/06 01/07 REFUNDREF EXAMPLE REFUND 10.00',
        'Purchases, Balance Transfers & Other Charges',
        '1234 12/31 01/01 PURCHASE1 EXAMPLE STORE 20.00',
        '1234 12/31 01/01 PURCHASE2 EXAMPLE STORE 20.00',
        'Fees Charged', '01/15 01/15 FEEREF EXAMPLE FEE 5.00',
        'Interest Charged', 'INTEREST CHARGE ON PURCHASES 2.00',
        'INTEREST CHARGE ON CASH ADVANCES 0.00', '2026 Totals Year-to-Date',
        'TOTAL INTEREST CHARGED IN 2026 $2.00',
    ]


def write_pdf(path, lines):
    c = canvas.Canvas(str(path))
    t = c.beginText(40, 800)
    t.setFont('Helvetica', 9)
    for line in lines:
        t.textLine(line)
    c.drawText(t)
    c.save()


def test_sections_balance_and_year_rollover():
    st = parse_lines([fixture_lines()], 'fictional.pdf')
    assert st.errors == []
    assert len(st.txns) == 6
    assert st.txns[2]['txn_date'] == '31/12/25'
    assert st.txns[2]['value_date'] == '01/01/26'
    assert st.closing == Decimal('87.00')
    assert st.account_id != '1234'
    assert st.txns[0]['direction'] == 'credit'
    assert st.txns[-1]['activity_type'] == 'interest'


@pytest.mark.parametrize('original,replacement,expected', [
    ('+ Purchases, Balance Transfers & $40.00', '+ Purchases, Balance Transfers & $41.00', 'detail does not match'),
    ('= New Balance $87.00', '= New Balance $88.00', 'balance equation'),
    ('- Payments $50.00', 'Missing field', 'missing summary'),
    ('01/05 01/06 PAYMENTREF EXAMPLE PAYMENT 50.00', '01/05 broken transaction', 'unrecognized transaction'),
    ('01/05 01/06 PAYMENTREF EXAMPLE PAYMENT 50.00', '01/05 02/06 PAYMENTREF EXAMPLE PAYMENT 50.00', 'outside statement'),
])
def test_bad_statements_are_rejected(original, replacement, expected):
    lines = [replacement if l == original else l for l in fixture_lines()]
    st = parse_lines([lines], 'fictional.pdf')
    assert any(expected in e for e in st.errors)


def test_pdf_dispatch_and_duplicate_files(tmp_path):
    first = tmp_path / 'a.pdf'
    write_pdf(first, fixture_lines())
    (tmp_path / 'copy.pdf').write_bytes(first.read_bytes())
    assert parse(first, '').currency == 'USD'
    txns, stmts = build_silver(tmp_path, '')
    assert len(txns) == 6 and len(stmts) == 2
    assert txns.txn_id.is_unique
    purchases = txns[txns.activity_type == 'purchase']
    assert len(purchases) == 2 and purchases.txn_id.nunique() == 2


def test_account_identity_separates_cards():
    frames = []
    for account in ('1234', '5678'):
        lines = [l.replace('Account ending in 1234', f'Account ending in {account}') for l in fixture_lines()]
        df = pd.DataFrame(parse_lines([lines], 'fictional.pdf').txns)
        for col in ('txn_date', 'value_date'):
            df[col] = pd.to_datetime(df[col], format='%d/%m/%y')
        df['amount'] = df.amount.astype(float)
        df['balance'] = df.balance.astype(float)
        frames.append(enrich(df))
    assert set(frames[0].txn_id).isdisjoint(frames[1].txn_id)


def test_unsupported_pdf_rejected(tmp_path):
    pdf = tmp_path / 'unsupported.pdf'
    write_pdf(pdf, ['A different bank layout', 'No supported transactions'])
    assert any('unsupported' in e for e in parse(pdf, '').errors)
    txns, stmts = build_silver(tmp_path, '')
    assert txns.empty and not stmts.reconciled.any()


def test_card_reports_payments_are_not_income(tmp_path, monkeypatch):
    import reports
    pdf = tmp_path / 'fictional.pdf'
    write_pdf(pdf, fixture_lines())
    txns, stmts = build_silver(tmp_path, '')
    # Even when requested, private card rows must not call Jev.
    import categorize as cat_module
    monkeypatch.setattr(cat_module, 'jev_category', lambda *a: pytest.fail('AI called for private card'))
    fact = categorize(txns, use_jev=True)
    assert fact.loc[fact.activity_type == 'payment', 'category_type'].iloc[0] == 'transfer'
    assert fact.loc[fact.activity_type == 'refund', 'category_type'].iloc[0] == 'contra-expense'
    assert not fact.is_founder.any()
    silver, gold = tmp_path / 'silver', tmp_path / 'gold'
    silver.mkdir()
    stmts.to_parquet(silver / 'statements.parquet', index=False)
    txns.to_parquet(silver / 'transactions.parquet', index=False)
    monkeypatch.setattr(reports, 'SILVER', silver)
    monkeypatch.setattr(reports, 'GOLD', gold)
    monkeypatch.setattr(reports, 'DB', tmp_path / 'test.duckdb')
    reports.build_gold(use_jev=False)
    import duckdb
    with duckdb.connect(str(reports.DB)) as con:
        assert con.sql('select sum(net_spending) from monthly_summary').fetchone()[0] == 37
        assert con.sql('select sum(payments) from monthly_summary').fetchone()[0] == 50
        assert con.sql('select distinct currency from fact_transactions').fetchone()[0] == 'USD'


def test_mixed_bank_and_card_rejected(tmp_path):
    write_pdf(tmp_path / 'card.pdf', fixture_lines())
    write_pdf(tmp_path / 'bank.pdf', [
        'From : 01/01/2026 To : 31/01/2026',
        '01/01/26 EXAMPLE 000000000001 01/01/26 10.00 90.00',
        'STATEMENT SUMMARY', '100.00 1 0 10.00 0.00 90.00',
    ])
    with pytest.raises(SystemExit, match='separate folders'):
        build_silver(tmp_path, '')


def test_repeated_identical_purchases_are_preserved(tmp_path):
    lines = [l.replace('PURCHASE2', 'PURCHASE1') for l in fixture_lines()]
    write_pdf(tmp_path / 'repeated.pdf', lines)
    txns, stmts = build_silver(tmp_path, '')
    assert stmts.reconciled.all()
    assert len(txns[txns.activity_type == 'purchase']) == 2
    assert txns.txn_id.is_unique


def test_balance_transfer_is_not_purchase_spending():
    lines = [l.replace('PURCHASE1 EXAMPLE STORE', 'PURCHASE1 BALANCE TRANSFER') for l in fixture_lines()]
    st = parse_lines([lines], 'fictional.pdf')
    assert not st.errors
    assert st.txns[2]['activity_type'] == 'balance-transfer'


def test_failed_extraction_does_not_overwrite_previous_output(tmp_path, monkeypatch):
    import pipeline
    raw = tmp_path / 'raw'
    raw.mkdir()
    write_pdf(raw / 'unsupported.pdf', ['Unsupported statement'])
    silver = tmp_path / 'data' / 'silver'
    silver.mkdir(parents=True)
    existing = silver / 'transactions.parquet'
    existing.write_bytes(b'previous-output')
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path)
    monkeypatch.setenv('STATEMENTS_DIR', str(raw))
    monkeypatch.setenv('PDF_PASSWORD', '')
    with pytest.raises(SystemExit, match='No reconciled transactions; output was not changed'):
        pipeline.main()
    assert existing.read_bytes() == b'previous-output'
