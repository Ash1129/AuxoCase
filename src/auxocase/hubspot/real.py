"""`HubSpotClient` over the CRM v3 API with httpx.

UNVERIFIED AGAINST A LIVE PORTAL. This module is wired from the HubSpot
documentation in the knowledge base (H01-H08) and has never been run against
a real or developer test account (H11). Endpoint paths and request shapes
follow the docs; what must be confirmed on the target portal before the first
live run, by reading `GET /crm/v3/properties/{object}` and the pipeline
definition:

- `hs_is_closed_won` on deals: present as a read-only boolean on default
  pipelines; confirm the property name and that `dealstage` internal IDs for
  won/lost are `closedwon`/`closedlost` in *this* account's pipeline (H07
  says internal names persist but custom pipelines differ).
- `hs_lead_status` option values: the defaults are NEW, OPEN, IN_PROGRESS,
  OPEN_DEAL, UNQUALIFIED, ATTEMPTED_TO_CONTACT, CONNECTED, BAD_TIMING; the
  account may have renamed or added some (`OPEN_LEAD_STATUSES` in models.py
  must match).
- `hs_analytics_source` option values: ORGANIC_SEARCH, PAID_SEARCH,
  EMAIL_MARKETING, SOCIAL_MEDIA, REFERRALS, OTHER_CAMPAIGNS, DIRECT_TRAFFIC,
  OFFLINE, PAID_SOCIAL; confirm which exist and how `source_data_1/2` are
  filled for the six channels (H01).
- `hs_meeting_outcome` values: SCHEDULED, COMPLETED, RESCHEDULED, NO_SHOW,
  CANCELED are the documented defaults, but outcomes come from the account's
  call/meeting outcome settings (H07).
- The custom contact properties this service *reads* (`seniority`,
  `pricing_page_views`, `webinar_registered`, `webinar_attended`,
  `referral_partner`, `notes`) are assumed to exist under those names; map
  them in `CONTACT_PROPERTY_MAP` if the portal uses different ones.
- `num_conversion_events` is used for form submissions and `hs_email_click`
  for email clicks; both are documented but the account may track them
  elsewhere.
- First touch reads emails and calls. A call counts as connected when its
  `hs_call_disposition` is HubSpot's default "Connected" outcome
  (`CALL_CONNECTED_DISPOSITION`); accounts with custom call outcomes must
  map theirs.
- Sequence enrolment (`/automation/v4/sequences/enrollments`) needs the
  `automation.sequences.enrollments.write` scope, a Sales or Service Hub seat
  for the sender and the sender's inbox connected; the enrolling user is the
  sender's HubSpot user ID, read from the owners list. Booking links come
  from `/scheduler/v3/meetings/meeting-links` (scope `scheduler.meetings.meeting-link.read`);
  without it drafts go out with no link.

No network calls happen at import time or in the constructor.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx

from .models import (
    AUXO_PROPERTIES,
    OPEN_LEAD_STATUSES,
    PROMOTED_ON_ASSIGNMENT,
    SDR_LIFECYCLE_STAGES,
    WAITING_LEAD_STATUSES,
    BatchResult,
    ChangeEvent,
    Company,
    Contact,
    Deal,
    Engagement,
    Meeting,
    Owner,
    SequenceEnrollment,
    Task,
)

BATCH_LIMIT = 100          # batch endpoints (H05)
SEARCH_PAGE = 200          # search page cap (H04)
SEARCH_MAX_RESULTS = 10_000
SEARCH_MIN_INTERVAL = 0.2  # 5 searches/second per account (H04)
RETRY_STATUSES = {429, 502, 503, 504, 521, 523, 524}

# Contact dataclass field -> HubSpot internal property name. Fields that are
# not listed are derived locally (`channel`) or share the same name.
CONTACT_PROPERTY_MAP: dict[str, str] = {
    "email": "email",
    "first_name": "firstname",
    "last_name": "lastname",
    "job_title": "jobtitle",
    "seniority": "seniority",                       # custom, confirm
    "country": "country",
    "lifecycle_stage": "lifecyclestage",
    "hs_lead_status": "hs_lead_status",
    "hs_analytics_source": "hs_analytics_source",
    "hs_analytics_source_data_1": "hs_analytics_source_data_1",
    "hs_analytics_source_data_2": "hs_analytics_source_data_2",
    "hs_analytics_num_page_views": "hs_analytics_num_page_views",
    "hs_analytics_num_visits": "hs_analytics_num_visits",
    "num_form_submissions": "num_conversion_events",
    "pricing_page_views": "pricing_page_views",     # custom, confirm
    "webinar_registered": "webinar_registered",     # custom, confirm
    "webinar_attended": "webinar_attended",         # custom, confirm
    "referral_partner": "referral_partner",         # custom, confirm
    "email_clicks_30d": "hs_email_click",
    "notes": "notes",                               # custom, confirm
    "hubspot_score": "hubspotscore",
    "hubspot_owner_id": "hubspot_owner_id",
    "createdate": "createdate",
    "lastmodifieddate": "lastmodifieddate",
    **{name: name for name in AUXO_PROPERTIES},
}
_HS_TO_FIELD = {v: k for k, v in CONTACT_PROPERTY_MAP.items()}
_CONTACT_INTS = {"hs_analytics_num_page_views", "hs_analytics_num_visits", "num_form_submissions",
                 "pricing_page_views", "email_clicks_30d", "hubspot_score", "auxo_rank"}
_CONTACT_FLOATS = {"auxo_score", "auxo_probability"}
_CONTACT_BOOLS = {"webinar_registered", "webinar_attended", "auxo_holdout", "auxo_below_gate"}
_CONTACT_DATETIMES = {"createdate", "lastmodifieddate", "auxo_scored_at"}

COMPANY_PROPERTIES = ["name", "domain", "industry", "numberofemployees", "annualrevenue", "country", "uses_payroll_vendor"]
DEAL_PROPERTIES = ["dealstage", "amount", "createdate", "closedate", "hs_is_closed_won", "hubspot_owner_id", "hs_lastmodifieddate", "closed_lost_reason"]
MEETING_PROPERTIES = ["hs_meeting_start_time", "hs_meeting_outcome", "hubspot_owner_id", "hs_timestamp", "hs_createdate"]
TASK_PROPERTIES = ["hs_task_subject", "hs_task_body", "hs_task_status", "hubspot_owner_id", "hs_timestamp", "hs_createdate"]
EMAIL_PROPERTIES = ["hs_timestamp", "hs_email_direction", "hs_email_subject", "hs_email_text", "hubspot_owner_id", "hs_createdate"]
CALL_PROPERTIES = ["hs_timestamp", "hs_call_direction", "hs_call_disposition", "hubspot_owner_id", "hs_createdate"]
CALL_CONNECTED_DISPOSITION = "f240bbac-87c9-4f6e-bf70-924b57d47db7"   # HubSpot's default "Connected" call outcome
# HubSpot's default call outcomes by id (Calls API docs); a portal with its own outcomes maps them here.
CALL_DISPOSITION_IDS = {
    CALL_CONNECTED_DISPOSITION: "connected",
    "73a0d17f-1163-4015-bdd5-ec830791da20": "no_answer",
    "b2cf5968-551e-4856-9783-52b3da59a7d0": "left_voicemail",
    "a4c4c377-d246-4b32-a13b-75a56a4cd0ff": "left_live_message",
    "9d9162e7-6cf3-4944-bf63-4dff82258764": "busy",
    "17b47fee-58de-441e-a44c-c6300d46f273": "wrong_number",
}

# Association type IDs (H06, H07).
ASSOC_TASK_TO_CONTACT = 204
ASSOC_MEETING_TO_CONTACT = 200
ASSOC_DEAL_TO_CONTACT = 3

# How `channel` is derived from the original source (H01). Order matters:
# the first matching rule wins.
def derive_channel(source: str, data_1: str, data_2: str) -> str:
    text = f"{data_1} {data_2}".lower()
    if source == "REFERRALS":
        return "referral"
    if "webinar" in text:
        return "webinar"
    if source in ("PAID_SEARCH", "PAID_SOCIAL"):
        return "paid_ads"
    if source == "EMAIL_MARKETING":
        return "email"
    if source == "OFFLINE" or "event" in text:
        return "events"
    return "website"


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_dt(value: Any) -> Optional[datetime]:
    """HubSpot returns ISO-8601 with Z, or epoch milliseconds for some fields."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).replace(tzinfo=None)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _parse_bool(value: Any) -> bool:
    return str(value).lower() in ("true", "1", "yes")


