---
id: H09
title: HubSpot's own scoring features and their limits
topic: hubspot
sources: [hubspot-kb-lead-scores, hubspot-kb-score-properties, hubspot-kb-predictive, hubspot-kb-contact-properties]
last_verified: 2026-10-27
---

## Claims

- HubSpot's lead scoring tool builds scores from record actions or properties and stores them in score properties usable in segments, workflows and reports; contact scores require Marketing Hub, company scores Marketing or Sales Hub, deal scores Sales Hub, all Professional or Enterprise [hubspot-kb-score-properties §Overview of the lead scoring tool].
- Three score types exist: engagement (event-based, such as visits and email opens), fit (property-based, such as job title or company size) and combined, which creates three properties: total, engagement and fit [hubspot-kb-score-properties §Types of scores].
- Each score has at least one group; points can be whole or decimal and added or subtracted; multiple criteria on the same property or event use AND logic; event rules can be filtered by frequency (Exactly, Between, At least, At most) and time frame; filters can use associated records such as a contact's primary company [hubspot-kb-score-properties §How scores are calculated].
- Score decay is available only for event-based (engagement) groups at intervals of 1, 3, 6 or 12 thirty-day months and does not apply to segment-membership criteria [hubspot-kb-score-properties §Score decay].
- Score limits range from -100..100 up to -10,000..10,000 with a default maximum of 100; association aggregation can be Sum, Average, Min or Max; call events count only for calls made through HubSpot calling [hubspot-kb-lead-scores §Set up engagement criteria based on events].
- Threshold properties are created automatically: engagement and fit scores use High/Medium/Low, combined scores use A1–C3 letter-number grades [hubspot-kb-score-properties §Score thresholds].
- When a score is turned on, records are evaluated retroactively and the property then updates continuously when a record meets any criterion; editing a score re-evaluates all records and can affect tools that use it [hubspot-kb-score-properties §What happens when a score is turned on or updated].
- AI-built engagement and fit scores require Marketing Hub Enterprise and at least 50 contacts (25 converted, 25 not) [hubspot-kb-score-properties §AI scores].
- Neither article states how many scores a tier allows, criteria limits, or whether score values can be set via the API [hubspot-kb-lead-scores §Create a lead score].
- Predictive lead scoring (Likelihood to close, Contact priority) requires Marketing Hub Enterprise or Sales Hub Enterprise and estimates the probability that an open contact becomes a customer within 90 days (a value of 22 means 22%) [hubspot-kb-predictive §Determine likelihood to close with predictive lead scoring].
- Contact priority ranks contacts into Very High, High, Medium and Low, each holding 25% of contacts, so tiers are relative and a contact's tier can change without its score changing; contacts with lifecycle stage Customer are set to Closed Won [hubspot-kb-predictive §Contact priority].
- The predictive model uses analytics and conversion data (page views, visits, social clicks, email delivered/bounced/opened/clicked/replied, conversions), CRM interactions (notes, days since last meeting booked or contacted, free email domain, lifecycle stage), associated-company firmographics via Breeze (revenue, technologies, employees) and the account's own business attributes [hubspot-kb-predictive §Data the model uses].
- Both predictive properties are set automatically, cannot be edited, and the model is described as black-box: inputs and outputs are known but how each input affects the score cannot be determined [hubspot-kb-predictive §Customization and explainability].
- Default contact properties include HubSpot score (sales readiness per the account's criteria), Likelihood to close (Enterprise) and Contact priority (Enterprise) [hubspot-kb-contact-properties §Scoring and prioritization].

## Implications for the build

- Replace, do not extend: the points-based score is a rules engine with AND-only criteria, a fixed decay schedule and no API write path, and it cannot express the cross-channel rank or the capacity constraint (12 SDRs, ~500 workable leads); keep it running only as a comparison baseline and as a fallback if the service is down.
- Reuse the inputs, not the outputs: the predictive model's feature list (recency of visits and emails, meeting booked, free email domain, firmographics from the primary company) is a sensible starting feature set for the external model and is all readable through the APIs in H03.
- Do not feed HubSpot score or Likelihood to close into the external model as features: the first is a hand-tuned rule output that will change whenever marketing edits criteria, and the second is a black-box percentile that shifts without data changes, which breaks reproducibility.
- The external service's biggest advantage is the plain-English reason; HubSpot's predictive score explicitly cannot explain itself, so the reason property is the differentiator for SDR trust and should be designed first-class, not as an afterthought.
- Write the external score into properties in a separate group, not into `hubspotscore` or any native score property, so native tools keep working and the two can be reported side by side during the pilot.
- If a cheaper interim is wanted, HubSpot's tool can consume the service's properties (for example a fit tier written by the service) as fit criteria, giving a hybrid without full replacement.
