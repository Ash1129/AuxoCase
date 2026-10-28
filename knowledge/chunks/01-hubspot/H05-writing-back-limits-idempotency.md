---
id: H05
title: Writing back: batch updates, rate limits, idempotency and partial failures
topic: hubspot
sources: [hubspot-docs-contacts, hubspot-docs-usage, hubspot-docs-private-apps, hubspot-docs-errors, hubspot-docs-associations, hubspot-docs-search]
last_verified: 2026-10-27
---

## Claims

- A single contact is updated with `PATCH /crm/v3/objects/contacts/{contactId}` (or by email with `?idProperty=email`); batch update is `POST /crm/v3/objects/contacts/batch/update` and must use record IDs [hubspot-docs-contacts §Update contacts].
- Batch create, read, update and upsert are limited to 100 records per request [hubspot-docs-contacts §Limits].
- `POST /crm/v3/objects/contacts/batch/upsert` matches on `email` or a custom unique identifier set by `idProperty`, updating existing contacts and creating missing ones; partial upserts are not supported when `email` is the `idProperty` [hubspot-docs-contacts §Upsert contacts].
- Privately distributed apps are limited per 10 seconds to 100 requests (Free/Starter), 190 (Professional), 190 (Enterprise) or 250 with the API limit increase; daily limits are 250,000, 625,000 and 1,000,000 respectively, plus 1,000,000 per purchased increase (maximum two); the burst limit is per app and the daily limit is shared across all apps in the account [hubspot-docs-usage §Privately distributed app limits].
- The legacy private-apps page gives the same tiers but 200 per 10 seconds with the API limit increase, and says daily usage can be checked with `GET /account-info/v3/api-usage/daily/private-apps` [hubspot-docs-private-apps §Private app limits].
- Publicly distributed OAuth apps are limited to 110 requests every 10 seconds per installing account and the add-on does not raise this; CRM search is excluded from that limit and has its own [hubspot-docs-usage §Publicly distributed OAuth app limits].
- Exceeding a limit returns 429 for subsequent calls with a body containing `errorType` `RATE_LIMIT` and `policyName` (`DAILY` or secondly); the daily limit resets at midnight in the account's time zone; error responses should stay under 5% of daily requests [hubspot-docs-usage §Rate limit error responses].
- Response headers `X-HubSpot-RateLimit-Daily`, `-Daily-Remaining`, `-Interval-Milliseconds`, `-Max` and `-Remaining` report remaining quota (the daily pair is not sent for OAuth); the `-Secondly` headers are deprecated [hubspot-docs-usage §Rate limit headers].
- High request volume can also produce 5xx errors, which should be handled like 429s; HubSpot advises caching data that rarely changes, such as property definitions, owners and form settings, and using batch APIs [hubspot-docs-usage §Address rate limit issues].
- Batch create endpoints return `207 Multi-Status` once multi-status handling is enabled by giving each input a unique `objectWriteTraceId`; successes appear in `results`, failures in `errors` with `numErrors`, and each error carries `status`, `category`, `message` and a `context` holding the failing `objectWriteTraceId` [hubspot-docs-errors §Multi-status errors].
- Error bodies may contain `status`, `message`, `errors[]` (`message`, `code`, `context`), `category` and `correlationId`, but every field is optional and parsers must tolerate any being absent [hubspot-docs-errors §Common error responses].
- For 423 Locked wait at least 2 seconds; for 477 Migration in Progress wait the `Retry-After` seconds (up to 24 hours); for 502/504/503/521/523/524 pause a few seconds and retry; for 522/525/526 contact support instead of retrying [hubspot-docs-errors §Common error responses].
- Association batch create accepts up to 2,000 inputs and association rate limits are separate and lower than general limits [hubspot-docs-associations §Limits].
- Search responses carry no rate-limit headers and search is capped at five requests per second per account [hubspot-docs-search §Rate limit].

## Implications for the build

- Write scores in batches of 100 via `batch/update` keyed by record ID, and budget requests: scoring 6,000 leads a month with re-scores on every engagement change is far below the daily limits, but a nightly full re-rank of all open leads is 1 request per 100 records plus reads, so plan reads with `properties` lists and batch read rather than per-record GETs.
- Build a token-bucket client that reads `X-HubSpot-RateLimit-Remaining` and `-Interval-Milliseconds`, backs off on 429/5xx with jitter, and keeps a separate 5 requests-per-second bucket for search.
- Make every write idempotent by construction: the payload is the full desired state (score, rank, reason, version, scored_at) for the record, so a retry or duplicate webhook re-writes the same values; never write deltas.
- Always send a unique `objectWriteTraceId` per input on batch create and parse `207` responses; for batch update, treat the whole batch as suspect on error and re-read the records to confirm which values landed before retrying.
- Keep the service's own write log (record ID, trace ID, payload hash, HubSpot `correlationId`) because HubSpot's own API logs for private apps do not store request bodies and are retained only 30 days (see H08).
- Cache owners, property definitions and pipeline/stage definitions for minutes to hours; they are the calls that otherwise silently eat the daily quota.
