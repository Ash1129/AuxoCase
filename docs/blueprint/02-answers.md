# Blueprint answers for the BrightPath brief (three jobs, 2026-10-27)

## Job 1: Score and rank every new or updated HubSpot lead by its likelihood to book a demo and close

9 of 12 answered from the description.

| Question | Answer | Read from the description |
|---|---|---|
| Same steps every time, or reading and judgement? | The same steps, but some need reading or judgement, such as writing a plain-English reason for each score | Fixed pipeline (score, rank, write back) with a reason step that needs reading |
| Distinct types needing different handling? | Yes, a few clear types, such as partner referrals, webinar leads and other channels | Referrals and webinars convert 2-3x better but score low |
| How do the parts relate? | Each step builds on the last: score, then rank, then write back with a reason | A chain where scoring feeds ranking and write-back |
| Could you write down what a good score looks like? | Yes, as a clear checklist: scores track bookings and closes, every lead gets a score, rank and reason | Success is defined by demos booked and deals closed |
| What should it draw on every time? | Memory of past bookings and closes, so scoring improves over time | Feedback must flow back into scoring |
| Work inside other software? | It needs to take actions | Writes score, rank and reason back into HubSpot |
| How does it start? | Automatically, when a new lead or new activity arrives in HubSpot | Reads lead and activity data continuously |
| How often? | Hundreds of times a day or more | Hundreds of new leads a day |
| Which are true? | It handles personal or confidential information | Business contacts, privacy law |
| Limits on location/providers? | No special limits | (asked) |
| Organisation | A larger organisation with developers or an IT team | (asked) |
| Essential requirements? | The scoring must run inside our product or backend service | (asked) |

Task-specific questions the AI added:

- Where do partner referrals and webinar registrations come from, and which systems hold them? -> All six channels land in HubSpot; referrals via a partner referral form and customer-success handoffs; webinar registrations and attendance via the webinar tool's HubSpot integration (marketing events); origin kept in HubSpot's original source properties.
- How are booked demos and closed deals recorded in HubSpot today? -> Deal stages in HubSpot
- Who owns the ranking rules and the score thresholds? -> Shared between marketing and sales

## Job 2: Route each scored lead to an SDR within capacity and alert them

4 of 8 answered from the description. The first interview answer was rejected (invalid JSON
from Haiku) and retried on Sonnet; afterwards the "reading" overlay stayed on screen though
the request had returned, and a reload recovered it (Blueprint open item 24). After the
reload the standard question wording showed, not the adapted one.

| Question | Answer | Read from the description |
|---|---|---|
| Same rules every time, or judgement? | The same rules every time: rank, capacity and fit decide who gets the lead | "Rules based on rank, capacity and fit" |
| Work inside other software? | It needs to take actions | Assign in HubSpot, alert, write back |
| How does it start? | Automatically, when a lead has been newly scored | "Each newly scored lead from the scoring step" |
| How often? | Hundreds of times a day or more | (asked) |
| Which are true? | It handles personal or confidential information | Leads are personal data |
| Limits on location/providers? | No special limits | (asked) |
| Organisation | A larger organisation with developers or an IT team | (asked) |
| Essential requirements? | The routing must run inside our own product or backend service | (asked) |

Task-specific questions the AI added (answered with sensible defaults; the owner's
follow-up email may change them):

- How should SDR capacity be counted? -> Open leads at any one time
- Which factors should decide fit when choosing an SDR? -> Round-robin only
- How should the assigned SDR be alerted? -> HubSpot task

Note: the questions were well chosen; capacity accounting, fit rules and alert channel are
exactly the three things the brief leaves open for routing. No "judgement" step was
detected, so this job is designed without AI.

## Job 3: Feed demo-booked and closed outcomes back into the scoring model

8 of 12 answered from the description.

| Question | Answer | Read from the description |
|---|---|---|
| Same steps every time, or judgement? | The same steps, but some need reading or judgement, such as deciding why a channel is over-scored and what to change | Reporting where the model is off needs judgement |
| Distinct types? | Yes, a few clear types, such as partner referrals, webinar leads and other channels | Results by channel |
| How do the parts relate? | Each step builds on the last: collect outcomes, compare, report, then recalibrate and review | A chain ending in review before live routing |
| Could you write down what good looks like? | Yes, as a clear checklist | Compare predicted with actual |
| What should it draw on? | Memory of past outcomes, so the model improves over time | Outcomes feed back over time |
| Work inside other software? | It needs to take actions | Model changes take effect in live routing |
| How does it start? | On a schedule | (asked) |
| How often? | A few times a week | (asked) |
| Which are true? | It handles personal or confidential information | Identifiable business contacts |
| Limits on location/providers? | No special limits | (asked) |
| Organisation | A larger organisation with developers or an IT team | (asked) |
| Essential requirements? | A person must approve every model change before it takes effect in live routing | "Reviewed before they take effect" |

Task-specific questions the AI added:

- Where are booked meetings and closed customers recorded today? -> Deal stages in HubSpot
- Who approves changes to the model before they go live? -> Shared between marketing and sales
- How are partner referrals and webinar registrations tagged in HubSpot? -> Original source and drill-down properties; referrals carry the partner on the form; webinars through the marketing events integration with registered/attended status.

## Observations across the three jobs

- "It decides or recommends something about a person" was not ticked by the AI, and was left as the AI read it. A lead score ranks an identifiable business contact for a sales call, which is a recommendation about a person, though not one with a legal or similarly significant effect on them. The build treats it as decision support with reasons, an override path and outcome monitoring by channel, without the full "decisions about people" machinery (identity stripping before AI steps) that AU12 to AU14 would have added.
- The requirements options still read as engineering jargon for a business owner ("partition-aware backfills"): Blueprint open item 3, seen again.
