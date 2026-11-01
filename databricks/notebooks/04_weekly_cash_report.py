# Databricks notebook source
# MAGIC %md
# MAGIC # 04 Weekly cash report: build, snapshot, render, send, log (bolts M1 to M3)
# MAGIC
# MAGIC **Why.** Once a week (Monday 00:00 America/New_York, the end of Sunday) the founders get the week's cash position by email: a formatted body (INR by default, USD by setting)
# MAGIC and two PDFs, USD and INR. Every figure is SQL on the gold tables; nothing is written by a model. Spec: `openspec/changes/weekly-cash-report`.
# MAGIC
# MAGIC **Steps.** Pick the week, skip if it was already sent, get one USD/INR rate for the report, build the figures, **save them as an append-only snapshot**, render the email and PDFs **from the
# MAGIC snapshot**, send through Microsoft Graph, log the send.
# MAGIC
# MAGIC **Reads.** `gold.fact_transactions`, `gold.upcoming_payments`, `gold.review_summary`, `silver.statements`, the rate API, secret scope `bank-stmt-dlh` (`mail_*`, `sp_tenant_id`).
# MAGIC **Writes.** `gold.fx_rates`, `gold.weekly_cash_snapshot`, `gold.weekly_cash_snapshot_lines`, `gold.report_log`. **Sends** one email.
# MAGIC
# MAGIC **Parameters.** `week_end` (date, blank = the Sunday just ended), `report_force` (true sends again and adds a new snapshot version), `mode` (`dry` = owner only, `live` = the recipient list; blank = secret `mail_mode`).
# MAGIC
# MAGIC **Safe to rerun.** A week already sent in the same mode is skipped unless `report_force=true`.

# COMMAND ----------

# MAGIC %pip install -q httpx reportlab pyyaml

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from pathlib import Path
BASE = "/Workspace" + str(Path(dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()).parents[1])  # the deployed repo root
sys.path.insert(0, f"{BASE}/src")
import settings as _settings
S = _settings.load()
CAT, SCOPE = S["catalog"], S["secret_scope"]
import lakehouse
lakehouse.require_bank_report(S["lakehouse_account_type"])
# Also guard direct notebook runs against a catalog accidentally pointed at cards.
if spark.table(f"{CAT}.silver.transactions").where("account_type <> 'bank' OR currency <> 'INR'").limit(1).count():
    raise ValueError("Weekly cash email cannot read credit card or non-INR transactions")

import pandas as pd
import yaml
import graph_mail as gm, report_data as rd, weekly_report as wr

secret = lambda k: dbutils.secrets.get(SCOPE, k)
settings = S
BODY_CURRENCY = settings.get("report_body_currency", "INR").upper()
assert BODY_CURRENCY in ("INR", "USD"), f"unknown report_body_currency {BODY_CURRENCY!r}"

for name, default in (("week_end", ""), ("report_force", "false"), ("mode", "")):
    try:
        dbutils.widgets.text(name, default)
    except Exception:
        pass
