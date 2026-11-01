# Databricks notebook source
# MAGIC %md
# MAGIC # 02 Ingest: statement source to bronze and silver (bolts B3, B8, S2, W2, W3)
# MAGIC
# MAGIC **Why.** Raw statement PDFs live only in the statement source: a SharePoint folder read through Microsoft Graph (`statement_source: sharepoint`,
# MAGIC folders `01 MONTHLY STATEMENTS` and `02 WEEKLY STATEMENTS`), or MinIO until it is decommissioned. This notebook lists them, skips files already loaded,
# MAGIC downloads each new one to temporary disk, parses it with the same deterministic code as the local pipeline, and checks it against
# MAGIC the bank's own totals. A statement that does not reconcile goes to `silver.quarantine` and is never loaded further.
# MAGIC
# MAGIC **Duplicates.** Weekly and monthly statements overlap. A line item is identified by date, reference, amount and running balance. Every appearance of a
# MAGIC line is written to `silver.transaction_sources`; the second and later ones are tagged `is_duplicate` and never reach `silver.transactions` twice.
# MAGIC The monthly text replaces a weekly one. Logic lives in `src/dedupe.py` (unit tested).
# MAGIC
# MAGIC **Reads.** SharePoint or MinIO, chosen in `config/settings.yaml` (secret scope `bank-stmt-dlh`). **Writes.** `bronze.statement_files`, `bronze.raw_lines`, `silver.statements`, `silver.transactions`, `silver.transaction_sources`, `silver.quarantine`.
# MAGIC
# MAGIC **Tells the job whether to continue.** Sets task value `run_gold` to `true` only when there are new files, or when the job was started with `force=true`
# MAGIC (`make run`, used after a config or code change). Otherwise the job stops here and nothing is rebuilt.
# MAGIC
# MAGIC **Safe to rerun.** A listed file whose key + etag are unknown is downloaded and hashed; if its SHA-256 is already in bronze it is only re-registered (new location or etag), never parsed again. The one-off rebuild of silver with the current line key runs only while `silver.transaction_sources` is empty. No PDF is stored in Databricks.

# COMMAND ----------

# MAGIC %pip install -q pdfplumber boto3 pyyaml httpx

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

from pathlib import Path
BASE = "/Workspace" + str(Path(dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()).parents[1])  # the deployed repo root
sys.path.insert(0, f"{BASE}/src")
import settings as _settings
S = _settings.load()
CAT, SCOPE = S["catalog"], S["secret_scope"]

import pandas as pd
import yaml
import extract, normalize, dedupe, lakehouse, minio_reader as mr, sharepoint_reader as spr
MODE = S["lakehouse_account_type"]
lakehouse.mode_currency(MODE)

BUCKET, PREFIX = S["minio_bucket"], S["minio_prefix"]
secret = lambda k: dbutils.secrets.get(SCOPE, k)
SOURCE = S["statement_source"]
assert SOURCE in ("minio", "sharepoint"), f"unknown statement_source {SOURCE!r}"
if SOURCE == "sharepoint":
    # The app is granted the year folder (secret sp_folder_id); the monthly and weekly statements are its sub-folders.
    cred = (secret("sp_tenant_id"), secret("sp_client_id"), secret("sp_client_secret"), secret("sp_drive_id"))
    subs = spr.SharePoint(*cred, secret("sp_folder_id")).subfolders()
    folders = {}
    for name, fid in subs.items():
        for kind in ("monthly", "weekly"):
            if kind in name.lower():
                folders[kind] = fid
    assert "monthly" in folders, f"no monthly sub-folder under the year folder: {list(subs)}"
    print("statement folders:", {k: "found" for k in folders})
    clients = {k: spr.SharePoint(*cred, fid) for k, fid in folders.items()}
    list_files = lambda: [{**o, "object_key": f"{k}/{o['object_key']}", "kind": k} for k, c in clients.items() for o in c.list_pdfs()]
    fetch = lambda o, path: clients[o["kind"]].download(o, path)
else:
    s3 = mr.client(secret("minio_endpoint"), secret("minio_access_key"), secret("minio_secret_key"), region=S["minio_region"])
    # keys under <prefix>weekly/ are weekly statements, every other PDF under the prefix is monthly
    list_files = lambda: [{**o, "kind": "weekly" if "/weekly/" in o["object_key"] else "monthly"} for o in mr.list_pdfs(s3, BUCKET, PREFIX)]
    fetch = lambda o, path: mr.download(s3, BUCKET, o["object_key"], path)
