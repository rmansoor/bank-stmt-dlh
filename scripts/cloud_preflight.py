"""Check cloud setup locally without printing credentials or contacting services."""
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import settings
from lakehouse import mode_currency


def check(values, config):
    issues = []
    mode_currency(config['lakehouse_account_type'])
    required = ['DATABRICKS_HOST', 'DATABRICKS_TOKEN', 'DATABRICKS_HTTP_PATH', 'DATABRICKS_USER']
    if config['statement_source'] == 'minio':
        required += ['MINIO_ENDPOINT', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY']
        if values.get('MINIO_BUCKET') and values['MINIO_BUCKET'] != config['minio_bucket']:
            issues.append('MINIO_BUCKET must match minio_bucket in config/settings.yaml')
        if not config.get('minio_bucket'):
            issues.append('Set minio_bucket in config/settings.yaml')
    elif config['statement_source'] == 'sharepoint':
        required += ['SP_TENANT_ID', 'SP_CLIENT_ID', 'SP_CLIENT_SECRET', 'SP_DRIVE_ID', 'SP_FOLDER_ID']
    else:
        issues.append('statement_source must be minio or sharepoint')
    for name in required:
        value = values.get(name, '')
        if not value or '<' in value or 'example.com' in value:
            issues.append(f'Set {name} in .env')
    if values.get('DATABRICKS_CATALOG') and values['DATABRICKS_CATALOG'] != config['catalog']:
        issues.append('DATABRICKS_CATALOG must match catalog in config/settings.yaml')
    return issues


def main():
    values = {**dotenv_values(ROOT / '.env'), **os.environ}
    config = settings.load()
    print('Mode:', config['lakehouse_account_type'], '| Currency:', mode_currency(config['lakehouse_account_type']))
    issues = check(values, config)
    if issues:
        print('\n'.join(issues))
        return 1
    print('Required values are present. This checks configuration only; cloud connectivity is not verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
