---
id: H06
title: Routing and alerts: owner assignment, workflows, notifications, Slack and tasks
topic: hubspot
sources: [hubspot-docs-owners, hubspot-docs-tasks, hubspot-kb-workflow-actions, hubspot-kb-workflow-triggers, hubspot-kb-rotate-owners, hubspot-kb-slack, hubspot-kb-lead-scores]
last_verified: 2026-10-27
---

## Claims

- Owners are assigned to records, activities and marketing tasks by writing the owner `id` through property-change API calls; the owners endpoints are read-only [hubspot-docs-owners §Retrieve a list of owners].
- Workflows require Professional or Enterprise in Marketing, Sales, Service, Data, Smart CRM or Revenue Hub, and creating them needs Super Admin or Workflows permissions [hubspot-kb-workflow-triggers §Available with any of the following subscriptions].
- Enrollment triggers are event triggers (enrol when an event occurs, once or every time), filter triggers (enrol when criteria become true, re-enrolment only if configured), webhook triggers (Data Hub Professional/Enterprise), schedule triggers and manual triggers; a trigger can have up to 250 filters and records enrol only the first time they meet triggers unless re-enrolment is enabled [hubspot-kb-workflow-triggers §Types of enrollment triggers].
- The "Rotate record to owner" action (Sales Hub and Service Hub Professional/Enterprise) assigns enrolled records equally within a selected team or between specified users, and only to activated paid users [hubspot-kb-workflow-actions §CRM actions].
- Rotation can target the record owner or any custom HubSpot user property, has an "Overwrite if [object] has an existing owner" option, assigns no owner if there is no eligible user, resets its counts when the owner list changes, and rotates only one object at a time for fairness, so processing can be slower [hubspot-kb-rotate-owners §Workflow assignment distribution].
- Load balanced, round robin and random distribution types exist only for leads (BETA, Sales Hub Professional/Enterprise) and tickets; lead assignment can skip users set to Away and requires turning off "Sync lead owner with contact or company owner" [hubspot-kb-rotate-owners §Additional distribution options].
- If the HubSpot owner property syncs to Salesforce, rotation may not work as expected because Salesforce can write the previous owner back [hubspot-kb-rotate-owners §Understand considerations].
- Workflow communication actions include "Send internal email notification" (to users, teams or owners, with tokens) and "Send in-app notification" (HubSpot notification centre and mobile app); team notifications reach only primary team members [hubspot-kb-workflow-actions §Communication actions].
- "Edit record" sets, copies or clears property values on the enrolled or an associated record; "Create task" creates tasks; "Send a webhook" (Data Hub Professional/Enterprise) posts JSON to an external application; branches can split on one property (up to 250 branches) or on AND/OR criteria [hubspot-kb-workflow-actions §CRM actions].
- "Send a Slack notification" is a workflow action under Integrated apps (Professional/Enterprise) that can target mapped users, channels (private channels only if the HubSpot app is added) or the record's existing owner, include selected properties and at-mentions, and attach action buttons such as Create task, Log call or Update [object] [hubspot-kb-slack §Send custom Slack notifications through workflows].
- HubSpot user notifications (mentions, form submissions, task assignments and reminders) can be delivered to Slack when the user enables Slack under Notifications and their HubSpot and Slack emails match [hubspot-kb-slack §Receive HubSpot user notifications].
- A task is created with `POST /crm/v3/objects/tasks` with a required `hs_timestamp` due date, `hubspot_owner_id`, `hs_task_priority` and `hs_task_type`, and is associated to a contact with type 204; the docs do not say whether creation notifies the owner [hubspot-docs-tasks §Create a task].
- HubSpot's own lead-score properties can be used by workflows to assign owners or send notifications based on score values [hubspot-kb-lead-scores §Use lead scores in HubSpot tools].

## Implications for the build

- Split responsibilities: the Python service decides the routing (which SDR) and writes `hubspot_owner_id` plus a routing-reason property directly; it does not need HubSpot's rotation action, which is round-robin only for contacts and single-threaded.
- Use one HubSpot workflow as the alerting layer, triggered by the service's "routing decision" property changing (event trigger, re-enrolment every time): branch on the tier property, send the Slack notification to the existing owner with score, rank and reason properties and a "Create task" action button, and optionally send an in-app notification; this keeps alert templates editable by RevOps without a deploy.
- If the SDR's workflow needs a task, have the service create it via the tasks API with the due date, priority and the reason in `hs_task_body`; do not rely on task creation alone to notify, since that is undocumented.
- Keep an owner roster in the service (active, non-archived, with team and capacity) refreshed from `/crm/v3/owners`, because a workflow assigns no owner at all when it finds no eligible user and HubSpot's capacity-aware distribution exists only for leads and tickets.
- Write the routing decision only when the record is unowned or the decision changed, and never overwrite an owner an SDR set manually unless the policy says so; log the previous owner in the service so the override is reversible.
- If Salesforce sync is ever enabled, owner writes from the service can be reverted by the sync; the service should re-read the owner after a delay and alert on mismatches.
