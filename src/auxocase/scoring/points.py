"""The points model, kept for two reasons.

1. It is the cold-start scorer before the first trained model is approved,
   and the fallback when the model artifact can't be loaded (Blueprint job 1:
   "leads whose AI output is missing or malformed get the rule score alone,
   marked as such").
2. It is the baseline every trained model is measured against (L03: lift at
   the capacity cut-off over the points model is the primary metric).

Unlike HubSpot's current table, which the brief says scores job title,
company size and pages only, this one gives channel its own row, so the
baseline already reflects what the business knows about referrals and
webinars. Marketing and sales can edit `POINTS` together; the version is
stamped on every score.
"""
from __future__ import annotations

from typing import Optional

from ..hubspot.models import Company, Contact
from .features import size_band

POINTS_VERSION = "points-v2"

POINTS = {
    "channel": {"referral": 25, "webinar": 18, "events": 10, "website": 8, "email": 5, "paid_ads": 4},
    "seniority": {"c_level": 15, "vp": 14, "director": 12, "manager": 8, "individual": 2, "unknown": 0},
    "size_band": {"50_149": 12, "150_299": 15, "300_500": 12, "under_50": 2, "over_500": 4, "unknown": 3},
    "webinar_attended": 8,
    "pricing_page_view": 4,       # per view, capped
    "pricing_cap": 12,
    "form_submission": 3,          # per form, capped
    "form_cap": 9,
    "page_views_per_5": 1,         # per 5 page views, capped
    "page_views_cap": 6,
    "competitor": -6,
    "student": -20,
}


def points_breakdown(contact: Contact, company: Optional[Company]) -> dict[str, int]:
    emp = company.num_employees if company else None
    b: dict[str, int] = {}
    b["channel"] = POINTS["channel"].get(contact.channel, 4)
    b["seniority"] = POINTS["seniority"].get(contact.seniority, 0)
    b["company size"] = POINTS["size_band"][size_band(emp)]
    if contact.webinar_attended:
        b["webinar attended"] = POINTS["webinar_attended"]
    if contact.pricing_page_views:
        b["pricing page"] = min(contact.pricing_page_views * POINTS["pricing_page_view"], POINTS["pricing_cap"])
    if contact.num_form_submissions:
        b["forms"] = min(contact.num_form_submissions * POINTS["form_submission"], POINTS["form_cap"])
    if contact.hs_analytics_num_page_views:
        b["page views"] = min(contact.hs_analytics_num_page_views // 5 * POINTS["page_views_per_5"], POINTS["page_views_cap"])
    if company and company.uses_payroll_vendor:
        b["competitor in place"] = POINTS["competitor"]
    t = (contact.job_title or "").lower()
    if any(w in t for w in ("student", "intern", "trainee")):
        b["student or intern"] = POINTS["student"]
    return b


def points_score(contact: Contact, company: Optional[Company]) -> int:
    return max(0, min(100, sum(points_breakdown(contact, company).values())))