print("statement source:", SOURCE)
try:
    PASSWORD = secret("pdf_password")
except Exception:
    PASSWORD = ""  # unencrypted statements need no secret

# COMMAND ----------

# MAGIC %md
# MAGIC ## Tables (created once)

# COMMAND ----------

SOURCES_SCHEMA = ("txn_id STRING, file_sha256 STRING, source_file STRING, statement_kind STRING, seq INT, narration STRING, "
                  "first_seen_at TIMESTAMP, is_duplicate BOOLEAN, duplicate_of_file STRING")
ddl = [
    f"""CREATE TABLE IF NOT EXISTS {CAT}.bronze.statement_files (object_key STRING, etag STRING, file_sha256 STRING, size_bytes BIGINT,
        ingested_at TIMESTAMP, parser_version STRING, reconciled BOOLEAN)""",
    f"""CREATE TABLE IF NOT EXISTS {CAT}.bronze.raw_lines (file_sha256 STRING, source_file STRING, seq INT, txn_date STRING, narration STRING,
        ref_no STRING, value_date STRING, amount DOUBLE, balance DOUBLE, direction STRING)""",
    f"""CREATE TABLE IF NOT EXISTS {CAT}.silver.statements (file_sha256 STRING, file STRING, period_from DATE, period_to DATE, opening DOUBLE,
        closing DOUBLE, debits DOUBLE, credits DOUBLE, txn_count INT, parser_version STRING, parsed_at TIMESTAMP)""",
    f"""CREATE TABLE IF NOT EXISTS {CAT}.silver.transactions (txn_id STRING, file_sha256 STRING, source_file STRING, seq INT, txn_date DATE,
        value_date DATE, narration STRING, ref_no STRING, amount DOUBLE, balance DOUBLE, direction STRING, signed_amount DOUBLE,
        channel STRING, counterparty_clean STRING, year INT, month INT)""",
    f"""CREATE TABLE IF NOT EXISTS {CAT}.silver.transaction_sources ({SOURCES_SCHEMA})""",
    f"""CREATE TABLE IF NOT EXISTS {CAT}.silver.quarantine (file_sha256 STRING, file STRING, errors STRING, quarantined_at TIMESTAMP)""",
]
for q in ddl:
    spark.sql(q)

def add_column(table, col, typ, backfill=None):
    if col not in [r.col_name for r in spark.sql(f"DESCRIBE {CAT}.{table}").collect()]:
        spark.sql(f"ALTER TABLE {CAT}.{table} ADD COLUMNS ({col} {typ})")
    if backfill:
        spark.sql(f"UPDATE {CAT}.{table} SET {col} = '{backfill}' WHERE {col} IS NULL")

add_column("bronze.statement_files", "source", "STRING", "minio")             # files loaded before SharePoint came from MinIO
add_column("bronze.statement_files", "statement_kind", "STRING", "monthly")   # and were all monthly statements
add_column("silver.statements", "statement_kind", "STRING", "monthly")
add_column("silver.transactions", "statement_kind", "STRING", "monthly")
for table in ("bronze.raw_lines", "silver.transactions"):
    for col, typ in lakehouse.METADATA_TYPES.items():
        add_column(table, col, typ, str(lakehouse.METADATA_DEFAULTS[col]))
for col in lakehouse.STATEMENT_METADATA:
    add_column("silver.statements", col, lakehouse.METADATA_TYPES[col], str(lakehouse.METADATA_DEFAULTS[col]))
# Fail before processing or writing if this catalog belongs to another account mode.
lakehouse.validate_mode(lakehouse.metadata(spark.table(f"{CAT}.silver.transactions").select("account_type", "currency").distinct().toPandas()), MODE)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Helpers: merge, append

# COMMAND ----------

def merge(df_pd, table, key, cols):
    if df_pd is None or len(df_pd) == 0:
        return 0
    schema = spark.table(f"{CAT}.{table}").select(*cols).schema
    lakehouse.typed_frame(spark, df_pd, schema).createOrReplaceTempView("_stage")
    sets = ", ".join(f"t.{c} = s.{c}" for c in cols if c != key)
    spark.sql(f"""MERGE INTO {CAT}.{table} t USING _stage s ON t.{key} = s.{key}
                  WHEN MATCHED THEN UPDATE SET {sets} WHEN NOT MATCHED THEN INSERT *""")
    return len(df_pd)

