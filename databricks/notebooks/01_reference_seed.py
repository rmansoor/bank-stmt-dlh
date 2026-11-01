# Databricks notebook source
# MAGIC %md
# MAGIC # 01 Reference seed (bolt B2)
# MAGIC
# MAGIC **Why.** Categories, payee map, rules and declared recurring payments live as YAML in git (`config/`).
# MAGIC This notebook copies them into `bank_stmt_dlh.gold` tables so the dashboard and SQL users can see what each label is based on.
# MAGIC The categorizer itself reads the same YAML files, so git stays the single source of truth. Do not edit these tables by hand: change the YAML and deploy again.
# MAGIC
# MAGIC **Writes.** `gold.dim_category`, `gold.payee_map`, `gold.category_rules`, `gold.recurring_declared`, `gold.settings` (full refresh each run).

# COMMAND ----------

import os, sys
from pathlib import Path
BASE = "/Workspace" + str(Path(dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()).parents[1])  # the deployed repo root
sys.path.insert(0, f"{BASE}/src")
import settings as _settings
S = _settings.load()
CAT, SCOPE = S["catalog"], S["secret_scope"]

import pandas as pd
import yaml
import lakehouse

cfg = lambda name: yaml.safe_load(open(f"{BASE}/config/{name}"))

# COMMAND ----------

categories = cfg("categories.yaml")["categories"]
dim = pd.DataFrame([{"category": k, "category_type": v["type"], "description": v["description"]} for k, v in categories.items()])

mm = cfg("merchant_map.yaml")
rows = []
for cat, entries in (mm.get("merchants") or {}).items():
    for e in entries or []:
        e = e if isinstance(e, dict) else {"name": e}
        rows.append({"category": cat, "name": str(e["name"]), "direction": e.get("direction"),
                     "valid_from": str(e["from"]) if e.get("from") else None, "valid_until": str(e["until"]) if e.get("until") else None})
payee = pd.DataFrame(rows, columns=["category", "name", "direction", "valid_from", "valid_until"])

rules = pd.DataFrame([{"seq": i, "pattern": r["match"], "category": r["category"], "direction": r.get("direction")}
                      for i, r in enumerate(cfg("rules.yaml")["rules"])])

rd = cfg("recurring_declared.yaml")
declared = pd.DataFrame([{"name": d["name"], "pattern": d["match"], "cadence": d["cadence"], "kind": "declared"} for d in rd.get("declared", [])]
                        + [{"name": e.get("note", e["match"]), "pattern": e["match"], "cadence": None, "kind": "ended"} for e in rd.get("ended", [])])

settings = pd.DataFrame([{"key": k, "value": str(v)} for k, v in cfg("settings.yaml").items()])

# COMMAND ----------

SCHEMAS = {
    "dim_category": "category STRING, category_type STRING, description STRING",
    "payee_map": "category STRING, name STRING, direction STRING, valid_from STRING, valid_until STRING",
    "category_rules": "seq INT, pattern STRING, category STRING, direction STRING",
    "recurring_declared": "name STRING, pattern STRING, cadence STRING, kind STRING",
    "settings": "key STRING, value STRING",
}
for name, pdf in {"dim_category": dim, "payee_map": payee, "category_rules": rules, "recurring_declared": declared, "settings": settings}.items():
    names = [part.strip().split()[0] for part in SCHEMAS[name].split(",")]
    sdf = lakehouse.typed_frame(spark, pdf.reindex(columns=names), SCHEMAS[name])
    sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CAT}.gold.{name}")
    print(f"{name}: {len(pdf)} rows")
