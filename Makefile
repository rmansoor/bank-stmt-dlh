.PHONY: help setup extract gold report test cloud-check dashboard-build notebook db-init minio-init upload deploy secrets run weekly-report etl dashboard publish clean

help:
	@echo "Targets:"
	@echo "  cloud-check - check required cloud configuration locally (no network)"
	@echo "  dashboard-build - generate the dashboard JSON locally"
	@echo "  setup     - install dependencies (uv sync)"
	@echo "  extract   - parse statement PDFs into silver parquet, reconcile each one"
	@echo "  gold      - categorize, detect recurring, build data/bank.duckdb (uses Jev if TYPESAFE_API_KEY is set)"
	@echo "  report    - print the standard reports from DuckDB"
	@echo "  weekly-report - run the weekly cash report job now (WEEK_END=2026-10-18 FORCE=1 MODE=dry)"
	@echo "  test      - run pytest"
	@echo "  db-init   - create Databricks schemas and landing volume (databricks/sql/00_init.sql)"
	@echo "  minio-init - create the MinIO bucket (scripts/bootstrap_minio.py)"
	@echo "  upload    - copy statement PDFs to the S3-compatible bucket (WEEKLY=1 for weekly statements)"
	@echo "  deploy    - upload code, config, SQL and notebooks to the Databricks workspace"
	@echo "  etl       - create/update the scheduled job and run it now"
	@echo "  dashboard - generate and publish the finance dashboard"
	@echo "  secrets   - copy .env secrets to the Databricks secret scope"
	@echo "  publish   - export the public copy of the source to PUBLIC_REPO_DIR (maintainers; PUSH=1 to push)"
	@echo "  notebook  - open Jupyter"
	@echo "  clean     - remove the virtualenv and generated data"

cloud-check:
	.venv/bin/python scripts/cloud_preflight.py

dashboard-build:
	.venv/bin/python databricks/dashboards/build_dashboard.py

setup:
	uv sync

extract:
	cd src && ../.venv/bin/python pipeline.py

gold:
	cd src && ../.venv/bin/python reports.py

report:
	cd src && ../.venv/bin/python reports.py --show

test:
	.venv/bin/python -m pytest -q

db-init:
	.venv/bin/python databricks/run_sql.py databricks/sql/00_init.sql

minio-init:
	.venv/bin/python scripts/bootstrap_minio.py

upload:
	.venv/bin/python scripts/upload_statements.py $(if $(WEEKLY),--weekly)

deploy:
	.venv/bin/python scripts/deploy_databricks.py

secrets:
	.venv/bin/python scripts/dbx_secrets.py

run:
	.venv/bin/python scripts/dbx_job.py --run

weekly-report:
	.venv/bin/python scripts/dbx_job.py --report $(if $(WEEK_END),--week-end $(WEEK_END)) $(if $(FORCE),--force) $(if $(MODE),--mode $(MODE))

etl:
	.venv/bin/python scripts/dbx_job.py --run

publish:
	.venv/bin/python scripts/publish_public.py $(if $(PUSH),--push)

dashboard:
	.venv/bin/python databricks/dashboards/build_dashboard.py
	.venv/bin/python databricks/dashboards/deploy_dashboard.py

notebook:
	uv run jupyter lab notebooks

clean:
	rm -rf .venv data
