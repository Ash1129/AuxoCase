---
id: H03
title: Activity and engagement data and where each signal is readable
topic: hubspot
sources: [hubspot-docs-meetings, hubspot-docs-tasks, hubspot-docs-associations, hubspot-docs-form-submissions, hubspot-docs-forms, hubspot-docs-email-events, hubspot-docs-events, hubspot-docs-marketing-events, hubspot-kb-contact-properties, hubspot-kb-activity-properties, hubspot-docs-search]
last_verified: 2026-10-27
---

## Claims

- Meetings are CRM objects at `/crm/v3/objects/meetings`; `hs_timestamp` is required and marks when the meeting occurred, and other properties include `hs_meeting_title`, `hs_meeting_start_time`, `hs_meeting_end_time`, `hs_meeting_outcome`, `hubspot_owner_id` and `hs_activity_type` [hubspot-docs-meetings §Create a meeting].
- Meetings logged manually, booked through the meetings tool, or scheduled through the Google Calendar or Office 365 integration can all be retrieved, updated or deleted through the meetings endpoints [hubspot-docs-meetings §Retrieve meetings].
- Tasks are CRM objects at `/crm/v3/objects/tasks` with `hs_timestamp` (due date, required), `hs_task_subject`, `hs_task_body`, `hubspot_owner_id`, `hs_task_status` (`NOT_STARTED` or `COMPLETED`), `hs_task_priority` (`LOW`/`MEDIUM`/`HIGH`) and `hs_task_type` (`CALL`/`EMAIL`/`TODO`) [hubspot-docs-tasks §Create a task].
- Engagement-to-contact association types are meeting 200, call 194, email 198, note 202 and task 204, so a contact's activities are found by reading its associations and then batch-reading the engagement objects [hubspot-docs-associations §Association type ID values].
- Form submissions for a form are read with `GET /form-integrations/v1/submissions/forms/{form_guid}`; each result has `conversionId`, `submittedAt`, `values` (name/value pairs) and `pageUrl`; results are returned in reverse-chronological order with `limit` defaulting to 20 and capped at 50, and cursor paging via `after` [hubspot-docs-form-submissions §Get submissions for a form].
- The Forms API itself manages form definitions, and `formType` can be `hubspot`, `captured`, `flow` or `blog_comment` [hubspot-docs-forms §Forms].
- Marketing email events are read through the legacy Email Events API; events are identified by `id` and a `created` millisecond timestamp and can be fetched in bulk by `recipient`, by `appId` plus `campaignId`, or combinations [hubspot-docs-email-events §Email Events API Overview].
- Event types include SENT, DROPPED, PROCESSED, DELIVERED, DEFERRED, BOUNCE, OPEN, CLICK, STATUSCHANGE, SPAMREPORT and SUPPRESSION; OPEN and CLICK carry `userAgent`, `browser`, `location` and a `filteredEvent` flag, CLICK adds `url`, and bot events may appear in the API but not in the UI [hubspot-docs-email-events §User engagement events].
- The Events API (`GET /events/event-occurrences/2026-03` with `eventType`, `objectType`, `objectId`, `occurredAfter`, `occurredBefore`) returns standard events such as `e_visited_page`, custom events and app events, but explicitly does not include marketing email opens, clicks or bounces, which must come from the email analytics API [hubspot-docs-events §Retrieve event occurrences].
- A marketing event is a CRM object used to track events such as webinars and the contacts who registered or attended; attendance states written per contact are `REGISTERED`, `ATTENDED` (optionally with `joinedAt`/`leftAt`) and `CANCELLED`, and the participation breakdown also reports `NO_SHOW` [hubspot-docs-marketing-events §Participant state endpoints].
- A contact's participations are read with `GET /marketing/v3/marketing-events/participations/contacts/{contactIdentifier}/breakdown` (contact ID or email), returning `occurredAt`, `attendanceState`, `attendancePercentage` and `attendanceDurationSeconds`, with `limit` 1–100 and `after` paging [hubspot-docs-marketing-events §Event attendance endpoints].
- Attendance writes are idempotent as long as the contact and `interactionDateTime` are unchanged, and only the `objectId`-based (not external-ID) variants show timeline events on the contact [hubspot-docs-marketing-events §Participant state endpoints].
- Default contact properties already aggregate engagement: Number of page views, Number of sessions, Number of form submissions (including meeting scheduling forms), First/Recent conversion, Marketing emails delivered/opened/clicked (each open or click counted at most once per email), Number of times contacted (logged calls, meetings and sales emails; not tasks or notes) [hubspot-kb-contact-properties §Contact activity].
- "Last engagement date" covers the latest email open or click, lead revisit notification, meeting booking or form submission, but only updates when a contact owner is assigned [hubspot-kb-contact-properties §Contact activity].
- Meeting outcome and call outcome options are configured in the account's call and meeting outcome settings; "Meeting source" records where the meeting was booked [hubspot-kb-activity-properties §Meeting properties].
- CRM search over engagement objects supports `hs_lastmodifieddate` range filters and the pseudo-property `associations.{objectType}` to find engagements linked to a given record [hubspot-docs-search §Search through associations].

## Implications for the build

- Prefer the aggregated contact properties (page views, sessions, form submissions, emails opened/clicked, times contacted) as first-class features: they are one read per contact instead of one API per channel, and they are already what HubSpot's own scoring sees.
- For event-level features (which webinar, how long attended, which form, which email clicked) use four readers: marketing-events participation breakdown per contact, form submissions per form GUID, email events by recipient, and associated engagements via associations then batch read; each has different paging and none is a single "timeline" endpoint.
- Ignore `filteredEvent`/bot opens and clicks when building email features, otherwise automated scanners inflate engagement.
- Web page-view counts depend on tracking cookies and are missing for ad-blocked or offline-created contacts; treat absence as unknown, not zero.
- Form submission paging is 50 per page in reverse-chronological order, which is fine for incremental reads keyed by `submittedAt` but slow for history backfills on high-volume forms; backfill once and then rely on the aggregated counters plus webhooks.
- "Last engagement date" is unreliable for unowned leads, which are exactly the records the scorer sees first; compute recency from the event sources instead.
