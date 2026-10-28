---
id: L04
title: Calibrated probabilities when marketing and sales share one number
topic: lead-scoring
sources: [guo-2017-calibration, niculescu-2005-calibration]
last_verified: 2026-10-27
---

## Claims

- A model is calibrated when, among predictions with confidence p, the fraction that are correct is p; Expected Calibration Error (ECE) approximates the expected gap between confidence and accuracy by binning predictions into M equal-width bins and averaging the per-bin |accuracy − confidence| weighted by bin size [guo-2017-calibration §2].
- Increased model capacity and lack of regularisation are closely related to miscalibration: a model can keep lowering negative log-likelihood by making its predictions more confident after it already classifies the training set, so it overfits NLL without overfitting 0/1 loss [guo-2017-calibration §3].
- Histogram binning assigns each bin a calibrated score; isotonic regression fits a monotone piecewise-constant map and is a strict generalisation of binning; Platt scaling fits a logistic regression σ(a·z + b) on the model's raw score using a held-out validation set [guo-2017-calibration §4.1].
- Temperature scaling, a single-parameter variant of Platt scaling, was the most effective post-processing method across most image and document datasets; miscalibration was found to be "intrinsically low dimensional" and best corrected by simple models [guo-2017-calibration §5].
- Across ten learning algorithms, maximum-margin methods (boosted trees, boosted stumps, SVMs) produce sigmoid-distorted probabilities, naive Bayes produces over-confident ones, and neural nets and bagged trees are already close to calibrated; Platt scaling and isotonic regression both repair the distortion, after which boosted trees, random forests and SVMs give the best probabilities [niculescu-2005-calibration abstract].

## Implications for the build

- Treat the raw score of a gradient-boosted model as a ranking signal, not a probability; fit a calibrator (Platt / temperature first, isotonic only if a plot shows non-sigmoid distortion) on a time-separated validation window and report ECE and a reliability diagram per release.
- Marketing, SDR managers and finance will read "0.32" as "32% chance of a demo"; the build must make that literally true, otherwise capacity planning (expected demos = sum of probabilities over the 500 worked leads) is wrong.
- Calibrate per funnel stage (demo-booked, closed-won) separately; the close model has far fewer positives, so prefer the two-parameter Platt map over isotonic there.
- Recalibrate whenever the base rate shifts (new channel mix, seasonality) even if the ranking model is not retrained; calibration drift is cheaper to fix than ranking drift (see L09).
- Store the calibrator version alongside the model version in every scoring log so a probability can be traced to the map that produced it.
