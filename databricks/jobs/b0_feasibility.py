# Databricks notebook source
# MAGIC %md
# MAGIC # B0 feasibility: can serverless Databricks run the bank ETL?
# MAGIC
# MAGIC **Why this notebook exists.** The plan keeps raw statement PDFs only in MinIO (bucket `bank-stmt-dlh`) and reads them from Databricks on demand. Before building on that, this one-off test checked three things on Free Edition serverless compute:
# MAGIC 1. `pdfplumber` can be installed with `%pip`.
# MAGIC 2. MinIO is reachable from Databricks over the internet, with the key from secret scope `bank-stmt-dlh`.
# MAGIC 3. A real statement can be downloaded to temporary disk, opened with the PDF password from the secret scope, and parsed.
# MAGIC
# MAGIC **Result (2026-10-18).** All three passed. January statement: 3 pages, 28 transaction lines, same as the local parser. The temporary file is deleted after parsing.
# MAGIC
# MAGIC **Safe to rerun.** Read-only on MinIO. Prints counts only, never secrets, names or amounts. Kept as a record and a quick health check of the MinIO link. Spec: `openspec/changes/databricks-lakehouse` (bolt B0).

# COMMAND ----------

# B0 feasibility: can serverless compute (1) install pdfplumber, (2) reach MinIO,
# (3) read one statement PDF? Prints counts only, never secrets, names or amounts.

# COMMAND ----------

# MAGIC %pip install -q pdfplumber boto3

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import json, os, re, tempfile, time

import boto3
import pdfplumber
from botocore.config import Config

import sys
from pathlib import Path
BASE = "/Workspace" + str(Path(dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()).parents[1])  # the deployed repo root
sys.path.insert(0, f"{BASE}/src")
import settings as _settings
S = _settings.load()
CAT, SCOPE = S["catalog"], S["secret_scope"]
get = lambda k: dbutils.secrets.get(SCOPE, k)
BUCKET, PREFIX = S["minio_bucket"], S["minio_prefix"]

result = {"pdfplumber": pdfplumber.__version__}
s3 = boto3.client(
    "s3",
    endpoint_url=get("minio_endpoint"),
    aws_access_key_id=get("minio_access_key"),
    aws_secret_access_key=get("minio_secret_key"),
    region_name=S["minio_region"],
    config=Config(s3={"addressing_style": "path"}, connect_timeout=15, read_timeout=60, retries={"max_attempts": 2}),
)

t0 = time.time()
objs = s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX).get("Contents", [])
pdfs = sorted(o["Key"] for o in objs if o["Key"].endswith(".pdf"))
result["minio_reachable"] = True
result["pdfs_in_minio"] = len(pdfs)
result["list_seconds"] = round(time.time() - t0, 2)

# download the first file to temporary disk, parse, delete
key = pdfs[0]
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "s.pdf")
    t1 = time.time()
    s3.download_file(BUCKET, key, path)
    result["download_seconds"] = round(time.time() - t1, 2)
    try:
        password = get("pdf_password")
    except Exception:
        password = ""
    with pdfplumber.open(path, password=password) as pdf:
        result["pages"] = len(pdf.pages)
        lines = []
        for p in pdf.pages:
            lines += (p.extract_text(x_tolerance=1.5, y_tolerance=3) or "").splitlines()
    import extract
    statement = extract.parse(Path(path), password)
    result["transaction_lines"] = len(statement.txns)
    result["reconciled"] = not statement.errors
    result["validation_errors"] = len(statement.errors)
result["temp_file_removed"] = not os.path.exists(path)

print("B0 RESULT", json.dumps(result))
dbutils.notebook.exit(json.dumps(result))
