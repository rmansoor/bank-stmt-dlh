"""Project settings from config/settings.yaml, found next to this package (laptop or the deployed workspace copy).

Names the pipeline needs that are not secrets: catalog, secret scope, MinIO bucket and prefix, statement source,
company name. Edit config/settings.yaml, not code.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
DEFAULTS = {
    "lakehouse_account_type": "bank",
    "catalog": "bank_stmt_dlh",
    "secret_scope": "bank-stmt-dlh",
    "minio_region": "us-east-1",
    "minio_bucket": "bank-stmt-dlh",
    "minio_prefix": "statements/",
    "statement_source": "sharepoint",
    "company_name": "ConceptLabs",
    "report_body_currency": "INR",
    "jev_confidence_threshold": 0.9,
    "review_amount_threshold": 5000,
}


def load() -> dict:
    data = yaml.safe_load(CONFIG.read_text()) if CONFIG.exists() else None
    return {**DEFAULTS, **(data or {})}


def slug(name: str) -> str:
    """'Acme Pvt Ltd' -> 'acme-pvt-ltd', for file names."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
