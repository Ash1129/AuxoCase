---
id: H01
title: CRM objects, associations, lead source and lifecycle stage
topic: hubspot
sources: [hubspot-docs-contacts, hubspot-docs-companies, hubspot-docs-deals, hubspot-docs-owners, hubspot-docs-associations, hubspot-kb-traffic-source, hubspot-kb-contact-properties, hubspot-kb-lifecycle]
last_verified: 2026-10-27
---

## Claims

- A contact is created with `POST /crm/v3/objects/contacts` and must include at least one of `email`, `firstname` or `lastname`; HubSpot recommends always including `email` because it is the primary unique identifier used to avoid duplicates, and additional emails are also unique, so two contacts cannot share one [hubspot-docs-contacts §Create contacts].
- A contact can be read by record ID (`GET /crm/v3/objects/contacts/{recordId}`) or by email (`GET /crm/v3/objects/contacts/{email}?idProperty=email`); listing returns at most 100 per request with `limit` and `after` pagination [hubspot-docs-contacts §Retrieve contacts].
- A company must be created with at least one of `name` or `domain`; `domain` is the primary unique identifier for avoiding duplicate companies, and extra domains go in `hs_additional_domains` separated by semicolons [hubspot-docs-companies §Properties].
- A deal requires `dealname`, `dealstage` and (with several pipelines) `pipeline`; stage and pipeline values must be internal IDs, not labels, and the default pipeline is `"default"` [hubspot-docs-deals §Create deals].
- Associations are typed: contact→company is type 279 and contact→primary company is 1; deal→contact is 3 and contact→deal is 4; deal→company is 341 and deal→primary company is 5; meeting→contact 200, call→contact 194, email→contact 198, note→contact 202, task→contact 204 [hubspot-docs-associations §Association type ID values].
- A record can have only one primary company; other company associations are unlabelled or carry custom labels [hubspot-docs-associations §Associate records with a label].
- Batch read of associations accepts up to 1,000 inputs; batch create up to 2,000; associations API bursts are 150 requests per 10 seconds on Professional/Enterprise and the daily cap is 500,000, and the API limit increase add-on does not raise association limits [hubspot-docs-associations §Limits].
- `GET /crm/v3/owners` lists current owners and `archived=true` returns deactivated ones; the owner object has `id`, `userId`, `email`, `firstName`, `lastName`, `archived` and `teams` (each with `id`, `name`, `primary`); the endpoints are read-only [hubspot-docs-owners §Retrieve a list of owners].
- The owner `id` (not `userId`) must be used when assigning ownership to a record; using `userId` for assignment causes an error, and archived owners have `userId` null with the value kept in `userIdIncludingInactive` [hubspot-docs-owners §Retrieve information about an individual owner].
- Original Traffic Source is the first known web source through which a contact interacted with the business and Latest Traffic Source is the most recent; companies and deals associated with a contact mirror these properties [hubspot-kb-traffic-source §Traffic source properties and values].
- Drill-down 1 and drill-down 2 add detail per category (for example paid search: campaign name then search term; referrals: referring domain then URL; email marketing: campaign name then HubSpot email name); they are set automatically, cannot be edited manually, and are cleared if the traffic source is changed [hubspot-kb-traffic-source §Understand traffic source drill-down properties].
- The Original or Latest Traffic Source value can be changed manually on a record, and ad-blockers can prevent HubSpot's tracking cookies from setting traffic source values [hubspot-kb-traffic-source §Editing and ad-blocker note].
- "Offline sources" was historically used to track contacts created by non-web means; HubSpot now recommends the Record Source property (with Record Source Detail 1) to see how a record was created, including imports, the mobile app and integrations [hubspot-kb-traffic-source §Record source properties].
- Among default contact properties are Original/Latest Traffic Source and their drill-downs, First/Last page seen, Number of page views, Number of sessions, Record Source and Record Source Detail 1 [hubspot-kb-contact-properties §Web Analytics History].
- The default lifecycle stage sequence is Subscriber, Lead, Marketing Qualified Lead, Sales Qualified Lead, Opportunity, Customer, Evangelist, with Other as a further option; Sales Qualified Lead contains Lead Status sub-stages [hubspot-kb-lifecycle §Understand lifecycle stages].
- Default automatic lifecycle updates only move a stage forward; to set an earlier stage the value must first be cleared manually or by workflow, and calculated "Date entered/exited [stage]" properties are not cleared on a backward move [hubspot-kb-lifecycle §Update a record's lifecycle stage].
- Via the API, `lifecyclestage` on contacts and companies can only move forward unless the existing value is cleared first [hubspot-docs-contacts §Update contacts; hubspot-docs-companies §Update companies].
- The default Lead Status options are New, Open, In Progress, Open Deal, Unqualified, Attempted to Contact, Connected and Bad Timing, and the property can be customised [hubspot-kb-lifecycle §Understand lead status options].
- Each lifecycle stage, default or custom, has calculated properties Date entered, Date exited, Latest time in and Cumulative time in stage; the time-in-stage pair requires Professional or Enterprise [hubspot-kb-lifecycle §Understand lifecycle stage calculated properties].

## Implications for the build

- Model the lead as contact plus primary company: fetch the contact, resolve its primary-company association (type 1), then batch-read the company; do not rely on batch read endpoints to return associations, because they cannot.
- Treat channel as a derived field: read Original Traffic Source, both drill-downs, and Record Source/Record Source Detail 1 together, and map them to the six business channels; expect gaps for ad-blocked and manually edited records and keep the mapping table versioned in the service, not hard-coded in HubSpot.
- Never write `lifecyclestage` backwards; the scorer should write its own rank property and let a workflow or SDR set MQL/SQL, or clear-then-set deliberately when a demotion is intended.
- Store the owner `id` from `/crm/v3/owners` (refreshed periodically, including archived owners) and never the `userId`; routing writes must use the owner `id`.
- Use "Date entered [stage]" calculated properties and Lead Status to reconstruct the funnel position at scoring time rather than inferring it from the single current stage value.
- Companies and deals mirror the contact's traffic source, so do not count "source" twice when combining contact and company features.
