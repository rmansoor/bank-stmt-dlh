"""Create the MinIO bucket for statement archives. Safe to run again.

    python scripts/bootstrap_minio.py

Needs MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET in .env.
Prints status only, never the keys.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import settings


def main() -> int:
    load_dotenv(ROOT / ".env")
    bucket = os.environ["MINIO_BUCKET"]
    region = settings.load()["minio_region"]
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        region_name=region,
        config=Config(s3={"addressing_style": "path"}, connect_timeout=10, read_timeout=20, retries={"max_attempts": 1}),
    )
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"exists   {bucket}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            print(f"FAILED   head {bucket}: {e.response['Error']['Code']}")
            return 1
        location = {} if region == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": region}}
        s3.create_bucket(Bucket=bucket, **location)
        print(f"created  {bucket}")
    # keep prefixes visible in the console
    for prefix in ("statements/", "exports/"):
        s3.put_object(Bucket=bucket, Key=prefix, Body=b"")
    print("ready    statements/ (raw PDFs), exports/ (parquet and reports)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
