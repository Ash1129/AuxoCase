---
id: H08
title: Authentication and permissions: private apps, OAuth, scopes and token handling
topic: hubspot
sources: [hubspot-docs-private-apps, hubspot-docs-auth, hubspot-docs-oauth, hubspot-docs-oauth-tokens, hubspot-docs-scopes, hubspot-docs-timeline, hubspot-docs-webhooks]
last_verified: 2026-10-27
---

## Claims

- Private apps let an account use HubSpot's APIs with a per-app access token whose scopes the account controls; only super admins can access them; up to 20 private apps are allowed per account [hubspot-docs-private-apps §Private app limits].
- Private app tokens are sent as `Authorization: Bearer <token>`, are built on OAuth so HubSpot's client libraries accept them, and keep the same token string while scopes shrink if the account loses a product [hubspot-docs-private-apps §Make API calls with your app's access token].
- If the user who created the private app is removed, some calls fail with `USER_DOES_NOT_HAVE_PERMISSIONS`; rotating the token or re-adding the user fixes it [hubspot-docs-private-apps §Rotate your access token].
- Tokens can be rotated immediately ("Rotate and expire now") or with a 7-day overlap ("Rotate and expire later"); HubSpot recommends rotating every six months and emails super admins after 180 days without rotation [hubspot-docs-private-apps §Rotate your access token].
- `POST /oauth/v2/private-apps/get/access-token-info` returns the token's user ID, Hub ID, app ID and scopes [hubspot-docs-private-apps §View private app access token information].
- Private-app API call logs cover 30 days, can be filtered and exported, and do not store request bodies, URL parameters or response payloads of successful calls [hubspot-docs-private-apps §View API call logs].
- Private apps support webhooks but not custom timeline events; timeline events require a public app, and developer API keys and private app tokens cannot create timeline events [hubspot-docs-private-apps §Feature limitations; hubspot-docs-timeline §Create an event].
- On the current developer platform, "static auth" (`auth.type: static`, `distribution: private`) limits an app to a single authorised account, while OAuth is for distribution to multiple accounts (marketplace or a managed set, up to 10 accounts for privately distributed OAuth apps); some features still require a developer API key passed as `hapikey`; client-credential tokens are short-lived [hubspot-docs-auth §App authentication].
- Any app installed by multiple HubSpot accounts or listed on the Marketplace must use OAuth; tokens reflect the requested scopes, not the authorising user's permissions, so a user limited to owned contacts can still grant access to all contacts; `redirect_uri` must be HTTPS in production and a domain, not an IP [hubspot-docs-oauth §Set up OAuth authentication].
- OAuth tokens come from `POST /oauth/v1/token` with `grant_type=authorization_code` or `refresh_token`; access tokens are short-lived with `expires_in` in seconds (the example shows 1800); `GET /oauth/v1/access-tokens/{token}` returns metadata; `DELETE /oauth/v1/refresh-tokens/{token}` revokes the refresh token; HubSpot will deprecate OAuth API v1 on February 16, 2027 [hubspot-docs-oauth-tokens §Refresh an access token].
- Granular CRM scopes include `crm.objects.contacts.read`/`.write`, `crm.schemas.contacts.read`/`.write` (property settings), `crm.objects.owners.read`, `crm.objects.marketing_events.read`/`.write`, `forms`, `automation` (workflows; Marketing Hub Professional/Enterprise), `sales-email-read` (one-to-one email content), `timeline` and `business-intelligence` (analytics and email events); sensitive-data scope variants require Enterprise [hubspot-docs-scopes §List of available scopes].
- Webhook subscriptions on contacts, companies, deals and line items require the object's `.read` scope [hubspot-docs-webhooks §Scopes].

## Implications for the build

- For a single-tenant internal scorer, use a private app (legacy) or static-auth app (new platform): one long-lived token, UI-managed webhook subscriptions, no OAuth refresh loop; choose OAuth only if the service will ever be installed in a second HubSpot account or needs timeline events.
- Request the minimum scopes: `crm.objects.contacts.read/write`, `crm.objects.companies.read`, `crm.objects.deals.read`, `crm.objects.owners.read`, `crm.schemas.contacts.write` (to create properties, ideally only during deploy), `forms`, `crm.objects.marketing_events.read`, `business-intelligence` for email events, and `sales-email-read` only if sales email bodies are genuinely needed (they usually are not for scoring).
- Create the private app under a service-account super admin, not a named engineer, so the token does not break when staff leave; schedule the six-month rotation with the 7-day overlap and store the token in a secrets manager, never in config files or logs.
- The private app's own API logs keep no bodies and only 30 days, so the service must keep its own audit log of every write (see H05).
- Consider two apps: a read/score app and a narrowly scoped schema-management app used only at deploy time, so routine traffic never carries `crm.schemas.*.write`.
- If OAuth is used, plan for the v1 OAuth deprecation (February 2027) and refresh tokens well before the 30-minute `expires_in`.
