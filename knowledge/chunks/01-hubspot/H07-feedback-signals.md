---
id: H07
title: Feedback signals: detecting demo booked and closed won/lost
topic: hubspot
sources: [hubspot-docs-meetings, hubspot-docs-deals, hubspot-docs-associations, hubspot-kb-deal-properties, hubspot-kb-pipelines, hubspot-kb-activity-properties, hubspot-kb-contact-properties, hubspot-kb-lifecycle, hubspot-docs-webhooks, hubspot-docs-search]
last_verified: 2026-10-27
---

## Claims

- A meeting object records `hs_meeting_outcome` with outcomes scheduled, completed, rescheduled, no show and canceled (the example uses `SCHEDULED`), `hs_meeting_start_time`, `hs_meeting_end_time` and `hs_timestamp`, and is associated to contacts with association type 200 [hubspot-docs-meetings §Create a meeting].
- Meetings created manually, through the meetings tool, or via Google Calendar and Office 365 integrations all appear as meeting objects [hubspot-docs-meetings §Retrieve meetings].
- The "Meeting source" activity property records where the meeting was booked, and meeting outcome options come from the account's call and meeting outcome settings [hubspot-kb-activity-properties §Meeting properties].
- "Number of form submissions" on a contact includes meeting scheduling page forms, and "Last engagement date" includes meeting bookings [hubspot-kb-contact-properties §Contact activity].
- Default deal stages and probabilities are Appointment scheduled 20%, Qualified to buy 40%, Presentation scheduled 60%, Decision maker bought-in 80%, Contract sent 90%, Closed won 100% (Won) and Closed lost 0% (Lost); Won and Lost are the closed stages, both must exist for sales reporting, and each stage has an internal name used by integrations and APIs that does not change when the label is renamed [hubspot-kb-pipelines §Default pipeline stages].
- Deal properties include "Is Closed Won" (true if the deal is in the closed won state), "Is closed lost", "Closed won reason", "Closed lost reason", "Close date" (set to the current date when a deal moves into a closed stage), "Date entered [stage]", "Date exited [stage]", "Date entered current stage" and "Deal probability" [hubspot-kb-deal-properties §Deal information].
- Deals are read with `GET /crm/v3/objects/deals/{dealId}` or batch read, updated with `PATCH` or `POST .../deals/batch/update`, and `dealstage` and `pipeline` hold internal IDs [hubspot-docs-deals §Retrieve deals].
- Deal→contact association type is 3 and deal→primary company is 5; a record can have one primary company [hubspot-docs-associations §Association type ID values].
- The Opportunity lifecycle stage is defined as "associated with a deal" and Customer as "has at least one closed deal"; stage updates can be driven by a record's associations [hubspot-kb-lifecycle §Understand lifecycle stages].
- Webhook subscriptions exist for `deal.creation`, `deal.propertyChange`, `deal.associationChange` and `contact.associationChange`, and association-change payloads carry `fromObjectId`, `toObjectId`, `associationType` and `associationRemoved` [hubspot-docs-webhooks §Webhook subscriptions].
- CRM search supports `associations.{objectType}` pseudo-properties to find records linked to a given record and `hs_lastmodifieddate` range filters on engagement objects such as meetings [hubspot-docs-search §Search through associations].

## Implications for the build

- Define "demo booked" as: a meeting object associated (type 200) to the contact whose `hs_meeting_start_time` is after the routing timestamp and whose Meeting source is the meetings tool or calendar integration, with outcome not canceled; capture the booking time from the meeting's create time, and re-check outcome later for completed vs no show, since the booking and the outcome are separate signals.
- Subscribe to `contact.associationChange` (new meeting or deal linked to the contact) and `deal.propertyChange` on `dealstage`; on each event re-read the deal and resolve its contacts (type 3) and primary company (type 5) to find which scored lead the outcome belongs to.
- Treat a lead as closed-won when a deal associated to the lead (or its primary company) has "Is Closed Won" true, and record "Date entered closed won" rather than Close date as the outcome timestamp, because Close date is overwritten to the current date whenever the deal enters any closed stage and can be edited.
- Read stage internal IDs from the account's pipeline definition and configure which stages count as won/lost per pipeline, instead of hard-coding "closedwon"/"closedlost"; custom pipelines rename labels but internal names persist.
- Label timing matters for training: a lead routed in month M can book a demo within days but close months later, so store the routing timestamp and compute labels at fixed horizons (for example demo within 14 days, won within 120 days) rather than at "now".
- Use lifecycle stage moves to Opportunity/Customer only as a cross-check; they are driven by association rules that admins can change and do not identify which deal or which SDR produced the outcome.