def append(rows, table):
    if rows:
        schema = spark.table(f"{CAT}.{table}").schema
        frame = pd.DataFrame(rows).reindex(columns=schema.fieldNames())
        lakehouse.typed_frame(spark, frame, schema).write.mode("append").saveAsTable(f"{CAT}.{table}")

TX_COLS = ["txn_id", "file_sha256", "source_file", "seq", "txn_date", "value_date", "narration", "ref_no", "amount", "balance", "direction",
           "signed_amount", "channel", "counterparty_clean", "year", "month", "statement_kind"] + list(lakehouse.METADATA_TYPES)

def write_resolved(appearances, rows):
    # Persist facts before their completion records so a failed write can be retried.
    merge(rows if len(rows) else None, "silver.transactions", "txn_id", TX_COLS)
    if len(appearances):
        lakehouse.typed_frame(spark, appearances, SOURCES_SCHEMA).write.mode("append").saveAsTable(f"{CAT}.silver.transaction_sources")

now = datetime.now(timezone.utc)

# COMMAND ----------

# MAGIC %md
# MAGIC ## One-off: rebuild silver with the current line key (only while transaction_sources is empty)
# MAGIC The key used to include the narration, which weekly and monthly statements print differently. Rebuilt from `bronze.raw_lines`, no PDF needed.

# COMMAND ----------

if spark.table(f"{CAT}.silver.transaction_sources").count() == 0 and spark.table(f"{CAT}.silver.transactions").count() > 0:
    order = (spark.table(f"{CAT}.bronze.statement_files").groupBy("file_sha256").agg({"ingested_at": "min", "statement_kind": "first"})
             .toPandas().rename(columns={"min(ingested_at)": "t", "first(statement_kind)": "kind"}))
    ok = {r.file_sha256: r.file for r in spark.table(f"{CAT}.silver.statements").select("file_sha256", "file").collect()}
    raw_all = spark.table(f"{CAT}.bronze.raw_lines").toPandas()
    cols = ["seq", "txn_date", "narration", "ref_no", "value_date", "amount", "balance", "direction"] + list(lakehouse.METADATA_TYPES)
    batch = []
    for r in order.sort_values(["t", "file_sha256"]).itertuples():
        if r.file_sha256 in ok:
            batch.append(dedupe.prepare(raw_all[raw_all.file_sha256 == r.file_sha256][cols], ok[r.file_sha256], r.file_sha256, r.kind or "monthly"))
    appearances, rows = dedupe.resolve(batch, {}, {}, now)
    spark.sql(f"DELETE FROM {CAT}.silver.transactions")
    write_resolved(appearances, rows)
    print(f"rebuilt silver: {len(rows)} transactions from {len(appearances)} appearances ({int(appearances.is_duplicate.sum())} duplicates)")
else:
    print("silver already uses the current line key")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Discover: which listed files are unknown?

# COMMAND ----------

objects = list_files()
bf = spark.table(f"{CAT}.bronze.statement_files").select("object_key", "etag", "file_sha256", "parser_version", "reconciled").collect()
completed_sha = {r.file_sha256 for r in spark.table(f"{CAT}.silver.transaction_sources").select("file_sha256").distinct().collect()}
known = {(r.object_key, r.etag) for r in bf if r.reconciled and r.file_sha256 in completed_sha}
known_sha = {r.file_sha256: (r.parser_version, bool(r.reconciled and r.file_sha256 in completed_sha)) for r in bf}
candidates = [o for o in objects if (o["object_key"], o["etag"]) not in known]
print(f"{len(objects)} PDFs listed in {SOURCE}, {len(candidates)} with an unknown key or etag")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Hash each candidate; parse and reconcile only unseen content

# COMMAND ----------

