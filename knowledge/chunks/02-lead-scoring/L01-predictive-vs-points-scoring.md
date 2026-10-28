---
id: L01
title: Predictive lead scoring versus hand-tuned points models
topic: lead-scoring
sources: [duncan-2015-funnel, nygard-2020-leadscoring, jarvinen-2016-automation, lee-2026-review, zhang-2026-hpro]
last_verified: 2026-10-27
---

## Claims

- Duncan and Elkan model the sales funnel with two probabilistic classifiers, a direct qualification model (DQM) and a full funnel model (FFM), that rank leads by probability of converting to an opportunity, probability of a successful sale, or expected revenue, trained on historical CRM / marketing-automation data [duncan-2015-funnel abstract].
- They state that the trained models "can replace traditional lead scoring systems", which they describe as hand-tuned, error-prone and non-probabilistic [duncan-2015-funnel abstract].
- On real sales data from two companies both models reached high AUC; one company reported a 307% increase in successful sales, and in production DQM reduced the time needed to qualify leads and the number of calls needed to schedule a product demo [duncan-2015-funnel abstract].
- Nygård and Mezei show that purchase probability for a lead can be estimated with supervised learning (random forest as the example) from historical behavioural data such as browsing and email activity, using purchase moments to truncate buyers' activity and testing several time-series aggregations so the truncation does not bias the model [nygard-2020-leadscoring abstract].
- In B2B practice lead qualification is often based on intuition, self-proclaimed competence and heuristic rules of thumb, and qualification errors waste resources and lose revenue when reps fail to focus on the most profitable leads [jarvinen-2016-automation pp. 10-11].
- When reps work at full capacity, sales efficiency can only be raised by contacting more top-ranked prospects, which makes the ranking itself the lever [jarvinen-2016-automation p. 10].
- A 2026 review of predictive lead-scoring studies finds the field has moved from manual heuristics to ML (logistic regression, random forests, boosting, neural networks) and reports recurring problems in published studies: imbalanced and small datasets, poor data quality, overfitting of high-performance models, and models never tested in a live environment [lee-2026-review §1, §6.1-6.3].
- The newest industrial work frames rule-based scorecards and pointwise ML scorers as sharing three weaknesses: sparse supervision, a semantic gap on unstructured CRM text, and an inability to capture relative lead priority; a pairwise-ranking objective raised precision among top-ranked leads by 39.7% and sales volume by 9.5% in a 132-day A/B test at one brand [zhang-2026-hpro abstract].

## Implications for the build

- Treat the existing points model as the baseline to beat, and measure the beat the way Duncan and Elkan did: demos booked and deals closed per worked lead, not model accuracy.
- Score two funnel stages separately (demo-booked and closed-won), as DQM/FFM do, so partner and webinar leads that the points model under-scores can be promoted on evidence of downstream conversion rather than on activity points.
- Because only ~500 of ~1,500 MQLs are worked, the ranking objective matters more than the pointwise score: evaluate on precision in the top 500, and consider a pairwise/ranking loss once the pointwise model is stable.
- Avoid the failure modes the review catalogues: hold out a time-based test set, report class-imbalance-aware metrics, and plan a live shadow test before replacing the points score.
- Keep a human-readable baseline (logistic regression on the same features) in the repo so every gain can be attributed to features or to model complexity.
