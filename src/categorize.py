"""Gold categorization. Order: manual overrides, merchant map, rules, Jev, review.

Every row records category_source and category_confidence so results stay auditable.
Jev sees only the cleaned counterparty, channel and direction, never amounts or
account numbers.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
UNCLASSIFIED = "unclassified"
HUMAN = "human-intervention-required"


def load_settings() -> dict:
    f = CONFIG / "settings.yaml"
    return yaml.safe_load(f.read_text()) if f.exists() else {}


def load_categories() -> dict:
    return yaml.safe_load((CONFIG / "categories.yaml").read_text())["categories"]


def load_rules() -> list[dict]:
    return yaml.safe_load((CONFIG / "rules.yaml").read_text())["rules"]


def _merchant_cfg() -> dict:
    f = CONFIG / "merchant_map.yaml"
    return yaml.safe_load(f.read_text()) if f.exists() else {}


def load_merchant_map() -> list[dict]:
    """Known payees as dicts (rx, category, direction, from, until), longest name first."""
    data = _merchant_cfg().get("merchants") or {}
    items = []
    for cat, entries in data.items():
        for e in entries or []:
            e = e if isinstance(e, dict) else {"name": e}
            items.append((str(e["name"]), cat, e))
    items.sort(key=lambda i: -len(i[0]))
    out = []
    for name, cat, e in items:
        pat = name[3:] if name.startswith("re:") else rf"(?<![A-Z]){re.escape(name.upper())}(?![A-Z])"
        out.append({
            "rx": re.compile(pat, re.I), "category": cat, "direction": e.get("direction"),
            "from": pd.Timestamp(e["from"]) if e.get("from") else None,
            "until": pd.Timestamp(e["until"]) if e.get("until") else None,
        })
    return out


def map_category(counterparty: str, mmap: list[dict], direction: str | None = None, when=None) -> str | None:
    for m in mmap:
        if m["direction"] and direction and m["direction"] != direction:
            continue
        if when is not None and ((m["from"] is not None and when < m["from"]) or (m["until"] is not None and when > m["until"])):
            continue
        if m["rx"].search(counterparty):
            return m["category"]
    return None


def apply_founder(df: pd.DataFrame) -> pd.DataFrame:
    """Debits to the founder are operations. Credits from him that match an earlier
    debit of the same amount are loan repayments, the rest are founder funding."""
    cfg = _merchant_cfg().get("founder")
    if not cfg:
        return df
    names = [re.compile(rf"(?<![A-Z]){re.escape(n.upper())}(?![A-Z])") for n in cfg["names"]]
    is_f = df["counterparty_clean"].map(lambda c: any(rx.search(c) for rx in names))
    df = df.copy()
    is_f &= df.get("account_type", pd.Series("bank", index=df.index)) != "credit-card"
    df["is_founder"] = is_f
    df.loc[is_f & (df["direction"] == "debit"), ["category", "category_source", "category_confidence"]] = [cfg["debit_category"], "founder", 1.0]
    open_debits = df[is_f & (df["direction"] == "debit")].sort_values("txn_date")
    used: set = set()
    for i, row in df[is_f & (df["direction"] == "credit")].sort_values("txn_date").iterrows():
        match = open_debits[(open_debits["amount"] == row["amount"]) & (open_debits["txn_date"] <= row["txn_date"]) & (~open_debits.index.isin(used))]
        if len(match):
            used.add(match.index[0])
            cat = cfg["credit_matched_category"]
        else:
            cat = cfg["credit_unmatched_category"]
        df.loc[i, ["category", "category_source", "category_confidence"]] = [cat, "founder", 1.0]
    return df


def rule_category(row: pd.Series, rules: list[dict]) -> str | None:
    text = f"{row['counterparty_clean']} | {row['narration']}"
    for r in rules:
        if r.get("direction") and r["direction"] != row["direction"]:
            continue
        if re.search(r["match"], text, flags=re.I):
            return r["category"]
    return None


def jev_category(state: str, categories: dict) -> tuple[str, float] | None:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return None
    body = {
        "model": "jev-latest",
        "state": state,
        "questions": {
            "category": {
                "type": "choice",
                "instructions": "Which category best fits this bank transaction of a small software company",
                "criteria": {k: v["description"] for k, v in categories.items()},
            }
        },
    }
    resp = httpx.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=30)
    resp.raise_for_status()
    ans = resp.json()["answers"]["category"]
    return ans["choice"], float(ans["confidence"])


def categorize(df: pd.DataFrame, use_jev: bool = True, jev_cache: dict | None = None) -> pd.DataFrame:
    """jev_cache maps request text to (category, confidence). Pass a loaded cache to reuse earlier
    answers; new answers are added to it so the caller can store them."""
    cats, rules, mmap = load_categories(), load_rules(), load_merchant_map()
    cfg = load_settings()
    jev_min = float(cfg.get("jev_confidence_threshold", 0.9))
    review_min = float(cfg.get("review_amount_threshold", 5000))
    out = df.copy()
    cat, src, conf, why = [], [], [], []
    cache = jev_cache if jev_cache is not None else {}
    for _, row in out.iterrows():
        cp = row["counterparty_clean"]
        card = row.get("account_type") == "credit-card"
        card_categories = {"payment": "card-payment", "refund": "card-refund", "fee": "bank-charges", "interest": "card-interest", "cash-advance": "card-cash-advance", "balance-transfer": "card-balance-transfer"}
        if card and row.get("activity_type") in card_categories:
            cat.append(card_categories[row["activity_type"]]); src.append("card-section"); conf.append(1.0); why.append(""); continue
        mc = map_category(cp, mmap, row["direction"], row["txn_date"])
        if mc:
            cat.append(mc); src.append("map"); conf.append(1.0); why.append(""); continue
        rc = rule_category(row, rules)
        if rc:
            cat.append(rc); src.append("rule"); conf.append(1.0); why.append(""); continue
        jev_unsure = False
        if use_jev and not card:
            state = f"Counterparty: {cp}. Channel: {row['channel']}. Direction: {row['direction']}."
            if state not in cache:
                cache[state] = jev_category(state, cats)
            hit = cache[state]
            if hit and hit[1] >= jev_min:
                cat.append(hit[0]); src.append("jev"); conf.append(hit[1]); why.append(""); continue
            jev_unsure = hit is not None
        reasons = []
        if row["amount"] >= review_min:
            reasons.append(f"amount >= {review_min:g}")
        if row["direction"] == "credit":
            reasons.append("unlabelled credit")
        if reasons and jev_unsure:
            reasons.append("jev unsure")
        if reasons:
            cat.append(HUMAN); src.append("review"); conf.append(0.0); why.append("; ".join(reasons))
        else:
            cat.append(UNCLASSIFIED); src.append("unclassified"); conf.append(0.0); why.append("")
    out["category"], out["category_source"], out["category_confidence"] = cat, src, conf
    out["review_reason"] = why
    out = apply_founder(out)
    out["category_type"] = out["category"].map(lambda c: cats.get(c, {}).get("type", "unknown"))
    return out
