"""Cloud transforms use fictional data. Spark execution is optional (requires pyspark and Java)."""
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'databricks' / 'dashboards'))
import dedupe
import lakehouse as lh
from extract_wells_fargo import parse_lines
from test_wells_fargo import fixture_lines


def card():
    return parse_lines([fixture_lines()], 'fictional.pdf')


def silver():
    return dedupe.prepare(pd.DataFrame(card().txns), 'fictional.pdf', 'content-hash', 'monthly')


def test_cloud_replay_preserves_identity_and_nullable_balance():
    first = silver()
    # Bronze replay holds exactly the raw line and metadata fields used by ingestion.
    raw = pd.DataFrame(card().txns)
    raw['identity_occurrence'] = raw.identity_occurrence.astype(float)
    replay = dedupe.prepare(raw, 'renamed.pdf', 'same-hash', 'monthly')
    assert list(first.txn_id) == list(replay.txn_id)
    assert first.balance.isna().all()
    appearances, rows = dedupe.resolve([first, replay], {}, {}, datetime(2026, 1, 28))
    assert len(rows) == 6 and appearances.is_duplicate.sum() == 6
    # An incremental job must also recognize already-persisted transaction identities.
    appearances, rows = dedupe.resolve([replay], dict(zip(first.txn_id, first.source_file)),
                                      dict(zip(first.txn_id, first.statement_kind)), datetime(2026, 1, 29))
    assert rows.empty and appearances.is_duplicate.all()


def test_gold_card_frames_and_legacy_defaults(monkeypatch):
    import categorize
    monkeypatch.setattr(categorize, 'jev_category', lambda *a: pytest.fail('AI must not see private card rows'))
    fact, recurring, review = lh.gold_tables(silver(), 'credit-card', use_jev=True)
    assert recurring.empty and set(fact.currency) == {'USD'}
    assert set(fact.account_type) == {'credit-card'}
    assert fact.balance.isna().all() and not fact.is_founder.any()
    assert set(review.currency) <= {'USD'}
    legacy = lh.metadata(pd.DataFrame({'amount': [1.0]}))
    assert legacy.account_type.iloc[0] == 'bank' and legacy.currency.iloc[0] == 'INR'
    with pytest.raises(ValueError, match='separate catalog'):
        lh.validate_mode(silver(), 'bank')
    wrong_currency = silver().assign(currency='INR')
    with pytest.raises(ValueError, match='separate catalog'):
        lh.validate_mode(wrong_currency, 'credit-card')


def test_bank_cash_email_guards():
    lh.require_bank_report('bank')
    with pytest.raises(ValueError, match='not implemented'):
        lh.require_bank_report('credit-card')


