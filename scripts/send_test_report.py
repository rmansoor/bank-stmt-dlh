"""Build the weekly cash report for one week from the gold tables and send it to MAIL_DRY_RUN_TO only.

    python scripts/send_test_report.py [WEEK_END=2026-10-18]

A dry-run preview from the laptop: INR body, USD and INR PDFs attached, subject prefixed [TEST]. It reads gold through the
SQL warehouse and sends through Microsoft Graph as MAIL_SENDER (override with --as ADDRESS). The scheduled job (bolt M3) will do the same inside Databricks.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import graph_mail as gm  # noqa: E402
import weekly_report as wr  # noqa: E402
from dbx_query import query  # noqa: E402

import settings  # noqa: E402

CAT = settings.load()["catalog"]


def nice(slug: str) -> str:
    return (slug or "unclassified").replace("-", " ").capitalize()


def build(week_end: date) -> wr.Report:
    from lakehouse import require_bank_report
    require_bank_report(settings.load()["lakehouse_account_type"])
    start = week_end - timedelta(days=6)
    q = lambda sql: query(sql)
    row = lambda r, k: float(r[0][k]) if r and r[0][k] is not None else 0.0
    bal = lambda cond: q(f"select balance from {CAT}.gold.fact_transactions where {cond} order by txn_date desc, seq desc limit 1")
    opening = row(bal(f"txn_date < '{start}'"), "balance")
    closing = row(bal(f"txn_date <= '{week_end}'"), "balance")
    tot = q(f"select coalesce(sum(case when direction='credit' then amount end),0) i, coalesce(sum(case when direction='debit' then amount end),0) o, count(*) n "
            f"from {CAT}.gold.fact_transactions where txn_date between '{start}' and '{week_end}'")[0]
    cats = q(f"select category, count(*) n, sum(amount) a from {CAT}.gold.fact_transactions where direction='debit' and txn_date between '{start}' and '{week_end}' group by 1 order by 3 desc")
    tx = q(f"select txn_date, counterparty_clean, category, direction, amount from {CAT}.gold.fact_transactions where txn_date between '{start}' and '{week_end}' order by txn_date, seq")
    up = q(f"select payee, next_expected, typical_amount from {CAT}.gold.upcoming_payments where status='upcoming' and next_expected between '{week_end + timedelta(days=1)}' and '{week_end + timedelta(days=14)}' order by next_expected")
    rv = q(f"select coalesce(sum(txns),0) n, coalesce(sum(amount),0) a from {CAT}.gold.review_summary")[0]
    rate_day = httpx.get(f"https://api.frankfurter.dev/v1/{week_end}?base=USD&symbols=INR", timeout=20).json()
    return wr.Report(
        week_start=start, week_end=week_end, opening=opening, closing=closing, money_in=float(tot["i"]), money_out=float(tot["o"]), payments=int(tot["n"]),
        rate=float(rate_day["rates"]["INR"]), rate_date=date.fromisoformat(rate_day["date"]),
        categories=[{"label": nice(c["category"]), "payments": int(c["n"]), "amount": float(c["a"])} for c in cats],
        movements=[{"date": date.fromisoformat(t["txn_date"]), "payee": t["counterparty_clean"] or "", "category": t["category"] or "",
                    "amount": float(t["amount"]) * (1 if t["direction"] == "credit" else -1)} for t in tx],
        upcoming=[{"payee": u["payee"], "expected": date.fromisoformat(u["next_expected"]), "amount": float(u["typical_amount"])} for u in up],
        review_count=int(float(rv["n"])), review_amount=float(rv["a"]), late_statement=False, generated_at=datetime.now())


def main(argv: list[str]) -> int:
    e = dotenv_values(ROOT / ".env")
    sender = argv[argv.index("--as") + 1] if "--as" in argv else e["MAIL_SENDER"].strip()
    week_end = date.fromisoformat(next((a for a in argv if a[:2] == "20"), "2026-10-18"))
    r = build(week_end)
    to = gm.parse_recipients(e["MAIL_DRY_RUN_TO"])[:1]
    logo = (ROOT / "assets" / "logo-white.png").read_bytes()
    atts = [gm.attachment("logo-white.png", logo, "image/png", "logo-white"),
            gm.attachment(wr.pdf_name(r, "USD"), wr.render_pdf(r, "USD"), "application/pdf"),
            gm.attachment(wr.pdf_name(r, "INR"), wr.render_pdf(r, "INR"), "application/pdf")]
    mail = gm.GraphMail(e["SP_TENANT_ID"].strip(), e["MAIL_CLIENT_ID"].strip(), e["MAIL_CLIENT_SECRET"].strip(), sender)
    status = mail.send(to, "[TEST] " + wr.subject(r), wr.render_html(r), atts, reply_to=(e.get("MAIL_REPLY_TO") or "").strip() or None)
    print(f"week {r.week_start} to {r.week_end}: {r.payments} payments, rate {r.rate} ({r.rate_date}), sendMail HTTP {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
