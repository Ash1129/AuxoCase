---
id: L10
title: Marketing-sales alignment, shared lead definitions and the volume trap
topic: lead-scoring
sources: [kotler-2006-hbr, homburg-2007-thought-worlds, jarvinen-2016-automation]
last_verified: 2026-10-27
---

## Claims

- Conflict between sales and marketing has economic roots (fights over budget, pricing and whether money should go to salespeople or campaigns) and cultural roots (sales sees marketers as out of touch with customers, marketing sees sales as myopic), and "each group often undervalues the other's contributions" [kotler-2006-hbr, opening section; remainder paywalled, see Notes].
- Thought-world differences between marketing and sales generally hamper cooperation, and poorer cooperation lowers business-unit market performance; but some differences help: market performance is higher when one side advocates the customer and the other the product, and when one side is short-term and the other long-term oriented, while differences in product knowledge and interpersonal skills are purely harmful [homburg-2007-thought-worlds abstract].
- There is no consensus on what makes a high-quality lead because the relevant characteristics vary by company; commonly cited ones are the lead's source, need and urgency [jarvinen-2016-automation pp. 10-11].
- When marketing and sales managers define qualified-lead criteria together, fewer arguments occur about lead quality and follow-up practice; in the case company the single most problematic issue was that won/lost deals had to be recorded manually in the CRM, were often skipped for small deals, and so marketing received inaccurate data on lead quality [jarvinen-2016-automation, Discussion].
- Reps cannot evaluate the objective quality of marketing-generated leads and follow up on their perception of the qualification process, so transparency of the method to reps is itself a design requirement [jarvinen-2016-automation p. 11].

## Implications for the build

- The service should publish one shared definition per stage (MQL, worked, demo booked, closed-won) as code and data (an enum plus the SQL that computes it), reviewed by both marketing and sales; the model's labels must be exactly these definitions.
- Measure marketing on the downstream numbers the model predicts (expected demos and expected closed revenue in the ranked list), not on MQL count; a volume metric rewards the behaviour that produced 1,500 MQLs for 500 slots.
- Make the "who gets credit" question explicit: the chunk's evidence says productive disagreement (customer vs product, short vs long term) is fine, but disagreement about facts (was the lead worked, did it close) is not, so outcome capture must be automatic (calendar booking events, CRM stage changes), not manual.
- Keep an SLA table in the service: time-to-first-touch per routed lead and label-recording latency per SDR, and report both to the joint marketing-sales review.
- Treat the explanation surface (L08) as an alignment tool: the same reason codes shown to reps should be aggregated for marketing so both sides see why channels rank where they do.
