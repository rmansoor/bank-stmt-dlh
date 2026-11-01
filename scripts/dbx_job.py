"""Create or update the scheduled ETL job (by name) and optionally run it now.

    python scripts/dbx_job.py            # create or update both jobs (ETL, weekly cash report)
    python scripts/dbx_job.py --run      # also start an ETL run now and print its id
    python scripts/dbx_job.py --report [--week-end 2026-10-18] [--force] [--mode dry|live]   # run the weekly cash report now

Tasks (serverless): ingest (02) -> has_new_data -> seed (01) -> gold (03).
Once a day at 00:00 (Asia/Kolkata) only `ingest` does real work: seed and gold run when ingest found new files,
or when the job is started with force=true (`--run`, used after a config or code change). The job id is written to .env as DATABRICKS_JOB_ID.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "src"))
import settings as project_settings
from lakehouse import require_bank_report

NAME = "bank-stmt-dlh"
REPORT_NAME = "bank-stmt-dlh-weekly-cash-report"
USER = os.environ["DATABRICKS_USER"]
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "").strip()
WS = f"/Users/{USER}/bank-stmt-dlh/notebooks"


def spec() -> dict:
    nb = lambda key, nbk, dep=None: {"task_key": key, "notebook_task": {"notebook_path": f"{WS}/{nbk}"},
                                     **({"depends_on": [dep]} if dep else {})}
    return {
        "name": NAME,
        "max_concurrent_runs": 1,
        "parameters": [{"name": "force", "default": "false"}],
        "schedule": {"quartz_cron_expression": "0 0 0 * * ?", "timezone_id": "Asia/Kolkata", "pause_status": "UNPAUSED"},
        "email_notifications": {"on_failure": [ALERT_EMAIL]} if ALERT_EMAIL else {},
        "tasks": [
            nb("ingest", "02_ingest_bronze_silver"),
            {"task_key": "has_new_data", "depends_on": [{"task_key": "ingest"}],
             "condition_task": {"op": "EQUAL_TO", "left": "{{tasks.ingest.values.run_gold}}", "right": "true"}},
            nb("seed", "01_reference_seed", {"task_key": "has_new_data", "outcome": "true"}),
            nb("gold", "03_gold_build", {"task_key": "seed"}),
        ],
    }


def report_spec() -> dict:
    """Same ingest and gold chain, then the report. Every Monday 00:00 America/New_York (the end of Sunday). Parameters: week_end,
    report_force, mode (blank = the Sunday just ended, false, secret mail_mode)."""
    base = spec()
    nb = lambda key, nbk, deps, **kw: {"task_key": key, "notebook_task": {"notebook_path": f"{WS}/{nbk}"}, "depends_on": deps, **kw}
    return {**base, "name": REPORT_NAME,
            "parameters": [{"name": "force", "default": "false"}, {"name": "week_end", "default": ""}, {"name": "report_force", "default": "false"}, {"name": "mode", "default": ""}],
            "schedule": {"quartz_cron_expression": "0 0 0 ? * MON", "timezone_id": "America/New_York", "pause_status": "UNPAUSED" if project_settings.load()["lakehouse_account_type"] == "bank" else "PAUSED"},
            "tasks": base["tasks"] + [nb("report", "04_weekly_cash_report", [{"task_key": "gold"}, {"task_key": "ingest"}], run_if="NONE_FAILED")]}


def upsert(api, name: str, body: dict) -> int:
    listing = api("GET", "/api/2.1/jobs/list", params={"name": name})
    listing.raise_for_status()
    jobs = listing.json().get("jobs", [])
    if jobs:
        jid = jobs[0]["job_id"]
        r = api("POST", "/api/2.1/jobs/reset", json={"job_id": jid, "new_settings": body})
        r.raise_for_status()
        print("updated job", name, jid)
    else:
        r = api("POST", "/api/2.1/jobs/create", json=body)
        if r.status_code != 200:
            raise SystemExit(f"create {name} failed {r.status_code} {r.text[:400]}")
        jid = r.json()["job_id"]
        print("created job", name, jid)
    return jid


def main() -> int:
    load_dotenv(ROOT / ".env")
    if "--report" in sys.argv[1:]:
        require_bank_report(project_settings.load()["lakehouse_account_type"])
    host = os.environ["DATABRICKS_HOST"].strip().rstrip("/")
    host = host if host.startswith("http") else "https://" + host
    h = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}"}
    api = lambda m, p, **kw: httpx.request(m, f"{host}{p}", headers=h, timeout=60, **kw)
    jid, rid = upsert(api, NAME, spec()), upsert(api, REPORT_NAME, report_spec())
    env = (ROOT / ".env").read_text()
    for key, val in (("DATABRICKS_JOB_ID", jid), ("DATABRICKS_REPORT_JOB_ID", rid)):
        env = re.sub(rf"^{key}=.*\n?", "", env, flags=re.M).rstrip("\n") + f"\n{key}={val}\n"
    (ROOT / ".env").write_text(env)
    argv = sys.argv[1:]
    if "--run" in argv:
        response = api("POST", "/api/2.1/jobs/run-now", json={"job_id": jid, "job_parameters": {"force": "true"}})
        response.raise_for_status()
        print("run", response.json())
    if "--report" in argv:
        arg = lambda flag: argv[argv.index(flag) + 1] if flag in argv else ""
        params = {"week_end": arg("--week-end"), "report_force": "true" if "--force" in argv else "false", "mode": arg("--mode")}
        response = api("POST", "/api/2.1/jobs/run-now", json={"job_id": rid, "job_parameters": params})
        response.raise_for_status()
        print("report run", response.json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
