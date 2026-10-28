---
id: L05
title: Selection bias and feedback loops when only worked leads get labels
topic: lead-scoring
sources: [bekker-2020-pu-survey, bottou-2013-counterfactual, barocas-2016-disparate-impact, jarvinen-2016-automation]
last_verified: 2026-10-27
---

## Claims

- Positive-unlabeled (PU) learning is the setting in which only some positives are labelled and the rest of the data is unlabelled and contains both classes; the labelling mechanism is described by a propensity score e(x) = Pr(s=1 | x, y=1) [bekker-2020-pu-survey §2.2, §3.1].
- Under the Selected Completely At Random (SCAR) assumption the propensity is a constant c (the label frequency), so Pr(s=1|x) = c·Pr(y=1|x): a "non-traditional" classifier trained with unlabelled examples treated as negatives preserves the true ranking, and dividing its output by c recovers the true probability [bekker-2020-pu-survey §3.1.1].
- Under the more general Selected At Random (SAR) assumption the labelling bias depends on the attributes x; when the mechanism is understood it can be incorporated to learn an unbiased classifier, otherwise additional assumptions are needed [bekker-2020-pu-survey §3.1.2].
- The Probabilistic-Gap variant of SAR says positives that resemble negatives are less likely to be labelled, which is "satisfied naturally in many applications" [bekker-2020-pu-survey §3.1.3].
- Counterfactual questions ("how would the system have performed if a different policy had chosen the actions?") can be answered from logged data by importance-sampling reweighting only when the logging policy involved active randomisation; the reweighting fails where the counterfactual policy puts mass in regions the logging policy rarely explored [bottou-2013-counterfactual §4.2, §4.4].
- Clipping importance weights (e.g. at the fifth-largest observed ratio) bounds the variance; the gap between the clipped and unclipped estimates measures how much of the uncertainty is due to lack of exploration rather than sample size, and tells you whether to collect more data or change the data-collection experiment [bottou-2013-counterfactual §4.4, §4.5].
- Labels created by human judgement are "unavoidably subjective" and skew the resulting model so that future cases are characterised along the same lines; prelabelled data (e.g. fraud reports) inherit whatever process generated those labels [barocas-2016-disparate-impact pp. 681-682].
- Sales reps often ignore marketing-generated leads, cannot evaluate their objective quality, and follow up based on their perception of the qualification process, so follow-up (and therefore the outcome label) is itself a function of rep belief, not of lead quality [jarvinen-2016-automation p. 11].

## Implications for the build

- Treat the historical dataset as PU data where the "selected" set is the ~500 leads SDRs chose each month: a model trained on worked-lead outcomes only learns Pr(worked and booked | x) blended with SDR taste, and will reproduce the under-working of partner and webinar leads unless corrected.
- Record who chose each lead and why (gut-feel pick vs. model-ranked vs. forced) from day one; this is the propensity data needed for SAR-style correction and inverse-propensity weighting.
- Introduce a small randomised exploration slice (e.g. 5-10% of the 500 worked slots drawn uniformly or by propensity-stratified sampling from the unworked MQLs) and keep it permanently; without it counterfactual evaluation of a new ranker is impossible and reported lift cannot be trusted.
- Evaluate candidate rankers offline with clipped inverse-propensity estimates and report both the clipped and unclipped intervals, following Bottou et al.; a wide inner interval means "explore more", not "train more".
- Treat "not worked" as unlabelled, never as negative, in the training table; store the label state as {booked, worked-no-demo, not-worked} and let the loss function decide how to use the third state.