def test_job_pauses_card_email_and_preserves_bank_schedule(monkeypatch):
    monkeypatch.setenv('DATABRICKS_USER', 'example-user')
    spec = importlib.util.spec_from_file_location('cloud_job_under_test', ROOT / 'scripts' / 'dbx_job.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.project_settings, 'load', lambda: {'lakehouse_account_type': 'credit-card'})
    assert module.report_spec()['schedule']['pause_status'] == 'PAUSED'
    assert module.spec()['schedule']['pause_status'] == 'UNPAUSED'
    monkeypatch.setattr(module.project_settings, 'load', lambda: {'lakehouse_account_type': 'bank'})
    assert module.report_spec()['schedule']['pause_status'] == 'UNPAUSED'


def test_card_dashboard_has_usd_and_consistent_dataset_references():
    from build_dashboard import build
    definition = build('credit-card')
    serialized = json.dumps(definition)
    assert 'INR' not in serialized
    assert 'runway' not in serialized.lower()
    assert 'cashflow_daily' not in serialized and 'balance_daily' not in serialized
    assert 'USD' in serialized and len(definition['pages']) == 4
    names = {d['name'] for d in definition['datasets']}
    for page in definition['pages']:
        for item in page['layout']:
            for query in item['widget'].get('queries', []):
                assert query['query']['datasetName'] in names
    assert len(build('bank')['pages']) == 6


@pytest.fixture(scope='module')
def spark(tmp_path_factory):
    pytest.importorskip('pyspark')
    from pyspark.sql import SparkSession
    warehouse = tmp_path_factory.mktemp('spark-warehouse')
    session = (SparkSession.builder.master('local[1]').appName('bank-stmt-fictional-tests')
               .config('spark.driver.bindAddress', '127.0.0.1')
               .config('spark.sql.warehouse.dir', warehouse.as_uri())
               .config('spark.sql.shuffle.partitions', '1').getOrCreate())
    session.sparkContext.setLogLevel('ERROR')
    yield session
    session.stop()


def test_spark_typed_nulls_and_empty_tables(spark):
    fact, recurring, review = lh.gold_tables(silver(), 'credit-card')
    frame = lh.typed_frame(spark, fact, lh.FACT_SCHEMA)
    assert frame.count() == 6 and frame.filter('balance IS NOT NULL').count() == 0
    assert lh.typed_frame(spark, recurring, lh.RECURRING_SCHEMA).count() == 0
    assert lh.typed_frame(spark, review.iloc[:0], lh.REVIEW_SCHEMA).count() == 0


def load_cloud_tables(spark, tx, statements, mode='credit-card'):
    fact, recurring, review = lh.gold_tables(tx, mode)
    for layer in ('bronze', 'silver', 'gold'):
        spark.sql(f'CREATE DATABASE IF NOT EXISTS {layer}')
    for name, pdf, schema in (('fact_transactions', fact, lh.FACT_SCHEMA),
                              ('recurring_series', recurring, lh.RECURRING_SCHEMA),
                              ('review_queue', review, lh.REVIEW_SCHEMA)):
        lh.typed_frame(spark, pdf, schema).write.mode('overwrite').option('overwriteSchema', 'true').saveAsTable(f'gold.{name}')
    lh.typed_frame(spark, statements, 'account_id STRING, currency STRING, account_type STRING, period_from DATE, period_to DATE, opening DOUBLE, closing DOUBLE, debits DOUBLE, credits DOUBLE, txn_count INT, file_sha256 STRING, file STRING, statement_kind STRING, parsed_at TIMESTAMP').write.mode('overwrite').saveAsTable('silver.statements')
    spark.createDataFrame([], 'file_sha256 STRING, object_key STRING, statement_kind STRING, size_bytes BIGINT, ingested_at TIMESTAMP, reconciled BOOLEAN').write.mode('overwrite').saveAsTable('bronze.statement_files')
    spark.createDataFrame([], 'file_sha256 STRING, is_duplicate BOOLEAN, source_file STRING, statement_kind STRING, duplicate_of_file STRING, first_seen_at TIMESTAMP').write.mode('overwrite').saveAsTable('silver.transaction_sources')
    spark.createDataFrame([], 'file_sha256 STRING, errors STRING').write.mode('overwrite').saveAsTable('silver.quarantine')
    for filename in ('20_gold_views.sql', '21_card_views.sql'):
        sql = (ROOT / 'databricks' / 'sql' / filename).read_text().replace('__CATALOG__', 'spark_catalog')
        sql = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
        for statement in sql.split(';'):
            if statement.strip():
                spark.sql(statement)


def test_spark_card_sql_and_every_dashboard_query(spark):
    st = card()
    row = {'account_id': st.account_id, 'currency': 'USD', 'account_type': 'credit-card',
           'period_from': datetime(2025, 12, 28).date(), 'period_to': datetime(2026, 1, 27).date(),
           'opening': 100.0, 'closing': 87.0, 'debits': 47.0, 'credits': 60.0, 'txn_count': 6,
           'file_sha256': 'example', 'file': 'fictional.pdf', 'statement_kind': 'monthly', 'parsed_at': datetime(2026, 1, 28)}
    # An older statement for the same account must not be added to latest debt.
    old = {**row, 'period_to': datetime(2025, 12, 27).date(), 'closing': 100.0, 'file_sha256': 'older'}
    second = {**row, 'account_id': 'another-fictional-account', 'closing': 25.0, 'file_sha256': 'another'}
    load_cloud_tables(spark, silver(), pd.DataFrame([row, old, second]))
    kpi = spark.sql('select * from gold.card_kpis').first()
    assert kpi.balance_owed == 112.0 and kpi.net_spending == 37.0 and kpi.payments == 50.0
    monthly = spark.sql('select sum(net_spending) as spending, sum(payments) as payments from gold.card_monthly').first()
    assert monthly.spending == 37.0 and monthly.payments == 50.0
    assert spark.sql('select count(*) from gold.monthly_flow').first()[0] == 0
    assert spark.sql('select count(*) from gold.balance_daily').first()[0] == 0
    from build_dashboard import build
    for dataset in build('credit-card')['datasets']:
        query = '\n'.join(dataset['queryLines']).replace('__CATALOG__', 'spark_catalog')
        assert spark.sql(query).collect() is not None
    # Execute date-filtered card activity with the same semantics as the dashboard.
    filtered = spark.sql("select sum(spending_amount) as spending from gold.card_activity where txn_date >= DATE '2026-01-01'").first()
    assert filtered.spending == -3.0  # refund -10 plus fee 5 and interest 2; purchases were in December.


def test_card_secret_setup_allows_unencrypted_pdfs_and_skips_mail(monkeypatch):
    monkeypatch.setenv('DATABRICKS_HOST', 'https://workspace.example.test')
    monkeypatch.setenv('DATABRICKS_TOKEN', 'fictional-token')
    spec = importlib.util.spec_from_file_location('cloud_secrets_under_test', ROOT / 'scripts' / 'dbx_secrets.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.S['lakehouse_account_type'] = 'credit-card'
    module.S['statement_source'] = 'minio'
    module.REQUIRED_GROUPS = {'core', 'minio'}
    monkeypatch.setattr(module, 'load_dotenv', lambda *a: None)
    monkeypatch.setattr(module, 'dotenv_values', lambda *a: {
        'MINIO_ENDPOINT': 'https://storage.example.test', 'MINIO_ACCESS_KEY': 'fictional-key',
        'MINIO_SECRET_KEY': 'fictional-secret', 'PDF_PASSWORD': '', 'TYPESAFE_API_KEY': '',
    })
    for name in ('PDF_PASSWORD', 'TYPESAFE_API_KEY', 'MINIO_ENDPOINT', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY'):
        monkeypatch.delenv(name, raising=False)
    calls = []

    class Response:
        status_code = 200
        def json(self):
            return {}

    def post(url, **kwargs):
        calls.append((url, kwargs['json']))
        return Response()
    monkeypatch.setattr(module.httpx, 'post', post)
    assert module.main([]) == 0
    assert calls[0][0].endswith('/secrets/scopes/create')
    assert 'initial_manage_principal' not in calls[0][1]  # no grant to all workspace users
    keys = {payload['key'] for _, payload in calls if 'key' in payload}
    assert keys == {'minio_endpoint', 'minio_access_key', 'minio_secret_key'}


def test_cloud_preflight_reports_names_without_credentials():
    spec = importlib.util.spec_from_file_location('cloud_preflight_under_test', ROOT / 'scripts' / 'cloud_preflight.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = {'lakehouse_account_type': 'credit-card', 'statement_source': 'minio', 'catalog': 'cards', 'minio_bucket': 'example-bucket'}
    issues = module.check({}, config)
    assert 'Set DATABRICKS_TOKEN in .env' in issues
    values = {name: 'fictional-value' for name in ('DATABRICKS_HOST', 'DATABRICKS_TOKEN', 'DATABRICKS_HTTP_PATH', 'DATABRICKS_USER', 'MINIO_ENDPOINT', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY')}
    assert not module.check(values, config)
    values['MINIO_BUCKET'] = 'different-bucket'
    assert any('must match' in issue for issue in module.check(values, config))


def test_spark_hdfc_gold_and_all_bank_dashboard_queries(spark):
    from pipeline import build_silver
    tx, statements = build_silver(ROOT / 'samples' / 'statements', '')
    statements['statement_kind'] = 'monthly'
    load_cloud_tables(spark, tx, statements, mode='bank')
    assert spark.table('gold.fact_transactions').count() == 80
    assert spark.table('gold.recurring_series').count() > 0
    expected = statements.sort_values('period_to').iloc[-1].closing
    assert spark.sql('select cash from gold.kpis').first()[0] == expected
    assert spark.sql('select count(*) from gold.card_activity').first()[0] == 0
    from build_dashboard import build
    for dataset in build('bank')['datasets']:
        query = '\n'.join(dataset['queryLines']).replace('__CATALOG__', 'spark_catalog')
        assert spark.sql(query).collect() is not None


def test_spark_reference_seed_handles_null_reference_columns(spark):
    import ast
    import yaml
    import settings
    path = ROOT / 'databricks' / 'notebooks' / '01_reference_seed.py'
    module = ast.parse(path.read_text())
    start = next(i for i, node in enumerate(module.body) if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'categories' for t in node.targets))
    body = ast.Module(body=module.body[start:], type_ignores=[])
    context = {'BASE': str(ROOT), 'CAT': 'spark_catalog', 'spark': spark, 'pd': pd,
               'yaml': yaml, 'lakehouse': lh,
               'cfg': lambda name: yaml.safe_load((ROOT / 'config' / name).read_text())}
    spark.sql('CREATE DATABASE IF NOT EXISTS gold')
    exec(compile(body, str(path), 'exec'), context)
    assert spark.table('gold.payee_map').count() > 0
    assert spark.table('gold.dim_category').count() > 0
