"""Parse supported HDFC bank and Wells Fargo credit card statement PDFs.

Deterministic. Direction comes from the running balance, and every statement is
reconciled against the bank's own STATEMENT SUMMARY before it is accepted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pdfplumber

PARSER_VERSION = "1.3"

TXN_START = re.compile(
    r"^(\d{2}/\d{2}/\d{2})\s+(.*?)\s*(\d{10,20})\s+(\d{2}/\d{2}/\d{2})\s+"
    r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$"
)
SUMMARY = re.compile(
    r"^([\d,]+\.\d{2})\s+(\d+)\s+(\d+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$"
)
PERIOD = re.compile(r"From\s*:\s*(\d{2}/\d{2}/\d{4})\s+To\s*:\s*(\d{2}/\d{2}/\d{4})")
NOISE = ("Page No", "Date Narration", "Statement of account", "Account Branch")


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


@dataclass
class Statement:
    file: str
    bank: str = "hdfc"
    account_type: str = "bank"
    currency: str = "INR"
    account_id: str = ""
    period_from: str = ""
    period_to: str = ""
    opening: Decimal = Decimal(0)
    dr_count: int = 0
    cr_count: int = 0
    debits: Decimal = Decimal(0)
    credits: Decimal = Decimal(0)
    closing: Decimal = Decimal(0)
    txns: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _pages(path: Path, password: str) -> list[list[str]]:
    with pdfplumber.open(path, password=password) as pdf:
        return [
            (page.extract_text(x_tolerance=1.5, y_tolerance=3) or "").splitlines()
            for page in pdf.pages
        ]


# Footer lines that follow the last row of a page. Compared with spaces removed.
FOOTER = ("GeneratedOn", "Thisisacomputer", "HDFCBANKLIMITED", "*Closingbalance")


def parse(path: Path, password: str) -> Statement:
    st = Statement(file=path.name)
    pages = _pages(path, password)
    text = "\n".join("\n".join(p) for p in pages)
    if "WELLS FARGO" in text.upper():
        from extract_wells_fargo import parse_lines
        return parse_lines(pages, path.name)
    rows: list[dict] = []
    for page_lines in pages:
        in_table = False  # page headers and footers are never part of a narration
        for raw in page_lines:
            line = raw.strip()
            if not line:
                continue
            m = PERIOD.search(line)
            if m:
                st.period_from, st.period_to = m.groups()
            if line.upper().startswith("STATEMENT SUMMARY"):
                in_table = False
                continue
            s = SUMMARY.match(line)
            if s:
                st.opening, st.dr_count, st.cr_count = _money(s[1]), int(s[2]), int(s[3])
                st.debits, st.credits, st.closing = _money(s[4]), _money(s[5]), _money(s[6])
                continue
            t = TXN_START.match(line)
            if t:
                in_table = True
                rows.append(
                    {
                        "txn_date": t[1],
                        "narration": t[2].strip(),
                        "ref_no": t[3],
                        "value_date": t[4],
                        "amount": _money(t[5]),
                        "balance": _money(t[6]),
                    }
                )
                continue
            if line.replace(" ", "").startswith(FOOTER):
                in_table = False
                continue
            if in_table and rows and not line.startswith(NOISE):
                rows[-1]["narration"] = (rows[-1]["narration"] + " " + line).strip()

    prev = st.opening
    for seq, r in enumerate(rows):
        r["seq"] = seq  # order inside the statement; the bank gives no other tie-break on one day
        delta = r["balance"] - prev
        if delta == r["amount"]:
            r["direction"] = "credit"
        elif delta == -r["amount"]:
            r["direction"] = "debit"
        else:
            r["direction"] = "unknown"
            st.errors.append(f"balance break at {r['txn_date']} ref {r['ref_no']}")
        prev = r["balance"]
    st.txns = rows
    if not rows or not st.period_from or "STATEMENT SUMMARY" not in text.upper():
        st.errors.append("unsupported or empty statement: expected HDFC current account or Wells Fargo credit card")
    _reconcile(st)
    return st


def _reconcile(st: Statement) -> None:
    dr = [r for r in st.txns if r["direction"] == "debit"]
    cr = [r for r in st.txns if r["direction"] == "credit"]
    checks = [
        (len(dr), st.dr_count, "debit count"),
        (len(cr), st.cr_count, "credit count"),
        (sum((r["amount"] for r in dr), Decimal(0)), st.debits, "debit total"),
        (sum((r["amount"] for r in cr), Decimal(0)), st.credits, "credit total"),
        (st.txns[-1]["balance"] if st.txns else st.opening, st.closing, "closing balance"),
    ]
    for got, want, name in checks:
        if got != want:
            st.errors.append(f"{name}: parsed {got} vs statement {want}")


def find_pdfs(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.pdf"))
