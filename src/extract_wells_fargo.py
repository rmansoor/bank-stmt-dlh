"""Wells Fargo text credit card statements. Decimal reconciliation, no network calls."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
import hashlib
import re

MONEY = r'(-?[\d,]+\.\d{2})'
SECTIONS = {
    'Payments': 'payment', 'Other Credits': 'refund', 'Cash Advances': 'cash-advance',
    'Purchases, Balance Transfers & Other Charges': 'purchase',
    'Fees Charged': 'fee', 'Interest Charged': 'interest',
}
SUMMARY = {
    'Previous Balance': 'opening', 'Payments': 'payment', 'Other Credits': 'refund',
    'Cash Advances': 'cash-advance', 'Purchases, Balance Transfers &': 'purchase',
    'Fees Charged': 'fee', 'Interest Charged': 'interest', 'New Balance': 'closing',
}
ROW = re.compile(r'^(?:\d{4}\s+)?(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(\S+)\s+(.+?)\s+\$?' + MONEY + r'$')


def parse_lines(pages, filename):
    from extract import Statement
    st = Statement(file=filename, bank='wells-fargo', account_type='credit-card', currency='USD')
    text = '\n'.join('\n'.join(p) for p in pages)
    period = re.search(r'Statement Period\s+(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})', text, re.I)
    account = re.search(r'Account ending in\s+(\d{4})', text, re.I)
    if not period or not account:
        st.errors.append('missing Wells Fargo statement period or account identifier')
        return st
    start, end = [datetime.strptime(d, '%m/%d/%Y') for d in period.groups()]
    st.period_from, st.period_to = start.strftime('%d/%m/%Y'), end.strftime('%d/%m/%Y')
    st.account_id = hashlib.sha256(('wells-fargo-credit-card|' + account[1]).encode()).hexdigest()[:16]
    if start > end or (end - start).days > 62:
        st.errors.append('invalid statement period')
    values = {}
    for line in pages[0]:
        for label, key in SUMMARY.items():
            m = re.match(r'^[+\-=]?\s*' + re.escape(label) + r'\s+\$' + MONEY, line.strip())
            if m:
                value = Decimal(m[1].replace(',', ''))
                if key in values and values[key] != value:
                    st.errors.append('conflicting summary: ' + key)
                values[key] = value
    missing = set(SUMMARY.values()) - values.keys()
    if missing:
        st.errors.append('missing summary fields: ' + ', '.join(sorted(missing)))
        return st
    totals = Counter()
    section = None
    in_transactions = False
    occurrences = Counter()

    def date_of(short, anchor):
        candidates = []
        for year in range(anchor.year - 1, anchor.year + 2):
            try:
                candidates.append(datetime.strptime(f'{short}/{year}', '%m/%d/%Y'))
            except ValueError:
                pass
        return min(candidates, key=lambda d: abs((d - anchor).days))

    def append(kind, amount, date, posted, ref, narration):
        if amount < 0:
            st.errors.append('unexpected negative detail amount')
            return
        if not amount:
            return
        totals["purchase" if kind == "balance-transfer" else kind] += amount
        occurrence_key = (date, posted, ref, amount, kind)
        occurrences[occurrence_key] += 1
        st.txns.append(dict(txn_date=date.strftime('%d/%m/%y'), value_date=posted.strftime('%d/%m/%y'),
            narration=narration, ref_no=ref, amount=amount, balance=None,
            direction='credit' if kind in ('payment', 'refund') else 'debit',
            activity_type=kind, account_id=st.account_id, account_type='credit-card', currency='USD',
            identity_occurrence=occurrences[occurrence_key], seq=len(st.txns)))

    for lines in pages:
        for raw in lines:
            line = raw.strip()
            if line.startswith('Transactions'):
                in_transactions = True
            if re.match(r'^\d{4} Totals Year-to-Date', line):
                in_transactions = False
            if not in_transactions:
                continue
            if line in SECTIONS:
                section = SECTIONS[line]
                continue
            m = ROW.match(line)
            if m:
                if not section:
                    st.errors.append('transaction outside recognized section')
                    continue
                trans, post, ref, narration, amount = m.groups()
                posted = date_of(post, end)
                date = date_of(trans, posted)
                if not start <= posted <= end:
                    st.errors.append('posting date outside statement period')
                kind = section
                if section == 'purchase' and re.search(r'\bBALANCE TRANSFER\b', narration, re.I):
                    kind = 'balance-transfer'
                append(kind, Decimal(amount.replace(',', '')), date, posted, ref, narration)
            elif section in ('interest', 'fee'):
                charge = re.match(r'^(.+?)\s+\$?' + MONEY + '$', line)
                if charge and not line.startswith('TOTAL'):
                    append(section, Decimal(charge[2].replace(',', '')), end, end,
                           f'{section}:{end:%Y%m%d}:{charge[1]}', charge[1])
            if re.match(r'^(?:\d{4}\s+)?\d{2}/\d{2}\s', line) and not m:
                st.errors.append('unrecognized transaction row')
    for kind in SECTIONS.values():
        if totals[kind] != values[kind]:
            st.errors.append('detail does not match summary: ' + kind)
    st.opening, st.closing = values['opening'], values['closing']
    st.debits = sum((values[k] for k in ('purchase', 'cash-advance', 'fee', 'interest')), Decimal(0))
    st.credits = values['payment'] + values['refund']
    if st.opening + st.debits - st.credits != st.closing:
        st.errors.append('credit card balance equation does not reconcile')
    st.dr_count = sum(r['direction'] == 'debit' for r in st.txns)
    st.cr_count = len(st.txns) - st.dr_count
    if not st.txns:
        st.errors.append('no recognized transactions')
    return st
