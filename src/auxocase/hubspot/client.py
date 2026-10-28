"""The HubSpot operations the service needs, as one interface.

Two implementations: `MockHubSpot` (SQLite, used by every test and by the
demo) and `RealHubSpot` (httpx against the v3 API, wired but unverified
until a developer test account is available; H11). The service code never
imports either directly; it takes a `HubSpotClient`.

Everything here follows the knowledge base: reads by last-modified search or
change events (H04); writes in batches of at most 100 with per-record status
(H05); owner assignment as compare-and-set so a retried run can't assign
twice (H06, Blueprint job 2 step 7); outcomes from deals and meetings (H07).
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Protocol

from .models import BatchResult, ChangeEvent, Company, Contact, Deal, Meeting, Owner, Task


class HubSpotClient(Protocol):
    # --- schema -------------------------------------------------------
    def ensure_properties(self) -> list[str]:
        """Create any missing `auxo_*` contact properties; return the names created."""

    # --- contacts and companies --------------------------------------
    def get_contact(self, contact_id: str) -> Optional[Contact]: ...
    def get_company(self, company_id: str) -> Optional[Company]: ...
    def search_contacts_modified_since(self, since: datetime, limit: int = 200) -> list[Contact]: ...
    def list_contacts(self, lifecycle_stage: Optional[str] = None) -> list[Contact]: ...
    def batch_update_contacts(self, updates: Iterable[tuple[str, dict]]) -> BatchResult:
        """Write properties for many contacts. At most 100 per request on the real API."""

    # --- change feed --------------------------------------------------
    def poll_events(self, max_events: int = 100) -> list[ChangeEvent]:
        """Webhook-equivalent: events since the last poll, possibly duplicated or out of order."""

    # --- routing ------------------------------------------------------
    def list_owners(self, active_only: bool = True) -> list[Owner]: ...
    def open_lead_counts(self) -> dict[str, int]:
        """owner_id -> number of contacts with an open lead status."""
    def set_owner(self, contact_id: str, owner_id: str, expected_current: Optional[str] = None) -> bool:
        """Compare-and-set the owner. Returns False (and writes nothing) if the current owner isn't `expected_current`."""
    def create_task(self, owner_id: str, contact_id: str, subject: str, body: str, due_at: Optional[datetime] = None) -> Task: ...
    def list_tasks(self, owner_id: Optional[str] = None) -> list[Task]: ...

    # --- outcomes -----------------------------------------------------
    def deals_modified_since(self, since: datetime) -> list[Deal]: ...
    def meetings_since(self, since: datetime) -> list[Meeting]: ...
    def deals_for_contact(self, contact_id: str) -> list[Deal]: ...
    def meetings_for_contact(self, contact_id: str) -> list[Meeting]: ...
