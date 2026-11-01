"""Read statement PDFs from MinIO with retries. Used by the Databricks ingest notebook.

MinIO sits behind Cloudflare, which once answered a valid request with HTTP 403, so every
call retries with backoff before the run fails.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable, TypeVar

import boto3
from botocore.config import Config

T = TypeVar("T")


def client(endpoint: str, access_key: str, secret_key: str, region: str = "us-east-1"):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}, connect_timeout=15, read_timeout=60, retries={"max_attempts": 1}),
    )


def with_retry(fn: Callable[[], T], tries: int = 3, base_delay: float = 2.0) -> T:
    last: Exception | None = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - any network or proxy error is retried
            last = e
            if i < tries - 1:
                time.sleep(base_delay * (2**i))
    assert last is not None
    raise last


def list_pdfs(s3, bucket: str, prefix: str = "statements/") -> list[dict]:
    """[{object_key, etag, size_bytes}] for every PDF under the prefix."""
    out, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
        r = with_retry(lambda: s3.list_objects_v2(**kw))
        out += [
            {"object_key": o["Key"], "etag": o["ETag"].strip('"'), "size_bytes": int(o["Size"])}
            for o in r.get("Contents", [])
            if o["Key"].lower().endswith(".pdf")
        ]
        token = r.get("NextContinuationToken")
        if not token:
            return sorted(out, key=lambda o: o["object_key"])


def download(s3, bucket: str, key: str, dest: Path) -> str:
    """Download to dest and return the SHA-256 of the file."""
    with_retry(lambda: s3.download_file(bucket, key, str(dest)))
    return hashlib.sha256(dest.read_bytes()).hexdigest()