w = lambda n: dbutils.widgets.get(n).strip()
mode = (w("mode") or secret("mail_mode")).lower()
assert mode in ("dry", "live"), f"mode must be dry or live, got {mode!r}"
force = w("report_force").lower() == "true"
week_end = date.fromisoformat(w("week_end")) if w("week_end") else rd.default_week_end(datetime.now(ZoneInfo("America/New_York")).date())
q = lambda sql: [r.asDict() for r in spark.sql(sql).collect()]
now = datetime.now(timezone.utc)
print(f"week ending {week_end} | mode {mode} | force {force} | body currency {BODY_CURRENCY}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Tables, and skip a week that was already sent

# COMMAND ----------

for ddl in rd.DDL:
    spark.sql(ddl)

sent = q(f"select count(*) n, coalesce(max(version), 0) v from {CAT}.gold.report_log where week_end = '{week_end}' and mode = '{mode}' and status = 'sent'")[0]
if sent["n"] and not force:
    msg = f"week {week_end} already sent in {mode} mode (version {sent['v']}); nothing to do (report_force=true sends again)"
    print(msg)
    dbutils.notebook.exit(msg)

# COMMAND ----------

# MAGIC %md
# MAGIC ## One exchange rate for the report (stored; fall back to the latest stored one)

# COMMAND ----------

try:
    rate, rate_date = rd.fetch_rate(week_end)
    if not q(f"select 1 from {CAT}.gold.fx_rates where rate_date = '{rate_date}' and base = 'USD' and quote = 'INR'"):
        spark.createDataFrame(pd.DataFrame([{"rate_date": rate_date, "base": "USD", "quote": "INR", "rate": rate, "source": "frankfurter", "fetched_at": now}]),
                              schema=rd.FX_SCHEMA).write.mode("append").saveAsTable(f"{CAT}.gold.fx_rates")
    print(f"rate {rate} for {rate_date} (source: Frankfurter / ECB)")
except Exception as e:  # noqa: BLE001 - fall back to the latest stored rate, never to a guess
    last = q(f"select rate, rate_date from {CAT}.gold.fx_rates where rate_date <= '{week_end}' order by rate_date desc limit 1")
    if not last:
        raise RuntimeError(f"no exchange rate available: {type(e).__name__}") from e
    rate, rate_date = float(last[0]["rate"]), last[0]["rate_date"]
    print(f"rate API unavailable ({type(e).__name__}); using the stored rate {rate} for {rate_date}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Build the figures and save the snapshot (append-only)

# COMMAND ----------

report = rd.build(q, week_end, rate, rate_date)
version = int(q(f"select coalesce(max(version), 0) v from {CAT}.gold.weekly_cash_snapshot where week_end = '{week_end}' and mode = '{mode}'")[0]["v"]) + 1
snap, lines = rd.to_snapshot(report, version, mode, now)
spark.createDataFrame(pd.DataFrame([snap]), schema=rd.SNAPSHOT_SCHEMA).write.mode("append").saveAsTable(f"{CAT}.gold.weekly_cash_snapshot")
spark.createDataFrame(pd.DataFrame(lines), schema=rd.LINES_SCHEMA).write.mode("append").saveAsTable(f"{CAT}.gold.weekly_cash_snapshot_lines")
print(f"snapshot {snap['report_id']}: {report.payments} payments, closing {report.closing:,.2f} INR, late statement: {report.late_statement}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Render from the snapshot, send, log

# COMMAND ----------

rid = snap["report_id"]
stored = q(f"select * from {CAT}.gold.weekly_cash_snapshot where report_id = '{rid}'")[0]
stored_lines = q(f"select * from {CAT}.gold.weekly_cash_snapshot_lines where report_id = '{rid}'")
r = rd.from_snapshot(stored, stored_lines)

recipients = gm.parse_recipients(secret("mail_dry_run_to") if mode == "dry" else secret("mail_recipients"))[: 1 if mode == "dry" else None]
logo = open(f"{BASE}/assets/logo-white.png", "rb").read()
attachments = [gm.attachment("logo-white.png", logo, "image/png", "logo-white"),
               gm.attachment(wr.pdf_name(r, "USD"), wr.render_pdf(r, "USD"), "application/pdf"),
               gm.attachment(wr.pdf_name(r, "INR"), wr.render_pdf(r, "INR"), "application/pdf")]
mailer = gm.GraphMail(secret("sp_tenant_id"), secret("mail_client_id"), secret("mail_client_secret"), secret("mail_sender"))
reply_to = secret("mail_reply_to").strip() or None
subject = ("[DRY RUN] " if mode == "dry" else "") + wr.subject(r)

status, error = "sent", None
try:
    mailer.send(recipients, subject, wr.render_html(r, currency=BODY_CURRENCY), attachments, reply_to=reply_to)
except Exception as e:  # noqa: BLE001 - log the failure (code only, no secrets or addresses), then fail the run
    status, error = "failed", str(e)[:200]
spark.createDataFrame(pd.DataFrame([{"report_id": rid, "week_end": week_end, "mode": mode, "version": version, "sent_at": datetime.now(timezone.utc),
                                     "recipient_count": len(recipients), "rate": rate, "rate_date": rate_date, "status": status, "error": error}]),
                      schema=rd.LOG_SCHEMA).write.mode("append").saveAsTable(f"{CAT}.gold.report_log")
if status != "sent":
    raise RuntimeError(f"report {rid} was not sent: {error}")
result = f"report {rid} sent to {len(recipients)} recipient(s); body {BODY_CURRENCY}; USD and INR PDFs attached; rate {rate} ({rate_date})"
print(result)
dbutils.notebook.exit(result)
