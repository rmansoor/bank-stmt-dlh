"""Upload statement PDFs to MinIO (or any S3-compatible bucket), the single home of the raw files.

    python scripts/upload_statements.py [folder] [--weekly]

--weekly puts the files under statements/weekly/ (weekly statements); without it they are monthly.

Folder defaults to STATEMENTS_DIR from .env. A file already present with the
same size is skipped, so it is safe to run again. After a new upload it starts the
Databricks ETL job (DATABRICKS_JOB_ID), so the data refreshes without waiting for the schedule. Databricks reads the files from
MinIO; nothing is copied to Databricks. Prints names and status only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import settings  # noqa: E402

PREFIX = settings.load()["minio_prefix"]


def minio_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        region_name=settings.load()["minio_region"],
        config=Config(s3={"addressing_style": "path"}, connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}),
    )


def to_minio(s3, bucket: str, f: Path, weekly: bool = False) -> str:
    key = f"{PREFIX}{'weekly/' if weekly else ''}{f.name}"
    try:
        if s3.head_object(Bucket=bucket, Key=key)["ContentLength"] == f.stat().st_size:
            return "skipped"
    except ClientError:
        pass
    s3.put_object(Bucket=bucket, Key=key, Body=f.read_bytes(), ContentType="application/pdf")
    return "uploaded"


def start_job() -> None:
    """Start the ETL job right after new files land, so they do not wait for the daily midnight (IST) schedule."""
    jid, tok, host = os.environ.get("DATABRICKS_JOB_ID"), os.environ.get("DATABRICKS_TOKEN"), os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    if not (jid and tok and host):
        return
    host = host if host.startswith("http") else "https://" + host
    r = httpx.post(f"{host}/api/2.1/jobs/run-now", headers={"Authorization": f"Bearer {tok}"}, json={"job_id": int(jid)}, timeout=60)
    print("ETL job started" if r.status_code == 200 else f"ETL job NOT started ({r.status_code}); the schedule will pick the files up")


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    weekly = "--weekly" in sys.argv
    folder = Path(args[0] if args else os.environ["STATEMENTS_DIR"])
    files = sorted(folder.glob("*.pdf"))
    s3, bucket = minio_client(), os.environ["MINIO_BUCKET"]
    bad, uploaded = 0, 0
    for f in files:
        try:
            m = to_minio(s3, bucket, f, weekly)
        except Exception as e:  # keep going so one failure does not hide the rest
            m = f"FAILED {type(e).__name__}"
        bad += m.startswith("FAILED")
        uploaded += m == "uploaded"
        print(f"{f.name:34} minio={m}")
    print(f"{len(files)} files, {bad} failures")
    if uploaded and not bad:
        start_job()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
