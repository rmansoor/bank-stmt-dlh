"""Silver enrichment: channel, clean counterparty, stable txn_id. No AI here."""
from __future__ import annotations

import hashlib
import re

import pandas as pd

CHANNELS = [
    (re.compile(r"^UPI-AUTOPAY", re.I), "upi-autopay"),
    (re.compile(r"^UPI", re.I), "upi"),
    (re.compile(r"^ME DC SI|^POS|^DC ", re.I), "card"),
    (re.compile(r"^IMPS", re.I), "imps"),
    (re.compile(r"^NEFT", re.I), "neft"),
    (re.compile(r"^RTGS", re.I), "rtgs"),
    (re.compile(r"^(CHQ|CHEQUE)", re.I), "cheque"),
    (re.compile(r"INT\.?\s?PD|INTEREST", re.I), "bank-interest"),
]


def channel_of(narr: str) -> str:
    for rx, name in CHANNELS:
        if rx.search(narr):
            return name
    return "other"


def counterparty_of(narr: str) -> str:
    """Best-effort merchant or person string, uppercase, free of ids and handles."""
    n = narr.strip()
    n = re.sub(r"^UPI-AUTOPAY-", "", n, flags=re.I)
    n = re.sub(r"^UPI-", "", n, flags=re.I)
    n = re.sub(r"^ME DC SI \d{6}X+\d{4}\s*", "", n, flags=re.I)
    n = re.sub(r"^(IMPS|NEFT|RTGS)-\w+-", "", n, flags=re.I)
    n = re.split(r"-[\w.\-]*@", n)[0]  # drop from the UPI handle onwards
    n = re.sub(r"^(CRV )?POS \w+ \w+ ", "", n, flags=re.I)
    n = re.split(r"\s(RZP|PAYMENT|PAY VIA|PAY TO|PAYTM|GPAY|OKBIZ|BHARATPE|UPI)\b", n, flags=re.I)[0]
    n = re.sub(r"\d{6,}", " ", n)
    n = re.sub(r"[^A-Za-z& ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip().upper()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["channel"] = df["narration"].map(channel_of)
    df["counterparty_clean"] = df["narration"].map(counterparty_of)
    df["signed_amount"] = df["amount"].where(df["direction"] == "credit", -df["amount"])
    # Identity of a bank line item: date, reference, amount and the bank's running balance. Narration is left out because
    # weekly and monthly statements print it differently for some lines; the balance keeps look-alike lines apart.
    df["txn_id"] = [
        hashlib.sha1(f"{d:%Y%m%d}|{r}|{a:.2f}|{b:.2f}".encode()).hexdigest()[:16]
        for d, r, a, b in zip(df["txn_date"], df["ref_no"], df["amount"], df["balance"])
    ]
    card = df.get("account_type", pd.Series("bank", index=df.index)) == "credit-card"
    for i, row in df[card].iterrows():
        identity = "|".join(str(row[k]) for k in ("account_id", "currency", "txn_date", "value_date", "ref_no", "amount", "direction", "activity_type", "identity_occurrence"))
        df.loc[i, "txn_id"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
        df.loc[i, "channel"] = "card"
    df["year"] = df["txn_date"].dt.year
    df["month"] = df["txn_date"].dt.month
    return df
