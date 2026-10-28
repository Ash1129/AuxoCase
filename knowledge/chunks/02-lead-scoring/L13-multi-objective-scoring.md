---
id: L13
title: Multi-objective scoring: demo probability, close probability and expected value
topic: lead-scoring
sources: [duncan-2015-funnel, yan-2015-pipeline, radcliffe-2011-uplift, zhang-2026-hpro]
last_verified: 2026-10-27
---

## Claims

- Duncan and Elkan's full funnel model ranks initial leads by any of three targets: probability of conversion to an opportunity, probability of a successful sale, or expected revenue, all derived from a staged probabilistic model of the funnel [duncan-2015-funnel abstract].
- Their direct qualification model targets the earliest stage and in production reduced qualification time and the number of calls needed to schedule demos, i.e. it optimised the SDR stage rather than revenue directly [duncan-2015-funnel abstract].
- Win-propensity prediction on pipeline opportunities is positioned as a replacement for subjective human rating of each opportunity's chance of closing; at the deployed enterprise, wins followed bursts of seller activity, so the close stage has its own dynamics and signals distinct from the lead stage [yan-2015-pipeline abstract].
- The response-vs-uplift distinction extends to continuous outcomes: a conventional model fits E(R | T) for revenue R, an uplift model fits the incremental revenue, and the choice of outcome (binary purchase vs. revenue) changes what the model estimates [radcliffe-2011-uplift §2].
- Funnels in long-cycle, high-stakes sales are multi-stage, and a hierarchical preference-ranking objective that encodes funnel stage order (a later stage outranks an earlier one) outperformed pointwise scorers on top-of-list precision and in a live A/B test [zhang-2026-hpro abstract].

## Implications for the build

- Compute three quantities per lead and store all three: p_demo (probability of booking a demo if worked), p_close_given_demo, and an expected-value estimate EV = p_demo × p_close_given_demo × predicted contract value (or a segment-average ACV where no per-lead value exists).
- Rank the SDR queue by p_demo (the SDR's job is demos and that label matures fast), but show EV and p_close as secondary columns and let managers switch the sort to EV for strategic accounts; document that EV ranking compounds two noisy models and a value estimate, so its confidence intervals are wider.
- Train p_close on opportunities (post-demo) with its own features and label window, not on the raw MQL table, following the staged structure of the full funnel model; back-propagate closed-won only as a delayed label into the demo model's evaluation, not its training target, until the delay model (L09) is in place.
- Encode stage order in evaluation: a lead that closed should always rank above one that merely booked, which gives a natural pairwise metric (concordance across stages) to report alongside precision@500.
- Use contract value cautiously: partner referrals and webinar leads convert 2-3x better but may carry different ACV; an EV sort that penalises high-conversion, lower-ACV channels needs a stated business decision behind it, not a silent default.
