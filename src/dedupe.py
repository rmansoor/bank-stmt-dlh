"""Line items that appear in more than one statement (weekly and monthly overlap). No AI, no Spark.

A line item has one identity (normalize.enrich -> txn_id). Every appearance is recorded; the second and later
ones are tagged duplicate. The monthly statement is the final record, so its text replaces a weekly one.
"""
from __future__ import annotations

import pandas as pd

import normalize
from lakehouse import metadata

APPEARANCE_COLS = ["txn_id", "file_sha256", "source_file", "statement_kind", "seq", "narration", "first_seen_at",
                   "is_duplicate", "duplicate_of_file"]


def prepare(raw: pd.DataFrame, source_file: str, file_sha256: str, statement_kind: str) -> pd.DataFrame:
    """Parsed lines of ONE statement (dd/mm/yy strings, numeric amounts) -> enriched silver rows."""
    df = metadata(raw)
    df["txn_date"] = pd.to_datetime(df["txn_date"], format="%d/%m/%y")
    df["value_date"] = pd.to_datetime(df["value_date"], format="%d/%m/%y")
    df["amount"], df["balance"] = df["amount"].astype(float), df["balance"].astype(float)
    df["seq"] = df["seq"].astype(int)
    df["source_file"], df["file_sha256"], df["statement_kind"] = source_file, file_sha256, statement_kind
    return normalize.enrich(df)


def collisions(df: pd.DataFrame) -> int:
    """Number of txn_id values that occur more than once inside one statement (a file with any is quarantined)."""
    return int(df["txn_id"].duplicated().sum())


def resolve(files: list[pd.DataFrame], first_file: dict[str, str], row_kind: dict[str, str], now) -> tuple[pd.DataFrame, pd.DataFrame]:
    """files: enriched statements in processing order. first_file: txn_id -> file of its first appearance already
    stored. row_kind: txn_id -> statement kind of the row now in silver.transactions.

    Returns (appearances, rows): one appearance per line; rows = silver.transactions rows to insert or replace
    (a new line, or a weekly row superseded by a monthly one)."""
    first, kind = dict(first_file), dict(row_kind)
    appearances, rows = [], {}
    for df in files:
        for r in df.to_dict("records"):
            tid = r["txn_id"]
            dup = tid in first
            appearances.append({**{c: r[c] for c in ("txn_id", "file_sha256", "source_file", "statement_kind", "seq", "narration")},
                                "first_seen_at": now, "is_duplicate": dup, "duplicate_of_file": first.get(tid)})
            if not dup:
                first[tid], kind[tid] = r["source_file"], r["statement_kind"]
                rows[tid] = r
            elif r["statement_kind"] == "monthly" and kind[tid] == "weekly":
                kind[tid] = "monthly"
                rows[tid] = r
    return pd.DataFrame(appearances, columns=APPEARANCE_COLS), pd.DataFrame(list(rows.values()))
