---
id: H12
title: Data quality of CRM inputs: what the scorer should and should not trust
topic: hubspot
sources: [wang-strong-1996, hubspot-kb-traffic-source, hubspot-kb-change-sources, hubspot-kb-contact-properties, hubspot-docs-contacts, hubspot-docs-companies, hubspot-docs-webhooks, hubspot-docs-email-events]
last_verified: 2026-10-27
---

## Claims

- Wang and Strong found, from a two-stage survey and sorting study of data consumers, that data quality is multi-dimensional and firms' improvement efforts focus too narrowly on accuracy; their framework has four categories: intrinsic (accuracy, objectivity, believability, reputation), contextual (value-added, relevancy, timeliness, completeness, appropriate amount), representational (interpretability, ease of understanding, consistency, conciseness) and accessibility (accessibility, access security) [wang-strong-1996 §Toward a Hierarchical Framework of Data Quality].
- Contextual data quality means quality must be judged against the task at hand rather than in the abstract [wang-strong-1996 §Contextual Data Quality].
- Original and Latest Traffic Source can be edited manually by users, drill-downs are cleared when the source is changed, and ad-blockers can prevent tracking cookies from setting source values at all [hubspot-kb-traffic-source §Editing and ad-blocker note].
- HubSpot records the source of every property change (Batch Update, Integration, Workflow, Import, Forms, Analytics, Data Quality/duplicate management, CRM UI with user identity, and others) [hubspot-kb-change-sources §HubSpot's change sources].
- Email is the primary unique identifier for contacts and domain for companies; duplicates are avoided only on these keys [hubspot-docs-contacts §Create contacts; hubspot-docs-companies §Properties].
- Contact merges emit `contact.merge` webhooks with `primaryObjectId`, `mergedObjectIds`, `newObjectId` and `numberOfPropertiesMoved` [hubspot-docs-webhooks §Webhook payloads].
- "Last engagement date" only updates once a contact owner is assigned, and marketing email opens and clicks are counted at most once per email in the aggregate properties [hubspot-kb-contact-properties §Contact activity].
- Email OPEN and CLICK events include a `filteredEvent` flag and bot events can appear in the API but not in the UI's recipient view [hubspot-docs-email-events §User engagement events].

## Implications for the build

- Score each input field on the Wang–Strong dimensions that matter for ranking (completeness, timeliness, believability) and monitor them: for example the share of new contacts with empty traffic source, missing primary company, or free-mail domains, and alert when a channel's completeness drops, because a feed change upstream will otherwise silently reshape the ranking.
- Use property history and change sources to weight believability: a job title entered by a form is weaker than one set by an import from a verified list; an owner set by the service versus by a user matters for feedback attribution.
- Handle merges explicitly: on `contact.merge`, re-key the service's feature store and labels from `mergedObjectIds` to `primaryObjectId`, otherwise demo and deal outcomes attach to a record the scorer no longer sees.
- Normalise before scoring: lower-case and trim emails and domains, map company domains to primary companies, and treat "Offline sources" plus a Record Source of import or integration as "channel unknown" rather than assigning it to a business channel.
- Exclude filtered/bot email events and do not derive recency from "Last engagement date" for unowned contacts; compute recency from raw events with timestamps.
- Keep the scorer's own writes out of its features (never read score, rank or reason back in as inputs), and record the data-quality snapshot used for each scoring run alongside the model version so that a reason can be reproduced later.
