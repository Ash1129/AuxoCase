"""Turn a lead (contact + company) into the model's input row.

Why these features and not others: the brief names channel, job title,
company size and page activity; the knowledge base adds that behavioural
signals (pricing views, forms, webinar attendance) and fit (size inside the
target band, no competitor lock-in) carry most of the lift in B2B scoring,
and that anything personal beyond the business role must stay out (L10,
H10). Names, emails and free-text notes are never features: notes go to the
separate, bounded LLM reading step.

Every feature is computable at scoring time from HubSpot fields alone, so a
score can be reproduced from the record (the `input_hash` in the scores
table is a hash of this row).
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Optional

import pandas as pd

from ..hubspot.models import CHANNELS, Company, Contact, SENIORITIES

CATEGORICAL = ["channel", "seniority", "size_band", "industry", "country_group"]
NUMERIC = [
    "log_page_views", "log_visits", "num_form_submissions", "pricing_page_views", "email_clicks_30d",
    "webinar_registered", "webinar_attended", "is_referral", "has_competitor", "in_target_size",
    "is_student_or_intern", "has_notes",
]
FEATURES = CATEGORICAL + NUMERIC

# Plain-English reasons (explain.py). Every entry is a whole clause with its own subject, so "Ranked up because"
# and "Held back because" can precede any of them, and a clause also reads as a row label in the score breakdown.
# Keyed by feature, or by "feature=value" for categoricals. A count feature has two clauses: one for when it
# pushes the score up (more than usual), one for when it holds it back (less than usual).
LABELS = {
    "channel=referral": "it came in as a partner or customer referral",
    "channel=webinar": "it came in through a webinar",
    "channel=website": "it came in through the website",
    "channel=paid_ads": "it came in through paid ads",
    "channel=email": "it came in through an email campaign",
    "channel=events": "it came in through an event",
    "seniority=c_level": "the contact is C-level",
    "seniority=vp": "the contact is VP-level",
    "seniority=director": "the contact is a director",
    "seniority=manager": "the contact is a manager",
    "seniority=individual": "the contact is an individual contributor",
    "seniority=unknown": "there is no job title on record",
    "size_band=under_50": "the company has under 50 employees, below the target band",
    "size_band=50_149": "the company has 50 to 149 employees, inside the target band",
    "size_band=150_299": "the company has 150 to 299 employees, inside the target band",
    "size_band=300_500": "the company has 300 to 500 employees, inside the target band",
    "size_band=over_500": "the company has over 500 employees, above the target band",
    "size_band=unknown": "the company size is unknown",
    "webinar_registered": "it registered for a webinar",
    "webinar_attended": "it attended a webinar",
    "is_referral": "it was referred by a partner or customer",
    "has_competitor": "the company already uses a competing payroll vendor",
    "in_target_size": "the company is in the 50 to 500 employee band",
    "is_student_or_intern": "the job title suggests a student or intern",
    "has_notes": "it left free-text answers or notes",
}
# (more than usual, less than usual) for the count features.
COUNT_LABELS = {
    "log_page_views": ("it viewed many pages on the site", "it viewed few pages on the site"),
    "log_visits": ("it came back to the site several times", "it came back to the site rarely"),
    "num_form_submissions": ("it submitted several forms", "it submitted few forms"),
    "pricing_page_views": ("it visited the pricing page", "it visited the pricing page only briefly"),
    "email_clicks_30d": ("it clicked recent emails", "it clicked few recent emails"),
}


def reason_clause(key: str, contribution: float) -> str:
    """The clause for one driver, in the direction it moved the score."""
    if key in COUNT_LABELS:
        up, down = COUNT_LABELS[key]
        return up if contribution > 0 else down
    if key in LABELS:
        return LABELS[key]
    if "=" in key:
        f, v = key.split("=", 1)
        return f"the {f.replace('_', ' ')} is {v.replace('_', ' ')}"
    return key.replace("_", " ")


def driver_label(key: str, contribution: float) -> str:
    """A clause as a row label: "it came in through the website" -> "Came in through the website"."""
    clause = reason_clause(key, contribution)
    if clause.startswith("it "):
        clause = clause[3:]
    return clause[:1].upper() + clause[1:]


def size_band(n: Optional[int]) -> str:
    if n is None:
        return "unknown"
    if n < 50:
        return "under_50"
    if n < 150:
        return "50_149"
    if n < 300:
        return "150_299"
    if n <= 500:
        return "300_500"
    return "over_500"


def country_group(c: str) -> str:
    c = (c or "").upper()
    if c in ("US", "USA", "UNITED STATES"):
        return "us"
    if c in ("CA", "CANADA", "UK", "GB", "UNITED KINGDOM", "AU", "AUSTRALIA", "IE", "NZ"):
        return "anglophone"
    return "other" if c else "unknown"


def is_student_or_intern(title: str) -> int:
    t = (title or "").lower()
    return int(any(w in t for w in ("student", "intern", "trainee", "graduate", "fellow")))


def feature_row(contact: Contact, company: Optional[Company]) -> dict:
    emp = company.num_employees if company else None
    industry = (company.industry if company and company.industry else "unknown").lower().replace(" ", "_")
    return {
        "channel": contact.channel if contact.channel in CHANNELS else "website",
        "seniority": contact.seniority if contact.seniority in SENIORITIES else "unknown",
        "size_band": size_band(emp),
        "industry": industry,
        "country_group": country_group(contact.country or (company.country if company else "")),
        "log_page_views": math.log1p(max(0, contact.hs_analytics_num_page_views)),
        "log_visits": math.log1p(max(0, contact.hs_analytics_num_visits)),
        "num_form_submissions": min(contact.num_form_submissions, 10),
        "pricing_page_views": min(contact.pricing_page_views, 10),
        "email_clicks_30d": min(contact.email_clicks_30d, 20),
        "webinar_registered": int(bool(contact.webinar_registered)),
        "webinar_attended": int(bool(contact.webinar_attended)),
        "is_referral": int(contact.channel == "referral" or bool(contact.referral_partner)),
        "has_competitor": int(bool(company and company.uses_payroll_vendor)),
        "in_target_size": int(emp is not None and 50 <= emp <= 500),
        "is_student_or_intern": is_student_or_intern(contact.job_title),
        "has_notes": int(bool((contact.notes or "").strip())),
    }


def frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=FEATURES)
    for c in CATEGORICAL:
        df[c] = df[c].astype(str)
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def input_hash(row: dict) -> str:
    """Stable hash of the model input, so a re-run on unchanged data is a no-op."""
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:16]