files, raw, stmts, txns, quarantine, new = [], [], [], [], [], []
with tempfile.TemporaryDirectory() as tmp:
    for o in candidates:
        name = o["object_key"].split("/")[-1]
        path = Path(tmp) / name
        sha = fetch(o, path)
        row = {"object_key": o["object_key"], "etag": o["etag"], "size_bytes": o["size_bytes"], "file_sha256": sha, "ingested_at": now,
               "source": SOURCE, "statement_kind": o["kind"]}
        if sha in known_sha and known_sha[sha][1]:  # same content already loaded: only record the new location and etag
            path.unlink()
            files.append({**row, "parser_version": known_sha[sha][0], "reconciled": known_sha[sha][1]})
            continue
        st = extract.parse(path, PASSWORD)
        path.unlink()  # the PDF is never kept
        errors = list(st.errors)
        if st.account_type != MODE or st.currency != lakehouse.mode_currency(MODE):
            errors.append("statement account type/currency does not match configured lakehouse mode")
        df = None
        if st.txns and not errors:
            df = dedupe.prepare(pd.DataFrame(st.txns), name, sha, o["kind"])
            if dedupe.collisions(df):
                errors.append(f"key collision: {dedupe.collisions(df)} repeated line key(s) inside this file")
        ok = not errors
        new.append(o)
        known_sha[sha] = (extract.PARSER_VERSION, ok)
        files.append({**row, "parser_version": extract.PARSER_VERSION, "reconciled": ok})
        for r in st.txns:
            raw.append({"file_sha256": sha, "source_file": name, "seq": r["seq"], "txn_date": r["txn_date"], "narration": r["narration"],
                        "ref_no": r["ref_no"], "value_date": r["value_date"], "amount": float(r["amount"]), "balance": float(r["balance"]) if r["balance"] is not None else None,
                        "direction": r["direction"],
                        **{key: r.get(key, default) for key, default in lakehouse.METADATA_DEFAULTS.items()}})
        if not ok:
            quarantine.append({"file_sha256": sha, "file": name, "errors": "; ".join(errors), "quarantined_at": now})
            print("QUARANTINED", name, "|", "; ".join(errors)[:200])
            continue
        stmts.append({**lakehouse.statement_metadata(st), "file_sha256": sha, "file": name, "statement_kind": o["kind"],
                      "period_from": pd.to_datetime(st.period_from, format="%d/%m/%Y").date(),
                      "period_to": pd.to_datetime(st.period_to, format="%d/%m/%Y").date(), "opening": float(st.opening),
                      "closing": float(st.closing), "debits": float(st.debits), "credits": float(st.credits), "txn_count": len(st.txns),
                      "parser_version": extract.PARSER_VERSION, "parsed_at": now})
        txns.append(df)
print(f"{len(files)} files registered: {len(new)} parsed ({len(stmts)} reconciled, {len(quarantine)} quarantined), {len(files) - len(new)} already loaded by content")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Tag duplicate line items

# COMMAND ----------

stored = spark.table(f"{CAT}.silver.transaction_sources").where("NOT is_duplicate").select("txn_id", "source_file").collect()
first_file = {r.txn_id: r.source_file for r in stored}
row_kind = {r.txn_id: r.statement_kind for r in spark.table(f"{CAT}.silver.transactions").select("txn_id", "statement_kind").collect()}
appearances, rows = dedupe.resolve(txns, first_file, row_kind, now)
n_dup = int(appearances.is_duplicate.sum()) if len(appearances) else 0
if len(appearances):
    per_file = appearances.groupby("source_file").agg(lines=("txn_id", "size"), duplicates=("is_duplicate", "sum"))
    per_file["new"] = per_file["lines"] - per_file["duplicates"]
    print(per_file[["lines", "new", "duplicates"]].to_string())
print(f"line items: {len(appearances)} found, {len(appearances) - n_dup} new, {n_dup} duplicates")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write: bronze append, silver merge

# COMMAND ----------

append(raw, "bronze.raw_lines")
append(files, "bronze.statement_files")
append(quarantine, "silver.quarantine")
merge(pd.DataFrame(stmts) if stmts else None, "silver.statements", "file_sha256",
      ["file_sha256", "file", "statement_kind", "period_from", "period_to", "opening", "closing", "debits", "credits", "txn_count", "parser_version", "parsed_at"] + lakehouse.STATEMENT_METADATA)
write_resolved(appearances, rows)
if stmts:
    spark.sql(f"DELETE FROM {CAT}.silver.quarantine WHERE file_sha256 IN (SELECT file_sha256 FROM {CAT}.silver.statements)")

# COMMAND ----------

try:
    dbutils.widgets.text("force", "false")
    force = dbutils.widgets.get("force").lower() == "true"
except Exception:
    force = False
run_gold = bool(new) or force
dbutils.jobs.taskValues.set(key="run_gold", value="true" if run_gold else "false")
print(f"new files: {len(new)}, force: {force} -> gold {'will run' if run_gold else 'skipped'}")

summary = {t: spark.table(f"{CAT}.{t}").count() for t in ["bronze.statement_files", "silver.statements", "silver.transactions", "silver.transaction_sources", "silver.quarantine"]}
print("INGEST DONE", summary)
dbutils.notebook.exit(str({"new_files": len(new), "duplicates": n_dup, **summary}))
