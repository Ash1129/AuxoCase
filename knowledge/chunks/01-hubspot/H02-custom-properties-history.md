---
id: H02
title: Custom properties for score, rank, reason and version; property history
topic: hubspot
sources: [hubspot-docs-properties, hubspot-docs-contacts, hubspot-kb-property-history, hubspot-kb-change-sources]
last_verified: 2026-10-27
---

## Claims

- A property is created with `POST /crm/v3/properties/{objectType}` and requires `groupName`, `name` (internal name), `label`, `type` and `fieldType` [hubspot-docs-properties §Create a property].
- Property types are `bool`, `enumeration` (a string holding a set of options), `date` and `datetime` (stored in UTC), `string` (plain text up to 65,536 characters) and `number` (numeric values with at most one decimal); `object_coordinates` and `json` are internal-only and cannot be created or edited [hubspot-docs-properties §Property type and fieldType values].
- Valid `fieldType` values depend on `type`; for `enumeration` they are `checkbox`, `radio`, `select`, `booleancheckbox` and `calculation_equation`, and other field types include `text`, `textarea`, `number`, `date`, `html` and `file` [hubspot-docs-properties §Property type and fieldType values].
- Calculation properties use `fieldType` `calculation_equation` with a `calculationFormula`; calculation properties created via the API cannot be edited in the HubSpot UI, only through the properties API [hubspot-docs-properties §Create calculation properties].
- Unique-identifier properties are created with `hasUniqueValue: true`; each object can have up to ten of them [hubspot-docs-properties §Create unique identifier properties].
- Every property must belong to a property group, which organises properties and shows where they originated [hubspot-docs-properties §Property groups].
- Multi-select checkbox values are written as a semicolon-separated string; a leading semicolon appends to existing values instead of replacing them [hubspot-docs-properties §Add values to checkbox type properties].
- Sensitive-data properties require the `dataSensitivity=sensitive` query parameter to be read, a beta for Enterprise accounts [hubspot-docs-properties §Retrieve properties].
- The `properties` query parameter returns the current value of listed properties (null when defined but empty, omitted when undefined); `propertiesWithHistory` returns current and historical values, and in batch read it is passed in the request body [hubspot-docs-contacts §Retrieve contacts].
- In the UI, property history is available with all products and plans; each entry shows the "Changed to" value, the date and the source, and history can be searched and filtered by property, source or date [hubspot-kb-property-history §View historical values for all properties on a record].
- A previous value can be restored only when the source of the change was a CRM UI edit, an import or a workflow [hubspot-kb-property-history §View historical values of a property on a record].
- Change sources recorded against property updates include Batch Update (batch API endpoint or internal batch migration), Integration (a connected app), Workflow, Import, Forms, Analytics, Meetings, Email Activity, HubSpot Calculation, Data Quality (duplicate management) and CRM UI (a manual action, with the user's name and email) [hubspot-kb-change-sources §HubSpot's change sources].

## Implications for the build

- Create a dedicated property group (for example "Lead Scoring Service") and in it: `number` properties for score and rank, an `enumeration` (`select`) for routing decision/tier, a `string` (`textarea`) for the plain-English reason, a `string` for model version, and a `datetime` for "scored at"; create them once via the API at deploy time and treat the property schema as code.
- Score precision is limited to one decimal on `number` properties, so round in the service and keep full-precision scores in the service's own store.
- Since the scorer writes through the batch API, its changes appear in property history with the Batch Update or Integration source rather than a user, which is what SDRs and auditors will see; include the model version in the reason text so the history entry is self-explaining.
- Use `propertiesWithHistory` on the scorer's own properties and on `lifecyclestage`/`hs_lead_status` when backfilling or auditing, instead of keeping a parallel history table that can drift.
- Do not use calculation properties for the score: they cannot be edited in the UI and tie the logic to HubSpot's formula language rather than the Python service.
- Restores from history cannot undo API writes, so the service must be able to re-run scoring for a record on demand.
