"""Shared cloud metadata, validation and typed Spark writes; no Spark import required."""
import pandas as pd

METADATA_TYPES = {
    'account_type': 'STRING', 'currency': 'STRING', 'account_id': 'STRING',
    'activity_type': 'STRING', 'identity_occurrence': 'BIGINT',
}
METADATA_DEFAULTS = {
    'account_type': 'bank', 'currency': 'INR', 'account_id': '',
    'activity_type': 'bank-transaction', 'identity_occurrence': 1,
}
STATEMENT_METADATA = ['account_type', 'currency', 'account_id']
FACT_SCHEMA = (
    'txn_id STRING, source_file STRING, seq INT, txn_date DATE, value_date DATE, narration STRING, '
    'ref_no STRING, amount DOUBLE, balance DOUBLE, direction STRING, signed_amount DOUBLE, '
    'channel STRING, counterparty_clean STRING, year INT, month INT, category STRING, '
    'category_type STRING, category_source STRING, category_confidence DOUBLE, review_reason STRING, '
    'is_founder BOOLEAN, ' + ', '.join(f'{k} {v}' for k, v in METADATA_TYPES.items())
)
RECURRING_SCHEMA = (
    'counterparty_clean STRING, direction STRING, cadence STRING, occurrences INT, typical_amount DOUBLE, '
    'first_seen DATE, last_seen DATE, next_expected DATE, category STRING, source STRING, ended BOOLEAN'
)
REVIEW_SCHEMA = (
    'account_id STRING, currency STRING, category STRING, counterparty_clean STRING, direction STRING, '
    'txns BIGINT, total DOUBLE, reason STRING, example STRING'
)


def metadata(df):
    """Legacy bronze/silver rows are HDFC; never infer a currency from an amount."""
    out = df.copy()
    for key, default in METADATA_DEFAULTS.items():
        if key not in out:
            out[key] = default
        else:
            out[key] = out[key].fillna(default)
    out['identity_occurrence'] = out['identity_occurrence'].astype(int)
    return out


def mode_currency(mode):
    if mode not in ('bank', 'credit-card'):
        raise ValueError('lakehouse_account_type must be bank or credit-card')
    return 'USD' if mode == 'credit-card' else 'INR'


def validate_mode(df, mode):
    expected_currency = mode_currency(mode)
    if not df.empty and ((df.account_type != mode) | (df.currency != expected_currency)).any():
        raise ValueError('Stored/input account type or currency conflicts with lakehouse_account_type; use a separate catalog')


def statement_metadata(st):
    return {key: getattr(st, key) for key in STATEMENT_METADATA}


def require_bank_report(mode):
    if mode != 'bank':
        raise ValueError('Weekly cash email is available only for bank accounts; credit-card email is not implemented')


def records(pdf, names):
    """Native Python tuples for an explicit schema, including empty and all-null frames."""
    def native(value):
        if value is None or pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        return value.item() if hasattr(value, 'item') else value
    return [tuple(native(value) for value in row) for row in pdf[names].itertuples(index=False, name=None)]


def typed_frame(spark, pdf, schema):
    names = [field.name for field in schema] if not isinstance(schema, str) else [part.strip().split()[0] for part in schema.split(',')]
    return spark.createDataFrame(records(pdf, names), schema=schema)


def gold_tables(tx, mode, use_jev=False, cache=None):
    """Pure gold transforms shared with tests; schemas live above for nullable Spark writes."""
    import categorize
    import recurring
    tx = metadata(tx)
    validate_mode(tx, mode)
    if tx.empty:
        raise ValueError('No silver transactions; ingest reconciled statements first')
    fact = categorize.categorize(tx, use_jev=use_jev, jev_cache=cache)
    fact['is_founder'] = fact['is_founder'].fillna(False).astype(bool) if 'is_founder' in fact else False
    fact['category_confidence'] = fact.category_confidence.astype(float)
    fact['review_reason'] = fact.review_reason.fillna('')
    if mode == 'credit-card':
        rec = pd.DataFrame(columns=[part.strip().split()[0] for part in RECURRING_SCHEMA.split(',')])
    else:
        rec = pd.concat([recurring.detect(fact).assign(source='detected'),
                         recurring.declared(fact).assign(source='declared')], ignore_index=True)
        ended = '|'.join(recurring.ended_patterns())
        rec['ended'] = rec.counterparty_clean.str.contains(ended, case=False, regex=True) if ended else False
    for col in ('first_seen', 'last_seen', 'next_expected'):
        rec[col] = pd.to_datetime(rec[col]).dt.date
    review = (fact[fact.category.isin(['human-intervention-required', 'unclassified'])]
              .groupby(['account_id', 'currency', 'category', 'counterparty_clean', 'direction'], as_index=False)
              .agg(txns=('amount', 'size'), total=('amount', 'sum'), reason=('review_reason', 'first'), example=('narration', 'first')))
    for col in ('txn_date', 'value_date'):
        fact[col] = pd.to_datetime(fact[col]).dt.date
    return fact, rec, review
