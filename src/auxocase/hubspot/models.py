"""HubSpot-shaped records.

The service only ever sees these types. The real client maps them to and from
HubSpot's `{"id": ..., "properties": {...}}` JSON; the mock keeps them in
SQLite. Property names follow HubSpot's internal names where one exists
(H01, H02, H03 in the knowledge base); the `auxo_*` names are the custom
properties this service creates (H02).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

# The six marketing channels in the brief, derived from HubSpot's original
# source and its drill-downs (H01). `channel` is what the model uses.
CHANNELS = ("website", "webinar", "paid_ads", "email", "events", "referral")

# HubSpot's hs_analytics_source labels (H01). The exact enumeration values
# must be confirmed against GET /crm/v3/properties/contacts on the real
# portal; the mock uses these display labels.
ANALYTICS_SOURCES = (
    "ORGANIC_SEARCH", "PAID_SEARCH", "PAID_SOCIAL", "SOCIAL_MEDIA", "EMAIL_MARKETING",
    "REFERRALS", "OTHER_CAMPAIGNS", "DIRECT_TRAFFIC", "OFFLINE",
)

SENIORITIES = ("c_level", "vp", "director", "manager", "individual", "unknown")

# Lead status values the routing step treats as "open" (an SDR is working it).
OPEN_LEAD_STATUSES = ("NEW", "OPEN", "IN_PROGRESS", "ATTEMPTED_TO_CONTACT", "CONNECTED")
# The statuses an assigned lead holds until its first touch. An SDR's capacity
# limit counts these only (BrightPath answer 4: one waiting lead, refilled the
# moment it is touched), so working a lead frees the slot.
WAITING_LEAD_STATUSES = ("NEW", "OPEN")
# A lead is "worked" once an SDR has moved it past the status an assignment sets (NEW/OPEN): the first touch.
TOUCHED_STATUSES = ("IN_PROGRESS", "ATTEMPTED_TO_CONTACT", "CONNECTED", "UNQUALIFIED", "QUALIFIED")

# Custom contact properties written by this service (H02). Score is 0-100 for
# the HubSpot view; probability is the calibrated value the score comes from.
AUXO_PROPERTIES = {
    "auxo_score": "number",
    "auxo_probability": "number",
    "auxo_rank": "number",
    "auxo_reason": "string",
    "auxo_model_version": "string",
    "auxo_scored_at": "datetime",
    "auxo_route": "enumeration",       # assigned | held | holdout | incomplete | review
    "auxo_review_flag": "string",      # why a person should look, or empty
    "auxo_holdout": "bool",            # routed regardless of score, for unbiased labels
}


@dataclass
class Company:
    id: str
    name: str
    domain: str = ""
    industry: str = ""
    num_employees: Optional[int] = None
    annual_revenue: Optional[float] = None
    country: str = ""
    uses_payroll_vendor: str = ""      # a competitor name, or empty


@dataclass
class Contact:
    id: str
    email: str
    first_name: str = ""
    last_name: str = ""
    job_title: str = ""
    seniority: str = "unknown"
    company_id: Optional[str] = None
    country: str = ""
    lifecycle_stage: str = "lead"              # lead | marketingqualifiedlead | salesqualifiedlead | opportunity | customer
    hs_lead_status: str = ""                   # empty until an SDR picks it up
    hs_analytics_source: str = "DIRECT_TRAFFIC"
    hs_analytics_source_data_1: str = ""
    hs_analytics_source_data_2: str = ""
    channel: str = "website"                   # derived; one of CHANNELS
    hs_analytics_num_page_views: int = 0
    hs_analytics_num_visits: int = 0
    num_form_submissions: int = 0
    pricing_page_views: int = 0
    webinar_registered: bool = False
    webinar_attended: bool = False
    referral_partner: str = ""
    email_clicks_30d: int = 0
    notes: str = ""                            # free text: form answers, webinar questions, CS notes
    hubspot_score: int = 0                     # HubSpot's own points model (0-100)
    hubspot_owner_id: Optional[str] = None
    createdate: datetime = field(default_factory=datetime.utcnow)
    lastmodifieddate: datetime = field(default_factory=datetime.utcnow)
    # Written back by this service
    auxo_score: Optional[float] = None
    auxo_probability: Optional[float] = None
    auxo_rank: Optional[int] = None
    auxo_reason: str = ""
    auxo_model_version: str = ""
    auxo_scored_at: Optional[datetime] = None
    auxo_route: str = ""
    auxo_review_flag: str = ""
    auxo_holdout: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("createdate", "lastmodifieddate", "auxo_scored_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d


@dataclass
class Deal:
    id: str
    contact_id: str
    dealstage: str                 # appointmentscheduled | qualifiedtobuy | presentationscheduled | closedwon | closedlost
    amount: float = 0.0
    createdate: datetime = field(default_factory=datetime.utcnow)
    closedate: Optional[datetime] = None
    hs_is_closed_won: bool = False
    owner_id: Optional[str] = None
    closed_lost_reason: str = ""        # HubSpot's closed_lost_reason: free text, messy (brief answer 2)


@dataclass
class Meeting:
    id: str
    contact_id: str
    owner_id: Optional[str]
    start_time: datetime
    outcome: str = "SCHEDULED"     # SCHEDULED | COMPLETED | NO_SHOW | CANCELED | RESCHEDULED


@dataclass
class Owner:
    id: str
    name: str
    email: str
    active: bool = True
    capacity_limit: int = 45       # assigned leads waiting for a first touch (WAITING_LEAD_STATUSES)
    team: str = "sdr"


@dataclass
class Task:
    id: str
    contact_id: str
    owner_id: str
    subject: str
    body: str
    status: str = "NOT_STARTED"
    created_at: datetime = field(default_factory=datetime.utcnow)
    due_at: Optional[datetime] = None


@dataclass
class ChangeEvent:
    """One webhook-style event (H04): creation or a property change.

    Delivery may be duplicated and out of order, so consumers key work on
    (object_id, occurred_at) and re-read the record rather than trusting the
    event payload.
    """
    event_id: str
    object_id: str
    subscription_type: str         # contact.creation | contact.propertyChange | deal.propertyChange | meeting.creation
    property_name: str = ""
    occurred_at: datetime = field(default_factory=datetime.utcnow)
    attempt: int = 0


@dataclass
class BatchResult:
    """Result of a batch write (H05): HubSpot returns 207 multi-status."""
    ok: list[str]
    failed: dict[str, str]         # id -> error

    @property
    def all_ok(self) -> bool:
        return not self.failed