class HubSpotError(RuntimeError):
    pass


class RealHubSpot:
    """CRM v3 client. Writes are full desired state so retries are idempotent (H05)."""

    def __init__(self, token: str, base_url: str = "https://api.hubapi.com", timeout: float = 20.0):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._queue: deque[ChangeEvent] = deque()
        self._last_search = 0.0
        self._owner_cache: tuple[float, list[Owner]] = (0.0, [])
        self._user_ids: dict[str, str] = {}    # owner email -> HubSpot user ID (sequence enrolment acts as a user)

    # ---------------------------------------------------------------- http
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url, timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            )
        return self._client

    def _request(self, method: str, path: str, *, json: Any = None, params: Optional[dict] = None, retries: int = 4) -> httpx.Response:
        """One call with backoff on 429/5xx; the caller decides what a 207 or 404 means."""
        delay = 1.0
        for attempt in range(retries + 1):
            resp = self.client.request(method, path, json=json, params=params)
            if resp.status_code not in RETRY_STATUSES or attempt == retries:
                if resp.status_code >= 400 and resp.status_code not in (207, 404):
                    raise HubSpotError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
                return resp
            retry_after = resp.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after else delay)
            delay = min(delay * 2, 30.0)
        raise HubSpotError("unreachable")

    def _search(self, object_type: str, body: dict) -> dict:
        """Search has its own 5/s budget and no rate-limit headers (H04), so space calls locally."""
        wait = SEARCH_MIN_INTERVAL - (time.monotonic() - self._last_search)
        if wait > 0:
            time.sleep(wait)
        self._last_search = time.monotonic()
        return self._request("POST", f"/crm/v3/objects/{object_type}/search", json=body).json()

    def _search_all(self, object_type: str, body: dict, max_results: int = SEARCH_MAX_RESULTS) -> list[dict]:
        results: list[dict] = []
        after: Optional[str] = None
        while len(results) < max_results:
            page = dict(body, limit=min(SEARCH_PAGE, max_results - len(results)))
            if after:
                page["after"] = after
            data = self._search(object_type, page)
            results.extend(data.get("results", []))
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
        return results

    # ------------------------------------------------------------- mapping
    @staticmethod
    def _contact_from_json(obj: dict) -> Contact:
        props = obj.get("properties", {}) or {}
        kwargs: dict[str, Any] = {"id": str(obj["id"])}
        for hs_name, raw in props.items():
            field = _HS_TO_FIELD.get(hs_name)
            if field is None or field == "id":
                continue
            if raw is None:
                continue
            if field in _CONTACT_INTS:
                kwargs[field] = int(float(raw))
            elif field in _CONTACT_FLOATS:
                kwargs[field] = float(raw)
            elif field in _CONTACT_BOOLS:
                kwargs[field] = _parse_bool(raw)
            elif field in _CONTACT_DATETIMES:
                kwargs[field] = _parse_dt(raw)
            else:
                kwargs[field] = str(raw)
        companies = (obj.get("associations", {}).get("companies", {}) or {}).get("results", [])
        if companies:
            kwargs["company_id"] = str(companies[0]["id"])
        kwargs.setdefault("email", "")
        contact = Contact(**{k: v for k, v in kwargs.items() if v is not None})
        contact.channel = derive_channel(contact.hs_analytics_source, contact.hs_analytics_source_data_1, contact.hs_analytics_source_data_2)
        return contact

    @staticmethod
    def _properties_to_json(props: dict) -> dict[str, str]:
        """HubSpot wants every property as a string; datetimes as ISO or ms, bools as 'true'/'false'."""
        out: dict[str, str] = {}
        for field, value in props.items():
            name = CONTACT_PROPERTY_MAP.get(field, field)
            if value is None:
                out[name] = ""
            elif isinstance(value, bool):
                out[name] = "true" if value else "false"
            elif isinstance(value, datetime):
                out[name] = str(_ms(value))
            else:
                out[name] = str(value)
        return out

    @staticmethod
    def _company_from_json(obj: dict) -> Company:
        p = obj.get("properties", {}) or {}
        return Company(
            id=str(obj["id"]), name=p.get("name") or "", domain=p.get("domain") or "", industry=p.get("industry") or "",
            num_employees=int(float(p["numberofemployees"])) if p.get("numberofemployees") else None,
            annual_revenue=float(p["annualrevenue"]) if p.get("annualrevenue") else None,
            country=p.get("country") or "", uses_payroll_vendor=p.get("uses_payroll_vendor") or "",
        )

    @staticmethod
    def _deal_from_json(obj: dict, contact_id: str) -> Deal:
        p = obj.get("properties", {}) or {}
        return Deal(
            id=str(obj["id"]), contact_id=contact_id, dealstage=p.get("dealstage") or "",
            amount=float(p["amount"]) if p.get("amount") else 0.0,
            createdate=_parse_dt(p.get("createdate")) or datetime.utcnow(), closedate=_parse_dt(p.get("closedate")),
            hs_is_closed_won=_parse_bool(p.get("hs_is_closed_won")), owner_id=p.get("hubspot_owner_id") or None,
            closed_lost_reason=p.get("closed_lost_reason") or "",
        )

    @staticmethod
    def _meeting_from_json(obj: dict, contact_id: str) -> Meeting:
        p = obj.get("properties", {}) or {}
        return Meeting(
            id=str(obj["id"]), contact_id=contact_id, owner_id=p.get("hubspot_owner_id") or None,
            start_time=_parse_dt(p.get("hs_meeting_start_time") or p.get("hs_timestamp")) or datetime.utcnow(),
            outcome=p.get("hs_meeting_outcome") or "SCHEDULED",
        )

    @staticmethod
    def _engagement_from_json(object_type: str, obj: dict, contact_id: str) -> Engagement:
        p = obj.get("properties", {}) or {}
        if object_type == "emails":
            direction = "INCOMING" if p.get("hs_email_direction") == "INCOMING_EMAIL" else "OUTGOING"
            kind, subject, connected = "EMAIL", p.get("hs_email_subject") or "", False
        else:
            direction = "INCOMING" if p.get("hs_call_direction") == "INBOUND" else "OUTGOING"
            kind, subject, connected = "CALL", "", p.get("hs_call_disposition") == CALL_CONNECTED_DISPOSITION
        logged = _parse_dt(p.get("hs_createdate")) or datetime.utcnow()
        disposition = CALL_DISPOSITION_IDS.get(p.get("hs_call_disposition") or "", "") if kind == "CALL" else ""
        return Engagement(id=f"{object_type}-{obj['id']}", contact_id=contact_id, type=kind, direction=direction,
                          timestamp=_parse_dt(p.get("hs_timestamp")) or logged, owner_id=p.get("hubspot_owner_id") or None,
                          subject=subject, connected=connected, disposition=disposition, logged_at=logged,
                          body=(p.get("hs_email_text") or "") if kind == "EMAIL" else "")

    @staticmethod
    def _task_from_json(obj: dict, contact_id: str) -> Task:
        p = obj.get("properties", {}) or {}
        return Task(
            id=str(obj["id"]), contact_id=contact_id, owner_id=p.get("hubspot_owner_id") or "",
            subject=p.get("hs_task_subject") or "", body=p.get("hs_task_body") or "",
            status=p.get("hs_task_status") or "NOT_STARTED",
            created_at=_parse_dt(p.get("hs_createdate")) or datetime.utcnow(), due_at=_parse_dt(p.get("hs_timestamp")),
        )

    def _contact_ids_for(self, object_type: str, object_ids: list[str]) -> dict[str, str]:
        """object id -> first associated contact id, via the v4 batch association read."""
        out: dict[str, str] = {}
        for start in range(0, len(object_ids), BATCH_LIMIT):
            chunk = object_ids[start:start + BATCH_LIMIT]
            data = self._request(
                "POST", f"/crm/v4/associations/{object_type}/contacts/batch/read",
                json={"inputs": [{"id": i} for i in chunk]},
            ).json()
            for r in data.get("results", []):
                targets = r.get("to", [])
                if targets:
                    out[str(r["from"]["id"])] = str(targets[0]["toObjectId"])
        return out

    # --------------------------------------------------------------- schema
    def ensure_properties(self) -> list[str]:
        existing = {p["name"] for p in self._request("GET", "/crm/v3/properties/contacts").json().get("results", [])}
        created: list[str] = []
        field_types = {"number": "number", "string": "text", "datetime": "date", "enumeration": "select", "bool": "booleancheckbox"}
        for name, type_ in AUXO_PROPERTIES.items():
            if name in existing:
                continue
            body: dict[str, Any] = {
                "name": name, "label": name.replace("_", " ").title(), "type": type_,
                "fieldType": field_types[type_], "groupName": "auxocase",
                "description": "Written by the AuxoCase scoring service.",
            }
            if type_ == "enumeration":
                body["options"] = [{"label": v, "value": v} for v in ("assigned", "held", "holdout", "incomplete", "review")]
            if name == "auxo_email_body":
                body["fieldType"] = "textarea"
            if type_ == "bool":
                body["type"], body["fieldType"] = "enumeration", "booleancheckbox"
                body["options"] = [{"label": "Yes", "value": "true"}, {"label": "No", "value": "false"}]
            self._request("POST", "/crm/v3/properties/contacts", json=body)
            created.append(name)
        return created

    # ------------------------------------------------- contacts / companies
    def get_contact(self, contact_id: str) -> Optional[Contact]:
        resp = self._request(
            "GET", f"/crm/v3/objects/contacts/{contact_id}",
            params={"properties": ",".join(CONTACT_PROPERTY_MAP.values()), "associations": "companies"},
        )
        if resp.status_code == 404:
            return None
        return self._contact_from_json(resp.json())

    def update_company(self, company_id: str, props: dict) -> bool:
        names = {"num_employees": "numberofemployees", "annual_revenue": "annualrevenue", "industry": "industry", "country": "country"}
        body = {"properties": {names[k]: v for k, v in props.items() if k in names and v is not None}}
        if not body["properties"]:
            return False
        resp = self._request("PATCH", f"/crm/v3/objects/companies/{company_id}", json=body)
        return resp.status_code < 300

    def get_company(self, company_id: str) -> Optional[Company]:
        resp = self._request("GET", f"/crm/v3/objects/companies/{company_id}", params={"properties": ",".join(COMPANY_PROPERTIES)})
        if resp.status_code == 404:
            return None
        return self._company_from_json(resp.json())

    def search_contacts_modified_since(self, since: datetime, limit: int = 200) -> list[Contact]:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "lastmodifieddate", "operator": "GTE", "value": str(_ms(since))}]}],
            "sorts": [{"propertyName": "lastmodifieddate", "direction": "ASCENDING"}],
            "properties": list(CONTACT_PROPERTY_MAP.values()),
            "limit": min(limit, SEARCH_PAGE),
        }
        return [self._contact_from_json(o) for o in self._search("contacts", body).get("results", [])]

    def list_contacts(self, lifecycle_stage: Optional[str] = None, created_since: Optional[datetime] = None) -> list[Contact]:
        """Search-based listing; capped at 10,000 by HubSpot, so callers filter by stage and creation date."""
        filters = [{"propertyName": "lifecyclestage", "operator": "EQ", "value": lifecycle_stage}] if lifecycle_stage else []
        if created_since is not None:
            filters.append({"propertyName": "createdate", "operator": "GTE", "value": str(int(created_since.timestamp() * 1000))})
        body = {
            "filterGroups": [{"filters": filters}] if filters else [],
            "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
            "properties": list(CONTACT_PROPERTY_MAP.values()),
        }
        return [self._contact_from_json(o) for o in self._search_all("contacts", body)]

    def batch_update_contacts(self, updates: Iterable[tuple[str, dict]]) -> BatchResult:
        items = list(updates)
        result = BatchResult(ok=[], failed={})
        for start in range(0, len(items), BATCH_LIMIT):
            chunk = items[start:start + BATCH_LIMIT]
            inputs = [{"id": cid, "properties": self._properties_to_json(props)} for cid, props in chunk]
            try:
                resp = self._request("POST", "/crm/v3/objects/contacts/batch/update", json={"inputs": inputs})
            except HubSpotError as e:
                # The whole chunk is suspect (H05): report it failed; the caller re-reads before retrying.
                for cid, _ in chunk:
                    result.failed[cid] = str(e)
                continue
            data = resp.json() if resp.content else {}
            ok_ids = {str(r["id"]) for r in data.get("results", [])}
            errors: dict[str, str] = {}
            for err in data.get("errors", []) or []:
                ctx = err.get("context", {}) or {}
                ids = ctx.get("ids") or ctx.get("objectWriteTraceId") or []
                message = err.get("message", "unknown error")
                for cid in (ids if isinstance(ids, list) else [ids]):
                    errors[str(cid)] = message
            for cid, _ in chunk:
                if cid in errors:
                    result.failed[cid] = errors[cid]
                elif cid in ok_ids or resp.status_code == 200:
                    result.ok.append(cid)
                else:
                    result.failed[cid] = "not in results of 207 response"
        return result

    # ---------------------------------------------------------- change feed
    @staticmethod
    def verify_signature(app_secret: str, body_bytes: bytes, signature_header: str) -> bool:
        """X-HubSpot-Signature v1: SHA-256 hex of app secret + raw body (H04)."""
        expected = hashlib.sha256(app_secret.encode("utf-8") + body_bytes).hexdigest()
        return hmac.compare_digest(expected, (signature_header or "").strip())

    def enqueue_webhook_payload(self, payload: list[dict]) -> int:
        """Called by the FastAPI receiver within HubSpot's 5-second window; returns how many were queued."""
        n = 0
        for item in payload:
            self._queue.append(ChangeEvent(
                event_id=str(item.get("eventId", "")),
                object_id=str(item.get("objectId", "")),
                subscription_type=str(item.get("subscriptionType", "")),
                property_name=str(item.get("propertyName", "") or ""),
                occurred_at=_parse_dt(item.get("occurredAt")) or datetime.utcnow(),
                attempt=int(item.get("attemptNumber", 0) or 0),
            ))
            n += 1
        return n

    def poll_events(self, max_events: int = 100) -> list[ChangeEvent]:
        out: list[ChangeEvent] = []
        while self._queue and len(out) < max_events:
            out.append(self._queue.popleft())
        return out

    # -------------------------------------------------------------- routing
    def list_owners(self, active_only: bool = True) -> list[Owner]:
        """Owners change rarely and the call eats quota, so cache for 10 minutes (H05)."""
        cached_at, cached = self._owner_cache
        if cached and time.monotonic() - cached_at < 600:
            owners = cached
        else:
            owners = []
            after: Optional[str] = None
            while True:
                params: dict[str, Any] = {"limit": 100, "archived": "false"}
                if after:
                    params["after"] = after
                data = self._request("GET", "/crm/v3/owners", params=params).json()
                for o in data.get("results", []):
                    name = " ".join(x for x in (o.get("firstName"), o.get("lastName")) if x) or o.get("email", "")
                    teams = o.get("teams") or []
                    owners.append(Owner(
                        id=str(o["id"]), name=name, email=o.get("email", ""), active=not o.get("archived", False),
                        team=(teams[0].get("name") if teams else "sdr") or "sdr",
                    ))
                    if o.get("userId"):
                        self._user_ids[o.get("email", "")] = str(o["userId"])
                after = data.get("paging", {}).get("next", {}).get("after")
                if not after:
                    break
            links = self._meeting_links()
            for o in owners:
                o.meetings_link = links.get(self._user_ids.get(o.email, ""), "")
            self._owner_cache = (time.monotonic(), owners)
        return [o for o in owners if o.active] if active_only else list(owners)

    def open_lead_counts(self) -> dict[str, int]:
        return self._owner_counts(OPEN_LEAD_STATUSES)

    def waiting_lead_counts(self) -> dict[str, int]:
        return self._owner_counts(WAITING_LEAD_STATUSES)

    def _owner_counts(self, statuses: tuple[str, ...]) -> dict[str, int]:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "hs_lead_status", "operator": "IN", "values": list(statuses)},
                {"propertyName": "hubspot_owner_id", "operator": "HAS_PROPERTY"},
                # still the SDR's: once a lead is an opportunity or customer it is the AE's load, not the SDR's
                {"propertyName": "lifecyclestage", "operator": "IN", "values": list(SDR_LIFECYCLE_STAGES)},
            ]}],
            "properties": ["hubspot_owner_id"],
        }
        counts: dict[str, int] = {o.id: 0 for o in self.list_owners()}
        for obj in self._search_all("contacts", body):
            owner = (obj.get("properties") or {}).get("hubspot_owner_id")
            if owner:
                counts[str(owner)] = counts.get(str(owner), 0) + 1
        return counts

    def set_owner(self, contact_id: str, owner_id: str, expected_current: Optional[str] = None) -> bool:
        """Read-then-write; HubSpot has no server-side compare-and-set, so the service serialises routing (H06)."""
        resp = self._request("GET", f"/crm/v3/objects/contacts/{contact_id}", params={"properties": "hubspot_owner_id,hs_lead_status,lifecyclestage"})
        if resp.status_code == 404:
            return False
        p = resp.json().get("properties", {}) or {}
        current = p.get("hubspot_owner_id") or None
        if current != (expected_current or None):
            return False
        props = {"hubspot_owner_id": owner_id}
        if not p.get("hs_lead_status"):
            props["hs_lead_status"] = "NEW"
        if p.get("lifecyclestage") in PROMOTED_ON_ASSIGNMENT:
            props["lifecyclestage"] = "salesqualifiedlead"
        self._request("PATCH", f"/crm/v3/objects/contacts/{contact_id}", json={"properties": props})
        return True

    def create_task(self, owner_id: str, contact_id: str, subject: str, body: str, due_at: Optional[datetime] = None) -> Task:
        due = due_at or datetime.utcnow()
        payload = {
            "properties": {
                "hs_timestamp": str(_ms(due)), "hs_task_subject": subject, "hs_task_body": body,
                "hubspot_owner_id": owner_id, "hs_task_status": "NOT_STARTED", "hs_task_priority": "HIGH", "hs_task_type": "TODO",
            },
            "associations": [{
                "to": {"id": contact_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": ASSOC_TASK_TO_CONTACT}],
            }],
        }
        data = self._request("POST", "/crm/v3/objects/tasks", json=payload).json()
        return self._task_from_json(data, contact_id)

    def list_tasks(self, owner_id: Optional[str] = None) -> list[Task]:
        filters = [{"propertyName": "hubspot_owner_id", "operator": "EQ", "value": owner_id}] if owner_id else []
        body = {"filterGroups": [{"filters": filters}] if filters else [], "properties": TASK_PROPERTIES,
                "sorts": [{"propertyName": "hs_createdate", "direction": "DESCENDING"}]}
        objs = self._search_all("tasks", body, max_results=1000)
        contacts = self._contact_ids_for("tasks", [str(o["id"]) for o in objs])
        return [self._task_from_json(o, contacts.get(str(o["id"]), "")) for o in objs]

    # ------------------------------------------------------------- outcomes
    def deals_modified_since(self, since: datetime) -> list[Deal]:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": str(_ms(since))}]}],
            "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}],
            "properties": DEAL_PROPERTIES,
        }
        objs = self._search_all("deals", body)
        contacts = self._contact_ids_for("deals", [str(o["id"]) for o in objs])
        return [self._deal_from_json(o, contacts.get(str(o["id"]), "")) for o in objs]

    def meetings_since(self, since: datetime) -> list[Meeting]:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": str(_ms(since))}]}],
            "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}],
            "properties": MEETING_PROPERTIES,
        }
        objs = self._search_all("meetings", body)
        contacts = self._contact_ids_for("meetings", [str(o["id"]) for o in objs])
        return [self._meeting_from_json(o, contacts.get(str(o["id"]), "")) for o in objs]

    def deals_for_contact(self, contact_id: str) -> list[Deal]:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "associations.contact", "operator": "EQ", "value": contact_id}]}],
            "properties": DEAL_PROPERTIES,
        }
        return [self._deal_from_json(o, contact_id) for o in self._search_all("deals", body, max_results=200)]

    def meetings_for_contact(self, contact_id: str) -> list[Meeting]:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "associations.contact", "operator": "EQ", "value": contact_id}]}],
            "properties": MEETING_PROPERTIES,
        }
        return [self._meeting_from_json(o, contact_id) for o in self._search_all("meetings", body, max_results=200)]

    # ------------------------------------------------------------- activity
    def engagements_logged_since(self, since: datetime) -> list[Engagement]:
        out: list[Engagement] = []
        for object_type, props in (("emails", EMAIL_PROPERTIES), ("calls", CALL_PROPERTIES)):
            body = {
                "filterGroups": [{"filters": [{"propertyName": "hs_createdate", "operator": "GTE", "value": str(_ms(since))}]}],
                "sorts": [{"propertyName": "hs_createdate", "direction": "ASCENDING"}],
                "properties": props,
            }
            objs = self._search_all(object_type, body, max_results=2000)
            contacts = self._contact_ids_for(object_type, [str(o["id"]) for o in objs])
            out += [self._engagement_from_json(object_type, o, contacts[str(o["id"])]) for o in objs if str(o["id"]) in contacts]
        return sorted(out, key=lambda e: e.logged_at)

    def engagements_for_contact(self, contact_id: str) -> list[Engagement]:
        out: list[Engagement] = []
        for object_type, props in (("emails", EMAIL_PROPERTIES), ("calls", CALL_PROPERTIES)):
            body = {
                "filterGroups": [{"filters": [{"propertyName": "associations.contact", "operator": "EQ", "value": contact_id}]}],
                "properties": props,
            }
            out += [self._engagement_from_json(object_type, o, contact_id) for o in self._search_all(object_type, body, max_results=200)]
        return sorted(out, key=lambda e: e.timestamp)

    # ------------------------------------------------------------ sequences
    def enroll_in_sequence(self, contact_id: str, sequence_id: str, sender_email: str) -> Optional[SequenceEnrollment]:
        if not self._user_ids:
            self.list_owners(active_only=False)
        user_id = self._user_ids.get(sender_email)
        if not user_id:
            return None
        try:          # 400 when the contact is already enrolled or unsubscribed, or the inbox isn't connected
            resp = self._request("POST", "/automation/v4/sequences/enrollments", params={"userId": user_id},
                                 json={"contactId": contact_id, "sequenceId": sequence_id, "senderEmail": sender_email})
        except HubSpotError:
            return None
        if resp.status_code == 404:
            return None
        data = resp.json()
        return SequenceEnrollment(id=str(data.get("id", "")), contact_id=contact_id, sequence_id=sequence_id,
                                  sender_email=sender_email, enrolled_at=_parse_dt(data.get("enrolledAt")) or datetime.utcnow())

    def _meeting_links(self) -> dict[str, str]:
        """HubSpot user ID -> the user's personal booking page; empty without the scheduler scope."""
        try:
            data = self._request("GET", "/scheduler/v3/meetings/meeting-links", params={"limit": 100}).json()
        except HubSpotError:
            return {}
        out: dict[str, str] = {}
        for r in data.get("results", []):
            user = str(r.get("organizerUserId") or "")
            if user and r.get("link") and (user not in out or r.get("type") == "PERSONAL_LINK"):
                out[user] = r["link"]
        return out
