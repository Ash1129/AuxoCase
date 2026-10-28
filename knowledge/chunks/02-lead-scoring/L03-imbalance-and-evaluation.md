---
id: L03
title: Class imbalance and evaluation at the SDR capacity cut-off
topic: lead-scoring
sources: [saito-2015-prc, gama-2014-drift-survey, bekker-2020-pu-survey, lee-2026-review]
last_verified: 2026-10-27
---

## Claims

- ROC plots can be misleadingly optimistic on imbalanced data because the false-positive rate divides by all negatives: in a simulation with 500 true positives, 160 false positives in a balanced set and 1,600 in an imbalanced set produced the same ROC curve, while precision fell from 75% to 25% [saito-2015-prc, Results: Simulation].
- The precision-recall baseline equals the positive proportion P/(P+N) (0.5 at 1:1, about 0.09 at 1:10), so it moves with class balance, whereas the ROC baseline is always the diagonal; PR is the only plot studied whose shape changes with the class ratio [saito-2015-prc, Theoretical Background: PRC].
- PR plots show early-retrieval (top-of-list) differences more clearly than ROC, CROC or cost curves; the authors recommend PR as the main evaluation on imbalanced binary problems and warn that PR interpolation is non-linear, so naive linear interpolation inflates AUC-PR [saito-2015-prc, Results; Conclusion].
- In a re-analysis with 111 positives and 13,444 negatives, ROC made one tool look strongest while PR showed most tools had precision close to random [saito-2015-prc, Results: Re-analysis].
- For streaming evaluation under class imbalance, the Kappa statistic is suggested as a cheap imbalance-aware accuracy measure, with prequential (interleaved test-then-train) and holdout evaluation as the two main schemes [gama-2014-drift-survey §4.1, §4.2].
- When negatives are not reliably labelled (positive-unlabeled data), recall can be estimated under the SCAR assumption but precision cannot be computed directly; the surrogate r²/Pr(ŷ=1) behaves like F1 and can be estimated from PU data [bekker-2020-pu-survey §4.1].
- Lead-scoring studies frequently report accuracy on imbalanced data and do not address overfitting, which the review lists among the recurring performance issues [lee-2026-review §6.1, §6.3].

## Implications for the build

- Make precision@500 (SDR monthly capacity) and lift@500 over the points model the primary offline metrics; report AUC-PR with proper (non-linear) interpolation; report AUC-ROC only as a secondary, model-wide sanity check.
- Always print the PR baseline (positive rate of the evaluation window) next to AUC-PR so a 0.35 AUC-PR on a 12% base rate is read correctly.
- Evaluate on a time-ordered split (train on months 1..k, test on month k+1) and track the prequential error month by month rather than a single random cross-validation score.
- Because precision cannot be measured on unworked leads, compute precision only on the worked subset and estimate recall on the labelled positives; flag both as biased until a randomised holdout exists (see L05).
- Never report accuracy; with ~12% demo rate among worked leads, a model that predicts "no demo" scores 88%.
