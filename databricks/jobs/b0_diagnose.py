# Databricks notebook source
# MAGIC %md
# MAGIC # B0 diagnose: why did MinIO return HTTP 403 once?
# MAGIC
# MAGIC **Why this notebook exists.** The first B0 feasibility run failed with `403 Forbidden` on a HeadObject call to MinIO, which sits behind Cloudflare. This notebook probes the same bucket with a list, a HEAD, and two ranged GET calls, and prints only status codes and the `server` header name.
# MAGIC
# MAGIC **Result (2026-10-18).** Every call returned 200 or 206 via Cloudflare, and the feasibility notebook then passed twice. The 403 was treated as transient, so the production job retries MinIO calls (3 tries with backoff).
# MAGIC
# MAGIC **Use it again** if the job starts failing with 403 or other MinIO errors: it shows which call type fails. Read-only. No secrets or file contents are printed. Spec: `openspec/changes/databricks-lakehouse` (bolt B0).

# COMMAND ----------

# B0 diagnose: why does HeadObject return 403 from serverless compute? Prints status codes and
# response header names only. No secrets, no file contents.

# COMMAND ----------

# MAGIC %pip install -q boto3

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import json
import boto3, botocore
from botocore.config import Config
from botocore.exceptions import ClientError

import sys
from pathlib import Path
BASE = "/Workspace" + str(Path(dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()).parents[1])  # the deployed repo root
sys.path.insert(0, f"{BASE}/src")
import settings as _settings
S = _settings.load()
CAT, SCOPE = S["catalog"], S["secret_scope"]
get = lambda k: dbutils.secrets.get(SCOPE, k)
BUCKET, PREFIX = S["minio_bucket"], S["minio_prefix"]
mk = lambda **kw: boto3.client(
    "s3", endpoint_url=get("minio_endpoint"), aws_access_key_id=get("minio_access_key"),
    aws_secret_access_key=get("minio_secret_key"), region_name=S["minio_region"],
    config=Config(s3={"addressing_style": "path"}, connect_timeout=15, read_timeout=60, retries={"max_attempts": 1}, **kw))

out = {"boto3": boto3.__version__}
s3 = mk()
keys = []
try:
    r = s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)
    keys = [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith(".pdf")]
    out["list"] = {"status": r["ResponseMetadata"]["HTTPStatusCode"], "pdfs": len(keys), "server": r["ResponseMetadata"]["HTTPHeaders"].get("server")}
except ClientError as e:
    out["list"] = {"error": e.response["Error"].get("Code"), "status": e.response["ResponseMetadata"]["HTTPStatusCode"],
                   "server": e.response["ResponseMetadata"]["HTTPHeaders"].get("server")}

def probe(name, fn):
    try:
        r = fn()
        out[name] = {"status": r["ResponseMetadata"]["HTTPStatusCode"], "server": r["ResponseMetadata"]["HTTPHeaders"].get("server")}
    except ClientError as e:
        m = e.response["ResponseMetadata"]
        out[name] = {"error": e.response["Error"].get("Code"), "status": m["HTTPStatusCode"], "server": m["HTTPHeaders"].get("server"), "cf_ray": "cf-ray" in m["HTTPHeaders"]}
    except Exception as e:
        out[name] = {"exception": type(e).__name__}

if keys:
    k = keys[0]
    probe("head_default", lambda: s3.head_object(Bucket=BUCKET, Key=k))
    probe("get_range", lambda: s3.get_object(Bucket=BUCKET, Key=k, Range="bytes=0-9"))
    probe("get_sigv2_path", lambda: boto3.client("s3", endpoint_url=get("minio_endpoint"), aws_access_key_id=get("minio_access_key"),
          aws_secret_access_key=get("minio_secret_key"), region_name=S["minio_region"],
          config=Config(signature_version="s3v4", s3={"addressing_style": "path", "payload_signing_enabled": False})).get_object(Bucket=BUCKET, Key=k, Range="bytes=0-9"))

print("B0 DIAG", json.dumps(out))
dbutils.notebook.exit(json.dumps(out))
