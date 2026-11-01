# Databricks notebook source
# MAGIC %md
# MAGIC # 03 Gold build: categorize, recurring, reporting views (bolts B4 and B5)
# MAGIC
# MAGIC **Why.** Turns the reconciled silver ledger into what the dashboard reads. Order of decision for every transaction:
# MAGIC payee map, rules, founder logic, stored Jev label, new Jev call (only for what nothing earlier decided, confidence at least 0.9),
# MAGIC then `unclassified` / `human-intervention-required`. Recurring payments come from a deterministic gap and amount test plus the declared yearly list.
# MAGIC Reports and dashboard use plain SQL views only.
# MAGIC
# MAGIC **Reads.** `silver.transactions`, `gold.jev_labels`, YAML in `config/`. **Writes.** `gold.fact_transactions`, `gold.recurring_series`, `gold.review_queue`, `gold.jev_labels`, and the views in `databricks/sql/20_gold_views.sql`.
# MAGIC
# MAGIC **Jev.** Called only for leftovers, and each answer is stored in `gold.jev_labels` so the same request is never sent twice. It sees counterparty, channel and direction only: no amounts, no account numbers. With no key it is skipped.

# COMMAND ----------

# MAGIC %pip install -q pyyaml httpx

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
from datetime import datetime, timezone

from pathlib import Path
BASE = "/Workspace" + str(Path(dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()).parents[1])  # the deployed repo root
sys.path.insert(0, f"{BASE}/src")
import settings as _settings
S = _settings.load()
CAT, SCOPE = S["catalog"], S["secret_scope"]

try:
    os.environ["TYPESAFE_API_KEY"] = dbutils.secrets.get(SCOPE, "typesafe_api_key")
except Exception:
    print("no Jev key: unmatched rows stay unclassified or for review")

import pandas as pd
import categorize, recurring, lakehouse
MODE = S["lakehouse_account_type"]

# COMMAND ----------

tx = spark.table(f"{CAT}.silver.transactions").toPandas()
for c in ("txn_date", "value_date"):
    tx[c] = pd.to_datetime(tx[c])
print(len(tx), "silver transactions")

spark.sql(f"""CREATE TABLE IF NOT EXISTS {CAT}.gold.jev_labels (request STRING, category STRING, confidence DOUBLE, created_at TIMESTAMP)""")
cache = {r.request: (r.category, r.confidence) for r in spark.table(f"{CAT}.gold.jev_labels").collect()}
loaded = set(cache)

fact, rec, rq = lakehouse.gold_tables(tx, MODE, use_jev=bool(os.environ.get("TYPESAFE_API_KEY")), cache=cache)

new = [(k, v[0], float(v[1]), datetime.now(timezone.utc)) for k, v in cache.items() if k not in loaded and v]
if new:
    spark.createDataFrame(pd.DataFrame(new, columns=["request", "category", "confidence", "created_at"])).write.mode("append").saveAsTable(f"{CAT}.gold.jev_labels")
print(f"{len(new)} new Jev answers stored, {len(loaded)} reused")

# COMMAND ----------

def save(pdf, name, schema):
    lakehouse.typed_frame(spark, pdf, schema).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CAT}.gold.{name}")

f = fact
save(fact, "fact_transactions", lakehouse.FACT_SCHEMA)
save(rec, "recurring_series", lakehouse.RECURRING_SCHEMA)
save(rq, "review_queue", lakehouse.REVIEW_SCHEMA)
print(f"fact_transactions {len(f)}, recurring_series {len(rec)}, review_queue {len(rq)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Reporting views

# COMMAND ----------

sql_text = open(f"{BASE}/databricks/sql/20_gold_views.sql").read().replace("__CATALOG__", CAT)
lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
for stmt in [s.strip() for s in "\n".join(lines).split(";") if s.strip()]:
    spark.sql(stmt)
card_sql = open(f"{BASE}/databricks/sql/21_card_views.sql").read().replace("__CATALOG__", CAT)
lines = [ln for ln in card_sql.splitlines() if not ln.strip().startswith("--")]
for stmt in [s.strip() for s in "\n".join(lines).split(";") if s.strip()]:
    spark.sql(stmt)
print("views refreshed")

# COMMAND ----------

check = spark.sql(f"SELECT COUNT(*) AS txns, ROUND(SUM(CASE WHEN direction='debit' THEN amount END),2) AS debits, ROUND(SUM(CASE WHEN direction='credit' THEN amount END),2) AS credits FROM {CAT}.gold.fact_transactions").first()
print("GOLD DONE", check.asDict())
dbutils.notebook.exit(str(check.asDict()))
