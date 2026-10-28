---
id: L06
title: Response modelling versus uplift modelling for a capacity-limited team
topic: lead-scoring
sources: [radcliffe-2011-uplift, gutierrez-2017-uplift]
last_verified: 2026-10-27
---

## Claims

- A conventional response model estimates P(O=1 | x, T), the probability a customer buys if treated; an uplift model estimates P(O=1 | x, T) − P(O=1 | x, C), the increase in purchase probability caused by the treatment relative to no treatment, and the control group plays no part in the response model's definition [radcliffe-2011-uplift §2, p. 4].
- Uplift cannot be measured for an individual because one cannot both treat and not treat the same person; it can only be estimated for segments with equivalent treated and control subpopulations, and segment uplift estimates are not additive [radcliffe-2011-uplift §4.1].
- The Qini measure is the area under the incremental-gains curve, a generalisation of the Gini coefficient to uplift [radcliffe-2011-uplift §4.2].
- Uplift modelling requires a valid control group in the historical data, and a large one: the authors' rules of thumb are that the control group needs to be about ten times larger for modelling than for simply measuring incremental response, and that overall uplift × population size should be at least 500 in each arm [radcliffe-2011-uplift pp. 28-29].
- The presence of negative effects (contacting some customers reduces their purchase probability) is a strong reason to use uplift modelling, because conventional response models are "inherently unable to handle negative effects" [radcliffe-2011-uplift p. 29].
- Uplift modelling is a causal-inference problem (estimating a CATE) that "crucially relies on randomized experiments", i.e. random assignment to treatment and control; three approaches exist: the two-model approach (separate treated and control models), class-variable transformation, and direct modelling by modified trees or forests [gutierrez-2017-uplift §1, §3].
- Because the true individual effect is never observed, uplift models are evaluated with aggregate measures such as uplift-per-decile bars, cumulative uplift curves and Qini rather than per-example losses [gutierrez-2017-uplift §4.1].
- A "sleeping dog" is an individual who churns when treated but not when left alone; the example given has a quarter of the population reacting negatively to a targeted action [gutierrez-2017-uplift §4.1, fn. 3].

## Implications for the build

- The service's first objective is a response model (probability of booking a demo given SDR contact), because that is what the labels support; it must be documented as such, not sold as "who converts because we called".
- Uplift is relevant here because capacity is the binding constraint: inbound demo requests and some partner referrals may book regardless of SDR effort ("sure things"), so ranking them first wastes the 500 slots; this cannot be shown without a control group.
- The randomised exploration slice from L05 is also the seed of a control arm: if a stratum of MQLs is deliberately left uncontacted for a month, their organic demo rate becomes the control baseline for a two-model uplift estimate.
- At ~1,500 MQLs/month the Radcliffe-Surry volume rule (uplift × arm size ≥ 500) will not be met quickly; treat uplift as a quarterly analysis over pooled data, not a monthly model, and report Qini with confidence bands.
- Expose both numbers in the UI eventually (P(demo | contacted) and estimated incremental lift) but rank by P(demo) until the control data exist.
