"""Run extraction for every statement PDF and write bronze and silver layers."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from extract import PARSER_VERSION, Statement, find_pdfs, parse
from normalize import enrich

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_silver(pdf_dir: Path, password: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    txn_frames, stmt_rows = [], []
    for pdf in find_pdfs(pdf_dir):
        st: Statement = parse(pdf, password)
        stmt_rows.append(
            {
                "file": pdf.name,
                "account_type": st.account_type,
                "currency": st.currency,
                "account_id": st.account_id,
                "file_sha256": sha256(pdf),
                "period_from": pd.to_datetime(st.period_from, format="%d/%m/%Y"),
                "period_to": pd.to_datetime(st.period_to, format="%d/%m/%Y"),
                "opening": float(st.opening),
                "closing": float(st.closing),
                "debits": float(st.debits),
                "credits": float(st.credits),
                "txn_count": len(st.txns),
                "reconciled": not st.errors,
                "errors": "; ".join(st.errors),
                "parser_version": PARSER_VERSION,
                "parsed_at": datetime.now(timezone.utc),
            }
        )
        if st.errors:
            continue  # quarantine: never load an unreconciled statement
        df = pd.DataFrame(st.txns)
        df["source_file"] = pdf.name
        df["account_type"] = st.account_type
        df["currency"] = st.currency
        txn_frames.append(df)
    stmts = pd.DataFrame(stmt_rows)
    if not txn_frames:
        return pd.DataFrame(), stmts
    txns = pd.concat(txn_frames, ignore_index=True)
    if txns["currency"].nunique() != 1 or txns["account_type"].nunique() != 1:
        raise SystemExit("Use separate folders for bank statements and credit cards, and for each currency")
    txns["txn_date"] = pd.to_datetime(txns["txn_date"], format="%d/%m/%y")
    txns["value_date"] = pd.to_datetime(txns["value_date"], format="%d/%m/%y")
    txns["amount"] = txns["amount"].astype(float)
    txns["balance"] = txns["balance"].astype(float)
    txns = enrich(txns).drop_duplicates("txn_id")  # a line in two statements counts once (see dedupe.py)
    return txns, stmts


def main() -> None:
    load_dotenv(ROOT / ".env")
    pdf_dir = Path(os.environ.get("STATEMENTS_DIR") or ROOT / "data" / "raw")
    pdf_dir = pdf_dir if pdf_dir.is_absolute() else ROOT / pdf_dir   # relative to the repo, whatever the cwd
    password = os.environ.get("PDF_PASSWORD", "")  # empty for unencrypted PDFs such as the synthetic samples
    if not find_pdfs(pdf_dir):
        raise SystemExit(f"no PDF in {pdf_dir} (set STATEMENTS_DIR)")
    txns, stmts = build_silver(pdf_dir, password)
    if txns.empty:
        raise SystemExit("No reconciled transactions; output was not changed. " + "; ".join(stmts["errors"].astype(str)))
    out = ROOT / "data" / "silver"
    out.mkdir(parents=True, exist_ok=True)
    stmts.to_parquet(out / "statements.parquet", index=False)
    txns.to_parquet(out / "transactions.parquet", index=False)
    bad = stmts[~stmts["reconciled"]]
    print(f"{len(stmts)} statements, {len(txns)} transactions, {len(bad)} failed reconciliation")
    for _, r in bad.iterrows():
        print("  FAILED", r["file"], r["errors"])


if __name__ == "__main__":
    main()
