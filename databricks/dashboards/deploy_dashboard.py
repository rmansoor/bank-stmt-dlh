"""Create or update the ConceptLabs Finance AI/BI dashboard and publish it.

    python databricks/dashboards/deploy_dashboard.py

Finds the dashboard by display name; creates it if missing, otherwise updates it. Prints links only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import settings  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")
    host = os.environ["DATABRICKS_HOST"].strip().rstrip("/")
    host = host if host.startswith("http") else "https://" + host
    h = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}"}
    USER = os.environ["DATABRICKS_USER"]
    S = settings.load()
    NAME = f"{S['company_name']} " + ("Credit Cards" if S["lakehouse_account_type"] == "credit-card" else "Finance")
    catalog = os.environ.get("DATABRICKS_CATALOG") or S["catalog"]
    wh = os.environ["DATABRICKS_HTTP_PATH"].rstrip("/").split("/")[-1]
    # Build from the selected mode at publish time, avoiding a stale generated bank dashboard.
    from build_dashboard import build
    body = json.dumps(build(S["lakehouse_account_type"])).replace("__CATALOG__", catalog).replace("__COMPANY__", S["company_name"])
    json.loads(body)
    api = lambda m, p, **kw: httpx.request(m, f"{host}{p}", headers=h, timeout=90, **kw)
    existing = None
    page = api("GET", "/api/2.0/lakeview/dashboards", params={"page_size": 100}).json()
    for d in page.get("dashboards", []):
        if d.get("display_name") == NAME:
            existing = d["dashboard_id"]
    payload = {"display_name": NAME, "serialized_dashboard": body, "warehouse_id": wh}
    if existing:
        r = api("PATCH", f"/api/2.0/lakeview/dashboards/{existing}", json=payload)
        did, action = existing, "updated"
    else:
        r = api("POST", "/api/2.0/lakeview/dashboards", json={**payload, "parent_path": f"/Workspace/Users/{USER}"})
        did, action = r.json().get("dashboard_id"), "created"
    if r.status_code != 200:
        print("FAILED", r.status_code, r.text[:500])
        return 1
    p = api("POST", f"/api/2.0/lakeview/dashboards/{did}/published", json={"embed_credentials": True, "warehouse_id": wh})
    print(action, "| publish:", p.status_code, p.text[:200] if p.status_code != 200 else "")
    print(f"draft:     {host}/dashboardsv3/{did}")
    print(f"published: {host}/dashboardsv3/{did}/published")
    return 0 if p.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
