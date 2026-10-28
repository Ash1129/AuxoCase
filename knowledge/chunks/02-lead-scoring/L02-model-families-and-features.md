---
id: L02
title: Model families and feature engineering for ~1,500 MQLs a month
topic: lead-scoring
sources: [christodoulou-2019-lr-vs-ml, niculescu-2005-calibration, nygard-2020-leadscoring, duncan-2015-funnel, yan-2015-pipeline, lee-2026-review]
last_verified: 2026-10-27
---

## Claims

- A systematic review of 71 clinical studies (282 comparisons) comparing logistic regression with machine learning for binary outcomes found "no evidence of superior performance of ML over LR"; studies at low risk of bias showed no difference in discrimination, while studies at high risk of bias favoured ML [christodoulou-2019-lr-vs-ml abstract].
- Boosted trees and boosted stumps push predicted probabilities away from 0 and 1 (a sigmoid-shaped distortion), naive Bayes pushes them toward 0 and 1, and neural nets and bagged trees are reasonably well calibrated out of the box; after Platt scaling or isotonic regression, boosted trees, random forests and SVMs give the best probability estimates [niculescu-2005-calibration abstract].
- Nygård and Mezei build their lead scorer from behavioural event streams (browsing, email activity) plus contact attributes and test several aggregations of the time series, because the way activity is aggregated and truncated at purchase time can itself bias the model [nygard-2020-leadscoring abstract].
- Duncan and Elkan's models are trained on the historical data that CRM or marketing-automation software already collects, and rank by conversion probability, close probability or expected revenue [duncan-2015-funnel abstract].
- In a deployed pipeline model at a Fortune 500 B2B vendor, win propensity was driven by the temporal pattern of seller activity on a lead (bursts of logins, browsing and updates shortly before a win), modelled jointly with a per-lead profile [yan-2015-pipeline abstract].
- Published lead-scoring studies report that simpler, interpretable models relying on feature engineering (logistic regression, decision trees) remain common, and that complex models trade interpretability for accuracy; a model users cannot interpret "may not earn users' trust" [lee-2026-review §2, §6.3].
- The same review attributes most low-performing lead-scoring models to data problems (missing values, limited collection period, no validation) rather than to the algorithm [lee-2026-review §6.1].

## Implications for the build

- Start with regularised logistic regression on well-engineered features and a gradient-boosted tree model trained on the same matrix; promote the tree model only if it beats LR on time-split precision@500 and stays calibrated after post-processing (see L04).
- With roughly 1,500 MQLs and a few hundred demo positives a month, keep the feature count small and favour features with stable meaning: channel (one-hot, with partner-referral and webinar as explicit levels), firmographics (size band, industry, region), and behaviour aggregated over fixed windows (7/30/90-day counts, recency).
- Aggregate behaviour only up to the scoring timestamp; never let post-MQL activity (which SDRs cause) leak into features, following Nygård and Mezei's truncation at purchase time.
- Because reps' own activity predicts wins (Yan et al.), keep seller-activity features out of the demo-booking model and use them only in the close model, where they are legitimately observed.
- Log the feature matrix per scoring run so that feature drift and data-quality regressions can be diagnosed before blaming the model.
