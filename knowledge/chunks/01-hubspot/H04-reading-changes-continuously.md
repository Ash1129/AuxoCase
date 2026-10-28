---
id: H04
title: Reading changes continuously: webhooks, search polling and the journal API
topic: hubspot
sources: [hubspot-docs-webhooks, hubspot-docs-webhooks-projects, hubspot-docs-errors, hubspot-docs-private-apps, hubspot-docs-search, hubspot-docs-usage, hubspot-docs-webhooks-journal]
last_verified: 2026-10-27
---

## Claims

- Webhook subscription types are per object: `contact.creation`, `contact.deletion`, `contact.merge`, `contact.associationChange`, `contact.restore`, `contact.privacyDeletion` and `contact.propertyChange`, with the same pattern for companies, deals, tickets and line items; contact/company/deal subscriptions need the matching `crm.objects.<type>.read` scope [hubspot-docs-webhooks §Webhook subscriptions].
- Each notification carries `objectId`, `propertyName`, `propertyValue`, `changeSource`, `eventId`, `subscriptionId`, `portalId`, `appId`, `occurredAt`, `subscriptionType` and `attemptNumber` (starting at 0); merge events add `primaryObjectId`, `mergedObjectIds`, `newObjectId`; association events add `associationType`, `fromObjectId`, `toObjectId`, `associationRemoved`, `isPrimaryAssociation` [hubspot-docs-webhooks §Webhook payloads].
- In the project-based configuration the generic forms `object.creation`, `object.deletion`, `object.merge`, `object.restore`, `object.propertyChange` and `object.associationChange` exist, payloads also include `objectTypeId` and `sourceId`, and `eventId` is documented as not guaranteed unique [hubspot-docs-webhooks-projects §Response payloads].
- HubSpot POSTs an array of notifications per request; a batch holds up to 100 events [hubspot-docs-webhooks §Webhook payloads].
- Notifications may not arrive in the order the events occurred (use `occurredAt` to order them) and the same notification may be delivered more than once [hubspot-docs-webhooks §Webhook payloads].
- The receiving service must respond within five seconds or the attempt is treated as a timeout; HubSpot retries on connection failure, timeout or any 4xx/5xx response, up to 10 times, spread over the next 24 hours with varying, randomised delays [hubspot-docs-webhooks §Retries; hubspot-docs-errors §Webhooks].
- The `X-HubSpot-Signature` header holds a SHA-256 hash of the app secret concatenated with the unparsed request body, to be compared against a locally computed hash [hubspot-docs-webhooks §Security].
- Default concurrency is 10 in-flight requests; `maxConcurrentRequests` must be greater than five; settings can be cached for up to five minutes; a maximum of 1,000 subscriptions per app applies; the target URL must be public HTTPS; and webhook POSTs from HubSpot do not count against the app's API rate limits [hubspot-docs-webhooks §Limits].
- Webhooks are supported in private apps, but their subscriptions cannot be edited through an API and must be changed in the app settings [hubspot-docs-private-apps §Feature limitations].
- The webhooks subscription APIs themselves work only with legacy public apps; project-based apps use a separate configuration reference plus a webhook journal and management API [hubspot-docs-webhooks §Webhook settings].
- The webhooks journal API (beta) exposes journal files of `journalEvents` with `offset`, `publishedAt`, `occurredAt`, `action` (`CREATE`, `UPDATE`, `DELETE`, `MERGE`, `RESTORE`), `objectTypeId`, `objectId` and `propertyChanges`, covering the past 3 days, to be processed in chronological order using the offset system, with limits of 100 journal requests per second per app [hubspot-docs-webhooks-journal §Webhooks journal API].
- CRM search is `POST /crm/objects/{version}/{object}/search`; filters within a group are ANDed and groups are ORed, with at most 5 groups, 6 filters per group and 18 filters total; operators include `GT`, `GTE`, `BETWEEN`, `IN`, `HAS_PROPERTY` [hubspot-docs-search §Filter search results].
- Contacts expose `lastmodifieddate` by default while most other objects expose `hs_lastmodifieddate`; range filters take millisecond timestamps [hubspot-docs-search §Filter search results].
- Only one sort rule is allowed per search, page size defaults to 10 and caps at 200, a query returns at most 10,000 results (paging further returns 400), newly created or updated records take "a few moments" to become searchable, and the search endpoints allow five requests per second per account with no rate-limit headers in the response [hubspot-docs-search §Paging through results].
- HubSpot's usage guidance recommends webhooks over polling, and workflow webhook actions (Marketing Enterprise) also push data without counting toward the API rate limit [hubspot-docs-usage §Use webhooks to get updated data from HubSpot].

## Implications for the build

- Subscribe to `contact.creation` plus `contact.propertyChange` on a short, explicit list of properties (lifecycle stage, lead status, owner, email, company association change, and the feedback properties in H07) rather than everything; every change is a notification and the 1,000-subscription cap is per property.
- Treat webhook delivery as at-least-once and unordered: acknowledge within 5 seconds by enqueueing, deduplicate on (`objectId`, `subscriptionType`, `propertyName`, `occurredAt`) rather than trusting `eventId`, and let the worker re-read the record's current state from the API before scoring instead of applying `propertyValue` from the payload.
- Run a reconciliation poller as the safety net: CRM search on contacts with `lastmodifieddate` `GTE` the last checkpoint minus a safety margin, sorted ascending, 200 per page, staying under 5 searches per second and splitting the window if a query would exceed 10,000 results.
- Verify `X-HubSpot-Signature` on every request and scale the endpoint for 10 concurrent in-flight POSTs of up to 100 events each.
- Decide the app type up front: a private app gives webhooks with UI-managed subscriptions and a static token; the subscription management API and journal API are only for the public/project app models.
- Expect bursts: a list import or a workflow touching thousands of contacts will fan out thousands of property-change events, so the queue must absorb spikes much larger than the steady "hundreds per day".
